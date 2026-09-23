import re
import subprocess
import zipfile
from dataclasses import dataclass
from typing import NamedTuple

# One cap for any APK entering staging, wherever it came from.
MAX_APK_BYTES = 500 * 1024 * 1024  # 500 MB

# The default Android debug certificate is generated locally per machine (it is
# NOT one fixed keypair everyone shares), so there is no single fingerprint to
# block. What IS fixed and checkable is the subject Android Studio/Gradle's
# default debug signing config always uses.
DEBUG_CERT_CN_RE = re.compile(r"CN=Android Debug", re.IGNORECASE)


class ApkVerifyError(Exception):
    pass


def assert_apk_container(apk_path: str) -> None:
    """Cheap structural check before handing a file to apksigner: it must be a
    zip, and it must carry an AndroidManifest.xml. Shared by the poller and by
    manual uploads so both reject the same things for the same reasons."""
    with open(apk_path, "rb") as f:
        if f.read(4) != b"PK\x03\x04":
            raise ApkVerifyError("file is not a valid zip/APK")
    try:
        with zipfile.ZipFile(apk_path) as zf:
            if "AndroidManifest.xml" not in zf.namelist():
                raise ApkVerifyError("file has no AndroidManifest.xml — not a valid APK")
    except zipfile.BadZipFile as exc:
        raise ApkVerifyError(f"file is not a readable zip/APK: {exc}") from exc


class SignerInfo(NamedTuple):
    """What the APK's signature says about it. `debug` is reported, not
    enforced: the poller refuses a debug-signed release outright, while a
    manual upload is allowed to be one and is marked instead. Keeping the
    policy in the callers means the two paths differ on purpose rather than
    by accident."""

    fingerprint: str
    debug: bool


def verify_signature(apk_path: str) -> SignerInfo:
    """Returns the signer certificate SHA-256 fingerprint(s), lowercase hex,
    and whether any signer is the default Android debug certificate.

    Every signer is examined, not just the first, so the debug check and the
    pin cover the whole signing identity. Multiple signers come back sorted
    and comma-joined; a single-signer APK still yields one bare fingerprint,
    so existing pins keep matching.

    Raises if verification fails or the APK is unsigned."""
    result = subprocess.run(
        ["apksigner", "verify", "--print-certs", apk_path],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise ApkVerifyError(
            f"apksigner verify failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return parse_signers(result.stdout)


def parse_signers(output: str) -> SignerInfo:
    lines = output.splitlines()
    debug = any(DEBUG_CERT_CN_RE.search(l) for l in lines if "certificate DN" in l)

    # "certificate SHA-256 digest", not any line containing "SHA-256 digest":
    # apksigner also prints a *public key* SHA-256 digest per signer, so the
    # looser match made the fingerprint depend on output ordering.
    digest_lines = [l for l in lines if "certificate SHA-256 digest" in l]
    if not digest_lines:
        raise ApkVerifyError("Could not find a signer certificate SHA-256 digest in apksigner output")

    fingerprints = set()
    for line in digest_lines:
        fingerprint = line.rsplit(":", 1)[-1].strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ApkVerifyError("Unexpected apksigner output format for signer fingerprint")
        fingerprints.add(fingerprint)
    return SignerInfo(fingerprint=",".join(sorted(fingerprints)), debug=debug)


@dataclass(frozen=True)
class PackageInfo:
    name: str
    version_code: int | None
    version_name: str | None
    abis: tuple[str, ...]  # empty = no native code, installs on any ABI


_BADGING_PACKAGE_RE = re.compile(r"^package: name='([^']+)'", re.MULTILINE)
_BADGING_CODE_RE = re.compile(r"\bversionCode='(\d+)'")
_BADGING_NAME_RE = re.compile(r"\bversionName='([^']*)'")
_BADGING_NATIVE_RE = re.compile(r"^native-code:(.*)$", re.MULTILINE)
_ABI_TOKEN_RE = re.compile(r"'([a-z0-9_-]+)'")
_DIGEST_RE = re.compile(r"SHA-256 digest:\s*([0-9a-fA-F]{64})\s*$", re.MULTILINE)


def get_package_info(apk_path: str) -> PackageInfo:
    result = subprocess.run(
        ["aapt", "dump", "badging", apk_path],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise ApkVerifyError(f"aapt dump badging failed: {result.stderr.strip()}")
    return parse_badging(result.stdout)


def parse_badging(output: str) -> PackageInfo:
    match = _BADGING_PACKAGE_RE.search(output)
    if not match:
        raise ApkVerifyError("Could not determine package name from APK")
    header = output[match.start():output.find("\n", match.start())]
    code = _BADGING_CODE_RE.search(header)
    name = _BADGING_NAME_RE.search(header)
    native = _BADGING_NATIVE_RE.search(output)
    abis = tuple(_ABI_TOKEN_RE.findall(native.group(1))) if native else ()
    return PackageInfo(
        name=match.group(1),
        version_code=int(code.group(1)) if code else None,
        version_name=name.group(1) if name else None,
        abis=abis,
    )


def signing_lineage(apk_path: str) -> list[str]:
    """SHA-256 digests of every certificate in the APK's v3 proof-of-rotation
    lineage, oldest first. Empty when the APK carries no lineage — that means
    "rotation not proven", not an error."""
    result = subprocess.run(
        ["apksigner", "lineage", "--in", apk_path, "--print-certs"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        return []
    return [d.lower() for d in _DIGEST_RE.findall(result.stdout)]
