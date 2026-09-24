import hashlib
import io
import os
import sqlite3
import zipfile

import pytest

import apk_verify
import db
import main
import uploads
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
    monkeypatch.setattr(apk_verify, "is_unsigned", lambda path: False)  # these fakes stand for signed APKs
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
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 10)
    monkeypatch.setattr(main, "UPLOAD_FORM_OVERHEAD", 10_000)
    r = _upload(authed, _apk_bytes())
    assert "size%20cap" in r.headers["location"]
    assert db.list_staged_apks() == []


def test_oversize_upload_is_refused_before_parsing(authed, verify, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 10)
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


# ---- artifact zips: a workflow run's download wraps the APK in a zip ----

def _zip_bytes(entries: dict[str, bytes], compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for name, data in entries.items():
            # A ZipInfo carries its own compression, ignoring the ZipFile's.
            z.writestr(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), data, compress_type=compression)
    return buf.getvalue()


def _upload_zip(authed, content, filename="app-release.zip"):
    return authed.post("/staged/upload", data={"csrf_token": CSRF, "label": ""},
                       files={"apk": (filename, content, "application/zip")}, follow_redirects=False)


def test_artifact_zip_stages_the_apk_inside(authed, verify):
    inner = _apk_bytes()
    r = _upload_zip(authed, _zip_bytes({"output-metadata.json": b"{}", "release/app-release.apk": inner}))
    assert "ok=" in r.headers["location"] and "app-release.zip" in r.headers["location"]
    [apk] = db.list_staged_apks()
    assert apk["sha256"] == hashlib.sha256(inner).hexdigest()  # the APK's hash, not the zip's
    assert apk["filename"] == "app-release.apk"
    with open(apk["path"], "rb") as f:
        assert f.read() == inner
    # Only the extracted APK remains: the zip and every temp file are gone.
    assert os.listdir(_uploads_dir()) == [f"{apk['sha256']}.apk"]


def test_zip_is_removed_before_the_apk_is_checked(authed, verify, monkeypatch):
    seen = []
    real = apk_verify.assert_apk_container

    def check(path):
        seen.append(sorted(os.listdir(_uploads_dir())))
        with open(path, "rb") as f:
            assert f.read() == _apk_bytes()  # the APK, not the zip
        real(path)
    monkeypatch.setattr(apk_verify, "assert_apk_container", check)
    _upload_zip(authed, _zip_bytes({"app.apk": _apk_bytes()}))
    assert len(seen) == 1 and len(seen[0]) == 1  # one temp file: the zip was already replaced


def test_zipped_and_bare_copies_of_one_apk_dedupe(authed, verify):
    _upload(authed, _apk_bytes())
    r = _upload_zip(authed, _zip_bytes({"app.apk": _apk_bytes()}))
    assert "already%20staged" in r.headers["location"]
    assert len(db.list_staged_apks()) == 1
    assert len(os.listdir(_uploads_dir())) == 1


def test_zipped_apk_is_held_to_the_same_rules(authed, verify, monkeypatch):
    def boom(path):
        raise apk_verify.ApkVerifyError("apksigner verify failed: no signature")
    monkeypatch.setattr(apk_verify, "verify_signature", boom)
    r = _upload_zip(authed, _zip_bytes({"app.apk": _apk_bytes()}))
    assert "apksigner" in r.headers["location"]
    assert db.list_staged_apks() == [] and os.listdir(_uploads_dir()) == []


def test_zipped_non_apk_is_refused(authed, verify):
    # Named .apk, but no manifest: found in the zip, then refused as an APK.
    r = _upload_zip(authed, _zip_bytes({"app.apk": _zip_bytes({"classes.dex": b"x"})}))
    assert "AndroidManifest" in r.headers["location"]
    assert db.list_staged_apks() == [] and os.listdir(_uploads_dir()) == []


@pytest.mark.parametrize("entries,needle", [
    ({"notes.txt": b"hi"}, "no%20APK"),
    ({"a/app-arm64.apk": _apk_bytes("a"), "b/app-x86.apk": _apk_bytes("b")}, "2%20APKs"),
])
def test_zip_must_hold_exactly_one_apk(authed, verify, entries, needle):
    r = _upload_zip(authed, _zip_bytes(entries))
    assert "error=" in r.headers["location"] and needle in r.headers["location"]
    assert db.list_staged_apks() == [] and os.listdir(_uploads_dir()) == []


def test_mac_resource_forks_are_not_counted(authed, verify):
    r = _upload_zip(authed, _zip_bytes({"app.apk": _apk_bytes(), "__MACOSX/._app.apk": b"junk"}))
    assert "ok=" in r.headers["location"]


def test_zip_entry_names_are_display_text_only(authed, verify):
    r = _upload_zip(authed, _zip_bytes({"../../../etc/evil.apk": _apk_bytes()}))
    assert "ok=" in r.headers["location"]
    [apk] = db.list_staged_apks()
    assert apk["filename"] == "evil.apk"
    assert apk["path"] == os.path.join(_uploads_dir(), f"{apk['sha256']}.apk")


def test_zipped_apk_over_the_cap_is_refused(authed, verify, monkeypatch):
    # Tiny on the wire, big once inflated: the cap applies to the APK, not the zip.
    big = _zip_bytes({"AndroidManifest.xml": b"\0" * 200_000}, zipfile.ZIP_STORED)
    content = _zip_bytes({"app.apk": big})
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", len(content) + 1000)
    r = _upload_zip(authed, content)
    assert "size%20cap" in r.headers["location"]
    assert db.list_staged_apks() == [] and os.listdir(_uploads_dir()) == []


def test_declared_size_over_the_cap_is_refused(tmp_path):
    src = tmp_path / "a.zip"
    src.write_bytes(_zip_bytes({"app.apk": b"\0" * 5000}))
    with pytest.raises(apk_verify.ApkVerifyError, match="size cap"):
        apk_verify.extract_archived_apk(str(src), str(tmp_path / "out.apk"), 4000)
    assert not (tmp_path / "out.apk").exists() or (tmp_path / "out.apk").stat().st_size == 0


def test_lying_declared_size_is_refused(tmp_path):
    # A bomb claims to be small: forge a 10-byte size into both headers. The
    # declared-size check passes, so what stops it is the inflate itself.
    data = bytearray(_zip_bytes({"app.apk": b"\0" * 50_000}))
    lh, cd = data.find(b"PK\x03\x04"), data.rfind(b"PK\x01\x02")
    data[lh + 22:lh + 26] = (10).to_bytes(4, "little")
    data[cd + 24:cd + 28] = (10).to_bytes(4, "little")
    src = tmp_path / "a.zip"
    src.write_bytes(bytes(data))
    with pytest.raises(apk_verify.ApkVerifyError):
        apk_verify.extract_archived_apk(str(src), str(tmp_path / "out.apk"), 4000)
    assert (tmp_path / "out.apk").stat().st_size <= 4000


def test_encrypted_entry_is_refused(tmp_path):
    data = bytearray(_zip_bytes({"app.apk": _apk_bytes()}, zipfile.ZIP_STORED))
    # Set the "encrypted" flag bit in both the local header and the central directory.
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        # rfind for the central directory: the stored APK carries its own first.
        i = data.find(sig) if off == 6 else data.rfind(sig)
        data[i + off] |= 0x1
    src = tmp_path / "a.zip"
    src.write_bytes(bytes(data))
    with pytest.raises(apk_verify.ApkVerifyError, match="encrypted"):
        apk_verify.extract_archived_apk(str(src), str(tmp_path / "out.apk"), 10**6)


def test_too_many_entries_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(apk_verify, "MAX_ARCHIVE_ENTRIES", 3)
    src = tmp_path / "a.zip"
    src.write_bytes(_zip_bytes({f"f{i}.txt": b"x" for i in range(4)} | {"app.apk": _apk_bytes()}))
    with pytest.raises(apk_verify.ApkVerifyError, match="entries"):
        apk_verify.extract_archived_apk(str(src), str(tmp_path / "out.apk"), 10**6)


def test_a_bare_apk_is_not_unwrapped(tmp_path):
    src = tmp_path / "app.apk"
    src.write_bytes(_apk_bytes())
    assert apk_verify.extract_archived_apk(str(src), str(tmp_path / "out.apk"), 10**6) is None


def test_corrupt_deflate_stream_is_refused_not_a_500(authed, verify):
    data = bytearray(_zip_bytes({"app.apk": _apk_bytes() * 50}))
    lh = data.find(b"PK\x03\x04")
    start = lh + 30 + int.from_bytes(data[lh + 26:lh + 28], "little")
    data[start:start + 16] = b"\xff" * 16  # garbage where the deflate stream begins
    r = _upload_zip(authed, bytes(data))
    assert r.status_code == 303 and "not%20a%20readable%20zip" in r.headers["location"]
    assert db.list_staged_apks() == [] and os.listdir(_uploads_dir()) == []
