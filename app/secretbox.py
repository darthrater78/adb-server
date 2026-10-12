"""Encryption at rest for the secrets kept in the database: the GitHub token,
the TOTP secret, and saved notification URLs (which carry service tokens).

Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography`) with a key derived
from SECRET_KEY by HKDF. The key is never stored: it lives only in .env,
which sits outside the data directory, so a copy of the database or its
backup reveals none of these secrets on its own.

Changing SECRET_KEY makes every sealed value unreadable (as it already signs
out every session). Readers get SecretUnreadable and treat the secret as
unset, failing closed: the token and notification URLs are entered again,
and two-factor sign-in is reset with `python mfa_admin.py reset`."""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = "enc:v1:"
_fernet: Fernet | None = None
_fernet_for: str | None = None


class SecretUnreadable(Exception):
    """A sealed value that doesn't decrypt under the current SECRET_KEY."""


def _box() -> Fernet:
    global _fernet, _fernet_for
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        # auth.py already refuses to start without it; never fall back to a
        # key anyone could compute.
        raise RuntimeError("SECRET_KEY is not set")
    if _fernet is None or _fernet_for != secret:
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"adb-server/secretbox",
                   info=b"database secrets v1").derive(secret.encode())
        _fernet, _fernet_for = Fernet(base64.urlsafe_b64encode(key)), secret
    return _fernet


SLOW_PREFIX = "scrypt:v1:"
# scrypt at N=2**15, r=8: 32 MiB and some tens of milliseconds a guess.
_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}


def slow_hash(value: str) -> str:
    """scrypt of `value`, salted with a key derived from SECRET_KEY, for
    values too short to be safe behind a fast hash (recovery codes: about 49
    bits). A copy of the database can't be used to guess them at all, and one
    taken together with SECRET_KEY only slowly. The salt is the same for every
    value so a code can be looked up by its hash. Like a sealed value, it
    stops matching if SECRET_KEY changes."""
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        raise RuntimeError("SECRET_KEY is not set")
    salt = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"adb-server/secretbox",
                info=b"slow hashes v1").derive(secret.encode())
    return SLOW_PREFIX + hashlib.scrypt(value.encode(), salt=salt, **_SCRYPT).hex()


def is_sealed(value: str) -> bool:
    return value.startswith(PREFIX)


def seal(plain: str) -> str:
    return PREFIX + _box().encrypt(plain.encode()).decode()


def unseal(value: str) -> str:
    """The plaintext of a sealed value. A value from before encryption (no
    prefix) is returned as it is: startup seals those, see db.init_db."""
    if not is_sealed(value):
        return value
    try:
        return _box().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as exc:
        raise SecretUnreadable("stored secret can't be decrypted — was SECRET_KEY changed?") from exc
