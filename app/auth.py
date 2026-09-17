import os
import secrets
import time

from fastapi import HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SECRET_KEY = os.environ.get("SECRET_KEY")
APP_USERNAME = os.environ.get("APP_USERNAME")
APP_PASSWORD = os.environ.get("APP_PASSWORD")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN") or None
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() != "false"

SESSION_MAX_AGE = 12 * 60 * 60  # 12 hours
COOKIE_NAME = "adb_server_session"

# Fail closed: refuse to start rather than run with no auth, or auth that's
# obviously still a placeholder.
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is not set — refusing to start with no session signing key")
if not APP_USERNAME or not APP_PASSWORD:
    raise RuntimeError("APP_USERNAME and APP_PASSWORD must both be set — refusing to start unauthenticated")
if APP_PASSWORD.lower() in ("change_me", "changeme", "password", "admin"):
    raise RuntimeError("APP_PASSWORD is still a placeholder value — set a real password")

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="adb-server-session")

# Single-process, single-worker app (see app/Dockerfile) — in-memory rate
# limiting is fine and needs no shared store.
_failed_attempts: dict[str, list[float]] = {}
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300
# Hard ceiling on tracked clients. Pruning used to happen only for the key
# being looked at, so every source IP that ever failed a login stayed in the
# dict forever.
MAX_TRACKED_CLIENTS = 4096


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _sweep(now: float) -> None:
    """Drop clients whose attempts have all aged out, then enforce a ceiling."""
    for key in [k for k, ts in _failed_attempts.items() if not ts or now - ts[-1] >= WINDOW_SECONDS]:
        del _failed_attempts[key]
    excess = len(_failed_attempts) - MAX_TRACKED_CLIENTS
    if excess > 0:
        # Still over budget: evict least-recently-seen first. Losing a partial
        # failure count for an idle client is the cheaper of the two failures.
        oldest = sorted(_failed_attempts.items(), key=lambda kv: kv[1][-1])[:excess]
        for key, _ in oldest:
            del _failed_attempts[key]


def check_rate_limit(request: Request) -> None:
    now = time.time()
    _sweep(now)
    key = _client_key(request)
    attempts = [t for t in _failed_attempts.get(key, []) if now - t < WINDOW_SECONDS]
    _failed_attempts[key] = attempts
    if len(attempts) >= MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.")


def record_failed_attempt(request: Request) -> None:
    now = time.time()
    _sweep(now)
    _failed_attempts.setdefault(_client_key(request), []).append(now)


def verify_credentials(username: str, password: str) -> bool:
    # compare_digest on both so a wrong username doesn't short-circuit before
    # the password comparison and leak timing about which field was wrong.
    user_ok = secrets.compare_digest(username, APP_USERNAME)
    pass_ok = secrets.compare_digest(password, APP_PASSWORD)
    return user_ok and pass_ok


def create_session(response: Response) -> str:
    csrf_token = secrets.token_urlsafe(32)
    token = _serializer.dumps({"authenticated": True, "csrf": csrf_token})
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


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def read_session(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        return _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def require_auth(request: Request) -> dict:
    session = read_session(request)
    if not session or not session.get("authenticated"):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return session


def check_origin(request: Request) -> None:
    """Origin check for any state-changing POST. Shared with /login, which has
    no session to carry a CSRF token yet and so had no cross-origin defence at
    all."""
    origin = request.headers.get("origin")
    # "null" is a real, legitimate value browsers send (private/incognito mode,
    # tracking-prevention settings, some redirect chains) — it means "opaque,
    # can't tell you," not "the origin is literally null." Treat it the same
    # as a missing header: can't verify, so don't block on it. On session
    # routes the CSRF token in require_csrf is the actual defence and this is
    # extra signal; on /login, where there is no session yet, it is the only
    # cross-origin check there is.
    if origin is None or origin == "null":
        return
    # With ALLOWED_ORIGIN unset, same-origin is derived from this request's own
    # Host header rather than skipped — otherwise this check would silently
    # accept any Origin, contradicting what .env.example documents.
    allowed = ALLOWED_ORIGIN or f"{request.url.scheme}://{request.headers.get('host', '')}"
    if origin != allowed:
        raise HTTPException(status_code=403, detail="Request Origin does not match this app")


def require_csrf(request: Request, session: dict, submitted_token: str | None) -> None:
    expected = session.get("csrf", "")
    if not submitted_token or not secrets.compare_digest(submitted_token, expected):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token")
    check_origin(request)
