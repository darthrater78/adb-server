"""Outbound notifications via Apprise (https://github.com/caronc/apprise),
which turns one URL per service — ntfy://, gotifys://, hassios://, discord://,
mailtos://, json:// and ~100 more — into a notification.

Services come from two places: APPRISE_URLS in .env (read-only here) and the
ones added on the Settings page (stored in the database, encrypted by
secretbox). The URLs hold credentials, so they are never logged or rendered whole — only Apprise's own
privacy-masked form is shown. Sending is best effort: a failure is logged and
swallowed, never allowed to fail a poll or a push."""
import logging
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import db

logger = logging.getLogger("notify")

EVENTS = ("staged", "rejected", "install_success", "install_failed", "token_expiring")
EVENT_LABELS = {
    "staged": "A new release was verified and staged",
    "rejected": "A release was rejected (pin mismatch or failed verification)",
    "install_success": "A push to a device succeeded",
    "install_failed": "A push to a device failed",
    "token_expiring": "The GitHub token saved in Settings expires within a week",
}
MAX_TARGETS = 20
MAX_URL_LENGTH = 2000


@dataclass(frozen=True)
class Target:
    id: int | None  # None for a service from .env
    url: str | None  # None: stored, but can't be decrypted
    label: str | None

    @property
    def from_env(self) -> bool:
        return self.id is None


def _env_urls() -> list[str]:
    raw = os.environ.get("APPRISE_URLS", "")
    return [u for u in raw.replace(",", " ").split() if u]


def targets() -> list[Target]:
    env = [Target(None, u, None) for u in _env_urls()]
    stored = [Target(r["id"], r["url"], r["label"]) for r in db.list_notify_targets()]
    return env + stored


def enabled_events() -> set[str]:
    """The Settings page's choice if one was saved, else NOTIFY_EVENTS, else all."""
    saved = db.get_meta("notify_events")
    raw = saved if saved is not None else os.environ.get("NOTIFY_EVENTS", "")
    if saved is None and not raw.strip():
        return set(EVENTS)
    return {e.strip() for e in raw.split(",") if e.strip()} & set(EVENTS)


def describe(url: str | None) -> dict:
    """Service name and a masked URL for display. Never returns the raw URL."""
    if url is None:
        return {"service": "unreadable", "masked": "can't be decrypted: SECRET_KEY changed? Remove and add again",
                "valid": False}
    try:
        import apprise

        ap = apprise.Apprise()
        if ap.add(url) and len(ap):
            plugin = ap[0]
            return {"service": plugin.service_name, "masked": plugin.url(privacy=True).split("?", 1)[0], "valid": True}
    except Exception as exc:
        # Type only: a traceback could carry the URL, and with it a token.
        logger.warning("could not parse a notification URL: %s", type(exc).__name__)
    scheme = url.split("://", 1)[0] if "://" in url else "?"
    return {"service": scheme, "masked": f"{scheme}://…", "valid": False}


def validate(url: str) -> str:
    """Raises ValueError with an operator-facing reason, or returns the URL."""
    url = url.strip()
    if not url:
        raise ValueError("Enter an Apprise URL")
    if len(url) > MAX_URL_LENGTH:
        raise ValueError(f"That URL is longer than {MAX_URL_LENGTH} characters")
    if any(c.isspace() for c in url):
        raise ValueError("An Apprise URL can't contain spaces — add services one at a time")
    if not describe(url)["valid"]:
        raise ValueError("Apprise doesn't recognise that URL — check the scheme against the list below")
    return url


_API_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_TAGS = 10


def build_api_url(server: str, key: str, tags: str = "") -> str:
    """An Apprise API server (https://github.com/caronc/apprise-api) as an
    Apprise URL. `server` may be just host[:port], a base URL with a
    reverse-proxy prefix, or the full .../notify/<key> URL, in which case
    `key` can be left blank. Raises ValueError with an operator-facing reason."""
    server, key = server.strip(), key.strip()
    if not server:
        raise ValueError("Enter the Apprise server's address, e.g. http://apprise.local:8000")
    if "://" not in server:
        server = "http://" + server
    parts = urlsplit(server)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("The server address must be an http:// or https:// URL")
    if parts.username or parts.password:
        raise ValueError("For a server that needs a login, add it as a plain Apprise URL instead")
    if parts.query or parts.fragment:
        raise ValueError("Leave query strings off the server address — use the Tags field")
    path = parts.path.rstrip("/")
    match = re.search(r"/notify/([^/]+)$", path)
    if match:
        key = key or match.group(1)
        path = path[:match.start()]
    if not key:
        raise ValueError("Enter the config key — the last part of .../notify/<key> on your Apprise server")
    if not _API_KEY_RE.match(key):
        raise ValueError("A config key is 1–128 letters, digits, '-' or '_'")
    tag_list = [t for t in re.split(r"[,\s]+", tags.strip()) if t]
    if len(tag_list) > MAX_TAGS or not all(_TAG_RE.match(t) for t in tag_list):
        raise ValueError(f"Tags are up to {MAX_TAGS} words of letters, digits, '-' or '_', separated by commas")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    netloc = f"{host}:{parts.port}" if parts.port else host
    scheme = "apprises" if parts.scheme == "https" else "apprise"
    url = f"{scheme}://{netloc}{path}/{key}"
    if tag_list:
        url += "?tags=" + ",".join(tag_list)
    return validate(url)


def _deliver(urls: list[str], title: str, body: str) -> bool:
    import apprise

    ap = apprise.Apprise()
    for url in urls:
        if not ap.add(url):
            # Don't echo the URL: it usually carries a token.
            logger.warning("ignoring an invalid notification URL")
    return bool(ap.notify(title=f"ADB Server: {title}", body=body or title))


def send(event: str, title: str, body: str) -> bool:
    """Blocking (network). Call from a worker thread, never the event loop.
    Returns True if at least one service accepted it."""
    urls = [t.url for t in targets() if t.url]  # an undecryptable one is skipped
    if not urls or event not in enabled_events():
        return False
    try:
        return _deliver(urls, title, body)
    except Exception as exc:
        logger.warning("notification for %s failed: %s", event, type(exc).__name__)
        return False


def test(target: Target) -> tuple[bool, str]:
    """Sends a test message to one service. Blocking. Returns (ok, message)."""
    if not target.url:
        return False, "Its saved address can't be decrypted (SECRET_KEY changed?) — remove it and add it again"
    try:
        ok = _deliver([target.url], "test notification",
                      "This is a test from ADB Server. If you can read it, notifications work.")
    except Exception as exc:
        logger.warning("test notification failed: %s", type(exc).__name__)
        return False, f"Error: {type(exc).__name__}"
    return (True, "Delivered") if ok else (False, "The service refused it or couldn't be reached — see the app log")
