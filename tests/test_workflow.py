"""3.7.0 workflow: one confirm-then-progress push flow that leads back where
it started, Update all per device, trust offered right after pairing, and
every block collapsible."""
import os
import re

import pytest

import adb_client
import db
import pushes
import staging
from conftest import CSRF


def _stage(repo_id, tag, filename, package="com.example", version_code=2, version_name="2.0"):
    os.makedirs(staging.repo_dir(repo_id), exist_ok=True)
    path = os.path.join(staging.repo_dir(repo_id), f"{tag}-{filename}")
    with open(path, "wb") as f:
        f.write(b"PK")
    return db.insert_staged_apk(repo_id, tag, filename, os.urandom(32).hex(), package, "a" * 64, path,
                                version_code=version_code, version_name=version_name)


@pytest.fixture
def device():
    db.upsert_paired_device("SER", "192.168.1.50:37000")
    db.set_device_trusted("SER", True)
    db.set_device_nickname("SER", "Pixel")


@pytest.fixture
def queued(monkeypatch):
    ran = []
    monkeypatch.setattr(pushes, "run_push", lambda install_id, device, apk: ran.append((install_id, apk["package_name"])))
    return ran


def _two_updates():
    """Two watched apps, each with a newer release than the device has."""
    ids = []
    for n, package in enumerate(("com.one", "com.two")):
        rid = db.create_repo("o", f"r{n}", "*.apk")
        _stage(rid, "v2", "app.apk", package=package)
        db.upsert_device_package("SER", package, installed=True, version_code=1, version_name="1.0")
        ids.append(rid)
    return ids


# ---- one push flow ----

def test_install_push_confirms_first_like_status(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    page = authed.get("/install").text
    row = page[page.index(f'id="app-{rid}"'):]
    trigger = row.index(f'href="#push-app-{rid}"')
    dialog = row[row.index(f'id="push-app-{rid}" class="modal"'):]
    assert trigger < row.index('action="/push-latest"')  # the button opens a dialog, the form is inside it
    assert "?</h2>" in dialog and "Push <strong>2.0</strong> to <strong>Pixel</strong>" in dialog
    assert 'name="back" value="/install"' in dialog and 'name="csrf_token"' in dialog
    assert f'href="#app-{rid}" class="button-link secondary">Cancel' in dialog


@pytest.mark.parametrize("back,lands", [("/install", "back=%2Finstall%3Fto%3DSER"), ("/status", "back=%2Fstatus"),
                                        ("https://evil.example/", "back=%2Fstatus")])
def test_a_push_goes_to_progress_that_knows_the_way_back(authed, device, queued, back, lands):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    r = authed.post("/push-latest", data={"csrf_token": CSRF, "device_serial": "SER", "repo_id": rid, "back": back},
                    follow_redirects=False)
    assert re.fullmatch(r"/installs/\d+\?" + re.escape(lands), r.headers["location"])
    assert len(queued) == 1


def test_progress_refreshes_while_running_then_returns(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    apk = _stage(rid, "v2", "app.apk")
    install = db.insert_install("SER", apk, status="installing")
    page = authed.get(f"/installs/{install}?back=%2Finstall%3Fto%3DSER").text
    assert '<meta http-equiv="refresh" content="2">' in page and 'href="/install?to=SER"' in page
    db.finish_install(install, "success", "Success")
    page = authed.get(f"/installs/{install}?back=%2Finstall%3Fto%3DSER").text
    assert '<meta http-equiv="refresh" content="3; url=/install?to=SER&amp;ok=Installed%20' in page
    assert "Taking you back to Install" in page


def test_a_failed_push_stays_put_with_its_log(authed, device):
    apk = _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    install = db.insert_install("SER", apk, status="installing")
    db.finish_install(install, "failed", "INSTALL_FAILED_VERSION_DOWNGRADE")
    page = authed.get(f"/installs/{install}").text
    assert "http-equiv" not in page and "INSTALL_FAILED_VERSION_DOWNGRADE" in page
    assert 'href="/status" class="button-link">Back to Status' in page


@pytest.mark.parametrize("back", ["https://evil.example/status", "//evil.example/status", "/settings",
                                  "/status?ok=<script>", "/install?to=NOPE"])
def test_the_way_back_is_rebuilt_never_echoed(authed, device, back):
    apk = _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    install = db.insert_install("SER", apk, status="failed")
    page = authed.get(f"/installs/{install}", params={"back": back}).text
    link = re.search(r'href="([^"]*)" class="button-link">Back to', page).group(1)
    assert link in ("/status", "/install")


# ---- update all ----

def test_update_all_shows_only_with_two_or_more(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    db.upsert_device_package("SER", "com.example", installed=True, version_code=1, version_name="1.0")
    assert 'action="/update-all"' not in authed.get("/status").text
    _two_updates()
    page = authed.get("/status").text
    assert 'action="/update-all"' in page and "3 updates ready" in page
    assert 'href="#confirm-all-1" class="button-link">Update all' in page  # the phone summary card too


def test_update_all_queues_every_update_one_after_another(authed, device, queued):
    _two_updates()
    r = authed.post("/update-all", data={"csrf_token": CSRF, "device_serial": "SER"}, follow_redirects=False)
    assert re.fullmatch(r"/installs/batch\?ids=\d+,\d+&back=%2Fstatus", r.headers["location"])
    assert sorted(p for _, p in queued) == ["com.one", "com.two"]
    page = authed.get(r.headers["location"]).text
    assert "Updating 2 apps" in page and page.count('class="card push-progress"') == 2


def test_update_all_refuses_an_untrusted_device(authed, device, queued):
    _two_updates()
    db.set_device_trusted("SER", False)
    r = authed.post("/update-all", data={"csrf_token": CSRF, "device_serial": "SER"}, follow_redirects=False)
    assert r.headers["location"].startswith("/status?error=") and queued == []


def test_update_all_needs_csrf(authed, device, queued):
    _two_updates()
    r = authed.post("/update-all", data={"csrf_token": "wrong", "device_serial": "SER"}, follow_redirects=False)
    assert r.status_code in (400, 403) and queued == []


@pytest.mark.parametrize("ids", ["", "x", "999"])
def test_batch_progress_404s_on_nothing(authed, ids):
    assert authed.get(f"/installs/batch?ids={ids}").status_code == 404


# ---- pair, then trust ----

def _pair(authed, monkeypatch, serial="NEWPHONE1"):
    monkeypatch.setattr(adb_client, "pair", lambda a, c: "Successfully paired")
    monkeypatch.setattr(adb_client, "connect", lambda a: "connected")
    monkeypatch.setattr(adb_client, "get_serialno", lambda a: serial)
    monkeypatch.setattr(adb_client, "device_abis", lambda a: ["arm64-v8a"])
    monkeypatch.setattr(adb_client, "device_model", lambda a: "Pixel 8")
    return authed.post("/devices/pair", data={"csrf_token": CSRF, "pairing_addr": "192.168.1.60:40001",
                                              "pairing_code": "123456", "connect_addr": "192.168.1.60:41999"},
                       follow_redirects=False)


def test_pairing_offers_trust_straight_away(authed, monkeypatch):
    r = _pair(authed, monkeypatch)
    assert r.headers["location"].startswith("/devices?trust=NEWPHONE1&ok=Paired")
    page = authed.get(r.headers["location"]).text
    offer = page[page.index('id="trust-offer"'):page.index("</details>", page.index('id="trust-offer"'))]
    assert "Pixel 8" in offer and 'action="/devices/NEWPHONE1/trust"' in offer and 'name="nickname"' in offer
    assert db.get_device("NEWPHONE1")["trusted"] == 0  # offered, not given


def test_trusting_from_the_offer_can_name_it(authed, monkeypatch):
    _pair(authed, monkeypatch)
    r = authed.post("/devices/NEWPHONE1/trust", data={"csrf_token": CSRF, "trusted": "1", "nickname": " Kid's phone "},
                    follow_redirects=False)
    d = db.get_device("NEWPHONE1")
    assert (d["trusted"], d["nickname"]) == (1, "Kid's phone") and "Trusted" in r.headers["location"]


@pytest.mark.parametrize("serial", ["SER", "NOPE"])
def test_no_offer_for_a_trusted_or_unknown_device(authed, device, serial):
    assert 'id="trust-offer"' not in authed.get(f"/devices?trust={serial}").text


# ---- collapsible blocks ----

def test_add_forms_start_folded_even_when_empty(authed):
    # Everything outside Settings starts collapsed, empty page or not.
    assert '<details class="panel fold">\n  <summary><h2>Add a device' in authed.get("/devices").text
    page = authed.get("/sources").text
    assert '<details class="panel fold" open>' not in page and 'id="upload">' in page


@pytest.mark.parametrize("path", ["/status", "/sources", "/devices", "/install"])
def test_every_block_outside_settings_starts_collapsed(authed, device, path):
    db.set_device_trusted("SER", True)
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    db.insert_staged_apk(None, "dev", "dev.apk", "e" * 64, "com.dev", "d" * 64, "/nonexistent", source="upload")
    page = authed.get(path).text
    assert " open>" not in page, path


def test_sources_and_devices_have_no_tables(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    for path in ("/sources", "/devices"):
        assert "<table" not in authed.get(path).text, path


def test_each_repo_and_device_is_its_own_folded_card(authed, device):
    db.create_repo("o", "r", "*.apk")
    assert '<details class="card row-card device-card item-card" id="repo-' in authed.get("/sources").text
    assert '<details class="card row-card device-card item-card" id="dev-1">' in authed.get("/devices").text


def test_a_repos_head_shows_its_most_recent_release(authed):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v1", "app.apk", version_name="1.0")
    _stage(rid, "v2", "app.apk", version_name="2.0")
    page = authed.get("/sources").text
    head = page[page.index('<h2 class="device-name">o/r'):]
    head = head[:head.index("</summary>")]
    assert "2.0" in head and "1.0" not in head


def test_a_repo_whose_release_was_deleted_says_so(authed):
    from conftest import CSRF
    rid = db.create_repo("o", "r", "*.apk")
    apk_id = _stage(rid, "v2", "app.apk")
    db.update_repo_check(rid, last_tag="v2")
    authed.post(f"/staged/{apk_id}/delete", data={"csrf_token": CSRF})
    page = authed.get("/sources").text
    assert "not staged" in page and "Check now</strong> downloads and verifies it again" in page


def test_devices_explains_find(authed, device):
    assert "What Find does" in authed.get("/devices").text


@pytest.mark.parametrize("path", ["/status", "/sources", "/devices", "/install", "/settings", "/settings/general",
                                  "/settings/security", "/settings/notifications", "/settings/github",
                                  "/settings/appearance"])
def test_every_block_folds(authed, device, path):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    db.insert_staged_apk(None, "dev", "dev.apk", "e" * 64, "com.dev", "d" * 64, "/nonexistent", source="upload")
    page = authed.get(path).text
    main = page[page.index("<main>"):]
    for tag in ("details", "section", "summary", "div", "form"):
        assert main.count(f"<{tag}") == main.count(f"</{tag}>"), tag
    # Sections left are rows inside a folding card, or a page's one untitled wrapper.
    for keep in ('<section class="row-item', '<section class="settings-section">'):
        main = main.replace(keep, "")
    assert "<section" not in main
    assert 'class="fold' in main or 'class="panel fold' in main or 'class="settings-section fold' in main \
        or 'device-card' in main


def test_an_apk_cant_inject_markup_through_its_version_or_label(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk", version_name='<img src=x onerror=alert(1)>')
    db.insert_staged_apk(None, '<b>up</b>', "u.apk", "e" * 64, "com.up", "d" * 64, "/nonexistent",
                         version_name='"><script>x</script>', source="upload")
    page = authed.get("/install").text
    assert "<img src=x" not in page and "<script>x" not in page and "<b>up</b>" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page


def test_install_kind_heads_sum_up_where_the_apps_stand(authed, device):
    for n, (pkg, has) in enumerate([("com.a", 1), ("com.b", 2), ("com.c", None)]):
        rid = db.create_repo("o", f"r{n}", "*.apk")
        _stage(rid, "v2", "app.apk", package=pkg, version_code=2)
        if has:
            db.upsert_device_package("SER", pkg, True, version_code=has, version_name=str(has))
    db.insert_staged_apk(None, "dev", "dev.apk", "e" * 64, "com.dev", "d" * 64, "/nonexistent", source="upload")
    page = authed.get("/install").text
    head = page[page.index('id="kind-release"'):]
    head = head[:head.index("</summary>")]
    assert "3 apps" in head
    assert "1 to update" in head and "1 up to date" in head and "1 not installed" in head
    assert 'id="kind-upload"' in page and 'id="kind-release" open' not in page


def test_picking_a_kind_opens_its_card(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    db.insert_staged_apk(None, "dev", "dev.apk", "e" * 64, "com.dev", "d" * 64, "/nonexistent", source="upload")
    assert 'id="kind-release" open>' in authed.get("/install?show=release").text


def test_stylesheet_url_changes_with_its_content(authed):
    import hashlib
    import web
    page = authed.get("/status").text
    with open(os.path.join(os.path.dirname(web.__file__), "static", "style.css"), "rb") as f:
        want = hashlib.sha256(f.read()).hexdigest()[:12]
    assert f'href="/static/style.css?v={want}"' in page
    # accent.css still loads after it, so the chosen colours win.
    assert page.index("/static/style.css?v=") < page.index("/accent.css?v=")
