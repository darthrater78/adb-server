"""The 3.1 page flow: Status · Sources · Devices · Install · Settings."""
import os

import pytest

import db
import staging
from conftest import CSRF


def _stage(repo_id, tag, filename, abis="", version_name="2.0", package="com.example", notes=None):
    os.makedirs(staging.repo_dir(repo_id), exist_ok=True)
    path = os.path.join(staging.repo_dir(repo_id), f"{tag}-{filename}")
    with open(path, "wb") as f:
        f.write(b"PK")
    return db.insert_staged_apk(repo_id, tag, filename, os.urandom(32).hex(), package, "a" * 64, path,
                                version_code=2, version_name=version_name, abis=abis, release_notes=notes)


def _stage_upload(label, package="com.example.dev", version_name="1.7-dev"):
    path = os.path.join(staging.STAGING_ROOT, "uploads", f"{label}.apk")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()
    return db.insert_staged_apk(None, label, f"{label}.apk", os.urandom(32).hex(), package, "d" * 64, path,
                                version_code=7, version_name=version_name, abis="arm64-v8a", source="upload")


@pytest.fixture
def trusted_device():
    db.upsert_paired_device("SER", "192.168.1.50:37000")
    db.set_device_trusted("SER", True)


def _nav(page):
    return page[page.index("<nav>"):page.index("</nav>")]


def test_nav_has_the_five_steps_in_order(authed):
    nav = _nav(authed.get("/status").text)
    hrefs = [part.split('"')[0] for part in nav.split('href="')[1:]]
    assert hrefs == ["/status", "/sources", "/devices", "/install", "/settings"]


@pytest.mark.parametrize("old,new", [("/repos", "/sources"), ("/upload", "/sources"), ("/staged", "/install")])
def test_old_pages_redirect_and_keep_their_flash(authed, old, new):
    r = authed.get(f"{old}?ok=Saved", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"{new}?ok=Saved"
    assert authed.get(old, follow_redirects=False).headers["location"] == new


def test_old_pages_still_need_a_login(client):
    for old in ("/repos", "/upload", "/staged"):
        assert client.get(old, follow_redirects=False).headers["location"] == "/login"


@pytest.mark.parametrize("path", ["/installs", "/audit"])
def test_activity_pages_sit_under_settings(authed, path):
    page = authed.get(path).text
    assert 'href="/settings" class="active"' in _nav(page)
    assert 'href="/settings#activity"' in page
    assert f'href="{path}"' in authed.get("/settings").text


def test_status_shows_the_setup_checklist_until_everything_is_done(authed, trusted_device):
    page = authed.get("/status").text
    assert "Get set up" in page and 'href="/sources"><strong>Add a source' in page
    rid = db.create_repo("o", "r", "*.apk")
    apk = _stage(rid, "v2", "app.apk")
    install = db.insert_install("SER", apk, "pending")
    db.finish_install(install, "success", "ok")
    assert "Get set up" not in authed.get("/status").text


def test_status_card_lists_the_devices_inventory(authed, trusted_device):
    rid = db.create_repo("o", "r", "*.apk")
    apk = _stage(rid, "v2", "app.apk", version_name="2.0")
    _stage_upload("dev build")
    db.upsert_device_package("SER", "com.example", installed=True, version_code=1, version_name="1.0")
    db.upsert_device_package("SER", "com.example.dev", installed=True, version_code=7, version_name="1.7-dev")
    db.upsert_device_package("SER", "com.gone", installed=False)
    install = db.insert_install("SER", apk, "pending")
    db.finish_install(install, "failed", "boom")
    page = authed.get("/status").text
    card = page[page.index('class="panel device-card"'):]
    assert "1.0 → 2.0" in card and "Update" in card
    assert "dev build" in card and "1.7-dev" in card  # an upload pushed to it
    assert "com.gone" not in card  # uninstalled packages aren't inventory
    assert "Recent installs" in card and f'href="/installs/{install}"' in card


def test_untrusted_device_card_offers_no_push(authed):
    db.upsert_paired_device("NEW", "192.168.1.51:37000")
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    page = authed.get("/status").text
    card = page[page.index('class="panel device-card"'):]
    assert "Not trusted" in card and "/push-latest" not in card


def test_install_groups_releases_by_app_newest_first(authed, trusted_device):
    rid = db.create_repo("o", "r", "*.apk")
    old = _stage(rid, "v1", "app.apk", version_name="1.0")
    new = _stage(rid, "v2", "app.apk", version_name="2.0", notes="Fresh notes")
    up = _stage_upload("dev build")
    page = authed.get("/install").text
    card = page[page.index(f'id="app-{rid}"'):page.index(f'id="upload-{up}"')]
    # The latest release is pushed by repo, so the right CPU build is picked.
    assert 'action="/push-latest"' in card and 'name="back" value="/install"' in card
    assert "Push 2.0" in card and "1 older" in card and "Fresh notes" in card
    assert card.index(f'id="apk-{new}"') < card.index(f'id="apk-{old}"')
    upload = page[page.index(f'id="upload-{up}"'):]
    assert "dev build" in upload and 'action="/push"' in upload and f'value="{up}"' in upload


def test_install_page_without_trusted_devices_points_to_devices(authed):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    page = authed.get("/install").text
    assert 'href="/devices">Pair and trust one' in page and "/push-latest" not in page


@pytest.mark.parametrize("back,expected", [("/install", "/install"), ("/status", "/status"),
                                           ("https://evil.example", "/status"), ("/settings", "/status")])
def test_push_latest_returns_only_to_known_pages(authed, trusted_device, back, expected):
    db.create_repo("o", "r", "*.apk")  # nothing staged, so the push is refused
    rid = db.list_repos()[0]["id"]
    r = authed.post("/push-latest", data={"csrf_token": CSRF, "device_serial": "SER", "repo_id": rid, "back": back},
                    follow_redirects=False)
    assert r.headers["location"].startswith(f"{expected}?error=")


def test_sources_lists_repos_and_uploads_together(authed):
    db.create_repo("octo", "hello", "*.apk")
    up = _stage_upload("dev build")
    page = authed.get("/sources").text
    assert "octo/hello" in page and "dev build" in page
    assert f'href="/install#upload-{up}"' in page and f'action="/staged/{up}/delete"' in page
