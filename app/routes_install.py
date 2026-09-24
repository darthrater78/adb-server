"""Status, Install and install history: what is staged, what each device
has, and pushing one to the other."""

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import adb_client
import auth
import db
import discovery
import pushes
import selection
import staging
from web import check_csrf, context, moved, record_audit, redirect, templates

router = APIRouter()


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


@router.get("/install", response_class=HTMLResponse)
def install_page(
    request: Request, session: dict = Depends(auth.require_auth),
    error: str | None = None, ok: str | None = None, warn: str | None = None, to: str | None = None,
):
    """`to` picks the device every Push on the page targets (default: the
    first trusted one); anything else falls back to the default."""
    trusted = [d for d in db.list_devices() if d["trusted"]]
    target = next((d for d in trusted if d["serial"] == to), trusted[0] if trusted else None)
    groups = _install_groups()
    installed = db.device_packages_map()
    for g in groups:
        # Trusted devices that have this card's latest version right now.
        first = g["releases"][0]["apks"][0]
        g["on"] = [
            d for d in trusted
            if (row := installed.get((d["serial"], first["package_name"]))) is not None and row["installed"]
            and row["version_code"] is not None and row["version_code"] == first["version_code"]
        ]
    return templates.TemplateResponse(
        request, "install.html",
        context(request, session, groups=groups, devices=trusted, target=target, error=error, ok=ok, warn=warn),
    )


@router.get("/staged")
def old_staged_page(request: Request, session: dict = Depends(auth.require_auth)):
    return moved(request, "/install")


@router.post("/staged/{apk_id}/delete")
def delete_staged(apk_id: int, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    check_csrf(request, session, csrf_token)
    apk = db.get_staged_apk(apk_id)
    if apk is None:
        raise HTTPException(status_code=404)
    staging.remove_file(apk["path"])
    db.mark_apk_pruned(apk_id)
    record_audit(request, "staged_delete", f"{apk['source_label']} {apk['tag']} {apk['filename']}")
    return redirect("/install", ok="Staged file deleted")


# ---- push ----

def _queue_push(request: Request, background_tasks: BackgroundTasks, device, apk, back: str) -> RedirectResponse:
    try:
        install_id = pushes.create_install(device, apk)
    except pushes.PushRefused as exc:
        return redirect(back, error=str(exc))
    record_audit(request, "push", f"{apk['source_label']} {apk['tag']} ({apk['filename']}) → {device['nickname'] or device['serial']}")
    background_tasks.add_task(pushes.run_push, install_id, dict(device), dict(apk))
    return RedirectResponse(f"/installs/{install_id}", status_code=303)


@router.post("/push")
def push(
    request: Request,
    background_tasks: BackgroundTasks,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    device_serial: str = Form(...),
    apk_id: int = Form(...),
):
    check_csrf(request, session, csrf_token)
    device = db.get_device(device_serial)
    apk = db.get_staged_apk(apk_id)
    if device is None or apk is None:
        raise HTTPException(status_code=404)
    return _queue_push(request, background_tasks, device, apk, "/install")


# Where a failed push-latest sends you back to: the two pages that offer it.
PUSH_BACK_PATHS = {"/status", "/install"}


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
    back = back if back in PUSH_BACK_PATHS else "/status"
    device = db.get_device(device_serial)
    if device is None:
        raise HTTPException(status_code=404)
    variants = [v for v in db.list_latest_variants() if v["repo_id"] == repo_id]
    if not variants:
        return redirect(back, error="Nothing staged for that repo")
    apk = selection.pick_variant(variants, device["abis"])
    if apk is None:
        return redirect(back, error="No APK in the latest release supports this device's CPU")
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
                "origin": selection.origin(have),
                "fits": selection.pick_variant(variants, d["abis"]) is not None,
                "following": (d["serial"], repo_id) in follows,
            })
        others = [
            {"package": pkg, "label": upload_labels.get(pkg), "installed": row, "origin": selection.origin(row)}
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


@router.get("/status", response_class=HTMLResponse)
def status_page(request: Request, session: dict = Depends(auth.require_auth), error: str | None = None, ok: str | None = None):
    cards = _device_cards()
    steps = _setup_steps(cards)
    return templates.TemplateResponse(
        request, "status.html",
        context(request, session, cards=cards, steps=steps, setup_done=all(s["done"] for s in steps),
              error=error, ok=ok),
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
            unreachable.append(device["nickname"] or device["serial"])
            continue
        pushes.refresh_abis(serial, addr)
        # Plus whatever this device is known to have had (uploads), so a
        # replaced or removed app stops showing as this server's.
        for package in packages | {p for (s, p) in known if s == serial}:
            pushes.refresh_installed(serial, addr, package)
    if unreachable:
        return redirect("/status", error="Not reachable (try Find on the Devices page): " + ", ".join(unreachable))
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
    on = follow == "1"
    if on and not device["trusted"]:
        return redirect("/status", error="Only trusted devices can auto-update")
    db.set_follow(device_serial, repo_id, on)
    record_audit(request, "auto_update_on" if on else "auto_update_off",
           f"{repo_row['owner']}/{repo_row['repo']} → {device['nickname'] or device_serial}")
    return redirect("/status", ok="Auto-update " + ("on" if on else "off"))


# ---- installs ----

@router.get("/installs", response_class=HTMLResponse)
def installs_page(request: Request, session: dict = Depends(auth.require_auth)):
    return templates.TemplateResponse(request, "installs.html", context(request, session, installs=db.list_installs()))


@router.get("/installs/{install_id}", response_class=HTMLResponse)
def install_status_page(install_id: int, request: Request, session: dict = Depends(auth.require_auth)):
    install = db.get_install(install_id)
    if install is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "install_status.html", context(request, session, install=install))
