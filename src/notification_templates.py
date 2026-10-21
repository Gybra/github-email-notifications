from notification_core import *
from notification_github import github_get

def safe_url(url, ctx: dict) -> str:
    """Only links that point to this GitHub server are rendered."""
    return url if isinstance(url, str) and url.startswith(ctx["server"] + "/") else ""


def format_time(value, ctx: dict) -> str:
    moment = None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        moment = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            moment = None
    if moment is None or moment.tzinfo is None:
        moment = datetime.now(timezone.utc)
    return moment.astimezone(ctx["tz"]).strftime("%d %b %Y, %H:%M %Z")


def first_line(message) -> str:
    lines = str(message or "").strip().splitlines()
    return clean(lines[0] if lines else "", 120)


def multiline(message) -> str:
    text = str(message or "").strip()
    return text if len(text) <= MAX_MESSAGE_CHARS else text[: MAX_MESSAGE_CHARS - 1] + "\u2026"


def describe(kind: str, branch: str, payload: dict, ctx: dict) -> dict:
    if kind == "push":
        return describe_push(payload, branch, ctx)
    if kind == "test":
        return describe_test(payload, branch, ctx)
    return describe_pull_request(kind, payload, branch, ctx)


def describe_push(payload: dict, branch: str, ctx: dict) -> dict:
    commits = [commit for commit in payload.get("commits") or [] if isinstance(commit, dict)]
    head = payload.get("head_commit") or (commits[-1] if commits else {}) or {}
    actor = (payload.get("pusher") or {}).get("name") or (payload.get("sender") or {}).get("login") or ""

    if payload.get("deleted"):
        headline, label, link = f"Branch {branch} deleted", "Branch deleted", None
    else:
        forced = bool(payload.get("forced"))
        headline = f"Force push on {branch}" if forced else f"New push on {branch}"
        label = ("Force push" if forced else "Push") + (" (new branch)" if payload.get("created") else "")
        compare = safe_url(payload.get("compare"), ctx)
        if len(commits) > 1 and compare and not payload.get("created"):
            link = ("View changes", compare)
        else:
            commit_url = safe_url(head.get("url"), ctx)
            link = ("View commit", commit_url) if commit_url else None

    listed = commits[-MAX_LISTED_COMMITS:][::-1]
    when = (payload.get("repository") or {}).get("pushed_at") or head.get("timestamp")
    rows = [
        ("Repository", ctx["repo"]),
        ("Event", label),
        ("Branch", branch),
        ("Author", actor),
        ("Date", format_time(when, ctx)),
    ]
    if commits:
        rows.append(("Commits", str(len(commits))))
    return {
        "kind": "push",
        "headline": headline,
        "rows": rows,
        "block": ("Latest commit", multiline(head.get("message"))) if head.get("message") else None,
        "link": link,
        "commits": [
            {
                "sha": str(commit.get("id") or "")[:7],
                "url": safe_url(commit.get("url"), ctx),
                "message": first_line(commit.get("message")),
                "author": clean((commit.get("author") or {}).get("username") or (commit.get("author") or {}).get("name"), 60),
            }
            for commit in listed
        ],
        "more": len(commits) - len(listed),
        "preheader": first_line(head.get("message")) or headline,
    }


def describe_pull_request(kind: str, payload: dict, branch: str, ctx: dict) -> dict:
    pr = payload.get("pull_request") or {}
    number = pr.get("number")
    verb = {"pr_opened": "opened", "pr_merged": "merged", "pr_closed": "closed"}[kind]
    label = {
        "pr_opened": "Pull request opened",
        "pr_merged": "Pull request merged",
        "pr_closed": "Pull request closed without merge",
    }[kind]
    if kind == "pr_opened" and pr.get("draft"):
        label += " (draft)"
    when = pr.get({"pr_opened": "created_at", "pr_merged": "merged_at", "pr_closed": "closed_at"}[kind])
    head_ref = (pr.get("head") or {}).get("label") or (pr.get("head") or {}).get("ref") or "?"
    actor = (payload.get("sender") or {}).get("login") or (pr.get("user") or {}).get("login") or ""
    title = clean(pr.get("title"), 300)

    commits, total = [], pr.get("commits") if isinstance(pr.get("commits"), int) else 0
    if isinstance(number, int):
        # shortcut: only the first 100 PR commits are fetched, so PRs with more list commits 91-100; paginate if that matters.
        fetched = github_get(f"{ctx['api']}/repos/{ctx['repo']}/pulls/{number}/commits?per_page=100", ctx["token"])
        if isinstance(fetched, list):
            commits = [commit for commit in fetched if isinstance(commit, dict)][-MAX_LISTED_COMMITS:][::-1]
            total = max(total, len(fetched))

    rows = [
        ("Repository", ctx["repo"]),
        ("Event", label),
        ("Pull request", f"#{number} {title}"),
        ("Branch", f"{head_ref} \u2192 {branch}"),
        ("Author", actor),
        ("Date", format_time(when, ctx)),
    ]
    if total:
        rows.append(("Commits", str(total)))
    pr_url = safe_url(pr.get("html_url"), ctx)
    return {
        "kind": kind,
        "headline": f"Pull Request #{number} {verb}",
        "rows": rows,
        "block": None,
        "link": ("View pull request", pr_url) if pr_url else None,
        "commits": [
            {
                "sha": str(commit.get("sha") or "")[:7],
                "url": safe_url(commit.get("html_url"), ctx),
                "message": first_line((commit.get("commit") or {}).get("message")),
                "author": clean(
                    (commit.get("author") or {}).get("login")
                    or ((commit.get("commit") or {}).get("author") or {}).get("name"),
                    60,
                ),
            }
            for commit in commits
        ],
        "more": max(total - len(commits), 0),
        "preheader": title or label,
    }


def describe_test(payload: dict, branch: str, ctx: dict) -> dict:
    run_url = f"{ctx['server']}/{ctx['repo']}/actions/runs/{ctx['run_id']}" if ctx["run_id"].isdigit() else ""
    configuration = (
        f"Events: {', '.join(sorted(ctx['events']))}\n"
        f"Branches: {', '.join(ctx['branches'])}\n"
        f"Recipients: {ctx['recipient_count']}"
    )
    return {
        "kind": "test",
        "headline": "Test notification",
        "rows": [
            ("Repository", ctx["repo"]),
            ("Event", "Manual test (workflow_dispatch)"),
            ("Branch", branch),
            ("Author", (payload.get("sender") or {}).get("login") or ""),
            ("Date", format_time(None, ctx)),
        ],
        "block": ("Active configuration", configuration),
        "link": ("View workflow run", run_url) if run_url else None,
        "commits": [],
        "more": 0,
        "preheader": "The Brevo SMTP e-mail notifications are working.",
    }


def footer(ctx: dict) -> str:
    return (
        f"Automated notification from GitHub Actions for {ctx['repo']}. "
        "Recipients are managed in the repository variable NOTIFICATION_EMAILS."
    )


def render_text(notification: dict, ctx: dict) -> str:
    lines = [notification["headline"], ""]
    lines += [f"{label}: {value}" for label, value in notification["rows"]]
    if notification["block"]:
        lines += ["", f"{notification['block'][0]}:", notification["block"][1]]
    if notification["commits"]:
        lines += ["", "Commits:"]
        lines += [f"- {c['sha']} {c['message']} ({c['author']})" for c in notification["commits"]]
        if notification["more"]:
            lines.append(f"... and {notification['more']} more")
    if notification["link"]:
        lines += ["", f"{notification['link'][0]}: {notification['link'][1]}"]
    lines += ["", "--", footer(ctx)]
    return "\n".join(lines) + "\n"


def render_html(notification: dict, ctx: dict, subject: str) -> str:
    e = lambda value: html.escape(str(value), quote=True)  # noqa: E731
    color = COLORS[notification["kind"]]

    rows = "".join(
        '<tr><td style="padding:6px 16px 6px 0;color:#59636e;font-size:13px;white-space:nowrap;vertical-align:top;">'
        f"{e(label)}</td>"
        f'<td style="padding:6px 0;font-size:14px;color:#1f2328;word-break:break-word;">{e(value)}</td></tr>'
        for label, value in notification["rows"]
    )

    block = ""
    if notification["block"]:
        label, text = notification["block"]
        block = (
            f'<p style="margin:20px 0 6px;font-size:13px;color:#59636e;">{e(label)}</p>'
            '<div style="padding:12px 14px;background:#f6f8fa;border:1px solid #d0d7de;border-radius:6px;'
            f'font-size:14px;line-height:1.5;white-space:pre-wrap;word-break:break-word;">{e(text)}</div>'
        )

    button = ""
    if notification["link"]:
        label, url = notification["link"]
        button = (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:24px 0 4px;">'
            f'<tr><td style="border-radius:6px;background:{color};">'
            f'<a href="{e(url)}" style="display:inline-block;padding:10px 18px;font-size:14px;font-weight:600;'
            f'color:#ffffff;text-decoration:none;border-radius:6px;">{e(label)}</a></td></tr></table>'
        )

    commits = ""
    if notification["commits"]:
        items = ""
        for commit in notification["commits"]:
            sha = (
                f'<a href="{e(commit["url"])}" style="color:#0969da;text-decoration:none;">{e(commit["sha"])}</a>'
                if commit["url"]
                else e(commit["sha"])
            )
            items += (
                '<tr><td width="1%" style="padding:7px 12px 7px 0;border-top:1px solid #eaeef2;vertical-align:top;'
                f'font-family:{MONO};font-size:12px;white-space:nowrap;">{sha}</td>'
                '<td style="padding:7px 0;border-top:1px solid #eaeef2;font-size:13px;line-height:1.4;word-break:break-word;">'
                f'{e(commit["message"])}<span style="color:#59636e;"> &mdash; {e(commit["author"])}</span></td></tr>'
            )
        if notification["more"]:
            items += (
                '<tr><td colspan="2" style="padding:7px 0;border-top:1px solid #eaeef2;font-size:13px;color:#59636e;">'
                f"&hellip; and {e(notification['more'])} more</td></tr>"
            )
        commits = (
            '<p style="margin:24px 0 4px;font-size:13px;color:#59636e;">Commits</p>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{items}</table>'
        )

    repo_url = safe_url(f"{ctx['server']}/{ctx['repo']}", ctx)
    repo_link = (
        f'<a href="{e(repo_url)}" style="color:#ffffff;text-decoration:none;">{e(ctx["repo"])}</a>'
        if repo_url
        else e(ctx["repo"])
    )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light only">'
        f"<title>{e(subject)}</title></head>"
        '<body style="margin:0;padding:0;background:#f6f8fa;">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
        f"{e(notification['preheader'])}</div>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f6f8fa;">'
        '<tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="max-width:600px;background:#ffffff;border:1px solid #d0d7de;border-radius:8px;font-family:{FONT};color:#1f2328;">'
        f'<tr><td style="background:{color};padding:20px 24px;border-radius:8px 8px 0 0;color:#ffffff;">'
        f'<div style="font-size:12px;letter-spacing:0.06em;text-transform:uppercase;opacity:0.9;">{repo_link}</div>'
        f'<div style="font-size:20px;font-weight:600;line-height:1.3;margin-top:4px;">{e(notification["headline"])}</div>'
        "</td></tr>"
        '<tr><td style="padding:20px 24px 24px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>'
        f"{block}{button}{commits}"
        "</td></tr>"
        '<tr><td style="padding:14px 24px;border-top:1px solid #d0d7de;font-size:12px;line-height:1.5;color:#59636e;">'
        f"{e(footer(ctx))}</td></tr>"
        "</table></td></tr></table></body></html>"
    )


