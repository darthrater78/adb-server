import subprocess

import pytest

import adb_client
import apk_verify
import db
import discovery
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
    assert adb_client.installed_version("SER", "com.example.app") == (41, "1.4.1")


def test_installed_version_not_installed(monkeypatch):
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: _proc("Unable to find package: com.example.app\n"))
    assert adb_client.installed_version("SER", "com.example.app") is None


@pytest.mark.parametrize("pkg", ["com.x;reboot", "com.x && rm -rf /", "$(id)", "nodots", "-flag.x", ""])
def test_package_name_is_validated_before_reaching_the_device_shell(pkg, monkeypatch):
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: pytest.fail("adb must not run"))
    with pytest.raises(adb_client.AdbError):
        adb_client.installed_version("SER", pkg)


def test_device_abis_filters_junk(monkeypatch):
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: _proc("arm64-v8a,armeabi-v7a,bad abi!\n"))
    assert adb_client.device_abis("SER") == ["arm64-v8a", "armeabi-v7a"]


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
    assert discovery.ensure_connected(device) == "192.168.1.50:37000"


def test_ensure_connected_scans_when_port_is_stale(monkeypatch, device):
    fake = FakeAdb(monkeypatch, "192.168.1.50:41999")

    async def open_ports(ip, ports):
        return [40000, 41999]
    monkeypatch.setattr(discovery, "_open_ports", open_ports)
    assert discovery.ensure_connected(device) == "192.168.1.50:41999"
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
