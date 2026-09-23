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


def is_legacy_serial(serial: str) -> bool:
    """Devices paired before 1.0 were recorded under adb's transport name —
    their ip:port at pairing time — instead of their hardware serial."""
    try:
        adb_client._validate_addr(serial)
    except adb_client.AdbError:
        return False
    return True


def is_same_device(stored: str, addr: str, confirmed: str) -> bool:
    if confirmed == stored:
        return True
    # A legacy record has no real serial to compare, so the best available
    # proof is the one it was already relying on: the device is on the IP it
    # was paired on, and it completed the TLS handshake — which a phone only
    # does for a host key it has paired with. Never when the serial that
    # answered already has its own record: that's a different phone that took
    # over the IP, and it keeps its own trust (a push must not reach it
    # through the legacy record's).
    return is_legacy_serial(stored) and db.get_device(confirmed) is None and (
        adb_client.split_host_port(stored)[0] == adb_client.split_host_port(addr)[0]
    )


def record(stored: str, confirmed: str, addr: str) -> str:
    """Stores the working address, upgrading a legacy record to its real
    serial. Returns the serial the device is now filed under."""
    if confirmed != stored:
        if db.rename_device(stored, confirmed):
            logger.info("%s: re-keyed to its hardware serial %s", stored, confirmed)
        else:
            logger.warning("%s: %s is already paired separately; using that record", stored, confirmed)
    db.touch_device(confirmed, addr)
    return confirmed


def find_device_port(ip: str, serial: str) -> tuple[str, str] | None:
    """Returns (confirmed serial, ip:port) where `serial` now answers, or
    None. Blocking — call from a worker thread, never from the event loop."""
    adb_client._validate_addr(adb_client.join_host_port(ip, 5555))  # private-IP check on the host part
    open_ports = asyncio.run(_open_ports(ip, _port_range()))
    for port in open_ports[:MAX_CANDIDATES]:
        addr = adb_client.join_host_port(ip, port)
        try:
            adb_client.connect(addr)
            confirmed = adb_client.get_serialno(addr)
        except adb_client.AdbError:
            continue
        if is_same_device(serial, addr, confirmed):
            return confirmed, addr
    return None


def ensure_connected(device, allow_scan: bool = True) -> tuple[str, str]:
    """Connects to `device` and confirms its identity, returning (serial,
    working address). The serial differs from device["serial"] only when a
    legacy record was just upgraded. Falls back to a port scan of its last
    IP when the stored port has gone stale, and records the new address."""
    serial, addr = device["serial"], device["last_connect_addr"]
    if not addr:
        raise adb_client.AdbError("No known address for this device — reconnect it on the Devices page")
    try:
        adb_client.connect(addr)
        confirmed = adb_client.get_serialno(addr)
    except adb_client.AdbError:
        confirmed = None
    if confirmed is not None:
        if is_same_device(serial, addr, confirmed):
            return record(serial, confirmed, addr), addr
        # Something answered, but it's a different device: never scan past
        # an identity mismatch, surface it.
        raise adb_client.AdbError("Device address now answers as a different serial")
    if not allow_scan:
        raise adb_client.AdbError(f"Device not reachable at {addr}")
    ip = adb_client.split_host_port(addr)[0]
    logger.info("%s: stale address %s, scanning %s", serial, addr, ip)
    found = find_device_port(ip, serial)
    if found is None:
        raise adb_client.AdbError(
            f"Device not found on {ip} — is wireless debugging on, and is it still at that IP?"
        )
    confirmed, found_addr = found
    return record(serial, confirmed, found_addr), found_addr
