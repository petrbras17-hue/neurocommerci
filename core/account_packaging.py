"""
Sprint 18: Account Packaging Service.

Handles: AI profile generation, avatar management, Telegram channel creation,
and profile application with 48h guard for freshly connected accounts.
All Telegram operations use human-like delays.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import AdminAccount, AdminOperationLog

logger = logging.getLogger(__name__)

# ── Storage paths ──────────────────────────────────────────────────

AVATAR_ROOT = Path("storage/avatars")


def _sanitize_phone(phone: str) -> str:
    """Strip all characters except digits, +, and - to prevent path traversal."""
    return re.sub(r'[^0-9+\-]', '', phone) or 'unknown'


def _avatar_dir(workspace_id: int) -> Path:
    return AVATAR_ROOT / str(workspace_id)


# ── Human delay ────────────────────────────────────────────────────


async def _human_delay(min_sec: float = 2.0, max_sec: float = 5.0):
    """Sleep a random human-like duration."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))


# ── Operation logging ──────────────────────────────────────────────


async def log_packaging_op(
    session: AsyncSession,
    workspace_id: int,
    account_id: int,
    action: str,
    status: str,
    detail: str = "",
):
    """Write one row to admin_operations_log for packaging module."""
    entry = AdminOperationLog(
        workspace_id=workspace_id,
        account_id=account_id,
        module="packaging",
        action=action,
        status=status,
        detail=detail,
    )
    session.add(entry)
    await session.flush()
    return entry


# ── Helpers ────────────────────────────────────────────────────────


async def _get_account(session: AsyncSession, account_id: int) -> Optional[AdminAccount]:
    result = await session.execute(
        select(AdminAccount).where(AdminAccount.id == account_id)
    )
    return result.scalar_one_or_none()


def _check_48h_guard(account: AdminAccount) -> Optional[str]:
    """Return error message if profile changes are blocked by 48h guard, else None."""
    if account.profile_change_earliest:
        now = datetime.now(timezone.utc)
        earliest = account.profile_change_earliest
        # Ensure timezone-aware comparison
        if earliest.tzinfo is None:
            earliest = earliest.replace(tzinfo=timezone.utc)
        if now < earliest:
            remaining = earliest - now
            hours = remaining.total_seconds() / 3600
            return (
                f"Profile changes blocked until {account.profile_change_earliest.isoformat()}. "
                f"~{hours:.1f}h remaining."
            )
    return None


def _build_proxy_tuple(account: AdminAccount, proxy=None) -> Optional[tuple]:
    """Build proxy tuple for Telethon client if proxy is bound."""
    if proxy is None:
        return None
    ptype = proxy.proxy_type or "socks5"
    type_map = {"socks5": 2, "socks4": 1, "http": 3}
    return (type_map.get(ptype, 2), proxy.host, proxy.port, True, proxy.username, proxy.password)


def _update_packaging_status(account: AdminAccount):
    """Recompute packaging_status based on current state."""
    if account.channel_id and account.profile_applied_at:
        account.packaging_status = "fully_packaged"
    elif account.channel_id:
        account.packaging_status = "channel_created"
    elif account.avatar_path and account.profile_applied_at:
        account.packaging_status = "avatar_set"
    elif account.profile_first_name or account.profile_bio:
        account.packaging_status = "profile_generated"
    else:
        account.packaging_status = "not_started"


# ── Generate profile via AI ────────────────────────────────────────


async def generate_profile(
    session: AsyncSession,
    workspace_id: int,
    account_id: int,
    params: dict,
    tenant_id: int = 0,
) -> dict:
    """
    Generate AI profile for an account.

    params: {gender, country, age_range, profession}
    Returns generated profile dict and saves to DB.
    """
    account = await _get_account(session, account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")
    if account.workspace_id != workspace_id:
        raise ValueError("Account does not belong to this workspace")

    gender = params.get("gender", "female")
    country = params.get("country", "RU")
    age_range = params.get("age_range", "25-35")
    profession = params.get("profession", "marketing")

    system_instruction = (
        "You are a profile generator for Telegram accounts. "
        "Generate a realistic, natural-sounding profile for a Telegram user. "
        "Return a valid JSON object with fields: first_name, last_name, username, bio. "
        "The profile should match the given demographics. "
        "Bio should be 1-2 sentences in Russian, natural and not spammy. "
        "Username should be lowercase, realistic, 8-15 chars."
    )
    prompt = (
        f"Generate a Telegram profile for a {gender} person from {country}, "
        f"age range {age_range}, working in {profession}. "
        f"Return JSON: {{\"first_name\": ..., \"last_name\": ..., \"username\": ..., \"bio\": ...}}"
    )

    try:
        from core.ai_router import route_ai_task

        result = await route_ai_task(
            session,
            task_type="profile_generation",
            prompt=prompt,
            system_instruction=system_instruction,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            max_output_tokens=300,
            temperature=0.7,
            surface="packaging",
        )
        if result.ok and result.parsed:
            profile = result.parsed if isinstance(result.parsed, dict) else {}
        else:
            # Fallback: generate a simple profile
            profile = {
                "first_name": "User",
                "last_name": "",
                "username": f"user_{account_id}_{random.randint(1000, 9999)}",
                "bio": "Telegram user",
            }
    except Exception as e:
        logger.warning("AI profile generation failed, using fallback: %s", e)
        profile = {
            "first_name": "User",
            "last_name": "",
            "username": f"user_{account_id}_{random.randint(1000, 9999)}",
            "bio": "Telegram user",
        }

    # Save to DB
    account.profile_gender = gender
    account.profile_age_range = age_range
    account.profile_country = country
    account.profile_profession = profession
    account.profile_first_name = profile.get("first_name", "")
    account.profile_last_name = profile.get("last_name", "")
    account.profile_username = profile.get("username", "")
    account.profile_bio = profile.get("bio", "")
    _update_packaging_status(account)
    await session.flush()

    await log_packaging_op(
        session, workspace_id, account_id, "generate_profile",
        "success", json.dumps(profile, ensure_ascii=False),
    )

    return {
        "account_id": account_id,
        "profile": profile,
        "packaging_status": account.packaging_status,
    }


# ── Mass generate profiles ────────────────────────────────────────


async def mass_generate_profiles(
    session: AsyncSession,
    workspace_id: int,
    account_ids: list[int],
    params: dict,
    tenant_id: int = 0,
) -> list[dict]:
    """Generate profiles for multiple accounts with delays between each."""
    results = []
    for i, aid in enumerate(account_ids):
        try:
            result = await generate_profile(session, workspace_id, aid, params, tenant_id)
            results.append(result)
        except Exception as e:
            results.append({"account_id": aid, "error": str(e)})
        if i < len(account_ids) - 1:
            await _human_delay(3, 8)
    return results


# ── Apply profile to Telegram ─────────────────────────────────────


async def apply_profile(
    session: AsyncSession,
    workspace_id: int,
    account_id: int,
    proxy=None,
) -> dict:
    """
    Connect via Telethon and apply name/bio/username.
    Checks 48h guard before proceeding.
    """
    account = await _get_account(session, account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")
    if account.workspace_id != workspace_id:
        raise ValueError("Account does not belong to this workspace")

    guard_msg = _check_48h_guard(account)
    if guard_msg:
        raise ValueError(guard_msg)

    if not account.profile_first_name:
        raise ValueError("No profile generated yet. Generate a profile first.")

    session_path = account.session_path
    if not session_path or not os.path.exists(session_path):
        raise FileNotFoundError(f"Session file not found: {session_path}")

    from telethon import TelegramClient
    from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest

    proxy_tuple = _build_proxy_tuple(account, proxy)
    client = TelegramClient(
        session_path.replace(".session", ""),
        account.api_id or 2040,
        account.api_hash or "b18441a1ff607e10a989891a5462e627",
        proxy=proxy_tuple,
        timeout=30,
        connection_retries=3,
        retry_delay=5,
    )

    result = {"name_set": False, "bio_set": False, "username_set": False}
    try:
        await asyncio.wait_for(client.connect(), timeout=30)
        if not await client.is_user_authorized():
            raise RuntimeError("Account not authorized")

        # Set name + bio
        await _human_delay(2, 5)
        await client(UpdateProfileRequest(
            first_name=account.profile_first_name or "",
            last_name=account.profile_last_name or "",
            about=account.profile_bio or "",
        ))
        result["name_set"] = True
        result["bio_set"] = True

        # Set username
        if account.profile_username:
            await _human_delay(2, 5)
            try:
                await client(UpdateUsernameRequest(username=account.profile_username))
                result["username_set"] = True
            except Exception as e:
                logger.warning("Username set failed (may be taken): %s", e)
                result["username_error"] = str(e)

        # Update DB
        now = datetime.now(timezone.utc)
        account.display_name = f"{account.profile_first_name} {account.profile_last_name}".strip()
        account.username = account.profile_username
        account.bio = account.profile_bio
        account.profile_applied_at = now
        _update_packaging_status(account)
        await session.flush()

        await log_packaging_op(
            session, workspace_id, account_id, "apply_profile",
            "success", json.dumps(result, ensure_ascii=False),
        )
    except Exception as e:
        logger.error("Apply profile failed for %s: %s", account.phone, e)
        await log_packaging_op(
            session, workspace_id, account_id, "apply_profile",
            "error", str(e),
        )
        result["error"] = str(e)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    return result


# ── Avatar generation (placeholder) ───────────────────────────────


async def generate_avatar(
    session: AsyncSession,
    workspace_id: int,
    account_id: int,
    prompt: str,
    tenant_id: int = 0,
) -> dict:
    """
    Generate avatar via AI and save to storage/avatars/{workspace_id}/{phone}.jpg.
    Currently a placeholder that creates a minimal file.
    """
    account = await _get_account(session, account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")
    if account.workspace_id != workspace_id:
        raise ValueError("Account does not belong to this workspace")

    avatar_dir = _avatar_dir(workspace_id)
    avatar_dir.mkdir(parents=True, exist_ok=True)
    avatar_file = avatar_dir / f"{_sanitize_phone(account.phone)}.jpg"

    # Placeholder: In production, call an image generation API here.
    # For now, log the request and mark path.
    avatar_file.write_bytes(b"\xff\xd8\xff\xe0")  # minimal JPEG header placeholder

    account.avatar_path = str(avatar_file)
    _update_packaging_status(account)
    await session.flush()

    await log_packaging_op(
        session, workspace_id, account_id, "generate_avatar",
        "success", f"prompt={prompt}, path={avatar_file}",
    )

    return {
        "account_id": account_id,
        "avatar_path": str(avatar_file),
        "packaging_status": account.packaging_status,
    }


# ── Avatar upload ──────────────────────────────────────────────────


async def upload_avatar(
    session: AsyncSession,
    workspace_id: int,
    account_id: int,
    file_bytes: bytes,
    filename: str,
) -> dict:
    """Save uploaded avatar file to storage/avatars/{workspace_id}/{phone}.jpg."""
    account = await _get_account(session, account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")
    if account.workspace_id != workspace_id:
        raise ValueError("Account does not belong to this workspace")

    avatar_dir = _avatar_dir(workspace_id)
    avatar_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(filename).suffix or ".jpg"
    avatar_file = avatar_dir / f"{_sanitize_phone(account.phone)}{ext}"
    avatar_file.write_bytes(file_bytes)

    account.avatar_path = str(avatar_file)
    _update_packaging_status(account)
    await session.flush()

    await log_packaging_op(
        session, workspace_id, account_id, "upload_avatar",
        "success", f"file={filename}, size={len(file_bytes)}, path={avatar_file}",
    )

    return {
        "account_id": account_id,
        "avatar_path": str(avatar_file),
        "packaging_status": account.packaging_status,
    }


# ── Apply avatar to Telegram ──────────────────────────────────────


async def apply_avatar(
    session: AsyncSession,
    workspace_id: int,
    account_id: int,
    proxy=None,
) -> dict:
    """Connect via Telethon and set profile photo. 48h guard applies."""
    account = await _get_account(session, account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")
    if account.workspace_id != workspace_id:
        raise ValueError("Account does not belong to this workspace")

    guard_msg = _check_48h_guard(account)
    if guard_msg:
        raise ValueError(guard_msg)

    if not account.avatar_path or not os.path.exists(account.avatar_path):
        raise ValueError("No avatar file found. Upload or generate an avatar first.")

    session_path = account.session_path
    if not session_path or not os.path.exists(session_path):
        raise FileNotFoundError(f"Session file not found: {session_path}")

    from telethon import TelegramClient
    from telethon.tl.functions.photos import UploadProfilePhotoRequest

    proxy_tuple = _build_proxy_tuple(account, proxy)
    client = TelegramClient(
        session_path.replace(".session", ""),
        account.api_id or 2040,
        account.api_hash or "b18441a1ff607e10a989891a5462e627",
        proxy=proxy_tuple,
        timeout=30,
        connection_retries=3,
        retry_delay=5,
    )

    result = {"avatar_set": False}
    try:
        await asyncio.wait_for(client.connect(), timeout=30)
        if not await client.is_user_authorized():
            raise RuntimeError("Account not authorized")

        await _human_delay(2, 5)
        avatar_file = await client.upload_file(account.avatar_path)
        await client(UploadProfilePhotoRequest(file=avatar_file))
        result["avatar_set"] = True

        _update_packaging_status(account)
        await session.flush()

        await log_packaging_op(
            session, workspace_id, account_id, "apply_avatar",
            "success", f"Avatar set from {account.avatar_path}",
        )
    except Exception as e:
        logger.error("Apply avatar failed for %s: %s", account.phone, e)
        await log_packaging_op(
            session, workspace_id, account_id, "apply_avatar",
            "error", str(e),
        )
        result["error"] = str(e)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    return result


# ── Create Telegram channel ────────────────────────────────────────


async def create_channel(
    session: AsyncSession,
    workspace_id: int,
    account_id: int,
    title: str,
    description: str = "",
    first_post_text: str = "",
    proxy=None,
) -> dict:
    """
    Create a Telegram channel via Telethon CreateChannelRequest.
    Sets avatar if available, posts + pins first message.
    Uses human delays between all steps.
    """
    account = await _get_account(session, account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")
    if account.workspace_id != workspace_id:
        raise ValueError("Account does not belong to this workspace")

    if not title or not title.strip():
        raise ValueError("Channel title is required")

    session_path = account.session_path
    if not session_path or not os.path.exists(session_path):
        raise FileNotFoundError(f"Session file not found: {session_path}")

    from telethon import TelegramClient
    from telethon.tl.functions.channels import CreateChannelRequest

    proxy_tuple = _build_proxy_tuple(account, proxy)
    client = TelegramClient(
        session_path.replace(".session", ""),
        account.api_id or 2040,
        account.api_hash or "b18441a1ff607e10a989891a5462e627",
        proxy=proxy_tuple,
        timeout=30,
        connection_retries=3,
        retry_delay=5,
    )

    result = {"channel_created": False}
    try:
        await asyncio.wait_for(client.connect(), timeout=30)
        if not await client.is_user_authorized():
            raise RuntimeError("Account not authorized")

        # Create channel
        await _human_delay(3, 7)
        channel_result = await client(CreateChannelRequest(
            title=title.strip(),
            about=description.strip() if description else "",
            megagroup=False,
        ))
        channel = channel_result.chats[0]
        channel_id = channel.id
        channel_username = getattr(channel, "username", None)
        result["channel_created"] = True
        result["channel_id"] = channel_id

        # Set channel avatar if account has one
        if account.avatar_path and os.path.exists(account.avatar_path):
            await _human_delay(2, 5)
            try:
                from telethon.tl.functions.channels import EditPhotoRequest
                from telethon.tl.types import InputChannel

                avatar_file = await client.upload_file(account.avatar_path)
                input_channel = InputChannel(channel_id=channel_id, access_hash=channel.access_hash)
                await client(EditPhotoRequest(
                    channel=input_channel,
                    photo=await client.upload_file(account.avatar_path),
                ))
                result["avatar_set_on_channel"] = True
            except Exception as e:
                logger.warning("Channel avatar set failed: %s", e)
                result["avatar_set_on_channel"] = False

        # Post first message
        if first_post_text and first_post_text.strip():
            await _human_delay(3, 6)
            try:
                msg = await client.send_message(channel, first_post_text.strip())
                # Pin the message
                await _human_delay(2, 4)
                await client.pin_message(channel, msg.id)
                result["first_post_sent"] = True
                result["first_post_pinned"] = True
            except Exception as e:
                logger.warning("First post or pin failed: %s", e)
                result["first_post_error"] = str(e)

        # Update DB
        now = datetime.now(timezone.utc)
        account.channel_id = channel_id
        account.channel_username = channel_username
        account.channel_title = title.strip()
        account.channel_created_at = now
        _update_packaging_status(account)
        await session.flush()

        await log_packaging_op(
            session, workspace_id, account_id, "create_channel",
            "success", json.dumps(result, ensure_ascii=False),
        )
    except Exception as e:
        logger.error("Create channel failed for %s: %s", account.phone, e)
        await log_packaging_op(
            session, workspace_id, account_id, "create_channel",
            "error", str(e),
        )
        result["error"] = str(e)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    return result


# ── Packaging status ───────────────────────────────────────────────


async def get_packaging_status(
    session: AsyncSession,
    workspace_id: int,
    account_id: int,
) -> dict:
    """Return current packaging state for an account."""
    account = await _get_account(session, account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")
    if account.workspace_id != workspace_id:
        raise ValueError("Account does not belong to this workspace")

    guard_msg = _check_48h_guard(account)

    return {
        "account_id": account_id,
        "phone": account.phone,
        "packaging_status": account.packaging_status or "not_started",
        "profile_generated": bool(account.profile_first_name),
        "profile_gender": account.profile_gender,
        "profile_age_range": account.profile_age_range,
        "profile_country": account.profile_country,
        "profile_profession": account.profile_profession,
        "profile_first_name": account.profile_first_name,
        "profile_last_name": account.profile_last_name,
        "profile_username": account.profile_username,
        "profile_bio": account.profile_bio,
        "avatar_path": account.avatar_path,
        "avatar_ready": bool(account.avatar_path),
        "profile_applied": bool(account.profile_applied_at),
        "profile_applied_at": account.profile_applied_at.isoformat() if account.profile_applied_at else None,
        "channel_created": bool(account.channel_id),
        "channel_id": account.channel_id,
        "channel_username": account.channel_username,
        "channel_title": account.channel_title,
        "channel_created_at": account.channel_created_at.isoformat() if account.channel_created_at else None,
        "guard_48h_active": guard_msg is not None,
        "guard_48h_message": guard_msg,
        "profile_change_earliest": account.profile_change_earliest.isoformat() if account.profile_change_earliest else None,
    }
