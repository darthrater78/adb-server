import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from urllib.parse import quote

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

import adb_client
import auth
import db
import discovery
import github_client
import poller
import selection
import staging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("adb_server")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
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
KNOWN_NAV_PATHS = {"/status", "/repos", "/staged", "/devices", "/installs"}


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


def _redirect(path: str, status_code: int = 303, **params) -> RedirectResponse:
    if params:
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items() if v is not None)
        path = f"{path}?{qs}" if qs else path
    return RedirectResponse(path, status_code=status_code)


# ---- auth ----

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if auth.read_session(request):
        return RedirectResponse("/repos", status_code=303)
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
        return templates.TemplateResponse(
            request, "login.html", _tctx(request, error="Invalid username or password"), status_code=401,
        )
    response = RedirectResponse("/repos", status_code=303)
    auth.create_session(response)
    return response


@app.post("/logout")
def logout(request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    response = RedirectResponse("/login", status_code=303)
    auth.clear_session(response)
    return response


@app.get("/")
def index():
    return RedirectResponse("/status", status_code=303)


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
):
    _check_csrf(request, session, csrf_token)
    asset_glob = asset_glob.strip() or "*.apk"
    try:
        owner, repo = github_client.parse_repo_reference(repo_url)
    except github_client.GithubError as exc:
        return _redirect("/repos", error=str(exc))
    try:
        db.create_repo(owner, repo, asset_glob)
    except sqlite3.IntegrityError:
        return _redirect("/repos", error="That repo is already registered")
    return _redirect("/repos", ok="Repo added")


@app.post("/repos/{repo_id}/delete")
def delete_repo(repo_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    db.delete_repo(repo_id)
    staging.remove_repo_dir(repo_id)
    return _redirect("/repos", ok="Repo removed")


@app.post("/repos/{repo_id}/check-now")
async def check_repo_now(repo_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    _check_csrf(request, session, csrf_token)
    repo_row = db.get_repo(repo_id)
    if repo_row is None:
        raise HTTPException(status_code=404)
    await poller.check_repo(repo_row)
    return _redirect("/repos", ok="Checked")


@app.post("/repos/{repo_id}/accept-signer")
async def accept_signer(
    repo_id: int, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), confirm: str = Form(""),
):
    _check_csrf(request, session, csrf_token)
    if confirm != "yes":
        return _redirect("/repos", error="Tick the confirmation box to accept a new signer")
    if not db.accept_pending_signer(repo_id):
        return _redirect("/repos", error="No pending signer change for that repo")
    logger.warning("repo %s: operator accepted a new signing certificate", repo_id)
    repo_row = db.get_repo(repo_id)
    await poller.check_repo(repo_row)
    return _redirect("/repos", ok="New signer pinned and release re-checked")


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
    _refresh_abis(serial)
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
    _refresh_abis(serial)
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
    _refresh_abis(serial)
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
    return _redirect("/devices", ok="Device forgotten")


# ---- push ----

def _refresh_abis(serial: str) -> str:
    """Best effort: a device that can't be queried keeps its last known ABIs."""
    try:
        abis = adb_client.device_abis(serial)
    except adb_client.AdbError:
        device = db.get_device(serial)
        return device["abis"] if device else ""
    db.set_device_abis(serial, abis)
    return " ".join(abis)


def _refresh_installed(serial: str, package: str) -> None:
    try:
        version = adb_client.installed_version(serial, package)
    except adb_client.AdbError:
        return
    if version is None:
        db.upsert_device_package(serial, package, installed=False)
    else:
        db.upsert_device_package(serial, package, installed=True, version_code=version[0], version_name=version[1])


def _run_push(install_id: int, device: dict, apk: dict) -> None:
    """Runs off the request thread (via BackgroundTasks, which Starlette
    executes in a threadpool for a sync callable) so a slow adb install
    doesn't block the single-worker event loop the poller also runs on."""
    db.set_install_status(install_id, "installing")
    try:
        # Wireless ADB ports drift. Re-resolve and re-confirm identity
        # immediately before installing (scanning the device's IP if its
        # stored port went stale) rather than trusting the last pairing.
        discovery.ensure_connected(device)
        device_abis = _refresh_abis(device["serial"])
        if not selection.compatible(apk["abis"], device_abis):
            raise adb_client.AdbError(
                f"{apk['filename']} is built for {apk['abis']}, but this device supports {device_abis}"
            )
        result = adb_client.install(device["serial"], apk["path"])
        log = (result.stdout or "") + (result.stderr or "")
        status = "success" if result.returncode == 0 and "Success" in result.stdout else "failed"
        db.finish_install(install_id, status, log.strip()[:8000])
        _refresh_installed(device["serial"], apk["package_name"])
    except adb_client.AdbError as exc:
        db.finish_install(install_id, "failed", str(exc))
    except Exception:
        # Anything unexpected here (DB hiccup, etc.) must still resolve the
        # install row — otherwise it's stuck "installing" forever and the
        # status page polls indefinitely with nothing to show for it.
        logger.exception("push job %s crashed", install_id)
        db.finish_install(install_id, "failed", "Internal error during push — check server logs")


def _queue_push(background_tasks: BackgroundTasks, device, apk, back: str) -> RedirectResponse:
    """Every push goes through here, so the checks can't be skipped by
    reaching a different route."""
    if apk["pruned_at"]:
        return _redirect(back, error="That release's file was pruned — it can no longer be pushed")
    # The actual security boundary: enforced here, server-side, not just by
    # hiding the button in the UI.
    if not device["trusted"]:
        return _redirect(back, error="Device is not trusted")
    if not selection.compatible(apk["abis"], device["abis"]):
        return _redirect(back, error=f"{apk['filename']} doesn't support this device's CPU ({device['abis']})")
    install_id = db.insert_install(device["serial"], apk["id"], status="pending")
    background_tasks.add_task(_run_push, install_id, dict(device), dict(apk))
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
    return _queue_push(background_tasks, device, apk, "/staged")


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
    return _queue_push(background_tasks, device, apk, "/status")


# ---- status ----

def _status_rows(devices) -> list[dict]:
    installed = db.device_packages_map()
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
        _refresh_abis(device["serial"])
        for package in packages:
            _refresh_installed(device["serial"], package)
    if unreachable:
        return _redirect("/status", error="Not reachable (try Find on the Devices page): " + ", ".join(unreachable))
    return _redirect("/status", ok="Installed versions refreshed")


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
