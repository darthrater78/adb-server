"""Devices: pairing, reconnecting, finding a moved port, trust and nicknames."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import adb_client
import auth
import db
import discovery
import pushes
from web import check_csrf, context, record_audit, redirect, templates

router = APIRouter()


# ---- devices ----

@router.get("/devices", response_class=HTMLResponse)
def devices_page(request: Request, session: dict = Depends(auth.require_auth), error: str | None = None, ok: str | None = None):
    return templates.TemplateResponse(
        request, "devices.html", context(request, session, devices=db.list_devices(), error=error, ok=ok),
    )


@router.post("/devices/pair")
def pair_device(
    request: Request,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    pairing_addr: str = Form(...),
    pairing_code: str = Form(...),
    connect_addr: str = Form(...),
):
    check_csrf(request, session, csrf_token)
    addr = connect_addr.strip()
    try:
        adb_client.pair(pairing_addr.strip(), pairing_code.strip())
        adb_client.connect(addr)
        serial = adb_client.get_serialno(addr)
    except adb_client.AdbError as exc:
        return redirect("/devices", error=str(exc))
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
    record_audit(request, "device_pair", f"{serial} at {addr}"
           + (f", took over the record of {adopted}; trust cleared" if adopted else ""))
    if db.get_device(serial)["trusted"]:
        return redirect("/devices", ok="Paired")
    if adopted:
        return redirect("/devices", ok="Paired. It took over the older record for that IP (nickname and history "
                                        "kept) — trust it again below if it's the same phone")
    return redirect("/devices", ok="Paired. Trust the device below before it can receive pushes")


@router.post("/devices/{serial}/connect")
def reconnect_device(
    serial: str, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), connect_addr: str = Form(...),
):
    check_csrf(request, session, csrf_token)
    device = db.get_device(serial)
    if device is None:
        raise HTTPException(status_code=404)
    addr = connect_addr.strip()
    try:
        adb_client.connect(addr)
        confirmed = adb_client.get_serialno(addr)
    except adb_client.AdbError as exc:
        return redirect("/devices", error=str(exc))
    if not discovery.is_same_device(serial, addr, confirmed):
        return redirect("/devices", error="That address now answers as a different device - not updated")
    serial = discovery.record(serial, confirmed, addr)
    pushes.refresh_abis(serial, addr)
    return redirect("/devices", ok="Reconnected")


@router.post("/devices/{serial}/find")
def find_device(serial: str, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    """Sync route, so Starlette runs it in a threadpool: the port scan can
    take several seconds and must not block the event loop."""
    check_csrf(request, session, csrf_token)
    device = db.get_device(serial)
    if device is None:
        raise HTTPException(status_code=404)
    try:
        serial, addr = discovery.ensure_connected(device)
    except adb_client.AdbError as exc:
        return redirect("/devices", error=str(exc))
    pushes.refresh_abis(serial, addr)
    return redirect("/devices", ok=f"Found at {addr}")


@router.post("/devices/{serial}/trust")
def trust_device(
    serial: str, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), trusted: str = Form(...),
):
    check_csrf(request, session, csrf_token)
    if db.get_device(serial) is None:
        raise HTTPException(status_code=404)
    db.set_device_trusted(serial, trusted == "1")
    record_audit(request, "device_trust" if trusted == "1" else "device_untrust", serial)
    return redirect("/devices", ok="Updated")


@router.post("/devices/{serial}/nickname")
def nickname_device(
    serial: str, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), nickname: str = Form(""),
):
    check_csrf(request, session, csrf_token)
    if db.get_device(serial) is None:
        raise HTTPException(status_code=404)
    db.set_device_nickname(serial, nickname.strip()[:100] or None)
    return redirect("/devices", ok="Updated")


@router.post("/devices/{serial}/delete")
def delete_device(serial: str, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    check_csrf(request, session, csrf_token)
    db.delete_device(serial)
    record_audit(request, "device_forget", serial)
    return redirect("/devices", ok="Device forgotten")
