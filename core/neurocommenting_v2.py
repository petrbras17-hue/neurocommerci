"""
Neurocommenting v2 — blacklists, whitelists, targeting, auto-DM, presets, channel import.

All significant actions are logged via log_operation().
Telethon operations use human-like delays for anti-detection.
"""
from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.ai_router import route_ai_task
from core.operation_logger import log_operation
from storage.models import (
    AutoDmConfig,
    ChannelBlacklist,
    ChannelMapEntry,
    ChannelWhitelist,
    FarmPreset,
)
from storage.sqlite_db import async_session
from utils.helpers import utcnow
from utils.logger import log


# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------


async def add_to_blacklist(
    workspace_id: int,
    channel_id: int,
    username: Optional[str] = None,
    title: Optional[str] = None,
    reason: str = "manual",
) -> dict:
    """Add a channel to the workspace blacklist. Returns the created row as dict."""
    async with async_session() as session:
        async with session.begin():
            entry = ChannelBlacklist(
                workspace_id=workspace_id,
                channel_id=channel_id,
                channel_username=username,
                channel_title=title,
                reason=reason,
            )
            session.add(entry)
            await session.flush()
            result = _serialize_blacklist(entry)

    await log_operation(
        workspace_id=workspace_id,
        account_id=None,
        module="neurocommenting_v2",
        action="add_to_blacklist",
        status="success",
        detail=f"channel_id={channel_id} reason={reason}",
    )
    return result


async def remove_from_blacklist(workspace_id: int, channel_id: int) -> bool:
    """Remove a channel from the workspace blacklist. Returns True if deleted."""
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                delete(ChannelBlacklist).where(
                    ChannelBlacklist.workspace_id == workspace_id,
                    ChannelBlacklist.channel_id == channel_id,
                )
            )
            deleted = result.rowcount > 0

    if deleted:
        await log_operation(
            workspace_id=workspace_id,
            account_id=None,
            module="neurocommenting_v2",
            action="remove_from_blacklist",
            status="success",
            detail=f"channel_id={channel_id}",
        )
    return deleted


async def auto_blacklist_on_ban(
    workspace_id: int,
    channel_id: int,
    username: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """Auto-blacklist a channel when an account gets banned there."""
    return await add_to_blacklist(workspace_id, channel_id, username, title, reason="auto_ban")


async def is_channel_blacklisted(workspace_id: int, channel_id: int) -> bool:
    """Check if a channel is in the blacklist."""
    async with async_session() as session:
        async with session.begin():
            row = (
                await session.execute(
                    select(ChannelBlacklist.id).where(
                        ChannelBlacklist.workspace_id == workspace_id,
                        ChannelBlacklist.channel_id == channel_id,
                    )
                )
            ).scalar_one_or_none()
            return row is not None


async def get_blacklist(
    workspace_id: int, limit: int = 50, offset: int = 0
) -> List[dict]:
    """Return paginated blacklist for the workspace."""
    async with async_session() as session:
        async with session.begin():
            rows = (
                await session.execute(
                    select(ChannelBlacklist)
                    .where(ChannelBlacklist.workspace_id == workspace_id)
                    .order_by(ChannelBlacklist.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars().all()
            return [_serialize_blacklist(r) for r in rows]


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------


async def add_to_whitelist(
    workspace_id: int,
    channel_id: int,
    username: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """Add a channel to the workspace whitelist."""
    async with async_session() as session:
        async with session.begin():
            entry = ChannelWhitelist(
                workspace_id=workspace_id,
                channel_id=channel_id,
                channel_username=username,
                channel_title=title,
                successful_comments=0,
            )
            session.add(entry)
            await session.flush()
            result = _serialize_whitelist(entry)

    await log_operation(
        workspace_id=workspace_id,
        account_id=None,
        module="neurocommenting_v2",
        action="add_to_whitelist",
        status="success",
        detail=f"channel_id={channel_id}",
    )
    return result


async def remove_from_whitelist(workspace_id: int, channel_id: int) -> bool:
    """Remove a channel from the workspace whitelist."""
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                delete(ChannelWhitelist).where(
                    ChannelWhitelist.workspace_id == workspace_id,
                    ChannelWhitelist.channel_id == channel_id,
                )
            )
            deleted = result.rowcount > 0

    if deleted:
        await log_operation(
            workspace_id=workspace_id,
            account_id=None,
            module="neurocommenting_v2",
            action="remove_from_whitelist",
            status="success",
            detail=f"channel_id={channel_id}",
        )
    return deleted


async def auto_whitelist_on_success(
    workspace_id: int,
    channel_id: int,
    username: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """Increment successful_comments; auto-add to whitelist if not present."""
    async with async_session() as session:
        async with session.begin():
            existing = (
                await session.execute(
                    select(ChannelWhitelist).where(
                        ChannelWhitelist.workspace_id == workspace_id,
                        ChannelWhitelist.channel_id == channel_id,
                    )
                )
            ).scalar_one_or_none()

            if existing:
                existing.successful_comments = (existing.successful_comments or 0) + 1
                await session.flush()
                result = _serialize_whitelist(existing)
            else:
                entry = ChannelWhitelist(
                    workspace_id=workspace_id,
                    channel_id=channel_id,
                    channel_username=username,
                    channel_title=title,
                    successful_comments=1,
                )
                session.add(entry)
                await session.flush()
                result = _serialize_whitelist(entry)

    return result


async def is_channel_whitelisted(workspace_id: int, channel_id: int) -> bool:
    """Check if a channel is in the whitelist."""
    async with async_session() as session:
        async with session.begin():
            row = (
                await session.execute(
                    select(ChannelWhitelist.id).where(
                        ChannelWhitelist.workspace_id == workspace_id,
                        ChannelWhitelist.channel_id == channel_id,
                    )
                )
            ).scalar_one_or_none()
            return row is not None


async def get_whitelist(
    workspace_id: int, limit: int = 50, offset: int = 0
) -> List[dict]:
    """Return paginated whitelist for the workspace."""
    async with async_session() as session:
        async with session.begin():
            rows = (
                await session.execute(
                    select(ChannelWhitelist)
                    .where(ChannelWhitelist.workspace_id == workspace_id)
                    .order_by(ChannelWhitelist.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars().all()
            return [_serialize_whitelist(r) for r in rows]


# ---------------------------------------------------------------------------
# Comment as channel (Telethon)
# ---------------------------------------------------------------------------


async def comment_as_channel(
    client: Any,
    channel_id: int,
    message: str,
    from_channel_id: int,
) -> bool:
    """
    Send a comment from an account's pinned channel using Telethon.

    Uses SendMessageRequest with send_as peer set to the source channel.
    Human-like typing delay applied before sending.
    """
    try:
        from telethon.tl.functions.messages import SendMessageRequest
        from telethon.tl.types import InputPeerChannel

        # Human delay: typing simulation
        await asyncio.sleep(random.uniform(2.0, 6.0))

        await client(
            SendMessageRequest(
                peer=channel_id,
                message=message,
                send_as=from_channel_id,
            )
        )
        log.info("comment_as_channel: sent to %s from channel %s", channel_id, from_channel_id)
        return True
    except Exception as exc:
        log.warning("comment_as_channel failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Auto-DM
# ---------------------------------------------------------------------------

# In-memory set of (farm_id, sender_id, date) to track who we already DM'd today.
# For production: replace with Redis SET.
_dm_sent_today: set = set()


async def setup_auto_dm(
    workspace_id: int,
    farm_id: int,
    message: str,
    max_per_day: int = 10,
) -> dict:
    """Create or update AutoDmConfig for a farm."""
    async with async_session() as session:
        async with session.begin():
            existing = (
                await session.execute(
                    select(AutoDmConfig).where(
                        AutoDmConfig.workspace_id == workspace_id,
                        AutoDmConfig.farm_id == farm_id,
                    )
                )
            ).scalar_one_or_none()

            if existing:
                existing.message = message
                existing.max_dms_per_day = max_per_day
                existing.is_active = True
                await session.flush()
                result = _serialize_auto_dm(existing)
            else:
                entry = AutoDmConfig(
                    workspace_id=workspace_id,
                    farm_id=farm_id,
                    message=message,
                    max_dms_per_day=max_per_day,
                    is_active=True,
                    dms_sent_today=0,
                )
                session.add(entry)
                await session.flush()
                result = _serialize_auto_dm(entry)

    await log_operation(
        workspace_id=workspace_id,
        account_id=None,
        module="neurocommenting_v2",
        action="setup_auto_dm",
        status="success",
        detail=f"farm_id={farm_id} max_per_day={max_per_day}",
    )
    return result


async def update_auto_dm(
    workspace_id: int,
    farm_id: int,
    message: Optional[str] = None,
    max_per_day: Optional[int] = None,
    is_active: Optional[bool] = None,
) -> Optional[dict]:
    """Update existing AutoDmConfig fields."""
    async with async_session() as session:
        async with session.begin():
            entry = (
                await session.execute(
                    select(AutoDmConfig).where(
                        AutoDmConfig.workspace_id == workspace_id,
                        AutoDmConfig.farm_id == farm_id,
                    )
                )
            ).scalar_one_or_none()
            if not entry:
                return None
            if message is not None:
                entry.message = message
            if max_per_day is not None:
                entry.max_dms_per_day = max_per_day
            if is_active is not None:
                entry.is_active = is_active
            await session.flush()
            return _serialize_auto_dm(entry)


async def get_auto_dm(workspace_id: int, farm_id: int) -> Optional[dict]:
    """Get AutoDmConfig for a farm."""
    async with async_session() as session:
        async with session.begin():
            entry = (
                await session.execute(
                    select(AutoDmConfig).where(
                        AutoDmConfig.workspace_id == workspace_id,
                        AutoDmConfig.farm_id == farm_id,
                    )
                )
            ).scalar_one_or_none()
            return _serialize_auto_dm(entry) if entry else None


async def delete_auto_dm(workspace_id: int, farm_id: int) -> bool:
    """Delete (disable) auto-DM config for a farm."""
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                delete(AutoDmConfig).where(
                    AutoDmConfig.workspace_id == workspace_id,
                    AutoDmConfig.farm_id == farm_id,
                )
            )
            return result.rowcount > 0


async def handle_incoming_dm(
    workspace_id: int,
    farm_id: int,
    sender_id: int,
    client: Any = None,
) -> bool:
    """
    Check if auto_dm enabled for this farm.
    If first DM from this user today, send the pre-set message.
    """
    config = await get_auto_dm(workspace_id, farm_id)
    if not config or not config.get("is_active"):
        return False

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = (farm_id, sender_id, today_str)
    if key in _dm_sent_today:
        return False

    # Check daily limit
    if config["dms_sent_today"] >= config["max_dms_per_day"]:
        return False

    # Human delay before responding
    await asyncio.sleep(random.uniform(5.0, 15.0))

    if client:
        try:
            await client.send_message(sender_id, config["message"])
        except Exception as exc:
            log.warning("handle_incoming_dm: failed to send DM: %s", exc)
            return False

    _dm_sent_today.add(key)

    # Increment counter
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                update(AutoDmConfig)
                .where(
                    AutoDmConfig.workspace_id == workspace_id,
                    AutoDmConfig.farm_id == farm_id,
                )
                .values(dms_sent_today=AutoDmConfig.dms_sent_today + 1)
            )

    await log_operation(
        workspace_id=workspace_id,
        account_id=None,
        module="neurocommenting_v2",
        action="auto_dm_sent",
        status="success",
        detail=f"farm_id={farm_id} sender_id={sender_id}",
    )
    return True


# ---------------------------------------------------------------------------
# Targeting
# ---------------------------------------------------------------------------


def filter_posts_by_targeting(
    posts: List[dict],
    targeting_mode: str,
    targeting_params: Optional[dict] = None,
) -> List[dict]:
    """
    Filter a list of posts based on targeting mode.

    Modes:
      - all: return all posts
      - random_pct: return a random percentage of posts (params: {"pct": 30})
      - keyword_match: return posts whose text matches any keyword (params: {"keywords": [...]})
    """
    if targeting_mode == "all" or not targeting_mode:
        return posts

    params = targeting_params or {}

    if targeting_mode == "random_pct":
        pct = params.get("pct", 100)
        pct = max(0, min(100, int(pct)))
        if pct >= 100:
            return posts
        count = max(1, round(len(posts) * pct / 100))
        return random.sample(posts, min(count, len(posts)))

    if targeting_mode == "keyword_match":
        keywords = params.get("keywords", [])
        if not keywords:
            return posts
        pattern = re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE)
        return [p for p in posts if pattern.search(p.get("text", "") or "")]

    return posts


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


async def detect_channel_language(channel_id: int) -> str:
    """
    Detect language of a channel.
    First checks channel_map_entries for a stored language value.
    Falls back to AI detection if not found.
    """
    async with async_session() as session:
        async with session.begin():
            row = (
                await session.execute(
                    select(ChannelMapEntry.language).where(
                        ChannelMapEntry.telegram_id == channel_id
                    )
                )
            ).scalar_one_or_none()
            if row:
                return row

    # Fallback: use AI to detect
    try:
        result = await route_ai_task(
            task_type="farm_comment",
            prompt=f"What language is primarily used in the Telegram channel with ID {channel_id}? "
            "Reply with a 2-letter ISO 639-1 code only (e.g. 'ru', 'en', 'uk').",
            workspace_id=0,
        )
        lang = (result.get("text") or "").strip().lower()[:2]
        if len(lang) == 2 and lang.isalpha():
            return lang
    except Exception as exc:
        log.warning("detect_channel_language: AI fallback failed: %s", exc)

    return "ru"  # default


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


async def save_preset(
    workspace_id: int,
    name: str,
    config_dict: dict,
    targeting_mode: str = "all",
    targeting_params: Optional[dict] = None,
    comment_as_channel: bool = False,
    auto_dm_enabled: bool = False,
    auto_dm_message: Optional[str] = None,
    language: str = "auto",
) -> dict:
    """Save a farm configuration as a reusable preset."""
    async with async_session() as session:
        async with session.begin():
            entry = FarmPreset(
                workspace_id=workspace_id,
                name=name,
                config=config_dict,
                targeting_mode=targeting_mode,
                targeting_params=targeting_params,
                comment_as_channel=comment_as_channel,
                auto_dm_enabled=auto_dm_enabled,
                auto_dm_message=auto_dm_message,
                language=language,
            )
            session.add(entry)
            await session.flush()
            result = _serialize_preset(entry)

    await log_operation(
        workspace_id=workspace_id,
        account_id=None,
        module="neurocommenting_v2",
        action="save_preset",
        status="success",
        detail=f"preset={name}",
    )
    return result


async def load_preset(workspace_id: int, preset_id: int) -> Optional[dict]:
    """Load a preset by ID."""
    async with async_session() as session:
        async with session.begin():
            entry = (
                await session.execute(
                    select(FarmPreset).where(
                        FarmPreset.workspace_id == workspace_id,
                        FarmPreset.id == preset_id,
                    )
                )
            ).scalar_one_or_none()
            return _serialize_preset(entry) if entry else None


async def list_presets(workspace_id: int) -> List[dict]:
    """List all presets for a workspace."""
    async with async_session() as session:
        async with session.begin():
            rows = (
                await session.execute(
                    select(FarmPreset)
                    .where(FarmPreset.workspace_id == workspace_id)
                    .order_by(FarmPreset.created_at.desc())
                )
            ).scalars().all()
            return [_serialize_preset(r) for r in rows]


async def delete_preset(workspace_id: int, preset_id: int) -> bool:
    """Delete a preset."""
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                delete(FarmPreset).where(
                    FarmPreset.workspace_id == workspace_id,
                    FarmPreset.id == preset_id,
                )
            )
            deleted = result.rowcount > 0

    if deleted:
        await log_operation(
            workspace_id=workspace_id,
            account_id=None,
            module="neurocommenting_v2",
            action="delete_preset",
            status="success",
            detail=f"preset_id={preset_id}",
        )
    return deleted


# ---------------------------------------------------------------------------
# Import channels from Telegram folder
# ---------------------------------------------------------------------------


async def import_channels_from_folder(
    client: Any,
    folder_name: str,
) -> List[int]:
    """
    Import channel IDs from a Telegram folder using Telethon.

    Uses GetDialogFiltersRequest to find the folder by name,
    then extracts channel peer IDs.
    """
    try:
        from telethon.tl.functions.messages import GetDialogFiltersRequest
        from telethon.tl.types import InputPeerChannel

        result = await client(GetDialogFiltersRequest())
        channel_ids: List[int] = []

        for f in result.filters if hasattr(result, "filters") else result:
            title = getattr(f, "title", None)
            if not title:
                continue
            # title can be a string or TextWithEntities
            folder_title = str(title) if not isinstance(title, str) else title
            if folder_title.lower() != folder_name.lower():
                continue

            # Extract included peers
            for peer in getattr(f, "include_peers", []):
                if hasattr(peer, "channel_id"):
                    channel_ids.append(peer.channel_id)

        log.info(
            "import_channels_from_folder: found %d channels in folder '%s'",
            len(channel_ids),
            folder_name,
        )
        return channel_ids

    except Exception as exc:
        log.warning("import_channels_from_folder failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _serialize_blacklist(entry: ChannelBlacklist) -> dict:
    return {
        "id": entry.id,
        "workspace_id": entry.workspace_id,
        "channel_id": entry.channel_id,
        "channel_username": entry.channel_username,
        "channel_title": entry.channel_title,
        "reason": entry.reason,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _serialize_whitelist(entry: ChannelWhitelist) -> dict:
    return {
        "id": entry.id,
        "workspace_id": entry.workspace_id,
        "channel_id": entry.channel_id,
        "channel_username": entry.channel_username,
        "channel_title": entry.channel_title,
        "successful_comments": entry.successful_comments or 0,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _serialize_preset(entry: FarmPreset) -> dict:
    return {
        "id": entry.id,
        "workspace_id": entry.workspace_id,
        "name": entry.name,
        "config": entry.config,
        "targeting_mode": entry.targeting_mode,
        "targeting_params": entry.targeting_params,
        "comment_as_channel": entry.comment_as_channel,
        "auto_dm_enabled": entry.auto_dm_enabled,
        "auto_dm_message": entry.auto_dm_message,
        "language": entry.language,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _serialize_auto_dm(entry: AutoDmConfig) -> dict:
    return {
        "id": entry.id,
        "workspace_id": entry.workspace_id,
        "farm_id": entry.farm_id,
        "message": entry.message,
        "is_active": entry.is_active,
        "max_dms_per_day": entry.max_dms_per_day,
        "dms_sent_today": entry.dms_sent_today or 0,
        "last_reset_at": entry.last_reset_at.isoformat() if entry.last_reset_at else None,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }
