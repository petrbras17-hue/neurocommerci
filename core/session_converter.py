"""
Converts Telethon .session files to Pyrogram session files.

Both Telethon and Pyrogram use the same Telegram auth_key internally.
Telethon stores sessions as SQLite files (.session) with schema:
  - sessions: dc_id, server_address, port, auth_key (BLOB 256 bytes), takeout_id
  - entities: id, hash, username, phone, name, date
  - version: version (INTEGER)

Pyrogram also uses SQLite files (.session) but with a different schema:
  - sessions: dc_id, api_id, test_mode, auth_key (BLOB 256 bytes), date, user_id, is_bot
  - peers: id, access_hash, type, username, phone_number, last_update_on
  - version: number (INTEGER)

This converter extracts the auth_key + dc_id from Telethon and creates
a Pyrogram-compatible session file with the same auth_key, preserving
the authentication state without requiring re-login.

Reference:
  - Pyrogram schema version: 3 (pyrogram.storage.sqlite_storage.SQLiteStorage.VERSION)
  - Auth key is always 256 bytes in both formats.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from utils.logger import log


# ---------------------------------------------------------------------------
# Pyrogram v2 SQLite schema (from pyrogram/storage/sqlite_storage.py)
# ---------------------------------------------------------------------------
PYROGRAM_SCHEMA = """
CREATE TABLE sessions
(
    dc_id     INTEGER PRIMARY KEY,
    api_id    INTEGER,
    test_mode INTEGER,
    auth_key  BLOB,
    date      INTEGER NOT NULL,
    user_id   INTEGER,
    is_bot    INTEGER
);

CREATE TABLE peers
(
    id             INTEGER PRIMARY KEY,
    access_hash    INTEGER,
    type           INTEGER NOT NULL,
    username       TEXT,
    phone_number   TEXT,
    last_update_on INTEGER NOT NULL DEFAULT (CAST(STRFTIME('%s', 'now') AS INTEGER))
);

CREATE TABLE version
(
    number INTEGER PRIMARY KEY
);

CREATE INDEX idx_peers_id ON peers (id);
CREATE INDEX idx_peers_username ON peers (username);
CREATE INDEX idx_peers_phone_number ON peers (phone_number);

CREATE TRIGGER trg_peers_last_update_on
    AFTER UPDATE
    ON peers
BEGIN
    UPDATE peers
    SET last_update_on = CAST(STRFTIME('%s', 'now') AS INTEGER)
    WHERE id = NEW.id;
END;
"""

PYROGRAM_SCHEMA_VERSION = 3
AUTH_KEY_LENGTH = 256


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TelethonSessionData:
    """Raw data extracted from a Telethon .session SQLite file."""

    dc_id: int
    server_address: str
    port: int
    auth_key: bytes
    user_id: Optional[int] = None
    phone: Optional[str] = None

    def validate(self) -> None:
        """Raise ValueError if the session data looks invalid."""
        if not self.auth_key:
            raise ValueError("auth_key is empty")
        if len(self.auth_key) != AUTH_KEY_LENGTH:
            raise ValueError(
                "auth_key must be {} bytes, got {}".format(
                    AUTH_KEY_LENGTH, len(self.auth_key)
                )
            )
        if not 1 <= self.dc_id <= 5:
            raise ValueError("dc_id must be 1-5, got {}".format(self.dc_id))


@dataclass(frozen=True)
class ConversionResult:
    """Result of a single session conversion."""

    source_path: Path
    target_path: Path
    dc_id: int
    user_id: Optional[int]
    auth_key_sha256_hex: str  # first 8 chars only, for safe logging
    success: bool
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Core conversion functions
# ---------------------------------------------------------------------------
def read_telethon_session(session_path: Path) -> TelethonSessionData:
    """
    Read a Telethon .session SQLite file and extract session data.

    Args:
        session_path: Path to the .session file (with or without .session suffix).

    Returns:
        TelethonSessionData with dc_id, server_address, port, auth_key,
        and optionally user_id and phone from the entities table.

    Raises:
        FileNotFoundError: If the session file does not exist.
        ValueError: If the session file is empty or has invalid data.
        sqlite3.Error: On database read errors.
    """
    path = Path(session_path)
    if not path.suffix:
        path = path.with_suffix(".session")

    if not path.exists():
        raise FileNotFoundError("Telethon session not found: {}".format(path))

    conn = sqlite3.connect(str(path))
    try:
        cursor = conn.cursor()

        # Verify this is a Telethon session (has server_address column)
        cursor.execute("PRAGMA table_info(sessions)")
        columns = {row[1] for row in cursor.fetchall()}
        if "server_address" not in columns:
            raise ValueError(
                "Not a Telethon session file (missing server_address column): {}".format(path)
            )

        # Read main session data
        cursor.execute(
            "SELECT dc_id, server_address, port, auth_key FROM sessions"
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("Empty sessions table in: {}".format(path))

        dc_id, server_address, port, auth_key = row

        if auth_key is None or len(auth_key) == 0:
            raise ValueError("Empty auth_key in session: {}".format(path))

        # Try to extract user_id from entities table
        # Telethon stores a self-reference entity with id=0 whose hash = telegram user_id
        # Also stores the full entity with the actual user_id and phone number
        user_id = None
        phone = None

        try:
            # Method 1: entity with id=0 has hash = actual telegram user_id
            cursor.execute("SELECT hash FROM entities WHERE id = 0")
            self_row = cursor.fetchone()
            if self_row and self_row[0]:
                user_id = int(self_row[0])

            # Method 2: entity with a phone number
            cursor.execute(
                "SELECT id, phone FROM entities WHERE phone IS NOT NULL AND phone != 0"
            )
            phone_row = cursor.fetchone()
            if phone_row:
                if user_id is None:
                    user_id = int(phone_row[0])
                phone = str(phone_row[1])
        except sqlite3.Error:
            # entities table might not exist or be empty; that is acceptable
            pass

        return TelethonSessionData(
            dc_id=dc_id,
            server_address=server_address,
            port=port,
            auth_key=auth_key,
            user_id=user_id,
            phone=phone,
        )
    finally:
        conn.close()


def create_pyrogram_session(
    target_path: Path,
    session_data: TelethonSessionData,
    api_id: Optional[int] = None,
    *,
    overwrite: bool = False,
) -> Path:
    """
    Create a Pyrogram .session SQLite file from extracted Telethon session data.

    Args:
        target_path: Where to write the Pyrogram session file.
                     Suffix .session is added if missing.
        session_data: Extracted Telethon session data.
        api_id: Optional Pyrogram api_id to embed in the session.
        overwrite: If True, replace an existing file. Otherwise raise FileExistsError.

    Returns:
        The resolved Path of the created file.

    Raises:
        FileExistsError: If target exists and overwrite=False.
        ValueError: If session_data fails validation.
    """
    session_data.validate()

    path = Path(target_path)
    if not path.suffix:
        path = path.with_suffix(".session")

    if path.exists() and not overwrite:
        raise FileExistsError(
            "Pyrogram session already exists: {}. Pass overwrite=True to replace.".format(path)
        )

    path.parent.mkdir(parents=True, exist_ok=True)

    # Remove existing file if overwriting
    if path.exists():
        path.unlink()

    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(PYROGRAM_SCHEMA)

        conn.execute(
            "INSERT INTO version VALUES (?)",
            (PYROGRAM_SCHEMA_VERSION,),
        )

        now_ts = int(time.time())

        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session_data.dc_id,
                api_id,       # api_id (nullable)
                0,            # test_mode = 0 (production)
                session_data.auth_key,
                now_ts,       # date
                session_data.user_id,
                0,            # is_bot = 0
            ),
        )

        # If we have entity data from Telethon, seed the peers table
        if session_data.user_id is not None:
            conn.execute(
                "INSERT OR IGNORE INTO peers (id, access_hash, type, phone_number) "
                "VALUES (?, ?, ?, ?)",
                (
                    session_data.user_id,
                    0,      # access_hash unknown at conversion time
                    1,      # type=1 (user)
                    session_data.phone,
                ),
            )

        conn.commit()
    finally:
        conn.close()

    return path


def convert_telethon_to_pyrogram(
    telethon_path: Path,
    pyrogram_path: Path,
    api_id: Optional[int] = None,
    *,
    overwrite: bool = False,
) -> ConversionResult:
    """
    High-level: convert a single Telethon .session to a Pyrogram .session.

    Args:
        telethon_path: Path to source Telethon .session file.
        pyrogram_path: Path to target Pyrogram .session file.
        api_id: Optional api_id to embed.
        overwrite: Allow overwriting existing target.

    Returns:
        ConversionResult with success status, paths, and metadata.
    """
    import hashlib

    telethon_resolved = Path(telethon_path)
    if not telethon_resolved.suffix:
        telethon_resolved = telethon_resolved.with_suffix(".session")

    pyrogram_resolved = Path(pyrogram_path)
    if not pyrogram_resolved.suffix:
        pyrogram_resolved = pyrogram_resolved.with_suffix(".session")

    try:
        session_data = read_telethon_session(telethon_resolved)
        session_data.validate()

        created_path = create_pyrogram_session(
            pyrogram_resolved,
            session_data,
            api_id=api_id,
            overwrite=overwrite,
        )

        key_hash = hashlib.sha256(session_data.auth_key).hexdigest()[:8]

        log.info(
            "Converted {} -> {} (dc={}, user_id={}, key={}...)".format(
                telethon_resolved.name,
                created_path.name,
                session_data.dc_id,
                session_data.user_id,
                key_hash,
            )
        )

        return ConversionResult(
            source_path=telethon_resolved,
            target_path=created_path,
            dc_id=session_data.dc_id,
            user_id=session_data.user_id,
            auth_key_sha256_hex=key_hash,
            success=True,
        )

    except Exception as exc:
        log.error(
            "Conversion failed for {}: {}".format(telethon_resolved, exc)
        )
        return ConversionResult(
            source_path=telethon_resolved,
            target_path=pyrogram_resolved,
            dc_id=0,
            user_id=None,
            auth_key_sha256_hex="",
            success=False,
            error=str(exc),
        )


def verify_conversion(
    telethon_path: Path,
    pyrogram_path: Path,
) -> bool:
    """
    Verify that a converted Pyrogram session has the same auth_key as the source.

    Returns True if auth_keys match, False otherwise.
    """
    telethon_resolved = Path(telethon_path)
    if not telethon_resolved.suffix:
        telethon_resolved = telethon_resolved.with_suffix(".session")

    pyrogram_resolved = Path(pyrogram_path)
    if not pyrogram_resolved.suffix:
        pyrogram_resolved = pyrogram_resolved.with_suffix(".session")

    if not telethon_resolved.exists():
        log.error("Telethon session not found: {}".format(telethon_resolved))
        return False

    if not pyrogram_resolved.exists():
        log.error("Pyrogram session not found: {}".format(pyrogram_resolved))
        return False

    telethon_conn = sqlite3.connect(str(telethon_resolved))
    pyrogram_conn = sqlite3.connect(str(pyrogram_resolved))

    try:
        telethon_key = telethon_conn.execute(
            "SELECT auth_key FROM sessions"
        ).fetchone()
        pyrogram_key = pyrogram_conn.execute(
            "SELECT auth_key FROM sessions"
        ).fetchone()

        if telethon_key is None or pyrogram_key is None:
            log.error("Missing auth_key in one of the session files")
            return False

        match = telethon_key[0] == pyrogram_key[0]

        if match:
            log.info(
                "auth_key verified: {} == {} (256 bytes match)".format(
                    telethon_resolved.name, pyrogram_resolved.name
                )
            )
        else:
            log.error(
                "auth_key MISMATCH: {} != {}".format(
                    telethon_resolved.name, pyrogram_resolved.name
                )
            )

        return match

    finally:
        telethon_conn.close()
        pyrogram_conn.close()


# ---------------------------------------------------------------------------
# Batch conversion
# ---------------------------------------------------------------------------
def convert_all_sessions(
    telethon_dir: Path,
    pyrogram_dir: Path,
    api_id: Optional[int] = None,
    *,
    overwrite: bool = False,
    skip_existing: bool = True,
) -> list[ConversionResult]:
    """
    Batch-convert all Telethon .session files in a directory to Pyrogram format.

    Args:
        telethon_dir: Directory containing Telethon .session files.
        pyrogram_dir: Directory where Pyrogram .session files will be written.
        api_id: Optional api_id to embed in each Pyrogram session.
        overwrite: If True, overwrite existing Pyrogram sessions.
        skip_existing: If True (and overwrite=False), silently skip files
                       that already have a Pyrogram counterpart.

    Returns:
        List of ConversionResult for each processed file.
    """
    telethon_dir = Path(telethon_dir)
    pyrogram_dir = Path(pyrogram_dir)

    if not telethon_dir.exists():
        raise FileNotFoundError(
            "Telethon sessions directory not found: {}".format(telethon_dir)
        )

    pyrogram_dir.mkdir(parents=True, exist_ok=True)

    session_files = sorted(telethon_dir.glob("*.session"))

    if not session_files:
        log.warning("No .session files found in {}".format(telethon_dir))
        return []

    log.info(
        "Batch converting {} sessions: {} -> {}".format(
            len(session_files), telethon_dir, pyrogram_dir
        )
    )

    results: list[ConversionResult] = []

    for src_path in session_files:
        target_path = pyrogram_dir / src_path.name

        # Skip if target exists and we are not overwriting
        if target_path.exists() and not overwrite:
            if skip_existing:
                log.debug("Skipping (exists): {}".format(src_path.name))
                continue
            # If not skipping, convert_telethon_to_pyrogram will raise FileExistsError

        result = convert_telethon_to_pyrogram(
            src_path,
            target_path,
            api_id=api_id,
            overwrite=overwrite,
        )
        results.append(result)

    succeeded = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)

    log.info(
        "Batch conversion complete: {} succeeded, {} failed out of {} processed".format(
            succeeded, failed, len(results)
        )
    )

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Convert Telethon .session files to Pyrogram format"
    )
    parser.add_argument(
        "source",
        help="Telethon .session file or directory of .session files",
    )
    parser.add_argument(
        "target",
        help="Pyrogram .session output file or directory",
    )
    parser.add_argument(
        "--api-id",
        type=int,
        default=None,
        help="api_id to embed in Pyrogram session",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing Pyrogram session files",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify auth_key match after conversion",
    )

    args = parser.parse_args()

    source = Path(args.source)
    target = Path(args.target)

    if source.is_dir():
        results = convert_all_sessions(
            source,
            target,
            api_id=args.api_id,
            overwrite=args.overwrite,
        )
        for r in results:
            status = "OK" if r.success else "FAIL: {}".format(r.error)
            print("{} -> {} [{}]".format(r.source_path.name, r.target_path.name, status))

            if args.verify and r.success:
                ok = verify_conversion(r.source_path, r.target_path)
                print("  auth_key verify: {}".format("PASS" if ok else "FAIL"))

    else:
        result = convert_telethon_to_pyrogram(
            source,
            target,
            api_id=args.api_id,
            overwrite=args.overwrite,
        )
        status = "OK" if result.success else "FAIL: {}".format(result.error)
        print("{} -> {} [{}]".format(result.source_path.name, result.target_path.name, status))

        if args.verify and result.success:
            ok = verify_conversion(result.source_path, result.target_path)
            print("auth_key verify: {}".format("PASS" if ok else "FAIL"))

        if not result.success:
            sys.exit(1)
