"""Append-only record of security-relevant operator actions: logins, trust
changes, re-pins, repo and device changes, pushes. Never records secrets."""
import db

MAX_DETAIL = 500


def record(action: str, detail: str = "", client: str | None = None) -> None:
    db.insert_audit(action, detail[:MAX_DETAIL], client)
