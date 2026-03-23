"""
Tests for core/session_converter.py

Verifies Telethon -> Pyrogram session conversion logic using
temporary SQLite files. No real Telegram connections are made.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from core.session_converter import (
    AUTH_KEY_LENGTH,
    PYROGRAM_SCHEMA_VERSION,
    ConversionResult,
    TelethonSessionData,
    convert_all_sessions,
    convert_telethon_to_pyrogram,
    create_pyrogram_session,
    read_telethon_session,
    verify_conversion,
)


# ---------------------------------------------------------------------------
# Helpers: create fake Telethon session files
# ---------------------------------------------------------------------------
FAKE_AUTH_KEY = os.urandom(AUTH_KEY_LENGTH)
FAKE_DC_ID = 2
FAKE_SERVER = "149.154.167.51"
FAKE_PORT = 443
FAKE_USER_ID = 123456789
FAKE_PHONE = 79001234567


def _create_telethon_session(path: Path, *, with_entities: bool = True) -> Path:
    """Create a minimal valid Telethon .session SQLite file."""
    path = Path(path)
    if not path.suffix:
        path = path.with_suffix(".session")

    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE version (version INTEGER PRIMARY KEY);
        CREATE TABLE sessions (
            dc_id INTEGER PRIMARY KEY,
            server_address TEXT,
            port INTEGER,
            auth_key BLOB,
            takeout_id INTEGER
        );
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY,
            hash INTEGER NOT NULL,
            username TEXT,
            phone INTEGER,
            name TEXT,
            date INTEGER
        );
        CREATE TABLE sent_files (
            md5_digest BLOB,
            file_size INTEGER,
            type INTEGER,
            id INTEGER,
            hash INTEGER
        );
        CREATE TABLE update_state (
            id INTEGER PRIMARY KEY,
            pts INTEGER,
            qts INTEGER,
            date INTEGER,
            seq INTEGER
        );
    """)

    conn.execute("INSERT INTO version VALUES (7)")
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        (FAKE_DC_ID, FAKE_SERVER, FAKE_PORT, FAKE_AUTH_KEY, None),
    )

    if with_entities:
        # Self-reference entity (id=0, hash=user_id)
        conn.execute(
            "INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?)",
            (0, FAKE_USER_ID, None, None, None, 1700000000),
        )
        # Full entity with phone
        conn.execute(
            "INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?)",
            (FAKE_USER_ID, 999888777, None, FAKE_PHONE, "Test User", 1700000000),
        )

    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# Tests: read_telethon_session
# ---------------------------------------------------------------------------
class TestReadTelethonSession:
    def test_reads_session_data(self, tmp_path):
        src = _create_telethon_session(tmp_path / "test.session")
        data = read_telethon_session(src)

        assert data.dc_id == FAKE_DC_ID
        assert data.server_address == FAKE_SERVER
        assert data.port == FAKE_PORT
        assert data.auth_key == FAKE_AUTH_KEY
        assert len(data.auth_key) == AUTH_KEY_LENGTH

    def test_extracts_user_id(self, tmp_path):
        src = _create_telethon_session(tmp_path / "test.session")
        data = read_telethon_session(src)

        assert data.user_id == FAKE_USER_ID

    def test_extracts_phone(self, tmp_path):
        src = _create_telethon_session(tmp_path / "test.session")
        data = read_telethon_session(src)

        assert data.phone == str(FAKE_PHONE)

    def test_works_without_entities(self, tmp_path):
        src = _create_telethon_session(tmp_path / "test.session", with_entities=False)
        data = read_telethon_session(src)

        assert data.auth_key == FAKE_AUTH_KEY
        assert data.user_id is None
        assert data.phone is None

    def test_adds_session_suffix(self, tmp_path):
        src = _create_telethon_session(tmp_path / "test.session")
        # Pass without .session suffix
        data = read_telethon_session(tmp_path / "test")

        assert data.auth_key == FAKE_AUTH_KEY

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_telethon_session(tmp_path / "nonexistent.session")

    def test_raises_on_non_telethon_db(self, tmp_path):
        """A random SQLite file that is not a Telethon session."""
        fake_db = tmp_path / "not_telethon.session"
        conn = sqlite3.connect(str(fake_db))
        conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, data TEXT)")
        conn.execute("INSERT INTO sessions VALUES (1, 'hello')")
        conn.commit()
        conn.close()

        with pytest.raises(ValueError, match="Not a Telethon session"):
            read_telethon_session(fake_db)


# ---------------------------------------------------------------------------
# Tests: TelethonSessionData.validate
# ---------------------------------------------------------------------------
class TestValidation:
    def test_valid_data_passes(self):
        data = TelethonSessionData(
            dc_id=2,
            server_address="149.154.167.51",
            port=443,
            auth_key=FAKE_AUTH_KEY,
        )
        data.validate()  # should not raise

    def test_empty_auth_key_fails(self):
        data = TelethonSessionData(
            dc_id=2, server_address="x", port=443, auth_key=b""
        )
        with pytest.raises(ValueError, match="auth_key is empty"):
            data.validate()

    def test_wrong_length_auth_key_fails(self):
        data = TelethonSessionData(
            dc_id=2, server_address="x", port=443, auth_key=b"\x00" * 128
        )
        with pytest.raises(ValueError, match="256 bytes"):
            data.validate()

    def test_invalid_dc_id_fails(self):
        data = TelethonSessionData(
            dc_id=99, server_address="x", port=443, auth_key=FAKE_AUTH_KEY
        )
        with pytest.raises(ValueError, match="dc_id must be 1-5"):
            data.validate()


# ---------------------------------------------------------------------------
# Tests: create_pyrogram_session
# ---------------------------------------------------------------------------
class TestCreatePyrogramSession:
    def test_creates_valid_pyrogram_db(self, tmp_path):
        data = TelethonSessionData(
            dc_id=FAKE_DC_ID,
            server_address=FAKE_SERVER,
            port=FAKE_PORT,
            auth_key=FAKE_AUTH_KEY,
            user_id=FAKE_USER_ID,
            phone=str(FAKE_PHONE),
        )

        out = create_pyrogram_session(tmp_path / "pyro.session", data)
        assert out.exists()

        conn = sqlite3.connect(str(out))

        # Check version
        version = conn.execute("SELECT number FROM version").fetchone()[0]
        assert version == PYROGRAM_SCHEMA_VERSION

        # Check sessions
        row = conn.execute(
            "SELECT dc_id, test_mode, auth_key, user_id, is_bot FROM sessions"
        ).fetchone()
        assert row[0] == FAKE_DC_ID
        assert row[1] == 0  # test_mode = production
        assert row[2] == FAKE_AUTH_KEY
        assert row[3] == FAKE_USER_ID
        assert row[4] == 0  # is_bot

        # Check peers
        peer = conn.execute(
            "SELECT id, type, phone_number FROM peers"
        ).fetchone()
        assert peer[0] == FAKE_USER_ID
        assert peer[1] == 1  # user type
        assert peer[2] == str(FAKE_PHONE)

        conn.close()

    def test_no_overwrite_by_default(self, tmp_path):
        data = TelethonSessionData(
            dc_id=2, server_address="x", port=443, auth_key=FAKE_AUTH_KEY
        )
        create_pyrogram_session(tmp_path / "pyro.session", data)

        with pytest.raises(FileExistsError):
            create_pyrogram_session(tmp_path / "pyro.session", data)

    def test_overwrite_allowed(self, tmp_path):
        data = TelethonSessionData(
            dc_id=2, server_address="x", port=443, auth_key=FAKE_AUTH_KEY
        )
        create_pyrogram_session(tmp_path / "pyro.session", data)
        create_pyrogram_session(
            tmp_path / "pyro.session", data, overwrite=True
        )  # should not raise

    def test_embeds_api_id(self, tmp_path):
        data = TelethonSessionData(
            dc_id=2, server_address="x", port=443, auth_key=FAKE_AUTH_KEY
        )
        out = create_pyrogram_session(tmp_path / "pyro.session", data, api_id=21724)

        conn = sqlite3.connect(str(out))
        api_id = conn.execute("SELECT api_id FROM sessions").fetchone()[0]
        assert api_id == 21724
        conn.close()


# ---------------------------------------------------------------------------
# Tests: convert_telethon_to_pyrogram (high-level)
# ---------------------------------------------------------------------------
class TestConvertTelethonToPyrogram:
    def test_full_conversion(self, tmp_path):
        src = _create_telethon_session(tmp_path / "source.session")
        dst = tmp_path / "pyrogram" / "dest.session"

        result = convert_telethon_to_pyrogram(src, dst)

        assert result.success is True
        assert result.error is None
        assert result.dc_id == FAKE_DC_ID
        assert result.user_id == FAKE_USER_ID
        assert len(result.auth_key_sha256_hex) == 8
        assert dst.exists()

    def test_returns_failure_on_missing_source(self, tmp_path):
        result = convert_telethon_to_pyrogram(
            tmp_path / "missing.session",
            tmp_path / "out.session",
        )
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_returns_failure_on_existing_target(self, tmp_path):
        src = _create_telethon_session(tmp_path / "source.session")
        dst = tmp_path / "dest.session"
        dst.touch()

        result = convert_telethon_to_pyrogram(src, dst)
        assert result.success is False
        assert "exists" in result.error.lower()


# ---------------------------------------------------------------------------
# Tests: verify_conversion
# ---------------------------------------------------------------------------
class TestVerifyConversion:
    def test_matching_keys(self, tmp_path):
        src = _create_telethon_session(tmp_path / "source.session")
        dst = tmp_path / "dest.session"

        convert_telethon_to_pyrogram(src, dst)

        assert verify_conversion(src, dst) is True

    def test_mismatched_keys(self, tmp_path):
        src = _create_telethon_session(tmp_path / "source.session")

        # Create a pyrogram session with a different auth_key
        different_data = TelethonSessionData(
            dc_id=2,
            server_address="x",
            port=443,
            auth_key=os.urandom(AUTH_KEY_LENGTH),
        )
        dst = tmp_path / "dest.session"
        create_pyrogram_session(dst, different_data)

        assert verify_conversion(src, dst) is False

    def test_missing_source(self, tmp_path):
        assert verify_conversion(
            tmp_path / "missing.session",
            tmp_path / "dest.session",
        ) is False


# ---------------------------------------------------------------------------
# Tests: convert_all_sessions (batch)
# ---------------------------------------------------------------------------
class TestConvertAllSessions:
    def test_batch_conversion(self, tmp_path):
        src_dir = tmp_path / "telethon"
        src_dir.mkdir()
        dst_dir = tmp_path / "pyrogram"

        _create_telethon_session(src_dir / "acc1.session")
        _create_telethon_session(src_dir / "acc2.session")
        _create_telethon_session(src_dir / "acc3.session")

        results = convert_all_sessions(src_dir, dst_dir)

        assert len(results) == 3
        assert all(r.success for r in results)
        assert (dst_dir / "acc1.session").exists()
        assert (dst_dir / "acc2.session").exists()
        assert (dst_dir / "acc3.session").exists()

    def test_skips_existing_by_default(self, tmp_path):
        src_dir = tmp_path / "telethon"
        src_dir.mkdir()
        dst_dir = tmp_path / "pyrogram"
        dst_dir.mkdir()

        _create_telethon_session(src_dir / "acc1.session")

        # Pre-create the target
        (dst_dir / "acc1.session").touch()

        results = convert_all_sessions(src_dir, dst_dir)

        # Should skip the existing file
        assert len(results) == 0

    def test_overwrite_existing(self, tmp_path):
        src_dir = tmp_path / "telethon"
        src_dir.mkdir()
        dst_dir = tmp_path / "pyrogram"
        dst_dir.mkdir()

        _create_telethon_session(src_dir / "acc1.session")
        (dst_dir / "acc1.session").touch()

        results = convert_all_sessions(src_dir, dst_dir, overwrite=True)

        assert len(results) == 1
        assert results[0].success is True

    def test_empty_directory(self, tmp_path):
        src_dir = tmp_path / "empty"
        src_dir.mkdir()
        dst_dir = tmp_path / "pyrogram"

        results = convert_all_sessions(src_dir, dst_dir)
        assert results == []

    def test_missing_source_dir(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            convert_all_sessions(tmp_path / "nonexistent", tmp_path / "out")


# ---------------------------------------------------------------------------
# Tests: real session files (integration — only if available)
# ---------------------------------------------------------------------------
class TestRealSessionIntegration:
    """Run against actual Telethon sessions on disk. Skipped if none found."""

    REAL_SESSION = Path(
        "/Users/braslavskii/NEURO COMMENTING/data/sessions/1/79637428613.session"
    )

    @pytest.mark.skipif(
        not REAL_SESSION.exists(),
        reason="Real session file not available",
    )
    def test_read_real_session(self):
        data = read_telethon_session(self.REAL_SESSION)

        assert data.dc_id in range(1, 6)
        assert len(data.auth_key) == AUTH_KEY_LENGTH
        assert data.user_id is not None

    @pytest.mark.skipif(
        not REAL_SESSION.exists(),
        reason="Real session file not available",
    )
    def test_convert_real_session(self, tmp_path):
        dst = tmp_path / "converted.session"

        result = convert_telethon_to_pyrogram(self.REAL_SESSION, dst)

        assert result.success is True
        assert result.dc_id in range(1, 6)
        assert result.user_id is not None

        # Verify auth_key match
        assert verify_conversion(self.REAL_SESSION, dst) is True

        # Verify Pyrogram schema structure
        conn = sqlite3.connect(str(dst))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "sessions" in tables
        assert "peers" in tables
        assert "version" in tables

        version = conn.execute("SELECT number FROM version").fetchone()[0]
        assert version == PYROGRAM_SCHEMA_VERSION

        conn.close()
