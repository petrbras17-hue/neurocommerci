"""
Sprint 17: Admin Account Onboarding Service.

Handles: upload (tdata/session+json), verify, harden, and operation logging.
All Telegram operations use human-like delays to avoid detection.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import secrets
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import AdminAccount, AdminOperationLog

logger = logging.getLogger(__name__)

# ── Storage paths ──────────────────────────────────────────────────

STORAGE_ROOT = Path("storage/accounts")


def _sanitize_phone(phone: str) -> str:
    """Strip all characters except digits, +, and - to prevent path traversal."""
    return re.sub(r'[^0-9+\-]', '', phone) or 'unknown'


def _account_dir(workspace_id: int, phone: str) -> Path:
    return STORAGE_ROOT / str(workspace_id) / _sanitize_phone(phone)


def _backup_dir(workspace_id: int, phone: str) -> Path:
    return _account_dir(workspace_id, phone) / "backups"


# ── Operation logging ─────────────────────────────────────────────


async def log_operation(
    db: AsyncSession,
    workspace_id: int,
    module: str,
    action: str,
    status: str,
    detail: str = "",
    account_id: Optional[int] = None,
    proxy_id: Optional[int] = None,
):
    """Write one row to admin_operations_log."""
    entry = AdminOperationLog(
        workspace_id=workspace_id,
        account_id=account_id,
        proxy_id=proxy_id,
        module=module,
        action=action,
        status=status,
        detail=detail,
    )
    db.add(entry)
    await db.flush()
    return entry


# ── Upload helpers ─────────────────────────────────────────────────


async def upload_session_json(
    db: AsyncSession,
    workspace_id: int,
    session_bytes: bytes,
    metadata_dict: dict,
) -> AdminAccount:
    """
    Save a .session + metadata.json pair, create DB record.
    Returns the new AdminAccount.
    """
    phone = metadata_dict.get("phone", "unknown")
    acct_dir = _account_dir(workspace_id, phone)
    acct_dir.mkdir(parents=True, exist_ok=True)

    # Save files
    session_path = acct_dir / f"{phone}.session"
    session_path.write_bytes(session_bytes)

    meta_path = acct_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata_dict, indent=2, ensure_ascii=False))

    # Create DB record
    account = AdminAccount(
        workspace_id=workspace_id,
        phone=phone,
        country=metadata_dict.get("country"),
        display_name=metadata_dict.get("name"),
        api_id=metadata_dict.get("api_id"),
        api_hash=metadata_dict.get("api_hash"),
        dc_id=metadata_dict.get("dc_id"),
        session_path=str(session_path.relative_to(Path.cwd())),
        source="session_json",
        status="uploaded",
        lifecycle_phase="day0",
    )
    db.add(account)
    await db.flush()

    await log_operation(
        db, workspace_id, "onboarding", "upload_session",
        "success", f"Uploaded session for {phone}", account_id=account.id,
    )
    return account


async def upload_tdata(
    db: AsyncSession,
    workspace_id: int,
    tdata_zip_path: str,
) -> AdminAccount:
    """
    Convert tdata ZIP via opentele, save session, create DB record.
    Requires opentele to be installed.
    """
    import tempfile

    extract_dir = tempfile.mkdtemp(prefix="tdata_")
    try:
        with zipfile.ZipFile(tdata_zip_path, "r") as zf:
            # Validate: no path traversal
            for member in zf.namelist():
                member_path = (Path(extract_dir) / member).resolve()
                if not str(member_path).startswith(str(Path(extract_dir).resolve())):
                    raise ValueError(f"Unsafe path in ZIP: {member}")
            # Validate: total uncompressed size <= 100MB
            total_size = sum(info.file_size for info in zf.infolist())
            if total_size > 100 * 1024 * 1024:
                raise ValueError("ZIP too large (>100MB uncompressed)")
            zf.extractall(extract_dir)

        # Find tdata directory
        tdata_dir = None
        for root, dirs, _files in os.walk(extract_dir):
            if "tdata" in dirs:
                tdata_dir = os.path.join(root, "tdata")
                break
        if not tdata_dir:
            raise ValueError("No tdata/ directory found in ZIP")

        # Convert via opentele
        from opentele.td import TDesktop
        from opentele.api import API

        td = TDesktop(tdata_dir)
        if not td.isLoaded():
            raise ValueError("Failed to load tdata — file may be corrupted")

        tg_client = await td.ToTelethon(
            session=os.path.join(extract_dir, "converted.session"),
            flag=td.UseCurrentSession,
            api=API.TelegramDesktop,
        )

        # Extract metadata
        phone = "unknown"
        try:
            await asyncio.wait_for(tg_client.connect(), timeout=30)
            if await tg_client.is_user_authorized():
                me = await tg_client.get_me()
                phone = me.phone or "unknown"
            await tg_client.disconnect()
        except Exception as e:
            logger.warning("tdata verify connect failed: %s", e)
            try:
                await tg_client.disconnect()
            except Exception:
                pass

        # Save to account directory
        acct_dir = _account_dir(workspace_id, phone)
        acct_dir.mkdir(parents=True, exist_ok=True)
        session_dest = acct_dir / f"{phone}.session"
        shutil.copy2(os.path.join(extract_dir, "converted.session"), session_dest)

        metadata_dict = {
            "api_id": API.TelegramDesktop.api_id,
            "api_hash": API.TelegramDesktop.api_hash,
            "phone": phone,
            "source": "tdata",
            "converted_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_path = acct_dir / "metadata.json"
        meta_path.write_text(json.dumps(metadata_dict, indent=2, ensure_ascii=False))

        account = AdminAccount(
            workspace_id=workspace_id,
            phone=phone,
            api_id=API.TelegramDesktop.api_id,
            api_hash=API.TelegramDesktop.api_hash,
            session_path=str(session_dest.relative_to(Path.cwd())),
            source="tdata",
            status="uploaded",
            lifecycle_phase="day0",
        )
        db.add(account)
        await db.flush()

        await log_operation(
            db, workspace_id, "onboarding", "upload_tdata",
            "success", f"Converted tdata for {phone}", account_id=account.id,
        )
        return account
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


async def upload_bulk_zip(
    db: AsyncSession,
    workspace_id: int,
    zip_path: str,
) -> list[AdminAccount]:
    """
    Process a ZIP containing multiple account folders.
    Each subfolder must have phone.session + metadata.json.
    """
    import tempfile

    extract_dir = tempfile.mkdtemp(prefix="bulk_")
    accounts = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Validate: no path traversal
            for member in zf.namelist():
                member_path = (Path(extract_dir) / member).resolve()
                if not str(member_path).startswith(str(Path(extract_dir).resolve())):
                    raise ValueError(f"Unsafe path in ZIP: {member}")
            # Validate: total uncompressed size <= 100MB
            total_size = sum(info.file_size for info in zf.infolist())
            if total_size > 100 * 1024 * 1024:
                raise ValueError("ZIP too large (>100MB uncompressed)")
            zf.extractall(extract_dir)

        for entry in sorted(os.listdir(extract_dir)):
            entry_path = os.path.join(extract_dir, entry)
            if not os.path.isdir(entry_path):
                continue

            # Find .session + metadata.json
            session_file = None
            meta_file = None
            for f in os.listdir(entry_path):
                if f.endswith(".session"):
                    session_file = os.path.join(entry_path, f)
                elif f in ("metadata.json",):
                    meta_file = os.path.join(entry_path, f)

            if session_file and meta_file:
                session_bytes = Path(session_file).read_bytes()
                metadata_dict = json.loads(Path(meta_file).read_text())
                account = await upload_session_json(db, workspace_id, session_bytes, metadata_dict)
                accounts.append(account)
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)

    return accounts


# ── Verify ─────────────────────────────────────────────────────────


async def verify_account(
    db: AsyncSession,
    account: AdminAccount,
    proxy_tuple: Optional[tuple] = None,
) -> dict:
    """
    Connect to Telegram, check authorization, return get_me() info.
    Updates account status to 'verified' on success.
    """
    from telethon import TelegramClient

    session_path = account.session_path
    if not session_path or not os.path.exists(session_path):
        raise FileNotFoundError(f"Session file not found: {session_path}")

    # Load metadata for device fingerprint
    meta_path = Path(session_path).parent / "metadata.json"
    device_kwargs = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        device_kwargs = {
            "device_model": meta.get("device", "Unknown"),
            "system_version": meta.get("sdk", "Unknown"),
            "app_version": meta.get("app_version", "12.4.3"),
            "lang_code": meta.get("lang_pack", "en"),
            "system_lang_code": meta.get("system_lang_pack", "en"),
        }

    client = TelegramClient(
        session_path.replace(".session", ""),
        account.api_id or 2040,
        account.api_hash or "b18441a1ff607e10a989891a5462e627",
        proxy=proxy_tuple,
        timeout=30,
        connection_retries=3,
        retry_delay=5,
        **device_kwargs,
    )

    result = {"authorized": False, "phone": None, "name": None, "username": None}
    try:
        await asyncio.wait_for(client.connect(), timeout=30)
        authorized = await client.is_user_authorized()
        result["authorized"] = authorized

        if authorized:
            me = await client.get_me()
            result["phone"] = me.phone
            result["name"] = f"{me.first_name or ''} {me.last_name or ''}".strip()
            result["username"] = me.username
            result["dc_id"] = me.photo.dc_id if me.photo else None

            # Update DB record
            account.status = "verified"
            account.display_name = result["name"]
            account.username = result["username"]
            if me.phone:
                account.phone = me.phone
            await db.flush()

        await log_operation(
            db, account.workspace_id, "onboarding", "verify",
            "success" if authorized else "error",
            json.dumps(result, ensure_ascii=False),
            account_id=account.id,
        )
    except Exception as e:
        logger.error("Verify failed for account %s: %s", account.phone, e)
        await log_operation(
            db, account.workspace_id, "onboarding", "verify",
            "error", str(e), account_id=account.id,
        )
        result["error"] = str(e)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    return result


# ── Harden ─────────────────────────────────────────────────────────


async def _human_delay(min_sec: float = 2.0, max_sec: float = 8.0):
    """Sleep a random human-like duration."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))


async def harden_account(
    db: AsyncSession,
    account: AdminAccount,
    proxy_tuple: Optional[tuple] = None,
    kill_sessions: bool = True,
    set_2fa: bool = True,
    configure_privacy: bool = True,
) -> dict:
    """
    Security hardening with human-like delays:
    1. Terminate foreign sessions
    2. Set 2FA password
    3. Configure privacy settings

    IMPORTANT: All operations use random delays to appear human.
    """
    from telethon import TelegramClient
    from telethon.tl.functions.account import (
        GetAuthorizationsRequest, ResetAuthorizationRequest,
        SetPrivacyRequest,
    )
    from telethon.tl.types import (
        InputPrivacyKeyPhoneNumber, InputPrivacyKeyStatusTimestamp,
        InputPrivacyKeyProfilePhoto, InputPrivacyKeyForwards,
        InputPrivacyValueDisallowAll, InputPrivacyValueAllowContacts,
    )

    session_path = account.session_path
    if not session_path or not os.path.exists(session_path):
        raise FileNotFoundError(f"Session file not found: {session_path}")

    # Load metadata for device fingerprint
    meta_path = Path(session_path).parent / "metadata.json"
    device_kwargs = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        device_kwargs = {
            "device_model": meta.get("device", "Unknown"),
            "system_version": meta.get("sdk", "Unknown"),
            "app_version": meta.get("app_version", "12.4.3"),
            "lang_code": meta.get("lang_pack", "en"),
            "system_lang_code": meta.get("system_lang_pack", "en"),
        }

    client = TelegramClient(
        session_path.replace(".session", ""),
        account.api_id or 2040,
        account.api_hash or "b18441a1ff607e10a989891a5462e627",
        proxy=proxy_tuple,
        timeout=30,
        connection_retries=3,
        retry_delay=5,
        **device_kwargs,
    )

    result = {"sessions_terminated": 0, "two_fa_set": False, "privacy_configured": False}
    try:
        await asyncio.wait_for(client.connect(), timeout=30)
        if not await client.is_user_authorized():
            raise RuntimeError("Account not authorized — cannot harden")

        # Step 1: Kill foreign sessions
        if kill_sessions:
            await _human_delay(3, 8)  # Wait before sensitive action
            auths = await client(GetAuthorizationsRequest())
            for s in auths.authorizations:
                if not s.current:
                    await client(ResetAuthorizationRequest(hash=s.hash))
                    result["sessions_terminated"] += 1
                    await _human_delay(2, 5)  # Human-like delay between kills

            await log_operation(
                db, account.workspace_id, "security", "kill_sessions",
                "success", f"Terminated {result['sessions_terminated']} sessions",
                account_id=account.id,
            )

        # Step 2: Set 2FA
        if set_2fa and not account.two_fa_password:
            await _human_delay(5, 15)  # Longer pause before 2FA
            password = secrets.token_hex(8)
            hint = f"nc-{account.phone[-4:]}"
            try:
                await client.edit_2fa(new_password=password, hint=hint)
                # TODO: encrypt 2FA password at rest instead of storing plaintext
                account.two_fa_password = password
                result["two_fa_set"] = True

                # Update metadata.json
                if meta_path.exists():
                    meta_data = json.loads(meta_path.read_text())
                    meta_data["twoFA"] = password
                    meta_data["twoFA_hint"] = hint
                    meta_path.write_text(json.dumps(meta_data, indent=2, ensure_ascii=False))

                await log_operation(
                    db, account.workspace_id, "security", "set_2fa",
                    "success", f"2FA set with hint: {hint}",
                    account_id=account.id,
                )
            except Exception as e:
                logger.warning("2FA set failed (may already be set): %s", e)
                await log_operation(
                    db, account.workspace_id, "security", "set_2fa",
                    "error", str(e), account_id=account.id,
                )

        # Step 3: Privacy settings
        if configure_privacy:
            await _human_delay(5, 12)

            privacy_rules = [
                (InputPrivacyKeyPhoneNumber(), [InputPrivacyValueDisallowAll()], "phone_number"),
                (InputPrivacyKeyStatusTimestamp(), [InputPrivacyValueAllowContacts()], "last_seen"),
                (InputPrivacyKeyProfilePhoto(), [InputPrivacyValueAllowContacts()], "profile_photo"),
                (InputPrivacyKeyForwards(), [InputPrivacyValueDisallowAll()], "forwards"),
            ]
            for key, rules, name in privacy_rules:
                try:
                    await client(SetPrivacyRequest(key, rules))
                    await _human_delay(2, 6)
                except Exception as e:
                    logger.warning("Privacy %s failed: %s", name, e)

            result["privacy_configured"] = True
            await log_operation(
                db, account.workspace_id, "security", "configure_privacy",
                "success", "Privacy configured",
                account_id=account.id,
            )

        # Update account status
        now = datetime.now(timezone.utc)
        account.status = "hardened"
        account.lifecycle_phase = "day0_security_done"
        account.security_hardened_at = now
        account.profile_change_earliest = now + timedelta(hours=48)
        await db.flush()

        # Backup session after hardening
        backup_dir = _backup_dir(account.workspace_id, account.phone)
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = now.strftime("%Y%m%d_%H%M%S")
        backup_name = f"{ts}_post_security.session"
        shutil.copy2(session_path, backup_dir / backup_name)

        await log_operation(
            db, account.workspace_id, "onboarding", "harden",
            "success", json.dumps(result, ensure_ascii=False),
            account_id=account.id,
        )
    except Exception as e:
        logger.error("Harden failed for %s: %s", account.phone, e)
        await log_operation(
            db, account.workspace_id, "onboarding", "harden",
            "error", str(e), account_id=account.id,
        )
        result["error"] = str(e)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    return result


# ── Account retrieval ──────────────────────────────────────────────


async def get_account(db: AsyncSession, account_id: int, workspace_id: Optional[int] = None) -> Optional[AdminAccount]:
    """Get single admin account by ID, optionally filtered by workspace for tenant safety."""
    q = select(AdminAccount).where(AdminAccount.id == account_id)
    if workspace_id is not None:
        q = q.where(AdminAccount.workspace_id == workspace_id)
    result = await db.execute(q)
    return result.scalar_one_or_none()


async def list_accounts(
    db: AsyncSession,
    workspace_id: int,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AdminAccount]:
    """List admin accounts with optional status filter."""
    q = select(AdminAccount).where(AdminAccount.workspace_id == workspace_id)
    if status:
        q = q.where(AdminAccount.status == status)
    q = q.order_by(AdminAccount.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return list(result.scalars().all())


async def delete_account(db: AsyncSession, account: AdminAccount) -> bool:
    """Remove account record (does NOT delete session files)."""
    await log_operation(
        db, account.workspace_id, "onboarding", "delete",
        "success", f"Deleted account {account.phone}",
        account_id=account.id,
    )
    await db.delete(account)
    await db.flush()
    return True
