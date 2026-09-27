"""Status, Library and install history: what each device has, what is
staged, and pushing one to the other."""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import adb_client
import auth
import db
import discovery
import pushes
import selection
import staging
from web import check_csrf, context, dom_id, moved, record_audit, redirect, templates

router = APIRouter()

RUNNING = ("pending", "installing")


# ---- library: every staged apk, by app ----

# The Library's folded cards, one per kind of source.
KIND_CARDS = {"release": "kind-release", "artifact": "kind-artifact", "upload": "kind-upload"}


def apk_kind(apk) -> str:
    if apk["repo_id"] is not None:
        return "release"
    return "artifact" if apk["artifact_repo"] else "upload"


def library_row_id(apk) -> str:
    return f"app-{apk['repo_id']}" if apk["repo_id"] is not None else f"upload-{apk['id']}"


def library_landing(apk, **flash) -> RedirectResponse:
    """The Library, opened on the card and row of a staged APK."""
    return redirect(f"/library#{library_row_id(apk)}", card=KIND_CARDS[apk_kind(apk)], **flash)


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


# How long a finished push stays in the activity strip.
ACTIVITY_SECONDS = 120


def _activity(installs: list) -> list:
    """Pushes running now, and those that finished in the last two minutes:
    what Status and Library show (refreshing while any runs) after a push,
    instead of sending you to a separate progress page."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ACTIVITY_SECONDS)).isoformat()
    return [i for i in installs if i["status"] in RUNNING or (i["finished_at"] or "") >= cutoff][:10]


@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None, to: str | None = None,
    show: str | None = None,
):
    """`to` picks the device every Push on the page targets (default: the
    first trusted one); anything else falls back to the default. `show`
    narrows the list to one kind (release, artifact, upload)."""
    trusted = [d for d in db.list_devices() if d["trusted"]]
    target = next((d for d in trusted if d["serial"] == to), trusted[0] if trusted else None)
    groups = _install_groups()
    kinds = list(dict.fromkeys(g["kind"] for g in groups))
    show = show if show in kinds else None
    if show:
        groups = [g for g in groups if g["kind"] == show]
    installed = db.device_packages_map()
    installs = db.list_installs()
    busy = {i["package_name"]: i for i in installs
            if target and i["device_serial"] == target["serial"] and i["status"] in RUNNING}
    for g in groups:
        # Trusted devices that have this card's latest version right now.
        first = g["releases"][0]["apks"][0]
        g["on"] = [
            d for d in trusted
            if (row := installed.get((d["serial"], first["package_name"]))) is not None and row["installed"]
            and row["version_code"] is not None and row["version_code"] == first["version_code"]
        ]
        # What the target device has of this app, if anything, and whether a push of it is running.
        row = installed.get((target["serial"], first["package_name"])) if target else None
        g["target_has"] = row if row is not None and row["installed"] else None
        g["busy"] = busy.get(first["package_name"])
    activity = _activity(installs)
    return templates.TemplateResponse(
        request, "library.html",
        context(request, session, card_ids=KIND_CARDS.values(), groups=groups, kinds=kinds, show=show,
                devices=trusted, target=target, activity=activity,
                running=any(i["status"] in RUNNING for i in activity), error=error, ok=ok, warn=warn),
    )


@router.get("/install")
@router.get("/staged")
def old_install_page(request: Request, session: dict = Depends(auth.require_auth)):
    return moved(request, "/library")


@router.post("/staged/{apk_id}/delete")
def delete_staged(apk_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    check_csrf(request, session, csrf_token)
    apk = db.get_staged_apk(apk_id)
    if apk is None:
        raise HTTPException(status_code=404)
    staging.remove_file(apk["path"])
    db.mark_apk_pruned(apk_id)
    record_audit(request, "staged_delete", f"{apk['source_label']} {apk['tag']} {apk['filename']}")
    return redirect("/library", card=KIND_CARDS[apk_kind(apk)], ok="Staged file deleted")


# ---- push ----

# Where a push was started from, and so where it leads back to: the two
# pages that offer one.
PUSH_BACK_PATHS = {"/status", "/library"}


def _back(path: str, device) -> str:
    """The page to return to after a push. Library keeps the device it was
    pushing to selected."""
    if path == "/library" and device is not None:
        return f"/library?to={quote(device['serial'], safe='')}"
    return path if path in PUSH_BACK_PATHS else "/status"


def status_row_id(apk, device) -> str:
    """The row a staged APK has in a device's card on Status."""
    dom = dom_id(device["serial"])
    return f"app-{apk['repo_id']}-{dom}" if apk["repo_id"] is not None else f"upload-{apk['id']}-{dom}"


def _landing(back: str, device, apk, **flash) -> RedirectResponse:
    """Back where the push started, opened on the card and row it came from,
    which shows it running (the page refreshes itself until it's done)."""
    if back.startswith("/library"):
        return redirect(f"{back}#{library_row_id(apk)}", card=KIND_CARDS[apk_kind(apk)], **flash)
    return redirect(f"/status#{status_row_id(apk, device)}", card=dom_id(device["serial"]), **flash)


def queue_push(request: Request, background_tasks: BackgroundTasks, device, apk, back: str,
               **flash) -> RedirectResponse:
    """Records the push and runs it after the response. `flash` is a message
    to show on arrival when it's queued (Stage and install says what it staged)."""
    back = _back(back, device)
    try:
        install_id = pushes.create_install(device, apk)
    except pushes.PushRefused as exc:
        return _landing(back, device, apk, error=str(exc))
    record_audit(request, "push", f"{apk['source_label']} {apk['tag']} ({apk['filename']}) → {device['nickname'] or device['serial']}")
    background_tasks.add_task(pushes.run_push, install_id, dict(device), dict(apk))
    return _landing(back, device, apk, **flash)


@router.post("/push")
def push(
    request: Request,
    background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    device_serial: str = Form(...),
    apk_id: int = Form(...),
    back: str = Form("/library"),
):
    check_csrf(request, session, csrf_token)
    device = db.get_device(device_serial)
    apk = db.get_staged_apk(apk_id)
    if device is None or apk is None:
        raise HTTPException(status_code=404)
    return queue_push(request, background_tasks, device, apk, back)


@router.post("/push-latest")
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
    check_csrf(request, session, csrf_token)
    device = db.get_device(device_serial)
    if device is None:
        raise HTTPException(status_code=404)
    variants = [v for v in db.list_latest_variants() if v["repo_id"] == repo_id]
    if not variants:
        return redirect(_back(back, device), error="Nothing staged for that repo")
    apk = selection.pick_variant(variants, device["abis"])
    if apk is None:
        return _landing(_back(back, device), device, variants[0],
                        error="No APK in the latest release supports this device's CPU")
    return queue_push(request, background_tasks, device, apk, back)


def _run_pushes(jobs: list[tuple[int, dict, dict]]) -> None:
    """One device, one install at a time: adb installs to the same phone
    don't run side by side."""
    for install_id, device, apk in jobs:
        pushes.run_push(install_id, device, apk)


@router.post("/update-all")
def update_all(
    request: Request,
    background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    device_serial: str = Form(...),
):
    """Every update Status offers for the device, queued together. The same
    checks as a single push, per app: create_install() refuses what it must."""
    check_csrf(request, session, csrf_token)
    device = db.get_device(device_serial)
    if device is None:
        raise HTTPException(status_code=404)
    card = next((c for c in _device_cards() if c["device"]["serial"] == device["serial"]), None)
    latest = db.list_latest_variants()
    jobs, refused = [], []
    for app in card["updates"] if card else []:
        apk = selection.pick_variant([v for v in latest if v["repo_id"] == app["repo_id"]], device["abis"])
        if apk is None:
            continue
        try:
            install_id = pushes.create_install(device, apk)
        except pushes.PushRefused as exc:
            refused.append(f"{apk['source_label']}: {exc}")
            continue
        record_audit(request, "push", f"{apk['source_label']} {apk['tag']} ({apk['filename']}) → {device['nickname'] or device['serial']}")
        jobs.append((install_id, dict(device), dict(apk)))
    dom = dom_id(device["serial"])
    if not jobs:
        return redirect("/status", card=dom, error="; ".join(refused) or "Nothing to update on that device")
    background_tasks.add_task(_run_pushes, jobs)
    return redirect("/status", card=dom)


# ---- status ----

def _pushed(last, mine) -> dict:
    """A row's push state from its app's newest push on the device: running
    (of any build: one at a time), or this row's own last push having failed."""
    status = last["status"] if last is not None else None
    return {"busy": last if status in RUNNING else None,
            "failed": last if status == "failed" and mine(last) else None}


def _app_rows(d, dom: str, variants_by_repo: dict, installed: dict, follows: set, last_push: dict) -> list[dict]:
    """Each watched app's latest release against what the device has."""
    rows = []
    for repo_id, variants in variants_by_repo.items():
        latest = variants[0]
        have = installed.get((d["serial"], latest["package_name"]))
        rows.append({
            "repo_id": repo_id, "latest": latest, "installed": have, "row": f"app-{repo_id}-{dom}",
            "state": selection.update_state(have, latest),
            "origin": selection.origin(have),
            "fits": selection.pick_variant(variants, d["abis"]) is not None,
            "following": (d["serial"], repo_id) in follows,
            **_pushed(last_push.get((d["serial"], latest["package_name"])),
                      lambda i, r=repo_id: i["repo_id"] == r),
        })
    return rows


def _staged_rows(d, dom: str, extras: list, installed: dict, last_push: dict) -> list[dict]:
    """Each staged test build and upload against what the device has."""
    rows = []
    for apk in extras:
        have = installed.get((d["serial"], apk["package_name"]))
        rows.append({
            "apk": apk, "kind": apk_kind(apk), "row": f"upload-{apk['id']}-{dom}",
            "installed": have if have is not None and have["installed"] else None,
            "state": selection.update_state(have, apk), "origin": selection.origin(have),
            "fits": selection.compatible(apk["abis"], d["abis"]),
            **_pushed(last_push.get((d["serial"], apk["package_name"])), lambda i, a=apk["id"]: i["apk_id"] == a),
        })
    return rows


def _device_cards() -> list[dict]:
    """One card per device: each watched app's latest release against what
    the device has, then its staged test builds and uploads, then anything
    else pushed to it, then its most recent installs. Trusted devices first."""
    installed = db.device_packages_map()
    follows = db.follows_set()
    variants_by_repo: dict[int, list] = {}
    for v in db.list_latest_variants():
        variants_by_repo.setdefault(v["repo_id"], []).append(v)
    # Test builds and uploads, newest first: each can be pushed from every device's card.
    extras = [g["releases"][0]["apks"][0] for g in _install_groups() if g["kind"] != "release"]
    staged_packages = {vs[0]["package_name"] for vs in variants_by_repo.values()} | {a["package_name"] for a in extras}
    installs = db.list_installs()
    last_push: dict[tuple, object] = {}  # (serial, package) -> its newest install
    for i in installs:  # newest first
        last_push.setdefault((i["device_serial"], i["package_name"]), i)
    cards = []
    for d in sorted(db.list_devices(), key=lambda d: not d["trusted"]):
        dom = dom_id(d["serial"])
        apps = _app_rows(d, dom, variants_by_repo, installed, follows, last_push)
        staged = _staged_rows(d, dom, extras, installed, last_push)
        others = [
            {"package": pkg, "installed": row, "origin": selection.origin(row)}
            for (serial, pkg), row in sorted(installed.items())
            if serial == d["serial"] and row["installed"] and pkg not in staged_packages
        ]
        # Updates that can be pushed now: a newer release fits the device.
        updates = [a for a in apps if d["trusted"] and a["state"] == "update" and a["fits"] and not a["busy"]]
        # Auto-update for everything at once covers the watched apps the device has.
        followable = [a for a in apps if a["state"] in ("current", "update", "newer")]
        busy = any(a["busy"] for a in apps + staged)
        cards.append({
            "device": d, "dom": dom, "apps": apps, "staged": staged, "others": others,
            "recent": [i for i in installs if i["device_serial"] == d["serial"]][:3],
            "updates": updates, "busy": busy, "followable": followable,
            "all_following": bool(followable) and all(a["following"] for a in followable),
            # A card starts open when something on it wants you.
            "attention": bool(updates) or not d["trusted"] or busy,
        })
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
        {"done": has_install, "href": "/status", "title": "Install an app",
         "hint": "Press Install on an app in the device's card below."},
    ]


@router.get("/status", response_class=HTMLResponse)
def status_page(request: Request, session: dict = Depends(auth.require_auth), error: str | None = None,
                ok: str | None = None, warn: str | None = None):
    cards = _device_cards()
    steps = _setup_steps(cards)
    activity = _activity(db.list_installs())
    return templates.TemplateResponse(
        request, "status.html",
        context(request, session, card_ids=[c["dom"] for c in cards], cards=cards, steps=steps,
                setup_done=all(s["done"] for s in steps), activity=activity,
                running=any(i["status"] in RUNNING for i in activity), error=error, ok=ok, warn=warn),
    )


@router.post("/status/refresh")
def refresh_status(request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    """Asks each trusted device which version of every pinned package it has.
    No port scan here (that's per-device, on demand), so an offline device
    costs one quick failed connect."""
    check_csrf(request, session, csrf_token)
    packages = {r["expected_package"] for r in db.list_repos() if r["expected_package"]}
    known = db.device_packages_map()
    unreachable = []
    for device in db.list_devices():
        if not device["trusted"]:
            continue
        try:
            serial, addr = discovery.ensure_connected(device, allow_scan=False)
        except adb_client.AdbError:
            unreachable.append(device)
            continue
        pushes.refresh_abis(serial, addr)
        # Plus whatever this device is known to have had (uploads), so a
        # replaced or removed app stops showing as this server's.
        for package in packages | {p for (s, p) in known if s == serial}:
            pushes.refresh_installed(serial, addr, package)
    if unreachable:
        names = ", ".join(d["nickname"] or d["serial"] for d in unreachable)
        # Opens the first one's card, where Find is.
        return redirect("/status", card=dom_id(unreachable[0]["serial"]),
                        error=f"Not reachable: {names}. Find looks for a phone whose port changed.")
    return redirect("/status", ok="Installed versions refreshed")


@router.post("/follow")
def set_follow(
    request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
    device_serial: str = Form(...), repo_id: int = Form(...), follow: str = Form(...),
):
    """Auto-update: a following device gets each newly staged release pushed
    to it. Only trusted devices can follow, and trust is re-checked at push."""
    check_csrf(request, session, csrf_token)
    device, repo_row = db.get_device(device_serial), db.get_repo(repo_id)
    if device is None or repo_row is None:
        raise HTTPException(status_code=404)
    dom = dom_id(device["serial"])
    back = f"/status#app-{int(repo_id)}-{dom}"
    on = follow == "1"
    if on and not device["trusted"]:
        return redirect(back, card=dom, error="Only trusted devices can auto-update")
    db.set_follow(device_serial, repo_id, on)
    record_audit(request, "auto_update_on" if on else "auto_update_off",
           f"{repo_row['owner']}/{repo_row['repo']} → {device['nickname'] or device_serial}")
    return redirect(back, card=dom, ok="Auto-update " + ("on" if on else "off"))


@router.post("/follow-all")
def set_follow_all(
    request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
    device_serial: str = Form(...), follow: str = Form(...),
):
    """Auto-update in one go: on for every watched app the device has, or
    off for every app it follows."""
    check_csrf(request, session, csrf_token)
    device = db.get_device(device_serial)
    if device is None:
        raise HTTPException(status_code=404)
    dom = dom_id(device["serial"])
    on = follow == "1"
    if on and not device["trusted"]:
        return redirect("/status", card=dom, error="Only trusted devices can auto-update")
    if on:
        card = next(c for c in _device_cards() if c["device"]["serial"] == device["serial"])
        repo_ids = [a["repo_id"] for a in card["followable"]]
    else:
        repo_ids = [r for (s, r) in db.follows_set() if s == device["serial"]]
    if not repo_ids:
        return redirect("/status", card=dom, error="No watched app is installed on it yet" if on else "Nothing to turn off")
    for repo_id in repo_ids:
        db.set_follow(device["serial"], repo_id, on)
    name = device["nickname"] or device["serial"]
    record_audit(request, "auto_update_on" if on else "auto_update_off", f"{len(repo_ids)} apps → {name}")
    count = f"{len(repo_ids)} app{'' if len(repo_ids) == 1 else 's'}"
    return redirect("/status", card=dom, ok=f"Auto-update {'on' if on else 'off'} for {count} on {name}")


# ---- installs ----

@router.get("/installs", response_class=HTMLResponse)
def installs_page(request: Request, session: dict = Depends(auth.require_auth)):
    return templates.TemplateResponse(request, "installs.html", context(request, session, installs=db.list_installs()))


def _safe_back(back: str | None) -> str:
    """Rebuilt from its parts, never echoed: one of the two pages, plus the
    device Library had selected if that device exists."""
    parts = urlsplit(back or "")
    if parts.scheme or parts.netloc or parts.path not in PUSH_BACK_PATHS:
        return "/status"
    to = parse_qs(parts.query).get("to", [None])[0]
    return _back(parts.path, db.get_device(to) if to else None)


def _progress_page(request: Request, session: dict, installs: list, back: str | None) -> HTMLResponse:
    """One push (or a batch) on its own, with its log: reached from install
    history and the activity strip. Refreshes while anything is running."""
    back = _safe_back(back)
    running = any(i["status"] in RUNNING for i in installs)
    return templates.TemplateResponse(
        request, "install_status.html",
        context(request, session, installs=installs, running=running, back=back,
                back_label="Library" if back.startswith("/library") else "Status"),
    )


@router.get("/installs/batch", response_class=HTMLResponse)
def install_batch_page(request: Request, session: dict = Depends(auth.require_auth), ids: str = "",
                       back: str | None = None):
    try:
        wanted = [int(i) for i in ids.split(",") if i][:50]
    except ValueError:
        raise HTTPException(status_code=404) from None
    installs = [i for i in (db.get_install(n) for n in wanted) if i is not None]
    if not installs:
        raise HTTPException(status_code=404)
    return _progress_page(request, session, installs, back)


@router.get("/installs/{install_id}", response_class=HTMLResponse)
def install_status_page(install_id: int, request: Request, session: dict = Depends(auth.require_auth),
                        back: str | None = None):
    install = db.get_install(install_id)
    if install is None:
        raise HTTPException(status_code=404)
    return _progress_page(request, session, [install], back)
