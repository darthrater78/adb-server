"""ADB Server: the FastAPI app, its middleware and the poll scheduler. The pages
themselves are in the routes_* modules."""
import logging
import os
import tempfile
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

import auth
import db
import poller
import routes_auth
import routes_builds
import routes_devices
import routes_install
import routes_settings
import routes_sources
import uploads

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
# Multipart boundaries and the csrf/label fields on top of the file itself.
UPLOAD_FORM_OVERHEAD = 64 * 1024
# Every other form in the app is a handful of short fields.
MAX_FORM_BYTES = 64 * 1024
UPLOAD_PATH = "/staged/upload"

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
        limit = uploads.MAX_UPLOAD_BYTES + UPLOAD_FORM_OVERHEAD
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


for module in (routes_auth, routes_sources, routes_builds, routes_install, routes_devices, routes_settings):
    app.include_router(module.router)
