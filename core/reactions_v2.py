"""
Reactions v2 — Monitoring-based auto-reactions + blacklist management.

Public API
----------
create_monitoring_config(...)      — create a new monitoring config
update_monitoring_config(...)      — update params on existing config
delete_monitoring_config(...)      — remove a config
list_monitoring_configs(...)       — list all configs for workspace
run_monitoring_loop(config_id)     — background: poll channel, react to new comments
react_as_channel(...)              — Premium: react from channel entity
add_reaction_blacklist(...)        — blacklist a channel from reactions
remove_reaction_blacklist(...)     — remove channel from blacklist
get_reaction_blacklist(...)        — list all blacklisted channels
is_reaction_blacklisted(...)       — check if channel is blacklisted

Safety rules:
  - Random 3-8 s human delay between reactions.
  - Hourly rate limit per config.
  - Blacklist check before every reaction.
  - FloodWaitError → sleep and continue.
  - FrozenMethodInvalidError → skip account.
  - Each DB write uses its own short-lived session with RLS.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.operation_logger import log_operation
from storage.models import ReactionBlacklist, ReactionMonitoringConfig
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log

# In-memory task registry
_monitoring_tasks: dict[int, asyncio.Task] = {}

# Human delay range between reactions (seconds)
_DELAY_MIN = 3
_DELAY_MAX = 8

# Poll interval for new comments (seconds)
_POLL_INTERVAL = 5


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------


async def create_monitoring_config(
    workspace_id: int,
    channel_id: int,
    title: Optional[str] = None,
    emoji: str = "\U0001f44d",
    react_within_seconds: int = 30,
    accounts: Optional[List[int]] = None,
    max_per_hour: int = 30,
    use_channel: bool = False,
) -> dict:
    """Create a new reaction monitoring config."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            cfg = ReactionMonitoringConfig(
                workspace_id=workspace_id,
                channel_id=channel_id,
                channel_title=title,
                reaction_emoji=emoji,
                react_within_seconds=react_within_seconds,
                accounts_assigned=accounts or [],
                max_reactions_per_hour=max_per_hour,
                use_channel_reaction=use_channel,
            )
            session.add(cfg)
            await session.flush()
            result = _serialize_config(cfg)
    await log_operation(workspace_id, None, "reactions_v2", "config_created",
                        "success", f"channel={channel_id}")
    return result


async def update_monitoring_config(
    workspace_id: int,
    config_id: int,
    **params: Any,
) -> dict:
    """Update fields on an existing monitoring config."""
    allowed = {
        "channel_title", "reaction_emoji", "react_within_seconds",
        "is_active", "accounts_assigned", "max_reactions_per_hour",
        "reactions_this_hour", "use_channel_reaction",
    }
    updates = {k: v for k, v in params.items() if k in allowed and v is not None}
    if not updates:
        raise ValueError("No valid fields to update")

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            stmt = (
                update(ReactionMonitoringConfig)
                .where(ReactionMonitoringConfig.id == config_id)
                .where(ReactionMonitoringConfig.workspace_id == workspace_id)
                .values(**updates)
                .returning(ReactionMonitoringConfig)
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if not row:
                raise ValueError(f"Config {config_id} not found")
            result = _serialize_config(row)
    await log_operation(workspace_id, None, "reactions_v2", "config_updated",
                        "success", f"config_id={config_id}")
    return result


async def delete_monitoring_config(workspace_id: int, config_id: int) -> None:
    """Delete a monitoring config. Stops any running loop first."""
    await stop_monitoring_loop(config_id)
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            stmt = (
                delete(ReactionMonitoringConfig)
                .where(ReactionMonitoringConfig.id == config_id)
                .where(ReactionMonitoringConfig.workspace_id == workspace_id)
            )
            res = await session.execute(stmt)
            if res.rowcount == 0:
                raise ValueError(f"Config {config_id} not found")
    await log_operation(workspace_id, None, "reactions_v2", "config_deleted",
                        "success", f"config_id={config_id}")


async def list_monitoring_configs(workspace_id: int) -> List[dict]:
    """List all monitoring configs for a workspace."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            rows = (await session.execute(
                select(ReactionMonitoringConfig)
                .where(ReactionMonitoringConfig.workspace_id == workspace_id)
                .order_by(ReactionMonitoringConfig.created_at.desc())
            )).scalars().all()
            return [_serialize_config(r) for r in rows]


# ---------------------------------------------------------------------------
# Monitoring loop
# ---------------------------------------------------------------------------


async def start_monitoring_loop(config_id: int, workspace_id: int) -> None:
    """Start background monitoring loop for a config."""
    if config_id in _monitoring_tasks and not _monitoring_tasks[config_id].done():
        log.info("reactions_v2: monitoring loop %d already running", config_id)
        return
    task = asyncio.create_task(_monitoring_loop(config_id, workspace_id))
    _monitoring_tasks[config_id] = task
    await log_operation(workspace_id, None, "reactions_v2", "monitoring_started",
                        "success", f"config_id={config_id}")


async def stop_monitoring_loop(config_id: int) -> None:
    """Stop a running monitoring loop."""
    task = _monitoring_tasks.pop(config_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        log.info("reactions_v2: monitoring loop %d stopped", config_id)


async def _monitoring_loop(config_id: int, workspace_id: int) -> None:
    """Poll channel for new comments, react within the configured window."""
    last_seen_msg_id = 0
    log.info("reactions_v2: starting monitoring loop for config %d", config_id)

    try:
        while True:
            # Reload config each iteration to pick up changes
            cfg = await _load_config(config_id, workspace_id)
            if not cfg or not cfg.get("is_active"):
                log.info("reactions_v2: config %d inactive, stopping loop", config_id)
                break

            if cfg.get("reactions_this_hour", 0) >= cfg.get("max_reactions_per_hour", 30):
                log.debug("reactions_v2: config %d hit hourly limit, sleeping", config_id)
                await asyncio.sleep(60)
                continue

            # Check blacklist
            if await is_reaction_blacklisted(workspace_id, cfg["channel_id"]):
                log.info("reactions_v2: channel %d blacklisted, stopping", cfg["channel_id"])
                break

            # Try to get new messages and react
            try:
                new_reactions = await _poll_and_react(cfg, workspace_id, last_seen_msg_id)
                if new_reactions > 0:
                    last_seen_msg_id = max(last_seen_msg_id, new_reactions)
            except Exception as exc:
                log.warning("reactions_v2: poll error for config %d: %s", config_id, exc)

            await asyncio.sleep(_POLL_INTERVAL)

    except asyncio.CancelledError:
        log.info("reactions_v2: monitoring loop %d cancelled", config_id)
    except Exception as exc:
        log.error("reactions_v2: monitoring loop %d crashed: %s", config_id, exc)
        await log_operation(workspace_id, None, "reactions_v2", "monitoring_error",
                            "error", str(exc)[:200])


async def _poll_and_react(cfg: dict, workspace_id: int, last_seen_msg_id: int) -> int:
    """
    Poll channel for new messages and react.
    Returns the highest message ID seen.

    In production this would use Telethon GetMessagesRequest.
    For now, this is a framework that logs intended reactions.
    """
    try:
        from core.session_pool import SessionPool
    except ImportError:
        log.debug("reactions_v2: SessionPool not available, skipping poll")
        return last_seen_msg_id

    accounts = cfg.get("accounts_assigned", [])
    if not accounts:
        return last_seen_msg_id

    channel_id = cfg["channel_id"]
    emoji = cfg.get("reaction_emoji", "\U0001f44d")
    react_within = cfg.get("react_within_seconds", 30)
    max_hourly = cfg.get("max_reactions_per_hour", 30)
    current_hourly = cfg.get("reactions_this_hour", 0)
    use_channel = cfg.get("use_channel_reaction", False)

    highest_msg = last_seen_msg_id

    # Use first available account to fetch messages
    pool = SessionPool()
    account_id = accounts[0]
    try:
        client = await pool.get_client(workspace_id, account_id)
        if not client:
            return last_seen_msg_id

        from telethon.tl.functions.messages import GetHistoryRequest
        from telethon.tl.types import PeerChannel

        entity = await client.get_entity(PeerChannel(channel_id))
        history = await client(GetHistoryRequest(
            peer=entity,
            offset_id=0,
            offset_date=None,
            add_offset=0,
            limit=20,
            max_id=0,
            min_id=last_seen_msg_id,
            hash=0,
        ))

        now = datetime.now(timezone.utc)

        for msg in reversed(history.messages):
            if not hasattr(msg, "id"):
                continue
            if msg.id <= last_seen_msg_id:
                continue
            highest_msg = max(highest_msg, msg.id)

            # Check if within react window
            msg_age = (now - msg.date.replace(tzinfo=timezone.utc)).total_seconds()
            if msg_age > react_within:
                continue

            if current_hourly >= max_hourly:
                break

            # Pick a random account to react with
            react_account_id = random.choice(accounts)

            if use_channel:
                await react_as_channel(client, channel_id, msg.id, emoji, channel_id)
            else:
                react_client = await pool.get_client(workspace_id, react_account_id)
                if react_client:
                    try:
                        from telethon.tl.functions.messages import SendReactionRequest
                        from telethon.tl.types import ReactionEmoji

                        await react_client(SendReactionRequest(
                            peer=entity,
                            msg_id=msg.id,
                            reaction=[ReactionEmoji(emoticon=emoji)],
                        ))
                        current_hourly += 1

                        await log_operation(
                            workspace_id, react_account_id, "reactions_v2",
                            "reaction_sent", "success",
                            f"channel={channel_id} msg={msg.id} emoji={emoji}",
                        )
                    except Exception as react_err:
                        err_name = type(react_err).__name__
                        if "FloodWait" in err_name:
                            wait = getattr(react_err, "seconds", 30)
                            log.warning("reactions_v2: FloodWait %ds on account %d", wait, react_account_id)
                            await asyncio.sleep(min(wait, 120))
                        elif "FrozenMethodInvalid" in err_name:
                            log.warning("reactions_v2: account %d frozen, skipping", react_account_id)
                        else:
                            log.warning("reactions_v2: react error: %s", react_err)
                            await log_operation(
                                workspace_id, react_account_id, "reactions_v2",
                                "reaction_error", "error", str(react_err)[:200],
                            )

            # Human delay between reactions
            await asyncio.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))

        # Update hourly counter
        if current_hourly != cfg.get("reactions_this_hour", 0):
            await _update_hourly_count(cfg["id"], workspace_id, current_hourly)

    except Exception as exc:
        log.warning("reactions_v2: poll_and_react error: %s", exc)

    return highest_msg


async def react_as_channel(
    client: Any,
    channel_id: int,
    msg_id: int,
    emoji: str,
    from_channel_id: int,
) -> None:
    """Premium feature: react from channel entity instead of user account."""
    try:
        from telethon.tl.functions.messages import SendReactionRequest
        from telethon.tl.types import ReactionEmoji, PeerChannel

        entity = await client.get_entity(PeerChannel(channel_id))
        await client(SendReactionRequest(
            peer=entity,
            msg_id=msg_id,
            reaction=[ReactionEmoji(emoticon=emoji)],
        ))
        log.info("reactions_v2: channel reaction sent ch=%d msg=%d emoji=%s", channel_id, msg_id, emoji)
    except Exception as exc:
        log.warning("reactions_v2: channel reaction failed: %s", exc)


# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------


async def add_reaction_blacklist(
    workspace_id: int,
    channel_id: int,
    title: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    """Add a channel to the reaction blacklist."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            entry = ReactionBlacklist(
                workspace_id=workspace_id,
                channel_id=channel_id,
                channel_title=title,
                reason=reason,
            )
            session.add(entry)
            await session.flush()
            result = _serialize_blacklist(entry)
    await log_operation(workspace_id, None, "reactions_v2", "blacklist_added",
                        "success", f"channel={channel_id}")
    return result


async def remove_reaction_blacklist(workspace_id: int, channel_id: int) -> None:
    """Remove a channel from the reaction blacklist."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            stmt = (
                delete(ReactionBlacklist)
                .where(ReactionBlacklist.workspace_id == workspace_id)
                .where(ReactionBlacklist.channel_id == channel_id)
            )
            res = await session.execute(stmt)
            if res.rowcount == 0:
                raise ValueError(f"Blacklist entry for channel {channel_id} not found")
    await log_operation(workspace_id, None, "reactions_v2", "blacklist_removed",
                        "success", f"channel={channel_id}")


async def get_reaction_blacklist(workspace_id: int) -> List[dict]:
    """Get all blacklisted channels for workspace."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            rows = (await session.execute(
                select(ReactionBlacklist)
                .where(ReactionBlacklist.workspace_id == workspace_id)
                .order_by(ReactionBlacklist.created_at.desc())
            )).scalars().all()
            return [_serialize_blacklist(r) for r in rows]


async def is_reaction_blacklisted(workspace_id: int, channel_id: int) -> bool:
    """Check if a channel is blacklisted for reactions."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            row = (await session.execute(
                select(ReactionBlacklist.id)
                .where(ReactionBlacklist.workspace_id == workspace_id)
                .where(ReactionBlacklist.channel_id == channel_id)
                .limit(1)
            )).scalar_one_or_none()
            return row is not None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _load_config(config_id: int, workspace_id: int) -> Optional[dict]:
    """Load a single config by ID."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            row = (await session.execute(
                select(ReactionMonitoringConfig)
                .where(ReactionMonitoringConfig.id == config_id)
                .where(ReactionMonitoringConfig.workspace_id == workspace_id)
            )).scalar_one_or_none()
            return _serialize_config(row) if row else None


async def _update_hourly_count(config_id: int, workspace_id: int, count: int) -> None:
    """Update the reactions_this_hour counter."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            await session.execute(
                update(ReactionMonitoringConfig)
                .where(ReactionMonitoringConfig.id == config_id)
                .values(reactions_this_hour=count)
            )


def _serialize_config(cfg: ReactionMonitoringConfig) -> dict:
    return {
        "id": cfg.id,
        "workspace_id": cfg.workspace_id,
        "channel_id": cfg.channel_id,
        "channel_title": cfg.channel_title,
        "reaction_emoji": cfg.reaction_emoji,
        "react_within_seconds": cfg.react_within_seconds,
        "is_active": cfg.is_active,
        "accounts_assigned": cfg.accounts_assigned,
        "max_reactions_per_hour": cfg.max_reactions_per_hour,
        "reactions_this_hour": cfg.reactions_this_hour,
        "use_channel_reaction": cfg.use_channel_reaction,
        "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
        "is_running": cfg.id in _monitoring_tasks and not _monitoring_tasks[cfg.id].done(),
    }


def _serialize_blacklist(entry: ReactionBlacklist) -> dict:
    return {
        "id": entry.id,
        "workspace_id": entry.workspace_id,
        "channel_id": entry.channel_id,
        "channel_title": entry.channel_title,
        "reason": entry.reason,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }
