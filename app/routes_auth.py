"""Sign-in password, then the two-factor code, sign-out, and the
unauthenticated health check."""
import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import auth
import db
import mfa
from web import check_csrf, client_ip, context, record_audit, redirect, templates

router = APIRouter()


# ---- auth ----

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if auth.read_session(request):
        return RedirectResponse("/status", status_code=303)
    return templates.TemplateResponse(request, "login.html", context(request))


@router.post("/login")
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
        record_audit(request, "login_failed")
        return templates.TemplateResponse(
            request, "login.html", context(request, error="Invalid username or password"), status_code=401,
        )
    if mfa.enabled() and not mfa.browser_trusted(auth.read_mfa_trust(request)):
        response = RedirectResponse("/login/mfa", status_code=303)
        auth.start_mfa(response)
        return response
    response = RedirectResponse("/status", status_code=303)
    auth.create_session(response)
    record_audit(request, "login", "trusted browser, no code asked" if mfa.enabled() else "")
    return response


def _mfa_form(request: Request, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, "login_mfa.html", context(
        request, error=error, trust_days=mfa.TRUST_DAYS,
    ), status_code=status_code)


def locked_message() -> str:
    minutes = max(1, -(-mfa.locked_for() // 60))
    return (f"Too many wrong codes — two-factor sign-in is locked for {minutes} more minute"
            f"{'s' if minutes != 1 else ''}. Or unlock it on the server: "
            "docker compose exec app python mfa_admin.py unlock")


@router.get("/login/mfa", response_class=HTMLResponse)
def login_mfa_form(request: Request):
    if not auth.mfa_pending(request):
        return RedirectResponse("/login", status_code=303)
    if mfa.locked_for():
        return _mfa_form(request, locked_message(), 429)
    return _mfa_form(request)


@router.post("/login/mfa")
def login_mfa_submit(request: Request, code: str = Form(...), trust: str = Form("")):
    """The second step: an authenticator code or a recovery code, after the
    password (proven by the short-lived pending cookie)."""
    auth.check_origin(request)
    auth.check_rate_limit(request)
    if not auth.mfa_pending(request) or not mfa.enabled():
        return RedirectResponse("/login", status_code=303)
    if mfa.locked_for():
        return _mfa_form(request, locked_message(), 429)
    kind = mfa.check(code)
    if kind is None:
        auth.record_failed_attempt(request)
        record_audit(request, "mfa_failed")
        if mfa.locked_for():
            record_audit(request, "mfa_locked", f"{mfa.MAX_FAILURES} wrong codes")
            return _mfa_form(request, locked_message(), 429)
        return _mfa_form(request, "That code didn't work — check the time on your phone, and try the newest code",
                         401)
    ok = None
    if kind == "recovery":
        left = db.recovery_codes_left()
        ok = (f"Signed in with a recovery code — {left} left. "
              + ("Make new ones in Settings → Security." if left <= 3 else ""))
    response = redirect("/status", ok=ok)
    auth.create_session(response)
    auth.clear_mfa_pending(response)
    if trust:
        label = mfa.browser_label(request.headers.get("user-agent", ""))
        auth.set_mfa_trust(response, mfa.trust_browser(label, client_ip(request)))
    record_audit(request, "login", ("with a recovery code" if kind == "recovery" else "with an authenticator code")
           + (f", trusted for {mfa.TRUST_DAYS} days" if trust else ""))
    return response


@router.post("/logout")
def logout(request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    check_csrf(request, session, csrf_token)
    record_audit(request, "logout")
    # Signed cookies can't be recalled, so logging out invalidates every
    # session issued so far — a copied cookie stops working too.
    auth.revoke_sessions()
    response = RedirectResponse("/login", status_code=303)
    auth.clear_session(response)
    return response


@router.get("/")
def index():
    return RedirectResponse("/status", status_code=303)


@router.get("/healthz")
def healthz():
    """Unauthenticated liveness check for Docker. Reveals nothing but ok/not."""
    try:
        db.ping()
    except sqlite3.Error:
        return JSONResponse({"status": "error"}, status_code=503)
    return {"status": "ok"}
