"""Settings: GitHub token and artifact filter, notifications, two-factor
sign-in, signing keys, appearance, time zone; and the audit log."""
import asyncio
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import appearance
import auth
import db
import github_client
import mfa
import notify
import poller
import signing
import web
from routes_auth import locked_message
from routes_builds import artifact_filter
from web import check_csrf, context, record_audit, redirect, templates

router = APIRouter()


# ---- theme ----

@router.post("/theme")
def set_theme(
    request: Request,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    theme: str = Form(...),
    next: str = Form("/status"),
):
    check_csrf(request, session, csrf_token)
    if theme not in web.VALID_THEMES:
        raise HTTPException(status_code=400, detail="Unknown theme")
    next_path = next if next in web.KNOWN_NAV_PATHS else "/status"
    response = RedirectResponse(next_path, status_code=303)
    # Cosmetic preference, not session state — plain cookie, not httponly, so
    # it stays simple and separate from the signed auth session.
    response.set_cookie(
        "theme", theme, max_age=365 * 24 * 60 * 60,
        samesite="lax", secure=auth.COOKIE_SECURE, path="/",
    )
    return response


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
    return templates.TemplateResponse(request, name, context(
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
        token_source=poller.token_source(), artifact_filter=artifact_filter(),
        time_zone=web.zone_name(), time_zones=web.TIME_ZONES, clock_24h=web.clock_24h(),
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


@router.post("/settings/github-token")
async def save_github_token(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), token: str = Form(...),
):
    """Saves a GitHub token, encrypted, once GitHub confirms it works. The
    token is never echoed back, logged, or put in a redirect."""
    check_csrf(request, session, csrf_token)
    token = token.strip()
    if not github_client.TOKEN_RE.fullmatch(token):
        return redirect("/settings/github", error="That doesn't look like a GitHub token "
                                                       "(ghp_… or github_pat_…) — nothing was saved")
    try:
        status = await github_client.check_token(token)
    except github_client.GithubError as exc:
        return redirect("/settings/github", error=f"Not saved: {exc}")
    db.set_secret("github_token", token)
    _record_token_status(status)
    record_audit(request, "github_token_set", f"login={status.login} expires={status.expires_at or 'never'}")
    return redirect("/settings/github",
                     ok=f"Token saved. GitHub knows it as {status.login} ({status.rate_limit} requests/hour).")


@router.post("/settings/github-token/test")
async def test_github_token(request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    check_csrf(request, session, csrf_token)
    token = poller.github_token()
    if not token:
        return redirect("/settings/github", error="No GitHub token is set")
    try:
        status = await github_client.check_token(token)
    except github_client.GithubError as exc:
        return redirect("/settings/github", error=str(exc))
    if poller.token_source() == "settings":
        _record_token_status(status)
    return redirect("/settings/github",
                     ok=f"The token works: GitHub knows it as {status.login} ({status.rate_limit} requests/hour).")


@router.post("/settings/github-token/clear")
def clear_github_token(request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    check_csrf(request, session, csrf_token)
    db.set_meta("github_token", None)
    db.set_meta("github_token_expires", None)
    record_audit(request, "github_token_cleared")
    fallback = " GITHUB_TOKEN from .env is used instead." if poller.GITHUB_TOKEN else ""
    return redirect("/settings/github", ok="Token removed." + fallback)


@router.post("/settings/artifacts")
def save_artifact_filter(
    request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
    hide_dockerbuild: str = Form(""), name_glob: str = Form(""),
):
    check_csrf(request, session, csrf_token)
    name_glob = name_glob.strip()
    if not ARTIFACT_GLOB_RE.fullmatch(name_glob):
        return redirect("/settings/github", error="The name pattern may use letters, digits, . _ - * ? and [ ], "
                                                       "up to 100 characters")
    db.set_meta("artifacts_hide_dockerbuild", "1" if hide_dockerbuild == "1" else "0")
    db.set_meta("artifacts_name_glob", name_glob or None)
    record_audit(request, "artifact_filter", f"hide_dockerbuild={hide_dockerbuild == '1'} glob={name_glob or '*'}")
    return redirect("/settings/github", ok="Artifact filter saved")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None,
):
    return _render_settings(request, session, page="overview", error=error, ok=ok, warn=warn,
                            token_expiry=_token_expiry())


def _signing_keys() -> list[dict]:
    """Each source's signing key, named after its source, for Settings → Security."""
    keys = []
    for source in signing.sources():
        if source == signing.UPLOADS:
            name = "Uploads"
        else:
            repo = db.get_repo_by_github_id(int(source.split("-", 1)[1]))
            name = f"{repo['owner']}/{repo['repo']}" if repo else f"A removed repo (GitHub ID {source.split('-', 1)[1]})"
        try:
            fingerprint = signing.key_fingerprint(source)
        except signing.SigningError:
            fingerprint = None
        keys.append({"name": name, "fingerprint": fingerprint})
    return keys


@router.get("/settings/general", response_class=HTMLResponse)
@router.get("/settings/security", response_class=HTMLResponse)
@router.get("/settings/notifications", response_class=HTMLResponse)
@router.get("/settings/appearance", response_class=HTMLResponse)
def settings_subpage(
    request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None,
):
    page = request.url.path.rsplit("/", 1)[-1]
    extra = {}
    if page == "security":
        extra["signing_keys"] = _signing_keys()
    return _render_settings(request, session, page=page, error=error, ok=ok, warn=warn, **extra)


@router.get("/settings/github", response_class=HTMLResponse)
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
    record_audit(request, "notify_test", f"unsaved {service}: {message}")
    if ok:
        return _render_settings(request, session, draft, ok=f"Test sent — {service}: {message}. Press Add to keep it.")
    return _render_settings(request, session, draft, error=f"Test failed — {service}: {message}")


@router.post("/settings/notify/try-server", response_class=HTMLResponse)
def try_apprise_server(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), server: str = Form(""), key: str = Form(""),
    tags: str = Form(""), label: str = Form(""),
):
    check_csrf(request, session, csrf_token)
    draft = {"server": server, "key": key, "tags": tags, "server_label": label}
    try:
        url, problem = notify.build_api_url(server, key, tags), None
    except ValueError as exc:
        url, problem = None, str(exc)
    return _try_target(request, session, url, problem, draft)


@router.post("/settings/notify/try", response_class=HTMLResponse)
def try_notify_url(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), url: str = Form(""), label: str = Form(""),
):
    check_csrf(request, session, csrf_token)
    draft = {"url": url, "url_label": label}
    try:
        checked, problem = notify.validate(url), None
    except ValueError as exc:
        checked, problem = None, str(exc)
    return _try_target(request, session, checked, problem, draft)


@router.post("/settings/notify/add")
def add_notify_target(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), url: str = Form(""), label: str = Form(""),
):
    check_csrf(request, session, csrf_token)
    try:
        url = notify.validate(url)
    except ValueError as exc:
        return redirect("/settings/notifications", error=str(exc))
    return _store_notify_target(request, url, label)


def _store_notify_target(request: Request, url: str, label: str) -> RedirectResponse:
    if len(db.list_notify_targets()) >= notify.MAX_TARGETS:
        return redirect("/settings/notifications", error=f"You can store up to {notify.MAX_TARGETS} services")
    if db.add_notify_target(url, label.strip()[:80] or None) is None:
        return redirect("/settings/notifications", error="That service is already configured")
    info = notify.describe(url)
    record_audit(request, "notify_add", f"{info['service']} {info['masked']}")
    return redirect("/settings/notifications", ok=f"Added {info['service']} — send a test to check it")


@router.post("/settings/notify/add-server")
def add_apprise_server(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), server: str = Form(""), key: str = Form(""),
    tags: str = Form(""), label: str = Form(""),
):
    check_csrf(request, session, csrf_token)
    try:
        url = notify.build_api_url(server, key, tags)
    except ValueError as exc:
        return redirect("/settings/notifications", error=str(exc))
    return _store_notify_target(request, url, label)


@router.post("/settings/notify/{target_id}/delete")
def delete_notify_target(
    target_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
):
    check_csrf(request, session, csrf_token)
    row = db.get_notify_target(target_id)
    if row is None:
        raise HTTPException(status_code=404)
    db.delete_notify_target(target_id)
    info = notify.describe(row["url"])
    record_audit(request, "notify_remove", f"{info['service']} {info['masked']}")
    return redirect("/settings/notifications", ok=f"Removed {info['service']}")


@router.post("/settings/notify/events")
def set_notify_events(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), events: list[str] = Form([]),
):
    check_csrf(request, session, csrf_token)
    chosen = sorted(set(events) & set(notify.EVENTS))
    db.set_meta("notify_events", ",".join(chosen))
    record_audit(request, "notify_events", ",".join(chosen) or "(none)")
    return redirect("/settings/notifications", ok="Notification events saved" if chosen else "All notification events turned off")


@router.post("/settings/notify/test")
def test_notify(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), target: str = Form("all"),
):
    """Sync route (threadpool): delivery is blocking network I/O."""
    check_csrf(request, session, csrf_token)
    if target == "all":
        chosen = notify.targets()
        if not chosen:
            return redirect("/settings/notifications", error="No notification services are configured yet")
    else:
        found = _find_target(target)
        if found is None:
            raise HTTPException(status_code=404)
        chosen = [found]
    results = []
    for t in chosen:
        ok, message = notify.test(t)
        results.append((ok, f"{notify.describe(t.url)['service']}: {message}"))
    record_audit(request, "notify_test", "; ".join(r[1] for r in results)[:500])
    summary = " · ".join(r[1] for r in results)
    if all(ok for ok, _ in results):
        return redirect("/settings/notifications", ok=f"Test sent — {summary}")
    return redirect("/settings/notifications", error=f"Test failed for some services — {summary}")


MAX_SAVED_COLOURS = 20


def _apply_colours(request: Request, primary: str | None, secondary: str | None, name: str | None = None):
    db.set_meta("accent_color", primary)
    db.set_meta("accent2_color", secondary)
    if primary is None:
        record_audit(request, "accent_reset")
        return redirect("/settings/appearance", ok="Colours reset to the default teal and ocean")
    record_audit(request, "accent_set", f"{primary} / {secondary}")
    return redirect("/settings/appearance", ok=f"Colours set to {name or f'{primary} and {secondary}'}")


# ---- settings: two-factor sign-in ----

def _settings_error(message: str) -> RedirectResponse:
    return redirect("/settings/security", error=message)


def _check_current_code(code: str) -> str | None:
    """For turning MFA off or replacing recovery codes: a current code (or
    recovery code), under the same lockout as sign-in. Returns an error."""
    if mfa.locked_for():
        return locked_message()
    if mfa.check(code) is None:
        return "That code didn't work" + (" — too many wrong codes, now locked" if mfa.locked_for() else "")
    return None


def _show_recovery_codes(request: Request, session: dict, codes: list[str]):
    response = templates.TemplateResponse(request, "mfa_codes.html", context(request, session, codes=codes))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/settings/mfa/setup", response_class=HTMLResponse)
def mfa_setup_page(request: Request, session: dict = Depends(auth.require_auth), error: str | None = None):
    if mfa.enabled():
        return redirect("/settings/security")
    secret = mfa.pending_secret(create=True)
    uri = mfa.provisioning_uri(secret, auth.APP_USERNAME)
    response = templates.TemplateResponse(request, "mfa_setup.html", context(
        request, session, error=error, qr_svg=mfa.qr_svg(uri), secret=mfa.format_secret(secret),
    ))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/settings/mfa/enable")
def mfa_enable(request: Request, session: dict = Depends(auth.require_auth),
               csrf_token: str = Form(...), code: str = Form(...)):
    check_csrf(request, session, csrf_token)
    if mfa.enabled():
        return redirect("/settings/security")
    codes = mfa.enable(code)
    if codes is None:
        return redirect("/settings/mfa/setup", error="That code didn't match — scan the QR code again and "
                                                      "enter the newest code")
    record_audit(request, "mfa_enabled")
    # Every other session signed in with just the password: sign them out,
    # and give this one a fresh cookie so it carries on.
    auth.revoke_sessions()
    csrf = auth.new_csrf_token()
    response = _show_recovery_codes(request, {"csrf": csrf}, codes)
    auth.create_session(response, csrf)
    return response


@router.post("/settings/mfa/recovery-codes")
def mfa_new_recovery_codes(request: Request, session: dict = Depends(auth.require_auth),
                           csrf_token: str = Form(...), code: str = Form(...)):
    check_csrf(request, session, csrf_token)
    if not mfa.enabled():
        return redirect("/settings/security")
    if (error := _check_current_code(code)):
        return _settings_error(error)
    record_audit(request, "mfa_recovery_codes_replaced")
    return _show_recovery_codes(request, session, mfa.new_recovery_codes())


@router.post("/settings/mfa/disable")
def mfa_disable(request: Request, session: dict = Depends(auth.require_auth),
                csrf_token: str = Form(...), code: str = Form(...)):
    check_csrf(request, session, csrf_token)
    if not mfa.enabled():
        return redirect("/settings/security")
    if (error := _check_current_code(code)):
        return _settings_error(error)
    mfa.disable()
    record_audit(request, "mfa_disabled")
    response = redirect("/settings/security", ok="Two-factor sign-in is off")
    response.delete_cookie(auth.MFA_TRUST_COOKIE, path="/")
    return response


@router.post("/settings/mfa/trusted/revoke")
def mfa_revoke_browser(request: Request, session: dict = Depends(auth.require_auth),
                       csrf_token: str = Form(...), browser: str = Form("")):
    """One trusted browser (by id), or every one (no id)."""
    check_csrf(request, session, csrf_token)
    if browser and not re.fullmatch(r"[0-9a-f]{64}", browser):
        raise HTTPException(status_code=400)
    db.delete_trusted_browser(browser or None)
    record_audit(request, "mfa_trust_revoked", "one browser" if browser else "all browsers")
    return redirect("/settings/security", ok="That browser will be asked for a code next time" if browser
                     else "Every browser will be asked for a code next time")


@router.post("/settings/appearance")
def set_accent(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), preset: str = Form(""), saved: str = Form(""),
    accent: str = Form(""), accent2: str = Form(""),
):
    """A preset pair, a saved pair, a custom primary + secondary, or
    (none of them) the default."""
    check_csrf(request, session, csrf_token)
    if saved:
        row = db.get_saved_colour(int(saved)) if saved.isdigit() else None
        if row is None:
            return redirect("/settings/appearance", error="That saved colour pair no longer exists")
        return _apply_colours(request, row["primary_color"], row["secondary_color"], row["name"])
    if preset:
        if preset not in appearance.PRESETS:
            return redirect("/settings/appearance", error="Unknown colour preset")
        primary, secondary = (None, None) if preset == appearance.DEFAULT_PRESET else appearance.PRESETS[preset]
    elif accent:
        try:
            primary = appearance.normalize(accent)
            secondary = appearance.normalize(accent2 or appearance.PRESETS[appearance.DEFAULT_PRESET][1])
        except ValueError as exc:
            return redirect("/settings/appearance", error=str(exc))
    else:
        primary = secondary = None
    return _apply_colours(request, primary, secondary)


@router.post("/settings/timezone")
def set_timezone(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), tz: str = Form(...), clock: str = Form("12"),
):
    """The zone and clock every date and time in the app is shown in (all are
    stored in UTC)."""
    check_csrf(request, session, csrf_token)
    if tz not in web.TIME_ZONES:
        return redirect("/settings/general", error="Pick a time zone from the list")
    if clock not in ("12", "24"):
        return redirect("/settings/general", error="Pick a 12-hour or a 24-hour clock")
    db.set_meta("timezone", tz)
    db.set_meta("clock", clock)
    web.forget_display_settings()
    record_audit(request, "timezone", f"{tz} {clock}h")
    return redirect("/settings/general", ok=f"Times are now shown in {tz}, on a {clock}-hour clock")


@router.post("/settings/appearance/save")
def save_colours(
    request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), name: str = Form(""), accent: str = Form(...), accent2: str = Form(...),
):
    """Saves the custom pair under a name (re-saving a name updates it) and applies it."""
    check_csrf(request, session, csrf_token)
    name = " ".join(name.split())
    if not name or len(name) > 40:
        return redirect("/settings/appearance", error="Give the colour pair a name of up to 40 characters")
    try:
        primary, secondary = appearance.normalize(accent), appearance.normalize(accent2)
    except ValueError as exc:
        return redirect("/settings/appearance", error=str(exc))
    names = {r["name"] for r in db.list_saved_colours()}
    if name not in names and len(names) >= MAX_SAVED_COLOURS:
        return redirect("/settings/appearance", error=f"You can save up to {MAX_SAVED_COLOURS} colour pairs — delete one first")
    db.save_colour(name, primary, secondary)
    record_audit(request, "colours_saved", f"{name}: {primary} / {secondary}")
    return _apply_colours(request, primary, secondary, name)


@router.post("/settings/appearance/saved/{colour_id}/delete")
def delete_saved_colours(
    colour_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
):
    """Removes the saved pair. Colours already in use stay as they are."""
    check_csrf(request, session, csrf_token)
    row = db.get_saved_colour(colour_id)
    if row is None:
        raise HTTPException(status_code=404)
    db.delete_saved_colour(colour_id)
    record_audit(request, "colours_deleted", row["name"])
    return redirect("/settings/appearance", ok=f"Deleted the saved pair “{row['name']}”")


@router.get("/accent.css")
def accent_css():
    """Public: the sign-in page uses it too, and it reveals only a colour.
    Generated here because the CSP forbids inline styles."""
    return Response(appearance.stylesheet(db.get_meta("accent_color"), db.get_meta("accent2_color"),
                                          db.list_saved_colours()),
                    media_type="text/css",
                    headers={"Cache-Control": "no-cache"})


# ---- audit ----

@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, session: dict = Depends(auth.require_auth)):
    return templates.TemplateResponse(request, "audit.html", context(request, session, entries=db.list_audit()))
