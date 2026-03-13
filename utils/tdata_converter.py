"""Convert TData (Telegram Desktop) folders/archives into Telethon .session + .json pairs.

Uses opentele library to read TData auth keys and produce Telethon-compatible session files.
Supports both raw TData directories and ZIP archives containing TData.

TData format structure:
    tdata/
        key_data          — master encryption key
        D877F783D5D3EF8C/  — account folder (MD5-based name)
            map0 or map1  — account map
            ...

Output: standard .session + .json pairs compatible with the existing upload pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, List, Optional

_log = logging.getLogger(__name__)

# TData marker files — if any of these exist, the directory is likely TData
_TDATA_MARKERS = {"key_data", "key_datas"}

# Default API credentials for TDesktop-originated sessions
_TDESKTOP_API_ID = 2040
_TDESKTOP_API_HASH = "b18441a1ff607e10a989891a5462e627"

# Default device fingerprint for converted TData accounts
_TDESKTOP_DEVICE = "Telegram Desktop"
_TDESKTOP_SDK = "Windows 10"
_TDESKTOP_APP_VERSION = "4.16.8"
_TDESKTOP_LANG_PACK = "ru"
_TDESKTOP_SYSTEM_LANG_PACK = "ru-ru"


@dataclass
class ConvertedAccount:
    """Result of a single TData account conversion."""

    phone: str
    session_bytes: bytes
    metadata: dict[str, Any]
    source: str  # "tdata"


@dataclass
class TDataConversionResult:
    """Result of converting a TData archive/folder."""

    accounts: List[ConvertedAccount]
    errors: List[str]


def is_tdata_directory(path: Path) -> bool:
    """Check if a directory looks like a TData folder."""
    if not path.is_dir():
        return False
    # Direct tdata/ folder
    if any((path / marker).exists() for marker in _TDATA_MARKERS):
        return True
    # Parent folder containing tdata/ subfolder
    tdata_sub = path / "tdata"
    if tdata_sub.is_dir() and any((tdata_sub / marker).exists() for marker in _TDATA_MARKERS):
        return True
    return False


def is_tdata_zip(data: bytes) -> bool:
    """Check if a ZIP archive contains TData structure."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = zf.namelist()
            for name in names:
                # Look for key_data anywhere in the archive
                basename = Path(name).name
                if basename in _TDATA_MARKERS:
                    return True
    except (zipfile.BadZipFile, Exception):
        return False
    return False


def _find_tdata_root(base: Path) -> Optional[Path]:
    """Find the actual tdata directory within an extracted archive."""
    # Direct: base itself has key_data
    if any((base / marker).exists() for marker in _TDATA_MARKERS):
        return base
    # One level down: base/tdata/key_data
    for child in base.iterdir():
        if child.is_dir():
            if any((child / marker).exists() for marker in _TDATA_MARKERS):
                return child
            # Two levels: base/SomeFolder/tdata/key_data
            for grandchild in child.iterdir():
                if grandchild.is_dir() and any(
                    (grandchild / marker).exists() for marker in _TDATA_MARKERS
                ):
                    return grandchild
    return None


def convert_tdata_directory(
    tdata_path: Path,
    output_dir: Path,
    *,
    passcode: str = "",
) -> TDataConversionResult:
    """Convert a TData directory into .session + .json pairs.

    Args:
        tdata_path: Path to the tdata/ directory (must contain key_data).
        output_dir: Where to write the resulting .session and .json files.
        passcode: TData local passcode if set (usually empty).

    Returns:
        TDataConversionResult with converted accounts and any errors.
    """
    try:
        from opentele.td import TDesktop
        from opentele.api import API
    except ImportError as exc:
        return TDataConversionResult(accounts=[], errors=[f"opentele not installed: {exc}"])

    actual_root = _find_tdata_root(tdata_path)
    if actual_root is None:
        return TDataConversionResult(
            accounts=[],
            errors=[f"no key_data found in {tdata_path}"],
        )

    accounts: List[ConvertedAccount] = []
    errors: List[str] = []

    try:
        tdesk = TDesktop(str(actual_root), passcode=passcode or None)
        if not tdesk.isLoaded():
            return TDataConversionResult(accounts=[], errors=["TData failed to load (bad passcode or corrupt data)"])
    except Exception as exc:
        return TDataConversionResult(accounts=[], errors=[f"TData load error: {exc}"])

    loaded_accounts = tdesk.accounts or []
    if not loaded_accounts:
        return TDataConversionResult(accounts=[], errors=["no accounts found in TData"])

    _log.info("TData loaded: %d account(s) found", len(loaded_accounts))

    for idx, td_account in enumerate(loaded_accounts):
        try:
            auth_key = td_account.authKey
            if not auth_key:
                errors.append(f"account[{idx}]: no auth_key")
                continue

            dc_id = td_account.MainDcId if hasattr(td_account, "MainDcId") else 2
            user_id = td_account.UserId if hasattr(td_account, "UserId") else 0

            # Build Telethon session file via opentele's conversion
            # Use a temp file for the .session output
            with tempfile.NamedTemporaryFile(suffix=".session", delete=False) as tmp:
                tmp_session_path = tmp.name

            # opentele ToTelethon writes the session file
            client = tdesk.ToTelethon(
                session=tmp_session_path,
                flag=__import__("opentele.api", fromlist=["UseCurrentSession"]).UseCurrentSession,
                api=API.TelegramDesktop,
            )
            # client is not connected — we just need the session file it created
            # Read the session bytes
            session_path = Path(tmp_session_path)
            if not session_path.exists():
                # Try with .session extension appended
                session_path = Path(tmp_session_path + ".session") if not tmp_session_path.endswith(".session") else session_path

            if not session_path.exists():
                errors.append(f"account[{idx}]: session file not created")
                continue

            session_bytes = session_path.read_bytes()

            # Try to extract phone from the session's cached entities
            phone = read_phone_from_session_sqlite(session_path) or ""
            if not phone and user_id:
                phone = str(user_id)

            # Build synthetic JSON metadata
            first_name = ""
            if hasattr(td_account, "firstName"):
                first_name = str(td_account.firstName or "")

            metadata = {
                "app_id": _TDESKTOP_API_ID,
                "app_hash": _TDESKTOP_API_HASH,
                "device": _TDESKTOP_DEVICE,
                "sdk": _TDESKTOP_SDK,
                "app_version": _TDESKTOP_APP_VERSION,
                "lang_pack": _TDESKTOP_LANG_PACK,
                "system_lang_pack": _TDESKTOP_SYSTEM_LANG_PACK,
                "first_name": first_name or "User",
                "source": "tdata",
                "dc_id": dc_id,
            }

            if phone:
                metadata["phone"] = phone

            accounts.append(ConvertedAccount(
                phone=phone,
                session_bytes=session_bytes,
                metadata=metadata,
                source="tdata",
            ))

            _log.info("TData account[%d] converted: user_id=%s, dc=%d", idx, user_id, dc_id)

            # Clean up temp file
            session_path.unlink(missing_ok=True)

        except Exception as exc:
            errors.append(f"account[{idx}]: conversion error: {exc}")
            _log.warning("TData account[%d] conversion failed: %s", idx, exc)

    return TDataConversionResult(accounts=accounts, errors=errors)


def convert_tdata_zip(
    zip_data: bytes,
    output_dir: Path,
    *,
    passcode: str = "",
) -> TDataConversionResult:
    """Extract a TData ZIP archive and convert all accounts inside.

    Args:
        zip_data: Raw bytes of the ZIP file.
        output_dir: Where to write the resulting .session and .json files.
        passcode: TData local passcode if set.

    Returns:
        TDataConversionResult with converted accounts and any errors.
    """
    with tempfile.TemporaryDirectory(prefix="tdata_") as tmpdir:
        tmp_path = Path(tmpdir)
        try:
            with zipfile.ZipFile(BytesIO(zip_data)) as zf:
                # Zip-slip protection: reject entries that escape the target dir
                for member in zf.infolist():
                    target = (tmp_path / member.filename).resolve()
                    if not str(target).startswith(str(tmp_path.resolve())):
                        return TDataConversionResult(
                            accounts=[],
                            errors=[f"zip-slip detected: {member.filename}"],
                        )
                zf.extractall(tmp_path)
        except zipfile.BadZipFile:
            return TDataConversionResult(accounts=[], errors=["invalid ZIP archive"])

        return convert_tdata_directory(tmp_path, output_dir, passcode=passcode)


def generate_tdata_metadata_json(
    phone: str,
    *,
    first_name: str = "User",
    api_id: int = _TDESKTOP_API_ID,
    api_hash: str = _TDESKTOP_API_HASH,
) -> dict[str, Any]:
    """Generate a standard JSON metadata file for a TData-converted account."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return {
        "phone": phone,
        "app_id": api_id,
        "app_hash": api_hash,
        "device": _TDESKTOP_DEVICE,
        "sdk": _TDESKTOP_SDK,
        "app_version": _TDESKTOP_APP_VERSION,
        "lang_pack": _TDESKTOP_LANG_PACK,
        "system_lang_pack": _TDESKTOP_SYSTEM_LANG_PACK,
        "first_name": first_name,
        "session_file": f"{digits}.session",
        "source": "tdata",
    }


# ---------------------------------------------------------------------------
# Phone discovery — connect via Telethon to get_me().phone
# ---------------------------------------------------------------------------


async def discover_phone_from_session(
    session_path: Path,
    metadata: dict[str, Any],
    *,
    proxy: Optional[tuple] = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Connect to Telegram using a .session file and discover the account's phone number.

    This is needed for TData accounts where the phone is not stored in the session metadata.
    IMPORTANT: Does NOT call send_code_request — only connects and calls get_me().

    Args:
        session_path: Path to the .session file (without extension).
        metadata: JSON metadata dict with app_id, app_hash, device info.
        proxy: Optional Telethon proxy tuple (type, host, port, rdns, user, pass).
        timeout: Connection timeout in seconds.

    Returns:
        Dict with phone, first_name, user_id, authorized status, and any error.
    """
    try:
        from telethon import TelegramClient
    except ImportError:
        return {"ok": False, "error": "telethon not installed"}

    session_str = str(session_path).replace(".session", "") if str(session_path).endswith(".session") else str(session_path)

    client = TelegramClient(
        session_str,
        api_id=int(metadata.get("app_id") or _TDESKTOP_API_ID),
        api_hash=str(metadata.get("app_hash") or _TDESKTOP_API_HASH),
        proxy=proxy,
        device_model=str(metadata.get("device") or _TDESKTOP_DEVICE),
        system_version=str(metadata.get("sdk") or _TDESKTOP_SDK),
        app_version=str(metadata.get("app_version") or _TDESKTOP_APP_VERSION),
        lang_code=str(metadata.get("lang_pack") or _TDESKTOP_LANG_PACK),
        system_lang_code=str(metadata.get("system_lang_pack") or _TDESKTOP_SYSTEM_LANG_PACK),
        timeout=timeout,
        connection_retries=3,
        retry_delay=3,
    )

    try:
        await client.connect()

        if not await client.is_user_authorized():
            return {
                "ok": False,
                "error": "not_authorized",
                "authorized": False,
            }

        me = await client.get_me()
        if me is None:
            return {"ok": False, "error": "get_me_returned_none", "authorized": True}

        phone = str(me.phone or "")
        first_name = str(me.first_name or "")
        last_name = str(me.last_name or "")
        user_id = int(me.id)

        return {
            "ok": True,
            "phone": phone,
            "first_name": first_name,
            "last_name": last_name,
            "user_id": user_id,
            "authorized": True,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def read_phone_from_session_sqlite(session_path: Path) -> Optional[str]:
    """Try to read the phone directly from the Telethon SQLite .session file.

    Telethon stores sessions in SQLite with a 'sessions' table that has
    dc_id, server_address, port, auth_key columns. But no phone column by default.
    However, after a successful connection, the 'Entity' cache may contain the user's phone.

    Returns the phone string if found, None otherwise.
    """
    db_path = session_path if session_path.suffix == ".session" else Path(str(session_path) + ".session")
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        # Check if entities table exists and has our self-user
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entities'")
        if not cursor.fetchone():
            conn.close()
            return None
        # Look for phone in entities — Telethon caches User entities with phone
        cursor.execute("SELECT phone FROM entities WHERE phone IS NOT NULL AND phone != '' LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return None
