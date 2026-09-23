import importlib.util
from pathlib import Path

import pytest



@pytest.fixture
def listener(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "mdns_listener", Path(__file__).resolve().parent.parent / "mdns" / "listener.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "OUT", str(tmp_path / "services.json"))
    monkeypatch.setattr(module, "MAX_SERVICES", 3)
    return module


class FakeInfo:
    def __init__(self, ip, port):
        self.ip, self.port = ip, port

    def parsed_addresses(self):
        return [self.ip]


class FakeZeroconf:
    def get_service_info(self, type_, name, timeout):
        return FakeInfo("192.168.1.50", 40000)


PAIRING = "_adb-tls-pairing._tcp.local."


def test_a_flood_cannot_crowd_out_a_new_phone(listener):
    zc, handler = FakeZeroconf(), listener.Listener()
    for i in range(10):
        handler.add_service(zc, PAIRING, f"junk-{i}.{PAIRING}")
    handler.add_service(zc, PAIRING, f"adbserver-abc.{PAIRING}")
    names = [s["name"] for s in listener._services.values()]
    assert len(names) == 3 and names[-1] == "adbserver-abc"


def test_public_addresses_are_ignored(listener, monkeypatch):
    zc = FakeZeroconf()
    monkeypatch.setattr(zc, "get_service_info", lambda *a, **k: FakeInfo("8.8.8.8", 40000))
    listener.Listener().add_service(zc, PAIRING, f"x.{PAIRING}")
    assert listener._services == {}
