"""Devices: pairing, reconnecting, finding a moved port, trust and nicknames."""
import ipaddress

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import adb_client
import auth
import db
import discovery
import pushes
from web import check_csrf, client_ip, context, dom_id, record_audit, redirect, templates

router = APIRouter()

ADD_CARD = "add-device"  # the Add a device panel, as a card a redirect can open


# ---- devices ----

@router.get("/devices", response_class=HTMLResponse)
def devices_page(request: Request, session: dict = Depends(auth.require_auth), error: str | None = None,
                 ok: str | None = None, trust: str | None = None):
    """`trust` names a just-paired device to offer trusting right away; it
    only shows for a device that exists and isn't trusted yet."""
    devices = db.list_devices()
    offer = next((d for d in devices if d["serial"] == trust and not d["trusted"]), None) if trust else None
    return templates.TemplateResponse(
        request, "devices.html",
        context(request, session, card_ids=[ADD_CARD, *(dom_id(d["serial"]) for d in devices)], devices=devices,
                offer=offer, own_ip=_own_address(request), state=_device_states(devices), error=error, ok=ok),
    )


def _device_states(devices) -> dict[str, dict]:
    """How each device stands right now, by serial: its connection as the
    adb server reports it, how many apps it has from here, and its last push."""
    try:
        transports = adb_client.transport_states()
    except adb_client.AdbError:
        transports = None
    installed = db.device_packages_map()
    last_push = {}
    for i in db.list_installs():  # newest first
        last_push.setdefault(i["device_serial"], i)
    states = {}
    for d in devices:
        addr = d["last_connect_addr"]
        if transports is None:
            link = "unknown"
        else:
            link = {"device": "connected", "unauthorized": "unauthorized"}.get(transports.get(addr or ""),
                                                                               "offline" if addr in transports else "disconnected")
        states[d["serial"]] = {
            "link": link,
            "apps": sum(1 for (serial, _), row in installed.items() if serial == d["serial"] and row["installed"]),
            "last_push": last_push.get(d["serial"]),
        }
    return states


def _back(back: str, serial: str, **flash):
    """After an action on a device: its card on Status when that's where the
    action was, else its card here."""
    return redirect("/status" if back == "/status" else "/devices", card=dom_id(serial), **flash)


def _own_address(request: Request) -> str:
    """The address an Android browser is coming from: the phone itself, when
    it's pairing itself. Only offered as the form's starting value (a proxy
    or a VPN makes it something else), and only a private address ever is."""
    if "android" not in request.headers.get("user-agent", "").lower():
        return ""
    try:
        ip = ipaddress.ip_address(client_ip(request) or "")
    except ValueError:
        return ""
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return str(ip) if ip.is_private and not ip.is_loopback else ""


def _port(value: str) -> str:
    """A port as typed. Someone copying the whole "192.168.1.50:41235" off
    the phone still means its port."""
    return value.strip().rpartition(":")[2]


@router.post("/devices/pair")
def pair_device(
    request: Request,
    session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...),
    ip: str = Form(...),
    pairing_port: str = Form(...),
    pairing_code: str = Form(...),
    connect_port: str = Form(""),
):
    """Sync route, so Starlette runs it in a threadpool: without a connect
    port, the phone's is found by a port scan that can take several seconds."""
    check_csrf(request, session, csrf_token)
    ip, pairing_port, connect_port = ip.strip().strip("[]"), _port(pairing_port), _port(connect_port)
    try:
        adb_client.pair(adb_client.join_host_port(ip, pairing_port), pairing_code.strip())
    except adb_client.AdbError as exc:
        return redirect("/devices", card=ADD_CARD, error=f"{exc}. The port and code only work while the pairing dialog "
                                                         "is open on the phone: open it again for new ones")
    try:
        if connect_port:
            addr = adb_client.join_host_port(ip, connect_port)
            adb_client.connect(addr)
            serial = adb_client.get_serialno(addr)
        else:
            found = discovery.find_paired_port(ip, int(pairing_port))
            if found is None:
                raise adb_client.AdbError(f"nothing on {ip} answered")
            serial, addr = found
    except adb_client.AdbError as exc:
        return redirect("/devices", card=ADD_CARD, error=f"The phone accepted the code, but connecting to it failed ({exc}). "
                                                         "Pair again with a new code, and fill in Connect port: the port "
                                                         "beside IP address & Port on the Wireless debugging screen")
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
        return _back("/devices", serial, ok="Paired")
    if adopted:
        return redirect("/devices", trust=serial, ok="Paired. It took over the older record for that IP (nickname "
                                                      "and history kept) — trust it again if it's the same phone")
    return redirect("/devices", trust=serial, ok="Paired")


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
        return _back("/devices", serial, error=str(exc))
    if not discovery.is_same_device(serial, addr, confirmed):
        return _back("/devices", serial, error="That address now answers as a different device - not updated")
    serial = discovery.record(serial, confirmed, addr)
    pushes.refresh_abis(serial, addr)
    return _back("/devices", serial, ok="Reconnected")


@router.post("/devices/{serial}/find")
def find_device(serial: str, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...),
                back: str = Form("/devices")):
    """Sync route, so Starlette runs it in a threadpool: the port scan can
    take several seconds and must not block the event loop. Offered on each
    device's card on Status as well as here."""
    check_csrf(request, session, csrf_token)
    device = db.get_device(serial)
    if device is None:
        raise HTTPException(status_code=404)
    try:
        serial, addr = discovery.ensure_connected(device)
    except adb_client.AdbError as exc:
        return _back(back, serial, error=str(exc))
    pushes.refresh_abis(serial, addr)
    return _back(back, serial, ok=f"Found at {addr}")


@router.post("/devices/{serial}/trust")
def trust_device(
    serial: str, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), trusted: str = Form(...), nickname: str | None = Form(None),
    back: str = Form("/devices"),
):
    """The trust prompt after pairing can name the device in the same step,
    and it (like Trust on Status) goes on to the device's card on Status."""
    check_csrf(request, session, csrf_token)
    if db.get_device(serial) is None:
        raise HTTPException(status_code=404)
    if nickname is not None and nickname.strip():
        db.set_device_nickname(serial, nickname.strip()[:100])
    db.set_device_trusted(serial, trusted == "1")
    record_audit(request, "device_trust" if trusted == "1" else "device_untrust", serial)
    if trusted == "1":
        return _back(back, serial, ok="Trusted. It can receive pushes now")
    return _back(back, serial, ok="Trust revoked")


@router.post("/devices/{serial}/nickname")
def nickname_device(
    serial: str, request: Request, session: dict = Depends(auth.require_auth),
    csrf_token: str = Form(...), nickname: str = Form(""),
):
    check_csrf(request, session, csrf_token)
    if db.get_device(serial) is None:
        raise HTTPException(status_code=404)
    db.set_device_nickname(serial, nickname.strip()[:100] or None)
    return _back("/devices", serial, ok="Name saved")


@router.post("/devices/{serial}/delete")
def delete_device(serial: str, request: Request, session: dict = Depends(auth.require_auth), csrf_token: str = Form(...)):
    check_csrf(request, session, csrf_token)
    db.delete_device(serial)
    record_audit(request, "device_forget", serial)
    return redirect("/devices", ok="Device forgotten")
