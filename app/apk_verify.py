import hashlib
import os
import re
import subprocess
import zipfile
import zlib
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


def sha256_file(path: str) -> str:
    """SHA-256 of a file, read in chunks so a large APK never sits in memory."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


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


# A workflow's artifact zip is a handful of build outputs; thousands of
# entries is not that, and the central directory is read into memory.
MAX_ARCHIVE_ENTRIES = 1000


class ArchivedApk(NamedTuple):
    name: str    # the entry's path inside the zip — display text only
    sha256: str  # of the extracted APK, so a zipped and a bare upload dedupe


def _is_apk_entry(info: zipfile.ZipInfo) -> bool:
    if info.is_dir() or not info.filename.lower().endswith(".apk"):
        return False
    # Finder's zips add a __MACOSX/._name.apk resource fork beside every file.
    parts = info.filename.split("/")
    return "__MACOSX" not in parts and not parts[-1].startswith("._")


def extract_archived_apk(zip_path: str, out_path: str, max_bytes: int) -> ArchivedApk | None:
    """Unwraps the zip GitHub Actions hands back for an uploaded artifact.

    Returns None when zip_path is not such a zip — it is an APK itself (its
    manifest sits at the root), or not a zip at all — so the caller checks it
    as an APK as before. Otherwise the zip must hold exactly one APK, which is
    streamed to out_path under max_bytes; everything else in it is ignored,
    and nothing is ever written under a name taken from the zip. The result
    still has to pass every APK check: this only finds it."""
    with open(zip_path, "rb") as f:
        if f.read(4) != b"PK\x03\x04":
            return None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            if any(i.filename == "AndroidManifest.xml" for i in infos):
                return None
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise ApkVerifyError(f"zip has {len(infos)} entries — an artifact zip holding one APK has a few")
            apks = [i for i in infos if _is_apk_entry(i)]
            if not apks:
                raise ApkVerifyError("zip has no APK in it and isn't an APK itself")
            if len(apks) > 1:
                raise ApkVerifyError(f"zip holds {len(apks)} APKs — upload a zip with exactly one")
            [entry] = apks
            if entry.flag_bits & 0x1:
                raise ApkVerifyError("the APK in that zip is encrypted")
            sha256 = _inflate_capped(zf, entry, out_path, max_bytes)
    # zlib.error and EOFError: a corrupt or truncated deflate stream, which
    # zipfile passes through rather than wrapping in BadZipFile.
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise ApkVerifyError(f"file is not a readable zip: {exc}") from exc
    except NotImplementedError as exc:  # a compression method zipfile can't inflate
        raise ApkVerifyError(f"zip uses an unsupported compression method: {exc}") from exc
    return ArchivedApk(name=entry.filename, sha256=sha256)


def _inflate_capped(zf: zipfile.ZipFile, entry: zipfile.ZipInfo, out_path: str, max_bytes: int) -> str:
    """Streams one entry to out_path and returns its sha256. The declared size
    is a cheap early refusal, not the limit: it's attacker-controlled, so the
    bytes actually inflated are what get counted."""
    too_big = ApkVerifyError(f"the APK in that zip exceeds the {max_bytes // (1024 * 1024)}MB size cap")
    if entry.file_size > max_bytes:
        raise too_big
    hasher = hashlib.sha256()
    written = 0
    with zf.open(entry) as src, open(out_path, "wb") as dst:
        while chunk := src.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                raise too_big
            hasher.update(chunk)
            dst.write(chunk)
    return hasher.hexdigest()


_SIGNATURE_FILE_SUFFIXES = (".SF", ".RSA", ".DSA", ".EC")


def is_unsigned(apk_path: str) -> bool:
    """True only for an APK that carries no signature of any kind: no v1
    (JAR) signature files and no APK Signing Block (v2 and later). An APK
    whose signature is present but broken is not unsigned, and never gets
    re-signed: it's refused like any failed verification. Something that
    isn't a readable zip isn't "unsigned" either: verification refuses it."""
    try:
        with zipfile.ZipFile(apk_path) as zf:
            if any(n.upper().startswith("META-INF/") and n.upper().endswith(_SIGNATURE_FILE_SUFFIXES)
                   for n in zf.namelist()):
                return False
    except zipfile.BadZipFile:
        return False
    with open(apk_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        tail_len = min(size, 65_536 + 22)
        f.seek(size - tail_len)
        tail = f.read()
        end = tail.rfind(b"PK\x05\x06")
        if end < 0 or end + 20 > len(tail):
            return False
        directory_offset = int.from_bytes(tail[end + 16:end + 20], "little")
        if directory_offset < 16:
            return True
        # A signing block sits right before the central directory and ends
        # with this magic.
        f.seek(directory_offset - 16)
        return f.read(16) != b"APK Sig Block 42"


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
        ["aapt2", "dump", "badging", apk_path],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise ApkVerifyError(f"aapt2 dump badging failed: {result.stderr.strip()}")
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
