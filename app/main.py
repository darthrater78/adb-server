import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from urllib.parse import quote

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

import adb_client
import audit
import auth
import db
import discovery
import github_client
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


def _read_version() -> str:
    try:
        with open(os.path.join(os.path.dirname(__file__), "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "unknown"


APP_VERSION = _read_version()

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    scheduler.add_job(
        poller.poll_all_repos, "interval",
        minutes=POLL_INTERVAL_MINUTES, id="poll_all_repos",
        max_instances=1, coalesce=True,
    )
    scheduler.start()
    logger.info("Polling every %s minutes", POLL_INTERVAL_MINUTES)
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


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
KNOWN_NAV_PATHS = {"/status", "/repos", "/staged", "/devices", "/installs", "/audit"}


def _get_theme(request: Request) -> str:
    theme = request.cookies.get("theme", "")
    return theme if theme in VALID_THEMES else "auto"


def _tctx(request: Request, session: dict | None = None, **extra) -> dict:
    ctx = {
        "app_version": APP_VERSION,
        "theme": _get_theme(request),
        "current_path": request.url.path if request.url.path in KNOWN_NAV_PATHS else "/repos",
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
    if params:
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items() if v is not None)
        path = f"{path}?{qs}" if qs else path
    return RedirectResponse(path, status_code=status_code)


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
    auth.check_rate_limit(request)
    if not auth.verify_credentials(username, password):
        auth.record_failed_attempt(request)
        _audit(request, "login_failed")
        return templates.TemplateResponse(
            request, "login.html", _tctx(request, error="Invalid username or password"), status_code=401,
        )
    response = RedirectResponse("/status", status_code=303)
    auth.create_session(response)
    _audit(request, "login")
    return response


@app.post("/logout")
def logout(request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    _audit(request, "logout")
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
    next: str = Form("/repos"),
):
    _check_csrf(request, session, csrf_token)
    if theme not in VALID_THEMES:
        raise HTTPException(status_code=400, detail="Unknown theme")
    next_path = next if next in KNOWN_NAV_PATHS else "/repos"
    response = RedirectResponse(next_path, status_code=303)
    # Cosmetic preference, not session state — plain cookie, not httponly, so
    # it stays simple and separate from the signed auth session.
    response.set_cookie(
        "theme", theme, max_age=365 * 24 * 60 * 60,
        samesite="lax", secure=auth.COOKIE_SECURE, path="/",
    )
    return response


# ---- repos ----

@app.get("/repos", response_class=HTMLResponse)
def repos_page(request: Request, session: dict = Depends(auth.require_auth), error: str | None = None, ok: str | None = None):
    return templates.TemplateResponse(
        request, "repos.html", _tctx(request, session, repos=db.list_repos(), error=error, ok=ok),
    )


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
        return _redirect("/repos", error=str(exc))
    try:
        db.create_repo(owner, repo, asset_glob, include_prereleases == "1")
    except sqlite3.IntegrityError:
        return _redirect("/repos", error="That repo is already registered")
    _audit(request, "repo_add", f"{owner}/{repo} glob={asset_glob}")
    return _redirect("/repos", ok="Repo added")


@app.post("/repos/{repo_id}/delete")
def delete_repo(repo_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    repo_row = db.get_repo(repo_id)
    db.delete_repo(repo_id)
    staging.remove_repo_dir(repo_id)
    if repo_row is not None:
        _audit(request, "repo_remove", f"{repo_row['owner']}/{repo_row['repo']}")
    return _redirect("/repos", ok="Repo removed")


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
    return _redirect("/repos", ok="Check started — reload this page in a moment to see the result")


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
    return _redirect("/repos", ok="Updated")


@app.post("/repos/{repo_id}/accept-signer")
def accept_signer(
    repo_id: int, request: Request, background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), confirm: str = Form(""),
):
    _check_csrf(request, session, csrf_token)
    if confirm != "yes":
        return _redirect("/repos", error="Tick the confirmation box to accept a new signer")
    before = db.get_repo(repo_id)
    if before is None or not db.accept_pending_signer(repo_id):
        return _redirect("/repos", error="No pending signer change for that repo")
    logger.warning("repo %s: operator accepted a new signing certificate", repo_id)
    _audit(
        request, "signer_accepted",
        f"{before['owner']}/{before['repo']} {before['signer_sha256']} -> {before['pending_signer']} "
        f"package={before['pending_package']} lineage_proven={bool(before['pending_lineage_ok'])}",
    )
    background_tasks.add_task(poller.check_repo, db.get_repo(repo_id))
    return _redirect("/repos", ok="New signer pinned — re-checking the release now")


# ---- staged apks ----

@app.get("/staged", response_class=HTMLResponse)
def staged_page(request: Request, session: dict = Depends(auth.require_auth), error: str | None = None, ok: str | None = None):
    return templates.TemplateResponse(
        request, "staged.html",
        _tctx(request, session, apks=db.list_staged_apks(), devices=db.list_devices(), error=error, ok=ok),
    )


@app.post("/staged/{apk_id}/delete")
def delete_staged(apk_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    apk = db.get_staged_apk(apk_id)
    if apk is None:
        raise HTTPException(status_code=404)
    staging.remove_file(apk["path"])
    db.mark_apk_pruned(apk_id)
    _audit(request, "staged_delete", f"{apk['owner']}/{apk['repo']} {apk['tag']} {apk['filename']}")
    return _redirect("/staged", ok="Staged file deleted")


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
    try:
        adb_client.pair(pairing_addr.strip(), pairing_code.strip())
        adb_client.connect(connect_addr.strip())
        serial = adb_client.get_serialno(connect_addr.strip())
    except adb_client.AdbError as exc:
        return _redirect("/devices", error=str(exc))
    db.upsert_paired_device(serial, connect_addr.strip())
    pushes.refresh_abis(serial)
    _audit(request, "device_pair", f"{serial} at {connect_addr.strip()}")
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
    try:
        adb_client.connect(connect_addr.strip())
        confirmed_serial = adb_client.get_serialno(connect_addr.strip())
    except adb_client.AdbError as exc:
        return _redirect("/devices", error=str(exc))
    if confirmed_serial != serial:
        return _redirect("/devices", error="That address now answers as a different device - not updated")
    db.touch_device(serial, connect_addr.strip())
    pushes.refresh_abis(serial)
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
        addr = discovery.ensure_connected(device)
    except adb_client.AdbError as exc:
        return _redirect("/devices", error=str(exc))
    pushes.refresh_abis(serial)
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
    _audit(request, "push", f"{apk['owner']}/{apk['repo']} {apk['tag']} ({apk['filename']}) → {device['nickname'] or device['serial']}")
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
    return _queue_push(request, background_tasks, device, apk, "/staged")


@app.post("/push-latest")
def push_latest(
    request: Request,
    background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    device_serial: str = Form(...),
    repo_id: int = Form(...),
):
    """Pushes the repo's newest staged release, choosing the APK variant
    that fits the device's CPU."""
    _check_csrf(request, session, csrf_token)
    device = db.get_device(device_serial)
    if device is None:
        raise HTTPException(status_code=404)
    variants = [v for v in db.list_latest_variants() if v["repo_id"] == repo_id]
    if not variants:
        return _redirect("/status", error="Nothing staged for that repo")
    apk = selection.pick_variant(variants, device["abis"])
    if apk is None:
        return _redirect("/status", error="No APK in the latest release supports this device's CPU")
    return _queue_push(request, background_tasks, device, apk, "/status")


# ---- status ----

def _status_rows(devices) -> list[dict]:
    installed = db.device_packages_map()
    follows = db.follows_set()
    rows = []
    variants_by_repo: dict[int, list] = {}
    for v in db.list_latest_variants():
        variants_by_repo.setdefault(v["repo_id"], []).append(v)
    for repo_id, variants in variants_by_repo.items():
        latest = variants[0]
        cells = []
        for d in devices:
            have = installed.get((d["serial"], latest["package_name"]))
            cells.append({
                "device": d, "installed": have,
                "state": selection.update_state(have, latest),
                "fits": selection.pick_variant(variants, d["abis"]) is not None,
                "following": (d["serial"], repo_id) in follows,
            })
        rows.append({"repo_id": repo_id, "latest": latest, "cells": cells})
    return rows


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request, session: dict = Depends(auth.require_auth), error: str | None = None, ok: str | None = None):
    devices = [d for d in db.list_devices() if d["trusted"]]
    return templates.TemplateResponse(
        request, "status.html",
        _tctx(request, session, devices=devices, rows=_status_rows(devices), error=error, ok=ok),
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
            discovery.ensure_connected(device, allow_scan=False)
        except adb_client.AdbError:
            unreachable.append(device["nickname"] or device["serial"])
            continue
        pushes.refresh_abis(device["serial"])
        for package in packages:
            pushes.refresh_installed(device["serial"], package)
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
