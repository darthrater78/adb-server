import json
import subprocess

import pytest

import adb_client
import db
import discovery
import pushes
import selection
from conftest import CSRF
from test_features import _stage

UPDATED = "2026-09-24 21:10:45"


@pytest.fixture
def device():
    db.upsert_paired_device("SER", "192.168.1.50:37000")
    db.set_device_trusted("SER", True)
    db.set_device_abis("SER", ["arm64-v8a"])
    return db.get_device("SER")


@pytest.fixture
def fake_device(monkeypatch):
    """A reachable device. `state["info"]` is what it reports for the package
    after the push, `state["ok"]` whether the install succeeds."""
    state = {"info": adb_client.PackageInfo(2, "2.0", UPDATED), "ok": True}
    monkeypatch.setattr(discovery, "ensure_connected", lambda d, allow_scan=True: (d["serial"], "192.168.1.50:37000"))
    monkeypatch.setattr(adb_client, "device_abis", lambda a: ["arm64-v8a"])
    monkeypatch.setattr(adb_client, "device_model", lambda a: "Pixel")
    monkeypatch.setattr(adb_client, "install", lambda a, p: subprocess.CompletedProcess(
        [], 0 if state["ok"] else 1, "Success\n" if state["ok"] else "Failure [INSTALL_FAILED]\n", ""))
    monkeypatch.setattr(adb_client, "package_info", lambda a, p: state["info"])
    return state


def _push(apk_id):
    apk = db.get_staged_apk(apk_id)
    install_id = pushes.create_install(db.get_device("SER"), apk)
    pushes.run_push(install_id, dict(db.get_device("SER")), dict(apk))
    return db.device_packages_map()[("SER", "com.example")]


def _artifact_upload():
    return db.insert_staged_apk(
        None, "build.apk", "build.apk", "1" * 64, "com.example", "a" * 64, "/nope.apk",
        version_code=2, version_name="2.0", source="upload", is_debug=True,
        artifact={"repo": "o/r", "run_id": 99, "branch": "feat/x", "sha": "abcdef1234567"},
    )


# ---- recording ----

def test_successful_push_records_a_release_origin(device, fake_device):
    rid = db.create_repo("o", "r", "*.apk")
    row = _push(_stage(rid, "v2", "app.apk"))
    origin = selection.origin(row)
    assert origin["state"] == "ours" and origin["kind"] == "release" and origin["ref"] == "v2"
    assert origin["update_time"] == UPDATED and row["update_time"] == UPDATED


def test_failed_push_records_no_origin(device, fake_device):
    fake_device["ok"] = False
    fake_device["info"] = None
    rid = db.create_repo("o", "r", "*.apk")
    apk = db.get_staged_apk(_stage(rid, "v2", "app.apk"))
    pushes.run_push(pushes.create_install(device, apk), dict(device), dict(apk))
    row = db.device_packages_map().get(("SER", "com.example"))
    assert row is None or row["origin"] is None


def test_push_still_recorded_when_the_device_cant_be_asked_after(device, fake_device, monkeypatch):
    def unreachable(a, p):
        raise adb_client.AdbError("gone")
    monkeypatch.setattr(adb_client, "package_info", unreachable)
    rid = db.create_repo("o", "r", "*.apk")
    row = _push(_stage(rid, "v2", "app.apk"))
    assert row["installed"] == 1 and row["version_code"] == 2
    assert selection.origin(row)["state"] == "ours"


def test_artifact_push_records_branch_commit_and_run(device, fake_device):
    origin = selection.origin(_push(_artifact_upload()))
    assert origin["kind"] == "artifact" and origin["ref"] == "feat/x @ abcdef1"
    assert (origin["repo"], origin["run_id"], origin["debug"]) == ("o/r", 99, True)


def test_origin_outlives_the_staged_apk(device, fake_device):
    rid = db.create_repo("o", "r", "*.apk")
    row = _push(_stage(rid, "v2", "app.apk"))
    db.delete_repo(rid)
    assert selection.origin(db.device_packages_map()[("SER", "com.example")])["kind"] == "release"
    assert row["origin"]


# ---- classification ----

def test_same_version_reinstalled_elsewhere_is_not_ours(device, fake_device):
    rid = db.create_repo("o", "r", "*.apk")
    _push(_stage(rid, "v2", "app.apk"))
    db.upsert_device_package("SER", "com.example", True, 2, "2.0", update_time="2026-09-25 08:00:00")
    assert selection.origin(db.device_packages_map()[("SER", "com.example")]) == {"state": "other"}


def test_different_version_is_not_ours(device, fake_device):
    rid = db.create_repo("o", "r", "*.apk")
    _push(_stage(rid, "v2", "app.apk"))
    db.upsert_device_package("SER", "com.example", True, 3, "3.0", update_time=UPDATED)
    assert selection.origin(db.device_packages_map()[("SER", "com.example")]) == {"state": "other"}


def test_uninstall_forgets_the_origin(device, fake_device):
    rid = db.create_repo("o", "r", "*.apk")
    _push(_stage(rid, "v2", "app.apk"))
    db.upsert_device_package("SER", "com.example", False)
    assert selection.origin(db.device_packages_map()[("SER", "com.example")]) is None
    db.upsert_device_package("SER", "com.example", True, 2, "2.0", update_time=UPDATED)
    assert selection.origin(db.device_packages_map()[("SER", "com.example")]) == {"state": "other"}


def test_never_pushed_is_not_from_this_server(device):
    db.upsert_device_package("SER", "com.example", True, 2, "2.0")
    assert selection.origin(db.device_packages_map()[("SER", "com.example")]) == {"state": "other"}


# ---- backfill of pushes made before tracking ----

def _backfill():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM meta WHERE key = 'origins_backfilled'")
    db.init_db()


def test_backfill_matches_an_earlier_push_by_version(device):
    rid = db.create_repo("o", "r", "*.apk")
    apk_id = _stage(rid, "v2", "app.apk")
    db.finish_install(db.insert_install("SER", apk_id, "installing"), "success", "Success")
    db.upsert_device_package("SER", "com.example", True, 2, "2.0", update_time=UPDATED)
    _backfill()
    origin = selection.origin(db.device_packages_map()[("SER", "com.example")])
    assert origin["state"] == "likely" and origin["ref"] == "v2"


def test_backfill_skips_a_version_this_server_never_pushed(device):
    rid = db.create_repo("o", "r", "*.apk")
    apk_id = _stage(rid, "v2", "app.apk")
    db.finish_install(db.insert_install("SER", apk_id, "installing"), "success", "Success")
    db.upsert_device_package("SER", "com.example", True, 5, "5.0")
    _backfill()
    assert selection.origin(db.device_packages_map()[("SER", "com.example")]) == {"state": "other"}


def test_backfill_runs_once(device):
    rid = db.create_repo("o", "r", "*.apk")
    apk_id = _stage(rid, "v2", "app.apk")
    db.finish_install(db.insert_install("SER", apk_id, "installing"), "success", "Success")
    db.upsert_device_package("SER", "com.example", True, 2, "2.0")
    db.init_db()  # already marked by the fixture's first start
    assert db.device_packages_map()[("SER", "com.example")]["origin"] is None


# ---- Status page ----

def test_status_shows_where_each_install_came_from(authed, device, fake_device):
    rid = db.create_repo("o", "r", "*.apk")
    _push(_stage(rid, "v2", "app.apk"))
    r = authed.get("/status")
    assert "Release" in r.text and "Not from this server" not in r.text


def test_status_shows_a_test_build_with_its_run(authed, device, fake_device):
    _push(_artifact_upload())
    r = authed.get("/status")
    assert "Test build" in r.text and "feat/x @ abcdef1" in r.text
    assert 'href="https://github.com/o/r/actions/runs/99"' in r.text and "Debug" in r.text


def test_status_flags_an_app_not_from_this_server(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    db.upsert_device_package("SER", "com.example", True, 1, "1.0")
    assert "Not from this server" in authed.get("/status").text


def test_origin_text_is_escaped(authed, device, fake_device):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO device_packages (device_serial, package_name, installed, version_code, checked_at, origin)"
            " VALUES ('SER', 'com.example', 1, 2, 'x', ?)",
            (json.dumps({"kind": "upload", "ref": "<script>x</script>", "version_code": 2, "at": None}),),
        )
    r = authed.get("/status")
    assert "<script>x</script>" not in r.text and "&lt;script&gt;" in r.text


def test_refresh_also_rechecks_uploaded_apps(authed, device, monkeypatch):
    asked = []
    monkeypatch.setattr(discovery, "ensure_connected", lambda d, allow_scan=True: (d["serial"], "192.168.1.50:37000"))
    monkeypatch.setattr(adb_client, "device_abis", lambda a: ["arm64-v8a"])
    monkeypatch.setattr(adb_client, "device_model", lambda a: "Pixel")
    monkeypatch.setattr(adb_client, "package_info", lambda a, p: asked.append(p))
    db.upsert_device_package("SER", "org.uploaded", True, 1, "1.0")
    authed.post("/status/refresh", data={"csrf_token": CSRF})
    assert "org.uploaded" in asked
    assert db.device_packages_map()[("SER", "org.uploaded")]["installed"] == 0
