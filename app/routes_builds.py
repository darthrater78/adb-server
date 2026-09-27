"""A watched repo's Builds page: its releases and workflow artifacts, staged
on request, and its opt-in to signing unsigned builds."""
import asyncio
import fnmatch
import json
import logging
import sqlite3

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

import apk_verify
import auth
import db
import github_client
import poller
import routes_install
import selection
import signing
import staging
import uploads
from uploads import VerifiedUpload, display_filename, new_upload_tmp, stage_received, unwrap_archive
from web import check_csrf, context, record_audit, redirect, templates

logger = logging.getLogger("adb_server")
router = APIRouter()


# ---- workflow artifacts: test builds straight from a watched repo ----

def _cannot_fetch(repo) -> str | None:
    """Why a repo's artifacts can't be fetched at all, before asking GitHub."""
    if not poller.github_token():
        return ("Fetching workflow artifacts needs a GitHub token: GitHub serves artifact downloads only to "
                "an authenticated caller, even for a public repo. Add one under Settings → GitHub, "
                "with Actions: read on this repo.")
    if repo["github_id"] is None:
        return "This repo's GitHub ID isn't pinned yet. Press Check now on Apps first."
    return None


async def _pinned_repo(repo_id: int) -> tuple[sqlite3.Row, str | None]:
    """The repo row, and why its artifacts can't be fetched right now (or
    None). Same identity rule as the poller: the name must still be the repo
    that was pinned."""
    repo = db.get_repo(repo_id)
    if repo is None:
        raise HTTPException(status_code=404)
    if problem := _cannot_fetch(repo):
        return repo, problem
    try:
        info = await github_client.get_repo_info(repo["owner"], repo["repo"], poller.github_token())
    except github_client.GithubError as exc:
        return repo, str(exc)
    return repo, poller.identity_problem(repo, info)


def _by_commit(artifacts: list[github_client.Artifact], runs: dict[int, dict]) -> list[dict]:
    """One entry per commit, newest first, each holding every artifact built
    from it (several workflows, or several outputs of one, often share one)."""
    groups: dict[str, dict] = {}
    for a in artifacts:  # newest first already
        g = groups.get(a.head_sha)
        if g is None:
            run = runs.get(a.run_id, {})
            g = groups[a.head_sha] = {"sha": a.head_sha, "branch": a.branch, "created": a.created_at,
                                      "subject": run.get("subject", ""), "message": run.get("message", ""),
                                      "artifacts": []}
        g["artifacts"].append({"a": a, "run": runs.get(a.run_id, {})})
    return list(groups.values())


def _staged_builds(repo_id: int, artifacts: list[github_client.Artifact]) -> dict[int, sqlite3.Row]:
    """The staged APK of each listed artifact that has one, by artifact id:
    it's the only place a test build's version is known."""
    staged = [a for a in db.list_staged_apks() if a["artifact_repo_id"] == repo_id]
    found = {}
    for art in artifacts:
        # A staged artifact is tagged "<artifact name> <branch>@<sha7>" (stage_artifact).
        match = next((a for a in staged if a["artifact_run_id"] == art.run_id
                      and a["tag"].startswith(f"{art.name} ")), None)
        if match is not None:
            found[art.id] = match
    return found


def _release_rows(repo, listed: list[dict]) -> list[dict]:
    """GitHub's releases as the Builds page lists them: what each is, how many
    APKs match the repo's glob, and whether it's staged or the current one."""
    staged_tags = {a["tag"] for a in db.list_staged_apks(repo["id"])}
    return [{"id": r["id"], "tag": r["tag_name"], "name": r.get("name") or "",
             "published": (r.get("published_at") or "")[:10], "prerelease": bool(r.get("prerelease")),
             "apks": len(github_client.find_matching_assets(r, repo["asset_glob"])),
             "staged": r["tag_name"] in staged_tags, "current": r["tag_name"] == repo["last_tag"],
             "notes": (r.get("body") or "").strip()}
            for r in listed]


@router.get("/repos/{repo_id}/artifacts", response_class=HTMLResponse)
async def artifacts_page(
    repo_id: int, request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None, refresh: bool = False,
):
    if refresh:
        repo_row = db.get_repo(repo_id)
        if repo_row is not None:
            github_client.forget_cached(repo_row["owner"], repo_row["repo"])
    repo = db.get_repo(repo_id)
    if repo is None:
        raise HTTPException(status_code=404)
    problem = _cannot_fetch(repo)
    artifacts, releases, signing, staged_builds = [], [], {}, {}
    token = poller.github_token()
    owner, name = repo["owner"], repo["repo"]
    if problem is None:
        # Two rounds of requests, each sent together. First: the identity
        # check (nothing is shown unless the name is still the pinned repo)
        # with the release and artifact lists.
        try:
            info, listed, all_artifacts = await asyncio.gather(
                github_client.get_repo_info(owner, name, token),
                github_client.list_releases(owner, name, token),
                github_client.list_artifacts(owner, name, repo["github_id"], token),
            )
            problem = poller.identity_problem(repo, info)
        except github_client.GithubError as exc:
            problem = str(exc)
    if problem is None:
        try:
            releases = _release_rows(repo, listed)
            tags = {r["tag"] for r in releases}
            flt = artifact_filter()
            candidates = [a for a in all_artifacts if artifact_shown(a, flt) and a.branch not in tags]
            # Second: which commits are releases' (a build of a release arrives
            # through Releases, with the release checks), and the runs that
            # built the candidates, so each can say what it is.
            release_shas, runs = await asyncio.gather(
                github_client.release_commits(owner, name, tags, token),
                github_client.get_runs(owner, name, {a.run_id for a in candidates}, token),
            )
            shown = [a for a in candidates if a.head_sha not in release_shas]
            artifacts = _by_commit(shown, runs)
            signing = db.artifact_signing_map(repo_id)
            staged_builds = _staged_builds(repo_id, shown)
        except github_client.GithubError as exc:
            problem = str(exc)
    return templates.TemplateResponse(
        request, "artifacts.html",
        context(request, session, repo=repo, artifacts=artifacts, releases=releases, problem=problem, signing=signing,
                staged_builds=staged_builds,
                devices=[d for d in db.list_devices() if d["trusted"]],
                max_mb=uploads.MAX_UPLOAD_BYTES // (1024 * 1024), error=error, ok=ok, warn=warn),
    )


@router.post("/repos/{repo_id}/sign-unsigned")
def set_sign_unsigned(
    repo_id: int, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), on: str = Form(...), understood: str = Form(""),
):
    """Per-repo opt-in to signing its unsigned builds (releases and artifacts)
    with this server's key. Turning it on needs the explanation acknowledged."""
    check_csrf(request, session, csrf_token)
    repo = db.get_repo(repo_id)
    if repo is None:
        raise HTTPException(status_code=404)
    turn_on = on == "1"
    if turn_on and understood != "yes":
        return redirect(f"/repos/{int(repo_id)}/artifacts#signing",
                         error="Tick that you understand what signing with this server's key means first")
    db.set_sign_unsigned(repo_id, turn_on)
    record_audit(request, "sign_unsigned_on" if turn_on else "sign_unsigned_off", f"{repo['owner']}/{repo['repo']}")
    return redirect(f"/repos/{int(repo_id)}/artifacts#signing",
                     ok=("Unsigned builds from this repo will be signed with this server's key for this repo" if turn_on
                         else "Unsigned builds from this repo will be refused again"))


def _push_target(push_to: str):
    """The device a Stage and install names, or None for Stage only."""
    if not push_to:
        return None
    device = db.get_device(push_to)
    if device is None:
        raise HTTPException(status_code=404)
    return device


@router.post("/repos/{repo_id}/releases/{release_id}/stage")
async def stage_release(
    repo_id: int, release_id: int, request: Request, background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth), csrf_token: str = Form(...), push_to: str = Form(""),
):
    """Stages an older release of a watched repo, through every release check
    (uploader, signature, no debug builds, the pin). With `push_to`, it then
    installs it on that device, the build for its CPU."""
    check_csrf(request, session, csrf_token)
    back = f"/repos/{int(repo_id)}/artifacts"
    if db.get_repo(repo_id) is None:
        raise HTTPException(status_code=404)
    device = _push_target(push_to)
    ok, message = await poller.stage_past_release(repo_id, release_id)
    repo = db.get_repo(repo_id)
    record_audit(request, "release_stage" if ok else "release_stage_refused",
           f"{repo['owner']}/{repo['repo']} release={int(release_id)}: {message}")
    if not ok:
        return redirect(back, error=message)
    # What was just staged: the repo's newest rows, which share one tag.
    staged = db.list_staged_apks(repo_id)
    newest = max(staged, key=lambda a: a["id"])
    if device is None:
        return routes_install.library_landing(newest, ok=message)
    apk = selection.pick_variant([a for a in staged if a["tag"] == newest["tag"]], device["abis"])
    if apk is None:
        return routes_install.library_landing(newest, error=f"{message}, but no build of it fits {device['nickname'] or device['serial']}'s CPU")
    return routes_install.queue_push(request, background_tasks, device, apk, "/status", ok=message)


@router.post("/repos/{repo_id}/artifacts/{artifact_id}/stage")
async def stage_artifact(
    repo_id: int, artifact_id: int, request: Request, background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth), csrf_token: str = Form(...), push_to: str = Form(""),
):
    """Stages the APK inside a workflow artifact, for testing a build that
    isn't released. It is handled exactly like an uploaded zip (debug builds
    allowed and flagged, no repo pin read or written), with the repo, run,
    branch and commit it came from recorded as its provenance. With
    `push_to`, it then installs it on that device."""
    check_csrf(request, session, csrf_token)
    back = f"/repos/{int(repo_id)}/artifacts"
    device = _push_target(push_to)
    repo, problem = await _pinned_repo(repo_id)
    if problem:
        return redirect(back, error=problem)
    owner, name, token = repo["owner"], repo["repo"], poller.github_token()
    tmp_path = new_upload_tmp()
    try:
        artifact = await github_client.get_artifact(owner, name, artifact_id, repo["github_id"], token)
        digest = await github_client.download_artifact(owner, name, artifact.id, tmp_path, token)
    except github_client.GithubError as exc:
        staging.remove_file(tmp_path)
        return redirect(back, error=f"Artifact refused: {exc}")
    except BaseException:
        staging.remove_file(tmp_path)
        raise
    async def notes_or_none() -> str | None:
        try:
            return await github_client.get_build_notes(owner, name, artifact, token)
        except github_client.GithubError as exc:
            # Notes are a courtesy: the build still stages without them.
            logger.warning("build notes for %s/%s run %d: %s", owner, name, artifact.run_id, exc)
            return None

    async def siblings_or_none() -> list[dict]:
        try:  # other builds of the same commit, for advice if this one is debug-signed
            listed = await github_client.list_artifacts(owner, name, repo["github_id"], token)
        except github_client.GithubError:
            return []
        return [{"id": a.id, "name": a.name} for a in listed
                if a.head_sha == artifact.head_sha and a.id != artifact.id
                and not a.name.lower().endswith(".dockerbuild")][:10]

    notes, siblings = await asyncio.gather(notes_or_none(), siblings_or_none())
    sha = artifact.head_sha[:7]
    origin = f" from {owner}/{name} artifact {artifact.name} ({artifact.branch} @ {sha}, run {artifact.run_id})"
    label = f"{artifact.name} {artifact.branch}@{sha}"
    marker = f"Commit {artifact.head_sha[:7]}:\n"  # get_build_notes puts the commit message after this
    subject = notes.split(marker, 1)[1].split("\n", 1)[0] if notes and marker in notes else ""
    provenance = {"repo": f"{owner}/{name}", "run_id": artifact.run_id, "branch": artifact.branch,
                  "sha": artifact.head_sha, "subject": subject[:200], "repo_id": int(repo_id),
                  "siblings": json.dumps(siblings)}

    # The repo's own key, so a test build can update its release, never another source's app.
    sign_as = signing.repo_source(repo["github_id"]) if repo["sign_unsigned"] else None

    def remember(verified: VerifiedUpload) -> None:
        kind = "unsigned" if verified.server_signed else "debug" if verified.signer.debug else "signed"
        db.record_artifact_signing(artifact.id, repo_id, kind,
                                   None if verified.server_signed else verified.signer.fingerprint)

    def then(apk_id: int, flash: dict):
        return routes_install.queue_push(request, background_tasks, device, db.get_staged_apk(apk_id), "/status",
                                         **flash)

    # apksigner and aapt2 block for seconds: keep them off the event loop.
    return await asyncio.to_thread(stage_received, request, tmp_path, digest,
                                   display_filename(f"{artifact.name}.zip"), label, origin, back, notes,
                                   provenance, sign_as, remember, then if device is not None else None)


SIGNING_LABELS = {"signed": "signed with a real key", "debug": "signed with a debug key",
                  "unsigned": "unsigned", "invalid": "not a valid APK build"}


def _inspect_signing(tmp_path: str) -> tuple[str, str | None, str]:
    """What a downloaded artifact is signed with: (kind, signer, detail)."""
    try:
        unwrap_archive(tmp_path)
        apk_verify.assert_apk_container(tmp_path)
        if apk_verify.is_unsigned(tmp_path):
            return "unsigned", None, ""
        signer = apk_verify.verify_signature(tmp_path)
    except apk_verify.ApkVerifyError as exc:
        return "invalid", None, str(exc)
    return ("debug" if signer.debug else "signed"), signer.fingerprint, ""


@router.post("/repos/{repo_id}/artifacts/{artifact_id}/check")
async def check_artifact_signing(
    repo_id: int, artifact_id: int, request: Request,
    session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
):
    """Downloads a test build only to see how it's signed, then deletes it.
    The answer is kept: an artifact never changes."""
    check_csrf(request, session, csrf_token)
    back = f"/repos/{int(repo_id)}/artifacts"
    repo, problem = await _pinned_repo(repo_id)
    if problem:
        return redirect(back, error=problem)
    token = poller.github_token()
    tmp_path = new_upload_tmp()
    try:
        artifact = await github_client.get_artifact(repo["owner"], repo["repo"], artifact_id, repo["github_id"], token)
        await github_client.download_artifact(repo["owner"], repo["repo"], artifact.id, tmp_path, token)
        kind, signer, detail = await asyncio.to_thread(_inspect_signing, tmp_path)
    except github_client.GithubError as exc:
        return redirect(back, error=f"Couldn't check it: {exc}")
    finally:
        staging.remove_file(tmp_path)
    db.record_artifact_signing(artifact.id, repo_id, kind, signer, detail)
    return redirect(f"{back}#artifact-{artifact.id}", ok=f"{artifact.name}: {SIGNING_LABELS[kind]}")


def artifact_filter() -> dict:
    return {"hide_dockerbuild": db.get_meta("artifacts_hide_dockerbuild") != "0",
            "name_glob": db.get_meta("artifacts_name_glob") or ""}


def artifact_shown(artifact: github_client.Artifact, flt: dict) -> bool:
    name = artifact.name.lower()
    # docker/build-push-action uploads a build record named <owner>~<repo>~<id>.dockerbuild.
    if flt["hide_dockerbuild"] and name.endswith(".dockerbuild"):
        return False
    return not flt["name_glob"] or fnmatch.fnmatch(name, flt["name_glob"].lower())
