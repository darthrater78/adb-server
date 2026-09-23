import subprocess

import pytest

import adb_client
import apk_verify
import db
import discovery
import pushes
import selection


def _proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


# ---- apk metadata ----

BADGING = """package: name='com.example.app' versionCode='42' versionName='1.4.2' platformBuildVersionName='14'
sdkVersion:'26'
native-code: 'arm64-v8a' 'x86_64'
"""


def test_parse_badging():
    info = apk_verify.parse_badging(BADGING)
    assert info == apk_verify.PackageInfo("com.example.app", 42, "1.4.2", ("arm64-v8a", "x86_64"))


def test_parse_badging_without_native_code():
    info = apk_verify.parse_badging("package: name='a.b' versionCode='1' versionName='1'\n")
    assert info.abis == ()


# ---- adb queries ----

DUMPSYS = """Packages:
  Package [com.example.app] (1a2b3c):
    versionCode=41 minSdk=26 targetSdk=34
    versionName=1.4.1
"""


def test_installed_version(monkeypatch):
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: _proc(DUMPSYS))
    assert adb_client.installed_version("192.168.1.50:37000", "com.example.app") == (41, "1.4.1")


def test_installed_version_not_installed(monkeypatch):
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: _proc("Unable to find package: com.example.app\n"))
    assert adb_client.installed_version("192.168.1.50:37000", "com.example.app") is None


@pytest.mark.parametrize("pkg", ["com.x;reboot", "com.x && rm -rf /", "$(id)", "nodots", "-flag.x", ""])
def test_package_name_is_validated_before_reaching_the_device_shell(pkg, monkeypatch):
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: pytest.fail("adb must not run"))
    with pytest.raises(adb_client.AdbError):
        adb_client.installed_version("192.168.1.50:37000", pkg)


def test_device_abis_filters_junk(monkeypatch):
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: _proc("arm64-v8a,armeabi-v7a,bad abi!\n"))
    assert adb_client.device_abis("192.168.1.50:37000") == ["arm64-v8a", "armeabi-v7a"]


# ---- variant selection ----

def _v(name, abis):
    return {"filename": name, "abis": abis}


VARIANTS = [_v("universal", "arm64-v8a armeabi-v7a x86_64"), _v("arm64", "arm64-v8a"), _v("v7", "armeabi-v7a")]


@pytest.mark.parametrize("device,expected", [
    ("arm64-v8a armeabi-v7a armeabi", "arm64"),
    ("armeabi-v7a armeabi", "v7"),
    ("x86_64 x86", "universal"),
    ("", "universal"),
])
def test_pick_variant(device, expected):
    assert selection.pick_variant(VARIANTS, device)["filename"] == expected


def test_pick_variant_falls_back_to_no_native_code():
    assert selection.pick_variant([_v("arm", "arm64-v8a"), _v("java", "")], "x86_64")["filename"] == "java"


def test_pick_variant_none_when_nothing_fits():
    assert selection.pick_variant([_v("arm", "arm64-v8a")], "x86_64") is None


@pytest.mark.parametrize("apk,device,ok", [
    ("", "x86_64", True), ("arm64-v8a", "", True), ("arm64-v8a", "x86_64", False), ("arm64-v8a x86_64", "x86_64", True),
])
def test_compatible(apk, device, ok):
    assert selection.compatible(apk, device) is ok


@pytest.mark.parametrize("installed,expected", [
    (None, "unknown"),
    ({"installed": 0, "version_code": None, "version_name": None}, "missing"),
    ({"installed": 1, "version_code": 41, "version_name": "1.4.1"}, "update"),
    ({"installed": 1, "version_code": 42, "version_name": "1.4.2"}, "current"),
    ({"installed": 1, "version_code": 50, "version_name": "2.0"}, "newer"),
])
def test_update_state(installed, expected):
    assert selection.update_state(installed, {"version_code": 42, "version_name": "1.4.2"}) == expected


# ---- discovery ----

@pytest.fixture
def device():
    db.upsert_paired_device("SER", "192.168.1.50:37000")
    return db.get_device("SER")


class FakeAdb:
    def __init__(self, monkeypatch, live_addr, serial="SER"):
        self.live_addr, self.serial, self.connects = live_addr, serial, []
        monkeypatch.setattr(adb_client, "connect", self.connect)
        monkeypatch.setattr(adb_client, "get_serialno", self.get_serialno)

    def connect(self, addr):
        self.connects.append(addr)
        if addr != self.live_addr:
            raise adb_client.AdbError("unable to connect")
        return "connected"

    def get_serialno(self, addr):
        if addr != self.live_addr:
            raise adb_client.AdbError("offline")
        return self.serial


def test_ensure_connected_uses_stored_address(monkeypatch, device):
    FakeAdb(monkeypatch, "192.168.1.50:37000")
    monkeypatch.setattr(discovery, "find_device_port", lambda *a: pytest.fail("no scan needed"))
    assert discovery.ensure_connected(device) == ("SER", "192.168.1.50:37000")


def test_ensure_connected_scans_when_port_is_stale(monkeypatch, device):
    fake = FakeAdb(monkeypatch, "192.168.1.50:41999")

    async def open_ports(ip, ports):
        return [40000, 41999]
    monkeypatch.setattr(discovery, "_open_ports", open_ports)
    assert discovery.ensure_connected(device) == ("SER", "192.168.1.50:41999")
    assert db.get_device("SER")["last_connect_addr"] == "192.168.1.50:41999"
    assert "192.168.1.50:40000" in fake.connects  # tried, rejected, moved on


def test_ensure_connected_never_scans_past_an_identity_mismatch(monkeypatch, device):
    FakeAdb(monkeypatch, "192.168.1.50:37000", serial="SOMEONE-ELSE")
    monkeypatch.setattr(discovery, "find_device_port", lambda *a: pytest.fail("must not scan"))
    with pytest.raises(adb_client.AdbError, match="different serial"):
        discovery.ensure_connected(device)


def test_ensure_connected_without_scan(monkeypatch, device):
    FakeAdb(monkeypatch, "192.168.1.50:41999")
    monkeypatch.setattr(discovery, "find_device_port", lambda *a: pytest.fail("scan disabled"))
    with pytest.raises(adb_client.AdbError, match="not reachable"):
        discovery.ensure_connected(device, allow_scan=False)


def test_get_serialno_reads_the_hardware_serial(monkeypatch):
    calls = []

    def run(*args, **kw):
        calls.append(args)
        return _proc("R5CT1234ABC\n")
    monkeypatch.setattr(adb_client, "_run", run)
    assert adb_client.get_serialno("192.168.1.50:37000") == "R5CT1234ABC"
    assert calls == [("-s", "192.168.1.50:37000", "shell", "getprop", "ro.serialno")]


def test_get_serialno_falls_back_to_boot_serial(monkeypatch):
    out = iter(["\n", "BOOT123\n"])
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: _proc(next(out)))
    assert adb_client.get_serialno("192.168.1.50:37000") == "BOOT123"


@pytest.mark.parametrize("junk", ["", "unknown", "10.0.0.5:4444", "-flag", "a b"])
def test_get_serialno_rejects_non_serials(monkeypatch, junk):
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: _proc(junk + "\n"))
    with pytest.raises(adb_client.AdbError):
        adb_client.get_serialno("192.168.1.50:37000")


@pytest.fixture
def legacy_device():
    """Paired before 1.0: filed under its ip:port, with history attached."""
    db.upsert_paired_device("192.168.1.50:37000", "192.168.1.50:37000")
    db.set_device_trusted("192.168.1.50:37000", True)
    db.set_device_nickname("192.168.1.50:37000", "Dad")
    db.upsert_device_package("192.168.1.50:37000", "com.example", installed=True)
    return db.get_device("192.168.1.50:37000")


def test_legacy_device_is_rekeyed_when_found_on_a_new_port(monkeypatch, legacy_device):
    FakeAdb(monkeypatch, "192.168.1.50:41999", serial="R5CT1234ABC")

    async def open_ports(ip, ports):
        return [41999]
    monkeypatch.setattr(discovery, "_open_ports", open_ports)
    assert discovery.ensure_connected(legacy_device) == ("R5CT1234ABC", "192.168.1.50:41999")
    assert db.get_device("192.168.1.50:37000") is None
    moved = db.get_device("R5CT1234ABC")
    assert (moved["nickname"], moved["trusted"], moved["last_connect_addr"]) == ("Dad", 1, "192.168.1.50:41999")
    assert ("R5CT1234ABC", "com.example") in db.device_packages_map()


def test_legacy_device_on_another_ip_is_not_adopted(monkeypatch, legacy_device):
    FakeAdb(monkeypatch, "192.168.1.51:41999", serial="R5CT1234ABC")
    assert not discovery.is_same_device("192.168.1.50:37000", "192.168.1.51:41999", "R5CT1234ABC")


def test_real_serial_never_matches_by_ip():
    assert not discovery.is_same_device("SER", "192.168.1.50:37000", "OTHER")


def test_find_device_port_refuses_public_ip(monkeypatch):
    monkeypatch.setattr(discovery, "_open_ports", lambda *a: pytest.fail("must not scan"))
    with pytest.raises(adb_client.AdbError):
        discovery.find_device_port("8.8.8.8", "SER")


@pytest.mark.parametrize("raw,expected", [
    (None, (30000, 49999)), ("40000-41000", (40000, 41000)), ("1-99999", (1024, 65535)),
    ("junk", (30000, 49999)), ("50000-40000", (30000, 49999)),
])
def test_port_range(raw, expected, monkeypatch):
    if raw is None:
        monkeypatch.delenv("ADB_SCAN_PORTS", raising=False)
    else:
        monkeypatch.setenv("ADB_SCAN_PORTS", raw)
    r = discovery._port_range()
    assert (r.start, r.stop - 1) == expected


def test_connect_waits_until_the_device_is_ready(monkeypatch):
    states = iter(["offline", "offline", "device"])
    calls = []

    def run(*args, **kw):
        calls.append(args)
        if args[0] == "connect":
            return _proc("connected to 192.168.1.50:37000\n")
        state = next(states)
        return _proc(state + "\n") if state == "device" else _proc("", 1, f"error: device {state}\n")
    monkeypatch.setattr(adb_client, "_run", run)
    monkeypatch.setattr(adb_client.time, "sleep", lambda s: None)
    adb_client.connect("192.168.1.50:37000")
    assert sum(1 for c in calls if "get-state" in c) == 3


def test_connect_gives_up_if_never_ready(monkeypatch):
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(adb_client.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(adb_client.time, "sleep", lambda s: None)
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: _proc("connected\n") if a[0] == "connect"
                        else _proc("", 1, "error: device offline\n"))
    with pytest.raises(adb_client.AdbError, match="never became ready"):
        adb_client.connect("192.168.1.50:37000")


def test_connect_reports_an_unauthorized_key(monkeypatch):
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: _proc("connected\n") if a[0] == "connect"
                        else _proc("", 1, "error: device unauthorized.\n"))
    with pytest.raises(adb_client.AdbError, match="pair it again"):
        adb_client.connect("192.168.1.50:37000")


def test_legacy_record_never_reaches_another_registered_phone(monkeypatch, legacy_device):
    """The legacy IP answers as a phone that has its own (untrusted) record:
    a push through the legacy record must not install on it."""
    db.upsert_paired_device("OTHERPHONE", "192.168.1.50:41999")
    FakeAdb(monkeypatch, "192.168.1.50:37000", serial="OTHERPHONE")
    with pytest.raises(adb_client.AdbError, match="different serial"):
        discovery.ensure_connected(legacy_device)
    assert db.get_device("192.168.1.50:37000")["trusted"] == 1  # nothing merged
    assert db.get_device("OTHERPHONE")["trusted"] == 0


def test_push_rechecks_trust_when_it_runs(monkeypatch, device):
    db.set_device_trusted("SER", True)
    rid = db.create_repo("o", "r", "*.apk")
    apk_id = db.insert_staged_apk(repo_id=rid, tag="v1", filename="a.apk", sha256="a" * 64,
                                  package_name="com.example", signer_sha256="b" * 64, path="/nope.apk")
    apk = db.get_staged_apk(apk_id)
    install_id = pushes.create_install(db.get_device("SER"), apk)
    db.set_device_trusted("SER", False)  # revoked while the push was queued
    FakeAdb(monkeypatch, "192.168.1.50:37000")
    monkeypatch.setattr(adb_client, "install", lambda *a: pytest.fail("must not install"))
    pushes.run_push(install_id, dict(db.get_device("SER")), dict(apk))
    install = db.get_install(install_id)
    assert install["status"] == "failed" and "no longer trusted" in install["log"]
