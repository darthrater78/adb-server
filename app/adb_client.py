import ipaddress
import os
import re
import subprocess
import time
from typing import NamedTuple

ADB_HOST = os.environ.get("ADB_HOST", "adb-server")
ADB_PORT = os.environ.get("ADB_PORT", "5037")

PAIRING_CODE_RE = re.compile(r"^\d{6}$")


class AdbError(Exception):
    pass


def split_host_port(addr: str) -> tuple[str, str]:
    """Splits host:port, including the bracketed [v6::addr]:port form that a
    plain rpartition(':') would tear apart at the wrong colon."""
    if addr.startswith("["):
        host, sep, rest = addr[1:].partition("]")
        if not sep or not rest.startswith(":"):
            raise AdbError("IPv6 addresses must be written as [address]:port")
        return host, rest[1:]
    host, sep, port = addr.rpartition(":")
    if not sep:
        raise AdbError("Address must be in host:port form")
    if ":" in host:
        # fd00::5:5555 could be either host fd00::5 port 5555 or a bare address.
        raise AdbError("IPv6 addresses must be written as [address]:port")
    return host, port


def join_host_port(host: str, port: int | str) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _validate_addr(addr: str) -> str:
    """Only literal IP:port on a private/loopback range — never passed to a shell,
    but still validated so a malformed value can't be misread as an adb option
    (anything starting with '-') or reach an address outside the LAN."""
    if not addr or addr.startswith("-"):
        raise AdbError("Invalid address")
    host, port = split_host_port(addr)
    if not host or not port:
        raise AdbError("Address must be in host:port form")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise AdbError("Address host must be a literal IP address")
    if not (ip.is_private or ip.is_loopback):
        raise AdbError("Address must be on a private network")
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        raise AdbError("Invalid port")
    return addr


def _validate_pairing_code(code: str) -> str:
    if not PAIRING_CODE_RE.match(code or ""):
        raise AdbError("Pairing code must be exactly 6 digits")
    return code


def _run(*args: str, timeout: int) -> subprocess.CompletedProcess:
    cmd = ["adb", "-H", ADB_HOST, "-P", ADB_PORT, *args]
    try:
        # errors="replace": adb relays device output, which isn't guaranteed
        # to be valid UTF-8, and a decode error must not escape as a crash.
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"adb command timed out after {timeout}s") from exc
    except OSError as exc:
        raise AdbError(f"could not run adb: {exc}") from exc


def pair(pairing_addr: str, code: str) -> str:
    """`code` is the 6-digit code the phone shows."""
    pairing_addr = _validate_addr(pairing_addr)
    code = _validate_pairing_code(code)
    result = _run("pair", pairing_addr, code, timeout=15)
    if result.returncode != 0 or "successfully paired" not in result.stdout.lower():
        raise AdbError(result.stderr.strip() or result.stdout.strip() or "Pairing failed")
    return result.stdout.strip()


READY_TIMEOUT_SECONDS = 10


def connect(connect_addr: str) -> str:
    """Connects and waits until the device is ready for commands. `adb
    connect` returns once the socket is up, but the TLS session can still be
    settling — or, just after pairing, be cut off while the phone replaces
    an older connection — and a command sent then fails."""
    connect_addr = _validate_addr(connect_addr)
    result = _run("connect", connect_addr, timeout=15)
    out = result.stdout.strip()
    if result.returncode != 0 or "unable to connect" in out.lower() or "failed" in out.lower():
        raise AdbError(out or result.stderr.strip() or "Connect failed")
    _wait_ready(connect_addr)
    return out


def _wait_ready(addr: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    state = ""
    while True:
        result = _run("-s", addr, "get-state", timeout=5)
        state = result.stdout.strip() or result.stderr.strip()
        if result.returncode == 0 and state == "device":
            return
        if "unauthorized" in state:
            raise AdbError(f"{addr} refused this server's key — pair it again")
        if time.monotonic() >= deadline:
            raise AdbError(f"{addr} connected but never became ready ({state or 'no state'})")
        time.sleep(0.5)


def disconnect(addr: str) -> None:
    """Drops adb's connection to `addr`, if it has one. Best effort."""
    addr = _validate_addr(addr)
    try:
        _run("disconnect", addr, timeout=10)
    except AdbError:
        pass


# What a real hardware serial looks like. Checked because it becomes the
# device's identity (and a URL path segment), and because an ip:port-shaped
# value here would mean we read adb's transport name, not the device's.
_SERIAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def get_serialno(target: str) -> str:
    """The device's own hardware serial. Not `adb get-serialno`: for a
    wireless device that returns adb's transport name — the ip:port — which
    changes every time wireless debugging restarts, so it can't be used to
    recognise the device on its next port. `target` is an ip:port."""
    target = _validate_addr(target)
    for prop in ("ro.serialno", "ro.boot.serialno"):
        result = _run("-s", target, "shell", "getprop", prop, timeout=10)
        if result.returncode != 0:
            raise AdbError(result.stderr.strip() or "Device did not report a serial")
        serial = result.stdout.strip()
        if _SERIAL_RE.match(serial) and serial.lower() != "unknown":
            return serial
    raise AdbError("Device did not report a serial")


def install(addr: str, apk_path: str, timeout: int = 180) -> subprocess.CompletedProcess:
    addr = _validate_addr(addr)
    # apk_path is always a server-generated /data/staging/<repo_id>/<sha256>.apk
    # path, never user input, so no separate validation needed here. -r only —
    # never -g (auto-grant permissions), -d (allow downgrade) or -t (test APKs).
    return _run("-s", addr, "install", "-r", apk_path, timeout=timeout)


def list_devices() -> list[str]:
    result = _run("devices", timeout=10)
    lines = result.stdout.strip().splitlines()[1:]
    return [line.split("\t")[0] for line in lines if "\t" in line and line.split("\t")[1].strip() == "device"]


# Java package names: dot-separated identifiers. Checked before a package name
# is ever placed on an `adb shell` command line, where the device's shell
# would otherwise interpret it.
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
_VERSION_CODE_RE = re.compile(r"\bversionCode=(\d+)")
_VERSION_NAME_RE = re.compile(r"\bversionName=(\S+)")
# "lastUpdateTime=2026-09-24 21:10:45": digits and separators only, so it is safe to store and show.
_UPDATE_TIME_RE = re.compile(r"\blastUpdateTime=(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
_ABI_RE = re.compile(r"^[a-z0-9_-]+$")


def validate_package(package: str) -> str:
    if not PACKAGE_RE.match(package or ""):
        raise AdbError("Invalid package name")
    return package


class PackageInfo(NamedTuple):
    version_code: int | None
    version_name: str | None
    update_time: str | None  # lastUpdateTime, in the device's own clock and format


def package_info(addr: str, package: str) -> PackageInfo | None:
    """What the device at `addr` reports for `package`, or None if it isn't
    installed. Raises AdbError if it can't be queried."""
    addr = _validate_addr(addr)
    package = validate_package(package)
    result = _run("-s", addr, "shell", "dumpsys", "package", package, timeout=20)
    if result.returncode != 0:
        raise AdbError(result.stderr.strip() or "Could not query installed packages")
    if f"Package [{package}]" not in result.stdout:
        return None
    code = _VERSION_CODE_RE.search(result.stdout)
    name = _VERSION_NAME_RE.search(result.stdout)
    updated = _UPDATE_TIME_RE.search(result.stdout)
    return PackageInfo(
        int(code.group(1)) if code else None,
        name.group(1) if name else None,
        updated.group(1) if updated else None,
    )


_MODEL_UNSAFE = re.compile(r"[^A-Za-z0-9 ._()+/-]")


def device_model(addr: str) -> str:
    """Make and model, e.g. "Google Pixel 8", for display. Device-reported,
    so reduced to a plain character set and length."""
    addr = _validate_addr(addr)
    parts = []
    for prop in ("ro.product.manufacturer", "ro.product.model"):
        result = _run("-s", addr, "shell", "getprop", prop, timeout=10)
        if result.returncode != 0:
            raise AdbError(result.stderr.strip() or "Could not read the device model")
        parts.append(_MODEL_UNSAFE.sub("", result.stdout.strip())[:40])
    maker, model = parts
    # Many models already start with the maker ("SM-S918B" doesn't; "Pixel 8" doesn't; "OnePlus 12" does).
    name = model if not maker or model.lower().startswith(maker.lower()) else f"{maker.title()} {model}"
    return name.strip()[:60]


def device_abis(addr: str) -> list[str]:
    """The supported ABIs of the device at `addr`, most preferred first."""
    addr = _validate_addr(addr)
    result = _run("-s", addr, "shell", "getprop", "ro.product.cpu.abilist", timeout=10)
    if result.returncode != 0:
        raise AdbError(result.stderr.strip() or "Could not read device ABIs")
    return [a for a in result.stdout.strip().split(",") if _ABI_RE.match(a)]
