"""Signs an unsigned APK with a key this server keeps for its source, for a
source the operator explicitly opted in to (a watched repo's setting, or the
checkbox on an upload). Android refuses to install an unsigned APK at all, so
this is the only way such a build can reach a device.

One key per source — each watched repo (by its GitHub ID, which survives
removing and re-adding it) and one for uploads — never one shared key. Android
lets an APK update an installed app when the package name and the signer
match, so with a shared key any opted-in source could ship a build under
another source's package name and replace that app, data and all.

What that means, and what the opt-in says: the phone then trusts this server
as the app's signer. It will only accept updates to that app signed by the
same key (so, staged through this server) and refuses one signed by the
developer's own key, or the reverse, until the app is uninstalled. The key is
a PKCS#12 keystore next to the database; its password is sealed with
secretbox. Losing either, or SECRET_KEY, means apps signed by it can't be
updated in place again."""
import logging
import os
import re
import secrets
import subprocess
import threading

import db
import secretbox

logger = logging.getLogger("signing")

KEY_ALIAS = "adbserver"
UPLOADS = "uploads"
_SOURCE_RE = re.compile(r"^(uploads|github-[0-9]{1,20})$")
_PASS_ENV = "ADB_SERVER_SIGNING_PASS"  # passed through the environment, never argv
_create_lock = threading.Lock()


class SigningError(Exception):
    pass


# Shown wherever the opt-in is offered.
EXPLANATION = (
    "Android can't install an unsigned APK at all. With this on, an unsigned build is signed before it's "
    "staged, with a key this server keeps for this source alone. The phone then trusts that key as the app's "
    "signer: it only accepts updates signed by the same key (staged through this server from the same "
    "source), and refuses one signed by the developer's key, or the other way round, until the app is "
    "uninstalled. The key lives next to the "
    "database, protected by SECRET_KEY; back both up, or apps it signed can't be updated in place again. "
    "A build that is signed but whose signature doesn't verify is still refused."
)


def repo_source(github_id: int) -> str:
    """The signing source for a watched repo: its GitHub ID, not the row ID,
    so removing and re-adding the repo keeps the key its apps trust."""
    return f"github-{int(github_id)}"


def _source(source: str) -> str:
    if not _SOURCE_RE.fullmatch(source or ""):
        raise SigningError("Unknown signing source")
    return source


def keystore_path(source: str) -> str:
    return os.path.join(os.path.dirname(db.DB_PATH), "signing", f"{_source(source)}.p12")


def _password_key(source: str) -> str:
    return f"signing_key_password:{_source(source)}"


def _env(password: str) -> dict:
    return {**os.environ, _PASS_ENV: password}


def _run(cmd: list[str], password: str, timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", env=_env(password),
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SigningError(f"{os.path.basename(cmd[0])} could not run: {type(exc).__name__}") from exc
    if result.returncode != 0:
        # stderr of these tools names files and options, never the password.
        raise SigningError(f"{os.path.basename(cmd[0])} failed: {(result.stderr or result.stdout).strip()[:300]}")
    return result


def _password(source: str) -> str:
    """The source's keystore password, creating its keystore on first use."""
    with _create_lock:
        password = db.get_secret(_password_key(source))
        path = keystore_path(source)
        if password and os.path.exists(path):
            return password
        if os.path.exists(path):
            # A keystore without its password is unusable; never overwrite it,
            # or every app it signed loses its update path for good.
            raise SigningError("The signing key exists but its password is missing — restore the database, "
                               f"or remove signing/{source}.p12 to start a new key")
        password = secrets.token_hex(24)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        _run(["keytool", "-genkeypair", "-keystore", path, "-storetype", "PKCS12", "-alias", KEY_ALIAS,
              "-keyalg", "RSA", "-keysize", "3072", "-validity", "36500",
              "-dname", f"CN=ADB Server {source} {secrets.token_hex(4)}, O=ADB Server",
              "-storepass:env", _PASS_ENV, "-keypass:env", _PASS_ENV], password)
        os.chmod(path, 0o600)
        db.set_secret(_password_key(source), password)
        logger.info("created the APK signing key for %s", source)
        return password


def sign_in_place(apk_path: str, source: str) -> None:
    """Aligns and signs apk_path with the source's key, replacing it."""
    password = _password(source)
    aligned, signed = f"{apk_path}.aligned", f"{apk_path}.signed"
    try:
        _run(["zipalign", "-p", "-f", "4", apk_path, aligned], password)
        _run(["apksigner", "sign", "--ks", keystore_path(source), "--ks-type", "PKCS12", "--ks-key-alias", KEY_ALIAS,
              "--ks-pass", f"env:{_PASS_ENV}", "--key-pass", f"env:{_PASS_ENV}",
              # v4 writes a separate .idsig file for incremental installs,
              # which adb install here never uses.
              "--v4-signing-enabled", "false",
              "--out", signed, aligned], password)
        os.replace(signed, apk_path)
    finally:
        for leftover in (aligned, signed, f"{signed}.idsig"):
            try:
                os.remove(leftover)
            except FileNotFoundError:
                pass


def key_fingerprint(source: str) -> str | None:
    """SHA-256 of the source's signing certificate, or None before any use."""
    if not os.path.exists(keystore_path(source)):
        return None
    try:
        password = db.get_secret(_password_key(source))
    except secretbox.SecretUnreadable:
        return None
    if not password:
        return None
    result = _run(["keytool", "-list", "-keystore", keystore_path(source), "-storetype", "PKCS12", "-alias", KEY_ALIAS,
                   "-storepass:env", _PASS_ENV], password, timeout=30)
    for line in result.stdout.splitlines():
        if "SHA-256" in line or "SHA256" in line:
            return line.split(":", 1)[1].strip().replace(":", "").lower()
    return None


def sources() -> list[str]:
    """Every source that has a signing key, uploads first."""
    try:
        names = os.listdir(os.path.dirname(keystore_path(UPLOADS)))
    except FileNotFoundError:
        return []
    found = [n[:-4] for n in names if n.endswith(".p12") and _SOURCE_RE.fullmatch(n[:-4])]
    return sorted(found, key=lambda s: (s != UPLOADS, s))
