import ipaddress
import os
import re
import subprocess

ADB_HOST = os.environ.get("ADB_HOST", "adb-server")
ADB_PORT = os.environ.get("ADB_PORT", "5037")

PAIRING_CODE_RE = re.compile(r"^\d{6}$")


class AdbError(Exception):
    pass


def _validate_addr(addr: str) -> str:
    """Only literal IP:port on a private/loopback range — never passed to a shell,
    but still validated so a malformed value can't be misread as an adb option
    (anything starting with '-') or reach an address outside the LAN."""
    if not addr or addr.startswith("-"):
        raise AdbError("Invalid address")
    host, sep, port = addr.rpartition(":")
    if not sep or not host or not port:
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


def _validate_serial(serial: str) -> str:
    if not serial or serial.startswith("-"):
        raise AdbError("Invalid device serial")
    return serial


def _run(*args: str, timeout: int) -> subprocess.CompletedProcess:
    cmd = ["adb", "-H", ADB_HOST, "-P", ADB_PORT, *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"adb command timed out after {timeout}s") from exc
    except OSError as exc:
        raise AdbError(f"could not run adb: {exc}") from exc


def pair(pairing_addr: str, code: str) -> str:
    pairing_addr = _validate_addr(pairing_addr)
    code = _validate_pairing_code(code)
    result = _run("pair", pairing_addr, code, timeout=15)
    if result.returncode != 0 or "successfully paired" not in result.stdout.lower():
        raise AdbError(result.stderr.strip() or result.stdout.strip() or "Pairing failed")
    return result.stdout.strip()


def connect(connect_addr: str) -> str:
    connect_addr = _validate_addr(connect_addr)
    result = _run("connect", connect_addr, timeout=15)
    out = result.stdout.strip()
    if result.returncode != 0 or "unable to connect" in out.lower() or "failed" in out.lower():
        raise AdbError(out or result.stderr.strip() or "Connect failed")
    return out


def get_serialno(target: str) -> str:
    """`target` may be an ip:port (validated as an address) or an already-known
    device serial (validated as an opaque, non-flag token)."""
    validated = _validate_addr(target) if ":" in target else _validate_serial(target)
    result = _run("-s", validated, "get-serialno", timeout=10)
    serial = result.stdout.strip()
    if result.returncode != 0 or not serial or serial.lower() == "unknown":
        raise AdbError(result.stderr.strip() or "Device did not report a serial")
    return serial


def install(serial: str, apk_path: str, timeout: int = 180) -> subprocess.CompletedProcess:
    serial = _validate_serial(serial)
    # apk_path is always a server-generated /data/staging/<repo_id>/<sha256>.apk
    # path, never user input, so no separate validation needed here. -r only —
    # never -g (auto-grant permissions), -d (allow downgrade) or -t (test APKs).
    return _run("-s", serial, "install", "-r", apk_path, timeout=timeout)


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
_ABI_RE = re.compile(r"^[a-z0-9_-]+$")


def validate_package(package: str) -> str:
    if not PACKAGE_RE.match(package or ""):
        raise AdbError("Invalid package name")
    return package


def installed_version(serial: str, package: str) -> tuple[int | None, str | None] | None:
    """(versionCode, versionName) of `package` on the device, or None if it
    isn't installed. Raises AdbError if the device can't be queried."""
    serial = _validate_serial(serial)
    package = validate_package(package)
    result = _run("-s", serial, "shell", "dumpsys", "package", package, timeout=20)
    if result.returncode != 0:
        raise AdbError(result.stderr.strip() or "Could not query installed packages")
    if f"Package [{package}]" not in result.stdout:
        return None
    code = _VERSION_CODE_RE.search(result.stdout)
    name = _VERSION_NAME_RE.search(result.stdout)
    return (int(code.group(1)) if code else None, name.group(1) if name else None)


def device_abis(serial: str) -> list[str]:
    """The device's supported ABIs, most preferred first."""
    serial = _validate_serial(serial)
    result = _run("-s", serial, "shell", "getprop", "ro.product.cpu.abilist", timeout=10)
    if result.returncode != 0:
        raise AdbError(result.stderr.strip() or "Could not read device ABIs")
    return [a for a in result.stdout.strip().split(",") if _ABI_RE.match(a)]
