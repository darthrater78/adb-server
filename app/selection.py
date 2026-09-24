"""Pure decision logic for pushes: which APK variant suits a device, and how
an installed version compares with the latest staged one. No I/O here."""

import json


def _abis(value: str | None) -> set[str]:
    return set((value or "").split())


def compatible(apk_abis: str | None, device_abis: str | None) -> bool:
    """An APK with no native code runs anywhere; an unknown device ABI list
    is given the benefit of the doubt (the install itself will then say)."""
    apk, device = _abis(apk_abis), _abis(device_abis)
    return not apk or not device or bool(apk & device)


def pick_variant(variants: list, device_abis: str | None):
    """Best APK among one release's variants for a device: walk the device's
    ABIs in preference order and take the most specific variant (fewest
    ABIs) that supports it; fall back to a variant with no native code.
    Returns None when nothing fits."""
    preferred = (device_abis or "").split()
    for abi in preferred:
        fits = [v for v in variants if abi in _abis(v["abis"])]
        if fits:
            return min(fits, key=lambda v: len(_abis(v["abis"])))
    universal = [v for v in variants if not _abis(v["abis"])]
    if universal:
        return universal[0]
    if not preferred and variants:
        # Device ABIs unknown: the variant covering the most ABIs is the
        # safest guess.
        return max(variants, key=lambda v: len(_abis(v["abis"])))
    return None


def update_state(installed, latest) -> str:
    """One of: unknown, missing, current, update, newer."""
    if installed is None:
        return "unknown"
    if not installed["installed"]:
        return "missing"
    have, want = installed["version_code"], latest["version_code"]
    if have is None or want is None:
        return "current" if installed["version_name"] == latest["version_name"] else "update"
    if have == want:
        return "current"
    return "update" if have < want else "newer"


def origin(package_row) -> dict | None:
    """Where the installed version of a package came from, as far as this
    server can tell: its recorded origin plus a "state" of
    ours     pushed from here, and the device still has that exact install;
    likely   pushed from here before 3.5.0 (same version, no install time to compare);
    other    not from this server: never pushed, or replaced since.
    None when the device doesn't have the package (or was never asked)."""
    if package_row is None or not package_row["installed"]:
        return None
    raw = package_row["origin"]
    if not raw:
        return {"state": "other"}
    recorded = json.loads(raw)
    have_code, want_code = package_row["version_code"], recorded.get("version_code")
    if have_code is not None and want_code is not None:
        if have_code != want_code:
            return {"state": "other"}
    elif package_row["version_name"] != recorded.get("version_name"):
        return {"state": "other"}
    have_time, want_time = package_row["update_time"], recorded.get("update_time")
    if have_time and want_time and have_time != want_time:
        return {"state": "other"}  # reinstalled since, by something else
    return recorded | {"state": "likely" if recorded.get("backfilled") else "ours"}
