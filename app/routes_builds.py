"""A watched repo's Builds page: its releases and workflow artifacts, staged
on request, and its opt-in to signing unsigned builds."""
import asyncio
import fnmatch
import json
import logging
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

import apk_verify
import auth
import db
import github_client
import poller
import signing
import staging
import uploads
from uploads import VerifiedUpload, display_filename, new_upload_tmp, stage_received, unwrap_archive
from web import check_csrf, context, record_audit, redirect, templates

logger = logging.getLogger("adb_server")
router = APIRouter()


# ---- workflow artifacts: test builds straight from a watched repo ----

async def _pinned_repo(repo_id: int) -> tuple[sqlite3.Row, str | None]:
    """The repo row, and why its artifacts can't be fetched right now (or
    None). Same identity rule as the poller: the name must still be the repo
    that was pinned."""
    repo = db.get_repo(repo_id)
    if repo is None:
        raise HTTPException(status_code=404)
    if not poller.github_token():
        return repo, ("Fetching workflow artifacts needs a GitHub token: GitHub serves artifact downloads only to "
                      "an authenticated caller, even for a public repo. Add one under Settings → GitHub, "
                      "with Actions: read on this repo.")
    if repo["github_id"] is None:
        return repo, "This repo's GitHub ID isn't pinned yet. Press Check now on Sources first."
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


@router.get("/repos/{repo_id}/artifacts", response_class=HTMLResponse)
async def artifacts_page(
    repo_id: int, request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None, refresh: bool = False,
):
    if refresh:
        repo_row = db.get_repo(repo_id)
        if repo_row is not None:
            github_client.forget_cached(repo_row["owner"], repo_row["repo"])
    repo, problem = await _pinned_repo(repo_id)
    artifacts, releases, signing = [], [], {}
    token = poller.github_token()
    if problem is None:
        try:
            listed = await github_client.list_releases(repo["owner"], repo["repo"], token)
            staged_tags = {a["tag"] for a in db.list_staged_apks(repo_id)}
            releases = [{"id": r["id"], "tag": r["tag_name"], "name": r.get("name") or "",
                         "published": (r.get("published_at") or "")[:10], "prerelease": bool(r.get("prerelease")),
                         "apks": len(github_client.find_matching_assets(r, repo["asset_glob"])),
                         "staged": r["tag_name"] in staged_tags, "current": r["tag_name"] == repo["last_tag"]}
                        for r in listed]
            tags = {r["tag"] for r in releases}
            # A build of a release (its tag's run, or its tagged commit) is that
            # release: it arrives through Releases, with the release checks.
            release_shas = await github_client.release_commits(repo["owner"], repo["repo"], tags, token)
            flt = artifact_filter()
            shown = [a for a in await github_client.list_artifacts(repo["owner"], repo["repo"], repo["github_id"], token)
                     if artifact_shown(a, flt) and a.branch not in tags and a.head_sha not in release_shas]
            try:
                runs = await github_client.list_runs(repo["owner"], repo["repo"], token)
            except github_client.GithubError:
                runs = {}  # the list still works; it just can't say what each build is
            artifacts = _by_commit(shown, runs)
            signing = db.artifact_signing_map(repo_id)
        except github_client.GithubError as exc:
            problem = str(exc)
    return templates.TemplateResponse(
        request, "artifacts.html",
        context(request, session, repo=repo, artifacts=artifacts, releases=releases, problem=problem, signing=signing,
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


@router.post("/repos/{repo_id}/releases/{release_id}/stage")
async def stage_release(
    repo_id: int, release_id: int, request: Request,
    session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
):
    """Stages an older release of a watched repo, through every release check
    (uploader, signature, no debug builds, the pin)."""
    check_csrf(request, session, csrf_token)
    back = f"/repos/{int(repo_id)}/artifacts"
    if db.get_repo(repo_id) is None:
        raise HTTPException(status_code=404)
    ok, message = await poller.stage_past_release(repo_id, release_id)
    repo = db.get_repo(repo_id)
    record_audit(request, "release_stage" if ok else "release_stage_refused",
           f"{repo['owner']}/{repo['repo']} release={int(release_id)}: {message}")
    return redirect("/install" if ok else back, **({"ok": message} if ok else {"error": message}))


@router.post("/repos/{repo_id}/artifacts/{artifact_id}/stage")
async def stage_artifact(
    repo_id: int, artifact_id: int, request: Request,
    session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
):
    """Stages the APK inside a workflow artifact, for testing a build that
    isn't released. It is handled exactly like an uploaded zip (debug builds
    allowed and flagged, no repo pin read or written), with the repo, run,
    branch and commit it came from recorded as its provenance."""
    check_csrf(request, session, csrf_token)
    back = f"/repos/{int(repo_id)}/artifacts"
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
    try:
        notes = await github_client.get_build_notes(owner, name, artifact, token)
    except github_client.GithubError as exc:
        # Notes are a courtesy: the build still stages without them.
        logger.warning("build notes for %s/%s run %d: %s", owner, name, artifact.run_id, exc)
        notes = None
    sha = artifact.head_sha[:7]
    origin = f" from {owner}/{name} artifact {artifact.name} ({artifact.branch} @ {sha}, run {artifact.run_id})"
    label = f"{artifact.name} {artifact.branch}@{sha}"
    try:  # other builds of the same commit, for advice if this one is debug-signed
        listed = await github_client.list_artifacts(owner, name, repo["github_id"], token)
        siblings = [{"id": a.id, "name": a.name} for a in listed
                    if a.head_sha == artifact.head_sha and a.id != artifact.id
                    and not a.name.lower().endswith(".dockerbuild")][:10]
    except github_client.GithubError:
        siblings = []
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

    # apksigner and aapt2 block for seconds: keep them off the event loop.
    return await asyncio.to_thread(stage_received, request, tmp_path, digest,
                                   display_filename(f"{artifact.name}.zip"), label, origin, back, notes,
                                   provenance, sign_as, remember)


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
