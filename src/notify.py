"""Entry point for GitHub email notifications."""
from notification_core import *
from notification_github import merged_pr_for_push
from notification_events import classify
from notification_templates import describe, render_text, render_html
from notification_mailer import build_message, send_email

def main() -> int:
    try:
        return run(os.environ)
    except ConfigError as exc:
        log("error", f"Configuration error: {exc}")
    except SendError as exc:
        log("error", f"E-mail not sent: {exc}")
    return 1


def run(env) -> int:
    if not parse_bool(env.get("NOTIFY_ENABLED")):
        log("notice", "Notifications are disabled (NOTIFICATION_ENABLED is not 'true'). Nothing sent.")
        return 0

    events = parse_events(env.get("NOTIFY_EVENTS"))
    branches = parse_list(env.get("NOTIFY_BRANCHES")) or parse_list(DEFAULT_BRANCHES)
    recipients = parse_recipients(env.get("NOTIFY_RECIPIENTS"))
    sender_name, sender = parse_sender(env.get("NOTIFY_FROM"))
    login, key = env.get("SMTP_LOGIN") or "", env.get("SMTP_KEY") or ""
    if not login or not key:
        raise ConfigError(
            "SMTP credentials are empty: set the BREVO_SMTP_LOGIN and BREVO_SMTP_KEY secrets "
            "and pass them to the reusable workflow (runs started by Dependabot need them as Dependabot secrets)."
        )

    payload = load_payload(env.get("GITHUB_EVENT_PATH"))
    event_name = env.get("GITHUB_EVENT_NAME") or ""
    repository = payload.get("repository") or {}
    repo = env.get("GITHUB_REPOSITORY") or repository.get("full_name") or ""
    ctx = {
        "repo": repo,
        "repo_name": repository.get("name") or repo.split("/")[-1],
        "server": (env.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/"),
        "api": (env.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/"),
        "token": env.get("GH_TOKEN") or "",
        "run_id": env.get("GITHUB_RUN_ID") or "",
        "tz": load_timezone(env.get("NOTIFY_TIMEZONE")),
        "events": events,
        "branches": branches,
        "recipient_count": len(recipients),
    }

    kind, branch = classify(event_name, payload, env.get("GITHUB_REF_NAME") or "")
    if kind is None:
        log("notice", f"Event '{event_name}' with action '{payload.get('action') or '-'}' is not notified.")
        return 0
    if kind != "test":
        if kind not in events:
            log("notice", f"'{kind}' is not enabled in NOTIFICATION_EVENTS. Nothing sent.")
            return 0
        if not any(fnmatchcase(branch, pattern) for pattern in branches):
            log("notice", f"Branch '{branch}' does not match NOTIFICATION_BRANCHES. Nothing sent.")
            return 0
        if kind == "push" and "pr_merged" in events:
            number = merged_pr_for_push(ctx, payload.get("after"), branch)
            if number:
                log("notice", f"This push is the merge of pull request #{number}; the pr_merged e-mail covers it.")
                return 0

    notification = describe(kind, branch, payload, ctx)
    subject = clean(f"[GitHub] {ctx['repo_name']} \u2014 {notification['headline']}", 250)
    message = build_message(
        sender_name,
        sender,
        recipients,
        subject,
        render_text(notification, ctx),
        render_html(notification, ctx, subject),
    )
    send_email(message, sender, recipients, env.get("SMTP_HOST") or "", int(env.get("SMTP_PORT") or 587), login, key)

    log("notice", f"Sent '{subject}' to {len(recipients)} recipient(s).")
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"E-mail sent: `{subject}` to {len(recipients)} recipient(s).\n")
    return 0



if __name__ == "__main__":
    sys.exit(main())
