import re
import subprocess
import zipfile
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


class SignerInfo(NamedTuple):
    """What the APK's signature says about it. `debug` is reported, not
    enforced: the poller refuses a debug-signed release outright, while a
    manual upload is allowed to be one and is marked instead. Keeping the
    policy in the callers means the two paths can differ on purpose rather
    than by accident."""

    fingerprint: str
    debug: bool


def assert_apk_container(apk_path: str) -> None:
    """Cheap structural check before handing a file to apksigner: it must be a
    zip, and it must carry an AndroidManifest.xml. Shared by the poller and by
    manual uploads so both reject the same things for the same reasons."""
    with open(apk_path, "rb") as f:
        if f.read(4) != b"PK\x03\x04":
            raise ApkVerifyError("File is not a valid zip/APK")
    try:
        with zipfile.ZipFile(apk_path) as zf:
            if "AndroidManifest.xml" not in zf.namelist():
                raise ApkVerifyError("File has no AndroidManifest.xml — not a valid APK")
    except zipfile.BadZipFile as exc:
        raise ApkVerifyError(f"File is not a readable zip/APK: {exc}") from exc


def verify_signature(apk_path: str) -> SignerInfo:
    """Returns the signer certificate SHA-256 fingerprint(s), lowercase hex,
    and whether any signer is the default Android debug certificate.

    An APK may carry more than one signer. Checking only the first left the
    remaining signers unexamined for the debug certificate and outside the
    pin, so the pin covered a subset of the signing identity. Multiple
    signers come back sorted and comma-joined; the ordinary single-signer
    APK still returns one bare fingerprint, so pins written by earlier
    versions keep matching.

    Raises if verification fails or the APK is unsigned. A debug certificate
    is reported via SignerInfo.debug, not raised on — see SignerInfo."""
    result = subprocess.run(
        ["apksigner", "verify", "--print-certs", apk_path],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise ApkVerifyError(
            f"apksigner verify failed: {result.stderr.strip() or result.stdout.strip()}"
        )

    lines = result.stdout.splitlines()

    debug = any(DEBUG_CERT_CN_RE.search(l) for l in lines if "certificate DN" in l)

    # "certificate SHA-256 digest", not any line containing "SHA-256 digest":
    # apksigner also prints a *public key* SHA-256 digest per signer, so the
    # looser match made which fingerprint you got depend on output ordering.
    digest_lines = [l for l in lines if "certificate SHA-256 digest" in l]
    if not digest_lines:
        raise ApkVerifyError("Could not find a signer certificate SHA-256 digest in apksigner output")

    fingerprints = []
    for line in digest_lines:
        fingerprint = line.rsplit(":", 1)[-1].strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ApkVerifyError("Unexpected apksigner output format for signer fingerprint")
        fingerprints.append(fingerprint)

    return SignerInfo(fingerprint=",".join(sorted(set(fingerprints))), debug=debug)


def get_package_name(apk_path: str) -> str:
    result = subprocess.run(
        ["aapt", "dump", "badging", apk_path],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise ApkVerifyError(f"aapt dump badging failed: {result.stderr.strip()}")
    match = re.search(r"package: name='([^']+)'", result.stdout)
    if not match:
        raise ApkVerifyError("Could not determine package name from APK")
    return match.group(1)
