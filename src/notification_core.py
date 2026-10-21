"""Send one GitHub event notification e-mail through Brevo SMTP (STARTTLS).

All event data comes from the payload file and environment variables; nothing
from the event is ever interpolated into this source (no workflow injection).
Everything rendered into HTML is escaped; recipient addresses are never logged.
"""
from __future__ import annotations

import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from fnmatch import fnmatchcase
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_BRANCHES = "main,master"
DEFAULT_EVENTS = "push,pr_opened,pr_merged"
DEFAULT_SENDER_NAME = "GitHub Notifications"
KNOWN_EVENTS = ("push", "pr_opened", "pr_merged", "pr_closed")
MAX_RECIPIENTS = 50  # guard for the Brevo free plan: every recipient counts as one e-mail
MAX_LISTED_COMMITS = 10
MAX_MESSAGE_CHARS = 1000
SMTP_TIMEOUT = 30
RETRY_DELAYS = (10, 30)  # seconds before the 2nd and the 3rd SMTP attempt

EMAIL_RE = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
)
SHA_RE = re.compile(r"[0-9a-f]{40}")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")

COLORS = {
    "push": "#0969da",
    "pr_opened": "#1a7f37",
    "pr_merged": "#8250df",
    "pr_closed": "#cf222e",
    "test": "#57606a",
}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "SFMono-Regular,Consolas,'Liberation Mono',Menlo,monospace"


class ConfigError(Exception):
    """Missing or invalid configuration: fail immediately."""


class SendError(Exception):
    """Permanent SMTP failure: retrying would not help."""


class TransientError(Exception):
    """Temporary SMTP or network failure: worth a retry."""


def log(level: str, message: str) -> None:
    """Emit a workflow command; the escaping stops event data from injecting other commands."""
    data = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{data}", flush=True)


def clean(value, limit: int = 200) -> str:
    """Single-line, length-limited text (safe for headers and log lines)."""
    text = CONTROL_RE.sub(" ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def parse_bool(value) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes", "on")


def parse_list(value) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]", value or "") if item.strip()]


def parse_events(value) -> set[str]:
    requested = [item.lower() for item in parse_list(value) or parse_list(DEFAULT_EVENTS)]
    unknown = [item for item in requested if item not in KNOWN_EVENTS]
    if unknown:
        log("warning", f"Ignoring unknown NOTIFICATION_EVENTS value(s): {', '.join(unknown)}. Allowed: {', '.join(KNOWN_EVENTS)}.")
    events = {item for item in requested if item in KNOWN_EVENTS}
    if not events:
        raise ConfigError(f"NOTIFICATION_EVENTS has no valid event. Allowed: {', '.join(KNOWN_EVENTS)}.")
    return events


def parse_recipients(value) -> list[str]:
    valid, seen, invalid = [], set(), 0
    for item in re.split(r"[,;\s]+", value or ""):
        if not item:
            continue
        if len(item) > 254 or not EMAIL_RE.fullmatch(item):
            invalid += 1
            continue
        if item.lower() not in seen:
            seen.add(item.lower())
            valid.append(item)
    if invalid:
        log("warning", f"Skipping {invalid} malformed address(es) in NOTIFICATION_EMAILS.")
    if not valid:
        raise ConfigError("NOTIFICATION_EMAILS is empty or has no valid address (use comma-separated e-mails).")
    if len(valid) > MAX_RECIPIENTS:
        raise ConfigError(f"NOTIFICATION_EMAILS has {len(valid)} addresses; the limit is {MAX_RECIPIENTS}.")
    return valid


def parse_sender(value) -> tuple[str, str]:
    name, address = parseaddr((value or "").strip())
    if not address or not EMAIL_RE.fullmatch(address):
        raise ConfigError("NOTIFICATION_FROM must be a Brevo-verified sender, e.g. 'notify@example.com' or 'Team <notify@example.com>'.")
    return clean(name, 80) or DEFAULT_SENDER_NAME, address


def load_timezone(name):
    try:
        return ZoneInfo((name or "").strip() or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        log("warning", f"Unknown timezone '{clean(name, 60)}', using UTC.")
        return timezone.utc


def load_payload(path) -> dict:
    if not path:
        raise ConfigError("GITHUB_EVENT_PATH is not set.")
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read the event payload ({type(exc).__name__}).") from None
    return payload if isinstance(payload, dict) else {}


