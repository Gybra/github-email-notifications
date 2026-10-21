from notification_core import *

def classify(event_name: str, payload: dict, ref_name: str):
    """Map the GitHub event to (kind, branch); kind None means 'not notified'."""
    if event_name == "push":
        ref = payload.get("ref") or ""
        return ("push", ref[len("refs/heads/"):]) if ref.startswith("refs/heads/") else (None, "")
    if event_name in ("pull_request", "pull_request_target"):
        pr = payload.get("pull_request") or {}
        branch = (pr.get("base") or {}).get("ref") or ""
        if payload.get("action") == "opened":
            return "pr_opened", branch
        if payload.get("action") == "closed":
            return ("pr_merged" if pr.get("merged") else "pr_closed"), branch
        return None, branch
    if event_name == "workflow_dispatch":
        return "test", ref_name
    return None, ""


