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
    include_prereleases INTEGER NOT NULL DEFAULT 0,
    UNIQUE(owner, repo)
);

CREATE TABLE IF NOT EXISTS staged_apks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id       INTEGER REFERENCES repos(id) ON DELETE CASCADE,  -- NULL for a manual upload
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
    source        TEXT NOT NULL DEFAULT 'github',  -- 'github' | 'upload'
    is_debug      INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS device_follows (
    device_serial TEXT NOT NULL REFERENCES devices(serial) ON DELETE CASCADE,
    repo_id       INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    PRIMARY KEY (device_serial, repo_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    action  TEXT NOT NULL,
    detail  TEXT NOT NULL,
    client  TEXT
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

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Apprise service URLs added on the Settings page. They carry credentials:
-- never logged, only ever rendered in Apprise's privacy-masked form.
CREATE TABLE IF NOT EXISTS notify_targets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    url        TEXT NOT NULL UNIQUE,
    label      TEXT,
    created_at TEXT NOT NULL
);

-- Colour pairs saved from Settings -> Appearance, next to the built-in presets.
CREATE TABLE IF NOT EXISTS saved_colours (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    primary_color   TEXT NOT NULL,
    secondary_color TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Two-factor sign-in. The TOTP secret itself is in meta (mfa_secret); these
-- hold only SHA-256 hashes, so a copy of the database can't be replayed.
CREATE TABLE IF NOT EXISTS mfa_recovery_codes (
    code_hash TEXT PRIMARY KEY,
    used_at   TEXT
);
CREATE TABLE IF NOT EXISTS mfa_trusted_browsers (
    token_hash   TEXT PRIMARY KEY,
    label        TEXT NOT NULL,
    client       TEXT,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_staged_apks_repo_id ON staged_apks(repo_id);
-- UNIQUE(repo_id, ...) can't dedupe uploads (repo_id is NULL), so this does.
CREATE UNIQUE INDEX IF NOT EXISTS idx_staged_apks_upload_sha256
    ON staged_apks(sha256) WHERE source = 'upload' AND pruned_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_staged_apks_downloaded_at ON staged_apks(downloaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_device_follows_repo_id ON device_follows(repo_id);
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
    _rebuild_staged_apks_unique()
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)


def _rebuild_staged_apks_unique() -> None:
    """0.2.0 allowed one staged APK per (repo, tag); a release can now carry
    one per ABI. Manual uploads then made repo_id nullable. SQLite can alter
    neither a UNIQUE constraint nor a NOT NULL, so an old table is rebuilt.
    Foreign keys must be OFF while the old table is dropped, or the ON DELETE
    CASCADE on installs would wipe the install history."""
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'staged_apks'"
        ).fetchone()
        if row is None:
            return
        cols_now = {r[1] for r in conn.execute("PRAGMA table_info(staged_apks)")}
        if "source" in cols_now and "UNIQUE(repo_id, tag)" not in row[0]:
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
    ("repos", "include_prereleases", "INTEGER NOT NULL DEFAULT 0"),
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


def create_repo(owner: str, repo: str, asset_glob: str, include_prereleases: bool = False) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO repos (owner, repo, asset_glob, include_prereleases) VALUES (?, ?, ?, ?)",
            (owner, repo, asset_glob, int(include_prereleases)),
        )
        return cur.lastrowid


def set_include_prereleases(repo_id: int, include: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE repos SET include_prereleases = ? WHERE id = ?", (int(include), repo_id))


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
    repo_id: int | None, tag: str, filename: str, sha256: str,
    package_name: str, signer_sha256: str, path: str,
    release_notes: str | None = None,
    version_code: int | None = None, version_name: str | None = None,
    abis: str = "", source: str = "github", is_debug: bool = False,
) -> int | None:
    with get_conn() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO staged_apks
                   (repo_id, tag, filename, sha256, package_name, signer_sha256, path,
                    downloaded_at, release_notes, version_code, version_name, abis,
                    source, is_debug)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (repo_id, tag, filename, sha256, package_name, signer_sha256, path, now(),
                 release_notes, version_code, version_name, abis, source, int(is_debug)),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


# Uploads have no repo: LEFT JOIN so they aren't silently dropped, and give
# every row one display label instead of owner/repo.
_SOURCE_LABEL = "COALESCE(repos.owner || '/' || repos.repo, 'Manual upload') AS source_label"


def list_staged_apks(repo_id: int | None = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        if repo_id is not None:
            return conn.execute(
                f"""SELECT staged_apks.*, repos.owner, repos.repo, {_SOURCE_LABEL}
                   FROM staged_apks LEFT JOIN repos ON repos.id = staged_apks.repo_id
                   WHERE repo_id = ? AND pruned_at IS NULL ORDER BY downloaded_at DESC""",
                (repo_id,),
            ).fetchall()
        return conn.execute(
            f"""SELECT staged_apks.*, repos.owner, repos.repo, {_SOURCE_LABEL}
               FROM staged_apks LEFT JOIN repos ON repos.id = staged_apks.repo_id
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
            f"""SELECT s.*, repos.owner, repos.repo, {_SOURCE_LABEL} FROM staged_apks s
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
            f"""SELECT staged_apks.*, repos.owner, repos.repo, {_SOURCE_LABEL}
               FROM staged_apks LEFT JOIN repos ON repos.id = staged_apks.repo_id
               WHERE staged_apks.id = ?""",
            (apk_id,),
        ).fetchone()


def get_uploaded_apk_by_sha256(sha256: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM staged_apks WHERE source = 'upload' AND sha256 = ? AND pruned_at IS NULL",
            (sha256,),
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


def rename_device(old: str, new: str) -> bool:
    """Re-keys a device, carrying its nickname, trust, follows, package
    state and install history. False (nothing changed) if `new` exists."""
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM devices WHERE serial = ?", (new,)).fetchone():
            return False
        # The children are updated after the parent, inside one transaction.
        conn.execute("PRAGMA defer_foreign_keys = ON")
        conn.execute("UPDATE devices SET serial = ? WHERE serial = ?", (new, old))
        for table in ("device_packages", "device_follows", "installs"):
            conn.execute(f"UPDATE {table} SET device_serial = ? WHERE device_serial = ?", (new, old))  # nosec B608 - fixed table names
        return True


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


def fail_interrupted_installs() -> int:
    """At startup: a push that was pending or installing when the app stopped
    will never finish. Mark it failed so its status page stops polling."""
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE installs SET status = 'failed', finished_at = ?,
                   log = COALESCE(log || char(10), '') || 'Interrupted: the app restarted before this push finished.'
               WHERE status IN ('pending', 'installing')""",
            (now(),),
        )
        return cur.rowcount


def finish_install(install_id: int, status: str, log: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE installs SET status = ?, finished_at = ?, log = ? WHERE id = ?",
            (status, now(), log, install_id),
        )


_INSTALL_SELECT = f"""
    SELECT installs.*, devices.nickname, staged_apks.filename, staged_apks.tag,
           repos.owner, repos.repo, {_SOURCE_LABEL}
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


# ---- auto-update follows ----

def set_follow(serial: str, repo_id: int, follow: bool) -> None:
    with get_conn() as conn:
        if follow:
            conn.execute(
                "INSERT OR IGNORE INTO device_follows (device_serial, repo_id) VALUES (?, ?)", (serial, repo_id),
            )
        else:
            conn.execute("DELETE FROM device_follows WHERE device_serial = ? AND repo_id = ?", (serial, repo_id))


def follows_set() -> set[tuple[str, int]]:
    with get_conn() as conn:
        return {(r["device_serial"], r["repo_id"]) for r in conn.execute("SELECT * FROM device_follows")}


def list_followers(repo_id: int) -> list[sqlite3.Row]:
    """Trusted devices following a repo. Untrusted followers are excluded
    here and refused again at push time."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT devices.* FROM devices
               JOIN device_follows ON device_follows.device_serial = devices.serial
               WHERE device_follows.repo_id = ? AND devices.trusted = 1""",
            (repo_id,),
        ).fetchall()


# ---- audit log ----

AUDIT_KEEP = 5000


def insert_audit(action: str, detail: str, client: str | None) -> None:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO audit_log (at, action, detail, client) VALUES (?, ?, ?, ?)",
            (now(), action, detail, client),
        )
        if cur.lastrowid % 100 == 0:  # trim occasionally, not on every write
            conn.execute("DELETE FROM audit_log WHERE id <= ?", (cur.lastrowid - AUDIT_KEEP,))


def list_audit(limit: int = 200) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ---- meta ----

def get_meta(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(key: str, value: str | None) -> None:
    """None deletes the key, restoring whatever the default is."""
    with get_conn() as conn:
        if value is None:
            conn.execute("DELETE FROM meta WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def increment_meta(key: str) -> int:
    """Adds one to an integer meta value (absent = 0) in a single statement,
    so concurrent requests can't both read the same count. Returns the new value."""
    with get_conn() as conn:
        return int(conn.execute(
            """INSERT INTO meta (key, value) VALUES (?, '1')
               ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1
               RETURNING value""",
            (key,),
        ).fetchone()[0])


def advance_meta(key: str, value: int) -> bool:
    """Sets an integer meta value only if `value` is greater than the stored
    one (or none is stored), atomically. True if it was set."""
    with get_conn() as conn:
        return conn.execute(
            """INSERT INTO meta (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value
               WHERE CAST(meta.value AS INTEGER) < CAST(excluded.value AS INTEGER)""",
            (key, str(value)),
        ).rowcount == 1


def get_session_epoch() -> int:
    value = get_meta("session_epoch")
    return int(value) if value else 0


def bump_session_epoch() -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO meta (key, value) VALUES ('session_epoch', '1')
               ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"""
        )


# ---- notification targets ----

def list_notify_targets() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM notify_targets ORDER BY id").fetchall()


def get_notify_target(target_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM notify_targets WHERE id = ?", (target_id,)).fetchone()


def add_notify_target(url: str, label: str | None) -> int | None:
    """Returns the new id, or None if that exact URL is already stored."""
    with get_conn() as conn:
        try:
            return conn.execute(
                "INSERT INTO notify_targets (url, label, created_at) VALUES (?, ?, ?)", (url, label, now()),
            ).lastrowid
        except sqlite3.IntegrityError:
            return None


def delete_notify_target(target_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM notify_targets WHERE id = ?", (target_id,))


# ---- saved colour pairs ----

def list_saved_colours() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM saved_colours ORDER BY id").fetchall()


def get_saved_colour(colour_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM saved_colours WHERE id = ?", (colour_id,)).fetchone()


def save_colour(name: str, primary: str, secondary: str) -> int:
    """Saves a pair, replacing the colours of an existing one with the same name."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO saved_colours (name, primary_color, secondary_color, created_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET primary_color = excluded.primary_color,
                   secondary_color = excluded.secondary_color""",
            (name, primary, secondary, now()),
        )
        return conn.execute("SELECT id FROM saved_colours WHERE name = ?", (name,)).fetchone()["id"]


def delete_saved_colour(colour_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM saved_colours WHERE id = ?", (colour_id,))


# ---- two-factor sign-in ----

def replace_recovery_codes(code_hashes: list[str]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM mfa_recovery_codes")
        conn.executemany("INSERT INTO mfa_recovery_codes (code_hash) VALUES (?)", [(h,) for h in code_hashes])


def use_recovery_code(code_hash: str) -> bool:
    """Marks an unused code used. True only the first time."""
    with get_conn() as conn:
        return conn.execute(
            "UPDATE mfa_recovery_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL", (now(), code_hash),
        ).rowcount == 1


def recovery_codes_left() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM mfa_recovery_codes WHERE used_at IS NULL").fetchone()[0]


def add_trusted_browser(token_hash: str, label: str, client: str | None, expires_at: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO mfa_trusted_browsers (token_hash, label, client, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (token_hash, label, client, now(), expires_at),
        )


def get_trusted_browser(token_hash: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM mfa_trusted_browsers WHERE token_hash = ?", (token_hash,)).fetchone()


def touch_trusted_browser(token_hash: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE mfa_trusted_browsers SET last_used_at = ? WHERE token_hash = ?", (now(), token_hash))


def list_trusted_browsers() -> list[sqlite3.Row]:
    with get_conn() as conn:
        conn.execute("DELETE FROM mfa_trusted_browsers WHERE expires_at <= ?", (now(),))
        return conn.execute("SELECT * FROM mfa_trusted_browsers ORDER BY created_at DESC").fetchall()


def delete_trusted_browser(token_hash: str | None = None) -> None:
    """One browser, or (no argument) all of them."""
    with get_conn() as conn:
        if token_hash is None:
            conn.execute("DELETE FROM mfa_trusted_browsers")
        else:
            conn.execute("DELETE FROM mfa_trusted_browsers WHERE token_hash = ?", (token_hash,))


def ping() -> None:
    with get_conn() as conn:
        conn.execute("SELECT 1").fetchone()
