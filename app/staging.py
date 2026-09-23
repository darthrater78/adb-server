"""Filesystem side of staged APKs: where they live, and removing them safely.

Every path this module deletes is re-checked to sit inside STAGING_ROOT, even
though they are all server-generated, so a bad DB row can never turn a cleanup
into a delete somewhere else on the volume."""
import logging
import os
import shutil

import db

logger = logging.getLogger("staging")

STAGING_ROOT = os.environ.get("STAGING_ROOT", "/data/staging")
KEEP_RELEASES_PER_REPO = max(1, int(os.environ.get("KEEP_RELEASES_PER_REPO", "3")))


def repo_dir(repo_id: int) -> str:
    return os.path.join(STAGING_ROOT, str(int(repo_id)))


def _inside_root(path: str) -> bool:
    root = os.path.realpath(STAGING_ROOT)
    target = os.path.realpath(path)
    return target != root and os.path.commonpath([root, target]) == root


def remove_file(path: str) -> None:
    if not _inside_root(path):
        logger.error("refusing to delete %s: outside STAGING_ROOT", path)
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def remove_repo_dir(repo_id: int) -> None:
    path = repo_dir(repo_id)
    if _inside_root(path):
        shutil.rmtree(path, ignore_errors=True)


def prune_repo(repo_id: int, keep: int = KEEP_RELEASES_PER_REPO) -> int:
    """Deletes the files of all but the newest `keep` staged releases. Rows are
    kept (marked pruned) so install history still resolves. Returns count."""
    pruned = 0
    for apk in db.list_prunable_apks(repo_id, keep):
        remove_file(apk["path"])
        db.mark_apk_pruned(apk["id"])
        pruned += 1
    return pruned
