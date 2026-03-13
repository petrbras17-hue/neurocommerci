"""
NeuroChatting — background chat loop that posts AI-generated messages
to Telegram channels to simulate organic audience engagement.

Public API
----------
engine = NeuroChatting(session_manager)
await engine.start(config_id, workspace_id, tenant_id, db_session)
await engine.stop(config_id, tenant_id, db_session)

Behavioural rules enforced:
  - max_messages_per_hour hard ceiling per config.
  - Random account and channel selection each iteration.
  - AntiDetection delays applied before every post.
  - FloodWaitError → skip current account, continue loop after jitter.
  - FrozenMethodInvalidError → remove account from rotation for this run.
  - In-memory task tracking (one task per config_id × tenant_id).
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.ai_router import route_ai_task
from core.anti_detection import AntiDetection
from storage.models import Account, ChattingConfig
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log


# Unique key for an in-memory task: (config_id, tenant_id)
_TaskKey = Tuple[int, int]

# Interval between monitoring loop iterations (seconds)
_LOOP_ITER_SLEEP_MIN = 30
_LOOP_ITER_SLEEP_MAX = 90

# How long to sleep when the hourly quota is exhausted (seconds)
_QUOTA_SLEEP = 120


async def _interruptible_sleep(seconds: float, stop_event: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(0.0, seconds))
    except asyncio.TimeoutError:
        pass


class NeuroChatting:
    """
    Manages background chat loops for ChattingConfig entries.

    One NeuroChatting instance per process.  Tracks in-memory:
        _tasks[(_config_id, _tenant_id)] = asyncio.Task
        _stop_events[(_config_id, _tenant_id)] = asyncio.Event
    """

    def __init__(self, session_manager=None) -> None:
        self._session_mgr = session_manager
        self._tasks: Dict[_TaskKey, asyncio.Task] = {}
        self._stop_events: Dict[_TaskKey, asyncio.Event] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(
        self,
        config_id: int,
        workspace_id: int,
        tenant_id: int,
        db_session: AsyncSession,
    ) -> None:
        """
        Launch a background chat loop for *config_id*.

        The caller must have already applied RLS context to *db_session*.
        """
        result = await db_session.execute(
            select(ChattingConfig).where(ChattingConfig.id == config_id)
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise ValueError(f"ChattingConfig {config_id} not found")

        key: _TaskKey = (config_id, tenant_id)

        # Cancel any existing task for this key
        existing_stop = self._stop_events.get(key)
        if existing_stop is not None:
            existing_stop.set()
        existing_task = self._tasks.get(key)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        stop_event = asyncio.Event()
        self._stop_events[key] = stop_event

        task = asyncio.create_task(
            self._chat_loop(config_id=config_id, tenant_id=tenant_id, stop_event=stop_event),
            name=f"neuro-chatting-cfg{config_id}-tenant{tenant_id}",
        )
        self._tasks[key] = task

        await db_session.execute(
            update(ChattingConfig)
            .where(ChattingConfig.id == config_id)
            .values(status="running", updated_at=utcnow())
        )

        log.info(
            f"NeuroChatting: started config {config_id} (tenant {tenant_id})"
        )

    async def stop(
        self,
        config_id: int,
        tenant_id: int,
        db_session: AsyncSession,
    ) -> None:
        """
        Stop the background chat loop for *config_id*.

        The caller must have already applied RLS context to *db_session*.
        """
        key: _TaskKey = (config_id, tenant_id)

        stop_event = self._stop_events.pop(key, None)
        if stop_event is not None:
            stop_event.set()

        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
            log.info(f"NeuroChatting: cancelled task for config {config_id}")

        await db_session.execute(
            update(ChattingConfig)
            .where(ChattingConfig.id == config_id)
            .values(status="stopped", updated_at=utcnow())
        )

        log.info(f"NeuroChatting: stopped config {config_id} (tenant {tenant_id})")

    # ------------------------------------------------------------------
    # Internal: background chat loop
    # ------------------------------------------------------------------

    async def _chat_loop(
        self,
        config_id: int,
        tenant_id: int,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Main loop: pick a random account and channel, generate a reply via AI,
        post it to Telegram with anti-detection delays, then sleep.
        """
        # Per-loop hourly rate bucket: (hour_int, messages_sent_this_hour)
        hour_bucket: Tuple[int, int] = (_utc_hour(), 0)

        # Track accounts that became frozen during this run so we stop retrying them
        frozen_accounts: set[int] = set()

        try:
            while not stop_event.is_set():
                config = await self._load_config(config_id, tenant_id)
                if config is None:
                    log.warning(
                        f"NeuroChatting: config {config_id} disappeared, stopping loop"
                    )
                    break

                if config.status == "stopped":
                    break

                # Refresh hourly bucket
                current_hour = _utc_hour()
                bucket_hour, bucket_count = hour_bucket
                if bucket_hour != current_hour:
                    hour_bucket = (current_hour, 0)
                    bucket_count = 0

                max_per_hour = config.max_messages_per_hour or 5
                if bucket_count >= max_per_hour:
                    log.debug(
                        f"NeuroChatting: config {config_id} hit hourly limit "
                        f"({max_per_hour}), sleeping"
                    )
                    await _interruptible_sleep(_QUOTA_SLEEP, stop_event)
                    continue

                # Select a random active account from the config
                account_ids: List[int] = list(config.account_ids or [])
                available = [a for a in account_ids if a not in frozen_accounts]
                if not available:
                    log.warning(
                        f"NeuroChatting: config {config_id} has no available accounts"
                    )
                    await _interruptible_sleep(300.0, stop_event)
                    continue

                account_id = random.choice(available)
                client = await self._get_client(account_id, tenant_id)
                if client is None:
                    await _interruptible_sleep(60.0, stop_event)
                    continue

                # Select a random target channel
                channels: List[str] = list(config.target_channels or [])
                if not channels:
                    await _interruptible_sleep(60.0, stop_event)
                    continue

                channel_id = random.choice(channels)

                # Attempt to post a chat message
                posted = await self._post_chat_message(
                    client=client,
                    account_id=account_id,
                    channel_id=channel_id,
                    config=config,
                    tenant_id=tenant_id,
                    frozen_accounts=frozen_accounts,
                    stop_event=stop_event,
                )

                if posted:
                    bucket_count += 1
                    hour_bucket = (current_hour, bucket_count)

                # Anti-detection inter-iteration sleep
                delay_min = config.min_delay_seconds or 120
                delay_max = config.max_delay_seconds or 600
                sleep_for = random.uniform(
                    min(delay_min, delay_max),
                    max(delay_min, delay_max),
                )
                await _interruptible_sleep(sleep_for, stop_event)

        except asyncio.CancelledError:
            log.info(f"NeuroChatting: loop for config {config_id} cancelled")
        except Exception as exc:
            log.error(
                f"NeuroChatting: loop for config {config_id} unrecoverable error: {exc}",
                exc_info=True,
            )
        finally:
            # Clean up in-memory references to prevent unbounded dict growth.
            key: _TaskKey = (config_id, tenant_id)
            self._tasks.pop(key, None)
            self._stop_events.pop(key, None)

        # Mark config stopped if the loop exits naturally
        await self._mark_stopped(config_id, tenant_id)

    async def _post_chat_message(
        self,
        client,
        account_id: int,
        channel_id: str,
        config: ChattingConfig,
        tenant_id: int,
        frozen_accounts: set[int],
        stop_event: asyncio.Event,
    ) -> bool:
        """
        Fetch recent messages for context, generate a reply via AI,
        then post it.  Returns True on successful post.
        """
        try:
            from telethon.errors import FloodWaitError
        except ImportError:
            return False

        anti_det = AntiDetection(mode=config.mode or "conservative")

        try:
            entity = await client.get_entity(channel_id)
            recent = await client.get_messages(entity, limit=5)
            context_text = "\n".join(
                (m.text or "")[:300] for m in reversed(recent) if m.text
            )
        except Exception as exc:
            log.debug(
                f"NeuroChatting: failed to fetch context for {channel_id}: {exc}"
            )
            return False

        if stop_event.is_set():
            return False

        # Generate reply via AI
        reply_text = await self._generate_chat_message(
            context_text=context_text,
            config=config,
            tenant_id=tenant_id,
        )
        if not reply_text:
            return False

        # Anti-detection: simulate typing before posting
        await anti_det.simulate_typing(client, entity)
        await anti_det.inter_action_delay()

        if stop_event.is_set():
            return False

        try:
            await client.send_message(entity, reply_text)
            log.info(
                f"NeuroChatting: account {account_id} posted to {channel_id}: "
                f"{reply_text[:60]!r}"
            )
            return True

        except FloodWaitError as exc:
            log.warning(
                f"NeuroChatting: FloodWait {exc.seconds}s on account {account_id}, skipping"
            )
            return False

        except Exception as exc:
            if "FrozenMethodInvalid" in type(exc).__name__:
                log.warning(
                    f"NeuroChatting: account {account_id} is frozen, removing from rotation"
                )
                frozen_accounts.add(account_id)
                return False
            log.debug(f"NeuroChatting: post error for account {account_id}: {exc}")
            return False

    async def _generate_chat_message(
        self,
        context_text: str,
        config: ChattingConfig,
        tenant_id: int,
    ) -> Optional[str]:
        """Generate a contextual chat message via route_ai_task."""
        prompt_template = config.prompt_template or ""
        system_instruction = (
            "You are an active Telegram channel participant. "
            "Write a short, natural, conversational message that fits the context. "
            "Keep it under 30 words. Do not mention AI or bots. "
            f"{prompt_template}".strip()
        )
        full_prompt = (
            f"Recent channel messages:\n{context_text[:1500]}\n\n"
            "Write one short natural reply or comment for this conversation."
        )

        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    result = await route_ai_task(
                        sess,
                        task_type="farm_comment",
                        prompt=full_prompt,
                        system_instruction=system_instruction,
                        tenant_id=tenant_id,
                        max_output_tokens=100,
                        temperature=0.88,
                        surface="chatting",
                    )

            if not result.ok or not result.parsed:
                return None

            text = (result.parsed or {}).get("text", "")
            return text.strip() if text else None

        except Exception as exc:
            log.warning(f"NeuroChatting: AI generation failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _load_config(
        self, config_id: int, tenant_id: int
    ) -> Optional[ChattingConfig]:
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    result = await sess.execute(
                        select(ChattingConfig).where(ChattingConfig.id == config_id)
                    )
                    return result.scalar_one_or_none()
        except Exception as exc:
            log.warning(f"NeuroChatting: _load_config error: {exc}", exc_info=True)
            return None

    async def _mark_stopped(self, config_id: int, tenant_id: int) -> None:
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    await sess.execute(
                        update(ChattingConfig)
                        .where(ChattingConfig.id == config_id)
                        .values(status="stopped", updated_at=utcnow())
                    )
        except Exception as exc:
            log.warning(f"NeuroChatting: _mark_stopped error: {exc}")

    async def _get_client(self, account_id: int, tenant_id: int):
        if self._session_mgr is None:
            return None
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    result = await sess.execute(
                        select(Account).where(Account.id == account_id)
                    )
                    account = result.scalar_one_or_none()

            if account is None:
                return None

            client = self._session_mgr.get_client(account.phone)
            if client is not None and client.is_connected():
                return client

            return await self._session_mgr.connect_client_for_action(
                account.phone,
                user_id=account.user_id,
            )
        except Exception as exc:
            log.warning(
                f"NeuroChatting: _get_client [account={account_id}]: {exc}"
            )
            return None


# ------------------------------------------------------------------
# Module helpers
# ------------------------------------------------------------------


def _utc_hour() -> int:
    from datetime import timezone
    return datetime.now(timezone.utc).hour
