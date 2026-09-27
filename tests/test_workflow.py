"""The push workflow: one confirm-then-push flow that stays on the row it
started from (3.8.0), Update all per device, trust offered right after
pairing, and every block collapsible, open when it wants you."""
import os
import re

import pytest

import adb_client
import db
import pushes
import staging
import web
from conftest import CSRF

DOM = web.dom_id("SER")


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
    page = authed.get("/apps").text
    row = page[page.index(f'id="repo-{rid}"'):]
    trigger = row.index(f'href="#push-repo-{rid}"')
    dialog = row[row.index(f'id="push-repo-{rid}" class="modal"'):]
    assert trigger < row.index('action="/push-latest"')  # the button opens a dialog, the form is inside it
    assert "?</h2>" in dialog and "Push <strong>2.0</strong> to <strong>Pixel</strong>" in dialog
    assert 'name="back" value="/apps"' in dialog and 'name="csrf_token"' in dialog
    assert f'href="#repo-{rid}" class="button-link secondary">Cancel' in dialog


@pytest.mark.parametrize("back,lands", [
    ("/apps", "/apps?to=SER&open=repo-{rid}#repo-{rid}"),
    ("/library", "/status?open={dom}#app-{rid}-{dom}"),
    ("/status", "/status?open={dom}#app-{rid}-{dom}"),
    ("https://evil.example/", "/status?open={dom}#app-{rid}-{dom}")])
def test_a_push_lands_back_on_the_row_it_came_from(authed, device, queued, back, lands):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    r = authed.post("/push-latest", data={"csrf_token": CSRF, "device_serial": "SER", "repo_id": rid, "back": back},
                    follow_redirects=False)
    assert r.headers["location"] == lands.format(rid=rid, dom=DOM)
    assert len(queued) == 1


def test_the_row_shows_its_push_running_then_done(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    apk = _stage(rid, "v2", "app.apk")
    install = db.insert_install("SER", apk, status="installing")
    page = authed.get(f"/status?open={DOM}").text
    assert '<meta http-equiv="refresh" content="2">' in page  # reloads itself while it runs
    row = page[page.index(f'id="app-{rid}-{DOM}"'):]
    row = row[:row.index('<div class="row-follow">')]
    assert "Installing 2.0…" in row and 'action="/push-latest"' not in row  # no second push while one runs
    assert 'class="progress-bar indeterminate"' in row  # a moving bar in the row itself
    activity = page[page.index('class="card activity"'):]
    assert "Installing…" in activity and f'href="/installs/{install}"' in activity
    db.finish_install(install, "success", "Success")
    page = authed.get(f"/status?open={DOM}").text
    assert "http-equiv" not in page and "Installed" in page[page.index('class="card activity"'):]
    row = page[page.index(f'id="app-{rid}-{DOM}"'):]
    row = row[:row.index('<div class="row-follow">')]
    assert 'class="progress-bar success"' in row and "✓ Installed 2.0" in row  # and how it went, where you are


def test_a_failed_push_says_so_on_its_row(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    install = db.insert_install("SER", _stage(rid, "v2", "app.apk"), status="installing")
    db.finish_install(install, "failed", "INSTALL_FAILED_VERSION_DOWNGRADE")
    page = authed.get("/status").text
    row = page[page.index(f'id="app-{rid}-{DOM}"'):]
    row = row[:row.index('<div class="row-follow">')]
    assert "Push failed" in row and 'class="progress-bar failed"' in row and f'href="/installs/{install}"' in row


def test_the_progress_page_refreshes_while_running_and_never_leaves(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    apk = _stage(rid, "v2", "app.apk")
    install = db.insert_install("SER", apk, status="installing")
    page = authed.get(f"/installs/{install}?back=%2Fapps%3Fto%3DSER").text
    assert '<meta http-equiv="refresh" content="2">' in page and 'href="/apps?to=SER"' in page
    db.finish_install(install, "success", "Success")
    page = authed.get(f"/installs/{install}?back=%2Fapps%3Fto%3DSER").text
    # Opened from history, a finished push stays put.
    assert "http-equiv" not in page and 'href="/apps?to=SER" class="button-link">Back to Apps' in page


def test_a_failed_push_stays_put_with_its_log(authed, device):
    apk = _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    install = db.insert_install("SER", apk, status="installing")
    db.finish_install(install, "failed", "INSTALL_FAILED_VERSION_DOWNGRADE")
    page = authed.get(f"/installs/{install}").text
    assert "http-equiv" not in page and "INSTALL_FAILED_VERSION_DOWNGRADE" in page
    assert 'href="/status" class="button-link">Back to Status' in page


@pytest.mark.parametrize("back", ["https://evil.example/status", "//evil.example/status", "/settings",
                                  "/status?ok=<script>", "/apps?to=NOPE", "/install"])
def test_the_way_back_is_rebuilt_never_echoed(authed, device, back):
    apk = _stage(db.create_repo("o", "r", "*.apk"), "v2", "app.apk")
    install = db.insert_install("SER", apk, status="failed")
    page = authed.get(f"/installs/{install}", params={"back": back}).text
    link = re.search(r'href="([^"]*)" class="button-link">Back to', page).group(1)
    assert link in ("/status", "/apps")


# ---- update all ----

def test_update_all_shows_only_with_two_or_more(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    db.upsert_device_package("SER", "com.example", installed=True, version_code=1, version_name="1.0")
    assert 'action="/update-all"' not in authed.get("/status").text
    _two_updates()
    page = authed.get("/status").text
    assert 'action="/update-all"' in page and "3 updates ready" in page
    assert f'href="#confirm-all-{DOM}" class="button-link">Update all' in page  # the phone summary card too


def test_update_all_queues_every_update_one_after_another(authed, device, queued):
    _two_updates()
    r = authed.post("/update-all", data={"csrf_token": CSRF, "device_serial": "SER"}, follow_redirects=False)
    assert r.headers["location"] == f"/status?open={DOM}#{DOM}"
    assert sorted(p for _, p in queued) == ["com.one", "com.two"]
    page = authed.get(r.headers["location"]).text
    activity = page[page.index('class="card activity"'):]
    assert activity.count('class="activity-row"') == 2 and '<meta http-equiv="refresh" content="2">' in page


def test_update_all_refuses_an_untrusted_device(authed, device, queued):
    _two_updates()
    db.set_device_trusted("SER", False)
    r = authed.post("/update-all", data={"csrf_token": CSRF, "device_serial": "SER"}, follow_redirects=False)
    assert r.headers["location"].startswith("/status?error=") and queued == []
    assert r.headers["location"].endswith(f"&open={DOM}#{DOM}")


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
    page = authed.get("/devices?trust=NEWPHONE1").text
    offer = page[page.index('id="trust-offer"'):]
    assert 'name="back" value="/status"' in offer[:offer.index("</form>")]
    r = authed.post("/devices/NEWPHONE1/trust", data={"csrf_token": CSRF, "trusted": "1", "nickname": " Kid's phone ",
                                                      "back": "/status"}, follow_redirects=False)
    d = db.get_device("NEWPHONE1")
    assert (d["trusted"], d["nickname"]) == (1, "Kid's phone") and "Trusted" in r.headers["location"]
    # On to its card on Status, ready to install.
    dom = web.dom_id("NEWPHONE1")
    assert r.headers["location"].startswith("/status?") and r.headers["location"].endswith(f"open={dom}#{dom}")


@pytest.mark.parametrize("serial", ["SER", "NOPE"])
def test_no_offer_for_a_trusted_or_unknown_device(authed, device, serial):
    assert 'id="trust-offer"' not in authed.get(f"/devices?trust={serial}").text


# ---- collapsible blocks ----

def test_add_forms_start_folded_even_when_empty(authed):
    # Everything outside Settings starts collapsed, empty page or not.
    assert '<details class="panel fold">\n  <summary><h2>Add a device' in authed.get("/devices").text
    page = authed.get("/apps").text
    assert '<details class="panel fold" open>' not in page and 'id="upload">' in page


def _quiet():
    """Two trusted devices with every app current, two healthy repos, two
    kinds staged and setup done: nothing that wants you."""
    db.upsert_paired_device("SER2", "192.168.1.51:37000")
    db.set_device_trusted("SER2", True)
    for n in range(2):
        rid = db.create_repo("o", f"r{n}", "*.apk")
        apk = _stage(rid, "v2", "app.apk", package=f"com.r{n}")
        for serial in ("SER", "SER2"):
            db.upsert_device_package(serial, f"com.r{n}", True, version_code=2, version_name="2.0")
    db.finish_install(db.insert_install("SER", apk, status="pending"), "success", "ok")
    db.insert_staged_apk(None, "dev", "dev.apk", "e" * 64, "com.dev", "d" * 64, "/nonexistent", source="upload")


@pytest.mark.parametrize("path", ["/status", "/sources", "/devices", "/library"])
def test_quiet_blocks_start_collapsed(authed, device, path):
    _quiet()
    page = authed.get(path).text
    assert " open>" not in page, path


def test_blocks_that_want_you_start_open(authed, device):
    _quiet()
    db.upsert_device_package("SER", "com.r0", True, version_code=1, version_name="1.0")  # an update
    db.upsert_paired_device("NEW", "192.168.1.52:37000")  # untrusted
    db.update_repo_check(db.list_repos()[1]["id"], last_error="GitHub said no")
    status = authed.get("/status").text
    assert f'id="{DOM}" open>' in status and f'id="{web.dom_id("NEW")}" open>' in status
    assert f'id="{web.dom_id("SER2")}">' in status
    sources = authed.get("/apps").text
    rids = [r["id"] for r in db.list_repos()]
    # A repo is never folded; one with an error is marked as such.
    assert f'repo-card repo-card-error" id="repo-{rids[1]}">' in sources
    assert f'class="card row-card repo-card" id="repo-{rids[0]}">' in sources
    devices = authed.get("/devices").text
    assert f'id="{web.dom_id("NEW")}" open>' in devices and f'id="{DOM}">' in devices


def test_sources_and_devices_have_no_tables(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    for path in ("/sources", "/devices"):
        assert "<table" not in authed.get(path).text, path


def test_each_repo_and_device_is_its_own_card(authed, device):
    db.create_repo("o", "r", "*.apk")
    assert '<section class="card row-card repo-card" id="repo-' in authed.get("/apps").text
    assert f'<details class="card row-card device-card item-card" id="{DOM}"' in authed.get("/devices").text


def test_a_repos_head_shows_its_most_recent_release(authed):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v1", "app.apk", version_name="1.0")
    _stage(rid, "v2", "app.apk", version_name="2.0")
    page = authed.get("/apps").text
    head = page[page.index('<h2 class="device-name">o/r'):]
    head = head[:head.index("</summary>")]
    assert "2.0" in head and "1.0" not in head


def test_a_repo_whose_release_was_deleted_says_so(authed):
    from conftest import CSRF
    rid = db.create_repo("o", "r", "*.apk")
    apk_id = _stage(rid, "v2", "app.apk")
    db.update_repo_check(rid, last_tag="v2")
    authed.post(f"/staged/{apk_id}/delete", data={"csrf_token": CSRF})
    page = authed.get("/apps").text
    assert "files deleted" in page and "Check now stages it again" in page


def test_devices_explains_find(authed, device):
    page = authed.get("/devices").text
    assert '<summary>What Find and Reconnect do</summary>' in page  # folded until asked


@pytest.mark.parametrize("path", ["/status", "/sources", "/devices", "/library", "/settings", "/settings/general",
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
    for keep in ('<section class="row-item', '<section class="settings-section">', '<section class="card row-card repo-card'):
        main = main.replace(keep, "")
    assert "<section" not in main
    assert 'class="fold' in main or 'class="panel fold' in main or 'class="settings-section fold' in main \
        or 'device-card' in main


def test_an_apk_cant_inject_markup_through_its_version_or_label(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk", version_name='<img src=x onerror=alert(1)>')
    db.insert_staged_apk(None, '<b>up</b>', "u.apk", "e" * 64, "com.up", "d" * 64, "/nonexistent",
                         version_name='"><script>x</script>', source="upload")
    for path in ("/apps", "/status"):  # Status lists uploads too, with their push dialogs
        page = authed.get(path).text
        assert "<img src=x" not in page and "<script>x" not in page and "<b>up</b>" not in page, path
        assert "&lt;img src=x onerror=alert(1)&gt;" in page and "&lt;b&gt;up&lt;/b&gt;" in page, path


def test_apps_sums_up_where_the_watched_apps_stand(authed, device):
    for n, (pkg, has) in enumerate([("com.a", 1), ("com.b", 2), ("com.c", None)]):
        rid = db.create_repo("o", f"r{n}", "*.apk")
        _stage(rid, "v2", "app.apk", package=pkg, version_code=2)
        if has:
            db.upsert_device_package("SER", pkg, True, version_code=has, version_name=str(has))
    db.insert_staged_apk(None, "dev", "dev.apk", "e" * 64, "com.dev", "d" * 64, "/nonexistent", source="upload")
    page = authed.get("/apps").text
    head = page[page.index('class="muted repo-summary"'):]
    head = head[:head.index("</p>")]
    assert "3 repos watched" in head and "on Pixel" in head
    assert "1 to update" in head and "1 up to date" in head and "1 not installed" in head
    assert 'id="kind-upload"' in page


def test_stylesheet_url_changes_with_its_content(authed):
    import hashlib
    import web
    page = authed.get("/status").text
    with open(os.path.join(os.path.dirname(web.__file__), "static", "style.css"), "rb") as f:
        want = hashlib.sha256(f.read()).hexdigest()[:12]
    assert f'href="/static/style.css?v={want}"' in page
    # accent.css still loads after it, so the chosen colours win.
    assert page.index("/static/style.css?v=") < page.index("/accent.css?v=")
