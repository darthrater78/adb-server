import os

import pytest

import db
import staging
from conftest import CSRF


def _staged_apk(tmp_path, repo_id, tag="v1"):
    os.makedirs(staging.repo_dir(repo_id), exist_ok=True)
    path = os.path.join(staging.repo_dir(repo_id), f"{tag}.apk")
    with open(path, "wb") as f:
        f.write(b"PK")
    return db.insert_staged_apk(repo_id, tag, "app.apk", "0" * 64, "com.example", "a" * 64, path), path


def test_unauthenticated_redirects_to_login(client):
    r = client.get("/repos", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_login_wrong_password(client):
    r = client.post("/login", data={"username": "operator", "password": "nope"})
    assert r.status_code == 401


def test_login_ok_sets_session(client):
    r = client.post("/login", data={"username": "operator", "password": "correct-horse-battery"}, follow_redirects=False)
    assert r.status_code == 303 and "adb_server_session" in r.cookies


def test_post_without_valid_csrf_is_rejected(authed):
    r = authed.post("/repos", data={"csrf_token": "wrong", "repo_url": "o/r"})
    assert r.status_code == 403
    assert db.list_repos() == []


def test_foreign_origin_is_rejected(authed):
    r = authed.post("/repos", data={"csrf_token": CSRF, "repo_url": "o/r"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


@pytest.mark.parametrize("nxt,expected", [("/devices", "/devices"), ("https://evil.example", "/status"), ("//evil.example", "/status")])
def test_theme_redirect_is_allow_listed(authed, nxt, expected):
    r = authed.post("/theme", data={"csrf_token": CSRF, "theme": "dark", "next": nxt}, follow_redirects=False)
    assert r.headers["location"] == expected


def test_push_to_untrusted_device_is_refused(authed, tmp_path):
    rid = db.create_repo("o", "r", "*.apk")
    apk_id, _ = _staged_apk(tmp_path, rid)
    db.upsert_paired_device("SERIAL1", "192.168.1.50:5555")
    r = authed.post("/push", data={"csrf_token": CSRF, "device_serial": "SERIAL1", "apk_id": apk_id}, follow_redirects=False)
    assert r.status_code == 303 and "not%20trusted" in r.headers["location"]
    assert db.list_installs() == []


def test_push_of_pruned_apk_is_refused(authed, tmp_path):
    rid = db.create_repo("o", "r", "*.apk")
    apk_id, _ = _staged_apk(tmp_path, rid)
    db.upsert_paired_device("SERIAL1", "192.168.1.50:5555")
    db.set_device_trusted("SERIAL1", True)
    db.mark_apk_pruned(apk_id)
    r = authed.post("/push", data={"csrf_token": CSRF, "device_serial": "SERIAL1", "apk_id": apk_id}, follow_redirects=False)
    assert "pruned" in r.headers["location"]
    assert db.list_installs() == []


def test_delete_repo_removes_staged_files(authed, tmp_path):
    rid = db.create_repo("o", "r", "*.apk")
    _, path = _staged_apk(tmp_path, rid)
    authed.post(f"/repos/{rid}/delete", data={"csrf_token": CSRF})
    assert not os.path.exists(path)
    assert not os.path.exists(staging.repo_dir(rid))


def test_delete_staged_file_keeps_install_history(authed, tmp_path):
    rid = db.create_repo("o", "r", "*.apk")
    apk_id, path = _staged_apk(tmp_path, rid)
    db.upsert_paired_device("SERIAL1", "192.168.1.50:5555")
    db.insert_install("SERIAL1", apk_id, status="success")
    authed.post(f"/staged/{apk_id}/delete", data={"csrf_token": CSRF})
    assert not os.path.exists(path)
    assert db.list_staged_apks() == []
    assert len(db.list_installs()) == 1


def test_staged_page_shows_error_flash(authed):
    r = authed.get("/staged?error=Device%20is%20not%20trusted")
    assert "Device is not trusted" in r.text


def test_security_headers(authed):
    r = authed.get("/repos")
    assert "script-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_rate_limit_forgets_expired_clients(client, monkeypatch):
    import auth
    auth._failed_attempts["10.9.9.9"] = [0.0]  # far outside the window

    class Req:
        client = type("C", (), {"host": "10.9.9.9"})()
    auth.check_rate_limit(Req())
    assert "10.9.9.9" not in auth._failed_attempts


def test_header_links_to_the_repo_and_this_versions_release_notes(authed):
    import web

    page = authed.get("/status").text
    header = page[page.index("<header"):page.index("</header>")]
    assert 'href="https://github.com/darthrater78/adb-server"' in header
    assert f'href="https://github.com/darthrater78/adb-server/releases/tag/v{web.APP_VERSION}"' in header
    assert web.APP_VERSION != "unknown"


def test_ui_fonts_are_served_as_fonts(client):
    r = client.get("/static/fonts/figtree-latin-wght-normal.woff2")
    assert r.status_code == 200 and r.headers["content-type"] == "font/woff2"
