"""
NeuroDialogs — orchestrated DM dialog sessions between account pairs.

Each session drives a conversation: account A sends an AI-generated message
to account B's DM, then B replies, repeating for messages_per_session rounds.
All delays and typing simulation are anti-detection safe.

Public API
----------
engine = NeuroDialogs(session_manager)
await engine.start(config_id, workspace_id, tenant_id, db_session)
await engine.stop(config_id, tenant_id, db_session)

Behavioural rules enforced:
  - AntiDetection delays wrap every send step.
  - FloodWaitError → skip the remainder of the current dialog session.
  - FrozenMethodInvalidError → remove frozen account from rotation.
  - One background task per (config_id, tenant_id).
  - Each DB write uses a fresh short-lived session with RLS.
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
from storage.models import Account, DialogConfig
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log


_TaskKey = Tuple[int, int]  # (config_id, tenant_id)

# How long to sleep between full dialog sessions (jitter around session_interval_hours)
_SESSION_JITTER_FRACTION = 0.2

# Seconds to wait after a FloodWait before continuing the outer loop
_FLOOD_WAIT_OUTER_SLEEP = 300


async def _interruptible_sleep(seconds: float, stop_event: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(0.0, seconds))
    except asyncio.TimeoutError:
        pass


class NeuroDialogs:
    """
    Manages dialog-session background tasks for DialogConfig entries.

    One NeuroDialogs instance per process.  Tracks in-memory:
        _tasks[key]       = asyncio.Task
        _stop_events[key] = asyncio.Event
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
        Launch the dialog orchestration loop for *config_id*.

        The caller must have already applied RLS context to *db_session*.
        """
        result = await db_session.execute(
            select(DialogConfig).where(DialogConfig.id == config_id)
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise ValueError(f"DialogConfig {config_id} not found")

        key: _TaskKey = (config_id, tenant_id)

        existing_stop = self._stop_events.get(key)
        if existing_stop is not None:
            existing_stop.set()
        existing_task = self._tasks.get(key)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        stop_event = asyncio.Event()
        self._stop_events[key] = stop_event

        task = asyncio.create_task(
            self._loop(config_id=config_id, tenant_id=tenant_id, stop_event=stop_event),
            name=f"neuro-dialogs-cfg{config_id}-tenant{tenant_id}",
        )
        def _on_dialog_done(t: asyncio.Task, k: str = key) -> None:
            exc = t.exception() if not t.cancelled() else None
            if exc:
                log.error("neuro_dialogs %s failed: %s", k, exc, exc_info=exc)
        task.add_done_callback(_on_dialog_done)
        self._tasks[key] = task

        await db_session.execute(
            update(DialogConfig)
            .where(DialogConfig.id == config_id)
            .values(status="running", updated_at=utcnow())
        )

        log.info(f"NeuroDialogs: started config {config_id} (tenant {tenant_id})")

    async def stop(
        self,
        config_id: int,
        tenant_id: int,
        db_session: AsyncSession,
    ) -> None:
        """
        Stop the dialog loop for *config_id*.

        The caller must have already applied RLS context to *db_session*.
        """
        key: _TaskKey = (config_id, tenant_id)

        stop_event = self._stop_events.pop(key, None)
        if stop_event is not None:
            stop_event.set()

        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
            log.info(f"NeuroDialogs: cancelled task for config {config_id}")

        await db_session.execute(
            update(DialogConfig)
            .where(DialogConfig.id == config_id)
            .values(status="stopped", updated_at=utcnow())
        )

        log.info(f"NeuroDialogs: stopped config {config_id} (tenant {tenant_id})")

    # ------------------------------------------------------------------
    # Internal: outer orchestration loop
    # ------------------------------------------------------------------

    async def _loop(
        self,
        config_id: int,
        tenant_id: int,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Iterate over account pairs and run a dialog session for each.
        Sleeps for session_interval_hours between full round-trips.
        """
        # Track frozen accounts for this run
        frozen_accounts: set[int] = set()

        try:
            while not stop_event.is_set():
                config = await self._load_config(config_id, tenant_id)
                if config is None or config.status == "stopped":
                    break

                pairs: List[List[int]] = list(config.account_pairs or [])
                if not pairs:
                    log.warning(
                        f"NeuroDialogs: config {config_id} has no account pairs, sleeping"
                    )
                    await _interruptible_sleep(600.0, stop_event)
                    continue

                for pair in pairs:
                    if stop_event.is_set():
                        break
                    if len(pair) < 2:
                        continue

                    account_a_id, account_b_id = int(pair[0]), int(pair[1])

                    # Skip pairs that involve a frozen account
                    if account_a_id in frozen_accounts or account_b_id in frozen_accounts:
                        log.debug(
                            f"NeuroDialogs: skipping pair ({account_a_id},{account_b_id}) "
                            f"due to frozen account"
                        )
                        continue

                    await self._dialog_session(
                        pair=(account_a_id, account_b_id),
                        config=config,
                        tenant_id=tenant_id,
                        frozen_accounts=frozen_accounts,
                        stop_event=stop_event,
                    )

                if stop_event.is_set():
                    break

                # Sleep until next session round
                interval_hours = config.session_interval_hours if config.session_interval_hours is not None else 4
                base_sleep = interval_hours * 3600.0
                jitter = base_sleep * _SESSION_JITTER_FRACTION
                sleep_for = random.uniform(
                    max(base_sleep - jitter, 60.0),
                    base_sleep + jitter,
                )
                log.debug(
                    f"NeuroDialogs: config {config_id} sleeping {sleep_for:.0f}s "
                    f"until next session round"
                )
                await _interruptible_sleep(sleep_for, stop_event)

        except asyncio.CancelledError:
            log.info(f"NeuroDialogs: loop for config {config_id} cancelled")
        except Exception as exc:
            log.error(
                f"NeuroDialogs: loop for config {config_id} unrecoverable error: {exc}",
                exc_info=True,
            )
        finally:
            # Clean up in-memory references to prevent unbounded dict growth.
            key: _TaskKey = (config_id, tenant_id)
            self._tasks.pop(key, None)
            self._stop_events.pop(key, None)

        await self._mark_stopped(config_id, tenant_id)

    # ------------------------------------------------------------------
    # Internal: single dialog session between a pair
    # ------------------------------------------------------------------

    async def _dialog_session(
        self,
        pair: Tuple[int, int],
        config: DialogConfig,
        tenant_id: int,
        frozen_accounts: set[int],
        stop_event: asyncio.Event,
    ) -> None:
        """
        Execute one dialog session: A sends, B replies, repeat
        for messages_per_session rounds with anti-detection delays.
        """
        account_a_id, account_b_id = pair
        messages_per_session = config.messages_per_session or 5
        dialog_type = config.dialog_type or "warmup"

        anti_det = AntiDetection(mode="conservative")

        try:
            from telethon.errors import FloodWaitError
        except ImportError:
            return

        client_a = await self._get_client(account_a_id, tenant_id)
        client_b = await self._get_client(account_b_id, tenant_id)

        if client_a is None or client_b is None:
            log.debug(
                f"NeuroDialogs: pair ({account_a_id},{account_b_id}) "
                f"missing client(s), skipping"
            )
            return

        # Obtain the Telegram User entity for B (target for A's messages)
        try:
            entity_b = await client_a.get_entity(
                await self._get_account_phone(account_b_id, tenant_id) or account_b_id
            )
        except Exception as exc:
            log.debug(
                f"NeuroDialogs: cannot resolve entity for account {account_b_id}: {exc}"
            )
            return

        # Obtain the Telegram User entity for A (target for B's messages)
        try:
            entity_a = await client_b.get_entity(
                await self._get_account_phone(account_a_id, tenant_id) or account_a_id
            )
        except Exception as exc:
            log.debug(
                f"NeuroDialogs: cannot resolve entity for account {account_a_id}: {exc}"
            )
            return

        dialog_history: List[str] = []

        for turn in range(messages_per_session):
            if stop_event.is_set():
                break

            # Turn: A sends to B
            text_a = await self._generate_dialog_message(
                turn=turn,
                speaker="A",
                dialog_history=dialog_history,
                dialog_type=dialog_type,
                prompt_template=config.prompt_template,
                tenant_id=tenant_id,
            )
            if not text_a:
                break

            sent = await self._send_dm(
                client=client_a,
                target=entity_b,
                text=text_a,
                anti_det=anti_det,
                sender_id=account_a_id,
                frozen_accounts=frozen_accounts,
                stop_event=stop_event,
            )
            if not sent:
                break

            dialog_history.append(f"A: {text_a}")
            await _interruptible_sleep(
                random.uniform(10.0, 45.0), stop_event
            )

            if stop_event.is_set():
                break

            # Turn: B replies to A
            text_b = await self._generate_dialog_message(
                turn=turn,
                speaker="B",
                dialog_history=dialog_history,
                dialog_type=dialog_type,
                prompt_template=config.prompt_template,
                tenant_id=tenant_id,
            )
            if not text_b:
                break

            sent = await self._send_dm(
                client=client_b,
                target=entity_a,
                text=text_b,
                anti_det=anti_det,
                sender_id=account_b_id,
                frozen_accounts=frozen_accounts,
                stop_event=stop_event,
            )
            if not sent:
                break

            dialog_history.append(f"B: {text_b}")
            await _interruptible_sleep(
                random.uniform(15.0, 60.0), stop_event
            )

        log.info(
            f"NeuroDialogs: dialog session for pair "
            f"({account_a_id},{account_b_id}) completed "
            f"({len(dialog_history)} messages)"
        )

    async def _send_dm(
        self,
        client,
        target,
        text: str,
        anti_det: AntiDetection,
        sender_id: int,
        frozen_accounts: set[int],
        stop_event: asyncio.Event,
    ) -> bool:
        """Send a DM from *client* to *target*.  Returns True on success."""
        try:
            from telethon.errors import FloodWaitError
        except ImportError:
            return False

        await anti_det.simulate_typing(client, target)
        await anti_det.inter_action_delay()

        if stop_event.is_set():
            return False

        try:
            await client.send_message(target, text)
            return True

        except FloodWaitError as exc:
            log.warning(
                f"NeuroDialogs: FloodWait {exc.seconds}s for account {sender_id}, "
                f"skipping dialog session"
            )
            return False

        except Exception as exc:
            if "FrozenMethodInvalid" in type(exc).__name__:
                log.warning(
                    f"NeuroDialogs: account {sender_id} is frozen, "
                    f"removing from rotation"
                )
                frozen_accounts.add(sender_id)
                return False
            log.debug(f"NeuroDialogs: send_dm error for account {sender_id}: {exc}")
            return False

    # ------------------------------------------------------------------
    # AI generation
    # ------------------------------------------------------------------

    async def _generate_dialog_message(
        self,
        turn: int,
        speaker: str,
        dialog_history: List[str],
        dialog_type: str,
        prompt_template: Optional[str],
        tenant_id: int,
    ) -> Optional[str]:
        """Generate one DM turn via route_ai_task."""
        type_context = {
            "warmup": "casual account warm-up, building natural presence",
            "engagement": "brand engagement and discussion",
            "support": "customer-like support interaction",
        }.get(dialog_type, "natural Telegram conversation")

        history_text = "\n".join(dialog_history[-6:]) if dialog_history else "(start of conversation)"

        custom = prompt_template or ""
        system_instruction = (
            f"You are Telegram user {speaker} in a private DM conversation. "
            f"Context: {type_context}. "
            f"Write one short, natural DM message (max 20 words). "
            f"Do not mention AI or bots. "
            f"{custom}".strip()
        )
        full_prompt = (
            f"Conversation so far:\n{history_text}\n\n"
            f"Write the next message from {speaker}."
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
                        max_output_tokens=80,
                        temperature=0.90,
                        surface="dialogs",
                    )

            if not result.ok or not result.parsed:
                return None

            text = (result.parsed or {}).get("text", "")
            return text.strip() if text else None

        except Exception as exc:
            log.warning(f"NeuroDialogs: AI generation failed (turn {turn}): {exc}")
            return None

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _load_config(
        self, config_id: int, tenant_id: int
    ) -> Optional[DialogConfig]:
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    result = await sess.execute(
                        select(DialogConfig).where(DialogConfig.id == config_id)
                    )
                    return result.scalar_one_or_none()
        except Exception as exc:
            log.warning(f"NeuroDialogs: _load_config error: {exc}", exc_info=True)
            return None

    async def _mark_stopped(self, config_id: int, tenant_id: int) -> None:
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    await sess.execute(
                        update(DialogConfig)
                        .where(DialogConfig.id == config_id)
                        .values(status="stopped", updated_at=utcnow())
                    )
        except Exception as exc:
            log.warning(f"NeuroDialogs: _mark_stopped error: {exc}")

    async def _get_account_phone(self, account_id: int, tenant_id: int) -> Optional[str]:
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    result = await sess.execute(
                        select(Account).where(Account.id == account_id)
                    )
                    account = result.scalar_one_or_none()
                    return account.phone if account else None
        except Exception as exc:
            log.warning(f"NeuroDialogs: _get_account_phone error: {exc}")
            return None

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
                    # Cache attributes before session closes to avoid detached instance
                    phone = account.phone
                    user_id = account.user_id

            client = self._session_mgr.get_client(phone)
            if client is not None and client.is_connected():
                return client

            return await self._session_mgr.connect_client_for_action(
                phone,
                user_id=user_id,
            )
        except Exception as exc:
            log.warning(
                f"NeuroDialogs: _get_client [account={account_id}]: {exc}"
            )
            return None
