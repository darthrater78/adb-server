import logging
import os
import tempfile

import apk_verify
import db
import github_client

logger = logging.getLogger("poller")

STAGING_ROOT = os.environ.get("STAGING_ROOT", "/data/staging")
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

    asset = github_client.find_matching_asset(release, glob_pattern)
    if asset is None:
        msg = f"No release asset in {tag} matches pattern '{glob_pattern}'"
        logger.warning("%s: %s", label, msg)
        db.update_repo_check(repo_row["id"], last_error=msg)
        return

    repo_dir = os.path.join(STAGING_ROOT, str(repo_row["id"]))
    os.makedirs(repo_dir, exist_ok=True)
    # The tag is upstream-controlled and must never reach a path. Git allows
    # '/' inside a ref name (release/1.0 is ordinary), which os.path.join
    # turns into a subdirectory that does not exist — the download then dies
    # on FileNotFoundError, which is not a GithubError and so escaped all the
    # way out of the scheduled job. mkstemp also gives an unpredictable name
    # (0600), closing the symlink race a fixed name leaves open.
    fd, tmp_dest = tempfile.mkstemp(dir=repo_dir, prefix="_download_")
    os.close(fd)

    try:
        sha256, _size = await github_client.download_asset(asset, tmp_dest, GITHUB_TOKEN)
    except github_client.GithubError as exc:
        logger.warning("%s: download failed: %s", label, exc)
        db.update_repo_check(repo_row["id"], last_error=f"Download failed: {exc}")
        _discard(tmp_dest)
        return
    except Exception:
        # An HTTP error from the CDN is not a GithubError and is handled a
        # level up — but the empty mkstemp file is ours to clean up either way.
        _discard(tmp_dest)
        raise

    final_path = os.path.join(repo_dir, f"{sha256}.apk")

    try:
        signer_sha256 = apk_verify.verify_signature(tmp_dest)
        package_name = apk_verify.get_package_name(tmp_dest)
    except apk_verify.ApkVerifyError as exc:
        _discard(tmp_dest)
        logger.warning("%s: verification failed: %s", label, exc)
        db.update_repo_check(repo_row["id"], last_error=f"APK verification failed: {exc}")
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
        _discard(tmp_dest)
        msg = (
            f"Pin mismatch: expected package '{expected_package}' signed by "
            f"{expected_signer}, got '{package_name}' signed by {signer_sha256}. "
            "Not staged — verify this release is genuinely from this repo before trusting it."
        )
        logger.error("%s: %s", label, msg)
        db.update_repo_check(repo_row["id"], last_error=msg)
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


def _discard(path: str) -> None:
    """Remove a staging temp file, tolerating one that is already gone."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


async def poll_all_repos() -> None:
    for repo_row in db.list_repos():
        try:
            await check_repo(repo_row)
        except Exception:
            # This loop is the scheduled job: an exception escaping here
            # skipped every repo after this one, on every cycle, for as long
            # as the offending repo stayed in the list.
            logger.exception("%s/%s: check failed", repo_row["owner"], repo_row["repo"])
            db.update_repo_check(
                repo_row["id"],
                last_error="Internal error while checking this repo — see server logs",
            )
