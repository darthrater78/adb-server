import re
import subprocess
from dataclasses import dataclass

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
