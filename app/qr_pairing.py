"""Pairing a phone by QR code, as Android Studio does.

The QR code carries a service name and password we make up. The phone
scans it, then announces a pairing service under that name over mDNS. The
mdns container (see mdns/listener.py) writes what it hears to a file; when
our name shows up there, we `adb pair` its address with the password.
Then the phone's connect port is found — from its mDNS announcement if
there is one, else by scanning its IP — exactly as for a stale port.

What the file says came off the LAN unauthenticated. That's fine: the
pairing itself is the proof. Only the phone that scanned our QR code knows
the password, and `adb pair` fails for anything else."""
import json
import os
import secrets
import string
import threading
import time
from dataclasses import dataclass

import segno

import adb_client
import discovery

MDNS_FILE = os.environ.get("MDNS_FILE", "/mdns/services.json")
SESSION_SECONDS = 180
MAX_SESSIONS = 20
_ALPHANUMERIC = string.ascii_letters + string.digits


@dataclass(frozen=True)
class Session:
    token: str
    name: str  # the service name the phone will announce
    password: str
    expires: float

    def seconds_left(self) -> int:
        return max(0, int(self.expires - time.time()))


# In memory: a pairing QR code is only good for a few minutes, and the app
# runs as a single worker, so there's nothing to share or keep.
_sessions: dict[str, Session] = {}
_lock = threading.Lock()


def _random(n: int) -> str:
    return "".join(secrets.choice(_ALPHANUMERIC) for _ in range(n))


def _prune() -> None:
    now = time.time()
    for token in [t for t, s in _sessions.items() if s.expires <= now]:
        del _sessions[token]


def start() -> Session:
    with _lock:
        _prune()
        while len(_sessions) >= MAX_SESSIONS:
            del _sessions[min(_sessions, key=lambda t: _sessions[t].expires)]
        session = Session(secrets.token_urlsafe(16), f"adbserver-{_random(10)}", _random(16),
                          time.time() + SESSION_SECONDS)
        _sessions[session.token] = session
        return session


def get(token: str) -> Session | None:
    with _lock:
        _prune()
        return _sessions.get(token)


def take(token: str) -> Session | None:
    """Removes and returns the session: a QR code pairs once. Also stops
    two overlapping page refreshes from both trying to pair."""
    with _lock:
        _prune()
        return _sessions.pop(token, None)


def payload(session: Session) -> str:
    """The format Android's "Pair device with QR code" scanner reads."""
    return f"WIFI:T:ADB;S:{session.name};P:{session.password};;"


def svg(session: Session) -> str:
    """Inline SVG (the CSP allows no scripts, and no inline styles — segno
    draws with attributes only). Always dark on white, whatever the theme,
    so phone cameras read it."""
    return segno.make(payload(session), error="m").svg_inline(scale=6, border=4, dark="#000", light="#fff")


def listener_running() -> bool:
    return os.path.exists(MDNS_FILE)


def _services(kind: str) -> list[dict]:
    try:
        with open(MDNS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [s for s in data if isinstance(s, dict) and s.get("kind") == kind
            and isinstance(s.get("name"), str) and isinstance(s.get("ip"), str) and isinstance(s.get("port"), int)]


def _addr(service: dict) -> str | None:
    try:
        return adb_client._validate_addr(adb_client.join_host_port(service["ip"], service["port"]))
    except adb_client.AdbError:
        return None


def pairing_addr(session: Session) -> str | None:
    """Where the phone that scanned this session's QR code waits to pair."""
    for service in _services("pairing"):
        if service["name"] == session.name and (addr := _addr(service)):
            return addr
    return None


def complete(session: Session, pairing: str) -> tuple[str, str]:
    """Pairs, then finds and connects the phone's connect port. Returns
    (serial, connect address). Blocking — call from a worker thread."""
    adb_client.pair(pairing, session.password, qr=True)
    ip = adb_client.split_host_port(pairing)[0]
    announced = [a for s in _services("connect") if s["ip"] == ip and (a := _addr(s))]
    # A connection adb still holds from before this pairing is about to be
    # dropped by the phone, and adb's automatic reconnect would then race
    # ours. Start clean.
    for addr in announced:
        adb_client.disconnect(addr)
    found = discovery.first_device_on(ip, announced + announced)  # the announced port gets one retry
    if found is None:
        raise adb_client.AdbError(
            f"Paired, but couldn't connect to {ip} — check wireless debugging is still on, then try again"
        )
    return found
