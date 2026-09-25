import asyncio
import logging
import sys
import types

import httpx
import pytest

import db
import github_client
import main
import notify
import poller
import pushes
from conftest import CSRF


# ---- notifications ----

class FakeApprise:
    instances: list["FakeApprise"] = []

    def __init__(self):
        self.urls, self.sent = [], []
        FakeApprise.instances.append(self)

    def add(self, url):
        if url.startswith("bad"):
            return False
        self.urls.append(url)
        return True

    def notify(self, title, body):
        self.sent.append((title, body))
        return True


@pytest.fixture
def fake_apprise(monkeypatch):
    FakeApprise.instances = []
    monkeypatch.setitem(sys.modules, "apprise", types.SimpleNamespace(Apprise=FakeApprise))
    return FakeApprise


def test_notify_is_off_without_urls(monkeypatch, fake_apprise):
    monkeypatch.delenv("APPRISE_URLS", raising=False)
    assert notify.send("staged", "t", "b") is False
    assert fake_apprise.instances == []


def test_notify_sends_to_every_url(monkeypatch, fake_apprise):
    monkeypatch.setenv("APPRISE_URLS", "ntfy://ntfy.sh/topic, json://host/hook")
    assert notify.send("staged", "o/r 1.0 ready", "body")
    ap = fake_apprise.instances[0]
    assert ap.urls == ["ntfy://ntfy.sh/topic", "json://host/hook"]
    assert ap.sent == [("ADB Server: o/r 1.0 ready", "body")]


def test_notify_respects_event_filter(monkeypatch, fake_apprise):
    monkeypatch.setenv("APPRISE_URLS", "ntfy://ntfy.sh/topic")
    monkeypatch.setenv("NOTIFY_EVENTS", "install_failed")
    assert notify.send("staged", "t", "b") is False
    assert notify.send("install_failed", "t", "b") is True


def test_invalid_url_is_not_logged(monkeypatch, fake_apprise, caplog):
    monkeypatch.setenv("APPRISE_URLS", "bad://secret-token@host ntfy://ntfy.sh/t")
    with caplog.at_level(logging.WARNING):
        notify.send("staged", "t", "b")
    assert "secret-token" not in caplog.text


def test_notify_failure_is_swallowed(monkeypatch):
    class Boom:
        def __init__(self):
            raise RuntimeError("network down")
    monkeypatch.setitem(sys.modules, "apprise", types.SimpleNamespace(Apprise=Boom))
    monkeypatch.setenv("APPRISE_URLS", "ntfy://ntfy.sh/t")
    assert notify.send("staged", "t", "b") is False


# ---- auto-update ----

def _staged(rid, tag="v2", abis="", code=2):
    return db.insert_staged_apk(rid, tag, f"{tag}.apk", "0" * 64, "com.example", "a" * 64, f"/x/{tag}.apk",
                                version_code=code, version_name=tag, abis=abis)


def _device(serial, trusted=True, abis=("arm64-v8a",)):
    db.upsert_paired_device(serial, "192.168.1.50:37000")
    db.set_device_trusted(serial, trusted)
    db.set_device_abis(serial, list(abis))


def test_auto_update_queues_only_followers_that_need_it():
    rid = db.create_repo("o", "r", "*.apk")
    apk = _staged(rid)
    for s in ("NEEDS", "CURRENT", "NOTFOLLOWING"):
        _device(s)
    _device("UNTRUSTED", trusted=False)
    for s in ("NEEDS", "CURRENT", "UNTRUSTED"):
        db.set_follow(s, rid, True)
    db.upsert_device_package("CURRENT", "com.example", True, 2, "v2")
    db.upsert_device_package("NEEDS", "com.example", True, 1, "v1")
    queued = pushes.auto_push_targets(rid)
    assert [(d["serial"], a["id"]) for _, d, a in queued] == [("NEEDS", apk)]
    assert db.list_installs()[0]["status"] == "pending"


def test_auto_update_skips_incompatible_cpu():
    rid = db.create_repo("o", "r", "*.apk")
    _staged(rid, abis="x86_64")
    _device("ARM")
    db.set_follow("ARM", rid, True)
    assert pushes.auto_push_targets(rid) == []


def test_follow_requires_trusted_device(authed):
    rid = db.create_repo("o", "r", "*.apk")
    _device("UNTRUSTED", trusted=False)
    r = authed.post("/follow", data={"csrf_token": CSRF, "device_serial": "UNTRUSTED", "repo_id": rid, "follow": "1"},
                    follow_redirects=False)
    assert "trusted" in r.headers["location"]
    assert db.follows_set() == set()


def test_follow_toggle_is_audited(authed):
    rid = db.create_repo("o", "r", "*.apk")
    _device("SER")
    authed.post("/follow", data={"csrf_token": CSRF, "device_serial": "SER", "repo_id": rid, "follow": "1"})
    assert ("SER", rid) in db.follows_set()
    assert db.list_audit()[0]["action"] == "auto_update_on"


# ---- background check-now / locking ----

def test_check_now_responds_before_checking(authed, monkeypatch):
    rid = db.create_repo("o", "r", "*.apk")
    started = []

    async def fake_check(row, restage=False):
        started.append(row["id"])
    monkeypatch.setattr(poller, "check_repo", fake_check)
    r = authed.post(f"/repos/{rid}/check-now", data={"csrf_token": CSRF}, follow_redirects=False)
    assert f"checking={rid}" in r.headers["location"]
    assert started == [rid]  # TestClient runs background tasks before returning


def test_overlapping_checks_of_one_repo_are_collapsed(monkeypatch):
    rid = db.create_repo("o", "r", "*.apk")
    calls = []

    async def slow(row, restage=False):
        calls.append(row["id"])
        await asyncio.sleep(0.05)
    monkeypatch.setattr(poller, "_check_repo", slow)

    async def both():
        row = db.get_repo(rid)
        await asyncio.gather(poller.check_repo(row), poller.check_repo(row))
    poller._repo_locks.clear()
    asyncio.run(both())
    assert calls == [rid]


# ---- GitHub client: pre-releases and ETags ----

class FakeGitHub:
    def __init__(self, monkeypatch):
        self.requests = []
        self.releases = [{"tag_name": "v3-beta", "prerelease": True, "draft": False},
                         {"tag_name": "v2", "prerelease": False, "draft": False}]
        transport = httpx.MockTransport(self.handle)
        real = httpx.AsyncClient
        monkeypatch.setattr(github_client.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
        github_client._etag_cache.clear()

    def handle(self, request):
        self.requests.append(request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json=self.releases[1], headers={"etag": '"v1"'})
        return httpx.Response(200, json=self.releases, headers={"etag": '"list"'})


def test_prereleases_are_opt_in(monkeypatch):
    gh = FakeGitHub(monkeypatch)
    stable = asyncio.run(github_client.get_latest_release("o", "r", None))
    pre = asyncio.run(github_client.get_latest_release("o", "r", None, include_prereleases=True))
    assert (stable["tag_name"], pre["tag_name"]) == ("v2", "v3-beta")


def test_prerelease_listing_skips_drafts(monkeypatch):
    gh = FakeGitHub(monkeypatch)
    gh.releases.insert(0, {"tag_name": "v4-draft", "draft": True})
    assert asyncio.run(github_client.get_latest_release("o", "r", None, include_prereleases=True))["tag_name"] == "v3-beta"


def test_unchanged_release_uses_conditional_request(monkeypatch):
    gh = FakeGitHub(monkeypatch)
    first = asyncio.run(github_client.get_latest_release("o", "r", None))
    second = asyncio.run(github_client.get_latest_release("o", "r", None))
    assert first == second
    assert gh.requests[1].headers["if-none-match"] == '"v1"'


def test_etag_cache_is_bounded(monkeypatch):
    FakeGitHub(monkeypatch)
    monkeypatch.setattr(github_client, "ETAG_CACHE_MAX", 3)
    for i in range(6):
        asyncio.run(github_client.get_latest_release("o", f"r{i}", None))
    assert len(github_client._etag_cache) == 3


# ---- health and audit ----

def test_healthz_is_unauthenticated(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_healthz_allows_loopback_host(client):
    r = client.get("http://127.0.0.1/healthz")
    assert r.status_code == 200


def test_login_attempts_are_audited_without_password(client):
    client.post("/login", data={"username": "operator", "password": "wrong-password-xyz"})
    client.post("/login", data={"username": "operator", "password": "correct-horse-battery"})
    entries = db.list_audit()
    assert [e["action"] for e in entries] == ["login", "login_failed"]
    assert not any("wrong-password-xyz" in e["detail"] or "correct-horse" in e["detail"] for e in entries)


def test_trust_change_is_audited_and_shown(authed):
    _device("SER", trusted=False)
    authed.post("/devices/SER/trust", data={"csrf_token": CSRF, "trusted": "1"})
    assert db.list_audit()[0]["action"] == "device_trust"
    assert "device_trust" in authed.get("/audit").text


def test_audit_log_is_trimmed(monkeypatch):
    monkeypatch.setattr(db, "AUDIT_KEEP", 50)
    for i in range(300):
        db.insert_audit("x", str(i), None)
    assert len(db.list_audit(limit=1000)) <= 150
