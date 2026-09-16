import re
import subprocess

# The default Android debug certificate is generated locally per machine (it is
# NOT one fixed keypair everyone shares), so there is no single fingerprint to
# block. What IS fixed and checkable is the subject Android Studio/Gradle's
# default debug signing config always uses.
DEBUG_CERT_CN_RE = re.compile(r"CN=Android Debug", re.IGNORECASE)


class ApkVerifyError(Exception):
    pass


def verify_signature(apk_path: str) -> str:
    """Returns the signer certificate's SHA-256 fingerprint (lowercase hex).
    Raises if verification fails, the APK is unsigned, or it's debug-signed."""
    result = subprocess.run(
        ["apksigner", "verify", "--print-certs", apk_path],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise ApkVerifyError(
            f"apksigner verify failed: {result.stderr.strip() or result.stdout.strip()}"
        )

    dn_lines = [l for l in result.stdout.splitlines() if "certificate DN" in l]
    if dn_lines and DEBUG_CERT_CN_RE.search(dn_lines[0]):
        raise ApkVerifyError("APK is signed with the default Android debug certificate (CN=Android Debug)")

    sha256_lines = [l for l in result.stdout.splitlines() if "SHA-256 digest" in l]
    if not sha256_lines:
        raise ApkVerifyError("Could not find a signer certificate SHA-256 digest in apksigner output")
    fingerprint = sha256_lines[0].rsplit(":", 1)[-1].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ApkVerifyError("Unexpected apksigner output format for signer fingerprint")

    return fingerprint


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
