import logging
import os

import apk_verify
import db
import github_client

logger = logging.getLogger("poller")

STAGING_ROOT = os.environ.get("STAGING_ROOT", "/data/staging")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or None


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
    tmp_dest = os.path.join(repo_dir, f"_download_{tag}")

    try:
        sha256, _size = await github_client.download_asset(asset, tmp_dest, GITHUB_TOKEN)
    except github_client.GithubError as exc:
        logger.warning("%s: download failed: %s", label, exc)
        db.update_repo_check(repo_row["id"], last_error=f"Download failed: {exc}")
        return

    final_path = os.path.join(repo_dir, f"{sha256}.apk")

    try:
        signer_sha256 = apk_verify.verify_signature(tmp_dest)
        package_name = apk_verify.get_package_name(tmp_dest)
    except apk_verify.ApkVerifyError as exc:
        os.remove(tmp_dest)
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
        os.remove(tmp_dest)
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

    apk_id = db.insert_staged_apk(
        repo_id=repo_row["id"], tag=tag, filename=asset["name"],
        sha256=sha256, package_name=package_name,
        signer_sha256=signer_sha256, path=final_path,
    )
    if apk_id is None:
        # Duplicate (repo_id, tag) — another poll beat us to it (shouldn't
        # happen with max_instances=1, but stay safe if this is ever called
        # manually while a scheduled run is in flight).
        os.remove(final_path)
        return

    logger.info("%s: staged %s (%s)", label, tag, asset["name"])


async def poll_all_repos() -> None:
    for repo_row in db.list_repos():
        await check_repo(repo_row)
