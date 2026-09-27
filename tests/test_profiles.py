"""Phones with more than one profile (Private space, a work profile), the
Library's delete-a-version, and the top bar knowing where you are."""
import os
import re
import subprocess

import pytest

import adb_client
import db
import discovery
import pushes
import web
from conftest import CSRF
from test_features import _stage

ADDR = "192.168.1.50:37000"


def _proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _dumpsys(main: bool, private: bool) -> str:
    return f"""Packages:
  Package [com.example.app] (1a2b3c):
    versionCode=41 minSdk=24 targetSdk=35
    versionName=1.4.1
    lastUpdateTime=2026-09-24 21:10:45
    User 0: ceDataInode=1 deDataInode=2 installed={str(main).lower()} hidden=false suspended=false
      firstInstallTime=2026-09-01 08:00:00
    User 10: ceDataInode=0 deDataInode=0 installed={str(private).lower()} hidden=false suspended=false
"""


USERS = "Users:\n\tUserInfo{0:Alex:4c13} running\n\tUserInfo{10:Private space:1090}\n"


def _fake_adb(monkeypatch, dumpsys: str, current="0"):
    calls = []

    def run(*args, timeout):
        calls.append(args)
        if "dumpsys" in args:
            return _proc(dumpsys)
        if "get-current-user" in args:
            return _proc(current + "\n")
        if "list" in args and "users" in args:
            return _proc(USERS)
        if "uninstall" in args:
            return _proc("Success\n")
        return _proc()
    monkeypatch.setattr(adb_client, "_run", run)
    return calls


# ---- adb: which profile has the app ----

def test_installs_only_into_the_profile_in_use(monkeypatch):
    calls = _fake_adb(monkeypatch, "")
    adb_client.install(ADDR, "/data/staging/1/x.apk")
    assert calls[-1][calls[-1].index("install"):] == ("install", "-r", "--user", "current", "/data/staging/1/x.apk")


def test_installed_in_every_profile_is_installed(monkeypatch):
    calls = _fake_adb(monkeypatch, _dumpsys(True, True))
    assert adb_client.package_info(ADDR, "com.example.app").version_code == 41
    assert not any("get-current-user" in c for c in calls)  # nothing to decide, no extra call


def test_a_copy_only_in_private_space_is_not_installed_here(monkeypatch):
    _fake_adb(monkeypatch, _dumpsys(False, True))
    assert adb_client.package_info(ADDR, "com.example.app") is None


def test_the_profile_in_use_decides(monkeypatch):
    _fake_adb(monkeypatch, _dumpsys(False, True), current="10")
    assert adb_client.package_info(ADDR, "com.example.app").version_name == "1.4.1"


def test_uninstalled_everywhere_but_remembered_is_not_installed(monkeypatch):
    _fake_adb(monkeypatch, _dumpsys(False, False))
    assert adb_client.package_info(ADDR, "com.example.app") is None
    assert adb_client.profiles_holding(ADDR, "com.example.app") == []


def test_profiles_holding_names_them(monkeypatch):
    _fake_adb(monkeypatch, _dumpsys(False, True))
    assert adb_client.profiles_holding(ADDR, "com.example.app") == [(10, "Private space")]


def test_uninstall_everywhere_checks_the_package_name(monkeypatch):
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: pytest.fail("adb must not run"))
    with pytest.raises(adb_client.AdbError):
        adb_client.uninstall_everywhere(ADDR, "com.x;reboot")


def test_uninstall_everywhere_reports_a_refusal(monkeypatch):
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: _proc("Failure [DELETE_FAILED_INTERNAL_ERROR]\n"))
    with pytest.raises(adb_client.AdbError, match="DELETE_FAILED"):
        adb_client.uninstall_everywhere(ADDR, "com.example.app")


# ---- a refused push explains itself ----

@pytest.fixture
def device():
    db.upsert_paired_device("SER", ADDR)
    db.set_device_trusted("SER", True)
    db.set_device_abis("SER", ["arm64-v8a"])
    return db.get_device("SER")


@pytest.fixture
def refused(monkeypatch, device):
    """A phone that refuses the push as a downgrade, because Private space still has a newer copy."""
    monkeypatch.setattr(discovery, "ensure_connected", lambda d, allow_scan=True: (d["serial"], ADDR))
    monkeypatch.setattr(adb_client, "device_abis", lambda a: ["arm64-v8a"])
    monkeypatch.setattr(adb_client, "device_model", lambda a: "Pixel")
    monkeypatch.setattr(adb_client, "install", lambda a, p: _proc(
        "Failure [INSTALL_FAILED_VERSION_DOWNGRADE: Downgrade detected]\n", 1))
    monkeypatch.setattr(adb_client, "package_info", lambda a, p: None)
    monkeypatch.setattr(adb_client, "profiles_holding", lambda a, p: [(10, "Private space")])
    removed = []
    monkeypatch.setattr(adb_client, "uninstall_everywhere", lambda a, p: removed.append(p))
    rid = db.create_repo("o", "r", "*.apk")
    apk = db.get_staged_apk(_stage(rid, "v1", "app.apk"))
    install_id = pushes.create_install(device, apk)
    pushes.run_push(install_id, dict(device), dict(apk))
    return {"id": install_id, "removed": removed}


def test_a_downgrade_refusal_names_the_profile_that_blocks_it(refused):
    log = db.get_install(refused["id"])["log"]
    assert "INSTALL_FAILED_VERSION_DOWNGRADE" in log
    assert "newer version of com.example in Private space" in log and "gone from all of them" in log


def test_other_failures_get_no_explanation():
    assert pushes.explain_blocked(ADDR, "com.example", "Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]") == ""


def test_the_status_page_offers_removing_it_everywhere(authed, refused):
    page = authed.get(f"/installs/{refused['id']}").text
    assert f'action="/installs/{refused["id"]}/remove-everywhere"' in page and "Remove from every profile" in page


def test_removing_it_everywhere(authed, refused):
    r = authed.post(f"/installs/{refused['id']}/remove-everywhere", data={"csrf_token": CSRF, "back": "/status"},
                    follow_redirects=False)
    assert r.status_code == 303 and "Removed" in r.headers["location"]
    assert refused["removed"] == ["com.example"]
    assert any(a["action"] == "uninstall_everywhere" for a in db.list_audit())


def test_removing_needs_a_trusted_device(authed, refused):
    db.set_device_trusted("SER", False)
    authed.post(f"/installs/{refused['id']}/remove-everywhere", data={"csrf_token": CSRF})
    assert refused["removed"] == []


def test_removing_is_only_for_a_refusal_of_that_kind(authed, refused):
    db.finish_install(refused["id"], "failed", "Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]")
    authed.post(f"/installs/{refused['id']}/remove-everywhere", data={"csrf_token": CSRF})
    assert refused["removed"] == []
    assert "Remove from every profile" not in authed.get(f"/installs/{refused['id']}").text


def test_removing_needs_csrf(authed, refused):
    r = authed.post(f"/installs/{refused['id']}/remove-everywhere", data={"csrf_token": "wrong"})
    assert r.status_code == 403 and refused["removed"] == []


# ---- Library: delete a version from its row ----

def test_apps_cards_offer_delete(authed):
    rid = db.create_repo("o", "r", "*.apk")
    apk_id = _stage(rid, "v2", "app.apk")
    page = authed.get("/apps").text
    row = page[page.index(f'id="repo-{rid}"'):]
    row = row[:row.index("app-card-more")]  # the card's face, not its details
    assert f'action="/staged/{apk_id}/delete-version"' in row


def test_delete_version_removes_every_file_of_that_release(authed):
    rid = db.create_repo("o", "r", "*.apk")
    a = _stage(rid, "v2", "app-arm64.apk")
    b = _stage(rid, "v2", "app-x86.apk")
    older = _stage(rid, "v1", "app.apk")
    paths = [db.get_staged_apk(i)["path"] for i in (a, b)]
    r = authed.post(f"/staged/{a}/delete-version", data={"csrf_token": CSRF}, follow_redirects=False)
    assert r.status_code == 303 and "2%20files" in r.headers["location"]
    assert db.get_staged_apk(a)["pruned_at"] and db.get_staged_apk(b)["pruned_at"]
    assert not db.get_staged_apk(older)["pruned_at"]
    assert not any(os.path.exists(p) for p in paths)


def test_delete_version_needs_csrf(authed):
    rid = db.create_repo("o", "r", "*.apk")
    a = _stage(rid, "v2", "app.apk")
    assert authed.post(f"/staged/{a}/delete-version", data={"csrf_token": "wrong"}).status_code == 403
    assert not db.get_staged_apk(a)["pruned_at"]


# ---- the top bar shows where you are ----

@pytest.mark.parametrize("path, section", [
    ("/status", "/status"), ("/installs", "/status"), ("/installs/7", "/status"),
    ("/repos/3/artifacts", "/apps"), ("/sources", "/sources"), ("/settings/github", "/settings"), ("/audit", "/settings"),
    ("/devices", "/devices"),
])
def test_nav_section(path, section):
    assert web.nav_section(path) == section


def test_builds_highlights_apps(authed):
    rid = db.create_repo("o", "r", "*.apk")
    page = authed.get(f"/repos/{rid}/artifacts").text
    nav = page[page.index('class="main-nav"'):page.index("</nav>")]
    assert '<a href="/apps" class="active">' in nav


# ---- every remove or delete asks first ----

DESTRUCTIVE = re.compile(r'<form[^>]*action="([^"]*(?:delete|remove|revoke|clear)[^"]*)"([^>]*)>')


@pytest.mark.parametrize("path", ["/apps", "/devices", "/settings/security",
                                  "/settings/notifications", "/settings/appearance", "/settings/github"])
def test_every_remove_or_delete_is_confirmed_in_a_dialog(authed, device, path):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    db.add_trusted_browser("b" * 64, "Firefox", "192.168.1.9", "2099-01-01T00:00:00+00:00")
    db.add_notify_target("ntfy://example/topic", "phone")
    db.save_colour("Mine", "#336699", "#993366")
    db.set_secret("github_token", "ghp_" + "a" * 36)
    if path == "/settings/security":  # trusted browsers are listed only with two-factor on
        import mfa
        import time
        mfa.pending_secret(create=True)
        assert mfa.enable(mfa._code_at(db.get_secret("mfa_pending_secret"), int(time.time() // mfa.STEP_SECONDS)))
    page = authed.get(path).text
    forms = DESTRUCTIVE.findall(page)
    assert forms, "the page should offer something to remove"
    for action, attrs in forms:
        assert 'class="modal-actions"' in attrs, f"{action} posts without a confirmation dialog"
    # No bare submit button posts a delete from inside another form either.
    assert "formaction=" not in page or not re.search(r'formaction="[^"]*(delete|remove)', page)


def test_the_profile_removal_is_confirmed_in_a_dialog(authed, refused):
    page = authed.get(f"/installs/{refused['id']}").text
    [(action, attrs)] = DESTRUCTIVE.findall(page)
    assert action.endswith("/remove-everywhere") and 'class="modal-actions"' in attrs
    assert f'href="#remove-everywhere-{refused["id"]}"' in page


# ---- Devices: where each one stands ----

def test_transport_states_reads_every_connection(monkeypatch):
    out = "List of devices attached\n10.0.0.5:4000\tdevice\n10.0.0.6:4001\toffline\n10.0.0.7:4002\tunauthorized\n"
    monkeypatch.setattr(adb_client, "_run", lambda *a, **k: _proc(out))
    assert adb_client.transport_states() == {"10.0.0.5:4000": "device", "10.0.0.6:4001": "offline",
                                              "10.0.0.7:4002": "unauthorized"}


@pytest.mark.parametrize("transports, badge, words", [
    ({ADDR: "device"}, "Connected", "ready for pushes"),
    ({ADDR: "offline"}, "Offline", "Unlock the"),
    ({ADDR: "unauthorized"}, "Key refused", "refused this server"),
    ({}, "Not connected", "press <strong>Find</strong>"),
    (None, "Connection unknown", "Couldn't reach the adb server"),
])
def test_a_device_card_says_where_it_stands(authed, device, monkeypatch, transports, badge, words):
    def states():
        if transports is None:
            raise adb_client.AdbError("down")
        return transports
    monkeypatch.setattr(adb_client, "transport_states", states)
    page = authed.get("/devices").text
    card = page[page.index(f'id="{web.dom_id("SER")}"'):]
    assert f">{badge}</span>" in card[:card.index("</summary>")] and words in card


def test_find_sits_next_to_reconnect(authed, device, monkeypatch):
    monkeypatch.setattr(adb_client, "transport_states", lambda: {})
    page = authed.get("/devices").text
    conn = page[page.index("connection-actions"):]
    conn = conn[:conn.index("</div>")]
    reconnect, find = conn.index(">Reconnect</button>"), conn.index(">Find</button>")
    assert conn.index('name="connect_addr"') < reconnect < find
    assert 'type="text"' not in conn[reconnect:find]  # no field between the two buttons


# ---- a debug build never goes over a signed install ----

def _debug_upload(signer="d" * 64, tag="dev"):
    return db.insert_staged_apk(None, tag, f"{tag}.apk", os.urandom(32).hex(), "com.example", signer, "/nonexistent",
                                version_code=3, version_name="3.0-dev", source="upload", is_debug=True)


def test_a_debug_build_is_refused_over_a_signed_install(device):
    db.upsert_device_package("SER", "com.example", True, version_code=2, version_name="2.0")
    with pytest.raises(pushes.PushRefused, match="debug build"):
        pushes.create_install(device, db.get_staged_apk(_debug_upload()))
    assert db.list_installs() == []


def test_a_debug_build_is_fine_when_the_app_isnt_installed(device):
    assert pushes.create_install(device, db.get_staged_apk(_debug_upload()))


def test_a_debug_build_may_replace_the_same_debug_build(device):
    first = db.get_staged_apk(_debug_upload(tag="dev1"))
    db.set_package_origin("SER", first, update_time=None)
    again = db.get_staged_apk(_debug_upload(tag="dev2"))
    assert pushes.create_install(device, again)


def test_a_debug_build_with_another_debug_key_is_refused(device):
    db.set_package_origin("SER", db.get_staged_apk(_debug_upload(signer="1" * 64, tag="dev1")), update_time=None)
    with pytest.raises(pushes.PushRefused):
        pushes.create_install(device, db.get_staged_apk(_debug_upload(signer="2" * 64, tag="dev2")))


def test_signed_builds_are_never_blocked_this_way(device):
    rid = db.create_repo("o", "r", "*.apk")
    db.upsert_device_package("SER", "com.example", True, version_code=1, version_name="1.0")
    assert pushes.create_install(device, db.get_staged_apk(_stage(rid, "v2", "app.apk")))


def test_the_push_route_refuses_it_with_the_reason(authed, device):
    db.upsert_device_package("SER", "com.example", True, version_code=2, version_name="2.0")
    up = _debug_upload()
    r = authed.post("/push", data={"csrf_token": CSRF, "apk_id": up, "device_serial": "SER", "back": "/apps"},
                    follow_redirects=False)
    assert "error=" in r.headers["location"] and "debug%20build" in r.headers["location"]
    assert db.list_installs() == []


@pytest.mark.parametrize("path", ["/apps", "/status"])
def test_the_pages_show_it_blocked_instead_of_a_push(authed, device, path):
    db.upsert_device_package("SER", "com.example", True, version_code=2, version_name="2.0")
    up = _debug_upload()
    page = authed.get(path).text
    assert "Push blocked" in page
    assert f'name="apk_id" value="{up}"' not in page


def test_a_failed_test_build_push_shows_only_on_its_own_card(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")
    up = db.insert_staged_apk(None, "dev", "dev.apk", os.urandom(32).hex(), "com.example", "d" * 64, "/nonexistent",
                              version_code=3, version_name="3.0-dev", source="upload")
    install = db.insert_install("SER", up, status="installing")
    db.finish_install(install, "failed", "Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]")
    page = authed.get("/apps").text
    repo_card = page[page.index(f'id="repo-{rid}"'):page.index('id="kind-upload"')]
    upload_card = page[page.index(f'id="upload-{up}"'):]
    assert "Push failed" not in repo_card and "Push failed" in upload_card
