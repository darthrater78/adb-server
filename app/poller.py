import asyncio
import logging
import os
import secrets
from dataclasses import dataclass

import apk_verify
import db
import github_client
import notify
import pushes
import staging

logger = logging.getLogger("poller")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or None
MAX_RELEASE_NOTES_CHARS = 20_000
# A release with per-ABI builds usually has 2-5 APKs. Cap it so one release
# can't make the poller download dozens of files.
MAX_VARIANTS_PER_RELEASE = 6


# One check per repo at a time: "Check now" (a background task) and the
# scheduled poll share the single event loop, so an asyncio.Lock is enough.
_repo_locks: dict[int, asyncio.Lock] = {}
# Strong references to fire-and-forget auto-update tasks, so they aren't
# garbage-collected mid-run.
_background: set[asyncio.Task] = set()


class _Rejected(Exception):
    """The release as a whole is rejected; message is shown on the Repos page."""

    def __init__(
        self, message: str, pending: tuple[str, str, bool] | None = None, permanent: bool = True,
    ):
        super().__init__(message)
        self.pending = pending  # (package, signer, lineage_ok) for a pin mismatch
        # A failed download may be transient: retry next poll rather than
        # remembering the tag as rejected.
        self.permanent = permanent


@dataclass
class _Variant:
    asset: dict
    tmp_path: str
    sha256: str
    signer: str
    info: apk_verify.PackageInfo


async def _download_and_verify(asset: dict, repo_dir: str) -> _Variant:
    # Never build a path from the tag or asset name: both are upstream-
    # controlled and may contain "/".
    tmp_path = os.path.join(repo_dir, f"_download_{secrets.token_hex(8)}")
    try:
        sha256, _size = await github_client.download_asset(asset, tmp_path, GITHUB_TOKEN)
    except github_client.GithubError as exc:
        raise _Rejected(f"Download of {asset['name']} failed: {exc}", permanent=False) from exc
    try:
        # apksigner/aapt are blocking subprocesses (up to 60s each) — run them
        # in a worker thread so the single event loop keeps serving the UI.
        signer = await asyncio.to_thread(apk_verify.verify_signature, tmp_path)
        info = await asyncio.to_thread(apk_verify.get_package_info, tmp_path)
    except apk_verify.ApkVerifyError as exc:
        staging.remove_file(tmp_path)
        raise _Rejected(f"APK verification failed for {asset['name']}: {exc}") from exc
    return _Variant(asset, tmp_path, sha256, signer, info)


async def _check_pin(repo_row, tag: str, first: _Variant) -> None:
    expected_package, expected_signer = repo_row["expected_package"], repo_row["signer_sha256"]
    if expected_package is None:
        return
    if first.info.name == expected_package and first.signer == expected_signer:
        return
    lineage = await asyncio.to_thread(apk_verify.signing_lineage, first.tmp_path)
    lineage_ok = expected_signer in lineage and first.info.name == expected_package
    raise _Rejected(
        f"Pin mismatch in {tag}: expected package '{expected_package}' signed by "
        f"{expected_signer}, got '{first.info.name}' signed by {first.signer}. "
        "Not staged — verify this release is genuinely from this repo before trusting it.",
        pending=(first.info.name, first.signer, lineage_ok),
    )


def _stage(repo_row, tag: str, release: dict, variants: list[_Variant], repo_dir: str) -> int:
    release_notes = (release.get("body") or "").strip()[:MAX_RELEASE_NOTES_CHARS] or None
    staged = 0
    for v in variants:
        final_path = os.path.join(repo_dir, f"{v.sha256}.apk")
        os.replace(v.tmp_path, final_path)
        apk_id = db.insert_staged_apk(
            repo_id=repo_row["id"], tag=tag, filename=v.asset["name"],
            sha256=v.sha256, package_name=v.info.name,
            signer_sha256=v.signer, path=final_path,
            release_notes=release_notes,
            version_code=v.info.version_code, version_name=v.info.version_name,
            abis=" ".join(v.info.abis),
        )
        if apk_id is None:
            # Duplicate (repo_id, tag, filename) — another check beat us to it.
            staging.remove_file(final_path)
        else:
            staged += 1
    return staged


async def check_repo(repo_row) -> None:
    lock = _repo_locks.setdefault(repo_row["id"], asyncio.Lock())
    if lock.locked():
        return  # a check of this repo is already running
    async with lock:
        # Re-read: the row passed in may be stale by the time the lock is held.
        fresh = db.get_repo(repo_row["id"])
        if fresh is not None:
            await _check_repo(fresh)


async def _notify(event: str, title: str, body: str) -> None:
    await asyncio.to_thread(notify.send, event, title, body)


async def _auto_update(repo_id: int) -> None:
    for install_id, device, apk in pushes.auto_push_targets(repo_id):
        logger.info("auto-update: %s %s -> %s", apk["repo"], apk["tag"], device["serial"])
        await asyncio.to_thread(pushes.run_push, install_id, device, apk)


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _check_repo(repo_row) -> None:
    owner, repo, glob_pattern = repo_row["owner"], repo_row["repo"], repo_row["asset_glob"]
    label = f"{owner}/{repo}"

    try:
        release = await github_client.get_latest_release(
            owner, repo, GITHUB_TOKEN, include_prereleases=bool(repo_row["include_prereleases"]),
        )
    except github_client.GithubError as exc:
        logger.warning("%s: %s", label, exc)
        db.update_repo_check(repo_row["id"], last_error=str(exc))
        return

    tag = release.get("tag_name")
    if not tag or tag == repo_row["last_tag"]:
        db.update_repo_check(repo_row["id"], last_error=None)
        return
    if tag == repo_row["rejected_tag"]:
        # Already downloaded, verified and rejected. Re-downloading it every
        # poll would burn bandwidth and change nothing; keep the error visible.
        db.mark_repo_checked(repo_row["id"])
        return

    assets = github_client.find_matching_assets(release, glob_pattern)
    if not assets:
        msg = f"No release asset in {tag} matches pattern '{glob_pattern}'"
        logger.warning("%s: %s", label, msg)
        db.update_repo_check(repo_row["id"], last_error=msg)
        return

    repo_dir = staging.repo_dir(repo_row["id"])
    os.makedirs(repo_dir, exist_ok=True)
    variants: list[_Variant] = []
    try:
        for asset in assets[:MAX_VARIANTS_PER_RELEASE]:
            variants.append(await _download_and_verify(asset, repo_dir))
        first = variants[0]
        for v in variants[1:]:
            if (v.info.name, v.signer) != (first.info.name, first.signer):
                raise _Rejected(
                    f"APKs in {tag} disagree: {first.asset['name']} and {v.asset['name']} "
                    "have different packages or signers — not staged"
                )
        await _check_pin(repo_row, tag, first)
    except _Rejected as exc:
        for v in variants:
            staging.remove_file(v.tmp_path)
        logger.error("%s: %s", label, exc)
        if not exc.permanent:
            db.update_repo_check(repo_row["id"], last_error=str(exc))
            return
        pkg, signer, lineage_ok = exc.pending or (None, None, None)
        db.set_rejected_tag(repo_row["id"], tag, str(exc), pkg, signer, lineage_ok)
        await _notify("rejected", f"{label} {tag} rejected", str(exc))
        return

    if repo_row["expected_package"] is None:
        # First release ever staged for this repo — pin it. Every later
        # release must match both, or it's a rejected, surfaced mismatch.
        db.update_repo_check(
            repo_row["id"], last_tag=tag,
            expected_package=first.info.name, signer_sha256=first.signer,
        )
    else:
        db.update_repo_check(repo_row["id"], last_tag=tag)

    staged = _stage(repo_row, tag, release, variants, repo_dir)
    logger.info("%s: staged %s (%d APK%s)", label, tag, staged, "" if staged == 1 else "s")
    pruned = staging.prune_repo(repo_row["id"])
    if pruned:
        logger.info("%s: pruned %d old staged file(s)", label, pruned)
    if staged:
        version = first.info.version_name or tag
        await _notify("staged", f"{label} {version} ready", f"{staged} APK(s) verified and staged.")
        _spawn(_auto_update(repo_row["id"]))


async def poll_all_repos() -> None:
    for repo_row in db.list_repos():
        try:
            await check_repo(repo_row)
        except Exception:
            # One repo failing in an unexpected way must not stop every repo
            # after it from being polled.
            logger.exception("%s/%s: poll crashed", repo_row["owner"], repo_row["repo"])
            db.update_repo_check(repo_row["id"], last_error="Internal error while checking — see server logs")
