import asyncio
import fnmatch
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import zoneinfo
from datetime import datetime, timezone
from typing import NamedTuple
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
import signing
import staging

logging.basicConfig(level=logging.INFO)
# httpx logs every request's full URL at INFO, and a release asset's or an
# artifact's download redirects to a signed URL whose query string is a
# working read token. Only warnings and errors from it are logged.
logging.getLogger("httpx").setLevel(logging.WARNING)
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


TIME_ZONES = sorted(zoneinfo.available_timezones() | {"UTC"})
_display_zone: list = []  # [ZoneInfo], loaded on first use and replaced when saved


def _zone_name() -> str:
    """Settings → Appearance, else TZ from the environment, else UTC."""
    for name in (db.get_meta("timezone"), os.environ.get("TZ")):
        if name in TIME_ZONES:
            return name
    return "UTC"


_clock: list = []  # [bool: 24-hour], cached like the zone


def _clock_24h() -> bool:
    if not _clock:
        _clock.append(db.get_meta("clock") == "24")
    return _clock[0]


def display_zone() -> zoneinfo.ZoneInfo:
    if not _display_zone:
        _display_zone.append(zoneinfo.ZoneInfo(_zone_name()))
    return _display_zone[0]


def _when(value: str | None, empty: str = "never") -> Markup:
    """Renders a stored UTC ISO timestamp as a short, readable <time> in the
    display time zone, keeping the exact UTC value in the tooltip and the
    machine-readable attribute."""
    if not value:
        return Markup('<span class="muted">{}</span>').format(empty)
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return Markup("{}").format(value)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    local = at.astimezone(display_zone())
    year = "" if local.year == datetime.now(display_zone()).year else f" {local.year}"
    if _clock_24h():
        clock = f"{local:%H:%M}"
    else:
        clock = f"{local.hour % 12 or 12}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"
    label = f"{local:%b} {local.day}{year}, {clock} {local.tzname() or ''}".rstrip()
    return Markup('<time datetime="{0}" title="{0}">{1}</time>').format(value, label)


templates.env.filters["when"] = _when


def _device_name(d) -> str:
    """What to call a device: its nickname, else its model with the end of
    its serial (two identical phones stay distinguishable), else the serial."""
    if d["nickname"]:
        return d["nickname"]
    model = d["model"] if "model" in d.keys() else None
    return f"{model} · …{d['serial'][-5:]}" if model else d["serial"]


templates.env.filters["device_name"] = _device_name


def _siblings(raw: str | None) -> list[dict]:
    """Other builds of an artifact's commit, as stored at staging time."""
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("id"), int) and isinstance(i.get("name"), str)]


templates.env.filters["siblings"] = _siblings


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
SETTINGS_PAGES = ("general", "security", "notifications", "github", "appearance")
KNOWN_NAV_PATHS = {"/status", "/sources", "/devices", "/install", "/settings", "/installs", "/audit",
                   *(f"/settings/{p}" for p in SETTINGS_PAGES)}
# Pages reached from Settings rather than the top bar highlight Settings.
NAV_SECTION = {"/installs": "/settings", "/audit": "/settings", **{f"/settings/{p}": "/settings" for p in SETTINGS_PAGES}}


def _get_theme(request: Request) -> str:
    theme = request.cookies.get("theme", "")
    return theme if theme in VALID_THEMES else "auto"


def _tctx(request: Request, session: dict | None = None, **extra) -> dict:
    ctx = {
        "app_version": APP_VERSION,
        "repo_url": REPO_URL,
        "release_notes_url": RELEASE_NOTES_URL,
        "sign_explanation": signing.EXPLANATION,
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

CHECK_WAIT_SECONDS = 60


def _check_result(repo) -> dict:
    """The flash for a finished Check now, in plain words."""
    name = f"{repo['owner']}/{repo['repo']}"
    if repo["last_error"]:
        return {"error": f"{name}: {repo['last_error']}"}
    if not repo["last_tag"]:
        return {"ok": f"{name} has no releases yet, only workflow builds: stage one from Builds."}
    return {"ok": f"{name} checked: its latest release is {repo['last_tag']}."}


@app.get("/sources", response_class=HTMLResponse)
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


@app.get("/repos")
@app.get("/upload")
def old_sources_pages(request: Request, session: dict = Depends(auth.require_auth)):
    return _moved(request, "/sources")


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
    uploads = [a for a in db.list_staged_apks() if a["repo_id"] is None]
    return templates.TemplateResponse(
        request, "sources.html",
        _tctx(request, session, repos=db.list_repos(), uploads=uploads,
              max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024), **extra),
    )


@app.post("/repos", response_class=HTMLResponse)
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
    _check_csrf(request, session, csrf_token)
    asset_glob = asset_glob.strip() or "*.apk"
    try:
        owner, repo = github_client.parse_repo_reference(repo_url)
        info = await github_client.get_repo_info(owner, repo, poller.github_token())
    except github_client.GithubError as exc:
        return _redirect("/sources", error=str(exc))
    if db.get_repo_by_github_id(info.id) is not None:
        return _redirect("/sources", error="That repo is already registered")
    kind, reason = await _apk_evidence(info, asset_glob)
    if kind is None:
        return _redirect("/sources", error=reason)
    return _sources_page(request, session, review={
        "info": info, "asset_glob": asset_glob, "include_prereleases": include_prereleases == "1",
        "warnings": _repo_warnings(info, kind),
    })


@app.post("/repos/confirm")
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
    _check_csrf(request, session, csrf_token)
    asset_glob = asset_glob.strip() or "*.apk"
    try:
        info = await github_client.get_repo_info(owner, repo, poller.github_token())
    except github_client.GithubError as exc:
        return _redirect("/sources", error=str(exc))
    if info.id != github_id:
        return _redirect("/sources", error="That name points at a different repo than the one you reviewed "
                                           "— nothing was added. Look it up again.")
    if db.get_repo_by_github_id(info.id) is not None:
        return _redirect("/sources", error="That repo is already registered")
    kind, reason = await _apk_evidence(info, asset_glob)  # confirm can be posted without a review
    if kind is None:
        return _redirect("/sources", error=reason)
    try:
        db.create_repo(info.owner, info.repo, asset_glob, include_prereleases == "1",
                       github_id=info.id, owner_id=info.owner_id, owner_type=info.owner_type)
    except sqlite3.IntegrityError:
        return _redirect("/sources", error="That repo is already registered")
    _audit(request, "repo_add", f"{info.owner}/{info.repo} id={info.id} owner={info.owner_type} glob={asset_glob}")
    return _redirect("/sources", ok=f"Watching {info.owner}/{info.repo}")


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
    # The Sources page then reloads itself until this check has finished.
    return _redirect("/sources", checking=repo_id, since=db.now())


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
            kind = "artifact" if a["artifact_repo"] else "upload"
            groups.append({"repo_id": None, "kind": kind, "label": a["artifact_repo"] or a["tag"],
                           "releases": [{"tag": a["tag"], "apks": [a]}]})
            continue
        group = by_repo.get(a["repo_id"])
        if group is None:
            group = by_repo[a["repo_id"]] = {"repo_id": a["repo_id"], "kind": "release",
                                             "label": a["source_label"], "releases": []}
            groups.append(group)
        if not group["releases"] or group["releases"][-1]["tag"] != a["tag"]:
            group["releases"].append({"tag": a["tag"], "apks": []})
        group["releases"][-1]["apks"].append(a)
    # Watched repos first, alphabetically; then test builds from artifacts,
    # then plain uploads, each newest first.
    order = {"release": 0, "artifact": 1, "upload": 2}
    return sorted(groups, key=lambda g: (order[g["kind"]], g["label"].lower() if g["repo_id"] else ""))


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


class VerifiedUpload(NamedTuple):
    sha256: str
    display_name: str
    archive_name: str | None  # the zip it arrived in, if it came as an artifact zip
    signer: apk_verify.SignerInfo
    info: apk_verify.PackageInfo
    server_signed: bool  # it was unsigned, and was signed with this server's key (opted in)


UNSIGNED_REFUSAL = ("the APK is unsigned, and Android can't install an unsigned APK. To stage it anyway, opt in "
                    "to signing it with this server's key (see what that means next to the option)")


def _sha256_file(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _sign_if_unsigned(tmp_path: str, allowed: bool) -> bool:
    """Signs an unsigned APK with the server key when the source opted in.
    Returns whether it did. Raises for an unsigned APK without the opt-in."""
    if not apk_verify.is_unsigned(tmp_path):
        return False
    if not allowed:
        raise apk_verify.ApkVerifyError(UNSIGNED_REFUSAL)
    try:
        signing.sign_in_place(tmp_path)
    except signing.SigningError as exc:
        raise apk_verify.ApkVerifyError(f"signing it with this server's key failed: {exc}") from exc
    return True


def _unwrap_archive(tmp_path: str) -> apk_verify.ArchivedApk | None:
    """If tmp_path is an artifact zip, replaces it in place with the one APK
    inside. The zip is deleted by that replace, straight after extraction and
    before any APK check runs, so no archive outlives its unpacking — whatever
    happens next, only one temp file is left for the caller to clean up."""
    fd, apk_path = tempfile.mkstemp(dir=os.path.dirname(tmp_path), prefix="_upload_")
    os.close(fd)
    try:
        archived = apk_verify.extract_archived_apk(tmp_path, apk_path, MAX_UPLOAD_BYTES)
        if archived is not None:
            os.replace(apk_path, tmp_path)
        return archived
    finally:
        staging.remove_file(apk_path)  # partial extraction, or unused; gone after a replace


def _verify_file(tmp_path: str, digest: str, display_name: str, sign_unsigned: bool = False) -> VerifiedUpload:
    """Checks a received file (a bare APK, or a zip holding one) in place."""
    archive_name = None
    archived = _unwrap_archive(tmp_path)
    if archived is not None:
        digest, archive_name, display_name = archived.sha256, display_name, _display_filename(archived.name)
    apk_verify.assert_apk_container(tmp_path)
    server_signed = _sign_if_unsigned(tmp_path, sign_unsigned)
    if server_signed:
        digest = _sha256_file(tmp_path)
    return VerifiedUpload(digest, display_name, archive_name,
                          apk_verify.verify_signature(tmp_path), apk_verify.get_package_info(tmp_path), server_signed)


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
    _check_csrf(request, session, csrf_token)
    tmp_path = _new_upload_tmp()
    try:
        digest = _receive_upload(apk, tmp_path)
    except UploadRejected as exc:
        staging.remove_file(tmp_path)
        return _redirect("/sources", error=f"Upload refused: {exc}")
    except BaseException:
        staging.remove_file(tmp_path)
        raise
    return _stage_received(request, tmp_path, digest, _display_filename(apk.filename), label,
                           sign_unsigned=sign_unsigned == "yes")


def _new_upload_tmp() -> str:
    upload_dir = _upload_dir()
    os.makedirs(upload_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=upload_dir, prefix="_upload_")
    os.close(fd)
    return tmp_path


def _stage_received(
    request: Request, tmp_path: str, digest: str, display_name: str, label: str,
    origin: str | None = None, refused_to: str = "/sources", notes: str | None = None,
    artifact: dict | None = None, sign_unsigned: bool = False, on_verified=None,
) -> RedirectResponse:
    """Verifies and stages a file already on disk at tmp_path, which this
    consumes: it ends up staged or removed. `origin` describes where the file
    came from, for the flash message and the audit log; without one, a zip's
    own name is used."""
    try:
        verified = _verify_file(tmp_path, digest, display_name, sign_unsigned)
    except (UploadRejected, apk_verify.ApkVerifyError) as exc:
        staging.remove_file(tmp_path)
        logger.warning("upload rejected (%s): %s", display_name, exc)
        return _redirect(refused_to, error=f"Upload refused: {exc}")
    except BaseException:
        staging.remove_file(tmp_path)
        raise
    digest, display_name, signer, info = verified.sha256, verified.display_name, verified.signer, verified.info
    if on_verified is not None:
        on_verified(verified)

    existing = db.get_uploaded_apk_by_sha256(digest)
    if existing is not None:
        staging.remove_file(tmp_path)
        return _redirect(refused_to, error=f"That exact APK is already staged as \"{existing['filename']}\"")

    final_path = os.path.join(_upload_dir(), f"{digest}.apk")
    os.replace(tmp_path, final_path)
    apk_id = db.insert_staged_apk(
        repo_id=None, tag=(label.strip() or info.version_name or display_name)[:120],
        filename=display_name, sha256=digest, package_name=info.name,
        signer_sha256=signer.fingerprint, path=final_path,
        version_code=info.version_code, version_name=info.version_name,
        abis=" ".join(info.abis), source="upload", is_debug=signer.debug, release_notes=notes,
        artifact=artifact, server_signed=verified.server_signed,
    )
    if apk_id is None:
        # A concurrent upload of the same file won; final_path is its file too.
        return _redirect(refused_to, error="That exact APK is already staged")

    source = origin or (f" from {verified.archive_name}" if verified.archive_name else "")
    _audit(request, "upload", f"{display_name}{source} {info.name} sha256={digest[:12]} debug={signer.debug}"
                              f"{' server_signed=True' if verified.server_signed else ''}")
    staged = f"Staged {display_name} ({info.name}){source}."
    if verified.server_signed:
        staged += " It was unsigned, so it was signed with this server's key."
    warnings = _upload_warnings(info, signer)
    if warnings:
        return _redirect("/install", warn=" ".join([staged, *warnings]))
    return _redirect("/install", ok=staged)


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
    return repo, poller._identity_problem(repo, info)


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


@app.get("/repos/{repo_id}/artifacts", response_class=HTMLResponse)
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
            flt = _artifact_filter()
            shown = [a for a in await github_client.list_artifacts(repo["owner"], repo["repo"], repo["github_id"], token)
                     if _artifact_shown(a, flt) and a.branch not in tags and a.head_sha not in release_shas]
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
        _tctx(request, session, repo=repo, artifacts=artifacts, releases=releases, problem=problem, signing=signing,
              max_mb=MAX_UPLOAD_BYTES // (1024 * 1024), error=error, ok=ok, warn=warn),
    )


@app.post("/repos/{repo_id}/sign-unsigned")
def set_sign_unsigned(
    repo_id: int, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), on: str = Form(...), understood: str = Form(""),
):
    """Per-repo opt-in to signing its unsigned builds (releases and artifacts)
    with this server's key. Turning it on needs the explanation acknowledged."""
    _check_csrf(request, session, csrf_token)
    repo = db.get_repo(repo_id)
    if repo is None:
        raise HTTPException(status_code=404)
    turn_on = on == "1"
    if turn_on and understood != "yes":
        return _redirect(f"/repos/{int(repo_id)}/artifacts#signing",
                         error="Tick that you understand what signing with this server's key means first")
    db.set_sign_unsigned(repo_id, turn_on)
    _audit(request, "sign_unsigned_on" if turn_on else "sign_unsigned_off", f"{repo['owner']}/{repo['repo']}")
    return _redirect(f"/repos/{int(repo_id)}/artifacts#signing",
                     ok=("Unsigned builds from this repo will be signed with this server's key" if turn_on
                         else "Unsigned builds from this repo will be refused again"))


@app.post("/repos/{repo_id}/releases/{release_id}/stage")
async def stage_release(
    repo_id: int, release_id: int, request: Request,
    session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
):
    """Stages an older release of a watched repo, through every release check
    (uploader, signature, no debug builds, the pin)."""
    _check_csrf(request, session, csrf_token)
    back = f"/repos/{int(repo_id)}/artifacts"
    if db.get_repo(repo_id) is None:
        raise HTTPException(status_code=404)
    ok, message = await poller.stage_past_release(repo_id, release_id)
    repo = db.get_repo(repo_id)
    _audit(request, "release_stage" if ok else "release_stage_refused",
           f"{repo['owner']}/{repo['repo']} release={int(release_id)}: {message}")
    return _redirect("/install" if ok else back, **({"ok": message} if ok else {"error": message}))


@app.post("/repos/{repo_id}/artifacts/{artifact_id}/stage")
async def stage_artifact(
    repo_id: int, artifact_id: int, request: Request,
    session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
):
    """Stages the APK inside a workflow artifact, for testing a build that
    isn't released. It is handled exactly like an uploaded zip (debug builds
    allowed and flagged, no repo pin read or written), with the repo, run,
    branch and commit it came from recorded as its provenance."""
    _check_csrf(request, session, csrf_token)
    back = f"/repos/{int(repo_id)}/artifacts"
    repo, problem = await _pinned_repo(repo_id)
    if problem:
        return _redirect(back, error=problem)
    owner, name, token = repo["owner"], repo["repo"], poller.github_token()
    tmp_path = _new_upload_tmp()
    try:
        artifact = await github_client.get_artifact(owner, name, artifact_id, repo["github_id"], token)
        digest = await github_client.download_artifact(owner, name, artifact.id, tmp_path, token)
    except github_client.GithubError as exc:
        staging.remove_file(tmp_path)
        return _redirect(back, error=f"Artifact refused: {exc}")
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

    def remember(verified: VerifiedUpload) -> None:
        kind = "unsigned" if verified.server_signed else "debug" if verified.signer.debug else "signed"
        db.record_artifact_signing(artifact.id, repo_id, kind,
                                   None if verified.server_signed else verified.signer.fingerprint)

    # apksigner and aapt2 block for seconds: keep them off the event loop.
    return await asyncio.to_thread(_stage_received, request, tmp_path, digest,
                                   _display_filename(f"{artifact.name}.zip"), label, origin, back, notes,
                                   provenance, bool(repo["sign_unsigned"]), remember)


SIGNING_LABELS = {"signed": "signed with a real key", "debug": "signed with a debug key",
                  "unsigned": "unsigned", "invalid": "not a valid APK build"}


def _inspect_signing(tmp_path: str) -> tuple[str, str | None, str]:
    """What a downloaded artifact is signed with: (kind, signer, detail)."""
    try:
        _unwrap_archive(tmp_path)
        apk_verify.assert_apk_container(tmp_path)
        if apk_verify.is_unsigned(tmp_path):
            return "unsigned", None, ""
        signer = apk_verify.verify_signature(tmp_path)
    except apk_verify.ApkVerifyError as exc:
        return "invalid", None, str(exc)
    return ("debug" if signer.debug else "signed"), signer.fingerprint, ""


@app.post("/repos/{repo_id}/artifacts/{artifact_id}/check")
async def check_artifact_signing(
    repo_id: int, artifact_id: int, request: Request,
    session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
):
    """Downloads a test build only to see how it's signed, then deletes it.
    The answer is kept: an artifact never changes."""
    _check_csrf(request, session, csrf_token)
    back = f"/repos/{int(repo_id)}/artifacts"
    repo, problem = await _pinned_repo(repo_id)
    if problem:
        return _redirect(back, error=problem)
    token = poller.github_token()
    tmp_path = _new_upload_tmp()
    try:
        artifact = await github_client.get_artifact(repo["owner"], repo["repo"], artifact_id, repo["github_id"], token)
        await github_client.download_artifact(repo["owner"], repo["repo"], artifact.id, tmp_path, token)
        kind, signer, detail = await asyncio.to_thread(_inspect_signing, tmp_path)
    except github_client.GithubError as exc:
        return _redirect(back, error=f"Couldn't check it: {exc}")
    finally:
        staging.remove_file(tmp_path)
    db.record_artifact_signing(artifact.id, repo_id, kind, signer, detail)
    return _redirect(f"{back}#artifact-{artifact.id}", ok=f"{artifact.name}: {SIGNING_LABELS[kind]}")


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


def _render_settings(
    request: Request, session: dict, draft: dict | None = None, page: str = "notifications", **flash,
) -> HTMLResponse:
    """One of the Settings pages ("overview" is /settings itself)."""
    name = "settings.html" if page == "overview" else f"settings_{page}.html"
    return templates.TemplateResponse(request, name, _tctx(
        request, session, **flash, draft=draft or {}, settings_page=page,
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
        token_source=poller.token_source(), artifact_filter=_artifact_filter(),
        time_zone=_zone_name(), time_zones=TIME_ZONES, clock_24h=_clock_24h(),
    ))


# ---- settings: GitHub token and artifacts ----

ARTIFACT_GLOB_RE = re.compile(r"^[A-Za-z0-9*?._\-\[\]]{0,100}$")


TOKEN_TEMPLATE_DAYS = 90
TOKEN_EXPIRY_WARN_DAYS = 7


def _token_template_url(request: Request) -> str:
    """GitHub's fine-grained token page, pre-filled with exactly what this app
    needs: read-only Contents, Actions and Metadata. Which repos it covers is
    the one thing GitHub can't pre-fill; the operator picks them there."""
    owners = sorted({r["owner"] for r in db.list_repos()})
    params = {
        "name": f"ADB Server {request.url.hostname or ''}".strip()[:40],
        "description": "Read-only: releases and workflow artifacts for ADB Server",
        "expires_in": str(TOKEN_TEMPLATE_DAYS),
        "contents": "read", "actions": "read", "metadata": "read",
    }
    if len(owners) == 1:
        params["target_name"] = owners[0]
    return "https://github.com/settings/personal-access-tokens/new?" + "&".join(
        f"{k}={quote(v)}" for k, v in params.items())


def _token_expiry() -> dict | None:
    """When the saved token expires, as GitHub reported it at save or test."""
    raw = db.get_meta("github_token_expires")
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    days = (when - datetime.now(timezone.utc)).days
    return {"date": when.date().isoformat(), "days": days, "soon": days < TOKEN_EXPIRY_WARN_DAYS}


def _record_token_status(status: github_client.TokenStatus) -> None:
    db.set_meta("github_token_expires", status.expires_at)


async def _repo_access(repo, token: str) -> dict:
    """Can the token read this repo, and download its artifacts? Checked
    for real: a public repo is readable with any token, so only the artifact
    download (a redirect GitHub hands out only when allowed) proves access."""
    row = {"name": f"{repo['owner']}/{repo['repo']}", "id": repo["id"], "repo": None, "artifacts": None, "note": ""}
    try:
        await github_client.get_repo_info(repo["owner"], repo["repo"], token)
        row["repo"] = True
    except github_client.GithubError as exc:
        row["repo"], row["note"] = False, str(exc)
        return row
    try:
        artifacts = await github_client.list_artifacts(repo["owner"], repo["repo"], repo["github_id"] or 0, token)
        if not artifacts:
            row["note"] = "No artifacts to test with yet"
            return row
        row["artifacts"] = await github_client.can_download_artifact(repo["owner"], repo["repo"], artifacts[0].id, token)
        if not row["artifacts"]:
            row["note"] = "The token can't download its artifacts: add this repo to it, with Actions: read"
    except github_client.GithubError as exc:
        row["note"] = str(exc)
    return row


async def _access_report(token: str) -> list[dict]:
    repos = db.list_repos()
    return list(await asyncio.gather(*(_repo_access(r, token) for r in repos)))


def _artifact_filter() -> dict:
    return {"hide_dockerbuild": db.get_meta("artifacts_hide_dockerbuild") != "0",
            "name_glob": db.get_meta("artifacts_name_glob") or ""}


def _artifact_shown(artifact: github_client.Artifact, flt: dict) -> bool:
    name = artifact.name.lower()
    # docker/build-push-action uploads a build record named <owner>~<repo>~<id>.dockerbuild.
    if flt["hide_dockerbuild"] and name.endswith(".dockerbuild"):
        return False
    return not flt["name_glob"] or fnmatch.fnmatch(name, flt["name_glob"].lower())


@app.post("/settings/github-token")
async def save_github_token(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), token: str = Form(...),
):
    """Saves a GitHub token, encrypted, once GitHub confirms it works. The
    token is never echoed back, logged, or put in a redirect."""
    _check_csrf(request, session, csrf_token)
    token = token.strip()
    if not github_client.TOKEN_RE.fullmatch(token):
        return _redirect("/settings/github", error="That doesn't look like a GitHub token "
                                                       "(ghp_… or github_pat_…) — nothing was saved")
    try:
        status = await github_client.check_token(token)
    except github_client.GithubError as exc:
        return _redirect("/settings/github", error=f"Not saved: {exc}")
    db.set_secret("github_token", token)
    _record_token_status(status)
    _audit(request, "github_token_set", f"login={status.login} expires={status.expires_at or 'never'}")
    return _redirect("/settings/github",
                     ok=f"Token saved. GitHub knows it as {status.login} ({status.rate_limit} requests/hour).")


@app.post("/settings/github-token/test")
async def test_github_token(request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    token = poller.github_token()
    if not token:
        return _redirect("/settings/github", error="No GitHub token is set")
    try:
        status = await github_client.check_token(token)
    except github_client.GithubError as exc:
        return _redirect("/settings/github", error=str(exc))
    if poller.token_source() == "settings":
        _record_token_status(status)
    return _redirect("/settings/github",
                     ok=f"The token works: GitHub knows it as {status.login} ({status.rate_limit} requests/hour).")


@app.post("/settings/github-token/clear")
def clear_github_token(request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    db.set_meta("github_token", None)
    db.set_meta("github_token_expires", None)
    _audit(request, "github_token_cleared")
    fallback = " GITHUB_TOKEN from .env is used instead." if poller.GITHUB_TOKEN else ""
    return _redirect("/settings/github", ok="Token removed." + fallback)


@app.post("/settings/artifacts")
def save_artifact_filter(
    request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
    hide_dockerbuild: str = Form(""), name_glob: str = Form(""),
):
    _check_csrf(request, session, csrf_token)
    name_glob = name_glob.strip()
    if not ARTIFACT_GLOB_RE.fullmatch(name_glob):
        return _redirect("/settings/github", error="The name pattern may use letters, digits, . _ - * ? and [ ], "
                                                       "up to 100 characters")
    db.set_meta("artifacts_hide_dockerbuild", "1" if hide_dockerbuild == "1" else "0")
    db.set_meta("artifacts_name_glob", name_glob or None)
    _audit(request, "artifact_filter", f"hide_dockerbuild={hide_dockerbuild == '1'} glob={name_glob or '*'}")
    return _redirect("/settings/github", ok="Artifact filter saved")


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None,
):
    return _render_settings(request, session, page="overview", error=error, ok=ok, warn=warn,
                            token_expiry=_token_expiry())


@app.get("/settings/general", response_class=HTMLResponse)
@app.get("/settings/security", response_class=HTMLResponse)
@app.get("/settings/notifications", response_class=HTMLResponse)
@app.get("/settings/appearance", response_class=HTMLResponse)
def settings_subpage(
    request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None,
):
    page = request.url.path.rsplit("/", 1)[-1]
    extra = {}
    if page == "security":
        try:
            extra["signing_fingerprint"] = signing.key_fingerprint()
        except signing.SigningError:
            extra["signing_fingerprint"] = None
    return _render_settings(request, session, page=page, error=error, ok=ok, warn=warn, **extra)


@app.get("/settings/github", response_class=HTMLResponse)
async def settings_github(
    request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None,
):
    token = poller.github_token()
    access = await _access_report(token) if token else []
    return _render_settings(request, session, page="github", error=error, ok=ok, warn=warn,
                            access=access, token_expiry=_token_expiry(),
                            token_template_url=_token_template_url(request))


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
        return _redirect("/settings/notifications", error=str(exc))
    return _store_notify_target(request, url, label)


def _store_notify_target(request: Request, url: str, label: str) -> RedirectResponse:
    if len(db.list_notify_targets()) >= notify.MAX_TARGETS:
        return _redirect("/settings/notifications", error=f"You can store up to {notify.MAX_TARGETS} services")
    if db.add_notify_target(url, label.strip()[:80] or None) is None:
        return _redirect("/settings/notifications", error="That service is already configured")
    info = notify.describe(url)
    _audit(request, "notify_add", f"{info['service']} {info['masked']}")
    return _redirect("/settings/notifications", ok=f"Added {info['service']} — send a test to check it")


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
        return _redirect("/settings/notifications", error=str(exc))
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
    return _redirect("/settings/notifications", ok=f"Removed {info['service']}")


@app.post("/settings/notify/events")
def set_notify_events(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), events: list[str] = Form([]),
):
    _check_csrf(request, session, csrf_token)
    chosen = sorted(set(events) & set(notify.EVENTS))
    db.set_meta("notify_events", ",".join(chosen))
    _audit(request, "notify_events", ",".join(chosen) or "(none)")
    return _redirect("/settings/notifications", ok="Notification events saved" if chosen else "All notification events turned off")


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
            return _redirect("/settings/notifications", error="No notification services are configured yet")
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
        return _redirect("/settings/notifications", ok=f"Test sent — {summary}")
    return _redirect("/settings/notifications", error=f"Test failed for some services — {summary}")


MAX_SAVED_COLOURS = 20


def _apply_colours(request: Request, primary: str | None, secondary: str | None, name: str | None = None):
    db.set_meta("accent_color", primary)
    db.set_meta("accent2_color", secondary)
    if primary is None:
        _audit(request, "accent_reset")
        return _redirect("/settings/appearance", ok="Colours reset to the default teal and ocean")
    _audit(request, "accent_set", f"{primary} / {secondary}")
    return _redirect("/settings/appearance", ok=f"Colours set to {name or f'{primary} and {secondary}'}")


# ---- settings: two-factor sign-in ----

def _settings_error(message: str) -> RedirectResponse:
    return _redirect("/settings/security", error=message)


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
        return _redirect("/settings/security")
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
        return _redirect("/settings/security")
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
        return _redirect("/settings/security")
    if (error := _check_current_code(code)):
        return _settings_error(error)
    _audit(request, "mfa_recovery_codes_replaced")
    return _show_recovery_codes(request, session, mfa.new_recovery_codes())


@app.post("/settings/mfa/disable")
def mfa_disable(request: Request, session: dict = Depends(auth.require_auth),
                csrf_token: str = Form(...), code: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    if not mfa.enabled():
        return _redirect("/settings/security")
    if (error := _check_current_code(code)):
        return _settings_error(error)
    mfa.disable()
    _audit(request, "mfa_disabled")
    response = _redirect("/settings/security", ok="Two-factor sign-in is off")
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
    return _redirect("/settings/security", ok="That browser will be asked for a code next time" if browser
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
            return _redirect("/settings/appearance", error="That saved colour pair no longer exists")
        return _apply_colours(request, row["primary_color"], row["secondary_color"], row["name"])
    if preset:
        if preset not in appearance.PRESETS:
            return _redirect("/settings/appearance", error="Unknown colour preset")
        primary, secondary = (None, None) if preset == appearance.DEFAULT_PRESET else appearance.PRESETS[preset]
    elif accent:
        try:
            primary = appearance.normalize(accent)
            secondary = appearance.normalize(accent2 or appearance.PRESETS[appearance.DEFAULT_PRESET][1])
        except ValueError as exc:
            return _redirect("/settings/appearance", error=str(exc))
    else:
        primary = secondary = None
    return _apply_colours(request, primary, secondary)


@app.post("/settings/timezone")
def set_timezone(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), tz: str = Form(...), clock: str = Form("12"),
):
    """The zone and clock every date and time in the app is shown in (all are
    stored in UTC)."""
    _check_csrf(request, session, csrf_token)
    if tz not in TIME_ZONES:
        return _redirect("/settings/general", error="Pick a time zone from the list")
    if clock not in ("12", "24"):
        return _redirect("/settings/general", error="Pick a 12-hour or a 24-hour clock")
    db.set_meta("timezone", tz)
    db.set_meta("clock", clock)
    _display_zone.clear()
    _clock.clear()
    _audit(request, "timezone", f"{tz} {clock}h")
    return _redirect("/settings/general", ok=f"Times are now shown in {tz}, on a {clock}-hour clock")


@app.post("/settings/appearance/save")
def save_colours(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), name: str = Form(""), accent: str = Form(...), accent2: str = Form(...),
):
    """Saves the custom pair under a name (re-saving a name updates it) and applies it."""
    _check_csrf(request, session, csrf_token)
    name = " ".join(name.split())
    if not name or len(name) > 40:
        return _redirect("/settings/appearance", error="Give the colour pair a name of up to 40 characters")
    try:
        primary, secondary = appearance.normalize(accent), appearance.normalize(accent2)
    except ValueError as exc:
        return _redirect("/settings/appearance", error=str(exc))
    names = {r["name"] for r in db.list_saved_colours()}
    if name not in names and len(names) >= MAX_SAVED_COLOURS:
        return _redirect("/settings/appearance", error=f"You can save up to {MAX_SAVED_COLOURS} colour pairs — delete one first")
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
    return _redirect("/settings/appearance", ok=f"Deleted the saved pair “{row['name']}”")


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
