"""Outbound notifications via Apprise (https://github.com/caronc/apprise),
which turns one URL per service — ntfy://, gotifys://, hassios://, discord://,
mailtos://, json:// and ~100 more — into a notification.

APPRISE_URLS holds credentials (tokens in the URLs), so it lives only in .env
and is never logged. Sending is best effort: a notification failure is logged
and swallowed, never allowed to fail a poll or a push."""
import logging
import os

logger = logging.getLogger("notify")

EVENTS = ("staged", "rejected", "install_success", "install_failed")


def _configured_urls() -> list[str]:
    raw = os.environ.get("APPRISE_URLS", "")
    return [u for u in raw.replace(",", " ").split() if u]


def _enabled_events() -> set[str]:
    raw = os.environ.get("NOTIFY_EVENTS", "")
    chosen = {e.strip() for e in raw.split(",") if e.strip()}
    return chosen & set(EVENTS) if chosen else set(EVENTS)


def send(event: str, title: str, body: str) -> bool:
    """Blocking (network). Call from a worker thread, never the event loop.
    Returns True if at least one service accepted it."""
    urls = _configured_urls()
    if not urls or event not in _enabled_events():
        return False
    try:
        import apprise

        ap = apprise.Apprise()
        for url in urls:
            if not ap.add(url):
                # Don't echo the URL: it usually carries a token.
                logger.warning("ignoring an invalid APPRISE_URLS entry")
        return bool(ap.notify(title=f"adb-server: {title}", body=body or title))
    except Exception:
        logger.exception("notification for %s failed", event)
        return False
