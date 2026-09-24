"""Secrets in the database are sealed with a key derived from SECRET_KEY."""
import sqlite3

import pytest

import db
import github_client
import mfa
import notify
import poller
import secretbox
from conftest import CSRF


def _raw(sql, *args):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


@pytest.fixture
def other_key(monkeypatch):
    def switch():
        monkeypatch.setenv("SECRET_KEY", "a-different-secret-key-entirely")
    return switch


def test_seal_round_trips_and_never_repeats():
    a, b = secretbox.seal("ghp_x"), secretbox.seal("ghp_x")
    assert a != b and a.startswith("enc:v1:") and "ghp_x" not in a
    assert secretbox.unseal(a) == "ghp_x"


def test_unsealed_legacy_value_passes_through():
    assert secretbox.unseal("plain") == "plain"


def test_another_key_cannot_open_it(other_key):
    sealed = secretbox.seal("secret")
    other_key()
    with pytest.raises(secretbox.SecretUnreadable):
        secretbox.unseal(sealed)


def test_secrets_are_stored_sealed():
    db.set_secret("github_token", "ghp_" + "a" * 36)
    db.add_notify_target("ntfy://ntfy.sh/topic-with-token", None)
    mfa.pending_secret(create=True)
    for (value,) in _raw("SELECT value FROM meta WHERE key IN ('github_token', 'mfa_pending_secret')") + \
            _raw("SELECT url FROM notify_targets"):
        assert value.startswith("enc:v1:")
    assert db.get_secret("github_token") == "ghp_" + "a" * 36
    assert db.list_notify_targets()[0]["url"] == "ntfy://ntfy.sh/topic-with-token"


def test_startup_seals_secrets_saved_before_encryption():
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("INSERT INTO meta (key, value) VALUES ('mfa_secret', 'JBSWY3DPEHPK3PXP')")
    conn.execute("INSERT INTO notify_targets (url, label, created_at) VALUES ('json://h/p', NULL, 'x')")
    conn.commit()
    conn.close()
    db.init_db()
    db.init_db()  # idempotent: sealed values are left alone
    [(secret,)] = _raw("SELECT value FROM meta WHERE key = 'mfa_secret'")
    [(url,)] = _raw("SELECT url FROM notify_targets")
    assert secret.startswith("enc:v1:") and url.startswith("enc:v1:")
    assert db.get_secret("mfa_secret") == "JBSWY3DPEHPK3PXP"
    assert db.list_notify_targets()[0]["url"] == "json://h/p"


def test_a_duplicate_notification_url_is_still_refused():
    assert db.add_notify_target("json://h/p", None) is not None
    assert db.add_notify_target("json://h/p", "again") is None


def test_after_a_key_change_notifications_skip_the_unreadable_url(other_key, monkeypatch):
    db.add_notify_target("json://h/p", None)
    other_key()
    [target] = db.list_notify_targets()
    assert target["url"] is None
    sent = []
    monkeypatch.setattr(notify, "_deliver", lambda urls, title, body: sent.append(urls) or True)
    assert notify.send("staged", "t", "b") is False and sent == []
    assert notify.describe(None)["valid"] is False


def test_after_a_key_change_two_factor_fails_closed(other_key):
    mfa.pending_secret(create=True)
    code = mfa._code_at(db.get_secret("mfa_pending_secret"), int(__import__("time").time() // mfa.STEP_SECONDS))
    mfa.enable(code)
    other_key()
    assert mfa.enabled()  # still on: never silently dropped
    now_code = mfa._code_at(mfa.new_secret(), 0)  # any code at all
    assert mfa.check(now_code) is None and mfa.check(code) is None


def test_after_a_key_change_the_saved_token_falls_back_to_env(other_key, monkeypatch):
    db.set_secret("github_token", "ghp_" + "a" * 36)
    monkeypatch.setattr(poller, "GITHUB_TOKEN", "ghp_" + "e" * 36)
    other_key()
    assert poller.github_token() == "ghp_" + "e" * 36
    assert poller.token_source() == "unreadable"


# ---- Settings → Artifacts ----

GOOD = "github_pat_" + "A1" * 20


@pytest.fixture
def token_check(monkeypatch):
    state = {"error": None, "seen": [], "expires": None}

    async def check(token):
        state["seen"].append(token)
        if state["error"]:
            raise github_client.GithubError(state["error"])
        return github_client.TokenStatus(login="darthrater78", rate_limit=5000, expires_at=state["expires"])
    monkeypatch.setattr(github_client, "check_token", check)
    monkeypatch.setattr(poller, "GITHUB_TOKEN", None)
    return state


def _save(authed, token):
    return authed.post("/settings/github-token", data={"csrf_token": CSRF, "token": token}, follow_redirects=False)


def test_a_checked_token_is_saved_sealed_and_never_echoed(authed, token_check):
    r = _save(authed, f"  {GOOD}  ")
    assert "ok=" in r.headers["location"] and "darthrater78" in r.headers["location"]
    assert GOOD not in r.headers["location"]
    assert db.get_secret("github_token") == GOOD and poller.github_token() == GOOD
    page = authed.get("/settings/github").text
    assert GOOD not in page and "Saved here" in page
    [row] = [a for a in db.list_audit() if a["action"] == "github_token_set"]
    assert GOOD not in row["detail"]


@pytest.mark.parametrize("bad", ["hunter2", "ghp_short", "github_pat_" + "x" * 20 + " extra", "ghp_" + "a" * 36 + "\n"])
def test_something_that_isnt_a_token_is_refused_without_calling_github(authed, token_check, bad):
    r = _save(authed, bad + "!")
    assert "error=" in r.headers["location"]
    assert token_check["seen"] == [] and db.get_meta("github_token") is None


def test_a_token_github_rejects_is_not_saved(authed, token_check):
    token_check["error"] = "GitHub rejected that token: it's wrong, expired or revoked"
    r = _save(authed, GOOD)
    assert "Not%20saved" in r.headers["location"] and db.get_meta("github_token") is None


def test_the_saved_token_wins_over_env_and_can_be_removed(authed, token_check, monkeypatch):
    monkeypatch.setattr(poller, "GITHUB_TOKEN", "ghp_" + "e" * 36)
    _save(authed, GOOD)
    assert poller.github_token() == GOOD and poller.token_source() == "settings"
    r = authed.post("/settings/github-token/clear", data={"csrf_token": CSRF}, follow_redirects=False)
    assert ".env" in r.headers["location"] or "env" in r.headers["location"]
    assert poller.github_token() == "ghp_" + "e" * 36 and poller.token_source() == "env"


def test_testing_the_token(authed, token_check):
    r = authed.post("/settings/github-token/test", data={"csrf_token": CSRF}, follow_redirects=False)
    assert "No%20GitHub%20token" in r.headers["location"]
    _save(authed, GOOD)
    r = authed.post("/settings/github-token/test", data={"csrf_token": CSRF}, follow_redirects=False)
    assert "token%20works" in r.headers["location"]


def test_token_routes_need_csrf(authed, token_check):
    for path, data in (("/settings/github-token", {"token": GOOD}), ("/settings/github-token/clear", {}),
                       ("/settings/github-token/test", {}), ("/settings/artifacts", {})):
        r = authed.post(path, data={"csrf_token": "no", **data}, follow_redirects=False)
        assert r.status_code in (400, 403), path
    assert token_check["seen"] == [] and db.get_meta("github_token") is None


def test_artifact_filter_is_saved_and_validated(authed):
    r = authed.post("/settings/artifacts", data={"csrf_token": CSRF, "name_glob": "*apk*"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    assert db.get_meta("artifacts_hide_dockerbuild") == "0" and db.get_meta("artifacts_name_glob") == "*apk*"
    r = authed.post("/settings/artifacts", data={"csrf_token": CSRF, "name_glob": "<script>"}, follow_redirects=False)
    assert "error=" in r.headers["location"] and db.get_meta("artifacts_name_glob") == "*apk*"


def test_check_token_does_not_use_the_etag_cache(monkeypatch):
    import asyncio
    import httpx
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"login": "me"}, headers={"x-ratelimit-limit": "5000"})
    real = httpx.AsyncClient
    monkeypatch.setattr(github_client.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    github_client._etag_cache.clear()
    status = asyncio.run(github_client.check_token(GOOD))
    assert (status.login, status.rate_limit) == ("me", 5000)
    assert seen[0].headers["authorization"] == f"Bearer {GOOD}" and "if-none-match" not in seen[0].headers
    assert github_client._etag_cache == {}


# ---- Settings layout: overview and sub-pages ----

@pytest.mark.parametrize("path", ["/settings", "/settings/general", "/settings/security", "/settings/notifications",
                                  "/settings/github", "/settings/appearance"])
def test_every_settings_page_renders_under_settings_nav(authed, path):
    r = authed.get(path)
    assert r.status_code == 200
    assert f'href="{path}" aria-current="page"' in r.text
    assert 'href="/settings" class="active"' in r.text  # top bar keeps Settings lit


def test_overview_summarises_each_area(authed, token_check):
    page = authed.get("/settings").text
    for needle in ("2FA off", "None set up", "No token", 'href="/settings/github"', "Install history"):
        assert needle in page, needle
    _save(authed, GOOD)
    assert "Token saved" in authed.get("/settings").text


def test_token_template_link_asks_for_read_only_access(authed):
    db.create_repo("darthrater78", "android-heartrate", "*.apk", github_id=1, owner_id=2, owner_type="User")
    page = authed.get("/settings/github").text
    import html, re
    from urllib.parse import parse_qs, urlsplit
    url = html.unescape(re.search(r'href="(https://github.com/settings/personal-access-tokens/new\?[^"]+)"', page).group(1))
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert (q["contents"], q["actions"], q["metadata"]) == ("read", "read", "read")
    assert q["target_name"] == "darthrater78" and q["expires_in"] == "90" and len(q["name"]) <= 40
    assert not {"administration", "workflows", "pull_requests"} & set(q) and "write" not in q.values()
    assert 'target="_blank" rel="noopener noreferrer"' in page


def test_expiry_is_recorded_shown_and_warned_once(authed, token_check, monkeypatch):
    import asyncio
    from datetime import datetime, timedelta, timezone
    soon = (datetime.now(timezone.utc) + timedelta(days=3, hours=1)).isoformat()
    token_check["expires"] = soon
    _save(authed, GOOD)
    assert db.get_meta("github_token_expires") == soon
    assert "3 days left" in authed.get("/settings").text
    assert "Expires " in authed.get("/settings/github").text
    sent = []
    monkeypatch.setattr(notify, "send", lambda event, title, body: sent.append(event))
    asyncio.run(poller._warn_token_expiry())
    asyncio.run(poller._warn_token_expiry())
    assert sent == ["token_expiring"]


def test_a_distant_expiry_is_not_warned(authed, token_check, monkeypatch):
    import asyncio
    from datetime import datetime, timedelta, timezone
    token_check["expires"] = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
    _save(authed, GOOD)
    sent = []
    monkeypatch.setattr(notify, "send", lambda event, title, body: sent.append(event))
    asyncio.run(poller._warn_token_expiry())
    assert sent == []


def test_expiry_header_is_parsed():
    assert github_client._parse_expiry("2026-12-23 10:00:00 UTC") == "2026-12-23T10:00:00+00:00"
    assert github_client._parse_expiry(None) is None and github_client._parse_expiry("soon") is None


def test_access_check_reports_each_repo(authed, token_check, monkeypatch):
    _save(authed, GOOD)
    db.create_repo("o", "ok", "*.apk", github_id=1, owner_id=2, owner_type="User")
    db.create_repo("o", "noart", "*.apk", github_id=3, owner_id=2, owner_type="User")
    db.create_repo("o", "gone", "*.apk", github_id=4, owner_id=2, owner_type="User")

    async def info(owner, repo, token):
        if repo == "gone":
            raise github_client.GithubError("Repo or release not found (a private repo needs a GitHub token)")
        return None

    async def arts(owner, repo, github_id, token):
        return [github_client.Artifact(9, "a", 1, "", "main", "abc", 1)]

    async def can(owner, repo, artifact_id, token):
        return repo == "ok"
    monkeypatch.setattr(github_client, "get_repo_info", info)
    monkeypatch.setattr(github_client, "list_artifacts", arts)
    monkeypatch.setattr(github_client, "can_download_artifact", can)
    page = authed.get("/settings/github").text
    row = lambda name: page[page.index(f">o/{name}<"):][:900]
    assert row("ok").count("badge-ok") == 2
    assert "badge-error" in row("noart") and "Actions: read" in row("noart")
    assert "No access" in row("gone") and "not found" in row("gone")


def test_artifact_download_probe_does_not_follow_the_redirect(monkeypatch):
    import asyncio
    import httpx
    seen = []

    def handle(request):
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://x.blob.core.windows.net/a"})
    real = httpx.AsyncClient
    monkeypatch.setattr(github_client.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    assert asyncio.run(github_client.can_download_artifact("o", "r", 5, GOOD)) is True
    assert seen == ["api.github.com"]  # nothing fetched from storage


def test_notification_add_forms_open_when_a_draft_came_back(authed, monkeypatch):
    monkeypatch.setattr(notify, "test", lambda target: (False, "nope"))
    page = authed.post("/settings/notify/try", data={"csrf_token": CSRF, "url": "json://h/p", "label": ""}).text
    assert page.count('<details class="add-details" open>') == 1 and 'value="json://h/p"' in page
    assert '<details class="add-details" open>' not in authed.get("/settings/notifications").text
