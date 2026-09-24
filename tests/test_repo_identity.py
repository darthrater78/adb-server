"""Watched repos are the real GitHub source: reviewed before they're watched,
pinned by GitHub ID, and release assets accepted only from the owner or a
workflow."""
import asyncio

import httpx
import pytest

import db
import github_client
import main
import notify
import poller
from conftest import CSRF
from test_poller import FakeUpstream, _poll


def _info(**kw):
    base = dict(id=1, owner="o", repo="r", owner_id=10, owner_type="User",
                created_at="2020-01-01T00:00:00Z", fork=False, archived=False, stars=5, description="An app")
    base.update(kw)
    return github_client.RepoInfo(**base)


@pytest.fixture
def upstream(monkeypatch):
    return FakeUpstream(monkeypatch)


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(notify, "send", lambda event, title, body: messages.append((event, title)))
    return messages


# ---- GitHub client ----

def test_repo_info_uses_githubs_canonical_name():
    info = github_client._parse_repo_info({
        "id": 7, "full_name": "OctoCat/Hello-World", "owner": {"id": 3, "type": "User"},
        "created_at": "2011-01-26T19:01:12Z", "fork": False, "archived": False, "stargazers_count": 9,
    })
    assert (info.id, info.owner, info.repo, info.owner_id) == (7, "OctoCat", "Hello-World", 3)


@pytest.mark.parametrize("data", [{}, {"id": "x", "full_name": "o/r", "owner": {"id": 1, "type": "User"}},
                                  {"id": 1, "full_name": "o/r"}, {"id": 1, "full_name": "../r", "owner": {"id": 1, "type": "User"}}])
def test_malformed_repo_info_is_refused(data):
    with pytest.raises(github_client.GithubError):
        github_client._parse_repo_info(data)


def test_renamed_repo_is_not_followed(monkeypatch):
    real = httpx.AsyncClient
    transport = httpx.MockTransport(lambda req: httpx.Response(301, headers={"location": "https://api.github.com/repositories/1"}))
    monkeypatch.setattr(github_client.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    github_client._etag_cache.clear()
    with pytest.raises(github_client.GithubError, match="moved or been renamed"):
        asyncio.run(github_client.get_repo_info("o", "r", None))


@pytest.mark.parametrize("uploader,owner_type,ok", [
    ({"login": "o"}, "User", True),
    ({"login": "O"}, "User", True),  # GitHub logins are case-insensitive
    ({"login": "github-actions[bot]"}, "User", True),
    ({"login": "github-actions[bot]"}, "Organization", True),
    ({"login": "someone-else"}, "User", False),
    ({"login": "o"}, "Organization", False),  # an org's own name never uploads; members need a workflow
    ({"login": "member"}, "Organization", False),
    (None, "User", False),
    ({}, "User", False),
    ("o", "User", False),
])
def test_uploader_rule(uploader, owner_type, ok):
    assert github_client.uploader_allowed({"uploader": uploader}, "o", owner_type) is ok


# ---- poller ----

def test_a_repo_added_before_pinning_is_pinned_on_first_poll(upstream):
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    repo = db.get_repo(rid)
    assert (repo["github_id"], repo["owner_id"], repo["owner_type"]) == (1, 10, "User")
    assert repo["last_tag"] == "v1"


def test_a_different_repo_under_the_pinned_name_is_refused(upstream, sent):
    rid = db.create_repo("o", "r", "*.apk", github_id=1, owner_id=10, owner_type="User")
    upstream.info = _info(id=999)
    _poll(rid)
    _poll(rid)
    repo = db.get_repo(rid)
    assert "different repo" in repo["last_error"] and repo["last_tag"] is None
    assert upstream.downloads == 0 and db.list_staged_apks() == []
    assert sent == [("rejected", "o/r identity changed")]  # once, not every poll
    assert repo["github_id"] == 1  # never re-pinned to the impostor


def test_an_unchanged_repo_costs_no_identity_lookup(upstream):
    rid = db.create_repo("o", "r", "*.apk", github_id=1, owner_id=10, owner_type="User")
    _poll(rid)
    assert upstream.repo_lookups == 1  # the new release was checked
    _poll(rid)
    _poll(rid)
    assert upstream.repo_lookups == 1  # nothing new to stage: no extra request


def test_a_transferred_repo_is_refused(upstream):
    rid = db.create_repo("o", "r", "*.apk", github_id=1, owner_id=10, owner_type="User")
    upstream.info = _info(owner_id=11)
    _poll(rid)
    assert "changed owner" in db.get_repo(rid)["last_error"]
    assert upstream.downloads == 0


def test_a_failed_lookup_stops_the_poll(upstream, monkeypatch):
    async def down(owner, repo, token):
        raise github_client.GithubError("Could not reach GitHub: ConnectError")
    monkeypatch.setattr(github_client, "get_repo_info", down)
    rid = db.create_repo("o", "r", "*.apk", github_id=1, owner_id=10, owner_type="User")
    _poll(rid)
    assert "Could not reach" in db.get_repo(rid)["last_error"]
    assert upstream.downloads == 0


def test_an_asset_from_someone_else_is_not_downloaded(upstream, sent):
    rid = db.create_repo("o", "r", "*.apk", github_id=1, owner_id=10, owner_type="User")
    upstream.uploader = "mallory"
    _poll(rid)
    repo = db.get_repo(rid)
    assert "uploaded by mallory" in repo["last_error"] and repo["rejected_tag"] == "v1"
    assert upstream.downloads == 0 and db.list_staged_apks() == []
    assert sent and sent[0][0] == "rejected"


@pytest.mark.parametrize("uploader,staged", [("member", False), ("github-actions[bot]", True)])
def test_organization_assets_must_come_from_a_workflow(upstream, uploader, staged):
    upstream.info = _info(owner_type="Organization")
    upstream.uploader = uploader
    rid = db.create_repo("o", "r", "*.apk", github_id=1, owner_id=10, owner_type="Organization")
    _poll(rid)
    assert bool(db.list_staged_apks()) is staged


# ---- adding a repo: review, then confirm ----

@pytest.fixture
def lookup(monkeypatch):
    state = {"info": _info(), "release": True, "calls": 0}

    async def repo_info(owner, repo, token):
        state["calls"] += 1
        if isinstance(state["info"], Exception):
            raise state["info"]
        return state["info"]

    async def releases(owner, repo, token, per_page=20):
        if not state["release"]:
            return []
        return [{"id": 1, "tag_name": "v1", "assets": [{"name": "app.apk"}]}]
    monkeypatch.setattr(github_client, "get_repo_info", repo_info)
    monkeypatch.setattr(github_client, "list_releases", releases)
    return state


def _review(authed, text="o/r", **extra):
    return authed.post("/repos", data={"csrf_token": CSRF, "repo_url": text, "asset_glob": "*.apk", **extra},
                       follow_redirects=False)


def _confirm(authed, github_id=1, **extra):
    return authed.post("/repos/confirm", data={"csrf_token": CSRF, "owner": "o", "repo": "r",
                                               "github_id": str(github_id), "asset_glob": "*.apk", **extra},
                       follow_redirects=False)


def test_looking_up_a_repo_shows_it_and_watches_nothing(authed, lookup):
    r = _review(authed, "https://github.com/o/r")
    assert r.status_code == 200
    assert "Is this the repo you meant?" in r.text and 'action="/repos/confirm"' in r.text
    assert 'name="github_id" value="1"' in r.text and "An app" in r.text
    assert db.list_repos() == []


def test_review_warns_about_look_alike_signals(authed, lookup):
    lookup["info"] = _info(fork=True, archived=True, owner_type="Organization",
                           created_at=main.datetime.now(main.timezone.utc).isoformat())
    page = _review(authed).text
    for needle in ("It&#39;s a fork", "archived", "created 0 days ago", "belongs to an organization"):
        assert needle in page, needle


def test_an_established_repo_gets_no_warnings(authed, lookup):
    page = _review(authed).text
    assert "flash flash-warn" not in page


def test_lookup_errors_are_shown(authed, lookup):
    lookup["info"] = github_client.GithubError("Repo or release not found (private repo needs GITHUB_TOKEN)")
    r = _review(authed)
    assert r.status_code == 303 and "not%20found" in r.headers["location"]


def test_confirm_watches_the_repo_with_its_identity_pinned(authed, lookup):
    r = _confirm(authed, include_prereleases="1")
    assert "ok=" in r.headers["location"]
    [repo] = db.list_repos()
    assert (repo["owner"], repo["repo"], repo["github_id"], repo["owner_id"], repo["owner_type"]) == ("o", "r", 1, 10, "User")
    assert repo["include_prereleases"] == 1
    assert any(a["action"] == "repo_add" and "id=1" in a["detail"] for a in db.list_audit())


def test_confirm_refuses_if_the_name_now_points_elsewhere(authed, lookup):
    lookup["info"] = _info(id=2)
    r = _confirm(authed, github_id=1)
    assert "different%20repo" in r.headers["location"]
    assert db.list_repos() == []


def test_the_same_repo_cannot_be_added_twice(authed, lookup):
    _confirm(authed)
    lookup["info"] = _info(owner="O", repo="R")  # same ID under another spelling
    assert "already%20registered" in _review(authed, "O/R").headers["location"]
    assert "already%20registered" in _confirm(authed).headers["location"]
    assert len(db.list_repos()) == 1


def test_confirm_needs_csrf(authed, lookup):
    r = authed.post("/repos/confirm", data={"csrf_token": "wrong", "owner": "o", "repo": "r", "github_id": "1"},
                    follow_redirects=False)
    assert r.status_code in (400, 403)
    assert db.list_repos() == [] and lookup["calls"] == 0


# ---- workflow artifacts ----

import io  # noqa: E402
import os  # noqa: E402
import zipfile  # noqa: E402

import apk_verify  # noqa: E402
import staging  # noqa: E402


def _artifact_json(id=5, repo_id=1, head_repo_id=1, expired=False, size=1000, name="app-debug"):
    return {"id": id, "name": name, "size_in_bytes": size, "expired": expired,
            "created_at": "2026-09-24T10:00:00Z",
            "workflow_run": {"id": 77, "repository_id": repo_id, "head_repository_id": head_repo_id,
                             "head_branch": "feature/x", "head_sha": "abcdef1234567890"}}


def test_artifacts_from_this_repos_own_runs_are_usable():
    a = github_client._parse_artifact(_artifact_json(), 1)
    assert (a.id, a.name, a.branch, a.head_sha, a.run_id) == (5, "app-debug", "feature/x", "abcdef1234567890", 77)


@pytest.mark.parametrize("kw", [
    {"head_repo_id": 2},          # a pull request from a fork: runs here, built from someone else's code
    {"repo_id": 2, "head_repo_id": 2},  # another repo entirely
    {"expired": True},
])
def test_unusable_artifacts_are_dropped(kw):
    assert github_client._parse_artifact(_artifact_json(**kw), 1) is None


def test_malformed_artifact_is_dropped():
    assert github_client._parse_artifact({"workflow_run": {"repository_id": 1, "head_repository_id": 1}}, 1) is None


class FakeArtifactsApi:
    def __init__(self, monkeypatch):
        self.items = [_artifact_json(), _artifact_json(id=6, head_repo_id=99)]
        self.requests = []
        self.redirect = "https://prod.blob.core.windows.net/a.zip?sig=x"
        real = httpx.AsyncClient
        transport = httpx.MockTransport(self.handle)
        monkeypatch.setattr(github_client.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
        github_client._etag_cache.clear()

    def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/actions/artifacts"):
            return httpx.Response(200, json={"total_count": len(self.items), "artifacts": self.items})
        if path.endswith("/zip"):
            return httpx.Response(302, headers={"location": self.redirect})
        if request.url.host.endswith("blob.core.windows.net"):
            return httpx.Response(200, content=b"PK\x03\x04zip")
        for item in self.items:
            if path.endswith(f"/actions/artifacts/{item['id']}"):
                return httpx.Response(200, json=item)
        return httpx.Response(404)


def test_list_artifacts_keeps_only_this_repos_own(monkeypatch):
    FakeArtifactsApi(monkeypatch)
    assert [a.id for a in asyncio.run(github_client.list_artifacts("o", "r", 1, "t"))] == [5]


def test_get_artifact_refuses_a_fork_prs_artifact(monkeypatch):
    FakeArtifactsApi(monkeypatch)
    with pytest.raises(github_client.GithubError, match="own workflows"):
        asyncio.run(github_client.get_artifact("o", "r", 6, 1, "t"))


def test_get_artifact_refuses_one_over_the_cap(monkeypatch):
    api = FakeArtifactsApi(monkeypatch)
    api.items[0]["size_in_bytes"] = github_client.MAX_ASSET_BYTES + 1
    with pytest.raises(github_client.GithubError, match="size cap"):
        asyncio.run(github_client.get_artifact("o", "r", 5, 1, "t"))


def test_artifact_download_drops_the_token_at_the_storage_host(monkeypatch, tmp_path):
    api = FakeArtifactsApi(monkeypatch)
    asyncio.run(github_client.download_artifact("o", "r", 5, str(tmp_path / "a.zip"), "secret"))
    api_req, blob_req = api.requests
    assert api_req.headers["authorization"] == "Bearer secret"
    assert "authorization" not in blob_req.headers
    assert (tmp_path / "a.zip").read_bytes() == b"PK\x03\x04zip"


def test_artifact_download_needs_a_token(tmp_path):
    with pytest.raises(github_client.GithubError, match="GitHub token"):
        asyncio.run(github_client.download_artifact("o", "r", 5, str(tmp_path / "a.zip"), None))


def test_artifact_redirect_elsewhere_is_refused(monkeypatch, tmp_path):
    api = FakeArtifactsApi(monkeypatch)
    api.redirect = "https://evil.example/a.zip"
    with pytest.raises(github_client.GithubError, match="evil.example"):
        asyncio.run(github_client.download_artifact("o", "r", 5, str(tmp_path / "a.zip"), "t"))


def _apk_zip(marker="x") -> bytes:
    apk = io.BytesIO()
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr(zipfile.ZipInfo("AndroidManifest.xml", date_time=(2020, 1, 1, 0, 0, 0)), marker)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr(zipfile.ZipInfo("app-debug.apk", date_time=(2020, 1, 1, 0, 0, 0)), apk.getvalue())
    return outer.getvalue()


@pytest.fixture
def artifact_env(monkeypatch):
    state = {"info": _info(), "zip": _apk_zip(), "downloads": 0}
    monkeypatch.setattr(poller, "GITHUB_TOKEN", "t")

    async def repo_info(owner, repo, token):
        return state["info"]

    async def list_artifacts(owner, repo, github_id, token):
        return [github_client._parse_artifact(_artifact_json(), github_id)]

    async def get_artifact(owner, repo, artifact_id, github_id, token):
        if artifact_id != 5:
            raise github_client.GithubError("That artifact has expired, or wasn't built by this repo's own workflows")
        return github_client._parse_artifact(_artifact_json(), github_id)

    async def download(owner, repo, artifact_id, dest, token):
        state["downloads"] += 1
        with open(dest, "wb") as f:
            f.write(state["zip"])
        import hashlib
        return hashlib.sha256(state["zip"]).hexdigest()

    async def build_notes(owner, repo, artifact, token):
        return "notes"

    async def no_releases(owner, repo, token, per_page=20):
        return state.get("releases", [])

    async def commits(owner, repo, tags, token):
        return state.get("release_shas", set())

    async def runs(owner, repo, token, per_page=50):
        return state.get("runs", {77: {"title": "CI", "event": "push", "number": 12,
                                       "message": "fix: faster reconnects\n\nLonger body.",
                                       "subject": "fix: faster reconnects"}})

    monkeypatch.setattr(github_client, "list_releases", no_releases)
    monkeypatch.setattr(github_client, "release_commits", commits)
    monkeypatch.setattr(github_client, "list_runs", runs)
    monkeypatch.setattr(github_client, "get_repo_info", repo_info)
    monkeypatch.setattr(github_client, "get_build_notes", build_notes)
    monkeypatch.setattr(github_client, "list_artifacts", list_artifacts)
    monkeypatch.setattr(github_client, "get_artifact", get_artifact)
    monkeypatch.setattr(github_client, "download_artifact", download)
    monkeypatch.setattr(apk_verify, "is_unsigned", lambda path: False)  # the fake APK stands for a signed one
    monkeypatch.setattr(apk_verify, "verify_signature", lambda path: apk_verify.SignerInfo("d" * 64, True))
    monkeypatch.setattr(apk_verify, "get_package_info",
                        lambda path: apk_verify.PackageInfo("com.example.app", 7, "1.7", ()))
    rid = db.create_repo("o", "r", "*.apk", github_id=1, owner_id=10, owner_type="User")
    state["rid"] = rid
    return state


def _uploads():
    d = os.path.join(staging.STAGING_ROOT, "uploads")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_artifacts_page_lists_them(authed, artifact_env):
    page = authed.get(f"/repos/{artifact_env['rid']}/artifacts").text
    assert "app-debug" in page and "feature/x" in page and "abcdef1" in page
    assert f'action="/repos/{artifact_env["rid"]}/artifacts/5/stage"' in page


def test_artifacts_page_explains_a_missing_token(authed, artifact_env, monkeypatch):
    monkeypatch.setattr(poller, "GITHUB_TOKEN", None)
    page = authed.get(f"/repos/{artifact_env['rid']}/artifacts").text
    assert "needs a GitHub token" in page and "/stage" not in page


def test_staging_an_artifact_stages_the_apk_inside_with_its_provenance(authed, artifact_env):
    r = authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/stage", data={"csrf_token": CSRF},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/install?")
    [apk] = db.list_staged_apks()
    assert apk["source"] == "upload" and apk["repo_id"] is None and apk["is_debug"]
    assert apk["tag"] == "app-debug feature/x@abcdef1" and apk["filename"] == "app-debug.apk"
    assert _uploads() == [f"{apk['sha256']}.apk"]  # the artifact zip is gone
    [row] = [a for a in db.list_audit() if a["action"] == "upload"]
    assert "from o/r artifact app-debug (feature/x @ abcdef1, run 77)" in row["detail"]
    assert db.get_repo(artifact_env["rid"])["expected_package"] is None  # no repo pin touched


def test_an_artifact_is_refused_when_the_repo_identity_changed(authed, artifact_env):
    artifact_env["info"] = _info(id=999)
    r = authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/stage", data={"csrf_token": CSRF},
                    follow_redirects=False)
    assert "different%20repo" in r.headers["location"]
    assert artifact_env["downloads"] == 0 and db.list_staged_apks() == []


def test_a_foreign_artifact_is_refused_and_leaves_nothing(authed, artifact_env):
    r = authed.post(f"/repos/{artifact_env['rid']}/artifacts/6/stage", data={"csrf_token": CSRF},
                    follow_redirects=False)
    assert "Artifact%20refused" in r.headers["location"]
    assert artifact_env["downloads"] == 0 and _uploads() == []


def test_an_artifact_that_isnt_an_apk_zip_is_refused_and_cleaned_up(authed, artifact_env):
    artifact_env["zip"] = b"PK\x03\x04 not really"
    r = authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/stage", data={"csrf_token": CSRF},
                    follow_redirects=False)
    assert "Upload%20refused" in r.headers["location"] and "/artifacts" in r.headers["location"]
    assert db.list_staged_apks() == [] and _uploads() == []


def test_staging_an_artifact_needs_csrf(authed, artifact_env):
    r = authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/stage", data={"csrf_token": "no"},
                    follow_redirects=False)
    assert r.status_code in (400, 403) and artifact_env["downloads"] == 0


def test_artifact_list_hides_docker_build_records_and_applies_the_pattern(authed, artifact_env, monkeypatch):
    async def many(owner, repo, github_id, token):
        return [github_client._parse_artifact(_artifact_json(id=i, name=n), github_id)
                for i, n in ((1, "o~r~ABC.dockerbuild"), (2, "app-debug-apk"), (3, "coverage"))]
    monkeypatch.setattr(github_client, "list_artifacts", many)
    page = authed.get(f"/repos/{artifact_env['rid']}/artifacts").text
    assert "dockerbuild" not in page and "app-debug-apk" in page and "coverage" in page
    db.set_meta("artifacts_name_glob", "*APK*")  # case-insensitive
    page = authed.get(f"/repos/{artifact_env['rid']}/artifacts").text
    assert "app-debug-apk" in page and "coverage" not in page


# ---- build notes for artifacts ----

class FakeRunsApi:
    def __init__(self, monkeypatch, run, pr=None):
        self.paths = []
        real = httpx.AsyncClient

        def handle(request):
            self.paths.append(request.url.path)
            if "/actions/runs/" in request.url.path:
                return httpx.Response(200, json=run)
            if "/pulls/" in request.url.path and pr is not None:
                return httpx.Response(200, json=pr)
            return httpx.Response(404)
        monkeypatch.setattr(github_client.httpx, "AsyncClient",
                            lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
        github_client._etag_cache.clear()


def _art():
    return github_client._parse_artifact(_artifact_json(), 1)


def test_build_notes_carry_the_run_and_the_full_commit_message(monkeypatch):
    FakeRunsApi(monkeypatch, {"display_title": "Release", "run_number": 12, "event": "workflow_dispatch",
                              "pull_requests": [], "head_commit": {"message": "fix(ble): stop hangs\n\nLong body."}})
    notes = asyncio.run(github_client.get_build_notes("o", "r", _art(), "t"))
    assert notes.startswith("Release (run #12, workflow_dispatch)")
    assert "feature/x @ abcdef1 · https://github.com/o/r/actions/runs/77" in notes
    assert notes.endswith("Commit abcdef1:\nfix(ble): stop hangs\n\nLong body.")


def test_build_notes_include_the_pull_request(monkeypatch):
    api = FakeRunsApi(monkeypatch, {"display_title": "CI", "run_number": 3, "event": "pull_request",
                                    "pull_requests": [{"number": 42}], "head_commit": {"message": "wip"}},
                      pr={"title": "Faster reconnects", "body": "## What\nReconnects in 1s."})
    notes = asyncio.run(github_client.get_build_notes("o", "r", _art(), "t"))
    assert "Pull request #42: Faster reconnects\n\n## What\nReconnects in 1s." in notes
    assert api.paths[-1].endswith("/pulls/42")


def test_build_notes_are_capped(monkeypatch):
    FakeRunsApi(monkeypatch, {"display_title": "x", "pull_requests": [], "head_commit": {"message": "m" * 50_000}})
    assert len(asyncio.run(github_client.get_build_notes("o", "r", _art(), "t"))) == github_client.MAX_NOTES_CHARS


def test_staged_artifact_shows_its_build_notes(authed, artifact_env, monkeypatch):
    async def notes(owner, repo, artifact, token):
        return "Release (run #12, push)\n<b>not html</b>"
    monkeypatch.setattr(github_client, "get_build_notes", notes)
    authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/stage", data={"csrf_token": CSRF})
    [apk] = db.list_staged_apks()
    assert apk["release_notes"].startswith("Release (run #12")
    page = authed.get("/install").text
    assert "Build notes for" in page and "&lt;b&gt;not html&lt;/b&gt;" in page


def test_an_artifact_stages_even_when_its_notes_cant_be_fetched(authed, artifact_env, monkeypatch):
    async def fail(owner, repo, artifact, token):
        raise github_client.GithubError("GitHub API answered HTTP 500")
    monkeypatch.setattr(github_client, "get_build_notes", fail)
    r = authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/stage", data={"csrf_token": CSRF},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/install?")
    [apk] = db.list_staged_apks()
    assert apk["release_notes"] is None


# ---- Install page: artifacts stand apart; devices get real names ----

def test_a_staged_artifact_records_where_it_came_from(authed, artifact_env):
    authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/stage", data={"csrf_token": CSRF})
    [apk] = db.list_staged_apks()
    assert (apk["artifact_repo"], apk["artifact_run_id"], apk["artifact_branch"], apk["artifact_sha"]) == \
        ("o/r", 77, "feature/x", "abcdef1234567890")


def test_install_page_sets_artifacts_apart(authed, artifact_env):
    authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/stage", data={"csrf_token": CSRF})
    import os as _os
    path = _os.path.join(staging.STAGING_ROOT, "uploads", "plain.apk")
    _os.makedirs(_os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(b"x")
    db.insert_staged_apk(repo_id=None, tag="hand upload", filename="plain.apk", sha256="e" * 64,
                         package_name="com.example.other", signer_sha256="d" * 64, path=path, source="upload")
    page = authed.get("/install").text
    art = page.index("Test builds from workflow artifacts")
    assert art < page.index(">Uploads<")
    card = page[art:page.index(">Uploads<")]
    assert "app-card-artifact" in card and "Artifact · test build" in card
    assert "feature/x" in card and "https://github.com/o/r/actions/runs/77" in card and "abcdef1 · run 77" in card
    assert "Artifact · test build" not in page[page.index(">Uploads<"):]


def test_one_kind_of_card_gets_no_section_headings(authed, artifact_env):
    authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/stage", data={"csrf_token": CSRF})
    assert "Test builds from workflow artifacts" not in authed.get("/install").text


def test_device_names_prefer_nickname_then_model(monkeypatch):
    import main as _main
    row = lambda **kw: {"nickname": None, "model": None, "serial": "56220DLCR005KT", **kw}

    class R(dict):
        def keys(self):
            return super().keys()
    assert _main._device_name(R(row(nickname="Steve's phone"))) == "Steve's phone"
    assert _main._device_name(R(row(model="Google Pixel 8"))) == "Google Pixel 8 · …005KT"
    assert _main._device_name(R(row())) == "56220DLCR005KT"


def test_device_model_is_read_and_sanitised(monkeypatch):
    import subprocess

    import adb_client
    answers = {"ro.product.manufacturer": "Google\n", "ro.product.model": "Pixel 8 <b>\x1b[31m\n"}

    def fake_run(*args, timeout):
        return subprocess.CompletedProcess(args, 0, stdout=answers[args[-1]], stderr="")
    monkeypatch.setattr(adb_client, "_run", fake_run)
    assert adb_client.device_model("10.0.0.5:5555") == "Google Pixel 8 b31m"
    answers.update({"ro.product.manufacturer": "OnePlus\n", "ro.product.model": "OnePlus 12\n"})
    assert adb_client.device_model("10.0.0.5:5555") == "OnePlus 12"


# ---- repos with nothing to install are refused ----

def test_a_repo_with_no_apk_release_and_no_token_is_refused(authed, lookup, monkeypatch):
    monkeypatch.setattr(poller, "GITHUB_TOKEN", None)
    lookup["release"] = False
    r = _review(authed)
    assert "no%20APK%20to%20install" in r.headers["location"] and "token" in r.headers["location"]
    assert "no%20APK" in _confirm(authed).headers["location"]  # confirm re-checks: it can be posted directly
    assert db.list_repos() == []


def test_releases_without_a_matching_asset_do_not_count(authed, lookup, monkeypatch):
    monkeypatch.setattr(poller, "GITHUB_TOKEN", None)

    async def releases(owner, repo, token, per_page=20):
        return [{"id": 1, "tag_name": "v1", "assets": [{"name": "app.exe"}]}]
    monkeypatch.setattr(github_client, "list_releases", releases)
    assert "matching" in _review(authed).headers["location"]


@pytest.mark.parametrize("lists_apk,allowed", [(True, True), (False, False), (None, False)])
def test_an_artifact_only_repo_needs_an_apk_in_its_artifacts(authed, lookup, monkeypatch, lists_apk, allowed):
    monkeypatch.setattr(poller, "GITHUB_TOKEN", "t")
    lookup["release"] = False

    async def arts(owner, repo, github_id, token):
        return [github_client._parse_artifact(_artifact_json(id=i, name=n), github_id)
                for i, n in ((1, "o~r~X.dockerbuild"), (2, "app-debug"))]
    checked = []

    async def lists(owner, repo, artifact_id, token):
        checked.append(artifact_id)
        return lists_apk
    monkeypatch.setattr(github_client, "list_artifacts", arts)
    monkeypatch.setattr(github_client, "artifact_lists_apk", lists)
    r = _review(authed)
    assert checked == [2]  # docker build records are never opened
    if allowed:
        assert r.status_code == 200 and "workflow artifacts do" in r.text
    else:
        assert "none%20of%20its%20recent%20workflow%20artifacts" in r.headers["location"]


def _zip_with(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(zipfile.ZipInfo(n, date_time=(2020, 1, 1, 0, 0, 0)), b"x" * 100)
    return buf.getvalue()


class FakeBlob:
    """An artifact download that serves byte ranges, like Azure blob storage."""

    def __init__(self, monkeypatch, body: bytes, ranges=True):
        self.body, self.ranges, self.requests = body, ranges, []
        real = httpx.AsyncClient
        monkeypatch.setattr(github_client.httpx, "AsyncClient",
                            lambda **kw: real(transport=httpx.MockTransport(self.handle), **kw))

    def handle(self, request):
        self.requests.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"location": "https://x.blob.core.windows.net/a.zip"})
        spec = request.headers.get("range", "")
        # Like Azure blob storage: explicit ranges only; a suffix range
        # ("bytes=-N") or none at all gets the whole file.
        if not self.ranges or not spec.startswith("bytes=") or spec.startswith("bytes=-"):
            return httpx.Response(200, content=self.body)
        a, _, b = spec[6:].partition("-")
        n = len(self.body)
        start, end = int(a), min(int(b), n - 1)
        return httpx.Response(206, content=self.body[start:end + 1], headers={"content-range": f"bytes {start}-{end}/{n}"})


@pytest.mark.parametrize("names,expected", [
    (["output-metadata.json", "release/app-release.apk"], True),
    (["mapping.txt", "__MACOSX/._app.apk"], False),
    (["build.exe"], False),
])
def test_artifact_zip_listing_reads_only_the_directory(monkeypatch, names, expected):
    blob = FakeBlob(monkeypatch, _zip_with(names))
    assert asyncio.run(github_client.artifact_lists_apk("o", "r", 5, "t")) is expected
    storage = [r for r in blob.requests if r.url.host != "api.github.com"]
    assert storage and all("range" in r.headers and "authorization" not in r.headers for r in storage)


def test_artifact_zip_listing_with_a_big_directory_uses_a_second_range(monkeypatch):
    names = [f"f{i:05d}-{'x' * 60}.txt" for i in range(1500)] + ["app.apk"]  # directory > the 64 KB tail
    blob = FakeBlob(monkeypatch, _zip_with(names))
    assert asyncio.run(github_client.artifact_lists_apk("o", "r", 5, "t")) is True
    assert len([r for r in blob.requests if r.url.host != "api.github.com"]) == 3  # size, tail, directory


def test_artifact_zip_listing_gives_up_without_ranges(monkeypatch):
    FakeBlob(monkeypatch, _zip_with(["app.apk"]), ranges=False)
    assert asyncio.run(github_client.artifact_lists_apk("o", "r", 5, "t")) is None


# ---- builds page: releases, and artifacts without release builds ----

def test_builds_page_lists_releases_and_hides_release_builds(authed, artifact_env, monkeypatch):
    artifact_env["releases"] = [
        {"id": 10, "tag_name": "v2", "published_at": "2026-09-20T00:00:00Z", "assets": [{"name": "app.apk"}]},
        {"id": 9, "tag_name": "v1", "published_at": "2026-09-01T00:00:00Z", "assets": [{"name": "notes.txt"}]},
    ]
    artifact_env["release_shas"] = {"1111111222"}

    async def arts(owner, repo, github_id, token):
        mk = lambda i, branch, sha: github_client._parse_artifact(
            {**_artifact_json(id=i, name=f"build-{i}"),
             "workflow_run": {"id": 70 + i, "repository_id": 1, "head_repository_id": 1,
                              "head_branch": branch, "head_sha": sha}}, github_id)
        return [mk(1, "v2", "aaaaaaa"), mk(2, "main", "1111111222"), mk(3, "feature/x", "bbbbbbb")]
    monkeypatch.setattr(github_client, "list_artifacts", arts)
    page = authed.get(f"/repos/{artifact_env['rid']}/artifacts").text
    assert f'action="/repos/{artifact_env["rid"]}/releases/10/stage"' in page
    assert "/releases/9/stage" not in page and "none matching" in page  # no APK: nothing to stage
    assert "build-3" in page and "build-1" not in page and "build-2" not in page


def test_staging_a_past_release_goes_through_the_release_checks(upstream, monkeypatch):
    rid = db.create_repo("o", "r", "*.apk", github_id=1, owner_id=10, owner_type="User")
    upstream.tag = "v2"
    _poll(rid)  # v2 is current and pins the repo

    async def get_release(owner, repo, release_id, token):
        return {"tag_name": "v1", "published_at": "2026-01-01T00:00:00Z", "body": "old",
                "assets": [{"name": "app.apk", "url": "https://api.github.com/app.apk", "uploader": {"login": upstream.uploader}}]}
    monkeypatch.setattr(github_client, "get_release", get_release)
    upstream.tag = "v1"  # what the fake download reports
    ok, message = asyncio.run(poller.stage_past_release(rid, 1))
    assert ok and "v1" in message
    repo = db.get_repo(rid)
    assert repo["last_tag"] == "v2"  # the current release is unchanged
    [latest] = {a["tag"] for a in db.list_latest_variants()}
    assert latest == "v2"  # newest by release date, not by download
    upstream.signer = "f" * 64
    monkeypatch.setattr(github_client, "get_release", lambda *a: _async({"tag_name": "v0", "published_at": "2025-01-01T00:00:00Z",
                        "assets": [{"name": "app.apk", "url": "https://api.github.com/app.apk", "uploader": {"login": "o"}}]}))
    upstream.tag = "v0"
    ok, message = asyncio.run(poller.stage_past_release(rid, 2))
    assert not ok and "Pin mismatch" in message
    assert db.get_repo(rid)["rejected_tag"] is None  # a refused old release isn't recorded as rejected


async def _async(value):
    return value


def test_builds_of_one_commit_are_grouped_with_its_message(authed, artifact_env, monkeypatch):
    async def arts(owner, repo, github_id, token):
        mk = lambda i, name, sha, run: github_client._parse_artifact(
            {**_artifact_json(id=i, name=name),
             "workflow_run": {"id": run, "repository_id": 1, "head_repository_id": 1,
                              "head_branch": "feature/x", "head_sha": sha}}, github_id)
        return [mk(1, "app-debug", "abcdef1234", 77), mk(2, "app-release", "abcdef1234", 78), mk(3, "old", "9999999999", 79)]
    monkeypatch.setattr(github_client, "list_artifacts", arts)
    artifact_env["runs"] = {
        77: {"title": "CI", "event": "push", "number": 12, "message": "fix: faster reconnects\n\nBody.", "subject": "fix: faster reconnects"},
        78: {"title": "Release build", "event": "push", "number": 5, "message": "fix: faster reconnects\n\nBody.", "subject": "fix: faster reconnects"},
    }
    page = authed.get(f"/repos/{artifact_env['rid']}/artifacts").text
    assert page.count('class="panel commit-group"') == 2
    first = page[page.index('class="panel commit-group"'):page.rindex('class="panel commit-group"')]
    assert "app-debug" in first and "app-release" in first and "fix: faster reconnects" in first
    assert "Body." in first and "CI #12" in first and "Release build #5" in first
    assert "(no commit message available)" in page[page.rindex('class="panel commit-group"'):]


def test_list_runs_parses_titles_and_messages(monkeypatch):
    real = httpx.AsyncClient
    body = {"workflow_runs": [{"id": 7, "display_title": "CI", "event": "push", "run_number": 3,
                               "head_commit": {"message": "feat: x\n\nmore"}}, {"id": "bad"}]}
    monkeypatch.setattr(github_client.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)), **kw))
    github_client._etag_cache.clear()
    runs = asyncio.run(github_client.list_runs("o", "r", "t"))
    assert runs == {7: {"title": "CI", "event": "push", "number": 3, "message": "feat: x\n\nmore", "subject": "feat: x"}}


def test_install_card_shows_the_commit_subject(authed, artifact_env, monkeypatch):
    async def notes(owner, repo, artifact, token):
        return "CI (run #12, push)\nfeature/x @ abcdef1 · link\n\nCommit abcdef1:\nfix: faster reconnects\n\nBody."
    monkeypatch.setattr(github_client, "get_build_notes", notes)
    authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/stage", data={"csrf_token": CSRF})
    [apk] = db.list_staged_apks()
    assert apk["artifact_subject"] == "fix: faster reconnects"
    assert '<p class="commit-subject">fix: faster reconnects</p>' in authed.get("/install").text


def test_refresh_drops_this_repos_cached_answers_only():
    github_client._etag_cache.clear()
    for url in ("https://api.github.com/repos/o/r/actions/artifacts?per_page=30",
                "https://api.github.com/repos/o/r", "https://api.github.com/repos/o/rr/releases"):
        github_client._etag_cache[url] = ('"e"', {})
    assert github_client.forget_cached("o", "r") == 2
    assert list(github_client._etag_cache) == ["https://api.github.com/repos/o/rr/releases"]


def test_builds_page_has_a_refresh_button(authed, artifact_env):
    github_client._etag_cache["https://api.github.com/repos/o/r/actions/artifacts?per_page=30"] = ('"e"', {})
    page = authed.get(f"/repos/{artifact_env['rid']}/artifacts?refresh=1").text
    assert f'href="/repos/{artifact_env["rid"]}/artifacts?refresh=1"' in page
    assert "https://api.github.com/repos/o/r/actions/artifacts?per_page=30" not in github_client._etag_cache


def test_a_repo_with_no_release_yet_is_not_an_error(upstream, monkeypatch):
    async def none_yet(owner, repo, token, include_prereleases=False):
        raise github_client.NoRelease("No published release yet")
    monkeypatch.setattr(github_client, "get_latest_release", none_yet)
    rid = db.create_repo("o", "r", "*.apk")  # not pinned yet: the check still pins it
    _poll(rid)
    repo = db.get_repo(rid)
    assert repo["last_error"] is None and repo["github_id"] == 1 and repo["last_checked_at"]


def test_latest_release_404_becomes_no_release(monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(github_client.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(404)), **kw))
    github_client._etag_cache.clear()
    with pytest.raises(github_client.NoRelease):
        asyncio.run(github_client.get_latest_release("o", "r", None))


# ---- Check now reports its result, reloading until it has one ----

def test_check_now_reloads_until_done_then_says_what_it_found(authed, monkeypatch):
    rid = db.create_repo("o", "r", "*.apk", github_id=1, owner_id=10, owner_type="User")

    async def slow(row):  # the background check hasn't finished yet
        return None
    monkeypatch.setattr(poller, "check_repo", slow)
    r = authed.post(f"/repos/{rid}/check-now", data={"csrf_token": CSRF}, follow_redirects=False)
    loc = r.headers["location"]
    assert loc.startswith(f"/sources?checking={rid}&since=")
    page = authed.get(loc).text
    assert '<meta http-equiv="refresh" content="2; url=/sources?checking=' in page and "Checking o/r" in page
    db.mark_repo_checked(rid)  # the check finishes: no release, no error
    page = authed.get(loc).text
    assert 'http-equiv="refresh"' not in page
    assert "has no releases yet, only workflow builds" in page


def test_check_now_shows_an_error_or_the_latest_release(authed):
    rid = db.create_repo("o", "r", "*.apk", github_id=1, owner_id=10, owner_type="User")
    since = db.now()
    db.update_repo_check(rid, last_tag="v2")
    assert "latest release is v2" in authed.get(f"/sources?checking={rid}&since={since}").text
    since = db.now()
    db.update_repo_check(rid, last_error="GitHub API answered HTTP 500")
    assert "HTTP 500" in authed.get(f"/sources?checking={rid}&since={since}").text


def test_check_now_stops_reloading_after_a_minute(authed):
    rid = db.create_repo("o", "r", "*.apk")
    page = authed.get(f"/sources?checking={rid}&since=2020-01-01T00:00:00%2B00:00").text
    assert 'http-equiv="refresh"' not in page and "taking a while" in page


def test_a_bogus_since_is_ignored(authed):
    rid = db.create_repo("o", "r", "*.apk")
    r = authed.get(f"/sources?checking={rid}&since=yesterday")
    assert r.status_code == 200 and 'http-equiv="refresh"' not in r.text


# ---- signing badges, and advice against a debug build ----

def _stage_debug_with_sibling(authed, artifact_env, monkeypatch):
    async def arts(owner, repo, github_id, token):
        base = _artifact_json()
        return [github_client._parse_artifact(base, github_id),
                github_client._parse_artifact({**base, "id": 6, "name": "app-release"}, github_id),
                github_client._parse_artifact({**base, "id": 7, "name": "o~r~X.dockerbuild"}, github_id)]
    monkeypatch.setattr(github_client, "list_artifacts", arts)
    authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/stage", data={"csrf_token": CSRF})


def test_staging_records_the_other_builds_of_the_commit(authed, artifact_env, monkeypatch):
    _stage_debug_with_sibling(authed, artifact_env, monkeypatch)
    [apk] = db.list_staged_apks()
    import json
    assert json.loads(apk["artifact_siblings"]) == [{"id": 6, "name": "app-release"}]  # docker records left out
    assert apk["artifact_repo_id"] == artifact_env["rid"]


@pytest.mark.parametrize("path", ["/sources", "/install"])
def test_a_debug_build_advises_its_signed_sibling(authed, artifact_env, monkeypatch, path):
    _stage_debug_with_sibling(authed, artifact_env, monkeypatch)  # artifact_env signs it as debug
    page = authed.get(path).text
    assert "Better not install this debug build" in page and "debug build</span>" in page
    assert f'action="/repos/{artifact_env["rid"]}/artifacts/6/stage"' in page and "Stage app-release" in page


def test_a_signed_build_gets_the_signed_badge_and_no_advice(authed, artifact_env, monkeypatch):
    monkeypatch.setattr(apk_verify, "verify_signature", lambda path: apk_verify.SignerInfo("d" * 64, False))
    _stage_debug_with_sibling(authed, artifact_env, monkeypatch)
    for path in ("/sources", "/install"):
        page = authed.get(path).text
        assert "title=\"Signed with the developer's own key\">signed</span>" in page
        assert "Better not install" not in page


def test_bad_sibling_data_is_ignored():
    import main as _main
    assert _main._siblings("not json") == [] and _main._siblings('[{"id": "x", "name": 1}, {"id": 2, "name": "ok"}]') == [{"id": 2, "name": "ok"}]


# ---- Builds page: how each test build is signed ----

def test_staging_an_artifact_records_its_signing(authed, artifact_env):
    authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/stage", data={"csrf_token": CSRF})
    rec = db.artifact_signing_map(artifact_env["rid"])[5]
    assert rec["kind"] == "debug" and rec["signer"] == "d" * 64  # artifact_env's verifier says debug


@pytest.mark.parametrize("unsigned,debug,signer,expected", [
    (False, False, "p" * 64, "signed · same key as releases"),
    (False, False, "q" * 64, "signed · different key"),
    (False, True, "d" * 64, "debug build"),
    (True, False, None, ">unsigned<"),
])
def test_check_signing_downloads_checks_and_forgets_the_file(authed, artifact_env, monkeypatch,
                                                             unsigned, debug, signer, expected):
    db.update_repo_check(artifact_env["rid"], expected_package="com.example.app", signer_sha256="p" * 64)
    monkeypatch.setattr(apk_verify, "is_unsigned", lambda path: unsigned)
    monkeypatch.setattr(apk_verify, "verify_signature", lambda path: apk_verify.SignerInfo(signer, debug))
    page = authed.get(f"/repos/{artifact_env['rid']}/artifacts").text
    assert f'action="/repos/{artifact_env["rid"]}/artifacts/5/check"' in page
    r = authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/check", data={"csrf_token": CSRF}, follow_redirects=False)
    assert r.headers["location"].endswith("#artifact-5") and "ok=" in r.headers["location"]
    assert artifact_env["downloads"] == 1 and _uploads() == [] and db.list_staged_apks() == []  # nothing kept
    page = authed.get(f"/repos/{artifact_env['rid']}/artifacts").text
    assert expected in page and "/artifacts/5/check" not in page  # known now: no second download


def test_a_broken_artifact_is_marked_invalid(authed, artifact_env):
    artifact_env["zip"] = b"PK\x03\x04 not really"
    authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/check", data={"csrf_token": CSRF})
    assert db.artifact_signing_map(artifact_env["rid"])[5]["kind"] == "invalid"
    assert "not a valid build" in authed.get(f"/repos/{artifact_env['rid']}/artifacts").text


def test_check_signing_needs_csrf_and_a_sound_repo(authed, artifact_env):
    r = authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/check", data={"csrf_token": "no"}, follow_redirects=False)
    assert r.status_code in (400, 403) and artifact_env["downloads"] == 0
    artifact_env["info"] = _info(id=999)
    r = authed.post(f"/repos/{artifact_env['rid']}/artifacts/5/check", data={"csrf_token": CSRF}, follow_redirects=False)
    assert "different%20repo" in r.headers["location"] and artifact_env["downloads"] == 0


def test_builds_page_explains_which_build_to_pick(authed, artifact_env):
    page = authed.get(f"/repos/{artifact_env['rid']}/artifacts").text
    assert "Which build to pick" in page and "same key as releases" in page and "throwaway debug key" in page
