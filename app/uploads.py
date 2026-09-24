"""Staging a file the operator supplied, or one fetched from a workflow
artifact: receiving it under the size cap, unwrapping an artifact zip,
signing it when its source opted in, verifying it, and staging it."""
import hashlib
import logging
import os
import re
import tempfile
from typing import NamedTuple

from fastapi import Request, UploadFile
from fastapi.responses import RedirectResponse

import apk_verify
import db
import signing
import staging
from web import record_audit, redirect

logger = logging.getLogger("adb_server")

MAX_UPLOAD_BYTES = apk_verify.MAX_APK_BYTES
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class UploadRejected(Exception):
    """Something the operator can fix, shown as a flash message."""


def upload_dir() -> str:
    # Inside STAGING_ROOT, so staging.remove_file's containment check covers it.
    return os.path.join(staging.STAGING_ROOT, "uploads")


def display_filename(raw: str | None) -> str:
    """The client-supplied filename is display text and nothing else — the
    stored path is always uploads/<sha256>.apk. Reduced to a basename and a
    conservative character set so it can't be read as a path anywhere it is
    later rendered, logged or copied."""
    name = os.path.basename((raw or "").replace("\\", "/").strip())
    name = _UNSAFE_NAME_CHARS.sub("_", name).lstrip(".")[:120]
    return name or "upload.apk"


def receive_upload(upload: UploadFile, tmp_path: str) -> str:
    """Copies the upload to tmp_path under the size cap in chunks, so a large
    APK never sits in memory. Returns its sha256."""
    hasher = hashlib.sha256()
    total = 0
    with open(tmp_path, "wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise UploadRejected(f"APK exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB size cap")
            hasher.update(chunk)
            out.write(chunk)
    if total == 0:
        raise UploadRejected("That file was empty — nothing was uploaded")
    return hasher.hexdigest()


class VerifiedUpload(NamedTuple):
    sha256: str
    display_name: str
    archive_name: str | None  # the zip it arrived in, if it came as an artifact zip
    signer: apk_verify.SignerInfo
    info: apk_verify.PackageInfo
    server_signed: bool  # it was unsigned, and was signed with this server's key (opted in)


UNSIGNED_REFUSAL = ("the APK is unsigned, and Android can't install an unsigned APK. To stage it anyway, opt in "
                    "to signing it with this server's key (see what that means next to the option)")


def _sign_if_unsigned(tmp_path: str, sign_as: str | None) -> bool:
    """Signs an unsigned APK with its source's key when the source opted in
    (`sign_as` is that source, None without the opt-in). Returns whether it
    did. Raises for an unsigned APK without the opt-in."""
    if not apk_verify.is_unsigned(tmp_path):
        return False
    if sign_as is None:
        raise apk_verify.ApkVerifyError(UNSIGNED_REFUSAL)
    try:
        signing.sign_in_place(tmp_path, sign_as)
    except signing.SigningError as exc:
        raise apk_verify.ApkVerifyError(f"signing it with this server's key failed: {exc}") from exc
    return True


def unwrap_archive(tmp_path: str) -> apk_verify.ArchivedApk | None:
    """If tmp_path is an artifact zip, replaces it in place with the one APK
    inside. The zip is deleted by that replace, straight after extraction and
    before any APK check runs, so no archive outlives its unpacking — whatever
    happens next, only one temp file is left for the caller to clean up."""
    fd, apk_path = tempfile.mkstemp(dir=os.path.dirname(tmp_path), prefix="_upload_")
    os.close(fd)
    try:
        archived = apk_verify.extract_archived_apk(tmp_path, apk_path, MAX_UPLOAD_BYTES)
        if archived is not None:
            os.replace(apk_path, tmp_path)
        return archived
    finally:
        staging.remove_file(apk_path)  # partial extraction, or unused; gone after a replace


def _verify_file(tmp_path: str, digest: str, display_name: str, sign_as: str | None = None) -> VerifiedUpload:
    """Checks a received file (a bare APK, or a zip holding one) in place."""
    archive_name = None
    archived = unwrap_archive(tmp_path)
    if archived is not None:
        digest, archive_name, display_name = archived.sha256, display_name, display_filename(archived.name)
    apk_verify.assert_apk_container(tmp_path)
    server_signed = _sign_if_unsigned(tmp_path, sign_as)
    if server_signed:
        digest = apk_verify.sha256_file(tmp_path)
    return VerifiedUpload(digest, display_name, archive_name,
                          apk_verify.verify_signature(tmp_path), apk_verify.get_package_info(tmp_path), server_signed)


def _upload_warnings(info: apk_verify.PackageInfo, signer: apk_verify.SignerInfo) -> list[str]:
    warnings = []
    if signer.debug:
        warnings.append("It's signed with the default Android debug certificate, which is generated "
                        "per machine and identifies nobody — only push it to a device you're testing on.")
    # Android refuses an update signed by a different key, so installing this
    # would block that repo's polled releases on the device (or the reverse).
    clashing = [f"{r['owner']}/{r['repo']}" for r in db.list_repos()
                if r["expected_package"] == info.name and r["signer_sha256"] != signer.fingerprint]
    if clashing:
        warnings.append(f"Its package matches {', '.join(clashing)} but its signer doesn't — a device "
                        "with one installed will refuse the other until it's uninstalled.")
    return warnings


def new_upload_tmp() -> str:
    directory = upload_dir()
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix="_upload_")
    os.close(fd)
    return tmp_path


def stage_received(
    request: Request, tmp_path: str, digest: str, display_name: str, label: str,
    origin: str | None = None, refused_to: str = "/sources", notes: str | None = None,
    artifact: dict | None = None, sign_as: str | None = None, on_verified=None,
) -> RedirectResponse:
    """Verifies and stages a file already on disk at tmp_path, which this
    consumes: it ends up staged or removed. `origin` describes where the file
    came from, for the flash message and the audit log; without one, a zip's
    own name is used."""
    try:
        verified = _verify_file(tmp_path, digest, display_name, sign_as)
    except (UploadRejected, apk_verify.ApkVerifyError) as exc:
        staging.remove_file(tmp_path)
        logger.warning("upload rejected (%s): %s", display_name, exc)
        return redirect(refused_to, error=f"Upload refused: {exc}")
    except BaseException:
        staging.remove_file(tmp_path)
        raise
    digest, display_name, signer, info = verified.sha256, verified.display_name, verified.signer, verified.info
    if on_verified is not None:
        on_verified(verified)

    existing = db.get_uploaded_apk_by_sha256(digest)
    if existing is not None:
        staging.remove_file(tmp_path)
        return redirect(refused_to, error=f"That exact APK is already staged as \"{existing['filename']}\"")

    final_path = os.path.join(upload_dir(), f"{digest}.apk")
    os.replace(tmp_path, final_path)
    apk_id = db.insert_staged_apk(
        repo_id=None, tag=(label.strip() or info.version_name or display_name)[:120],
        filename=display_name, sha256=digest, package_name=info.name,
        signer_sha256=signer.fingerprint, path=final_path,
        version_code=info.version_code, version_name=info.version_name,
        abis=" ".join(info.abis), source="upload", is_debug=signer.debug, release_notes=notes,
        artifact=artifact, server_signed=verified.server_signed,
    )
    if apk_id is None:
        # A concurrent upload of the same file won; final_path is its file too.
        return redirect(refused_to, error="That exact APK is already staged")

    source = origin or (f" from {verified.archive_name}" if verified.archive_name else "")
    record_audit(request, "upload", f"{display_name}{source} {info.name} sha256={digest[:12]} debug={signer.debug}"
                              f"{' server_signed=True' if verified.server_signed else ''}")
    staged = f"Staged {display_name} ({info.name}){source}."
    if verified.server_signed:
        staged += (" It was unsigned, so it was signed with this server's key for "
                   + ("uploads." if sign_as == signing.UPLOADS else "this repo."))
    warnings = _upload_warnings(info, signer)
    if warnings:
        return redirect("/install", warn=" ".join([staged, *warnings]))
    return redirect("/install", ok=staged)
