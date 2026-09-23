"""Two-factor sign-in: time-based one-time codes (TOTP, RFC 6238 — what
Google Authenticator, Aegis, 1Password and the rest generate), single-use
recovery codes, and browsers trusted to skip the code for 30 days.

TOTP is a few lines over the standard library (HMAC-SHA1, 30-second steps,
6 digits), so there's no dependency to vet. State lives in the database:
  meta.mfa_secret          the enabled secret (base32); absent = MFA off
  meta.mfa_pending_secret  a secret shown during setup, not yet confirmed
  meta.mfa_last_step       the last time step accepted, so a code that was
                           just used can't be replayed within its window
  meta.mfa_failures, meta.mfa_locked_until   the lockout (see below)
Recovery codes and trusted browsers are stored only as SHA-256 hashes.

Unlocking and resetting without the phone: `python mfa_admin.py` inside the
app container (see that file). Shell access to the server is the proof of
ownership there."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import segno

import db

ISSUER = "ADB Server"
STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # also accept the previous and next code, for clock drift
RECOVERY_CODE_COUNT = 10
TRUST_DAYS = 30
# Password already passed by the time a code is asked for, so the lockout is
# global, not per IP: it's the last line against someone who has the password.
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60


# ---- TOTP ----

def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")  # 160 bits, as RFC 4226 recommends


def _code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 10 ** DIGITS:0{DIGITS}d}"


def _normalize_code(code: str) -> str:
    return "".join((code or "").split())


def matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    """The time step `code` belongs to (within the window), or None."""
    code = _normalize_code(code)
    if len(code) != DIGITS or not code.isdigit():
        return None
    current = int((time.time() if now is None else now) // STEP_SECONDS)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if hmac.compare_digest(_code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str) -> str:
    label = quote(f"{ISSUER}:{account}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")


def qr_svg(uri: str) -> str:
    return segno.make(uri, error="m").svg_inline(scale=5, border=4, dark="#000", light="#fff")


def format_secret(secret: str) -> str:
    """Groups of four, for typing into an app by hand."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# ---- state ----

def enabled() -> bool:
    return db.get_meta("mfa_secret") is not None


def pending_secret(create: bool = False) -> str | None:
    secret = db.get_meta("mfa_pending_secret")
    if secret is None and create:
        secret = new_secret()
        db.set_meta("mfa_pending_secret", secret)
    return secret


def enable(code: str) -> list[str] | None:
    """Confirms setup with a code from the app. Returns fresh recovery codes,
    or None if the code is wrong."""
    secret = pending_secret()
    if secret is None or (step := matching_step(secret, code)) is None:
        return None
    db.set_meta("mfa_secret", secret)
    db.set_meta("mfa_pending_secret", None)
    db.set_meta("mfa_last_step", str(step))
    unlock()
    return new_recovery_codes()


def disable() -> None:
    """MFA off: secret, recovery codes and trusted browsers all go."""
    for key in ("mfa_secret", "mfa_pending_secret", "mfa_last_step"):
        db.set_meta(key, None)
    db.replace_recovery_codes([])
    db.delete_trusted_browser()
    unlock()


# ---- lockout ----

def locked_for() -> int:
    """Seconds until the second step unlocks; 0 when it isn't locked."""
    until = db.get_meta("mfa_locked_until")
    return max(0, int(float(until) - time.time())) if until else 0


def _lock() -> None:
    db.set_meta("mfa_locked_until", str(time.time() + LOCK_SECONDS))
    db.set_meta("mfa_failures", None)


def unlock() -> None:
    db.set_meta("mfa_failures", None)
    db.set_meta("mfa_locked_until", None)


# ---- checking a code ----

def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_recovery(code: str) -> str:
    return "".join(c for c in (code or "").lower() if c.isalnum())


def new_recovery_codes() -> list[str]:
    """Replaces all recovery codes; returns the new ones, to show once."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
    codes = ["".join(secrets.choice(alphabet) for _ in range(10)) for _ in range(RECOVERY_CODE_COUNT)]
    db.replace_recovery_codes([_hash(c) for c in codes])
    return [f"{c[:5]}-{c[5:]}" for c in codes]


def check(code: str) -> str | None:
    """Verifies a sign-in (or confirmation) code: an authenticator code, or
    a recovery code, which is then used up. Returns "totp" or "recovery" on
    success, None on failure (always None while locked). Counts failures
    toward the lockout.

    The attempt is counted before the code is looked at, and each step is
    claimed atomically: sign-in runs in a threadpool, so a burst of parallel
    requests could otherwise all pass the lockout check, or all spend the
    same code, before any of them recorded anything."""
    secret = db.get_meta("mfa_secret")
    if secret is None or locked_for():
        return None
    attempt = db.increment_meta("mfa_failures")
    # Parallel guesses past the budget aren't checked, nor any that arrive
    # after one of them set the lock.
    if attempt > MAX_FAILURES or locked_for():
        return None
    step = matching_step(secret, code)
    if step is not None:
        if db.advance_meta("mfa_last_step", step):  # a code works once, even within its 30 seconds
            db.set_meta("mfa_failures", None)
            return "totp"
    elif len(normalized := _normalize_recovery(code)) == 10 and db.use_recovery_code(_hash(normalized)):
        db.set_meta("mfa_failures", None)
        return "recovery"
    if attempt >= MAX_FAILURES:
        _lock()
    return None


# ---- trusted browsers ----

def trust_browser(label: str, client: str | None) -> str:
    """Records a new trusted browser; returns the token for its cookie."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=TRUST_DAYS)
    db.add_trusted_browser(_hash(token), label[:120], client, expires.isoformat())
    return token


def browser_trusted(token: str | None) -> bool:
    if not token:
        return False
    row = db.get_trusted_browser(_hash(token))
    if row is None or row["expires_at"] <= datetime.now(timezone.utc).isoformat():
        return False
    db.touch_trusted_browser(row["token_hash"])
    return True


def browser_id(token: str | None) -> str | None:
    """The stored id (hash) of the browser holding `token`, to mark it on
    the Settings page."""
    return _hash(token) if token else None


def browser_label(user_agent: str) -> str:
    """A rough, human name for a browser, from its User-Agent."""
    ua = user_agent or ""
    browser = next((name for key, name in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                           ("Safari/", "Safari")) if key in ua), "Browser")
    system = next((name for key, name in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                          ("Windows", "Windows"), ("Mac OS", "macOS"), ("Linux", "Linux"))
                   if key in ua), "")
    return f"{browser} on {system}" if system else browser
