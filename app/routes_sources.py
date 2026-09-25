"""Sources: watched repos review, confirm, remove, check now, re-pin a signer and APK uploads."""
import logging
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

import auth
import db
import github_client
import poller
import signing
import staging
import uploads
from uploads import UploadRejected, display_filename, new_upload_tmp, receive_upload, stage_received
from web import check_csrf, context, moved, record_audit, redirect, templates

logger = logging.getLogger("adb_server")
router = APIRouter()


# ---- sources: watched repos and uploads ----

CHECK_WAIT_SECONDS = 60


def _check_result(repo) -> dict:
    """The flash for a finished Check now, in plain words."""
    name = f"{repo['owner']}/{repo['repo']}"
    if repo["last_error"]:
        return {"error": f"{name}: {repo['last_error']}"}
    if not repo["last_tag"]:
        return {"ok": f"{name} has no releases yet, only workflow builds: stage one from Builds."}
    return {"ok": f"{name} checked: its latest release is {repo['last_tag']}."}


@router.get("/sources", response_class=HTMLResponse)
def sources_page(
    request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None,
    checking: int | None = None, since: str | None = None,
):
    """While a Check now runs (?checking=<repo>&since=<when it started>) the
    page reloads itself every 2 seconds with a meta refresh (no JavaScript
    here), then shows what the check found."""
    refresh = None
    since = since.replace(" ", "+") if since else since  # a "+" left unencoded in the URL arrives as a space
    if checking is not None and since:
        repo = db.get_repo(checking)
        try:
            started = datetime.fromisoformat(since)
            waited = (datetime.now(timezone.utc) - started).total_seconds()
        except ValueError:
            repo, waited = None, 0
        if repo is not None:
            if repo["last_checked_at"] and repo["last_checked_at"] >= since:
                flash = _check_result(repo)
                error, ok = flash.get("error"), flash.get("ok")
            elif waited < CHECK_WAIT_SECONDS:
                refresh = f"/sources?checking={int(checking)}&since={quote(since)}"
                ok = f"Checking {repo['owner']}/{repo['repo']}…"
            else:
                warn = "The check is taking a while (a large download?). This page will show the result when you reload it."
    return _sources_page(request, session, error=error, ok=ok, warn=warn, auto_refresh=refresh)


@router.get("/repos")
@router.get("/upload")
def old_sources_pages(request: Request, session: dict = Depends(auth.require_auth)):
    return moved(request, "/sources")


# Newer than this and a repo gets a "just created" warning on review: a
# look-alike of a real project is usually days old.
NEW_REPO_DAYS = 30


def _repo_warnings(info: github_client.RepoInfo, apk_kind: str) -> list[str]:
    warnings = []
    if info.fork:
        warnings.append("It's a fork. Forks are where look-alikes of real projects live — make sure this "
                        "is the one the developer publishes from.")
    if info.archived:
        warnings.append("It's archived: read-only, no new releases will come.")
    try:
        created = datetime.fromisoformat(info.created_at.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - created).days
        if age_days < NEW_REPO_DAYS:
            warnings.append(f"It was created {age_days} day{'' if age_days == 1 else 's'} ago.")
    except ValueError:
        warnings.append("GitHub didn't say when it was created.")
    if apk_kind == "artifact":
        warnings.append("No release has an APK yet, but its workflow artifacts do: test builds can be staged "
                        "from Builds now, and releases will be staged once one has an APK.")
    if info.owner_type != "User":
        warnings.append(f"It belongs to an organization, so release assets are only accepted when a workflow "
                        f"uploaded them ({github_client.ACTIONS_BOT}), never a member by hand.")
    return warnings


async def _apk_evidence(info: github_client.RepoInfo, asset_glob: str) -> tuple[str | None, str]:
    """Whether a repo has anything this app can install: a published release
    with an asset matching the glob, else (with a token) a recent workflow
    artifact whose zip lists an .apk. Returns (kind, reason); kind None
    means it has none, and the reason says why and what would change it."""
    token = poller.github_token()
    try:
        releases = await github_client.list_releases(info.owner, info.repo, token)
    except github_client.GithubError:
        releases = []
    if any(github_client.find_matching_assets(r, asset_glob) for r in releases):
        return "release", ""
    none_in_releases = (f"none of its releases has an asset matching '{asset_glob}'" if releases
                        else "it has no published releases")
    if not token:
        return None, (f"{info.owner}/{info.repo} has no APK to install: {none_in_releases}. Its workflow artifacts "
                      "can only be checked with a GitHub token (Settings → GitHub).")
    try:
        artifacts = await github_client.list_artifacts(info.owner, info.repo, info.id, token)
    except github_client.GithubError:
        artifacts = []
    candidates = [a for a in artifacts if not a.name.lower().endswith(".dockerbuild")][:3]
    for artifact in candidates:
        if await github_client.artifact_lists_apk(info.owner, info.repo, artifact.id, token):
            return "artifact", ""
    return None, (f"{info.owner}/{info.repo} has no APK to install: {none_in_releases}, and "
                  f"{'none of its recent workflow artifacts holds one' if candidates else 'its only workflow artifacts are Docker build records' if artifacts else 'it has no workflow artifacts'}. "
                  "ADB Server only watches repos that publish APKs.")


def _sources_page(request: Request, session: dict, **extra) -> HTMLResponse:
    uploaded = [a for a in db.list_staged_apks() if a["repo_id"] is None]
    # Each repo's most recent staged release, shown on its folded card's head.
    latest: dict[int, list] = {}
    for a in db.list_latest_variants():
        latest.setdefault(a["repo_id"], []).append(a)
    return templates.TemplateResponse(
        request, "sources.html",
        context(request, session, repos=db.list_repos(), latest=latest, uploads=uploaded,
              max_upload_mb=uploads.MAX_UPLOAD_BYTES // (1024 * 1024), **extra),
    )


@router.post("/repos", response_class=HTMLResponse)
async def review_repo(
    request: Request,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    repo_url: str = Form(...),
    asset_glob: str = Form("*.apk"),
    include_prereleases: str = Form(""),
):
    """Step one of adding a repo: look it up and show what GitHub says it is.
    Nothing is watched until the operator confirms that this is the repo
    they meant — the moment a look-alike name would otherwise slip through."""
    check_csrf(request, session, csrf_token)
    asset_glob = asset_glob.strip() or "*.apk"
    try:
        owner, repo = github_client.parse_repo_reference(repo_url)
        info = await github_client.get_repo_info(owner, repo, poller.github_token())
    except github_client.GithubError as exc:
        return redirect("/sources", error=str(exc))
    if db.get_repo_by_github_id(info.id) is not None:
        return redirect("/sources", error="That repo is already registered")
    kind, reason = await _apk_evidence(info, asset_glob)
    if kind is None:
        return redirect("/sources", error=reason)
    return _sources_page(request, session, review={
        "info": info, "asset_glob": asset_glob, "include_prereleases": include_prereleases == "1",
        "warnings": _repo_warnings(info, kind),
    })


@router.post("/repos/confirm")
async def confirm_repo(
    request: Request,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    owner: str = Form(...),
    repo: str = Form(...),
    github_id: int = Form(...),
    asset_glob: str = Form("*.apk"),
    include_prereleases: str = Form(""),
):
    """Step two: watch the repo that was reviewed, and pin its identity. It is
    looked up again, and refused if the name now points at a different repo
    than the one on the review page."""
    check_csrf(request, session, csrf_token)
    asset_glob = asset_glob.strip() or "*.apk"
    try:
        info = await github_client.get_repo_info(owner, repo, poller.github_token())
    except github_client.GithubError as exc:
        return redirect("/sources", error=str(exc))
    if info.id != github_id:
        return redirect("/sources", error="That name points at a different repo than the one you reviewed "
                                           "— nothing was added. Look it up again.")
    if db.get_repo_by_github_id(info.id) is not None:
        return redirect("/sources", error="That repo is already registered")
    kind, reason = await _apk_evidence(info, asset_glob)  # confirm can be posted without a review
    if kind is None:
        return redirect("/sources", error=reason)
    try:
        db.create_repo(info.owner, info.repo, asset_glob, include_prereleases == "1",
                       github_id=info.id, owner_id=info.owner_id, owner_type=info.owner_type)
    except sqlite3.IntegrityError:
        return redirect("/sources", error="That repo is already registered")
    record_audit(request, "repo_add", f"{info.owner}/{info.repo} id={info.id} owner={info.owner_type} glob={asset_glob}")
    return redirect("/sources", ok=f"Watching {info.owner}/{info.repo}")


@router.post("/repos/{repo_id}/delete")
def delete_repo(repo_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    check_csrf(request, session, csrf_token)
    repo_row = db.get_repo(repo_id)
    db.delete_repo(repo_id)
    staging.remove_repo_dir(repo_id)
    if repo_row is not None:
        record_audit(request, "repo_remove", f"{repo_row['owner']}/{repo_row['repo']}")
    return redirect("/sources", ok="Repo removed")


@router.post("/repos/{repo_id}/check-now")
def check_repo_now(
    repo_id: int, request: Request, background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
):
    """Runs the check after responding: a new release can mean downloading
    hundreds of MB, which shouldn't hold the browser's request open."""
    check_csrf(request, session, csrf_token)
    repo_row = db.get_repo(repo_id)
    if repo_row is None:
        raise HTTPException(status_code=404)
    # Check now also restages the current release if its files were deleted.
    background_tasks.add_task(poller.check_repo, repo_row, True)
    # The Sources page then reloads itself until this check has finished.
    return redirect("/sources", checking=repo_id, since=db.now())


@router.post("/repos/{repo_id}/prereleases")
def toggle_prereleases(
    repo_id: int, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), include: str = Form(...),
):
    check_csrf(request, session, csrf_token)
    repo_row = db.get_repo(repo_id)
    if repo_row is None:
        raise HTTPException(status_code=404)
    db.set_include_prereleases(repo_id, include == "1")
    record_audit(request, "repo_prereleases", f"{repo_row['owner']}/{repo_row['repo']} include={include == '1'}")
    return redirect("/sources", ok="Updated")


@router.post("/repos/{repo_id}/accept-signer")
def accept_signer(
    repo_id: int, request: Request, background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), confirm: str = Form(""),
):
    check_csrf(request, session, csrf_token)
    if confirm != "yes":
        return redirect("/sources", error="Tick the confirmation box to accept a new signer")
    before = db.get_repo(repo_id)
    if before is None or not db.accept_pending_signer(repo_id):
        return redirect("/sources", error="No pending signer change for that repo")
    logger.warning("repo %s: operator accepted a new signing certificate", repo_id)
    record_audit(
        request, "signer_accepted",
        f"{before['owner']}/{before['repo']} {before['signer_sha256']} -> {before['pending_signer']} "
        f"package={before['pending_package']} lineage_proven={bool(before['pending_lineage_ok'])}",
    )
    background_tasks.add_task(poller.check_repo, db.get_repo(repo_id))
    return redirect("/sources", ok="New signer pinned — re-checking the release now")


@router.post("/staged/upload")
def upload_apk(
    request: Request,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    apk: UploadFile = File(...),
    label: str = Form(""),
    sign_unsigned: str = Form(""),
):
    """Stages an APK the operator supplies directly. It has no upstream repo,
    so it neither reads nor writes a repo's package/signer pin — the operator
    is its provenance. The signature must still verify, and the signer is
    recorded so what got pushed stays auditable.

    Unlike a polled release, a debug-signed upload is allowed: staging a dev
    build is the point of uploading by hand. It is flagged and warned about,
    because a debug certificate is generated per machine and identifies nobody.

    A zip is accepted too — the artifact a workflow run hands back — as long
    as it holds exactly one APK; that APK is then held to every rule above.

    Sync on purpose: Starlette runs it in a threadpool, keeping apksigner and
    aapt2 off the event loop the poller shares."""
    check_csrf(request, session, csrf_token)
    tmp_path = new_upload_tmp()
    try:
        digest = receive_upload(apk, tmp_path)
    except UploadRejected as exc:
        staging.remove_file(tmp_path)
        return redirect("/sources", error=f"Upload refused: {exc}")
    except BaseException:
        staging.remove_file(tmp_path)
        raise
    return stage_received(request, tmp_path, digest, display_filename(apk.filename), label,
                           sign_as=signing.UPLOADS if sign_unsigned == "yes" else None)
