import asyncio
import logging
import os
import secrets

import apk_verify
import db
import github_client
import staging

logger = logging.getLogger("poller")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or None
MAX_RELEASE_NOTES_CHARS = 20_000


async def check_repo(repo_row) -> None:
    owner, repo, glob_pattern = repo_row["owner"], repo_row["repo"], repo_row["asset_glob"]
    label = f"{owner}/{repo}"

    try:
        release = await github_client.get_latest_release(owner, repo, GITHUB_TOKEN)
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

    asset = github_client.find_matching_asset(release, glob_pattern)
    if asset is None:
        msg = f"No release asset in {tag} matches pattern '{glob_pattern}'"
        logger.warning("%s: %s", label, msg)
        db.update_repo_check(repo_row["id"], last_error=msg)
        return

    repo_dir = staging.repo_dir(repo_row["id"])
    os.makedirs(repo_dir, exist_ok=True)
    # Never build a path from the tag: it's upstream-controlled and may
    # contain "/" (e.g. "release/1.2").
    tmp_dest = os.path.join(repo_dir, f"_download_{secrets.token_hex(8)}")

    try:
        sha256, _size = await github_client.download_asset(asset, tmp_dest, GITHUB_TOKEN)
    except github_client.GithubError as exc:
        logger.warning("%s: download failed: %s", label, exc)
        db.update_repo_check(repo_row["id"], last_error=f"Download failed: {exc}")
        return

    final_path = os.path.join(repo_dir, f"{sha256}.apk")

    try:
        # apksigner/aapt are blocking subprocesses (up to 60s each) — run them
        # in a worker thread so the single event loop keeps serving the UI.
        signer_sha256 = await asyncio.to_thread(apk_verify.verify_signature, tmp_dest)
        package_name = await asyncio.to_thread(apk_verify.get_package_name, tmp_dest)
    except apk_verify.ApkVerifyError as exc:
        os.remove(tmp_dest)
        logger.warning("%s: verification failed: %s", label, exc)
        db.set_rejected_tag(repo_row["id"], tag, f"APK verification failed in {tag}: {exc}")
        return

    expected_package = repo_row["expected_package"]
    expected_signer = repo_row["signer_sha256"]

    if expected_package is None:
        # First release ever staged for this repo — pin it. Every later
        # release must match both, or it's a rejected, surfaced mismatch, not
        # a silent skip.
        db.update_repo_check(
            repo_row["id"], last_tag=tag,
            expected_package=package_name, signer_sha256=signer_sha256,
        )
    elif package_name != expected_package or signer_sha256 != expected_signer:
        os.remove(tmp_dest)
        msg = (
            f"Pin mismatch in {tag}: expected package '{expected_package}' signed by "
            f"{expected_signer}, got '{package_name}' signed by {signer_sha256}. "
            "Not staged — verify this release is genuinely from this repo before trusting it."
        )
        logger.error("%s: %s", label, msg)
        db.set_rejected_tag(repo_row["id"], tag, msg)
        return
    else:
        db.update_repo_check(repo_row["id"], last_tag=tag)

    os.replace(tmp_dest, final_path)

    release_notes = (release.get("body") or "").strip()[:MAX_RELEASE_NOTES_CHARS] or None

    apk_id = db.insert_staged_apk(
        repo_id=repo_row["id"], tag=tag, filename=asset["name"],
        sha256=sha256, package_name=package_name,
        signer_sha256=signer_sha256, path=final_path,
        release_notes=release_notes,
    )
    if apk_id is None:
        # Duplicate (repo_id, tag) — another poll beat us to it (shouldn't
        # happen with max_instances=1, but stay safe if this is ever called
        # manually while a scheduled run is in flight).
        os.remove(final_path)
        return

    logger.info("%s: staged %s (%s)", label, tag, asset["name"])
    pruned = staging.prune_repo(repo_row["id"])
    if pruned:
        logger.info("%s: pruned %d old staged release(s)", label, pruned)


async def poll_all_repos() -> None:
    for repo_row in db.list_repos():
        try:
            await check_repo(repo_row)
        except Exception:
            # One repo failing in an unexpected way must not stop every repo
            # after it from being polled.
            logger.exception("%s/%s: poll crashed", repo_row["owner"], repo_row["repo"])
            db.update_repo_check(repo_row["id"], last_error="Internal error while checking — see server logs")
