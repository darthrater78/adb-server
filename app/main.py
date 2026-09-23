import hashlib
import logging
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from urllib.parse import quote

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.trustedhost import TrustedHostMiddleware

import adb_client
import apk_verify
import appearance
import audit
import auth
import db
import discovery
import github_client
import mfa
import notify
import poller
import pushes
import selection
import staging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("adb_server")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
# The container's own healthcheck calls http://127.0.0.1:8080/healthz. A
# literal loopback IP can't be used for DNS rebinding (that needs an
# attacker-controlled hostname), so allowing it keeps the protection intact.
if "127.0.0.1" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("127.0.0.1")
POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "10"))
MAX_UPLOAD_BYTES = apk_verify.MAX_APK_BYTES
# Multipart boundaries and the csrf/label fields on top of the file itself.
UPLOAD_FORM_OVERHEAD = 64 * 1024
# Every other form in the app is a handful of short fields.
MAX_FORM_BYTES = 64 * 1024
UPLOAD_PATH = "/staged/upload"
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _read_version() -> str:
    # Next to this file in the image (the Dockerfile copies it there); the
    # repo root when running from a checkout.
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "VERSION"), os.path.join(here, os.pardir, "VERSION")):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            continue
    return "unknown"


APP_VERSION = _read_version()
REPO_URL = "https://github.com/darthrater78/adb-server"
RELEASE_NOTES_URL = f"{REPO_URL}/releases/tag/v{APP_VERSION}"

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    interrupted = db.fail_interrupted_installs()
    if interrupted:
        logger.warning("marked %d install(s) interrupted by a restart as failed", interrupted)
    # Starlette spools multipart file parts through tempfile, and the
    # container's /tmp is a small tmpfs (memory). Point tempfile at the data
    # volume so a large APK upload isn't held in RAM. Assigning tempdir is the
    # documented override and beats TMPDIR, which gettempdir() caches.
    spool_dir = os.path.join(os.path.dirname(db.DB_PATH), "tmp")
    os.makedirs(spool_dir, exist_ok=True)
    tempfile.tempdir = spool_dir
    scheduler.add_job(
        poller.poll_all_repos, "interval",
        minutes=POLL_INTERVAL_MINUTES, id="poll_all_repos",
        max_instances=1, coalesce=True,
    )
    scheduler.start()
    logger.info("Polling every %s minutes", POLL_INTERVAL_MINUTES)
    # TrustedHostMiddleware answers a bare 400 to an unlisted Host, which looks
    # like the app is broken rather than configured. Log the list.
    logger.info("Accepting requests for hosts: %s (set ALLOWED_HOSTS to add your LAN name or IP)",
                ", ".join(ALLOWED_HOSTS))
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _when(value: str | None, empty: str = "never") -> Markup:
    """Renders a stored UTC ISO timestamp as a short, readable <time>, keeping
    the exact value in the tooltip and the machine-readable attribute."""
    if not value:
        return Markup('<span class="muted">{}</span>').format(empty)
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return Markup("{}").format(value)
    year = "" if at.year == datetime.now(timezone.utc).year else f" {at.year}"
    label = f"{at:%b} {at.day}{year}, {at:%H:%M} UTC"
    return Markup('<time datetime="{0}" title="{0}">{1}</time>').format(value, label)


templates.env.filters["when"] = _when


@app.middleware("http")
async def request_body_guard(request: Request, call_next):
    """FastAPI reads and parses a request's whole body — spooling any file
    part to disk, unbounded — before a route's auth dependency runs. So body
    limits have to be enforced here, before anything is read, or anyone who
    can reach the port can fill the volume without logging in.

    - Every POST must declare its length (no chunked bodies to meter).
    - Only the upload route accepts multipart, and only from a signed-in
      session; every other route takes a small urlencoded form."""
    if request.method != "POST":
        return await call_next(request)
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        return JSONResponse({"detail": "Content-Length required"}, status_code=411)
    if request.url.path == UPLOAD_PATH:
        if auth.read_session(request) is None:
            return RedirectResponse("/login", status_code=303)
        limit = MAX_UPLOAD_BYTES + UPLOAD_FORM_OVERHEAD
    else:
        if request.headers.get("content-type", "").lower().startswith("multipart/"):
            return JSONResponse({"detail": "Unsupported form encoding"}, status_code=415)
        limit = MAX_FORM_BYTES
    if int(length) > limit:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'none'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return response


VALID_THEMES = {"flashbang", "dark", "oled"}
# Exact allow-list, not a prefix/startswith check — the "next" field on the
# theme form is client-supplied, and an open redirect is exactly what a
# permissive check here would hand an attacker.
KNOWN_NAV_PATHS = {"/status", "/sources", "/devices", "/install", "/settings", "/installs", "/audit"}
# Pages reached from Settings rather than the top bar highlight Settings.
NAV_SECTION = {"/installs": "/settings", "/audit": "/settings"}


def _get_theme(request: Request) -> str:
    theme = request.cookies.get("theme", "")
    return theme if theme in VALID_THEMES else "auto"


def _tctx(request: Request, session: dict | None = None, **extra) -> dict:
    ctx = {
        "app_version": APP_VERSION,
        "repo_url": REPO_URL,
        "release_notes_url": RELEASE_NOTES_URL,
        # Changes whenever the accent does, so browsers refetch /accent.css.
        "accent_version": "".join((db.get_meta(k) or "") for k in ("accent_color", "accent2_color")).replace("#", "")
                          or "default",
        "theme": _get_theme(request),
        "current_path": request.url.path if request.url.path in KNOWN_NAV_PATHS else "/status",
        "nav_path": NAV_SECTION.get(request.url.path, request.url.path),
    }
    if session is not None:
        ctx["csrf_token"] = session.get("csrf", "")
    ctx.update(extra)
    return ctx


def _check_csrf(request: Request, session: dict, csrf_token: str | None) -> None:
    auth.require_csrf(request, session, csrf_token)


def _client(request: Request) -> str | None:
    return request.client.host if request.client else None


def _audit(request: Request, action: str, detail: str = "") -> None:
    audit.record(action, detail, _client(request))


def _redirect(path: str, status_code: int = 303, **params) -> RedirectResponse:
    path, hash_, fragment = path.partition("#")
    if params:
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items() if v is not None)
        path = f"{path}?{qs}" if qs else path
    return RedirectResponse(path + hash_ + fragment, status_code=status_code)


# ---- auth ----

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if auth.read_session(request):
        return RedirectResponse("/status", status_code=303)
    return templates.TemplateResponse(request, "login.html", _tctx(request))


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    # No session exists yet, so there is no CSRF token to compare — the Origin
    # check is what stops a cross-site form from driving this endpoint.
    auth.check_origin(request)
    auth.check_rate_limit(request)
    if not auth.verify_credentials(username, password):
        auth.record_failed_attempt(request)
        _audit(request, "login_failed")
        return templates.TemplateResponse(
            request, "login.html", _tctx(request, error="Invalid username or password"), status_code=401,
        )
    if mfa.enabled() and not mfa.browser_trusted(auth.read_mfa_trust(request)):
        response = RedirectResponse("/login/mfa", status_code=303)
        auth.start_mfa(response)
        return response
    response = RedirectResponse("/status", status_code=303)
    auth.create_session(response)
    _audit(request, "login", "trusted browser, no code asked" if mfa.enabled() else "")
    return response


def _mfa_form(request: Request, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, "login_mfa.html", _tctx(
        request, error=error, trust_days=mfa.TRUST_DAYS,
    ), status_code=status_code)


def _locked_message() -> str:
    minutes = max(1, -(-mfa.locked_for() // 60))
    return (f"Too many wrong codes — two-factor sign-in is locked for {minutes} more minute"
            f"{'s' if minutes != 1 else ''}. Or unlock it on the server: "
            "docker compose exec app python mfa_admin.py unlock")


@app.get("/login/mfa", response_class=HTMLResponse)
def login_mfa_form(request: Request):
    if not auth.mfa_pending(request):
        return RedirectResponse("/login", status_code=303)
    if mfa.locked_for():
        return _mfa_form(request, _locked_message(), 429)
    return _mfa_form(request)


@app.post("/login/mfa")
def login_mfa_submit(request: Request, code: str = Form(...), trust: str = Form("")):
    """The second step: an authenticator code or a recovery code, after the
    password (proven by the short-lived pending cookie)."""
    auth.check_origin(request)
    auth.check_rate_limit(request)
    if not auth.mfa_pending(request) or not mfa.enabled():
        return RedirectResponse("/login", status_code=303)
    if mfa.locked_for():
        return _mfa_form(request, _locked_message(), 429)
    kind = mfa.check(code)
    if kind is None:
        auth.record_failed_attempt(request)
        _audit(request, "mfa_failed")
        if mfa.locked_for():
            _audit(request, "mfa_locked", f"{mfa.MAX_FAILURES} wrong codes")
            return _mfa_form(request, _locked_message(), 429)
        return _mfa_form(request, "That code didn't work — check the time on your phone, and try the newest code",
                         401)
    ok = None
    if kind == "recovery":
        left = db.recovery_codes_left()
        ok = (f"Signed in with a recovery code — {left} left. "
              + ("Make new ones in Settings → Security." if left <= 3 else ""))
    response = _redirect("/status", ok=ok)
    auth.create_session(response)
    auth.clear_mfa_pending(response)
    if trust:
        label = mfa.browser_label(request.headers.get("user-agent", ""))
        auth.set_mfa_trust(response, mfa.trust_browser(label, _client(request)))
    _audit(request, "login", "with a recovery code" if kind == "recovery" else "with an authenticator code"
           + (f", trusted for {mfa.TRUST_DAYS} days" if trust else ""))
    return response


@app.post("/logout")
def logout(request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    _audit(request, "logout")
    # Signed cookies can't be recalled, so logging out invalidates every
    # session issued so far — a copied cookie stops working too.
    auth.revoke_sessions()
    response = RedirectResponse("/login", status_code=303)
    auth.clear_session(response)
    return response


@app.get("/")
def index():
    return RedirectResponse("/status", status_code=303)


@app.get("/healthz")
def healthz():
    """Unauthenticated liveness check for Docker. Reveals nothing but ok/not."""
    try:
        db.ping()
    except sqlite3.Error:
        return JSONResponse({"status": "error"}, status_code=503)
    return {"status": "ok"}


# ---- theme ----

@app.post("/theme")
def set_theme(
    request: Request,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    theme: str = Form(...),
    next: str = Form("/status"),
):
    _check_csrf(request, session, csrf_token)
    if theme not in VALID_THEMES:
        raise HTTPException(status_code=400, detail="Unknown theme")
    next_path = next if next in KNOWN_NAV_PATHS else "/status"
    response = RedirectResponse(next_path, status_code=303)
    # Cosmetic preference, not session state — plain cookie, not httponly, so
    # it stays simple and separate from the signed auth session.
    response.set_cookie(
        "theme", theme, max_age=365 * 24 * 60 * 60,
        samesite="lax", secure=auth.COOKIE_SECURE, path="/",
    )
    return response


def _moved(request: Request, path: str) -> RedirectResponse:
    """Old page URLs from before 3.1 land on the page that replaced them,
    flash message and all."""
    query = request.url.query
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=303)


# ---- sources: watched repos and uploads ----

@app.get("/sources", response_class=HTMLResponse)
def sources_page(
    request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None,
):
    uploads = [a for a in db.list_staged_apks() if a["repo_id"] is None]
    return templates.TemplateResponse(
        request, "sources.html",
        _tctx(request, session, repos=db.list_repos(), uploads=uploads, error=error, ok=ok, warn=warn,
              max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024)),
    )


@app.get("/repos")
@app.get("/upload")
def old_sources_pages(request: Request, session: dict = Depends(auth.require_auth)):
    return _moved(request, "/sources")


@app.post("/repos")
def create_repo(
    request: Request,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    repo_url: str = Form(...),
    asset_glob: str = Form("*.apk"),
    include_prereleases: str = Form(""),
):
    _check_csrf(request, session, csrf_token)
    asset_glob = asset_glob.strip() or "*.apk"
    try:
        owner, repo = github_client.parse_repo_reference(repo_url)
    except github_client.GithubError as exc:
        return _redirect("/sources", error=str(exc))
    try:
        db.create_repo(owner, repo, asset_glob, include_prereleases == "1")
    except sqlite3.IntegrityError:
        return _redirect("/sources", error="That repo is already registered")
    _audit(request, "repo_add", f"{owner}/{repo} glob={asset_glob}")
    return _redirect("/sources", ok="Repo added")


@app.post("/repos/{repo_id}/delete")
def delete_repo(repo_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    repo_row = db.get_repo(repo_id)
    db.delete_repo(repo_id)
    staging.remove_repo_dir(repo_id)
    if repo_row is not None:
        _audit(request, "repo_remove", f"{repo_row['owner']}/{repo_row['repo']}")
    return _redirect("/sources", ok="Repo removed")


@app.post("/repos/{repo_id}/check-now")
def check_repo_now(
    repo_id: int, request: Request, background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
):
    """Runs the check after responding: a new release can mean downloading
    hundreds of MB, which shouldn't hold the browser's request open."""
    _check_csrf(request, session, csrf_token)
    repo_row = db.get_repo(repo_id)
    if repo_row is None:
        raise HTTPException(status_code=404)
    background_tasks.add_task(poller.check_repo, repo_row)
    return _redirect("/sources", ok="Check started — reload this page in a moment to see the result")


@app.post("/repos/{repo_id}/prereleases")
def toggle_prereleases(
    repo_id: int, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), include: str = Form(...),
):
    _check_csrf(request, session, csrf_token)
    repo_row = db.get_repo(repo_id)
    if repo_row is None:
        raise HTTPException(status_code=404)
    db.set_include_prereleases(repo_id, include == "1")
    _audit(request, "repo_prereleases", f"{repo_row['owner']}/{repo_row['repo']} include={include == '1'}")
    return _redirect("/sources", ok="Updated")


@app.post("/repos/{repo_id}/accept-signer")
def accept_signer(
    repo_id: int, request: Request, background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), confirm: str = Form(""),
):
    _check_csrf(request, session, csrf_token)
    if confirm != "yes":
        return _redirect("/sources", error="Tick the confirmation box to accept a new signer")
    before = db.get_repo(repo_id)
    if before is None or not db.accept_pending_signer(repo_id):
        return _redirect("/sources", error="No pending signer change for that repo")
    logger.warning("repo %s: operator accepted a new signing certificate", repo_id)
    _audit(
        request, "signer_accepted",
        f"{before['owner']}/{before['repo']} {before['signer_sha256']} -> {before['pending_signer']} "
        f"package={before['pending_package']} lineage_proven={bool(before['pending_lineage_ok'])}",
    )
    background_tasks.add_task(poller.check_repo, db.get_repo(repo_id))
    return _redirect("/sources", ok="New signer pinned — re-checking the release now")


# ---- install: staged apks, ready to push ----

def _install_groups() -> list[dict]:
    """Staged APKs by app: one group per repo (its releases newest first,
    each release's CPU variants together) and one per uploaded APK."""
    groups: list[dict] = []
    by_repo: dict[int, dict] = {}
    for a in db.list_staged_apks():  # newest first
        if a["repo_id"] is None:
            # An upload's tag is its label (or version, or filename).
            groups.append({"repo_id": None, "label": a["tag"], "releases": [{"tag": a["tag"], "apks": [a]}]})
            continue
        group = by_repo.get(a["repo_id"])
        if group is None:
            group = by_repo[a["repo_id"]] = {"repo_id": a["repo_id"], "label": a["source_label"], "releases": []}
            groups.append(group)
        if not group["releases"] or group["releases"][-1]["tag"] != a["tag"]:
            group["releases"].append({"tag": a["tag"], "apks": []})
        group["releases"][-1]["apks"].append(a)
    # Watched repos first, alphabetically; uploads after them, newest first.
    return sorted(groups, key=lambda g: (g["repo_id"] is None, g["label"].lower() if g["repo_id"] else ""))


@app.get("/install", response_class=HTMLResponse)
def install_page(
    request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None,
):
    trusted = [d for d in db.list_devices() if d["trusted"]]
    return templates.TemplateResponse(
        request, "install.html",
        _tctx(request, session, groups=_install_groups(), devices=trusted, error=error, ok=ok, warn=warn),
    )


@app.get("/staged")
def old_staged_page(request: Request, session: dict = Depends(auth.require_auth)):
    return _moved(request, "/install")


class UploadRejected(Exception):
    """Something the operator can fix, shown as a flash message."""


def _upload_dir() -> str:
    # Inside STAGING_ROOT, so staging.remove_file's containment check covers it.
    return os.path.join(staging.STAGING_ROOT, "uploads")


def _display_filename(raw: str | None) -> str:
    """The client-supplied filename is display text and nothing else — the
    stored path is always uploads/<sha256>.apk. Reduced to a basename and a
    conservative character set so it can't be read as a path anywhere it is
    later rendered, logged or copied."""
    name = os.path.basename((raw or "").replace("\\", "/").strip())
    name = _UNSAFE_NAME_CHARS.sub("_", name).lstrip(".")[:120]
    return name or "upload.apk"


def _receive_upload(upload: UploadFile, tmp_path: str) -> str:
    """Copies the upload to tmp_path under the size cap in chunks, so a large
    APK never sits in memory. Returns its sha256."""
    hasher = hashlib.sha256()
    total = 0
    with open(tmp_path, "wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise UploadRejected(f"APK exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB size cap")
            hasher.update(chunk)
            out.write(chunk)
    if total == 0:
        raise UploadRejected("That file was empty — nothing was uploaded")
    return hasher.hexdigest()


def _verify_upload(upload: UploadFile, tmp_path: str) -> tuple[str, apk_verify.SignerInfo, apk_verify.PackageInfo]:
    digest = _receive_upload(upload, tmp_path)
    apk_verify.assert_apk_container(tmp_path)
    return digest, apk_verify.verify_signature(tmp_path), apk_verify.get_package_info(tmp_path)


def _upload_warnings(info: apk_verify.PackageInfo, signer: apk_verify.SignerInfo) -> list[str]:
    warnings = []
    if signer.debug:
        warnings.append("It's signed with the default Android debug certificate, which is generated "
                        "per machine and identifies nobody — only push it to a device you're testing on.")
    # Android refuses an update signed by a different key, so installing this
    # would block that repo's polled releases on the device (or the reverse).
    clashing = [f"{r['owner']}/{r['repo']}" for r in db.list_repos()
                if r["expected_package"] == info.name and r["signer_sha256"] != signer.fingerprint]
    if clashing:
        warnings.append(f"Its package matches {', '.join(clashing)} but its signer doesn't — a device "
                        "with one installed will refuse the other until it's uninstalled.")
    return warnings


@app.post("/staged/upload")
def upload_apk(
    request: Request,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    apk: UploadFile = File(...),
    label: str = Form(""),
):
    """Stages an APK the operator supplies directly. It has no upstream repo,
    so it neither reads nor writes a repo's package/signer pin — the operator
    is its provenance. The signature must still verify, and the signer is
    recorded so what got pushed stays auditable.

    Unlike a polled release, a debug-signed upload is allowed: staging a dev
    build is the point of uploading by hand. It is flagged and warned about,
    because a debug certificate is generated per machine and identifies nobody.

    Sync on purpose: Starlette runs it in a threadpool, keeping apksigner and
    aapt off the event loop the poller shares."""
    _check_csrf(request, session, csrf_token)
    display_name = _display_filename(apk.filename)
    upload_dir = _upload_dir()
    os.makedirs(upload_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=upload_dir, prefix="_upload_")
    os.close(fd)
    try:
        digest, signer, info = _verify_upload(apk, tmp_path)
    except (UploadRejected, apk_verify.ApkVerifyError) as exc:
        staging.remove_file(tmp_path)
        logger.warning("upload rejected (%s): %s", display_name, exc)
        return _redirect("/sources", error=f"Upload refused: {exc}")
    except BaseException:
        staging.remove_file(tmp_path)
        raise

    existing = db.get_uploaded_apk_by_sha256(digest)
    if existing is not None:
        staging.remove_file(tmp_path)
        return _redirect("/sources", error=f"That exact APK is already staged as \"{existing['filename']}\"")

    final_path = os.path.join(upload_dir, f"{digest}.apk")
    os.replace(tmp_path, final_path)
    apk_id = db.insert_staged_apk(
        repo_id=None, tag=(label.strip() or info.version_name or display_name)[:120],
        filename=display_name, sha256=digest, package_name=info.name,
        signer_sha256=signer.fingerprint, path=final_path,
        version_code=info.version_code, version_name=info.version_name,
        abis=" ".join(info.abis), source="upload", is_debug=signer.debug,
    )
    if apk_id is None:
        # A concurrent upload of the same file won; final_path is its file too.
        return _redirect("/sources", error="That exact APK is already staged")

    _audit(request, "upload", f"{display_name} {info.name} sha256={digest[:12]} debug={signer.debug}")
    staged = f"Staged {display_name} ({info.name})."
    warnings = _upload_warnings(info, signer)
    if warnings:
        return _redirect("/install", warn=" ".join([staged, *warnings]))
    return _redirect("/install", ok=staged)


@app.post("/staged/{apk_id}/delete")
def delete_staged(apk_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    apk = db.get_staged_apk(apk_id)
    if apk is None:
        raise HTTPException(status_code=404)
    staging.remove_file(apk["path"])
    db.mark_apk_pruned(apk_id)
    _audit(request, "staged_delete", f"{apk['source_label']} {apk['tag']} {apk['filename']}")
    return _redirect("/install", ok="Staged file deleted")


# ---- devices ----

@app.get("/devices", response_class=HTMLResponse)
def devices_page(request: Request, session: dict = Depends(auth.require_auth), error: str | None = None, ok: str | None = None):
    return templates.TemplateResponse(
        request, "devices.html", _tctx(request, session, devices=db.list_devices(), error=error, ok=ok),
    )


@app.post("/devices/pair")
def pair_device(
    request: Request,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    pairing_addr: str = Form(...),
    pairing_code: str = Form(...),
    connect_addr: str = Form(...),
):
    _check_csrf(request, session, csrf_token)
    addr = connect_addr.strip()
    try:
        adb_client.pair(pairing_addr.strip(), pairing_code.strip())
        adb_client.connect(addr)
        serial = adb_client.get_serialno(addr)
    except adb_client.AdbError as exc:
        return _redirect("/devices", error=str(exc))
    return _register_paired(request, serial, addr)


def _register_paired(request: Request, serial: str, addr: str) -> RedirectResponse:
    """Records a just-paired, connected device."""
    adopted = None
    if db.get_device(serial) is None:
        # Re-pairing a phone recorded before 1.0 under its old ip:port: carry
        # its nickname and history over rather than adding a twin. Not its
        # trust: sharing an IP doesn't prove it's the same phone (DHCP may
        # have handed the address on), and new devices start untrusted.
        ip = adb_client.split_host_port(addr)[0]
        for old in db.list_devices():
            if discovery.is_legacy_serial(old["serial"]) and adb_client.split_host_port(old["serial"])[0] == ip:
                if db.rename_device(old["serial"], serial):
                    adopted = old["serial"]
                    db.set_device_trusted(serial, False)
                break
    db.upsert_paired_device(serial, addr)
    pushes.refresh_abis(serial, addr)
    _audit(request, "device_pair", f"{serial} at {addr}"
           + (f", took over the record of {adopted}; trust cleared" if adopted else ""))
    if db.get_device(serial)["trusted"]:
        return _redirect("/devices", ok="Paired")
    if adopted:
        return _redirect("/devices", ok="Paired. It took over the older record for that IP (nickname and history "
                                        "kept) — trust it again below if it's the same phone")
    return _redirect("/devices", ok="Paired. Trust the device below before it can receive pushes")


@app.post("/devices/{serial}/connect")
def reconnect_device(
    serial: str, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), connect_addr: str = Form(...),
):
    _check_csrf(request, session, csrf_token)
    device = db.get_device(serial)
    if device is None:
        raise HTTPException(status_code=404)
    addr = connect_addr.strip()
    try:
        adb_client.connect(addr)
        confirmed = adb_client.get_serialno(addr)
    except adb_client.AdbError as exc:
        return _redirect("/devices", error=str(exc))
    if not discovery.is_same_device(serial, addr, confirmed):
        return _redirect("/devices", error="That address now answers as a different device - not updated")
    serial = discovery.record(serial, confirmed, addr)
    pushes.refresh_abis(serial, addr)
    return _redirect("/devices", ok="Reconnected")


@app.post("/devices/{serial}/find")
def find_device(serial: str, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    """Sync route, so Starlette runs it in a threadpool: the port scan can
    take several seconds and must not block the event loop."""
    _check_csrf(request, session, csrf_token)
    device = db.get_device(serial)
    if device is None:
        raise HTTPException(status_code=404)
    try:
        serial, addr = discovery.ensure_connected(device)
    except adb_client.AdbError as exc:
        return _redirect("/devices", error=str(exc))
    pushes.refresh_abis(serial, addr)
    return _redirect("/devices", ok=f"Found at {addr}")


@app.post("/devices/{serial}/trust")
def trust_device(
    serial: str, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), trusted: str = Form(...),
):
    _check_csrf(request, session, csrf_token)
    if db.get_device(serial) is None:
        raise HTTPException(status_code=404)
    db.set_device_trusted(serial, trusted == "1")
    _audit(request, "device_trust" if trusted == "1" else "device_untrust", serial)
    return _redirect("/devices", ok="Updated")


@app.post("/devices/{serial}/nickname")
def nickname_device(
    serial: str, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), nickname: str = Form(""),
):
    _check_csrf(request, session, csrf_token)
    if db.get_device(serial) is None:
        raise HTTPException(status_code=404)
    db.set_device_nickname(serial, nickname.strip()[:100] or None)
    return _redirect("/devices", ok="Updated")


@app.post("/devices/{serial}/delete")
def delete_device(serial: str, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    db.delete_device(serial)
    _audit(request, "device_forget", serial)
    return _redirect("/devices", ok="Device forgotten")


# ---- push ----

def _queue_push(request: Request, background_tasks: BackgroundTasks, device, apk, back: str) -> RedirectResponse:
    try:
        install_id = pushes.create_install(device, apk)
    except pushes.PushRefused as exc:
        return _redirect(back, error=str(exc))
    _audit(request, "push", f"{apk['source_label']} {apk['tag']} ({apk['filename']}) → {device['nickname'] or device['serial']}")
    background_tasks.add_task(pushes.run_push, install_id, dict(device), dict(apk))
    return RedirectResponse(f"/installs/{install_id}", status_code=303)


@app.post("/push")
def push(
    request: Request,
    background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    device_serial: str = Form(...),
    apk_id: int = Form(...),
):
    _check_csrf(request, session, csrf_token)
    device = db.get_device(device_serial)
    apk = db.get_staged_apk(apk_id)
    if device is None or apk is None:
        raise HTTPException(status_code=404)
    return _queue_push(request, background_tasks, device, apk, "/install")


# Where a failed push-latest sends you back to: the two pages that offer it.
PUSH_BACK_PATHS = {"/status", "/install"}


@app.post("/push-latest")
def push_latest(
    request: Request,
    background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    device_serial: str = Form(...),
    repo_id: int = Form(...),
    back: str = Form("/status"),
):
    """Pushes the repo's newest staged release, choosing the APK variant
    that fits the device's CPU."""
    _check_csrf(request, session, csrf_token)
    back = back if back in PUSH_BACK_PATHS else "/status"
    device = db.get_device(device_serial)
    if device is None:
        raise HTTPException(status_code=404)
    variants = [v for v in db.list_latest_variants() if v["repo_id"] == repo_id]
    if not variants:
        return _redirect(back, error="Nothing staged for that repo")
    apk = selection.pick_variant(variants, device["abis"])
    if apk is None:
        return _redirect(back, error="No APK in the latest release supports this device's CPU")
    return _queue_push(request, background_tasks, device, apk, back)


# ---- status ----

def _device_cards() -> list[dict]:
    """One card per device: each watched app's latest release against what
    the device has, then anything else pushed to it (uploads), then its most
    recent installs. Trusted devices first."""
    installed = db.device_packages_map()
    follows = db.follows_set()
    variants_by_repo: dict[int, list] = {}
    for v in db.list_latest_variants():
        variants_by_repo.setdefault(v["repo_id"], []).append(v)
    repo_packages = {vs[0]["package_name"] for vs in variants_by_repo.values()}
    upload_labels: dict[str, str] = {}
    for a in db.list_staged_apks():  # newest first, so the first label wins
        if a["repo_id"] is None:
            upload_labels.setdefault(a["package_name"], a["tag"])
    installs = db.list_installs()
    cards = []
    for d in sorted(db.list_devices(), key=lambda d: not d["trusted"]):
        apps = []
        for repo_id, variants in variants_by_repo.items():
            latest = variants[0]
            have = installed.get((d["serial"], latest["package_name"]))
            apps.append({
                "repo_id": repo_id, "latest": latest, "installed": have,
                "state": selection.update_state(have, latest),
                "fits": selection.pick_variant(variants, d["abis"]) is not None,
                "following": (d["serial"], repo_id) in follows,
            })
        others = [
            {"package": pkg, "label": upload_labels.get(pkg), "installed": row}
            for (serial, pkg), row in sorted(installed.items())
            if serial == d["serial"] and row["installed"] and pkg not in repo_packages
        ]
        recent = [i for i in installs if i["device_serial"] == d["serial"]][:3]
        cards.append({"device": d, "apps": apps, "others": others, "recent": recent})
    return cards


def _setup_steps(cards: list[dict]) -> list[dict]:
    """The first-run checklist, in the order the app is set up."""
    has_source = bool(db.list_repos()) or bool(db.list_staged_apks())
    has_trusted = any(c["device"]["trusted"] for c in cards)
    has_install = any(i["status"] == "success" for c in cards for i in c["recent"])
    return [
        {"done": has_source, "href": "/sources", "title": "Add a source",
         "hint": "Watch a GitHub repo for releases, or upload an APK."},
        {"done": has_trusted, "href": "/devices", "title": "Pair and trust a device",
         "hint": "Pair with the phone's wireless debugging code, then trust it."},
        {"done": has_install, "href": "/install", "title": "Install an app",
         "hint": "Push a staged APK to a trusted device."},
    ]


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request, session: dict = Depends(auth.require_auth), error: str | None = None, ok: str | None = None):
    cards = _device_cards()
    steps = _setup_steps(cards)
    return templates.TemplateResponse(
        request, "status.html",
        _tctx(request, session, cards=cards, steps=steps, setup_done=all(s["done"] for s in steps),
              error=error, ok=ok),
    )


@app.post("/status/refresh")
def refresh_status(request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    """Asks each trusted device which version of every pinned package it has.
    No port scan here (that's per-device, on demand), so an offline device
    costs one quick failed connect."""
    _check_csrf(request, session, csrf_token)
    packages = {r["expected_package"] for r in db.list_repos() if r["expected_package"]}
    unreachable = []
    for device in db.list_devices():
        if not device["trusted"]:
            continue
        try:
            serial, addr = discovery.ensure_connected(device, allow_scan=False)
        except adb_client.AdbError:
            unreachable.append(device["nickname"] or device["serial"])
            continue
        pushes.refresh_abis(serial, addr)
        for package in packages:
            pushes.refresh_installed(serial, addr, package)
    if unreachable:
        return _redirect("/status", error="Not reachable (try Find on the Devices page): " + ", ".join(unreachable))
    return _redirect("/status", ok="Installed versions refreshed")


@app.post("/follow")
def set_follow(
    request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
    device_serial: str = Form(...), repo_id: int = Form(...), follow: str = Form(...),
):
    """Auto-update: a following device gets each newly staged release pushed
    to it. Only trusted devices can follow, and trust is re-checked at push."""
    _check_csrf(request, session, csrf_token)
    device, repo_row = db.get_device(device_serial), db.get_repo(repo_id)
    if device is None or repo_row is None:
        raise HTTPException(status_code=404)
    on = follow == "1"
    if on and not device["trusted"]:
        return _redirect("/status", error="Only trusted devices can auto-update")
    db.set_follow(device_serial, repo_id, on)
    _audit(request, "auto_update_on" if on else "auto_update_off",
           f"{repo_row['owner']}/{repo_row['repo']} → {device['nickname'] or device_serial}")
    return _redirect("/status", ok="Auto-update " + ("on" if on else "off"))


# ---- settings: notifications and appearance ----

def _notify_rows() -> list[dict]:
    rows = []
    env_index = 0
    for t in notify.targets():
        info = notify.describe(t.url)
        key = f"env:{env_index}" if t.from_env else str(t.id)
        if t.from_env:
            env_index += 1
        rows.append({"key": key, "label": t.label, "from_env": t.from_env, **info})
    return rows


def _find_target(key: str) -> notify.Target | None:
    env = [t for t in notify.targets() if t.from_env]
    if key.startswith("env:"):
        index = key[4:]
        return env[int(index)] if index.isdigit() and int(index) < len(env) else None
    if not key.isdigit():
        return None
    row = db.get_notify_target(int(key))
    return notify.Target(row["id"], row["url"], row["label"]) if row else None


def _render_settings(request: Request, session: dict, draft: dict | None = None, **flash) -> HTMLResponse:
    return templates.TemplateResponse(request, "settings.html", _tctx(
        request, session, **flash, draft=draft or {},
        targets=_notify_rows(), max_targets=notify.MAX_TARGETS,
        events=[(e, notify.EVENT_LABELS[e], e in notify.enabled_events()) for e in notify.EVENTS],
        events_from=("settings" if db.get_meta("notify_events") is not None
                     else "env" if os.environ.get("NOTIFY_EVENTS", "").strip() else "default"),
        mfa_enabled=mfa.enabled(), mfa_locked=mfa.locked_for(), recovery_left=db.recovery_codes_left(),
        trusted_browsers=db.list_trusted_browsers(), this_browser=mfa.browser_id(auth.read_mfa_trust(request)),
        trust_days=mfa.TRUST_DAYS,
        presets=appearance.PRESETS, accent=db.get_meta("accent_color"), accent2=db.get_meta("accent2_color"),
        saved_colours=db.list_saved_colours(), max_saved_colours=MAX_SAVED_COLOURS,
        current_preset=appearance.preset_name(db.get_meta("accent_color"), db.get_meta("accent2_color")),
        default_pair=appearance.PRESETS[appearance.DEFAULT_PRESET],
        default_preset=appearance.DEFAULT_PRESET,
    ))


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None,
):
    return _render_settings(request, session, error=error, ok=ok, warn=warn)


def _try_target(request: Request, session: dict, url: str | None, problem: str | None, draft: dict) -> HTMLResponse:
    """Sends a test to a URL that hasn't been saved, and re-renders the page
    with the form still filled in — no redirect, so the draft (which may hold
    a token) never goes into a URL or the browser history."""
    if problem:
        return _render_settings(request, session, draft, error=problem)
    ok, message = notify.test(notify.Target(None, url, None))
    service = notify.describe(url)["service"]
    _audit(request, "notify_test", f"unsaved {service}: {message}")
    if ok:
        return _render_settings(request, session, draft, ok=f"Test sent — {service}: {message}. Press Add to keep it.")
    return _render_settings(request, session, draft, error=f"Test failed — {service}: {message}")


@app.post("/settings/notify/try-server", response_class=HTMLResponse)
def try_apprise_server(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), server: str = Form(""), key: str = Form(""),
    tags: str = Form(""), label: str = Form(""),
):
    _check_csrf(request, session, csrf_token)
    draft = {"server": server, "key": key, "tags": tags, "server_label": label}
    try:
        url, problem = notify.build_api_url(server, key, tags), None
    except ValueError as exc:
        url, problem = None, str(exc)
    return _try_target(request, session, url, problem, draft)


@app.post("/settings/notify/try", response_class=HTMLResponse)
def try_notify_url(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), url: str = Form(""), label: str = Form(""),
):
    _check_csrf(request, session, csrf_token)
    draft = {"url": url, "url_label": label}
    try:
        checked, problem = notify.validate(url), None
    except ValueError as exc:
        checked, problem = None, str(exc)
    return _try_target(request, session, checked, problem, draft)


@app.post("/settings/notify/add")
def add_notify_target(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), url: str = Form(""), label: str = Form(""),
):
    _check_csrf(request, session, csrf_token)
    try:
        url = notify.validate(url)
    except ValueError as exc:
        return _redirect("/settings", error=str(exc))
    return _store_notify_target(request, url, label)


def _store_notify_target(request: Request, url: str, label: str) -> RedirectResponse:
    if len(db.list_notify_targets()) >= notify.MAX_TARGETS:
        return _redirect("/settings", error=f"You can store up to {notify.MAX_TARGETS} services")
    if db.add_notify_target(url, label.strip()[:80] or None) is None:
        return _redirect("/settings", error="That service is already configured")
    info = notify.describe(url)
    _audit(request, "notify_add", f"{info['service']} {info['masked']}")
    return _redirect("/settings", ok=f"Added {info['service']} — send a test to check it")


@app.post("/settings/notify/add-server")
def add_apprise_server(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), server: str = Form(""), key: str = Form(""),
    tags: str = Form(""), label: str = Form(""),
):
    _check_csrf(request, session, csrf_token)
    try:
        url = notify.build_api_url(server, key, tags)
    except ValueError as exc:
        return _redirect("/settings", error=str(exc))
    return _store_notify_target(request, url, label)


@app.post("/settings/notify/{target_id}/delete")
def delete_notify_target(
    target_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
):
    _check_csrf(request, session, csrf_token)
    row = db.get_notify_target(target_id)
    if row is None:
        raise HTTPException(status_code=404)
    db.delete_notify_target(target_id)
    info = notify.describe(row["url"])
    _audit(request, "notify_remove", f"{info['service']} {info['masked']}")
    return _redirect("/settings", ok=f"Removed {info['service']}")


@app.post("/settings/notify/events")
def set_notify_events(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), events: list[str] = Form([]),
):
    _check_csrf(request, session, csrf_token)
    chosen = sorted(set(events) & set(notify.EVENTS))
    db.set_meta("notify_events", ",".join(chosen))
    _audit(request, "notify_events", ",".join(chosen) or "(none)")
    return _redirect("/settings", ok="Notification events saved" if chosen else "All notification events turned off")


@app.post("/settings/notify/test")
def test_notify(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), target: str = Form("all"),
):
    """Sync route (threadpool): delivery is blocking network I/O."""
    _check_csrf(request, session, csrf_token)
    if target == "all":
        chosen = notify.targets()
        if not chosen:
            return _redirect("/settings", error="No notification services are configured yet")
    else:
        found = _find_target(target)
        if found is None:
            raise HTTPException(status_code=404)
        chosen = [found]
    results = []
    for t in chosen:
        ok, message = notify.test(t)
        results.append((ok, f"{notify.describe(t.url)['service']}: {message}"))
    _audit(request, "notify_test", "; ".join(r[1] for r in results)[:500])
    summary = " · ".join(r[1] for r in results)
    if all(ok for ok, _ in results):
        return _redirect("/settings", ok=f"Test sent — {summary}")
    return _redirect("/settings", error=f"Test failed for some services — {summary}")


MAX_SAVED_COLOURS = 20


def _apply_colours(request: Request, primary: str | None, secondary: str | None, name: str | None = None):
    db.set_meta("accent_color", primary)
    db.set_meta("accent2_color", secondary)
    if primary is None:
        _audit(request, "accent_reset")
        return _redirect("/settings#appearance", ok="Colours reset to the default teal and ocean")
    _audit(request, "accent_set", f"{primary} / {secondary}")
    return _redirect("/settings#appearance", ok=f"Colours set to {name or f'{primary} and {secondary}'}")


# ---- settings: two-factor sign-in ----

def _settings_error(message: str) -> RedirectResponse:
    return _redirect("/settings#security", error=message)


def _check_current_code(code: str) -> str | None:
    """For turning MFA off or replacing recovery codes: a current code (or
    recovery code), under the same lockout as sign-in. Returns an error."""
    if mfa.locked_for():
        return _locked_message()
    if mfa.check(code) is None:
        return "That code didn't work" + (" — too many wrong codes, now locked" if mfa.locked_for() else "")
    return None


def _show_recovery_codes(request: Request, session: dict, codes: list[str]):
    response = templates.TemplateResponse(request, "mfa_codes.html", _tctx(request, session, codes=codes))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/settings/mfa/setup", response_class=HTMLResponse)
def mfa_setup_page(request: Request, session: dict = Depends(auth.require_auth), error: str | None = None):
    if mfa.enabled():
        return _redirect("/settings#security")
    secret = mfa.pending_secret(create=True)
    uri = mfa.provisioning_uri(secret, auth.APP_USERNAME)
    response = templates.TemplateResponse(request, "mfa_setup.html", _tctx(
        request, session, error=error, qr_svg=mfa.qr_svg(uri), secret=mfa.format_secret(secret),
    ))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/settings/mfa/enable")
def mfa_enable(request: Request, session: dict = Depends(auth.require_auth),
               csrf_token: str = Form(...), code: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    if mfa.enabled():
        return _redirect("/settings#security")
    codes = mfa.enable(code)
    if codes is None:
        return _redirect("/settings/mfa/setup", error="That code didn't match — scan the QR code again and "
                                                      "enter the newest code")
    _audit(request, "mfa_enabled")
    # Every other session signed in with just the password: sign them out,
    # and give this one a fresh cookie so it carries on.
    auth.revoke_sessions()
    csrf = auth.new_csrf_token()
    response = _show_recovery_codes(request, {"csrf": csrf}, codes)
    auth.create_session(response, csrf)
    return response


@app.post("/settings/mfa/recovery-codes")
def mfa_new_recovery_codes(request: Request, session: dict = Depends(auth.require_auth),
                           csrf_token: str = Form(...), code: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    if not mfa.enabled():
        return _redirect("/settings#security")
    if (error := _check_current_code(code)):
        return _settings_error(error)
    _audit(request, "mfa_recovery_codes_replaced")
    return _show_recovery_codes(request, session, mfa.new_recovery_codes())


@app.post("/settings/mfa/disable")
def mfa_disable(request: Request, session: dict = Depends(auth.require_auth),
                csrf_token: str = Form(...), code: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    if not mfa.enabled():
        return _redirect("/settings#security")
    if (error := _check_current_code(code)):
        return _settings_error(error)
    mfa.disable()
    _audit(request, "mfa_disabled")
    response = _redirect("/settings#security", ok="Two-factor sign-in is off")
    response.delete_cookie(auth.MFA_TRUST_COOKIE, path="/")
    return response


@app.post("/settings/mfa/trusted/revoke")
def mfa_revoke_browser(request: Request, session: dict = Depends(auth.require_auth),
                       csrf_token: str = Form(...), browser: str = Form("")):
    """One trusted browser (by id), or every one (no id)."""
    _check_csrf(request, session, csrf_token)
    if browser and not re.fullmatch(r"[0-9a-f]{64}", browser):
        raise HTTPException(status_code=400)
    db.delete_trusted_browser(browser or None)
    _audit(request, "mfa_trust_revoked", "one browser" if browser else "all browsers")
    return _redirect("/settings#security", ok="That browser will be asked for a code next time" if browser
                     else "Every browser will be asked for a code next time")


@app.post("/settings/appearance")
def set_accent(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), preset: str = Form(""), saved: str = Form(""),
    accent: str = Form(""), accent2: str = Form(""),
):
    """A preset pair, a saved pair, a custom primary + secondary, or
    (none of them) the default."""
    _check_csrf(request, session, csrf_token)
    if saved:
        row = db.get_saved_colour(int(saved)) if saved.isdigit() else None
        if row is None:
            return _redirect("/settings#appearance", error="That saved colour pair no longer exists")
        return _apply_colours(request, row["primary_color"], row["secondary_color"], row["name"])
    if preset:
        if preset not in appearance.PRESETS:
            return _redirect("/settings", error="Unknown colour preset")
        primary, secondary = (None, None) if preset == appearance.DEFAULT_PRESET else appearance.PRESETS[preset]
    elif accent:
        try:
            primary = appearance.normalize(accent)
            secondary = appearance.normalize(accent2 or appearance.PRESETS[appearance.DEFAULT_PRESET][1])
        except ValueError as exc:
            return _redirect("/settings", error=str(exc))
    else:
        primary = secondary = None
    return _apply_colours(request, primary, secondary)


@app.post("/settings/appearance/save")
def save_colours(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), name: str = Form(""), accent: str = Form(...), accent2: str = Form(...),
):
    """Saves the custom pair under a name (re-saving a name updates it) and applies it."""
    _check_csrf(request, session, csrf_token)
    name = " ".join(name.split())
    if not name or len(name) > 40:
        return _redirect("/settings#appearance", error="Give the colour pair a name of up to 40 characters")
    try:
        primary, secondary = appearance.normalize(accent), appearance.normalize(accent2)
    except ValueError as exc:
        return _redirect("/settings#appearance", error=str(exc))
    names = {r["name"] for r in db.list_saved_colours()}
    if name not in names and len(names) >= MAX_SAVED_COLOURS:
        return _redirect("/settings#appearance", error=f"You can save up to {MAX_SAVED_COLOURS} colour pairs — delete one first")
    db.save_colour(name, primary, secondary)
    _audit(request, "colours_saved", f"{name}: {primary} / {secondary}")
    return _apply_colours(request, primary, secondary, name)


@app.post("/settings/appearance/saved/{colour_id}/delete")
def delete_saved_colours(
    colour_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
):
    """Removes the saved pair. Colours already in use stay as they are."""
    _check_csrf(request, session, csrf_token)
    row = db.get_saved_colour(colour_id)
    if row is None:
        raise HTTPException(status_code=404)
    db.delete_saved_colour(colour_id)
    _audit(request, "colours_deleted", row["name"])
    return _redirect("/settings#appearance", ok=f"Deleted the saved pair “{row['name']}”")


@app.get("/accent.css")
def accent_css():
    """Public: the sign-in page uses it too, and it reveals only a colour.
    Generated here because the CSP forbids inline styles."""
    return Response(appearance.stylesheet(db.get_meta("accent_color"), db.get_meta("accent2_color"),
                                          db.list_saved_colours()),
                    media_type="text/css",
                    headers={"Cache-Control": "no-cache"})


# ---- audit ----

@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, session: dict = Depends(auth.require_auth)):
    return templates.TemplateResponse(request, "audit.html", _tctx(request, session, entries=db.list_audit()))


# ---- installs ----

@app.get("/installs", response_class=HTMLResponse)
def installs_page(request: Request, session: dict = Depends(auth.require_auth)):
    return templates.TemplateResponse(request, "installs.html", _tctx(request, session, installs=db.list_installs()))


@app.get("/installs/{install_id}", response_class=HTMLResponse)
def install_status_page(install_id: int, request: Request, session: dict = Depends(auth.require_auth)):
    install = db.get_install(install_id)
    if install is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "install_status.html", _tctx(request, session, install=install))
