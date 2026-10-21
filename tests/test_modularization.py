"""Smoke tests for extracted notification modules (no network or SMTP calls)."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import notify
from notification_core import parse_events, parse_list, DEFAULT_BRANCHES
from notification_events import classify


class ModularizationTests(unittest.TestCase):
    def test_entry_point_imports(self):
        self.assertTrue(callable(notify.run))
        self.assertTrue(callable(notify.render_html))
        self.assertTrue(callable(notify.send_email))

    def test_default_branches(self):
        self.assertEqual(parse_list("") or parse_list(DEFAULT_BRANCHES), ["main", "master"])

    def test_events(self):
        self.assertEqual(parse_events("push,pr_opened"), {"push", "pr_opened"})

    def test_pr_opened_target(self):
        self.assertEqual(classify("pull_request_target", {
            "action": "opened", "pull_request": {"base": {"ref": "production"}}
        }, ""), ("pr_opened", "production"))


if __name__ == "__main__":
    unittest.main()
