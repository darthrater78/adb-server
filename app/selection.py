"""Pure decision logic for pushes: which APK variant suits a device, and how
an installed version compares with the latest staged one. No I/O here."""


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
