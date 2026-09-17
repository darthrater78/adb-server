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
    """Returns the signer certificate SHA-256 fingerprint(s), lowercase hex.

    An APK may carry more than one signer. Checking only the first left the
    remaining signers unexamined for the debug certificate and outside the
    pin, so the pin covered a subset of the signing identity. Multiple
    signers come back sorted and comma-joined; the ordinary single-signer
    APK still returns one bare fingerprint, so pins written by earlier
    versions keep matching.

    Raises if verification fails, the APK is unsigned, or any signer uses the
    default Android debug certificate."""
    result = subprocess.run(
        ["apksigner", "verify", "--print-certs", apk_path],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise ApkVerifyError(
            f"apksigner verify failed: {result.stderr.strip() or result.stdout.strip()}"
        )

    lines = result.stdout.splitlines()

    for line in (l for l in lines if "certificate DN" in l):
        if DEBUG_CERT_CN_RE.search(line):
            raise ApkVerifyError(
                "APK is signed with the default Android debug certificate (CN=Android Debug)"
            )

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

    return ",".join(sorted(set(fingerprints)))


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
