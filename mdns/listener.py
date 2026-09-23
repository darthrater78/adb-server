"""Writes the Android wireless-debugging services announced on the LAN to
/mdns/services.json, for the app to read.

Why a separate container: after a phone scans a pairing QR code it
announces its pairing port only over mDNS, and multicast doesn't cross
Docker's bridge network. This listener runs on the host's network instead,
so the adb server never has to (its port would be on the LAN). It opens no
listening port of its own beyond mDNS, and holds no keys. It talks to the
app only through the file, which the app mounts read-only.

Everything in the file came off the network unauthenticated: the app
treats it as a hint, never as proof of a device's identity."""
import ipaddress
import json
import logging
import os
import threading

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

OUT = os.environ.get("MDNS_FILE", "/mdns/services.json")
TYPES = {
    "_adb-tls-pairing._tcp.local.": "pairing",
    "_adb-tls-connect._tcp.local.": "connect",
}
MAX_SERVICES = 256  # a noisy LAN can't grow the file without bound

logging.basicConfig(level=logging.INFO, format="%(levelname)s:mdns:%(message)s")
logger = logging.getLogger("mdns")

_services: dict[str, dict] = {}
_lock = threading.Lock()


def _private_ipv4(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.version == 4 and ip.is_private and not ip.is_loopback


def _write() -> None:
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(list(_services.values()), f)
    os.replace(tmp, OUT)  # atomic: the app never reads a half-written file


class Listener(ServiceListener):
    def _put(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name, timeout=3000)
        if info is None or not info.port:
            return
        ips = [a for a in info.parsed_addresses() if _private_ipv4(a)]
        if not ips:
            return
        instance = name[: -len(type_) - 1] if name.endswith("." + type_) else name
        with _lock:
            # Full: drop the oldest entry rather than refuse the new one, so
            # a host flooding the LAN with announcements can't lock out the
            # phone that's pairing right now. Re-announcing moves an entry last.
            _services.pop(name, None)
            while len(_services) >= MAX_SERVICES:
                del _services[next(iter(_services))]
            _services[name] = {"kind": TYPES[type_], "name": instance[:128], "ip": ips[0], "port": info.port}
            _write()
        logger.info("%s %s at %s:%s", TYPES[type_], instance[:128], ips[0], info.port)

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._put(zc, type_, name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._put(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        with _lock:
            if _services.pop(name, None) is not None:
                _write()


def main() -> None:
    with _lock:
        _write()  # an empty file tells the app the listener is running
    zc = Zeroconf()
    ServiceBrowser(zc, list(TYPES), Listener())
    logger.info("listening for adb wireless-debugging services")
    threading.Event().wait()


if __name__ == "__main__":
    main()
