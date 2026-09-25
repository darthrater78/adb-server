import asyncio
import hashlib
import os

import pytest

import apk_verify
import db
import github_client
import poller
import staging

SIGNER_A = "a" * 64
SIGNER_B = "b" * 64


class FakeUpstream:
    """Stands in for GitHub + apksigner/aapt2: one current release, a counter
    of downloads, and whatever signer/package the next APK reports."""

    def __init__(self, monkeypatch):
        self.tag = "v1"
        self.signer = SIGNER_A
        self.debug = False
        self.package = "com.example.app"
        self.assets = {"app.apk": ()}  # asset name -> ABIs its APK reports
        self.lineage: list[str] = []
        self.download_error: str | None = None
        self.downloads = 0
        self._abis_by_path: dict[str, tuple] = {}
        self.info = github_client.RepoInfo(id=1, owner="o", repo="r", owner_id=10, owner_type="User",
                                           created_at="2020-01-01T00:00:00Z", fork=False, archived=False,
                                           stars=5, description="")
        self.uploader = "o"
        self.repo_lookups = 0
        monkeypatch.setattr(github_client, "get_repo_info", self._repo_info)
        monkeypatch.setattr(github_client, "get_latest_release", self._release)
        monkeypatch.setattr(github_client, "download_asset", self._download)
        monkeypatch.setattr(apk_verify, "verify_signature",
                            lambda path: apk_verify.SignerInfo(self.signer, self.debug))
        monkeypatch.setattr(apk_verify, "get_package_info", self._info)
        monkeypatch.setattr(apk_verify, "signing_lineage", lambda path: self.lineage)

    async def _repo_info(self, owner, repo, token):
        self.repo_lookups += 1
        return self.info

    async def _release(self, owner, repo, token, include_prereleases=False):
        return {"tag_name": self.tag, "body": f"notes for {self.tag}",
                "assets": [{"name": n, "url": f"https://api.github.com/{n}", "uploader": {"login": self.uploader}}
                           for n in self.assets]}

    async def _download(self, asset, dest, token):
        if self.download_error:
            raise github_client.GithubError(self.download_error)
        self.downloads += 1
        content = f"{self.tag}/{asset['name']}".encode()
        with open(dest, "wb") as f:
            f.write(content)
        self._abis_by_path[dest] = self.assets[asset["name"]]
        return hashlib.sha256(content).hexdigest(), len(content)

    def _info(self, path):
        return apk_verify.PackageInfo(self.package, int(self.tag.lstrip("v").split("/")[-1].replace(".", "") or 0), self.tag, self._abis_by_path[path])


@pytest.fixture
def upstream(monkeypatch):
    return FakeUpstream(monkeypatch)


def _poll(repo_id):
    asyncio.run(poller.check_repo(db.get_repo(repo_id)))


def test_first_release_pins_package_and_signer(upstream):
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    repo = db.get_repo(rid)
    assert repo["expected_package"] == "com.example.app"
    assert repo["signer_sha256"] == SIGNER_A
    assert len(db.list_staged_apks()) == 1


def test_pin_mismatch_is_rejected_and_not_redownloaded(upstream):
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    upstream.tag, upstream.signer = "v2", SIGNER_B
    _poll(rid)
    _poll(rid)
    _poll(rid)
    repo = db.get_repo(rid)
    assert "Pin mismatch in v2" in repo["last_error"]
    assert repo["last_tag"] == "v1"
    assert upstream.downloads == 2  # v1 once, v2 once — never again
    assert len(db.list_staged_apks()) == 1


def test_verification_failure_is_not_redownloaded(upstream, monkeypatch):
    def boom(path):
        raise apk_verify.ApkVerifyError("unsigned")
    monkeypatch.setattr(apk_verify, "verify_signature", boom)
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    _poll(rid)
    assert upstream.downloads == 1
    assert "unsigned" in db.get_repo(rid)["last_error"]


def test_newer_good_release_after_rejection_is_staged(upstream):
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    upstream.tag, upstream.signer = "v2", SIGNER_B
    _poll(rid)
    upstream.tag, upstream.signer = "v3", SIGNER_A
    _poll(rid)
    repo = db.get_repo(rid)
    assert repo["last_tag"] == "v3"
    assert repo["last_error"] is None


def test_retention_prunes_old_files_but_keeps_rows(upstream, monkeypatch):
    rid = db.create_repo("o", "r", "*.apk")
    for i in range(1, 6):
        upstream.tag = f"v{i}"
        _poll(rid)
    visible = db.list_staged_apks()
    assert [a["tag"] for a in visible] == ["v5", "v4", "v3"]
    remaining = sorted(os.listdir(staging.repo_dir(rid)))
    assert len(remaining) == 3


def test_remove_file_refuses_paths_outside_root(tmp_path):
    outside = tmp_path / "precious.txt"
    outside.write_text("keep me")
    staging.remove_file(str(outside))
    assert outside.exists()


def test_tag_with_slash_is_staged(upstream):
    upstream.tag = "release/1.2"
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    assert [a["tag"] for a in db.list_staged_apks()] == ["release/1.2"]


def test_one_crashing_repo_does_not_stop_the_others(upstream, monkeypatch):
    bad = db.create_repo("a", "bad", "*.apk")
    good = db.create_repo("z", "good", "*.apk")
    real = upstream._release

    async def flaky(owner, repo, token, include_prereleases=False):
        if repo == "bad":
            raise RuntimeError("boom")
        return await real(owner, repo, token)
    monkeypatch.setattr(github_client, "get_latest_release", flaky)
    asyncio.run(poller.poll_all_repos())
    assert db.get_repo(good)["last_tag"] == "v1"
    assert "Internal error" in db.get_repo(bad)["last_error"]


def test_all_abi_variants_of_a_release_are_staged(upstream):
    upstream.assets = {"app-arm64-v8a.apk": ("arm64-v8a",), "app-armeabi-v7a.apk": ("armeabi-v7a",),
                       "app-universal.apk": ("arm64-v8a", "armeabi-v7a", "x86_64")}
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    staged = {a["filename"]: a["abis"] for a in db.list_staged_apks()}
    assert staged == {"app-arm64-v8a.apk": "arm64-v8a", "app-armeabi-v7a.apk": "armeabi-v7a",
                      "app-universal.apk": "arm64-v8a armeabi-v7a x86_64"}


def test_variants_with_different_signers_reject_the_release(upstream, monkeypatch):
    upstream.assets = {"a.apk": (), "b.apk": ()}
    signers = iter([SIGNER_A, SIGNER_B])
    monkeypatch.setattr(apk_verify, "verify_signature",
                        lambda path: apk_verify.SignerInfo(next(signers), False))
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    assert db.list_staged_apks() == []
    assert "disagree" in db.get_repo(rid)["last_error"]
    assert os.listdir(staging.repo_dir(rid)) == []  # temp files cleaned up


def test_download_failure_is_retried_next_poll(upstream):
    upstream.download_error = "connection reset"
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    assert db.get_repo(rid)["rejected_tag"] is None
    upstream.download_error = None
    _poll(rid)
    assert db.get_repo(rid)["last_tag"] == "v1"


def test_retention_keeps_all_variants_of_kept_releases(upstream):
    upstream.assets = {"a.apk": ("arm64-v8a",), "b.apk": ("x86_64",)}
    rid = db.create_repo("o", "r", "*.apk")
    for i in range(1, 5):
        upstream.tag = f"v{i}"
        _poll(rid)
    tags = [a["tag"] for a in db.list_staged_apks()]
    assert sorted(tags) == ["v2", "v2", "v3", "v3", "v4", "v4"]


def test_pin_mismatch_records_rotation_for_review(upstream):
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    upstream.tag, upstream.signer, upstream.lineage = "v2", SIGNER_B, [SIGNER_A, SIGNER_B]
    _poll(rid)
    repo = db.get_repo(rid)
    assert repo["pending_signer"] == SIGNER_B
    assert repo["pending_lineage_ok"] == 1


def test_pin_mismatch_without_lineage_is_not_proven(upstream):
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    upstream.tag, upstream.signer = "v2", SIGNER_B
    _poll(rid)
    assert db.get_repo(rid)["pending_lineage_ok"] == 0


def test_accepting_pending_signer_repins(upstream):
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    upstream.tag, upstream.signer = "v2", SIGNER_B
    _poll(rid)
    assert db.accept_pending_signer(rid)
    _poll(rid)
    repo = db.get_repo(rid)
    assert repo["signer_sha256"] == SIGNER_B and repo["last_tag"] == "v2" and repo["last_error"] is None
    assert not db.accept_pending_signer(rid)  # nothing pending any more


def test_debug_signed_release_is_refused_and_not_pinned(upstream):
    upstream.debug = True
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    repo = db.get_repo(rid)
    assert db.list_staged_apks() == []
    assert "debug certificate" in repo["last_error"]
    assert repo["signer_sha256"] is None


def test_an_unsigned_release_is_refused_unless_the_repo_opted_in(upstream, monkeypatch):
    monkeypatch.setattr(apk_verify, "is_unsigned", lambda path: True)
    signed = []
    monkeypatch.setattr(poller.signing, "sign_in_place", lambda path, source: signed.append(source))
    rid = db.create_repo("o", "r", "*.apk", github_id=1, owner_id=10, owner_type="User")
    _poll(rid)
    repo = db.get_repo(rid)
    assert "unsigned" in repo["last_error"] and repo["rejected_tag"] == "v1" and signed == []
    db.set_sign_unsigned(rid, True)
    upstream.tag = "v2"
    _poll(rid)
    [apk] = db.list_staged_apks()
    # With the repo's own key (by GitHub ID), not one shared by every source.
    assert apk["tag"] == "v2" and apk["server_signed"] == 1 and signed == ["github-1"]


# ---- restaging a deleted release ----

def _delete_all_staged(authed):
    from conftest import CSRF
    for a in db.list_staged_apks():
        authed.post(f"/staged/{a['id']}/delete", data={"csrf_token": CSRF})


def test_staging_a_deleted_release_again_is_not_refused_as_already_staged(upstream, authed, monkeypatch):
    async def get_release(owner, repo, release_id, token):
        return await upstream._release(owner, repo, token)
    monkeypatch.setattr(github_client, "get_release", get_release)
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    [before] = db.list_staged_apks()
    _delete_all_staged(authed)
    ok, message = asyncio.run(poller.stage_past_release(rid, 1))
    assert ok, message
    [after] = db.list_staged_apks()
    # The deleted row is revived, so install history keeps pointing at it.
    assert after["id"] == before["id"] and after["pruned_at"] is None
    assert os.path.exists(after["path"])


def test_scheduled_poll_leaves_a_deleted_release_deleted(upstream, authed):
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    _delete_all_staged(authed)
    _poll(rid)
    assert db.list_staged_apks() == []
    assert upstream.downloads == 1


def test_check_now_restages_a_deleted_release(upstream, authed):
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    _delete_all_staged(authed)
    asyncio.run(poller.check_repo(db.get_repo(rid), restage=True))
    assert len(db.list_staged_apks()) == 1
    assert upstream.downloads == 2


def test_check_now_does_not_redownload_a_staged_release(upstream):
    rid = db.create_repo("o", "r", "*.apk")
    _poll(rid)
    asyncio.run(poller.check_repo(db.get_repo(rid), restage=True))
    assert upstream.downloads == 1


def test_check_now_route_asks_for_a_restage(authed, monkeypatch):
    from conftest import CSRF
    calls = []

    async def fake_check(row, restage=False):
        calls.append(restage)

    monkeypatch.setattr(poller, "check_repo", fake_check)
    rid = db.create_repo("o", "r", "*.apk")
    authed.post(f"/repos/{rid}/check-now", data={"csrf_token": CSRF})
    assert calls == [True]


def test_insert_still_refuses_a_live_duplicate():
    rid = db.create_repo("o", "r", "*.apk")
    args = dict(repo_id=rid, tag="v1", filename="a.apk", sha256="x" * 64,
                package_name="p", signer_sha256=SIGNER_A, path="/data/staging/1/x.apk")
    assert db.insert_staged_apk(**args) is not None
    assert db.insert_staged_apk(**args) is None
