"""Finding a paired device again after its wireless-debugging port changes.

Android picks a new random port every time wireless debugging restarts, so a
stored ip:port goes stale. mDNS would announce the new one, but multicast
doesn't cross Docker's bridge network (and host networking would expose the
adb server's unauthenticated port to the LAN). Instead: scan the device's own
last-known IP for open ports in the wireless-debugging range, and accept a
port only if `adb get-serialno` there returns the serial we already trust.

Only an IP already stored for a paired device is ever scanned, and it must be
private — the same rule as every other address this app touches."""
import asyncio
import logging
import os

import adb_client
import db

logger = logging.getLogger("discovery")

SCAN_TIMEOUT_SECONDS = 0.5
SCAN_CONCURRENCY = 256
MAX_CANDIDATES = 10  # open ports tried with `adb connect` before giving up


def _port_range() -> range:
    raw = os.environ.get("ADB_SCAN_PORTS", "30000-49999")
    try:
        low, high = (int(p) for p in raw.split("-", 1))
    except ValueError:
        low, high = 30000, 49999
    low, high = max(1024, low), min(65535, high)
    if low > high:
        low, high = 30000, 49999
    return range(low, high + 1)


async def _is_open(ip: str, port: int, sem: asyncio.Semaphore) -> int | None:
    async with sem:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), SCAN_TIMEOUT_SECONDS)
        except (OSError, asyncio.TimeoutError):
            return None
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return port


async def _open_ports(ip: str, ports: range) -> list[int]:
    sem = asyncio.Semaphore(SCAN_CONCURRENCY)
    results = await asyncio.gather(*(_is_open(ip, p, sem) for p in ports))
    return [p for p in results if p is not None]


def find_device_port(ip: str, serial: str) -> str | None:
    """Returns the ip:port where `serial` now answers, or None. Blocking —
    call from a worker thread, never from the event loop."""
    adb_client._validate_addr(f"{ip}:5555")  # private-IP check on the host part
    open_ports = asyncio.run(_open_ports(ip, _port_range()))
    for port in open_ports[:MAX_CANDIDATES]:
        addr = f"{ip}:{port}"
        try:
            adb_client.connect(addr)
            if adb_client.get_serialno(addr) == serial:
                return addr
        except adb_client.AdbError:
            continue
    return None


def ensure_connected(device, allow_scan: bool = True) -> str:
    """Connects to `device` and confirms its identity, returning the working
    address. Falls back to a port scan of its last IP when the stored port has
    gone stale, and records the new address when that finds it."""
    serial, addr = device["serial"], device["last_connect_addr"]
    if not addr:
        raise adb_client.AdbError("No known address for this device — reconnect it on the Devices page")
    try:
        adb_client.connect(addr)
        confirmed = adb_client.get_serialno(addr)
    except adb_client.AdbError:
        confirmed = None
    if confirmed == serial:
        db.touch_device(serial, addr)
        return addr
    if confirmed is not None:
        # Something answered, but it's a different device: never scan past
        # an identity mismatch, surface it.
        raise adb_client.AdbError("Device address now answers as a different serial")
    if not allow_scan:
        raise adb_client.AdbError(f"Device not reachable at {addr}")
    ip = addr.rpartition(":")[0]
    logger.info("%s: stale address %s, scanning %s", serial, addr, ip)
    found = find_device_port(ip, serial)
    if found is None:
        raise adb_client.AdbError(
            f"Device not found on {ip} — is wireless debugging on, and is it still at that IP?"
        )
    db.touch_device(serial, found)
    return found
