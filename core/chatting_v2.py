"""
Chatting v2 — advanced chat participation with modes, product promotion,
unified DM inbox, auto-responder, and presets.

Public API
----------
create_chatting_config(workspace_id, **params) -> ChattingConfigV2
update_chatting_config(workspace_id, config_id, **params) -> ChattingConfigV2
should_chat_on_message(config, message_text) -> bool
generate_chat_message(config, channel_context, recent_messages) -> str
get_dm_inbox(workspace_id, limit, offset) -> list[DmInbox]
get_dm_conversation(workspace_id, account_id, peer_id, limit) -> list[DmMessage]
send_dm(workspace_id, account_id, peer_id, text) -> DmMessage
sync_inbox_from_telegram(workspace_id, account_id) -> int
auto_respond_to_dm(workspace_id, account_id, peer_id, incoming_text) -> str | None
save_chatting_preset(workspace_id, name, config_dict) -> ChattingPreset
list_chatting_presets(workspace_id) -> list[ChattingPreset]
load_chatting_preset(workspace_id, preset_id) -> dict
delete_chatting_preset(workspace_id, preset_id) -> bool
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.ai_router import route_ai_task
from core.operation_logger import log_operation
from storage.models import (
    Account,
    AutoResponderConfig,
    ChattingConfigV2,
    ChattingPreset,
    DmInbox,
    DmMessage,
)
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log


# ---------------------------------------------------------------------------
# Chatting Config CRUD
# ---------------------------------------------------------------------------


async def create_chatting_config(workspace_id: int, **params: Any) -> ChattingConfigV2:
    """Create a new chatting v2 config."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            cfg = ChattingConfigV2(workspace_id=workspace_id, **params)
            session.add(cfg)
            await session.flush()
            await session.refresh(cfg)
            await log_operation(
                workspace_id=workspace_id,
                account_id=None,
                module="chatting_v2",
                action="config_created",
                status="success",
                detail=f"Config '{cfg.name}' id={cfg.id} mode={cfg.mode}",
            )
            return cfg


async def update_chatting_config(
    workspace_id: int, config_id: int, **params: Any
) -> Optional[ChattingConfigV2]:
    """Update an existing chatting v2 config. Returns None if not found."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            cfg = (
                await session.execute(
                    select(ChattingConfigV2).where(
                        ChattingConfigV2.id == config_id,
                        ChattingConfigV2.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if cfg is None:
                return None
            for key, value in params.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
            await session.flush()
            await session.refresh(cfg)
            await log_operation(
                workspace_id=workspace_id,
                account_id=None,
                module="chatting_v2",
                action="config_updated",
                status="success",
                detail=f"Config id={config_id} updated",
            )
            return cfg


async def list_chatting_configs(workspace_id: int) -> List[ChattingConfigV2]:
    """List all chatting v2 configs for a workspace."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            rows = (
                await session.execute(
                    select(ChattingConfigV2)
                    .where(ChattingConfigV2.workspace_id == workspace_id)
                    .order_by(ChattingConfigV2.id.desc())
                )
            ).scalars().all()
            return list(rows)


async def delete_chatting_config(workspace_id: int, config_id: int) -> bool:
    """Delete a chatting v2 config. Returns True if deleted."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            result = await session.execute(
                delete(ChattingConfigV2).where(
                    ChattingConfigV2.id == config_id,
                    ChattingConfigV2.workspace_id == workspace_id,
                )
            )
            deleted = result.rowcount > 0
            if deleted:
                await log_operation(
                    workspace_id=workspace_id,
                    account_id=None,
                    module="chatting_v2",
                    action="config_deleted",
                    status="success",
                    detail=f"Config id={config_id} deleted",
                )
            return deleted


# ---------------------------------------------------------------------------
# Message Decision Engine
# ---------------------------------------------------------------------------


async def should_chat_on_message(config: ChattingConfigV2, message_text: str) -> bool:
    """
    Decide whether to post a chat message based on config mode.
    - interval: random probability based on interval_percent
    - keyword_trigger: any keyword present in message_text
    - semantic_match: AI-based topic classification
    """
    mode = config.mode or "interval"

    if mode == "interval":
        percent = config.interval_percent or 10
        return random.random() < (percent / 100.0)

    if mode == "keyword_trigger":
        keywords = config.trigger_keywords or []
        if not keywords:
            return False
        text_lower = message_text.lower()
        return any(kw.lower() in text_lower for kw in keywords)

    if mode == "semantic_match":
        topics = config.semantic_topics or []
        if not topics:
            return False
        try:
            result = await route_ai_task(
                "semantic_match",
                {
                    "message": message_text,
                    "target_topics": topics,
                    "instruction": (
                        "Determine if the message relates to any of the target topics. "
                        "Return JSON: {\"match\": true/false, \"topic\": \"matched topic or null\"}"
                    ),
                },
            )
            if isinstance(result, str):
                parsed = json.loads(result)
            else:
                parsed = result
            return bool(parsed.get("match", False))
        except Exception as exc:
            log.warning("semantic_match failed: %s", exc)
            return False

    return False


async def generate_chat_message(
    config: ChattingConfigV2,
    channel_context: str,
    recent_messages: List[str],
) -> str:
    """Generate an AI chat message using config product context and recent messages."""
    mention_freq = config.mention_frequency or "subtle"
    product_block = ""
    if mention_freq != "never" and config.product_name:
        product_block = (
            f"\nProduct to optionally mention ({mention_freq} frequency):\n"
            f"Name: {config.product_name}\n"
            f"Description: {config.product_description or 'N/A'}\n"
            f"Problems solved: {config.product_problems_solved or 'N/A'}\n"
        )

    context_depth = config.context_depth or 5
    recent_text = "\n".join(recent_messages[-context_depth:]) if recent_messages else "(no recent messages)"

    prompt = (
        "You are a real Telegram user casually chatting in a channel discussion.\n"
        "Write a short, natural Russian-language comment (1-3 sentences) that fits "
        "the ongoing conversation. Do NOT sound like a bot or an ad.\n"
        f"\nChannel context: {channel_context}\n"
        f"\nRecent messages:\n{recent_text}\n"
        f"{product_block}"
        "\nReturn ONLY the message text, nothing else."
    )

    try:
        result = await route_ai_task("assistant_reply", {"prompt": prompt})
        return str(result).strip() if result else ""
    except Exception as exc:
        log.warning("generate_chat_message failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# DM Inbox
# ---------------------------------------------------------------------------


async def get_dm_inbox(
    workspace_id: int, limit: int = 50, offset: int = 0
) -> List[DmInbox]:
    """Get unified DM inbox entries sorted by last_message_at desc."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            rows = (
                await session.execute(
                    select(DmInbox)
                    .where(DmInbox.workspace_id == workspace_id)
                    .order_by(DmInbox.last_message_at.desc().nullslast())
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars().all()
            return list(rows)


async def get_dm_conversation(
    workspace_id: int,
    account_id: int,
    peer_id: int,
    limit: int = 100,
) -> List[DmMessage]:
    """Get DM messages for a specific conversation."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            # Find the inbox entry
            inbox = (
                await session.execute(
                    select(DmInbox).where(
                        DmInbox.workspace_id == workspace_id,
                        DmInbox.account_id == account_id,
                        DmInbox.peer_id == peer_id,
                    )
                )
            ).scalar_one_or_none()
            if inbox is None:
                return []
            rows = (
                await session.execute(
                    select(DmMessage)
                    .where(
                        DmMessage.workspace_id == workspace_id,
                        DmMessage.inbox_id == inbox.id,
                    )
                    .order_by(DmMessage.created_at.asc())
                    .limit(limit)
                )
            ).scalars().all()
            return list(rows)


async def send_dm(
    workspace_id: int,
    account_id: int,
    peer_id: int,
    text: str,
) -> Optional[DmMessage]:
    """Send a DM via Telethon with human delay, save to dm_messages."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            # Verify account exists
            account = (
                await session.execute(
                    select(Account).where(
                        Account.id == account_id,
                        Account.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if account is None:
                return None

            # Find or create inbox entry
            inbox = (
                await session.execute(
                    select(DmInbox).where(
                        DmInbox.workspace_id == workspace_id,
                        DmInbox.account_id == account_id,
                        DmInbox.peer_id == peer_id,
                    )
                )
            ).scalar_one_or_none()
            if inbox is None:
                inbox = DmInbox(
                    workspace_id=workspace_id,
                    account_id=account_id,
                    account_phone=account.phone,
                    peer_id=peer_id,
                )
                session.add(inbox)
                await session.flush()

    # Human delay before sending
    await asyncio.sleep(random.uniform(1.5, 4.0))

    # Attempt Telethon send
    sent_ok = False
    try:
        from core.session_pool import SessionPool
        pool = SessionPool.instance()
        client = await pool.acquire(account_id)
        if client:
            try:
                from telethon.tl.functions.messages import SendMessageRequest
                await client.send_message(peer_id, text)
                sent_ok = True
            finally:
                await pool.release(account_id)
    except Exception as exc:
        log.warning("send_dm Telethon failed account=%s peer=%s: %s", account_id, peer_id, exc)

    # Save message regardless (for manual sends even if Telethon unavailable)
    now = utcnow()
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            inbox = (
                await session.execute(
                    select(DmInbox).where(
                        DmInbox.workspace_id == workspace_id,
                        DmInbox.account_id == account_id,
                        DmInbox.peer_id == peer_id,
                    )
                )
            ).scalar_one_or_none()
            if inbox is None:
                return None

            msg = DmMessage(
                workspace_id=workspace_id,
                inbox_id=inbox.id,
                sender="us",
                text=text,
                created_at=now,
            )
            session.add(msg)

            inbox.last_message_text = text
            inbox.last_message_at = now
            await session.flush()
            await session.refresh(msg)

            await log_operation(
                workspace_id=workspace_id,
                account_id=account_id,
                module="chatting_v2",
                action="dm_sent",
                status="success" if sent_ok else "saved_only",
                detail=f"peer={peer_id} len={len(text)}",
            )
            return msg


async def sync_inbox_from_telegram(workspace_id: int, account_id: int) -> int:
    """
    Fetch recent DMs from Telegram via Telethon, sync to dm_inbox/dm_messages.
    Returns count of new messages synced.
    """
    new_count = 0

    # Verify account
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            account = (
                await session.execute(
                    select(Account).where(
                        Account.id == account_id,
                        Account.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if account is None:
                return 0
            account_phone = account.phone

    try:
        from core.session_pool import SessionPool
        pool = SessionPool.instance()
        client = await pool.acquire(account_id)
        if not client:
            log.warning("sync_inbox: no client for account=%s", account_id)
            return 0
        try:
            dialogs = await client.get_dialogs(limit=30)
            for dialog in dialogs:
                if not dialog.is_user:
                    continue
                peer = dialog.entity
                peer_id = peer.id
                peer_name = getattr(peer, "first_name", "") or ""
                peer_username = getattr(peer, "username", "") or ""

                # Get recent messages
                messages = await client.get_messages(peer, limit=20)

                # Human delay between dialog fetches
                await asyncio.sleep(random.uniform(0.5, 1.5))

                now = utcnow()
                async with async_session() as session:
                    async with session.begin():
                        await apply_session_rls_context(session, tenant_id=workspace_id)

                        inbox = (
                            await session.execute(
                                select(DmInbox).where(
                                    DmInbox.workspace_id == workspace_id,
                                    DmInbox.account_id == account_id,
                                    DmInbox.peer_id == peer_id,
                                )
                            )
                        ).scalar_one_or_none()

                        if inbox is None:
                            inbox = DmInbox(
                                workspace_id=workspace_id,
                                account_id=account_id,
                                account_phone=account_phone,
                                peer_id=peer_id,
                                peer_name=peer_name,
                                peer_username=peer_username,
                                unread_count=dialog.unread_count or 0,
                                created_at=now,
                            )
                            session.add(inbox)
                            await session.flush()
                        else:
                            inbox.peer_name = peer_name
                            inbox.peer_username = peer_username
                            inbox.unread_count = dialog.unread_count or 0

                        # Sync messages
                        for tg_msg in reversed(messages):
                            if not tg_msg.text:
                                continue
                            is_outgoing = tg_msg.out
                            sender = "us" if is_outgoing else "them"
                            msg_time = tg_msg.date

                            # Check if message already exists (simple dedup by time + sender)
                            existing = (
                                await session.execute(
                                    select(DmMessage.id).where(
                                        DmMessage.inbox_id == inbox.id,
                                        DmMessage.sender == sender,
                                        DmMessage.created_at == msg_time,
                                    )
                                )
                            ).scalar_one_or_none()
                            if existing:
                                continue

                            dm = DmMessage(
                                workspace_id=workspace_id,
                                inbox_id=inbox.id,
                                sender=sender,
                                text=tg_msg.text,
                                created_at=msg_time,
                            )
                            session.add(dm)
                            new_count += 1

                        # Update last message
                        if messages and messages[0].text:
                            inbox.last_message_text = messages[0].text
                            inbox.last_message_at = messages[0].date
        finally:
            await pool.release(account_id)
    except Exception as exc:
        log.warning("sync_inbox_from_telegram failed account=%s: %s", account_id, exc)

    await log_operation(
        workspace_id=workspace_id,
        account_id=account_id,
        module="chatting_v2",
        action="inbox_synced",
        status="success",
        detail=f"new_messages={new_count}",
    )
    return new_count


# ---------------------------------------------------------------------------
# Auto-Responder
# ---------------------------------------------------------------------------


async def auto_respond_to_dm(
    workspace_id: int,
    account_id: int,
    peer_id: int,
    incoming_text: str,
) -> Optional[str]:
    """
    AI generates a response using product knowledge, sends via Telethon.
    Returns the response text or None if not configured/quota exhausted.
    """
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            cfg = (
                await session.execute(
                    select(AutoResponderConfig).where(
                        AutoResponderConfig.workspace_id == workspace_id,
                        AutoResponderConfig.account_id == account_id,
                        AutoResponderConfig.is_active == True,  # noqa: E712
                    )
                )
            ).scalar_one_or_none()
            if cfg is None:
                return None
            if cfg.responses_today >= cfg.max_responses_per_day:
                log.info("auto_respond quota exhausted account=%s", account_id)
                return None

    # Generate AI response
    product_block = ""
    if cfg.product_name:
        product_block = (
            f"\nProduct knowledge:\n"
            f"Name: {cfg.product_name}\n"
            f"Description: {cfg.product_description or 'N/A'}\n"
        )

    prompt = (
        f"You are a helpful {cfg.tone or 'friendly'} assistant replying to a Telegram DM.\n"
        f"The user wrote: \"{incoming_text}\"\n"
        f"{product_block}\n"
        "Write a short, natural reply in Russian (1-3 sentences). "
        "Do not sound like a bot. Return ONLY the reply text."
    )

    try:
        result = await route_ai_task("assistant_reply", {"prompt": prompt})
        response_text = str(result).strip() if result else None
    except Exception as exc:
        log.warning("auto_respond AI failed: %s", exc)
        return None

    if not response_text:
        return None

    # Send via DM
    await send_dm(workspace_id, account_id, peer_id, response_text)

    # Increment daily counter
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            await session.execute(
                update(AutoResponderConfig)
                .where(AutoResponderConfig.id == cfg.id)
                .values(responses_today=AutoResponderConfig.responses_today + 1)
            )

    await log_operation(
        workspace_id=workspace_id,
        account_id=account_id,
        module="chatting_v2",
        action="auto_responded",
        status="success",
        detail=f"peer={peer_id} len={len(response_text)}",
    )
    return response_text


# ---------------------------------------------------------------------------
# Auto-Responder CRUD
# ---------------------------------------------------------------------------


async def create_auto_responder(workspace_id: int, **params: Any) -> AutoResponderConfig:
    """Create a new auto-responder config."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            cfg = AutoResponderConfig(workspace_id=workspace_id, **params)
            session.add(cfg)
            await session.flush()
            await session.refresh(cfg)
            return cfg


async def list_auto_responders(workspace_id: int) -> List[AutoResponderConfig]:
    """List all auto-responder configs for a workspace."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            rows = (
                await session.execute(
                    select(AutoResponderConfig)
                    .where(AutoResponderConfig.workspace_id == workspace_id)
                    .order_by(AutoResponderConfig.id.desc())
                )
            ).scalars().all()
            return list(rows)


async def update_auto_responder(
    workspace_id: int, config_id: int, **params: Any
) -> Optional[AutoResponderConfig]:
    """Update an auto-responder config."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            cfg = (
                await session.execute(
                    select(AutoResponderConfig).where(
                        AutoResponderConfig.id == config_id,
                        AutoResponderConfig.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if cfg is None:
                return None
            for key, value in params.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
            await session.flush()
            await session.refresh(cfg)
            return cfg


async def delete_auto_responder(workspace_id: int, config_id: int) -> bool:
    """Delete an auto-responder config."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            result = await session.execute(
                delete(AutoResponderConfig).where(
                    AutoResponderConfig.id == config_id,
                    AutoResponderConfig.workspace_id == workspace_id,
                )
            )
            return result.rowcount > 0


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


async def save_chatting_preset(
    workspace_id: int, name: str, config_dict: Dict[str, Any]
) -> ChattingPreset:
    """Save a chatting preset."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            preset = ChattingPreset(
                workspace_id=workspace_id,
                name=name,
                config=config_dict,
            )
            session.add(preset)
            await session.flush()
            await session.refresh(preset)
            await log_operation(
                workspace_id=workspace_id,
                account_id=None,
                module="chatting_v2",
                action="preset_saved",
                status="success",
                detail=f"Preset '{name}' id={preset.id}",
            )
            return preset


async def list_chatting_presets(workspace_id: int) -> List[ChattingPreset]:
    """List all chatting presets for a workspace."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            rows = (
                await session.execute(
                    select(ChattingPreset)
                    .where(ChattingPreset.workspace_id == workspace_id)
                    .order_by(ChattingPreset.id.desc())
                )
            ).scalars().all()
            return list(rows)


async def load_chatting_preset(workspace_id: int, preset_id: int) -> Optional[Dict[str, Any]]:
    """Load a chatting preset config dict."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            preset = (
                await session.execute(
                    select(ChattingPreset).where(
                        ChattingPreset.id == preset_id,
                        ChattingPreset.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if preset is None:
                return None
            return preset.config


async def delete_chatting_preset(workspace_id: int, preset_id: int) -> bool:
    """Delete a chatting preset."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            result = await session.execute(
                delete(ChattingPreset).where(
                    ChattingPreset.id == preset_id,
                    ChattingPreset.workspace_id == workspace_id,
                )
            )
            deleted = result.rowcount > 0
            if deleted:
                await log_operation(
                    workspace_id=workspace_id,
                    account_id=None,
                    module="chatting_v2",
                    action="preset_deleted",
                    status="success",
                    detail=f"Preset id={preset_id} deleted",
                )
            return deleted
