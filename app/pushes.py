"""Installing staged APKs on devices. Every push — clicked in the UI or
queued by auto-update — goes through create_install(), so the trust and
compatibility checks can't be skipped by any caller."""
import logging

import adb_client
import db
import discovery
import notify
import selection

logger = logging.getLogger("pushes")


class PushRefused(Exception):
    pass


def refresh_abis(serial: str, addr: str) -> str:
    """Best effort: a device that can't be queried keeps its last known ABIs.
    Its model name is read at the same time, for display."""
    try:
        abis = adb_client.device_abis(addr)
    except adb_client.AdbError:
        device = db.get_device(serial)
        return device["abis"] if device else ""
    db.set_device_abis(serial, abis)
    try:
        if model := adb_client.device_model(addr):
            db.set_device_model(serial, model)
    except adb_client.AdbError:
        pass
    return " ".join(abis)


def refresh_installed(serial: str, addr: str, package: str) -> None:
    try:
        version = adb_client.installed_version(addr, package)
    except adb_client.AdbError:
        return
    if version is None:
        db.upsert_device_package(serial, package, installed=False)
    else:
        db.upsert_device_package(serial, package, installed=True, version_code=version[0], version_name=version[1])


def create_install(device, apk) -> int:
    """Validates a push and records it as pending. Raises PushRefused."""
    if apk["pruned_at"]:
        raise PushRefused("That release's file was pruned — it can no longer be pushed")
    # The actual security boundary: enforced here, server-side, not just by
    # hiding the button in the UI.
    if not device["trusted"]:
        raise PushRefused("Device is not trusted")
    if not selection.compatible(apk["abis"], device["abis"]):
        raise PushRefused(f"{apk['filename']} doesn't support this device's CPU ({device['abis']})")
    return db.insert_install(device["serial"], apk["id"], status="pending")


def _device_label(device) -> str:
    return device["nickname"] or device["serial"]


def run_push(install_id: int, device: dict, apk: dict) -> None:
    """Blocking. Runs in a worker thread (Starlette's threadpool for a
    BackgroundTasks sync callable, or asyncio.to_thread for auto-update) so a
    slow adb install doesn't block the single-worker event loop."""
    db.set_install_status(install_id, "installing")
    status, log = "failed", ""
    try:
        # Wireless ADB ports drift. Re-resolve and re-confirm identity
        # immediately before installing (scanning the device's IP if its
        # stored port went stale) rather than trusting the last pairing.
        serial, addr = discovery.ensure_connected(device)
        # Trust again, as it stands now, for whichever record answered: it
        # may have been revoked since the push was queued.
        current = db.get_device(serial)
        if current is None or not current["trusted"]:
            raise adb_client.AdbError("Device is no longer trusted — not installed")
        device_abis = refresh_abis(serial, addr)
        if not selection.compatible(apk["abis"], device_abis):
            raise adb_client.AdbError(
                f"{apk['filename']} is built for {apk['abis']}, but this device supports {device_abis}"
            )
        result = adb_client.install(addr, apk["path"])
        log = ((result.stdout or "") + (result.stderr or "")).strip()[:8000]
        status = "success" if result.returncode == 0 and "Success" in result.stdout else "failed"
        refresh_installed(serial, addr, apk["package_name"])
    except adb_client.AdbError as exc:
        log = str(exc)
    except Exception:
        # Anything unexpected here (DB hiccup, etc.) must still resolve the
        # install row — otherwise it's stuck "installing" forever and the
        # status page polls indefinitely with nothing to show for it.
        logger.exception("push job %s crashed", install_id)
        log = "Internal error during push — check server logs"
    db.finish_install(install_id, status, log)
    what = f"{apk['source_label']} {apk.get('version_name') or apk['tag']}"
    if status == "success":
        notify.send("install_success", f"Installed {what}", f"on {_device_label(device)}")
    else:
        notify.send("install_failed", f"Install failed: {what}", f"on {_device_label(device)}: {log[:500]}")


def auto_push_targets(repo_id: int) -> list[tuple[int, dict, dict]]:
    """Installs to queue for the repo's newest release on every trusted
    device following it that doesn't already have it. Returns
    (install_id, device, apk) triples, already recorded as pending."""
    variants = [v for v in db.list_latest_variants() if v["repo_id"] == repo_id]
    if not variants:
        return []
    installed = db.device_packages_map()
    queued = []
    for device in db.list_followers(repo_id):
        apk = selection.pick_variant(variants, device["abis"])
        if apk is None:
            continue
        state = selection.update_state(installed.get((device["serial"], apk["package_name"])), apk)
        if state in ("current", "newer"):
            continue
        try:
            install_id = create_install(device, apk)
        except PushRefused as exc:
            logger.info("auto-update skipped for %s: %s", device["serial"], exc)
            continue
        queued.append((install_id, dict(device), dict(apk)))
    return queued
