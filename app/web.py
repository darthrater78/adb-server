"""Shared by every page: the templates and their filters, the display time
zone, the context every page is rendered with, and the helpers every route
uses (CSRF check, audit record, redirect)."""
import json
import os
import zoneinfo
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

import audit
import auth
import db
import signing


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
templates = Jinja2Templates(directory="templates")


TIME_ZONES = sorted(zoneinfo.available_timezones() | {"UTC"})
_display_zone: list = []  # [ZoneInfo], loaded on first use and replaced when saved


def zone_name() -> str:
    """Settings → Appearance, else TZ from the environment, else UTC."""
    for name in (db.get_meta("timezone"), os.environ.get("TZ")):
        if name in TIME_ZONES:
            return name
    return "UTC"


_clock: list = []  # [bool: 24-hour], cached like the zone


def clock_24h() -> bool:
    if not _clock:
        _clock.append(db.get_meta("clock") == "24")
    return _clock[0]


def display_zone() -> zoneinfo.ZoneInfo:
    if not _display_zone:
        _display_zone.append(zoneinfo.ZoneInfo(zone_name()))
    return _display_zone[0]


def when(value: str | None, empty: str = "never") -> Markup:
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
    if clock_24h():
        clock = f"{local:%H:%M}"
    else:
        clock = f"{local.hour % 12 or 12}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"
    label = f"{local:%b} {local.day}{year}, {clock} {local.tzname() or ''}".rstrip()
    return Markup('<time datetime="{0}" title="{0}">{1}</time>').format(value, label)


templates.env.filters["when"] = when


def device_name(d) -> str:
    """What to call a device: its nickname, else its model with the end of
    its serial (two identical phones stay distinguishable), else the serial."""
    if d["nickname"]:
        return d["nickname"]
    model = d["model"] if "model" in d.keys() else None
    return f"{model} · …{d['serial'][-5:]}" if model else d["serial"]


templates.env.filters["device_name"] = device_name


def siblings(raw: str | None) -> list[dict]:
    """Other builds of an artifact's commit, as stored at staging time."""
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("id"), int) and isinstance(i.get("name"), str)]


templates.env.filters["siblings"] = siblings


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


def context(request: Request, session: dict | None = None, **extra) -> dict:
    ctx = {
        "app_version": APP_VERSION,
        "repo_url": REPO_URL,
        "release_notes_url": RELEASE_NOTES_URL,
        "sign_explanation": signing.EXPLANATION,
        "weak_settings": auth.WEAK_SETTINGS,
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


def check_csrf(request: Request, session: dict, csrf_token: str | None) -> None:
    auth.require_csrf(request, session, csrf_token)


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def record_audit(request: Request, action: str, detail: str = "") -> None:
    audit.record(action, detail, client_ip(request))


def redirect(path: str, status_code: int = 303, **params) -> RedirectResponse:
    path, hash_, fragment = path.partition("#")
    if params:
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items() if v is not None)
        path = f"{path}?{qs}" if qs else path
    return RedirectResponse(path + hash_ + fragment, status_code=status_code)


def moved(request: Request, path: str) -> RedirectResponse:
    """Old page URLs from before 3.1 land on the page that replaced them,
    flash message and all."""
    query = request.url.query
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=303)


def forget_display_settings() -> None:
    """Drops the cached zone and clock, once Settings → General saves new ones."""
    _display_zone.clear()
    _clock.clear()
