import io
import os
import sqlite3
import zipfile

import pytest

import apk_verify
import db
import main
import staging
from conftest import CSRF

SIGNER = "d" * 64


def _apk_bytes(marker: str = "x") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        # A fixed timestamp: writestr(name) stamps the current time, so two
        # calls straddling a 2-second tick would hash differently.
        z.writestr(zipfile.ZipInfo("AndroidManifest.xml", date_time=(2020, 1, 1, 0, 0, 0)), marker)
    return buf.getvalue()


@pytest.fixture
def verify(monkeypatch):
    state = {"debug": False}
    monkeypatch.setattr(apk_verify, "verify_signature",
                        lambda path: apk_verify.SignerInfo(SIGNER, state["debug"]))
    monkeypatch.setattr(apk_verify, "get_package_info",
                        lambda path: apk_verify.PackageInfo("com.example.dev", 7, "1.7-dev", ("arm64-v8a",)))
    return state


def _upload(authed, content, filename="app.apk", label="", **kw):
    return authed.post("/staged/upload", data={"csrf_token": CSRF, "label": label},
                       files={"apk": (filename, content, "application/vnd.android.package-archive")},
                       follow_redirects=False, **kw)


def _uploads_dir():
    return os.path.join(staging.STAGING_ROOT, "uploads")


def test_upload_stages_by_content_hash(authed, verify):
    r = _upload(authed, _apk_bytes(), filename="../../etc/evil.apk")
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    [apk] = db.list_staged_apks()
    assert apk["source"] == "upload" and apk["repo_id"] is None
    assert apk["filename"] == "evil.apk"
    assert apk["source_label"] == "Manual upload"
    assert apk["path"] == os.path.join(_uploads_dir(), f"{apk['sha256']}.apk")
    assert apk["abis"] == "arm64-v8a" and apk["version_name"] == "1.7-dev" and apk["tag"] == "1.7-dev"
    assert os.listdir(_uploads_dir()) == [f"{apk['sha256']}.apk"]  # no temp files left


def test_debug_upload_is_accepted_and_flagged(authed, verify):
    verify["debug"] = True
    r = _upload(authed, _apk_bytes())
    assert "warn=" in r.headers["location"]
    [apk] = db.list_staged_apks()
    assert apk["is_debug"] == 1
    assert "debug build" in authed.get("/staged").text


def test_upload_does_not_touch_any_repo_pin(authed, verify):
    rid = db.create_repo("o", "r", "*.apk")
    _upload(authed, _apk_bytes())
    repo = db.get_repo(rid)
    assert repo["expected_package"] is None and repo["signer_sha256"] is None


@pytest.mark.parametrize("content,needle", [(b"not a zip", "zip"), (b"", "empty")])
def test_bad_uploads_are_refused(authed, verify, content, needle):
    r = _upload(authed, content)
    assert "error=" in r.headers["location"] and needle in r.headers["location"]
    assert db.list_staged_apks() == []
    assert os.listdir(_uploads_dir()) == []


def test_failed_signature_is_refused(authed, verify, monkeypatch):
    def boom(path):
        raise apk_verify.ApkVerifyError("apksigner verify failed: no signature")
    monkeypatch.setattr(apk_verify, "verify_signature", boom)
    r = _upload(authed, _apk_bytes())
    assert "error=" in r.headers["location"]
    assert db.list_staged_apks() == []


def test_duplicate_upload_is_refused(authed, verify):
    _upload(authed, _apk_bytes())
    r = _upload(authed, _apk_bytes())
    assert "already%20staged" in r.headers["location"]
    assert len(db.list_staged_apks()) == 1


def test_upload_after_delete_is_allowed(authed, verify):
    _upload(authed, _apk_bytes())
    [apk] = db.list_staged_apks()
    authed.post(f"/staged/{apk['id']}/delete", data={"csrf_token": CSRF})
    r = _upload(authed, _apk_bytes())
    assert "ok=" in r.headers["location"]


def test_oversize_upload_is_refused_in_handler(authed, verify, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
    monkeypatch.setattr(main, "UPLOAD_FORM_OVERHEAD", 10_000)
    r = _upload(authed, _apk_bytes())
    assert "size%20cap" in r.headers["location"]
    assert db.list_staged_apks() == []


def test_oversize_upload_is_refused_before_parsing(authed, verify, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
    monkeypatch.setattr(main, "UPLOAD_FORM_OVERHEAD", 10)
    r = _upload(authed, _apk_bytes())
    assert r.status_code == 413


def test_upload_requires_csrf(authed, verify):
    r = authed.post("/staged/upload", data={"csrf_token": "wrong"},
                    files={"apk": ("a.apk", _apk_bytes(), "application/octet-stream")})
    assert r.status_code == 403


def test_uploaded_apk_push_and_install_history(authed, verify, monkeypatch):
    import pushes
    monkeypatch.setattr(pushes, "run_push", lambda *a: None)
    db.upsert_paired_device("SER", "192.168.1.50:37000")
    db.set_device_trusted("SER", True)
    db.set_device_abis("SER", ["arm64-v8a"])
    _upload(authed, _apk_bytes())
    [apk] = db.list_staged_apks()
    r = authed.post("/push", data={"csrf_token": CSRF, "apk_id": apk["id"], "device_serial": "SER"},
                    follow_redirects=False)
    assert r.status_code == 303
    [install] = db.list_installs()
    assert install["source_label"] == "Manual upload"
    assert "Manual upload" in authed.get("/installs").text


def test_release_notes_are_on_the_install_page_and_linked_from_status(authed):
    rid = db.create_repo("o", "r", "*.apk")
    os.makedirs(staging.repo_dir(rid), exist_ok=True)
    path = os.path.join(staging.repo_dir(rid), "a.apk")
    open(path, "wb").close()
    apk_id = db.insert_staged_apk(rid, "v1", "a.apk", "0" * 64, "com.x", "a" * 64, path,
                                  release_notes="Long notes", version_name="1.0")
    db.upsert_paired_device("SER", "192.168.1.50:37000")
    db.set_device_trusted("SER", True)
    page = authed.get("/install").text
    assert f'id="app-{rid}"' in page and f'id="apk-{apk_id}"' in page
    assert "Release notes for 1.0" in page and "Long notes" in page
    assert f'href="/install#app-{rid}"' in authed.get("/status").text


def test_migration_from_pre_upload_schema(tmp_path, monkeypatch):
    """The shape last shipped on this branch before uploads: repo_id NOT NULL,
    UNIQUE(repo_id, tag, filename), no source/is_debug columns."""
    path = tmp_path / "prev.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE repos (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, repo TEXT NOT NULL,
            asset_glob TEXT NOT NULL DEFAULT '*.apk', expected_package TEXT, signer_sha256 TEXT, last_tag TEXT,
            last_checked_at TEXT, last_error TEXT, UNIQUE(owner, repo));
        CREATE TABLE staged_apks (id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE, tag TEXT NOT NULL,
            filename TEXT NOT NULL, sha256 TEXT NOT NULL, package_name TEXT NOT NULL,
            signer_sha256 TEXT NOT NULL, path TEXT NOT NULL, downloaded_at TEXT NOT NULL,
            release_notes TEXT, pruned_at TEXT, version_code INTEGER, version_name TEXT,
            abis TEXT NOT NULL DEFAULT '', UNIQUE(repo_id, tag, filename));
        CREATE TABLE devices (serial TEXT PRIMARY KEY, nickname TEXT, trusted INTEGER NOT NULL DEFAULT 0,
            last_connect_addr TEXT, paired_at TEXT NOT NULL, last_seen_at TEXT);
        CREATE TABLE installs (id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_serial TEXT NOT NULL REFERENCES devices(serial) ON DELETE CASCADE,
            apk_id INTEGER NOT NULL REFERENCES staged_apks(id) ON DELETE CASCADE, status TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, log TEXT);
        INSERT INTO repos (owner, repo) VALUES ('o', 'r');
        INSERT INTO staged_apks (repo_id, tag, filename, sha256, package_name, signer_sha256, path, downloaded_at)
            VALUES (1, 'v1', 'app.apk', 'h', 'com.x', 's', '/p', 't');
        INSERT INTO devices (serial, paired_at) VALUES ('SER', 't');
        INSERT INTO installs (device_serial, apk_id, status, started_at) VALUES ('SER', 1, 'success', 't');
    """)
    old.commit()
    old.close()
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    db.init_db()  # idempotent
    apk = db.get_staged_apk(1)
    assert apk["source"] == "github" and apk["is_debug"] == 0 and apk["source_label"] == "o/r"
    assert len(db.list_installs()) == 1
    assert db.insert_staged_apk(None, "dev", "u.apk", "h9", "com.x", "s", "/u", source="upload") is not None


def test_upload_warns_when_package_clashes_with_a_pinned_repo(authed, verify):
    rid = db.create_repo("o", "r", "*.apk")
    db.update_repo_check(rid, last_tag="v1", last_error=None,
                         expected_package="com.example.dev", signer_sha256="e" * 64)
    r = _upload(authed, _apk_bytes())
    assert "warn=" in r.headers["location"] and "o/r" in r.headers["location"]


def test_upload_matching_pinned_repo_signer_is_not_warned(authed, verify):
    rid = db.create_repo("o", "r", "*.apk")
    db.update_repo_check(rid, last_tag="v1", last_error=None,
                         expected_package="com.example.dev", signer_sha256=SIGNER)
    r = _upload(authed, _apk_bytes())
    assert "ok=" in r.headers["location"]


def test_upload_lives_on_the_sources_page(authed):
    page = authed.get("/sources").text
    assert "Upload an APK" in page and 'action="/staged/upload"' in page
    assert "Watch a GitHub repo" in page and 'action="/repos"' in page
    assert 'href="/sources" class="active" aria-current="page"' in page
    r = authed.get("/upload?error=x", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/sources?error=x"


def test_upload_errors_return_to_the_sources_page(authed, verify):
    r = _upload(authed, b"not a zip")
    assert r.headers["location"].startswith("/sources?error=")
