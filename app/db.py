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
    UNIQUE(owner, repo)
);

CREATE TABLE IF NOT EXISTS staged_apks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    -- NULL for a manually uploaded APK: it has no upstream repo to belong to,
    -- and the operator who uploaded it is its provenance. UNIQUE(repo_id, tag)
    -- below therefore does not constrain uploads, since SQLite treats NULLs in
    -- a unique index as distinct.
    repo_id       INTEGER REFERENCES repos(id) ON DELETE CASCADE,
    source        TEXT NOT NULL DEFAULT 'github',
    -- Signed with the default Android debug certificate. Only ever 1 for an
    -- uploaded APK: a polled release that is debug-signed is refused, never
    -- staged.
    is_debug      INTEGER NOT NULL DEFAULT 0,
    tag           TEXT NOT NULL,
    filename      TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    package_name  TEXT NOT NULL,
    signer_sha256 TEXT NOT NULL,
    path          TEXT NOT NULL,
    downloaded_at TEXT NOT NULL,
    release_notes TEXT,
    UNIQUE(repo_id, tag)
);

CREATE TABLE IF NOT EXISTS devices (
    serial            TEXT PRIMARY KEY,
    nickname          TEXT,
    trusted           INTEGER NOT NULL DEFAULT 0,
    last_connect_addr TEXT,
    paired_at         TEXT NOT NULL,
    last_seen_at      TEXT
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

-- Every foreign key these tables join on, plus the columns each listing
-- sorts by. IF NOT EXISTS means this runs against an existing database on
-- the next start, the same way the CREATE TABLEs do.
CREATE INDEX IF NOT EXISTS idx_staged_apks_repo_id ON staged_apks(repo_id);
-- The same file uploaded twice is the same APK; identical content from two
-- different repos is not our business to collapse, so this is upload-only.
CREATE UNIQUE INDEX IF NOT EXISTS idx_staged_apks_upload_sha256
    ON staged_apks(sha256) WHERE source = 'upload';
CREATE INDEX IF NOT EXISTS idx_staged_apks_downloaded_at ON staged_apks(downloaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_devices_paired_at ON devices(paired_at DESC);
CREATE INDEX IF NOT EXISTS idx_installs_device_serial ON installs(device_serial);
CREATE INDEX IF NOT EXISTS idx_installs_apk_id ON installs(apk_id);
CREATE INDEX IF NOT EXISTS idx_installs_started_at ON installs(started_at DESC);
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
    # Runs first: the rebuild below drops and recreates staged_apks, which
    # would take the SCHEMA indexes with it if they had already been created.
    _migrate_staged_apks_shape()
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)


_STAGED_APKS_COLUMNS = (
    "id, repo_id, source, is_debug, tag, filename, sha256, package_name, "
    "signer_sha256, path, downloaded_at, release_notes"
)

# Columns added since the first release, as (name, definition). Applied with
# ALTER TABLE ADD COLUMN before any table rebuild, so the rebuild's column
# list is present on both sides of the copy.
_ADDED_COLUMNS = (
    ("source", "TEXT NOT NULL DEFAULT 'github'"),
    ("is_debug", "INTEGER NOT NULL DEFAULT 0"),
)


def _migrate_staged_apks_shape() -> None:
    """Brings an existing staged_apks up to the current shape: a `source`
    column, and a `repo_id` that admits NULL for manual uploads.

    SQLite cannot drop a NOT NULL constraint in place, so the documented
    workaround is to rebuild the table. Done on its own autocommit connection
    because PRAGMA foreign_keys cannot be changed inside a transaction, and
    with an explicit row count check before the old table is dropped."""
    if not os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'staged_apks'"
        ).fetchone()
        if table is None:
            return

        info = list(conn.execute("PRAGMA table_info(staged_apks)"))
        columns = {row["name"] for row in info}
        for name, definition in _ADDED_COLUMNS:
            if name not in columns:
                conn.execute(f"ALTER TABLE staged_apks ADD COLUMN {name} {definition}")

        repo_id_is_not_null = any(row["name"] == "repo_id" and row["notnull"] for row in info)
        if not repo_id_is_not_null:
            return

        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            before = conn.execute("SELECT COUNT(*) AS n FROM staged_apks").fetchone()["n"]
            conn.execute("""
                CREATE TABLE staged_apks_new (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_id       INTEGER REFERENCES repos(id) ON DELETE CASCADE,
                    source        TEXT NOT NULL DEFAULT 'github',
                    is_debug      INTEGER NOT NULL DEFAULT 0,
                    tag           TEXT NOT NULL,
                    filename      TEXT NOT NULL,
                    sha256        TEXT NOT NULL,
                    package_name  TEXT NOT NULL,
                    signer_sha256 TEXT NOT NULL,
                    path          TEXT NOT NULL,
                    downloaded_at TEXT NOT NULL,
                    release_notes TEXT,
                    UNIQUE(repo_id, tag)
                )
            """)
            conn.execute(
                f"INSERT INTO staged_apks_new ({_STAGED_APKS_COLUMNS}) "
                f"SELECT {_STAGED_APKS_COLUMNS} FROM staged_apks"
            )
            after = conn.execute("SELECT COUNT(*) AS n FROM staged_apks_new").fetchone()["n"]
            if after != before:
                raise RuntimeError(f"staged_apks rebuild copied {after} of {before} rows — rolled back")
            conn.execute("DROP TABLE staged_apks")
            conn.execute("ALTER TABLE staged_apks_new RENAME TO staged_apks")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS never adds columns to a table that already
    exists, so a DB created before a schema change needs an explicit ALTER."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(staged_apks)")}
    if "release_notes" not in cols:
        conn.execute("ALTER TABLE staged_apks ADD COLUMN release_notes TEXT")


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


# ---- staged apks ----

def insert_staged_apk(
    repo_id: int | None, tag: str, filename: str, sha256: str,
    package_name: str, signer_sha256: str, path: str,
    release_notes: str | None = None, source: str = "github", is_debug: bool = False,
) -> int | None:
    """Returns None when the row already exists — a duplicate (repo_id, tag)
    for a polled release, or a re-upload of a file already staged."""
    with get_conn() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO staged_apks
                   (repo_id, source, is_debug, tag, filename, sha256, package_name,
                    signer_sha256, path, downloaded_at, release_notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (repo_id, source, 1 if is_debug else 0, tag, filename, sha256, package_name,
                 signer_sha256, path, now(), release_notes),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def get_uploaded_apk_by_sha256(sha256: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM staged_apks WHERE sha256 = ? AND source = 'upload'", (sha256,),
        ).fetchone()


def list_staged_apks(repo_id: int | None = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        if repo_id is not None:
            return conn.execute(
                """SELECT staged_apks.*, repos.owner, repos.repo
                   FROM staged_apks LEFT JOIN repos ON repos.id = staged_apks.repo_id
                   WHERE repo_id = ? ORDER BY downloaded_at DESC""",
                (repo_id,),
            ).fetchall()
        return conn.execute(
            """SELECT staged_apks.*, repos.owner, repos.repo
               FROM staged_apks LEFT JOIN repos ON repos.id = staged_apks.repo_id
               ORDER BY downloaded_at DESC"""
        ).fetchall()


def get_staged_apk(apk_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """SELECT staged_apks.*, repos.owner, repos.repo
               FROM staged_apks LEFT JOIN repos ON repos.id = staged_apks.repo_id
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


def set_device_nickname(serial: str, nickname: str | None) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE devices SET nickname = ? WHERE serial = ?", (nickname, serial))


def touch_device(serial: str, connect_addr: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE devices SET last_connect_addr = ?, last_seen_at = ? WHERE serial = ?",
            (connect_addr, now(), serial),
        )


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
    LEFT JOIN repos ON repos.id = staged_apks.repo_id
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
