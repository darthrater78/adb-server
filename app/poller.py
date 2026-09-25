import asyncio
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import apk_verify
import db
import github_client
import notify
import pushes
import secretbox
import signing
import staging

logger = logging.getLogger("poller")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or None


def github_token() -> str | None:
    """The token saved on the Settings page, else GITHUB_TOKEN from .env."""
    try:
        saved = db.get_secret("github_token")
    except secretbox.SecretUnreadable:
        logger.error("the GitHub token saved in Settings can't be decrypted (SECRET_KEY changed?) — "
                     "save it again; falling back to GITHUB_TOKEN")
        saved = None
    return saved or GITHUB_TOKEN


def token_source() -> str | None:
    """"settings", "env", "unreadable" or None — for the Settings page."""
    if db.get_meta("github_token") is not None:
        try:
            db.get_secret("github_token")
            return "settings"
        except secretbox.SecretUnreadable:
            return "unreadable"
    return "env" if GITHUB_TOKEN else None
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
    """The release as a whole is rejected; message is shown on the Sources page."""

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
    server_signed: bool = False


async def _download_and_verify(asset: dict, repo_dir: str, sign_as: str | None = None) -> _Variant:
    """`sign_as` is the signing source an unsigned APK is signed for, or None
    when the repo hasn't opted in (it is then refused)."""
    # Never build a path from the tag or asset name: both are upstream-
    # controlled and may contain "/".
    tmp_path = os.path.join(repo_dir, f"_download_{secrets.token_hex(8)}")
    try:
        sha256, _size = await github_client.download_asset(asset, tmp_path, github_token())
    except github_client.GithubError as exc:
        raise _Rejected(f"Download of {asset['name']} failed: {exc}", permanent=False) from exc
    server_signed = False
    try:
        if await asyncio.to_thread(apk_verify.is_unsigned, tmp_path):
            if sign_as is None:
                raise apk_verify.ApkVerifyError(
                    "it's unsigned, and Android can't install an unsigned APK. Turn on signing unsigned builds "
                    "with this server's key for this repo (Sources → Builds) to stage it")
            try:
                await asyncio.to_thread(signing.sign_in_place, tmp_path, sign_as)
            except signing.SigningError as exc:
                raise apk_verify.ApkVerifyError(f"signing it with this server's key failed: {exc}") from exc
            sha256, server_signed = await asyncio.to_thread(apk_verify.sha256_file, tmp_path), True
        # apksigner/aapt2 are blocking subprocesses (up to 60s each) — run them
        # in a worker thread so the single event loop keeps serving the UI.
        signer = await asyncio.to_thread(apk_verify.verify_signature, tmp_path)
        # Nobody is watching when the poller runs, so a debug certificate
        # (generated locally per machine, identifies nobody) is refused here.
        # Manual uploads allow it with a warning — see routes_sources.upload_apk.
        if signer.debug:
            raise apk_verify.ApkVerifyError(
                "APK is signed with the default Android debug certificate (CN=Android Debug)"
            )
        info = await asyncio.to_thread(apk_verify.get_package_info, tmp_path)
    except apk_verify.ApkVerifyError as exc:
        staging.remove_file(tmp_path)
        raise _Rejected(f"APK verification failed for {asset['name']}: {exc}") from exc
    return _Variant(asset, tmp_path, sha256, signer.fingerprint, info, server_signed)


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


def _release_date(release: dict) -> str | None:
    """The release's publish date, in the same form as downloaded_at, so the
    two sort together: "latest" means newest release, not newest download."""
    raw = release.get("published_at") or release.get("created_at")
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


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
            abis=" ".join(v.info.abis), released_at=_release_date(release), server_signed=v.server_signed,
        )
        if apk_id is None:
            # Duplicate (repo_id, tag, filename) — another check beat us to it.
            staging.remove_file(final_path)
        else:
            staged += 1
    return staged


async def check_repo(repo_row, restage: bool = False) -> None:
    """restage: also re-download the current release if its files were
    deleted. Only a manual Check now asks for that; the scheduled poll leaves
    a deliberately deleted release deleted."""
    lock = _repo_locks.setdefault(repo_row["id"], asyncio.Lock())
    if lock.locked():
        return  # a check of this repo is already running
    async with lock:
        # Re-read: the row passed in may be stale by the time the lock is held.
        fresh = db.get_repo(repo_row["id"])
        if fresh is not None:
            await _check_repo(fresh, restage)


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


def identity_problem(repo_row, info: github_client.RepoInfo) -> str | None:
    """Whether the repo GitHub serves under this name is still the one that
    was pinned. A different ID means the name now belongs to another repo
    (the old one was renamed or deleted and the name re-registered); a
    different owner ID means the repo was transferred."""
    if repo_row["github_id"] is None:
        return None
    if info.id != repo_row["github_id"]:
        return (f"{repo_row['owner']}/{repo_row['repo']} is now a different repo on GitHub (ID {info.id}, "
                f"pinned {repo_row['github_id']}). Nothing is staged from it — the original was renamed "
                "or deleted and someone else holds the name. Remove it, and re-add the real repo if you find it.")
    if info.owner_id != repo_row["owner_id"]:
        return (f"{repo_row['owner']}/{repo_row['repo']} changed owner on GitHub (owner ID {info.owner_id}, "
                f"pinned {repo_row['owner_id']}). Nothing is staged from it until you remove and re-add it "
                "to confirm the new owner.")
    return None


def _check_uploaders(tag: str, assets: list[dict], info: github_client.RepoInfo) -> None:
    refused = [a for a in assets if not github_client.uploader_allowed(a, info.owner, info.owner_type)]
    if not refused:
        return
    who = ", ".join(sorted({str((a.get("uploader") or {}).get("login") or "unknown") for a in refused}))
    allowed = (f"{github_client.ACTIONS_BOT} (an organization's release assets must come from a workflow)"
               if info.owner_type != "User" else f"{info.owner} or {github_client.ACTIONS_BOT}")
    raise _Rejected(f"Assets in {tag} were uploaded by {who}, not {allowed} — not downloaded or staged")


async def _check_identity(repo_row, label: str) -> github_client.RepoInfo | None:
    """Returns the repo's current info, or None if it must not be polled."""
    try:
        info = await github_client.get_repo_info(repo_row["owner"], repo_row["repo"], github_token())
    except github_client.GithubError as exc:
        logger.warning("%s: %s", label, exc)
        db.update_repo_check(repo_row["id"], last_error=str(exc))
        return None
    problem = identity_problem(repo_row, info)
    if problem:
        logger.error("%s: %s", label, problem)
        if repo_row["last_error"] != problem:  # notify once, not every poll
            await _notify("rejected", f"{label} identity changed", problem)
        db.update_repo_check(repo_row["id"], last_error=problem)
        return None
    if repo_row["github_id"] is None:
        db.set_repo_identity(repo_row["id"], info.id, info.owner_id, info.owner_type)
        logger.info("%s: pinned GitHub repo ID %d", label, info.id)
    return info


async def _verify_release(repo_row, tag: str, assets: list[dict], info, repo_dir: str) -> list[_Variant]:
    """Every check a release goes through before it's staged: who uploaded
    it, each APK's download and signature, that its APKs agree, and the pin.
    Raises _Rejected, having removed anything it downloaded."""
    assets = assets[:MAX_VARIANTS_PER_RELEASE]
    sign_as = signing.repo_source(info.id) if repo_row["sign_unsigned"] else None
    variants: list[_Variant] = []
    try:
        _check_uploaders(tag, assets, info)
        for asset in assets:
            variants.append(await _download_and_verify(asset, repo_dir, sign_as))
        first = variants[0]
        for v in variants[1:]:
            if (v.info.name, v.signer) != (first.info.name, first.signer):
                raise _Rejected(
                    f"APKs in {tag} disagree: {first.asset['name']} and {v.asset['name']} "
                    "have different packages or signers — not staged"
                )
        await _check_pin(repo_row, tag, first)
    except _Rejected:
        for v in variants:
            staging.remove_file(v.tmp_path)
        raise
    return variants


async def stage_past_release(repo_id: int, release_id: int) -> tuple[bool, str]:
    """Stages an older release on request, through every check a polled
    release gets. It never moves the repo's current tag, records nothing as
    rejected (the operator sees the reason at once), and never auto-updates:
    "latest" is by release date, so an old release staged now isn't it."""
    lock = _repo_locks.setdefault(repo_id, asyncio.Lock())
    async with lock:
        repo_row = db.get_repo(repo_id)
        if repo_row is None:
            return False, "No such repo"
        owner, repo = repo_row["owner"], repo_row["repo"]
        try:
            info = await github_client.get_repo_info(owner, repo, github_token())
            release = await github_client.get_release(owner, repo, release_id, github_token())
        except github_client.GithubError as exc:
            return False, str(exc)
        if problem := identity_problem(repo_row, info):
            return False, problem
        tag = release.get("tag_name")
        if not tag or release.get("draft"):
            return False, "That release isn't published"
        assets = github_client.find_matching_assets(release, repo_row["asset_glob"])
        if not assets:
            return False, f"No asset in {tag} matches '{repo_row['asset_glob']}'"
        repo_dir = staging.repo_dir(repo_id)
        os.makedirs(repo_dir, exist_ok=True)
        try:
            variants = await _verify_release(repo_row, tag, assets, info, repo_dir)
        except _Rejected as exc:
            return False, str(exc)
        if repo_row["expected_package"] is None:
            db.update_repo_check(repo_id, last_error=repo_row["last_error"],
                                 expected_package=variants[0].info.name, signer_sha256=variants[0].signer)
        staged = _stage(repo_row, tag, release, variants, repo_dir)
        staging.prune_repo(repo_id)
        if not staged:
            return False, f"{tag} is already staged"
        return True, f"Staged {tag} ({staged} APK{'' if staged == 1 else 's'})"


async def _check_repo(repo_row, restage: bool = False) -> None:
    owner, repo, glob_pattern = repo_row["owner"], repo_row["repo"], repo_row["asset_glob"]
    label = f"{owner}/{repo}"

    try:
        release = await github_client.get_latest_release(
            owner, repo, github_token(), include_prereleases=bool(repo_row["include_prereleases"]),
        )
    except github_client.NoRelease:
        # Normal for a repo watched for its workflow artifacts until it
        # publishes a release; the identity is still checked (and pinned).
        if repo_row["github_id"] is None:
            await _check_identity(repo_row, label)
        db.update_repo_check(repo_row["id"], last_error=None)
        return
    except github_client.GithubError as exc:
        logger.warning("%s: %s", label, exc)
        db.update_repo_check(repo_row["id"], last_error=str(exc))
        return

    tag = release.get("tag_name")
    # Check now on a current release whose files were all deleted stages it again.
    restaging = restage and bool(tag) and tag == repo_row["last_tag"] and not db.has_staged_release(repo_row["id"], tag)
    new_release = bool(tag) and (restaging or tag not in (repo_row["last_tag"], repo_row["rejected_tag"]))
    # Only when something could be staged (plus once, to pin a repo added
    # before pinning existed): an unchanged repo costs one request per poll,
    # as before, and a name that changed hands is caught before any download.
    if new_release or repo_row["github_id"] is None:
        info = await _check_identity(repo_row, label)
        if info is None:
            return
    if not tag or (tag == repo_row["last_tag"] and not restaging):
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
    try:
        variants = await _verify_release(repo_row, tag, assets, info, repo_dir)
        first = variants[0]
    except _Rejected as exc:
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


TOKEN_EXPIRY_WARN_DAYS = 7


async def _warn_token_expiry() -> None:
    """One notification per token, a week before it expires."""
    expires = db.get_meta("github_token_expires")
    if not expires or token_source() != "settings" or db.get_meta("github_token_expiry_warned") == expires:
        return
    try:
        when = datetime.fromisoformat(expires)
    except ValueError:
        return
    days = (when - datetime.now(timezone.utc)).days
    if days >= TOKEN_EXPIRY_WARN_DAYS:
        return
    db.set_meta("github_token_expiry_warned", expires)
    await _notify("token_expiring", "GitHub token expires soon",
                  f"The GitHub token saved in Settings expires on {when.date().isoformat()}. "
                  "Create a new one under Settings → GitHub, or releases from private repos and "
                  "workflow artifacts stop working.")


async def poll_all_repos() -> None:
    await _warn_token_expiry()
    for repo_row in db.list_repos():
        try:
            await check_repo(repo_row)
        except Exception:
            # One repo failing in an unexpected way must not stop every repo
            # after it from being polled.
            logger.exception("%s/%s: poll crashed", repo_row["owner"], repo_row["repo"])
            db.update_repo_check(repo_row["id"], last_error="Internal error while checking — see server logs")
