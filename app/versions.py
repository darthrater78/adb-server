"""Whether the two containers are from the same release. The app and the
adb-server image are built and released together, but they're pulled and
recreated separately, so one can be left behind:

- **Image version.** The adb-server image writes its VERSION into a directory
  both containers mount (ADB_INFO_DIR, read-only here) when it starts; it's
  compared with this app's VERSION.
- **adb protocol.** The adb server is asked its protocol version over the
  existing connection and compared with this app's own adb client. A mismatch
  here breaks pairing and installs outright, whatever the image versions say.

The checks run in the background (check_and_alert, from the scheduler), never
while a page is being served; pages only read the last result."""
import logging
import os
import re
import socket
import subprocess
import threading
from dataclasses import dataclass, field
from functools import lru_cache

import adb_client
import db
import notify

logger = logging.getLogger("versions")

INFO_DIR = os.environ.get("ADB_INFO_DIR", "/adbinfo")
_VERSION_RE = re.compile(r"^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,6}(?:-[0-9A-Za-z.-]{1,40})?$")
_ADB_CLIENT_RE = re.compile(r"Android Debug Bridge version 1\.0\.(\d+)")
UPDATE_BOTH = "docker compose pull && docker compose up -d"


def _read_version() -> str:
    # Next to this file in the image (the Dockerfile copies it there); the
    # repo root when running from a checkout.
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "VERSION"), os.path.join(here, os.pardir, "VERSION")):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            continue
    return "unknown"


APP_VERSION = _read_version()


def adb_image_version() -> str | None:
    """The version the adb-server image published, or None if there is none
    (the directory isn't mounted, or the image predates publishing it)."""
    try:
        with open(os.path.join(INFO_DIR, "version"), encoding="utf-8") as f:
            value = f.read(65).strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value if _VERSION_RE.fullmatch(value) else None


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("adb server closed the connection")
        data += chunk
    return data


def server_protocol() -> int | None:
    """The adb server's protocol version (host:version), or None if it can't
    be reached or answers something unexpected."""
    try:
        with socket.create_connection((adb_client.ADB_HOST, int(adb_client.ADB_PORT)), timeout=3) as sock:
            sock.settimeout(3)
            sock.sendall(b"000chost:version")
            if _recv_exact(sock, 4) != b"OKAY":
                return None
            length = int(_recv_exact(sock, 4), 16)
            if not 0 < length <= 8:
                return None
            return int(_recv_exact(sock, length), 16)
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=1)
def client_protocol() -> int | None:
    """This app's adb client's protocol version (the last part of "1.0.41").
    The binary is part of the image, so it's read once."""
    try:
        result = subprocess.run(["adb", "version"], capture_output=True, text=True, errors="replace",
                                timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _ADB_CLIENT_RE.search(result.stdout)
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class Status:
    app: str
    adb_image: str | None
    server_protocol: int | None
    client_protocol: int | None
    problems: list[str] = field(default_factory=list)


def check() -> Status:
    """Runs both checks now. Blocking (a socket and, once, a subprocess)."""
    image, server, client = adb_image_version(), server_protocol(), client_protocol()
    problems = []
    if image is None and not os.path.isdir(INFO_DIR):
        problems.append("Can't tell which version the adb-server container runs: it publishes its version into "
                        f"{INFO_DIR}, which isn't mounted into both containers. Add the adbinfo lines from this "
                        "release's compose.yaml, then run: " + UPDATE_BOTH)
    elif image is None:
        problems.append(f"The adb-server container hasn't published its version, so it's older than 3.4.0 while the "
                        f"web app is {APP_VERSION}. Update both to the same release: {UPDATE_BOTH}")
    elif image != APP_VERSION:
        problems.append(f"The adb-server container runs {image}, but the web app is {APP_VERSION}. Update both to "
                        f"the same release: {UPDATE_BOTH}")
    if server is not None and client is not None and server != client:
        problems.append(f"The adb server speaks adb protocol {server}, but this app's adb client speaks {client}, so "
                        f"pairing and installs can fail. Update the adb-server image: {UPDATE_BOTH}")
    return Status(APP_VERSION, image, server, client, problems)


_last: list[Status] = []
_lock = threading.Lock()


def last() -> Status | None:
    """The most recent background check's result, without checking again."""
    with _lock:
        return _last[0] if _last else None


def problems() -> list[str]:
    status = last()
    return status.problems if status else []


def check_and_alert() -> Status:
    """Checks, keeps the result for pages to show, and notifies once per
    distinct problem (again only after it was fixed and came back)."""
    status = check()
    with _lock:
        _last[:] = [status]
    signature = " | ".join(status.problems)
    if signature and db.get_meta("version_alerted") != signature:
        for problem in status.problems:
            logger.warning(problem)
        notify.send("version_mismatch", "The two containers are out of step", "\n\n".join(status.problems))
        db.set_meta("version_alerted", signature)
    elif not signature and db.get_meta("version_alerted") is not None:
        logger.info("adb-server and the web app agree again (%s)", APP_VERSION)
        db.set_meta("version_alerted", None)
    return status
