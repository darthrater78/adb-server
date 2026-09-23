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
    """Stands in for GitHub + apksigner/aapt: one current release, a counter
    of downloads, and whatever signer/package the next APK reports."""

    def __init__(self, monkeypatch):
        self.tag = "v1"
        self.signer = SIGNER_A
        self.package = "com.example.app"
        self.downloads = 0
        monkeypatch.setattr(github_client, "get_latest_release", self._release)
        monkeypatch.setattr(github_client, "download_asset", self._download)
        monkeypatch.setattr(apk_verify, "verify_signature", lambda path: self.signer)
        monkeypatch.setattr(apk_verify, "get_package_name", lambda path: self.package)

    async def _release(self, owner, repo, token):
        return {"tag_name": self.tag, "body": f"notes for {self.tag}",
                "assets": [{"name": "app.apk", "url": "https://api.github.com/x"}]}

    async def _download(self, asset, dest, token):
        self.downloads += 1
        with open(dest, "wb") as f:
            f.write(self.tag.encode())
        return hashlib.sha256(self.tag.encode()).hexdigest(), 2


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

    async def flaky(owner, repo, token):
        if repo == "bad":
            raise RuntimeError("boom")
        return await real(owner, repo, token)
    monkeypatch.setattr(github_client, "get_latest_release", flaky)
    asyncio.run(poller.poll_all_repos())
    assert db.get_repo(good)["last_tag"] == "v1"
    assert "Internal error" in db.get_repo(bad)["last_error"]
