import asyncio
import os
import socket
import sqlite3
from urllib.parse import unquote

import pytest

import adb_client
import db
import discovery
import main
import pushes
import staging
from conftest import CSRF


def _stage(repo_id, tag, filename, abis="", version_code=2, version_name="2.0"):
    os.makedirs(staging.repo_dir(repo_id), exist_ok=True)
    path = os.path.join(staging.repo_dir(repo_id), f"{tag}-{filename}")
    with open(path, "wb") as f:
        f.write(b"PK")
    return db.insert_staged_apk(repo_id, tag, filename, "0" * 64, "com.example", "a" * 64, path,
                                version_code=version_code, version_name=version_name, abis=abis)


@pytest.fixture
def trusted_device():
    db.upsert_paired_device("SER", "192.168.1.50:37000")
    db.set_device_trusted("SER", True)
    db.set_device_abis("SER", ["arm64-v8a", "armeabi-v7a"])
    return db.get_device("SER")


@pytest.fixture
def no_background(monkeypatch):
    queued = []
    monkeypatch.setattr(pushes, "run_push", lambda *a: queued.append(a))
    return queued


def test_push_latest_picks_the_variant_for_the_device(authed, trusted_device, no_background):
    rid = db.create_repo("o", "r", "*.apk")
    db.update_repo_check(rid, expected_package="com.example", signer_sha256="a" * 64)
    _stage(rid, "v2", "universal.apk", "arm64-v8a armeabi-v7a x86_64")
    arm64 = _stage(rid, "v2", "arm64.apk", "arm64-v8a")
    r = authed.post("/push-latest", data={"csrf_token": CSRF, "repo_id": rid, "device_serial": "SER"}, follow_redirects=False)
    assert r.headers["location"].startswith("/installs/")
    assert db.list_installs()[0]["apk_id"] == arm64


def test_push_of_incompatible_variant_is_refused(authed, trusted_device, no_background):
    rid = db.create_repo("o", "r", "*.apk")
    x86 = _stage(rid, "v2", "x86.apk", "x86_64")
    r = authed.post("/push", data={"csrf_token": CSRF, "apk_id": x86, "device_serial": "SER"}, follow_redirects=False)
    assert "CPU" in r.headers["location"] or "doesn" in r.headers["location"]
    assert db.list_installs() == []


def test_status_page_shows_update_available(authed, trusted_device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    db.upsert_device_package("SER", "com.example", installed=True, version_code=1, version_name="1.0")
    r = authed.get("/status")
    assert r.status_code == 200
    assert '<span class="version">1.0</span>' in r.text and "2.0 available" in r.text and "Update" in r.text


def test_status_refresh_records_versions(authed, trusted_device, monkeypatch):
    rid = db.create_repo("o", "r", "*.apk")
    db.update_repo_check(rid, expected_package="com.example", signer_sha256="a" * 64)
    monkeypatch.setattr(discovery, "ensure_connected", lambda d, allow_scan=True: (d["serial"], d["last_connect_addr"]))
    monkeypatch.setattr(adb_client, "device_abis", lambda s: ["arm64-v8a"])
    monkeypatch.setattr(adb_client, "package_info", lambda s, p: adb_client.PackageInfo(7, "0.7", None))
    authed.post("/status/refresh", data={"csrf_token": CSRF})
    row = db.device_packages_map()[("SER", "com.example")]
    assert (row["installed"], row["version_code"], row["version_name"]) == (1, 7, "0.7")


def test_accept_signer_requires_confirmation(authed):
    rid = db.create_repo("o", "r", "*.apk")
    db.set_rejected_tag(rid, "v2", "Pin mismatch", "com.example", "b" * 64, False)
    r = authed.post(f"/repos/{rid}/accept-signer", data={"csrf_token": CSRF}, follow_redirects=False)
    assert "confirmation" in r.headers["location"]
    assert db.get_repo(rid)["pending_signer"] == "b" * 64


def test_repos_page_shows_rotation_panel(authed):
    rid = db.create_repo("o", "r", "*.apk")
    db.update_repo_check(rid, expected_package="com.example", signer_sha256="a" * 64)
    db.set_rejected_tag(rid, "v2", "Pin mismatch", "com.example", "b" * 64, True)
    r = authed.get("/repos")
    assert "Signing change" in r.text and "Proven" in r.text


def test_root_redirects_to_status(authed):
    assert authed.get("/", follow_redirects=False).headers["location"] == "/status"


def test_port_scan_finds_a_real_listening_port():
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        found = asyncio.run(discovery._open_ports("127.0.0.1", range(port - 5, port + 5)))
    assert port in found


def test_migration_from_0_2_0_keeps_install_history(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE repos (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, repo TEXT NOT NULL,
            asset_glob TEXT NOT NULL DEFAULT '*.apk', expected_package TEXT, signer_sha256 TEXT, last_tag TEXT,
            last_checked_at TEXT, last_error TEXT, UNIQUE(owner, repo));
        CREATE TABLE staged_apks (id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE, tag TEXT NOT NULL,
            filename TEXT NOT NULL, sha256 TEXT NOT NULL, package_name TEXT NOT NULL,
            signer_sha256 TEXT NOT NULL, path TEXT NOT NULL, downloaded_at TEXT NOT NULL,
            release_notes TEXT, UNIQUE(repo_id, tag));
        CREATE TABLE devices (serial TEXT PRIMARY KEY, nickname TEXT, trusted INTEGER NOT NULL DEFAULT 0,
            last_connect_addr TEXT, paired_at TEXT NOT NULL, last_seen_at TEXT);
        CREATE TABLE installs (id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_serial TEXT NOT NULL REFERENCES devices(serial) ON DELETE CASCADE,
            apk_id INTEGER NOT NULL REFERENCES staged_apks(id) ON DELETE CASCADE, status TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, log TEXT);
        INSERT INTO repos (owner, repo) VALUES ('o', 'r');
        INSERT INTO staged_apks (repo_id, tag, filename, sha256, package_name, signer_sha256, path, downloaded_at, release_notes)
            VALUES (1, 'v1', 'app.apk', 'h', 'com.x', 's', '/p', 't', 'notes');
        INSERT INTO devices (serial, paired_at) VALUES ('SER', 't');
        INSERT INTO installs (device_serial, apk_id, status, started_at) VALUES ('SER', 1, 'success', 't');
    """)
    old.commit()
    old.close()
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    db.init_db()  # idempotent
    assert len(db.list_installs()) == 1
    apk = db.get_staged_apk(1)
    assert apk["release_notes"] == "notes" and apk["abis"] == ""
    # The new constraint allows a second variant of the same tag.
    assert db.insert_staged_apk(1, "v1", "app-arm64.apk", "h2", "com.x", "s", "/p2") is not None
    with sqlite3.connect(path) as conn:
        fk = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'installs'").fetchone()[0]
    assert "REFERENCES staged_apks(id)" in fk


def test_status_update_asks_for_confirmation(authed, trusted_device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk", version_name="2.0")
    page = authed.get("/status").text
    # The visible control only opens the dialog; the POST lives inside it.
    assert 'href="#confirm-' in page and 'class="modal"' in page
    dialog = page[page.index('class="modal"'):]
    assert 'action="/push-latest"' in dialog and 'name="csrf_token"' in dialog
    assert "Cancel" in dialog and "2.0" in dialog


def test_repairing_a_legacy_device_keeps_its_record(authed, monkeypatch):
    db.upsert_paired_device("192.168.1.50:37000", "192.168.1.50:37000")
    db.set_device_trusted("192.168.1.50:37000", True)
    db.set_device_nickname("192.168.1.50:37000", "Dad")
    monkeypatch.setattr(adb_client, "pair", lambda a, c: "Successfully paired")
    monkeypatch.setattr(adb_client, "connect", lambda a: "connected")
    monkeypatch.setattr(adb_client, "get_serialno", lambda a: "R5CT1234ABC")
    monkeypatch.setattr(adb_client, "device_abis", lambda a: ["arm64-v8a"])
    r = authed.post("/devices/pair", data={
        "csrf_token": CSRF, "pairing_addr": "192.168.1.50:40001",
        "pairing_code": "123456", "connect_addr": "192.168.1.50:41999",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert [d["serial"] for d in db.list_devices()] == ["R5CT1234ABC"]
    device = db.get_device("R5CT1234ABC")
    # Same IP isn't proof of the same phone: history kept, trust given again by hand.
    assert (device["nickname"], device["trusted"], device["last_connect_addr"]) == ("Dad", 0, "192.168.1.50:41999")
    assert "trust it again" in unquote(r.headers["location"])
    assert "trust cleared" in db.list_audit()[0]["detail"]


def test_code_pairing_still_starts_untrusted(authed, monkeypatch):
    monkeypatch.setattr(adb_client, "pair", lambda a, c: "Successfully paired")
    monkeypatch.setattr(adb_client, "connect", lambda a: "connected")
    monkeypatch.setattr(adb_client, "get_serialno", lambda a: "NEWPHONE1")
    monkeypatch.setattr(adb_client, "device_abis", lambda a: ["arm64-v8a"])
    authed.post("/devices/pair", data={
        "csrf_token": CSRF, "pairing_addr": "192.168.1.60:40001",
        "pairing_code": "123456", "connect_addr": "192.168.1.60:41999",
    })
    assert db.get_device("NEWPHONE1")["trusted"] == 0
