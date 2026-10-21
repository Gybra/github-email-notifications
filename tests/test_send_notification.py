"""Tests for the script embedded in .github/workflows/send-notification.yml.

Run with: python3 -m unittest discover -s tests -v
Set PREVIEW_DIR=/some/dir to also write the rendered HTML e-mails there.
"""
import json
import os
import smtplib
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "send-notification.yml"
SHA = "a" * 40
SERVER = "https://github.com"


import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import notify
import notification_github
import notification_templates


class FakeSMTP:
    """Records sessions; `failures` holds exceptions raised by successive send_message calls."""

    sessions = []
    sent = []
    failures = []
    login_error = None

    def __init__(self, host, port, timeout):
        self.host, self.port, self.tls = host, port, False
        FakeSMTP.sessions.append(self)

    def ehlo(self):
        pass

    def starttls(self, context):
        self.tls = True

    def login(self, user, key):
        if FakeSMTP.login_error:
            raise FakeSMTP.login_error

    def send_message(self, message, from_addr, to_addrs):
        if FakeSMTP.failures:
            raise FakeSMTP.failures.pop(0)
        FakeSMTP.sent.append((message, from_addr, to_addrs))
        return {}

    def quit(self):
        pass

    def close(self):
        pass


def push_payload(**overrides):
    payload = {
        "ref": "refs/heads/main",
        "after": SHA,
        "compare": f"{SERVER}/acme/demo/compare/111...{SHA[:12]}",
        "created": False,
        "deleted": False,
        "forced": False,
        "pusher": {"name": "octocat"},
        "sender": {"login": "octocat"},
        "repository": {"name": "demo", "full_name": "acme/demo", "pushed_at": 1792000000},
        "commits": [
            {"id": "1" * 40, "url": f"{SERVER}/acme/demo/commit/{'1' * 40}", "message": "First change", "author": {"name": "Octo", "username": "octocat"}},
            {
                "id": SHA,
                "url": f"{SERVER}/acme/demo/commit/{SHA}",
                "message": "<script>alert(1)</script> fix\n\nBody line",
                "author": {"name": "Octo", "username": "octocat"},
            },
        ],
    }
    payload["head_commit"] = payload["commits"][-1]
    payload.update(overrides)
    return payload


def pr_payload(action="opened", merged=False, **pr_overrides):
    pr = {
        "number": 42,
        "title": 'Add <b>bold</b> "feature"\r\nBcc: evil@example.com',
        "html_url": f"{SERVER}/acme/demo/pull/42",
        "merged": merged,
        "draft": False,
        "commits": 3,
        "created_at": "2026-10-21T10:00:00Z",
        "merged_at": "2026-10-21T11:00:00Z" if merged else None,
        "closed_at": "2026-10-21T11:00:00Z" if action == "closed" else None,
        "user": {"login": "author"},
        "base": {"ref": "main"},
        "head": {"ref": "feature/x", "label": "fork:feature/x"},
    }
    pr.update(pr_overrides)
    return {"action": action, "pull_request": pr, "sender": {"login": "merger"}, "repository": {"name": "demo", "full_name": "acme/demo"}}


class NotificationTest(unittest.TestCase):
    def setUp(self):
        FakeSMTP.sessions, FakeSMTP.sent, FakeSMTP.failures, FakeSMTP.login_error = [], [], [], None
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for patcher in (
            mock.patch.object(notify.smtplib, "SMTP", FakeSMTP),
            mock.patch.object(notify.time, "sleep", lambda seconds: None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api_calls = []
        self.api_responses = {}
        for module in (notification_github, notification_templates):
            api = mock.patch.object(module, "github_get", self.fake_github_get)
            api.start()
            self.addCleanup(api.stop)

    def fake_github_get(self, url, token):
        self.api_calls.append(url)
        for fragment, response in self.api_responses.items():
            if fragment in url:
                return response
        return None

    def run_event(self, event_name, payload, **env):
        path = Path(self.tmp.name) / "event.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        base = {
            "NOTIFY_ENABLED": "true",
            "NOTIFY_RECIPIENTS": "dev@example.com, ops@example.org",
            "NOTIFY_FROM": "Acme CI <ci@example.com>",
            "NOTIFY_BRANCHES": "",
            "NOTIFY_EVENTS": "",
            "NOTIFY_TIMEZONE": "Europe/Rome",
            "SMTP_HOST": "smtp-relay.brevo.com",
            "SMTP_PORT": "587",
            "SMTP_LOGIN": "login@smtp-brevo.com",
            "SMTP_KEY": "secret-key",
            "GH_TOKEN": "token",
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_EVENT_PATH": str(path),
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_SERVER_URL": SERVER,
            "GITHUB_API_URL": "https://api.github.com",
            "GITHUB_REF_NAME": "main",
            "GITHUB_RUN_ID": "123",
        }
        base.update(env)
        return notify.run(base)

    def sent_message(self):
        self.assertEqual(len(FakeSMTP.sent), 1, "exactly one e-mail expected")
        message, sender, recipients = FakeSMTP.sent[0]
        html_part = message.get_body(("html",)).get_content()
        text_part = message.get_body(("plain",)).get_content()
        preview_dir = os.environ.get("PREVIEW_DIR")
        if preview_dir:
            Path(preview_dir, f"{self.id().rsplit('.', 1)[-1]}.html").write_text(html_part, encoding="utf-8")
        return message, sender, recipients, html_part, text_part

    def test_disabled_sends_nothing(self):
        for value in ("false", "", "no"):
            self.assertEqual(self.run_event("push", push_payload(), NOTIFY_ENABLED=value), 0)
        self.assertEqual(FakeSMTP.sessions, [])

    def test_push_email_content_links_and_escaping(self):
        self.assertEqual(self.run_event("push", push_payload()), 0)
        message, sender, recipients, html_part, text_part = self.sent_message()

        self.assertEqual(message["Subject"], "[GitHub] demo — New push on main")
        self.assertEqual(sender, "ci@example.com")
        self.assertEqual(message["From"], "Acme CI <ci@example.com>")
        self.assertEqual(recipients, ["dev@example.com", "ops@example.org"])
        self.assertTrue(FakeSMTP.sessions[0].tls)
        self.assertEqual((FakeSMTP.sessions[0].host, FakeSMTP.sessions[0].port), ("smtp-relay.brevo.com", 587))

        self.assertNotIn("<script>", html_part)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; fix", html_part)
        self.assertIn(f'href="{SERVER}/acme/demo/compare/111...{SHA[:12]}"', html_part)
        self.assertIn("View changes", html_part)
        self.assertIn("aaaaaaa", html_part)
        self.assertIn("octocat", html_part)
        self.assertIn('name="viewport"', html_part)
        self.assertIn("Date: 14 Oct 2026, 19:46 CEST", text_part)  # repository.pushed_at in Europe/Rome

    def test_single_commit_push_links_the_commit(self):
        payload = push_payload()
        payload["commits"] = payload["commits"][-1:]
        self.run_event("push", payload)
        html_part = self.sent_message()[3]
        self.assertIn(f'href="{SERVER}/acme/demo/commit/{SHA}"', html_part)
        self.assertIn("View commit", html_part)

    def test_foreign_links_are_not_rendered(self):
        payload = push_payload(compare="javascript:alert(1)")
        payload["commits"][0]["url"] = "https://evil.example/x"
        self.run_event("push", payload)
        html_part = self.sent_message()[3]
        self.assertNotIn("javascript:", html_part)
        self.assertNotIn("evil.example", html_part)

    def test_branch_filter_with_globs(self):
        self.run_event("push", push_payload(ref="refs/heads/develop"))
        self.assertEqual(FakeSMTP.sent, [])
        self.run_event("push", push_payload(ref="refs/heads/release/1.2"), NOTIFY_BRANCHES="main, release/*")
        self.assertEqual(self.sent_message()[0]["Subject"], "[GitHub] demo — New push on release/1.2")

    def test_tag_push_is_ignored(self):
        self.assertEqual(self.run_event("push", push_payload(ref="refs/tags/v1")), 0)
        self.assertEqual(FakeSMTP.sent, [])

    def test_push_created_by_pr_merge_is_not_duplicated(self):
        self.api_responses[f"/commits/{SHA}/pulls"] = [
            {"number": 42, "merged_at": "2026-10-21T11:00:00Z", "merge_commit_sha": SHA, "base": {"ref": "main"}}
        ]
        self.assertEqual(self.run_event("push", push_payload()), 0)
        self.assertEqual(FakeSMTP.sent, [])
        self.assertIn(f"https://api.github.com/repos/acme/demo/commits/{SHA}/pulls", self.api_calls)

    def test_merge_push_is_sent_when_pr_merged_is_disabled(self):
        self.api_responses[f"/commits/{SHA}/pulls"] = [
            {"number": 42, "merged_at": "2026-10-21T11:00:00Z", "merge_commit_sha": SHA, "base": {"ref": "main"}}
        ]
        self.run_event("push", push_payload(), NOTIFY_EVENTS="push")
        self.sent_message()

    def test_direct_push_is_sent_when_commit_has_only_open_prs(self):
        self.api_responses[f"/commits/{SHA}/pulls"] = [{"number": 7, "merged_at": None, "merge_commit_sha": "b" * 40, "base": {"ref": "main"}}]
        self.run_event("push", push_payload())
        self.sent_message()

    def test_pr_merged(self):
        self.api_responses["/pulls/42/commits"] = [
            {"sha": "c" * 40, "html_url": f"{SERVER}/acme/demo/commit/{'c' * 40}", "commit": {"message": "Implement <x>", "author": {"name": "A"}}, "author": {"login": "author"}}
        ]
        self.run_event("pull_request_target", pr_payload("closed", merged=True))
        message, _, _, html_part, text_part = self.sent_message()

        self.assertEqual(message["Subject"], "[GitHub] demo — Pull Request #42 merged")
        self.assertNotIn("evil@example.com", message["Bcc"] or "")
        self.assertIn("&lt;b&gt;bold&lt;/b&gt; &quot;feature&quot;", html_part)
        self.assertNotIn("<b>bold</b>", html_part)
        self.assertIn("Implement &lt;x&gt;", html_part)
        self.assertIn("fork:feature/x → main", html_part)
        self.assertIn(f'href="{SERVER}/acme/demo/pull/42"', html_part)
        self.assertIn("merger", text_part)
        self.assertIn("and 2 more", text_part)

    def test_pr_opened_draft(self):
        self.run_event("pull_request_target", pr_payload("opened", draft=True))
        message, _, _, html_part, _ = self.sent_message()
        self.assertEqual(message["Subject"], "[GitHub] demo — Pull Request #42 opened")
        self.assertIn("Pull request opened (draft)", html_part)

    def test_pr_closed_is_opt_in(self):
        self.run_event("pull_request_target", pr_payload("closed"))
        self.assertEqual(FakeSMTP.sent, [])
        self.run_event("pull_request_target", pr_payload("closed"), NOTIFY_EVENTS="pr_closed")
        self.assertEqual(self.sent_message()[0]["Subject"], "[GitHub] demo — Pull Request #42 closed")

    def test_pr_to_unmonitored_base_branch_is_ignored(self):
        payload = pr_payload("opened")
        payload["pull_request"]["base"]["ref"] = "develop"
        self.run_event("pull_request_target", payload)
        self.assertEqual(FakeSMTP.sent, [])

    def test_other_pr_actions_are_ignored(self):
        self.run_event("pull_request_target", pr_payload("synchronize"))
        self.assertEqual(FakeSMTP.sent, [])

    def test_workflow_dispatch_sends_test_email_regardless_of_filters(self):
        self.run_event("workflow_dispatch", {"sender": {"login": "octocat"}, "repository": {"name": "demo"}}, NOTIFY_EVENTS="pr_closed", GITHUB_REF_NAME="feature")
        message, _, _, html_part, _ = self.sent_message()
        self.assertEqual(message["Subject"], "[GitHub] demo — Test notification")
        self.assertIn(f'href="{SERVER}/acme/demo/actions/runs/123"', html_part)
        self.assertIn("Recipients: 2", html_part)

    def test_recipient_validation(self):
        self.run_event("push", push_payload(), NOTIFY_RECIPIENTS="good@example.com; nope,  GOOD@example.com\nbad@host, x@y.io")
        self.assertEqual(self.sent_message()[2], ["good@example.com", "x@y.io"])
        for value in ("", " , ", "not-an-email"):
            with self.assertRaises(notify.ConfigError):
                self.run_event("push", push_payload(), NOTIFY_RECIPIENTS=value)
        with self.assertRaises(notify.ConfigError):
            self.run_event("push", push_payload(), NOTIFY_RECIPIENTS=",".join(f"u{i}@example.com" for i in range(51)))

    def test_invalid_sender_and_missing_secrets(self):
        with self.assertRaises(notify.ConfigError):
            self.run_event("push", push_payload(), NOTIFY_FROM="")
        with self.assertRaises(notify.ConfigError):
            self.run_event("push", push_payload(), SMTP_KEY="")

    def test_unknown_events_only_is_an_error(self):
        with self.assertRaises(notify.ConfigError):
            self.run_event("push", push_payload(), NOTIFY_EVENTS="deploy")

    def test_transient_smtp_errors_are_retried(self):
        FakeSMTP.failures = [smtplib.SMTPServerDisconnected("gone"), smtplib.SMTPDataError(421, b"try later")]
        self.run_event("push", push_payload())
        self.sent_message()
        self.assertEqual(len(FakeSMTP.sessions), 3)

    def test_retries_are_limited(self):
        FakeSMTP.failures = [smtplib.SMTPDataError(451, b"busy")] * 5
        with self.assertRaises(notify.SendError):
            self.run_event("push", push_payload())
        self.assertEqual(len(FakeSMTP.sessions), 3)

    def test_authentication_error_is_not_retried_and_hides_the_key(self):
        FakeSMTP.login_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")
        with self.assertRaises(notify.SendError) as caught:
            self.run_event("push", push_payload())
        self.assertEqual(len(FakeSMTP.sessions), 1)
        self.assertIn("535", str(caught.exception))
        self.assertNotIn("secret-key", str(caught.exception))

    def test_permanent_rejection_is_not_retried(self):
        FakeSMTP.failures = [smtplib.SMTPDataError(554, b"quota exceeded")]
        with self.assertRaises(notify.SendError):
            self.run_event("push", push_payload())
        self.assertEqual(len(FakeSMTP.sessions), 1)

    def test_main_returns_failure_exit_code(self):
        with mock.patch.dict(os.environ, {"NOTIFY_ENABLED": "true", "NOTIFY_RECIPIENTS": ""}, clear=True):
            self.assertEqual(notify.main(), 1)

    def test_log_escapes_workflow_commands(self):
        with mock.patch("builtins.print") as printed:
            notify.log("notice", "branch\n::error::injected %")
        self.assertEqual(printed.call_args.args[0], "::notice::branch%0A::error::injected %25")


if __name__ == "__main__":
    unittest.main()
