from notification_core import *

def github_get(url: str, token: str):
    """GET a GitHub REST API URL; returns the JSON or None (non-fatal) on any failure."""
    if not token:
        return None
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-email-notifications",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        log("warning", f"GitHub API returned HTTP {exc.code} (check the workflow 'permissions'); continuing without it.")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        log("warning", f"GitHub API request failed ({type(exc).__name__}); continuing without it.")
    return None


def merged_pr_for_push(ctx: dict, sha, branch: str):
    """Number of the merged PR that produced this push, so the push e-mail is skipped."""
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        return None
    pulls = github_get(f"{ctx['api']}/repos/{ctx['repo']}/commits/{sha}/pulls", ctx["token"])
    for pr in pulls if isinstance(pulls, list) else []:
        if (
            isinstance(pr, dict)
            and pr.get("merged_at")
            and pr.get("merge_commit_sha") == sha
            and (pr.get("base") or {}).get("ref") == branch
        ):
            return pr.get("number")
    return None


