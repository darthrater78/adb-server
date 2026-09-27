"""3.8.0 navigation: every action comes back to the card (and row) it was
taken on, Status holds everything a device needs, and each step leads on to
the next instead of to another page."""
import os

import pytest

import adb_client
import db
import discovery
import github_client
import poller
import pushes
import staging
import web
from conftest import CSRF

DOM = web.dom_id("SER")


def _stage(repo_id, tag, filename="app.apk", package="com.example", version_code=2, version_name="2.0", abis=""):
    os.makedirs(staging.repo_dir(repo_id), exist_ok=True)
    path = os.path.join(staging.repo_dir(repo_id), f"{tag}-{filename}")
    with open(path, "wb") as f:
        f.write(b"PK")
    return db.insert_staged_apk(repo_id, tag, filename, os.urandom(32).hex(), package, "a" * 64, path,
                                version_code=version_code, version_name=version_name, abis=abis)


def _upload(label="kiosk build", package="com.example.kiosk", artifact=None):
    path = os.path.join(staging.STAGING_ROOT, "uploads", f"{label}.apk")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()
    return db.insert_staged_apk(None, label, f"{label}.apk", os.urandom(32).hex(), package, "d" * 64, path,
                                version_code=3, version_name="3.0", source="upload", artifact=artifact)


@pytest.fixture
def device():
    db.upsert_paired_device("SER", "192.168.1.50:37000")
    db.set_device_trusted("SER", True)
    db.set_device_nickname("SER", "Pixel")


@pytest.fixture
def queued(monkeypatch):
    ran = []
    monkeypatch.setattr(pushes, "run_push", lambda install_id, device, apk: ran.append(apk["id"]))
    return ran


# ---- landing where you were ----

def test_redirect_opens_a_card_and_scrolls_to_it():
    assert web.redirect("/devices", card="dev-1", ok="Hi").headers["location"] == "/devices?ok=Hi&open=dev-1#dev-1"
    # A row inside the card keeps its own fragment.
    assert web.redirect("/status#app-2-dev-1", card="dev-1").headers["location"] == "/status?open=dev-1#app-2-dev-1"


def test_device_ids_are_stable_and_html_safe():
    serial = "adb-R5CX12AB34F-a1b2c3._adb-tls-connect._tcp"
    assert web.dom_id(serial) == web.dom_id(serial) != web.dom_id("other")
    assert web.dom_id(serial).startswith("dev-") and web.dom_id(serial)[4:].isalnum()


def test_an_opened_card_holds_its_message(authed, device):
    page = authed.get(f"/devices?open={DOM}&ok=Name%20saved").text
    card = page[page.index(f'id="{DOM}"'):]
    assert f'id="{DOM}" open>' in page and '<p class="flash flash-ok">Name saved</p>' in card
    assert page.count("flash-ok") == 1  # not also at the top


@pytest.mark.parametrize("wanted", ["dev-nope", "<script>", "DEV-X", ""])
def test_an_unknown_card_leaves_the_message_at_the_top(authed, device, wanted):
    page = authed.get("/devices", params={"open": wanted, "ok": "Done"}).text
    main = page[page.index("<main>"):]
    assert main.index('<p class="flash flash-ok">Done</p>') < main.index("<h1>")
    assert "<script>" not in page


def test_device_actions_come_back_to_the_card(authed, device, monkeypatch):
    r = authed.post("/devices/SER/nickname", data={"csrf_token": CSRF, "nickname": "Kitchen"}, follow_redirects=False)
    assert r.headers["location"] == f"/devices?ok=Name%20saved&open={DOM}#{DOM}"
    monkeypatch.setattr(discovery, "ensure_connected", lambda d, **k: ("SER", "192.168.1.50:40000"))
    monkeypatch.setattr(pushes, "refresh_abis", lambda s, a: "arm64-v8a")
    r = authed.post("/devices/SER/find", data={"csrf_token": CSRF}, follow_redirects=False)
    assert r.headers["location"].startswith("/devices?ok=Found") and r.headers["location"].endswith(f"#{DOM}")


def test_find_from_status_goes_back_to_status(authed, device, monkeypatch):
    page = authed.get("/status").text
    card = page[page.index(f'id="{DOM}"'):]
    assert 'action="/devices/SER/find"' in card and 'name="back" value="/status"' in card

    def unreachable(d, **k):
        raise adb_client.AdbError("No answer on 192.168.1.50")

    monkeypatch.setattr(discovery, "ensure_connected", unreachable)
    r = authed.post("/devices/SER/find", data={"csrf_token": CSRF, "back": "/status"}, follow_redirects=False)
    assert r.headers["location"] == f"/status?error=No%20answer%20on%20192.168.1.50&open={DOM}#{DOM}"
    page = authed.get(r.headers["location"]).text
    assert "No answer on 192.168.1.50" in page[page.index(f'id="{DOM}"'):]


@pytest.mark.parametrize("back", ["https://evil.example/", "/settings", ""])
def test_find_only_goes_back_to_known_pages(authed, device, monkeypatch, back):
    monkeypatch.setattr(discovery, "ensure_connected", lambda d, **k: ("SER", "192.168.1.50:40000"))
    monkeypatch.setattr(pushes, "refresh_abis", lambda s, a: "arm64-v8a")
    r = authed.post("/devices/SER/find", data={"csrf_token": CSRF, "back": back}, follow_redirects=False)
    assert r.headers["location"].startswith("/devices?")


def test_an_untrusted_device_can_be_trusted_on_status(authed):
    db.upsert_paired_device("SER", "192.168.1.50:37000")
    page = authed.get("/status").text
    card = page[page.index(f'id="{DOM}" open>'):]
    assert 'action="/devices/SER/trust"' in card and 'name="back" value="/status"' in card
    r = authed.post("/devices/SER/trust", data={"csrf_token": CSRF, "trusted": "1", "back": "/status"},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/status?ok=Trusted") and db.get_device("SER")["trusted"] == 1


def test_refresh_opens_the_card_of_a_phone_it_couldnt_reach(authed, device, monkeypatch):
    def unreachable(d, **k):
        raise adb_client.AdbError("offline")

    monkeypatch.setattr(discovery, "ensure_connected", unreachable)
    r = authed.post("/status/refresh", data={"csrf_token": CSRF}, follow_redirects=False)
    assert r.headers["location"].startswith("/status?error=Not%20reachable%3A%20Pixel")
    assert r.headers["location"].endswith(f"open={DOM}#{DOM}")


# ---- auto-update ----

def test_auto_update_comes_back_to_its_row(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2")
    r = authed.post("/follow", data={"csrf_token": CSRF, "device_serial": "SER", "repo_id": rid, "follow": "1"},
                    follow_redirects=False)
    assert r.headers["location"] == f"/status?ok=Auto-update%20on&open={DOM}#app-{rid}-{DOM}"


def test_auto_update_all_covers_the_apps_the_device_has(authed, device):
    ids = []
    for n in range(3):
        rid = db.create_repo("o", f"r{n}", "*.apk")
        _stage(rid, "v2", package=f"com.r{n}")
        ids.append(rid)
    db.upsert_device_package("SER", "com.r0", True, version_code=2, version_name="2.0")
    db.upsert_device_package("SER", "com.r1", True, version_code=1, version_name="1.0")
    page = authed.get("/status").text
    assert 'action="/follow-all"' in page and ">\n          Auto-update all\n" in page
    r = authed.post("/follow-all", data={"csrf_token": CSRF, "device_serial": "SER", "follow": "1"},
                    follow_redirects=False)
    assert "Auto-update%20on%20for%202%20apps" in r.headers["location"] and r.headers["location"].endswith(f"#{DOM}")
    assert db.follows_set() == {("SER", ids[0]), ("SER", ids[1])}  # not the app it doesn't have
    assert "Auto-update all: on" in authed.get("/status").text
    authed.post("/follow-all", data={"csrf_token": CSRF, "device_serial": "SER", "follow": "0"})
    assert db.follows_set() == set()


def test_auto_update_all_refuses_an_untrusted_device(authed):
    db.upsert_paired_device("SER", "192.168.1.50:37000")
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2")
    db.upsert_device_package("SER", "com.example", True, version_code=1, version_name="1.0")
    r = authed.post("/follow-all", data={"csrf_token": CSRF, "device_serial": "SER", "follow": "1"},
                    follow_redirects=False)
    assert "error=Only%20trusted" in r.headers["location"] and db.follows_set() == set()


def test_auto_update_all_needs_csrf(authed, device):
    r = authed.post("/follow-all", data={"csrf_token": "wrong", "device_serial": "SER", "follow": "1"},
                    follow_redirects=False)
    assert r.status_code in (400, 403)


# ---- Status holds test builds and uploads too ----

def test_status_offers_test_builds_and_uploads_per_device(authed, device, queued):
    up = _upload()
    art = _upload("app-debug main@abc1234", package="com.example.app",
                  artifact={"repo": "o/r", "run_id": 5, "branch": "main", "sha": "abc1234def", "subject": "",
                            "repo_id": None, "siblings": "[]"})
    page = authed.get("/status").text
    row = page[page.index(f'id="upload-{up}-{DOM}"'):]
    row = row[:row.index('<div class="row-follow">')]
    assert "kiosk build" in row and "Not installed" in row and 'action="/push"' in row
    assert f'name="apk_id" value="{up}"' in row and 'name="back" value="/status"' in row
    artifact = page[page.index(f'id="upload-{art}-{DOM}"'):]
    assert "o/r" in artifact[:600] and "main @ abc1234" in artifact[:600] and "Test build" in artifact[:1500]
    r = authed.post("/push", data={"csrf_token": CSRF, "device_serial": "SER", "apk_id": up, "back": "/status"},
                    follow_redirects=False)
    assert r.headers["location"] == f"/status?open={DOM}#upload-{up}-{DOM}" and queued == [up]


def test_an_installed_upload_is_its_row_not_an_other_app(authed, device):
    up = _upload()
    db.upsert_device_package("SER", "com.example.kiosk", True, version_code=3, version_name="3.0")
    db.upsert_device_package("SER", "com.elsewhere", True, version_code=1, version_name="1.0")
    page = authed.get("/status").text
    row = page[page.index(f'id="upload-{up}-{DOM}"'):]
    row = row[:row.index('<div class="row-follow">')]
    assert "3.0" in row and "installed</span>" in row and "Push" in row  # the same build: a re-push, not an install
    assert "com.example.kiosk" not in page  # no second, "other app" row for it
    assert page.count("Nothing staged for it") == 1 and "com.elsewhere" in page


def test_no_apps_yet_points_at_sources(authed, device):
    assert 'No apps yet. <a href="/sources">Add a source</a>' in authed.get("/status").text


# ---- Library ----

def test_library_shows_a_push_running_on_its_row(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    apk = _stage(rid, "v2")
    db.insert_install("SER", apk, status="installing")
    page = authed.get("/library").text
    row = page[page.index(f'id="app-{rid}"'):]
    row = row[:row.index("app-card-more")]
    assert "Installing 2.0…" in row and 'action="/push-latest"' not in row
    assert '<meta http-equiv="refresh" content="2">' in page


def test_deleting_a_file_comes_back_to_its_kind(authed, device):
    up = _upload()
    r = authed.post(f"/staged/{up}/delete", data={"csrf_token": CSRF}, follow_redirects=False)
    assert r.headers["location"] == "/library?ok=Staged%20file%20deleted&open=kind-upload#kind-upload"


# ---- Sources ----

@pytest.fixture
def lookup(monkeypatch):
    info = github_client.RepoInfo(id=1, owner="o", repo="r", owner_id=10, owner_type="User",
                                  created_at="2020-01-01T00:00:00Z", fork=False, archived=False, stars=5,
                                  description="An app")

    async def repo_info(owner, repo, token):
        return info

    async def evidence(info, glob):
        return "release", ""

    import routes_sources
    monkeypatch.setattr(github_client, "get_repo_info", repo_info)
    monkeypatch.setattr(routes_sources, "_apk_evidence", evidence)
    checked = []

    async def check(repo_row, restage=False):
        checked.append((repo_row["id"], restage))

    monkeypatch.setattr(poller, "check_repo", check)
    return checked


def test_review_offers_trusted_devices_to_install_on(authed, device, lookup):
    db.upsert_paired_device("NEW", "192.168.1.52:37000")
    page = authed.post("/repos", data={"csrf_token": CSRF, "repo_url": "o/r"}).text
    pick = page[page.index('class="follow-pick"'):page.index("</fieldset>")]
    assert 'name="follow" value="SER"' in pick and "NEW" not in pick


def test_watching_a_repo_checks_it_now_and_follows_the_ticked_devices(authed, device, lookup):
    db.upsert_paired_device("NEW", "192.168.1.52:37000")  # untrusted: ignored
    r = authed.post("/repos/confirm", data={"csrf_token": CSRF, "owner": "o", "repo": "r", "github_id": "1",
                                            "follow": ["SER", "NEW", "GHOST"]}, follow_redirects=False)
    [repo] = db.list_repos()
    assert db.follows_set() == {("SER", repo["id"])}
    assert lookup == [(repo["id"], True)]  # its first check, right away
    assert r.headers["location"].startswith(f"/sources?checking={repo['id']}&since=")
    assert r.headers["location"].endswith(f"&open=repo-{repo['id']}#repo-{repo['id']}")


def test_a_check_waits_on_the_repos_card(authed, device):
    rid = db.create_repo("o", "r", "*.apk")
    page = authed.get(f"/sources?checking={rid}&since={db.now()}&open=repo-{rid}").text
    assert f'url=/sources?checking={rid}&amp;since=' in page and f'open=repo-{rid}#repo-{rid}"' in page
    card = page[page.index(f'id="repo-{rid}" open>'):]
    assert "Checking o/r…" in card


def test_check_now_and_prereleases_come_back_to_the_card(authed, lookup):
    rid = db.create_repo("o", "r", "*.apk")
    r = authed.post(f"/repos/{rid}/check-now", data={"csrf_token": CSRF}, follow_redirects=False)
    assert r.headers["location"].endswith(f"&open=repo-{rid}#repo-{rid}")
    r = authed.post(f"/repos/{rid}/prereleases", data={"csrf_token": CSRF, "include": "1"}, follow_redirects=False)
    assert r.headers["location"] == f"/sources?ok=Pre-releases%20included&open=repo-{rid}#repo-{rid}"


# ---- Builds: stage, or stage and install ----

@pytest.fixture
def past_release(monkeypatch):
    """Stages two CPU builds of v1, like poller.stage_past_release would."""
    rid = db.create_repo("o", "r", "*.apk")
    _stage(rid, "v2", "app.apk")

    async def stage(repo_id, release_id):
        _stage(repo_id, "v1", "arm64.apk", version_name="1.0", abis="arm64-v8a")
        _stage(repo_id, "v1", "x86.apk", version_name="1.0", abis="x86_64")
        return True, "Staged v1 (2 APKs)"

    monkeypatch.setattr(poller, "stage_past_release", stage)
    return rid


def test_staging_a_release_lands_on_it_in_the_library(authed, past_release):
    r = authed.post(f"/repos/{past_release}/releases/9/stage", data={"csrf_token": CSRF}, follow_redirects=False)
    assert r.headers["location"] == \
        f"/library?ok=Staged%20v1%20%282%20APKs%29&open=kind-release#app-{past_release}"


def test_stage_and_install_a_release_pushes_the_build_for_the_cpu(authed, device, queued, past_release):
    with db.get_conn() as conn:
        conn.execute("UPDATE devices SET abis = 'x86_64' WHERE serial = 'SER'")
    r = authed.post(f"/repos/{past_release}/releases/9/stage", data={"csrf_token": CSRF, "push_to": "SER"},
                    follow_redirects=False)
    [x86] = [a for a in db.list_staged_apks(past_release) if a["filename"] == "x86.apk"]
    assert queued == [x86["id"]]
    assert r.headers["location"] == f"/status?ok=Staged%20v1%20%282%20APKs%29&open={DOM}#app-{past_release}-{DOM}"


def test_stage_and_install_needs_a_known_device(authed, past_release):
    r = authed.post(f"/repos/{past_release}/releases/9/stage", data={"csrf_token": CSRF, "push_to": "GHOST"},
                    follow_redirects=False)
    assert r.status_code == 404 and len(db.list_staged_apks(past_release)) == 1

