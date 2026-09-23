import os
import secrets
import time

from fastapi import HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import db

SECRET_KEY = os.environ.get("SECRET_KEY")
APP_USERNAME = os.environ.get("APP_USERNAME")
APP_PASSWORD = os.environ.get("APP_PASSWORD")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN") or None
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() != "false"

SESSION_MAX_AGE = 12 * 60 * 60  # 12 hours
COOKIE_NAME = "adb_server_session"
# Password accepted, two-factor code still to come. Short-lived, and never
# accepted by require_auth: it only unlocks the code form.
MFA_PENDING_COOKIE = "adb_server_mfa_pending"
MFA_PENDING_MAX_AGE = 5 * 60
# "Trust this browser for 30 days": a signed random token, whose hash is
# stored (mfa.trust_browser) so it can be revoked from Settings.
MFA_TRUST_COOKIE = "adb_server_mfa_trust"
MFA_TRUST_MAX_AGE = 30 * 24 * 60 * 60

# Fail closed: refuse to start rather than run with no auth, or auth that's
# obviously still a placeholder.
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is not set — refusing to start with no session signing key")
if not APP_USERNAME or not APP_PASSWORD:
    raise RuntimeError("APP_USERNAME and APP_PASSWORD must both be set — refusing to start unauthenticated")
if APP_PASSWORD.lower() in ("change_me", "changeme", "password", "admin"):
    raise RuntimeError("APP_PASSWORD is still a placeholder value — set a real password")

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="adb-server-session")
_mfa_pending_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="adb-server-mfa-pending")
_mfa_trust_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="adb-server-mfa-trust")

# Single-process, single-worker app (see app/Dockerfile) — in-memory rate
# limiting is fine and needs no shared store.
_failed_attempts: dict[str, list[float]] = {}
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300
# Hard ceiling on tracked clients, so a flood of distinct source IPs can't grow
# the dict without bound between sweeps.
MAX_TRACKED_CLIENTS = 4096


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _sweep(now: float) -> None:
    """Drop every client whose attempts have all aged out — not just the one
    being looked at, or each IP that ever failed once would stay forever —
    then evict least-recently-seen clients past the ceiling."""
    for key in [k for k, ts in _failed_attempts.items() if not ts or now - ts[-1] >= WINDOW_SECONDS]:
        del _failed_attempts[key]
    excess = len(_failed_attempts) - MAX_TRACKED_CLIENTS
    if excess > 0:
        for key, _ in sorted(_failed_attempts.items(), key=lambda kv: kv[1][-1])[:excess]:
            del _failed_attempts[key]


def check_rate_limit(request: Request) -> None:
    now = time.time()
    _sweep(now)
    key = _client_key(request)
    attempts = [t for t in _failed_attempts.get(key, []) if now - t < WINDOW_SECONDS]
    if attempts:
        _failed_attempts[key] = attempts
    if len(attempts) >= MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.")


def record_failed_attempt(request: Request) -> None:
    now = time.time()
    _sweep(now)
    _failed_attempts.setdefault(_client_key(request), []).append(now)


def _same(a: str, b: str) -> bool:
    # Bytes, not str: compare_digest raises TypeError on non-ASCII str, which
    # turned a username like "é" into a 500.
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_credentials(username: str, password: str) -> bool:
    # Both compared every time, so a wrong username doesn't short-circuit
    # before the password comparison and leak timing about which was wrong.
    user_ok = _same(username, APP_USERNAME)
    pass_ok = _same(password, APP_PASSWORD)
    return user_ok and pass_ok


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def create_session(response: Response, csrf_token: str | None = None) -> str:
    """Sets a new session cookie. `csrf_token` lets a caller render a page
    with the new session's token before setting the cookie on it."""
    csrf_token = csrf_token or new_csrf_token()
    token = _serializer.dumps({"authenticated": True, "csrf": csrf_token, "epoch": db.get_session_epoch()})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
        path="/",
    )
    return csrf_token


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, httponly=True, samesite="strict",
                        secure=COOKIE_SECURE, path="/")


def start_mfa(response: Response) -> None:
    _set_cookie(response, MFA_PENDING_COOKIE,
                _mfa_pending_serializer.dumps({"first_factor": True, "epoch": db.get_session_epoch()}),
                MFA_PENDING_MAX_AGE)


def mfa_pending(request: Request) -> bool:
    token = request.cookies.get(MFA_PENDING_COOKIE)
    if not token:
        return False
    try:
        data = _mfa_pending_serializer.loads(token, max_age=MFA_PENDING_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(data, dict) and data.get("first_factor") is True and data.get("epoch") == db.get_session_epoch()


def clear_mfa_pending(response: Response) -> None:
    response.delete_cookie(MFA_PENDING_COOKIE, path="/")


def set_mfa_trust(response: Response, token: str) -> None:
    _set_cookie(response, MFA_TRUST_COOKIE, _mfa_trust_serializer.dumps(token), MFA_TRUST_MAX_AGE)


def read_mfa_trust(request: Request) -> str | None:
    """The trust token, if the cookie is genuine and unexpired. Whether it's
    still trusted (not revoked) is mfa.browser_trusted's call."""
    cookie = request.cookies.get(MFA_TRUST_COOKIE)
    if not cookie:
        return None
    try:
        token = _mfa_trust_serializer.loads(cookie, max_age=MFA_TRUST_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return token if isinstance(token, str) else None


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def revoke_sessions() -> None:
    """Every cookie issued before now stops validating (see read_session)."""
    db.bump_session_epoch()


def read_session(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        session = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(session, dict) or session.get("epoch") != db.get_session_epoch():
        return None
    return session


def require_auth(request: Request) -> dict:
    session = read_session(request)
    if not session or not session.get("authenticated"):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return session


def require_csrf(request: Request, session: dict, submitted_token: str | None) -> None:
    expected = session.get("csrf", "")
    if not submitted_token or not _same(submitted_token, expected):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token")
    check_origin(request)


def check_origin(request: Request) -> None:
    """Origin check for state-changing POSTs. Also used alone by /login, which
    has no session yet to carry a CSRF token."""
    origin = request.headers.get("origin")
    # "null" is a real, legitimate value browsers send (private/incognito mode,
    # tracking-prevention settings, some redirect chains) — it means "opaque,
    # can't tell you," not "the origin is literally null." Treat it the same
    # as a missing header: can't verify, so don't block on it. On session
    # routes the CSRF token is the actual defense and this is extra signal; on
    # /login it is the only cross-origin check there is.
    if origin is None or origin == "null":
        return
    # With ALLOWED_ORIGIN unset, same-origin is derived from this request's own
    # Host header rather than skipped — otherwise this check would silently
    # accept any Origin, contradicting what .env.example documents.
    allowed = ALLOWED_ORIGIN or f"{request.url.scheme}://{request.headers.get('host', '')}"
    if origin != allowed:
        raise HTTPException(status_code=403, detail="Request Origin does not match this app")
