import json

import pytest

import adb_client
import db
import discovery
import qr_pairing
from conftest import CSRF


@pytest.fixture
def mdns(tmp_path, monkeypatch):
    path = tmp_path / "services.json"
    monkeypatch.setattr(qr_pairing, "MDNS_FILE", str(path))

    def announce(*services):
        path.write_text(json.dumps(list(services)))
    announce()
    return announce


@pytest.fixture(autouse=True)
def no_sessions(monkeypatch):
    monkeypatch.setattr(qr_pairing, "_sessions", {})


def test_payload_is_androids_qr_format():
    s = qr_pairing.start()
    assert qr_pairing.payload(s) == f"WIFI:T:ADB;S:{s.name};P:{s.password};;"
    assert s.name.startswith("adbserver-") and adb_client.QR_PASSWORD_RE.match(s.password)
    assert qr_pairing.svg(s).startswith("<svg")


def test_sessions_are_single_use_and_expire(monkeypatch):
    s = qr_pairing.start()
    assert qr_pairing.take(s.token) == s and qr_pairing.take(s.token) is None
    s = qr_pairing.start()
    monkeypatch.setattr(qr_pairing.time, "time", lambda: s.expires + 1)
    assert qr_pairing.get(s.token) is None


def test_pairing_addr_matches_only_our_name_on_a_private_ip(mdns):
    s = qr_pairing.start()
    mdns({"kind": "pairing", "name": "adbserver-someoneelse", "ip": "10.0.0.9", "port": 40000},
         {"kind": "pairing", "name": s.name, "ip": "8.8.8.8", "port": 40001})
    assert qr_pairing.pairing_addr(s) is None
    mdns({"kind": "pairing", "name": s.name, "ip": "10.0.0.46", "port": 40001})
    assert qr_pairing.pairing_addr(s) == "10.0.0.46:40001"


@pytest.mark.parametrize("junk", ["not json", "{}", '[{"kind": "pairing", "name": 1, "ip": "x", "port": "y"}]'])
def test_malformed_mdns_file_is_ignored(mdns, junk):
    open(qr_pairing.MDNS_FILE, "w").write(junk)
    assert qr_pairing.pairing_addr(qr_pairing.start()) is None


def test_qr_password_is_validated_before_adb_runs(monkeypatch):
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: pytest.fail("adb must not run"))
    for bad in ("short", "has space 12345", "-flag-looking1234"):
        with pytest.raises(adb_client.AdbError):
            adb_client.pair("10.0.0.46:40001", bad, qr=True)


def test_qr_start_needs_the_listener(authed, tmp_path, monkeypatch):
    monkeypatch.setattr(qr_pairing, "MDNS_FILE", str(tmp_path / "missing.json"))
    r = authed.post("/devices/qr", data={"csrf_token": CSRF}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_qr_page_waits_then_pairs(authed, mdns, monkeypatch):
    r = authed.post("/devices/qr", data={"csrf_token": CSRF}, follow_redirects=False)
    page_url = r.headers["location"]
    (s,) = qr_pairing._sessions.values()
    page = authed.get(page_url)
    assert page.status_code == 200 and "<svg" in page.text and 'http-equiv="refresh"' in page.text
    assert "trusted straight away" in page.text

    paired, dropped = [], []
    monkeypatch.setattr(adb_client, "pair", lambda addr, code, qr=False: paired.append((addr, code, qr)))
    monkeypatch.setattr(adb_client, "disconnect", dropped.append)
    monkeypatch.setattr(adb_client, "connect", lambda a: "connected")
    monkeypatch.setattr(adb_client, "get_serialno", lambda a: "R5CT1234ABC")
    monkeypatch.setattr(adb_client, "device_abis", lambda a: ["arm64-v8a"])
    monkeypatch.setattr(discovery, "_open_ports", lambda *a: pytest.fail("announced port, no scan"))
    mdns({"kind": "pairing", "name": s.name, "ip": "10.0.0.46", "port": 40001},
         {"kind": "connect", "name": "adb-R5CT1234ABC-x", "ip": "10.0.0.46", "port": 38061})
    r = authed.get(page_url, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    assert paired == [("10.0.0.46:40001", s.password, True)]
    assert dropped == ["10.0.0.46:38061"]  # stale connection dropped before connecting fresh
    device = db.get_device("R5CT1234ABC")
    assert device["last_connect_addr"] == "10.0.0.46:38061" and device["trusted"] == 1  # QR pairing trusts
    assert "trusted" in r.headers["location"]
    assert "expired" in authed.get(page_url, follow_redirects=False).headers["location"]  # single use


def test_qr_pairing_scans_when_connect_port_is_not_announced(monkeypatch):
    async def open_ports(ip, ports):
        return [40001, 38061]
    monkeypatch.setattr(discovery, "_open_ports", open_ports)
    monkeypatch.setattr(adb_client, "connect", lambda a: "connected")

    def serial(addr):
        if addr.endswith(":40001"):
            raise adb_client.AdbError("not a connect port")
        return "R5CT1234ABC"
    monkeypatch.setattr(adb_client, "get_serialno", serial)
    assert discovery.first_device_on("10.0.0.46") == ("R5CT1234ABC", "10.0.0.46:38061")


def test_qr_page_for_unknown_token(authed):
    r = authed.get("/devices/qr/nope", follow_redirects=False)
    assert r.status_code == 303 and "expired" in r.headers["location"]


def test_qr_page_requires_login(client):
    r = client.get("/devices/qr/anything", follow_redirects=False)
    assert r.status_code in (303, 401)


def test_qr_pairing_retries_the_announced_port(mdns, monkeypatch):
    s = qr_pairing.start()
    mdns({"kind": "connect", "name": "adb-X-y", "ip": "10.0.0.46", "port": 38061})
    monkeypatch.setattr(adb_client, "pair", lambda *a, **k: "Successfully paired")
    monkeypatch.setattr(adb_client, "disconnect", lambda a: None)
    monkeypatch.setattr(discovery, "_open_ports", lambda *a: pytest.fail("the retry should succeed first"))
    attempts = iter([adb_client.AdbError("connection reset while the phone re-keyed"), "connected"])

    def connect(addr):
        outcome = next(attempts)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(adb_client, "connect", connect)
    monkeypatch.setattr(adb_client, "get_serialno", lambda a: "R5CT1234ABC")
    assert qr_pairing.complete(s, "10.0.0.46:40001") == ("R5CT1234ABC", "10.0.0.46:38061")
