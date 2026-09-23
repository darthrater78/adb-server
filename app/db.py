import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "/data/app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    owner             TEXT NOT NULL,
    repo              TEXT NOT NULL,
    asset_glob        TEXT NOT NULL DEFAULT '*.apk',
    expected_package  TEXT,
    signer_sha256     TEXT,
    last_tag          TEXT,
    last_checked_at   TEXT,
    last_error        TEXT,
    rejected_tag      TEXT,
    pending_package   TEXT,
    pending_signer    TEXT,
    pending_lineage_ok INTEGER,
    UNIQUE(owner, repo)
);

CREATE TABLE IF NOT EXISTS staged_apks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id       INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    tag           TEXT NOT NULL,
    filename      TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    package_name  TEXT NOT NULL,
    signer_sha256 TEXT NOT NULL,
    path          TEXT NOT NULL,
    downloaded_at TEXT NOT NULL,
    release_notes TEXT,
    pruned_at     TEXT,
    version_code  INTEGER,
    version_name  TEXT,
    abis          TEXT NOT NULL DEFAULT '',
    UNIQUE(repo_id, tag, filename)
);

CREATE TABLE IF NOT EXISTS devices (
    serial            TEXT PRIMARY KEY,
    nickname          TEXT,
    trusted           INTEGER NOT NULL DEFAULT 0,
    last_connect_addr TEXT,
    paired_at         TEXT NOT NULL,
    last_seen_at      TEXT,
    abis              TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS device_packages (
    device_serial TEXT NOT NULL REFERENCES devices(serial) ON DELETE CASCADE,
    package_name  TEXT NOT NULL,
    installed     INTEGER NOT NULL,
    version_code  INTEGER,
    version_name  TEXT,
    checked_at    TEXT NOT NULL,
    PRIMARY KEY (device_serial, package_name)
);

CREATE TABLE IF NOT EXISTS installs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_serial TEXT NOT NULL REFERENCES devices(serial) ON DELETE CASCADE,
    apk_id        INTEGER NOT NULL REFERENCES staged_apks(id) ON DELETE CASCADE,
    status        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    log           TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    _rebuild_staged_apks_unique()
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)


def _rebuild_staged_apks_unique() -> None:
    """0.2.0 allowed one staged APK per (repo, tag); a release can now carry
    one per ABI. SQLite can't alter a UNIQUE constraint, so an old table is
    rebuilt. Foreign keys must be OFF while the old table is dropped, or the
    ON DELETE CASCADE on installs would wipe the install history."""
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'staged_apks'"
        ).fetchone()
        if row is None or "UNIQUE(repo_id, tag)" not in row[0]:
            return
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        # SQLite's documented order: build the new table under a temporary
        # name, copy, drop the old, rename the new. Renaming the *old* table
        # instead would rewrite installs' foreign key to point at it.
        ddl = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS staged_apks"):].split(";", 1)[0]
        conn.execute(ddl.replace("IF NOT EXISTS staged_apks", "staged_apks_new", 1))
        old_cols = [r[1] for r in conn.execute("PRAGMA table_info(staged_apks)")]
        new_cols = {r[1] for r in conn.execute("PRAGMA table_info(staged_apks_new)")}
        cols = ", ".join(c for c in old_cols if c in new_cols)
        conn.execute(f"INSERT INTO staged_apks_new ({cols}) SELECT {cols} FROM staged_apks")
        conn.execute("DROP TABLE staged_apks")
        conn.execute("ALTER TABLE staged_apks_new RENAME TO staged_apks")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            conn.execute("ROLLBACK")
            raise RuntimeError("staged_apks migration would break foreign keys — rolled back")
        conn.execute("COMMIT")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


# (table, column, type) — every column added after 0.1.0. Table and column
# names here are constants, never user input, so the f-string ALTER is safe.
_ADDED_COLUMNS = [
    ("staged_apks", "release_notes", "TEXT"),
    ("staged_apks", "pruned_at", "TEXT"),
    ("staged_apks", "version_code", "INTEGER"),
    ("staged_apks", "version_name", "TEXT"),
    ("staged_apks", "abis", "TEXT NOT NULL DEFAULT ''"),
    ("repos", "rejected_tag", "TEXT"),
    ("repos", "pending_package", "TEXT"),
    ("repos", "pending_signer", "TEXT"),
    ("repos", "pending_lineage_ok", "INTEGER"),
    ("devices", "abis", "TEXT NOT NULL DEFAULT ''"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS never adds columns to a table that already
    exists, so a DB created before a schema change needs an explicit ALTER."""
    for table, column, col_type in _ADDED_COLUMNS:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


# ---- repos ----

def list_repos() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM repos ORDER BY owner, repo").fetchall()


def get_repo(repo_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()


def create_repo(owner: str, repo: str, asset_glob: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO repos (owner, repo, asset_glob) VALUES (?, ?, ?)",
            (owner, repo, asset_glob),
        )
        return cur.lastrowid


def delete_repo(repo_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))


def update_repo_check(
    repo_id: int,
    last_tag: str | None = None,
    last_error: str | None = None,
    expected_package: str | None = None,
    signer_sha256: str | None = None,
) -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
        if row is None:
            return
        conn.execute(
            """UPDATE repos SET
                last_checked_at = ?,
                last_tag = COALESCE(?, last_tag),
                last_error = ?,
                expected_package = COALESCE(?, expected_package),
                signer_sha256 = COALESCE(?, signer_sha256)
               WHERE id = ?""",
            (now(), last_tag, last_error, expected_package, signer_sha256, repo_id),
        )


def mark_repo_checked(repo_id: int) -> None:
    """Records a check without touching last_error — used when a poll sees a
    release it already rejected, so the rejection stays visible."""
    with get_conn() as conn:
        conn.execute("UPDATE repos SET last_checked_at = ? WHERE id = ?", (now(), repo_id))


def set_rejected_tag(
    repo_id: int, tag: str, error: str,
    pending_package: str | None = None, pending_signer: str | None = None,
    pending_lineage_ok: bool | None = None,
) -> None:
    """A tag that failed verification or the pin check. The poller skips it
    until a newer release appears, instead of re-downloading it every poll.
    For a pin mismatch, the new package/signer are kept for the operator to
    review and, if genuine, accept as the new pin."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE repos SET rejected_tag = ?, last_error = ?, last_checked_at = ?,
                   pending_package = ?, pending_signer = ?, pending_lineage_ok = ?
               WHERE id = ?""",
            (tag, error, now(), pending_package, pending_signer,
             None if pending_lineage_ok is None else int(pending_lineage_ok), repo_id),
        )


def accept_pending_signer(repo_id: int) -> bool:
    """Re-pins a repo to the package/signer of its last pin mismatch and
    clears the rejection so the next check stages that release. Returns False
    if there was nothing pending."""
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE repos SET expected_package = pending_package, signer_sha256 = pending_signer,
                   pending_package = NULL, pending_signer = NULL, pending_lineage_ok = NULL,
                   rejected_tag = NULL, last_error = NULL
               WHERE id = ? AND pending_signer IS NOT NULL""",
            (repo_id,),
        )
        return cur.rowcount == 1


# ---- staged apks ----

def insert_staged_apk(
    repo_id: int, tag: str, filename: str, sha256: str,
    package_name: str, signer_sha256: str, path: str,
    release_notes: str | None = None,
    version_code: int | None = None, version_name: str | None = None,
    abis: str = "",
) -> int | None:
    with get_conn() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO staged_apks
                   (repo_id, tag, filename, sha256, package_name, signer_sha256, path,
                    downloaded_at, release_notes, version_code, version_name, abis)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (repo_id, tag, filename, sha256, package_name, signer_sha256, path, now(),
                 release_notes, version_code, version_name, abis),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def list_staged_apks(repo_id: int | None = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        if repo_id is not None:
            return conn.execute(
                """SELECT staged_apks.*, repos.owner, repos.repo
                   FROM staged_apks JOIN repos ON repos.id = staged_apks.repo_id
                   WHERE repo_id = ? AND pruned_at IS NULL ORDER BY downloaded_at DESC""",
                (repo_id,),
            ).fetchall()
        return conn.execute(
            """SELECT staged_apks.*, repos.owner, repos.repo
               FROM staged_apks JOIN repos ON repos.id = staged_apks.repo_id
               WHERE pruned_at IS NULL
               ORDER BY downloaded_at DESC"""
        ).fetchall()


def list_prunable_apks(repo_id: int, keep: int) -> list[sqlite3.Row]:
    """Unpruned staged APKs for a repo outside its newest `keep` releases
    (a release's ABI variants are kept or pruned together)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM staged_apks
               WHERE repo_id = ? AND pruned_at IS NULL AND tag NOT IN (
                   SELECT tag FROM staged_apks WHERE repo_id = ? AND pruned_at IS NULL
                   GROUP BY tag ORDER BY MAX(downloaded_at) DESC, MAX(id) DESC LIMIT ?
               )""",
            (repo_id, repo_id, keep),
        ).fetchall()


def list_latest_variants() -> list[sqlite3.Row]:
    """Every unpruned variant of each repo's most recently staged release."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT s.*, repos.owner, repos.repo FROM staged_apks s
               JOIN repos ON repos.id = s.repo_id
               WHERE s.pruned_at IS NULL AND s.tag = (
                   SELECT tag FROM staged_apks WHERE repo_id = s.repo_id AND pruned_at IS NULL
                   ORDER BY downloaded_at DESC, id DESC LIMIT 1
               )
               ORDER BY repos.owner, repos.repo, s.filename"""
        ).fetchall()


def mark_apk_pruned(apk_id: int) -> None:
    # The row stays so install history keeps pointing at it; only the file goes.
    with get_conn() as conn:
        conn.execute("UPDATE staged_apks SET pruned_at = ? WHERE id = ?", (now(), apk_id))


def get_staged_apk(apk_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """SELECT staged_apks.*, repos.owner, repos.repo
               FROM staged_apks JOIN repos ON repos.id = staged_apks.repo_id
               WHERE staged_apks.id = ?""",
            (apk_id,),
        ).fetchone()


# ---- devices ----

def list_devices() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM devices ORDER BY paired_at DESC").fetchall()


def get_device(serial: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM devices WHERE serial = ?", (serial,)).fetchone()


def upsert_paired_device(serial: str, connect_addr: str) -> None:
    with get_conn() as conn:
        existing = conn.execute("SELECT serial FROM devices WHERE serial = ?", (serial,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE devices SET last_connect_addr = ?, last_seen_at = ? WHERE serial = ?",
                (connect_addr, now(), serial),
            )
        else:
            conn.execute(
                """INSERT INTO devices (serial, last_connect_addr, trusted, paired_at, last_seen_at)
                   VALUES (?, ?, 0, ?, ?)""",
                (serial, connect_addr, now(), now()),
            )


def set_device_trusted(serial: str, trusted: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE devices SET trusted = ? WHERE serial = ?", (1 if trusted else 0, serial))


def set_device_nickname(serial: str, nickname: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE devices SET nickname = ? WHERE serial = ?", (nickname, serial))


def touch_device(serial: str, connect_addr: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE devices SET last_connect_addr = ?, last_seen_at = ? WHERE serial = ?",
            (connect_addr, now(), serial),
        )


def set_device_abis(serial: str, abis: list[str]) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE devices SET abis = ? WHERE serial = ?", (" ".join(abis), serial))


def upsert_device_package(
    serial: str, package: str, installed: bool,
    version_code: int | None = None, version_name: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO device_packages
                   (device_serial, package_name, installed, version_code, version_name, checked_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(device_serial, package_name) DO UPDATE SET
                   installed = excluded.installed, version_code = excluded.version_code,
                   version_name = excluded.version_name, checked_at = excluded.checked_at""",
            (serial, package, int(installed), version_code, version_name, now()),
        )


def device_packages_map() -> dict[tuple[str, str], sqlite3.Row]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM device_packages").fetchall()
    return {(r["device_serial"], r["package_name"]): r for r in rows}


def delete_device(serial: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM devices WHERE serial = ?", (serial,))


# ---- installs ----

def insert_install(device_serial: str, apk_id: int, status: str, log: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO installs (device_serial, apk_id, status, started_at, log)
               VALUES (?, ?, ?, ?, ?)""",
            (device_serial, apk_id, status, now(), log),
        )
        return cur.lastrowid


def set_install_status(install_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE installs SET status = ? WHERE id = ?", (status, install_id))


def finish_install(install_id: int, status: str, log: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE installs SET status = ?, finished_at = ?, log = ? WHERE id = ?",
            (status, now(), log, install_id),
        )


_INSTALL_SELECT = """
    SELECT installs.*, devices.nickname, staged_apks.filename, staged_apks.tag,
           repos.owner, repos.repo
    FROM installs
    JOIN devices ON devices.serial = installs.device_serial
    JOIN staged_apks ON staged_apks.id = installs.apk_id
    JOIN repos ON repos.id = staged_apks.repo_id
"""


def get_install(install_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            _INSTALL_SELECT + " WHERE installs.id = ?", (install_id,),
        ).fetchone()


def list_installs(limit: int = 100) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            _INSTALL_SELECT + " ORDER BY installs.started_at DESC LIMIT ?", (limit,),
        ).fetchall()
