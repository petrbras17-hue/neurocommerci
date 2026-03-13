"""
WarmupEngine — per-account warmup session orchestrator.

Each WarmupConfig can have multiple accounts assigned.  For each account a
WarmupSession row is created (or reused) and a background asyncio.Task drives
the actual Telegram activity loop.

Public API
----------
engine = WarmupEngine(session_pool=None)
await engine.start_warmup(config_id, account_ids, tenant_id, db_session)
await engine.stop_warmup(config_id, tenant_id, db_session)
await engine.run_warmup_session(session_id, db_session)         # sync/test path
sessions = await engine.list_sessions(config_id, tenant_id, db_session)

The engine instance is expected to be a singleton per process (see ops_api.py
lifespan or a dedicated module-level instance).

Safety rules enforced:
  - Active-hours gate checked on every iteration.
  - Hourly action-rate bucket: safety_limit_actions_per_hour is hard ceiling.
  - FloodWaitError -> quarantine the account + call lifecycle.on_flood_wait().
  - FrozenMethodInvalidError -> call lifecycle.on_frozen() then stop session.
  - SessionDeadError -> call lifecycle.on_session_dead() then stop session.
  - Session completes all planned actions -> call lifecycle.on_warmup_complete().
  - NEVER calls send_code_request.
  - Clients are always released in a finally block via SessionPool.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.account_lifecycle import AccountLifecycle
from core.anti_detection import AntiDetection
from core.session_pool import PoolCapacityError, SessionDeadError, SessionPool
from storage.models import Account, AccountHealthScore, WarmupConfig, WarmupSession
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log


# ------------------------------------------------------------------
# Status constants
# ------------------------------------------------------------------
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# Interval between rate-limit bucket refresh checks (seconds)
_RATE_CHECK_INTERVAL = 60

# Popular fallback read targets when target_channels is empty
_FALLBACK_READ_CHANNELS = [
    "durov",
    "telegram",
    "tginfo",
]


# ------------------------------------------------------------------
# Internal helper: interruptible sleep
# ------------------------------------------------------------------


async def _interruptible_sleep(seconds: float, stop_event: asyncio.Event) -> None:
    """Sleep for *seconds* but wake immediately when *stop_event* is set."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(0.0, seconds))
    except asyncio.TimeoutError:
        pass


# ------------------------------------------------------------------
# Active-hours helpers
# ------------------------------------------------------------------


def _utc_hour() -> int:
    return datetime.now(timezone.utc).hour


def _within_active_hours(start: int, end: int) -> bool:
    """
    Return True if the current UTC hour falls inside [start, end).

    Handles wrap-around midnight (e.g. start=22, end=8).
    """
    hour = _utc_hour()
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


# ------------------------------------------------------------------
# WarmupEngine
# ------------------------------------------------------------------


class WarmupEngine:
    """
    Manages all active warmup background asyncio tasks.

    One WarmupEngine per process.  Tracks in-memory:
        _tasks[config_id][account_id] = asyncio.Task
        _stop_events[config_id][account_id] = asyncio.Event
    """

    def __init__(
        self,
        session_pool: Optional[SessionPool] = None,
        # Legacy parameter kept for backward compat; ignored when session_pool is given.
        session_manager=None,
    ) -> None:
        # {config_id: {account_id: Task}}
        self._tasks: Dict[int, Dict[int, asyncio.Task]] = {}
        # {config_id: {account_id: asyncio.Event}}  — stop signals
        self._stop_events: Dict[int, Dict[int, asyncio.Event]] = {}
        # Prefer the new SessionPool; fall back to legacy manager for compat.
        self._session_pool: Optional[SessionPool] = session_pool
        # Keep legacy reference so existing callers that pass session_manager= still work.
        self._session_mgr = session_manager

    # ------------------------------------------------------------------
    # Public lifecycle API
    # ------------------------------------------------------------------

    async def start_warmup(
        self,
        config_id: int,
        account_ids: List[int],
        tenant_id: int,
        db_session: AsyncSession,
    ) -> List[int]:
        """
        Create WarmupSession rows for *account_ids* and launch a background
        task per account.

        The caller must have already applied RLS context to *db_session*.

        Returns the list of WarmupSession IDs that were created or restarted.
        """
        result = await db_session.execute(
            select(WarmupConfig).where(WarmupConfig.id == config_id)
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise ValueError(f"WarmupConfig {config_id} not found")

        if config_id not in self._tasks:
            self._tasks[config_id] = {}
        if config_id not in self._stop_events:
            self._stop_events[config_id] = {}

        session_ids: List[int] = []

        for account_id in account_ids:
            # Reuse an existing non-terminal WarmupSession if present.
            existing = await db_session.execute(
                select(WarmupSession).where(
                    WarmupSession.warmup_id == config_id,
                    WarmupSession.account_id == account_id,
                    WarmupSession.tenant_id == tenant_id,
                    WarmupSession.status.in_([STATUS_PENDING, STATUS_RUNNING]),
                )
            )
            ws = existing.scalar_one_or_none()

            if ws is None:
                ws = WarmupSession(
                    tenant_id=tenant_id,
                    warmup_id=config_id,
                    account_id=account_id,
                    status=STATUS_PENDING,
                    actions_performed=0,
                )
                db_session.add(ws)
                await db_session.flush()

            session_ids.append(ws.id)

            # Cancel any previous task for this account.
            old_stop = self._stop_events[config_id].get(account_id)
            if old_stop is not None:
                old_stop.set()

            old_task = self._tasks[config_id].get(account_id)
            if old_task and not old_task.done():
                old_task.cancel()

            # Create a fresh stop event and task.
            stop_event = asyncio.Event()
            self._stop_events[config_id][account_id] = stop_event

            task = asyncio.create_task(
                self._run_warmup_session(
                    warmup_session_id=ws.id,
                    config_id=config_id,
                    account_id=account_id,
                    tenant_id=tenant_id,
                    stop_event=stop_event,
                ),
                name=f"warmup-cfg{config_id}-acc{account_id}",
            )
            self._tasks[config_id][account_id] = task

        # Mark config as running.
        await db_session.execute(
            update(WarmupConfig)
            .where(WarmupConfig.id == config_id)
            .values(status="running", updated_at=utcnow())
        )

        log.info(
            f"WarmupEngine: started {len(account_ids)} session(s) "
            f"for config {config_id} (tenant {tenant_id})"
        )
        return session_ids

    async def stop_warmup(
        self,
        config_id: int,
        tenant_id: int,
        db_session: AsyncSession,
    ) -> None:
        """
        Signal all background tasks for *config_id* to stop and mark the
        config + in-flight sessions as stopped/completed.

        The caller must have already applied RLS context to *db_session*.
        """
        # Signal and cancel tasks.
        stop_events = self._stop_events.pop(config_id, {})
        for account_id, ev in stop_events.items():
            ev.set()

        account_tasks = self._tasks.pop(config_id, {})
        for account_id, task in account_tasks.items():
            if not task.done():
                task.cancel()
                log.info(
                    f"WarmupEngine: cancelled task cfg={config_id} acc={account_id}"
                )

        # Persist: flip running sessions to completed, config to stopped.
        await db_session.execute(
            update(WarmupSession)
            .where(
                WarmupSession.warmup_id == config_id,
                WarmupSession.tenant_id == tenant_id,
                WarmupSession.status == STATUS_RUNNING,
            )
            .values(status=STATUS_COMPLETED, completed_at=utcnow())
        )
        await db_session.execute(
            update(WarmupConfig)
            .where(WarmupConfig.id == config_id)
            .values(status="stopped", updated_at=utcnow())
        )

        log.info(
            f"WarmupEngine: stopped config {config_id} (tenant {tenant_id})"
        )

    async def list_sessions(
        self,
        config_id: int,
        tenant_id: int,
        db_session: AsyncSession,
    ) -> List[WarmupSession]:
        """
        Return all WarmupSession rows for *config_id*.

        The caller must have already applied RLS context to *db_session*.
        """
        result = await db_session.execute(
            select(WarmupSession).where(
                WarmupSession.warmup_id == config_id,
                WarmupSession.tenant_id == tenant_id,
            )
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Synchronous / test-friendly single-session runner
    # (same logic as the background loop but driven from the caller's session)
    # ------------------------------------------------------------------

    async def run_warmup_session(
        self,
        session_id: int,
        db_session: AsyncSession,
    ) -> dict:
        """
        Execute one warmup session synchronously (useful for cron or tests).

        The caller must have already applied RLS context to *db_session*.
        """
        ws = await db_session.get(WarmupSession, session_id)
        if ws is None:
            raise ValueError(f"WarmupSession {session_id} not found")

        config = await db_session.get(WarmupConfig, ws.warmup_id)
        if config is None:
            raise ValueError(f"WarmupConfig {ws.warmup_id} not found")

        if not _within_active_hours(
            config.active_hours_start or settings.ACCOUNT_SLEEP_END_HOUR,
            config.active_hours_end or settings.ACCOUNT_SLEEP_START_HOUR,
        ):
            return {
                "session_id": session_id,
                "skipped": True,
                "reason": "outside_active_hours",
            }

        ws.status = STATUS_RUNNING
        ws.started_at = utcnow()
        await db_session.flush()

        anti_det = AntiDetection(mode=config.mode or "conservative")
        limit = config.safety_limit_actions_per_hour or 5
        actions_done = 0

        account = await db_session.get(Account, ws.account_id)
        if account is None:
            ws.status = STATUS_FAILED
            ws.completed_at = utcnow()
            await db_session.flush()
            return {
                "session_id": session_id,
                "status": STATUS_FAILED,
                "reason": "account_not_found",
            }

        account_id = ws.account_id

        # Acquire a Telethon client from SessionPool (preferred) or legacy manager.
        client = None
        client_from_pool = False

        if self._session_pool is not None:
            try:
                client = await self._session_pool.get_client(
                    account_id, db_session=db_session
                )
                client_from_pool = True
            except SessionDeadError as exc:
                log.warning(
                    f"WarmupEngine: session dead for account {account_id}: {exc}"
                )
                ws.status = STATUS_FAILED
                ws.completed_at = utcnow()
                await db_session.flush()
                # Lifecycle: mark account as dead
                lifecycle = AccountLifecycle(db_session)
                try:
                    await lifecycle.on_session_dead(account_id)
                except Exception as lc_exc:
                    log.debug(
                        f"WarmupEngine: lifecycle.on_session_dead skipped: {lc_exc}"
                    )
                return {
                    "session_id": session_id,
                    "status": STATUS_FAILED,
                    "reason": "session_dead",
                }
            except PoolCapacityError as exc:
                log.warning(
                    f"WarmupEngine: pool full for account {account_id}: {exc}"
                )
                ws.status = STATUS_FAILED
                ws.completed_at = utcnow()
                await db_session.flush()
                return {
                    "session_id": session_id,
                    "status": STATUS_FAILED,
                    "reason": "pool_capacity",
                }
        elif self._session_mgr is not None:
            client = self._session_mgr.get_client(account.phone)

        if client is None:
            ws.status = STATUS_FAILED
            ws.completed_at = utcnow()
            await db_session.flush()
            return {
                "session_id": session_id,
                "status": STATUS_FAILED,
                "reason": "no_client",
            }

        target_channels: List[str] = []
        raw = config.target_channels
        if isinstance(raw, list):
            target_channels = [str(c) for c in raw if c]

        lifecycle = AccountLifecycle(db_session)

        try:
            if config.enable_read_channels and actions_done < limit:
                await self._do_read_channel(client, anti_det, config)
                actions_done += 1
                await anti_det.inter_action_delay()

            if config.enable_reactions and target_channels and actions_done < limit:
                reacted = await self._do_reaction(client, anti_det, config)
                actions_done += int(reacted)
                await anti_det.inter_action_delay()

            if config.enable_dialogs_between_accounts and actions_done < limit:
                await self._do_dialog_read(client, anti_det)
                actions_done += 1

        except _QuarantineError as exc:
            log.warning(
                f"WarmupEngine: session {session_id} flood-wait {exc.seconds}s"
            )
            ws.status = STATUS_FAILED
            ws.completed_at = utcnow()
            await db_session.flush()
            try:
                await lifecycle.on_flood_wait(account_id, exc.seconds)
            except Exception as lc_exc:
                log.debug(
                    f"WarmupEngine: lifecycle.on_flood_wait skipped: {lc_exc}"
                )
            return {
                "session_id": session_id,
                "status": STATUS_FAILED,
                "reason": "flood_wait",
                "actions_performed": actions_done,
            }
        except _FrozenError:
            log.warning(f"WarmupEngine: session {session_id} account frozen")
            ws.status = STATUS_FAILED
            ws.completed_at = utcnow()
            await db_session.flush()
            try:
                await lifecycle.on_frozen(account_id)
            except Exception as lc_exc:
                log.debug(
                    f"WarmupEngine: lifecycle.on_frozen skipped: {lc_exc}"
                )
            return {
                "session_id": session_id,
                "status": STATUS_FAILED,
                "reason": "account_frozen",
            }
        finally:
            if client_from_pool and self._session_pool is not None:
                await self._session_pool.release_client(account_id)

        # All planned actions completed — advance lifecycle.
        try:
            await lifecycle.on_warmup_complete(account_id)
        except Exception as lc_exc:
            log.debug(
                f"WarmupEngine: lifecycle.on_warmup_complete skipped: {lc_exc}"
            )

        ws.status = STATUS_COMPLETED
        ws.actions_performed = actions_done
        ws.completed_at = utcnow()
        ws.next_session_at = utcnow() + timedelta(
            hours=config.interval_between_sessions_hours or 6
        )
        await db_session.flush()

        log.info(
            f"WarmupEngine: session {session_id} completed ({actions_done} actions)"
        )
        return {
            "session_id": session_id,
            "status": STATUS_COMPLETED,
            "actions_performed": actions_done,
        }

    # ------------------------------------------------------------------
    # Internal: long-running background loop (one per account)
    # ------------------------------------------------------------------

    async def _run_warmup_session(
        self,
        warmup_session_id: int,
        config_id: int,
        account_id: int,
        tenant_id: int,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Drive a single account through its warmup window.

        Opens its own short-lived DB sessions for every write to avoid
        holding long-lived transactions.
        """
        actions_performed = 0

        await self._set_session_status(
            warmup_session_id,
            tenant_id,
            STATUS_RUNNING,
            started_at=utcnow(),
        )

        try:
            config = await self._load_config(config_id, tenant_id)
            if config is None:
                log.warning(
                    f"WarmupEngine: config {config_id} not found, "
                    f"aborting session {warmup_session_id}"
                )
                await self._set_session_status(
                    warmup_session_id, tenant_id, STATUS_FAILED
                )
                return

            anti_detection = AntiDetection(mode=config.mode or "conservative")
            deadline = utcnow() + timedelta(
                minutes=config.warmup_duration_minutes or 30
            )

            # Hourly rate-limit bucket: (hour, count)
            _hour_bucket: Tuple[int, int] = (_utc_hour(), 0)

            while utcnow() < deadline and not stop_event.is_set():
                # Active-hours gate.
                if not _within_active_hours(
                    config.active_hours_start or settings.ACCOUNT_SLEEP_END_HOUR,
                    config.active_hours_end or settings.ACCOUNT_SLEEP_START_HOUR,
                ):
                    log.debug(
                        f"WarmupEngine: session {warmup_session_id} "
                        f"outside active hours, sleeping 5 min"
                    )
                    await _interruptible_sleep(300.0, stop_event)
                    continue

                # Hourly rate limit.
                current_hour = _utc_hour()
                bucket_hour, bucket_count = _hour_bucket
                if bucket_hour != current_hour:
                    _hour_bucket = (current_hour, 0)
                    bucket_count = 0

                limit = config.safety_limit_actions_per_hour or 5
                if bucket_count >= limit:
                    log.debug(
                        f"WarmupEngine: session {warmup_session_id} hit "
                        f"hourly limit ({limit}), waiting"
                    )
                    await _interruptible_sleep(_RATE_CHECK_INTERVAL, stop_event)
                    continue

                # Resolve Telethon client via SessionPool (preferred) or legacy.
                client = await self._get_client(account_id, tenant_id)
                if client is None:
                    log.warning(
                        f"WarmupEngine: no Telethon client for account "
                        f"{account_id}, retrying in 60 s"
                    )
                    await _interruptible_sleep(60.0, stop_event)
                    continue

                # Dispatch one action; always release pool client afterward.
                action_done = False
                client_from_pool = self._session_pool is not None
                try:
                    action_done = await self._execute_one_action(
                        client=client,
                        config=config,
                        anti_detection=anti_detection,
                        stop_event=stop_event,
                    )
                except _FrozenError:
                    log.warning(
                        f"WarmupEngine: account {account_id} is frozen, "
                        f"stopping session {warmup_session_id}"
                    )
                    await self._set_session_status(
                        warmup_session_id,
                        tenant_id,
                        STATUS_FAILED,
                        completed_at=utcnow(),
                    )
                    await self._run_lifecycle(
                        account_id, tenant_id, "frozen"
                    )
                    return
                except _QuarantineError as exc:
                    log.warning(
                        f"WarmupEngine: account {account_id} flood-wait "
                        f"{exc.seconds}s, quarantining"
                    )
                    await self._quarantine_account(account_id, tenant_id, exc.seconds)
                    await self._run_lifecycle(
                        account_id, tenant_id, "flood_wait", seconds=exc.seconds
                    )
                    await _interruptible_sleep(
                        min(exc.seconds * 1.5, 3600.0), stop_event
                    )
                    continue
                finally:
                    if client_from_pool and self._session_pool is not None:
                        await self._session_pool.release_client(account_id)

                if action_done:
                    actions_performed += 1
                    bucket_count += 1
                    _hour_bucket = (current_hour, bucket_count)
                    await self._flush_progress(
                        warmup_session_id, tenant_id, actions_performed
                    )

                await anti_detection.inter_action_delay()

            # Window ended normally — advance lifecycle to gate_review.
            next_at = utcnow() + timedelta(
                hours=config.interval_between_sessions_hours or 6
            )
            await self._complete_session(
                warmup_session_id,
                tenant_id,
                actions_performed,
                next_session_at=next_at,
            )
            await self._run_lifecycle(account_id, tenant_id, "warmup_complete")
            log.info(
                f"WarmupEngine: session {warmup_session_id} completed "
                f"({actions_performed} actions), next at {next_at.isoformat()}"
            )

        except asyncio.CancelledError:
            log.info(
                f"WarmupEngine: session {warmup_session_id} cancelled "
                f"({actions_performed} actions so far)"
            )
            await self._flush_progress(
                warmup_session_id, tenant_id, actions_performed
            )
        except Exception as exc:
            log.error(
                f"WarmupEngine: session {warmup_session_id} unrecoverable "
                f"error: {exc}",
                exc_info=True,
            )
            await self._set_session_status(
                warmup_session_id,
                tenant_id,
                STATUS_FAILED,
                completed_at=utcnow(),
            )
        finally:
            # Clean up in-memory references to prevent unbounded dict growth.
            tasks_map = self._tasks.get(config_id)
            if tasks_map is not None:
                tasks_map.pop(account_id, None)
                if not tasks_map:
                    self._tasks.pop(config_id, None)
            events_map = self._stop_events.get(config_id)
            if events_map is not None:
                events_map.pop(account_id, None)
                if not events_map:
                    self._stop_events.pop(config_id, None)

    # ------------------------------------------------------------------
    # Action dispatcher
    # ------------------------------------------------------------------

    async def _execute_one_action(
        self,
        *,
        client,
        config: WarmupConfig,
        anti_detection: AntiDetection,
        stop_event: asyncio.Event,
    ) -> bool:
        """
        Pick one warmup action from the enabled pool and execute it.

        Returns True if a meaningful action was executed.
        Raises _FrozenError or _QuarantineError on critical Telegram errors.
        """
        pool: List[str] = []
        if config.enable_read_channels:
            pool.extend(["read_channel", "read_channel"])  # weight 2
        if config.enable_reactions:
            pool.append("reaction")
        if config.enable_dialogs_between_accounts:
            pool.append("dialog_read")

        if not pool:
            return False

        if anti_detection.should_skip_action():
            return False

        action = random.choice(pool)

        if action == "read_channel":
            return await self._do_read_channel(client, anti_detection, config)
        if action == "reaction":
            return await self._do_reaction(client, anti_detection, config)
        if action == "dialog_read":
            return await self._do_dialog_read(client, anti_detection)

        return False

    # ------------------------------------------------------------------
    # Concrete Telegram actions
    # ------------------------------------------------------------------

    async def _do_read_channel(
        self,
        client,
        anti_detection: AntiDetection,
        config: WarmupConfig,
    ) -> bool:
        """
        Fetch and simulate reading recent messages in a target channel.

        Returns True on success.
        Raises _QuarantineError on FloodWaitError.
        """
        try:
            from telethon.errors import FloodWaitError, ChannelPrivateError
        except ImportError:
            log.warning("WarmupEngine: Telethon not available for _do_read_channel")
            return False

        channels: List[str] = []
        raw = config.target_channels
        if isinstance(raw, list):
            channels = [str(c) for c in raw if c]
        if not channels:
            channels = list(_FALLBACK_READ_CHANNELS)

        channel_id = random.choice(channels)

        try:
            entity = await client.get_entity(channel_id)
            messages = await client.get_messages(entity, limit=random.randint(3, 8))
            if messages:
                await anti_detection.simulate_reading(client, list(messages))
            return True
        except FloodWaitError as exc:
            raise _QuarantineError(exc.seconds) from exc
        except ChannelPrivateError:
            log.debug(f"WarmupEngine: channel {channel_id} private, skipping")
            return False
        except Exception as exc:
            # Check for FrozenMethodInvalidError by class name (avoids hard Telethon import)
            if "FrozenMethodInvalid" in type(exc).__name__:
                raise _FrozenError() from exc
            log.debug(f"WarmupEngine: _do_read_channel [{channel_id}]: {exc}")
            return False

    async def _do_reaction(
        self,
        client,
        anti_detection: AntiDetection,
        config: WarmupConfig,
    ) -> bool:
        """
        React to a recent post in a target channel with a random emoji.

        Returns True on success.
        Raises _QuarantineError on FloodWaitError.
        """
        try:
            from telethon.errors import FloodWaitError, ChannelPrivateError
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
        except ImportError:
            log.warning("WarmupEngine: Telethon not available for _do_reaction")
            return False

        channels: List[str] = []
        raw = config.target_channels
        if isinstance(raw, list):
            channels = [str(c) for c in raw if c]
        if not channels:
            channels = list(_FALLBACK_READ_CHANNELS)

        channel_id = random.choice(channels)

        try:
            entity = await client.get_entity(channel_id)
            messages = await client.get_messages(entity, limit=10)
            if not messages:
                return False

            message = random.choice(messages)
            emoji = anti_detection.randomize_emoji()

            await anti_detection.inter_action_delay()
            await client(
                SendReactionRequest(
                    peer=entity,
                    msg_id=message.id,
                    reaction=[ReactionEmoji(emoticon=emoji)],
                )
            )
            return True

        except FloodWaitError as exc:
            raise _QuarantineError(exc.seconds) from exc
        except ChannelPrivateError:
            log.debug(f"WarmupEngine: channel {channel_id} private for reaction")
            return False
        except Exception as exc:
            if "FrozenMethodInvalid" in type(exc).__name__:
                raise _FrozenError() from exc
            log.debug(f"WarmupEngine: _do_reaction [{channel_id}]: {exc}")
            return False

    async def _do_dialog_read(
        self,
        client,
        anti_detection: AntiDetection,
    ) -> bool:
        """
        Scroll through the recent dialog list and 'read' one entry.

        Returns True on success.
        Raises _QuarantineError on FloodWaitError.
        """
        try:
            from telethon.errors import FloodWaitError
        except ImportError:
            log.warning("WarmupEngine: Telethon not available for _do_dialog_read")
            return False

        try:
            dialogs = await client.get_dialogs(limit=10)
            if not dialogs:
                return False

            dialog = random.choice(dialogs)
            messages = await client.get_messages(dialog.entity, limit=3)
            if messages:
                await anti_detection.simulate_reading(client, list(messages))
            return True

        except FloodWaitError as exc:
            raise _QuarantineError(exc.seconds) from exc
        except Exception as exc:
            if "FrozenMethodInvalid" in type(exc).__name__:
                raise _FrozenError() from exc
            log.debug(f"WarmupEngine: _do_dialog_read: {exc}")
            return False

    # ------------------------------------------------------------------
    # DB helpers — each opens its own session to keep transactions short
    # ------------------------------------------------------------------

    async def _load_config(
        self, config_id: int, tenant_id: int
    ) -> Optional[WarmupConfig]:
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    result = await sess.execute(
                        select(WarmupConfig).where(WarmupConfig.id == config_id)
                    )
                    return result.scalar_one_or_none()
        except Exception as exc:
            log.warning(f"WarmupEngine: _load_config error: {exc}", exc_info=True)
            return None

    async def _set_session_status(
        self,
        warmup_session_id: int,
        tenant_id: int,
        status: str,
        *,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
    ) -> None:
        values: dict = {"status": status}
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at

        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    await sess.execute(
                        update(WarmupSession)
                        .where(WarmupSession.id == warmup_session_id)
                        .values(**values)
                    )
        except Exception as exc:
            log.warning(
                f"WarmupEngine: _set_session_status "
                f"[session={warmup_session_id}]: {exc}"
            )

    async def _flush_progress(
        self,
        warmup_session_id: int,
        tenant_id: int,
        actions_performed: int,
    ) -> None:
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    await sess.execute(
                        update(WarmupSession)
                        .where(WarmupSession.id == warmup_session_id)
                        .values(actions_performed=actions_performed)
                    )
        except Exception as exc:
            log.warning(
                f"WarmupEngine: _flush_progress "
                f"[session={warmup_session_id}]: {exc}"
            )

    async def _complete_session(
        self,
        warmup_session_id: int,
        tenant_id: int,
        actions_performed: int,
        next_session_at: datetime,
    ) -> None:
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    await sess.execute(
                        update(WarmupSession)
                        .where(WarmupSession.id == warmup_session_id)
                        .values(
                            status=STATUS_COMPLETED,
                            actions_performed=actions_performed,
                            completed_at=utcnow(),
                            next_session_at=next_session_at,
                        )
                    )
        except Exception as exc:
            log.warning(
                f"WarmupEngine: _complete_session "
                f"[session={warmup_session_id}]: {exc}"
            )

    async def _quarantine_account(
        self,
        account_id: int,
        tenant_id: int,
        flood_wait_seconds: int,
    ) -> None:
        """
        Set Account.quarantined_until and increment the AccountHealthScore
        flood_wait_count.
        """
        padded = int(flood_wait_seconds * 1.5)
        quarantine_until = utcnow() + timedelta(seconds=padded)

        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)

                    await sess.execute(
                        update(Account)
                        .where(Account.id == account_id)
                        .values(
                            quarantined_until=quarantine_until,
                            status="flood_wait",
                        )
                    )

                    # Upsert health score row.
                    res = await sess.execute(
                        select(AccountHealthScore).where(
                            AccountHealthScore.account_id == account_id,
                            AccountHealthScore.tenant_id == tenant_id,
                        )
                    )
                    hs = res.scalar_one_or_none()
                    if hs is None:
                        hs = AccountHealthScore(
                            tenant_id=tenant_id,
                            account_id=account_id,
                            flood_wait_count=1,
                            last_calculated_at=utcnow(),
                        )
                        sess.add(hs)
                    else:
                        hs.flood_wait_count = (hs.flood_wait_count or 0) + 1
                        hs.last_calculated_at = utcnow()

        except Exception as exc:
            log.warning(
                f"WarmupEngine: _quarantine_account "
                f"[account={account_id}]: {exc}"
            )

    # ------------------------------------------------------------------
    # Client acquisition
    # ------------------------------------------------------------------

    async def _get_client(self, account_id: int, tenant_id: int):
        """
        Return a connected Telethon client for *account_id*, or None.

        Uses SessionPool when available.  Falls back to the legacy
        SessionManager for backward compatibility.

        On SessionDeadError the account lifecycle is advanced to "dead" and
        None is returned so the caller can skip and retry later.
        """
        # New path: SessionPool (account_id-keyed, fully async).
        if self._session_pool is not None:
            try:
                async with async_session() as sess:
                    async with sess.begin():
                        await apply_session_rls_context(sess, tenant_id=tenant_id)
                        client = await self._session_pool.get_client(
                            account_id, db_session=sess
                        )
                return client
            except SessionDeadError as exc:
                log.warning(
                    f"WarmupEngine: session dead account={account_id}: {exc}"
                )
                await self._run_lifecycle(account_id, tenant_id, "session_dead")
                return None
            except PoolCapacityError as exc:
                log.warning(
                    f"WarmupEngine: pool full, cannot acquire account={account_id}: {exc}"
                )
                return None
            except Exception as exc:
                log.warning(
                    f"WarmupEngine: _get_client (pool) [account={account_id}]: {exc}"
                )
                return None

        # Legacy path: SessionManager (phone-keyed).
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
                log.warning(
                    f"WarmupEngine: account {account_id} not found in DB"
                )
                return None

            phone = account.phone
            client = self._session_mgr.get_client(phone)
            if client is not None and client.is_connected():
                return client

            # Attempt a connect without the frozen-probe to keep warmup light.
            client = await self._session_mgr.connect_client_for_action(
                phone,
                user_id=account.user_id,
            )
            return client

        except Exception as exc:
            log.warning(
                f"WarmupEngine: _get_client [account={account_id}]: {exc}"
            )
            return None

    # ------------------------------------------------------------------
    # Lifecycle transition helper
    # ------------------------------------------------------------------

    async def _run_lifecycle(
        self,
        account_id: int,
        tenant_id: int,
        event: str,
        *,
        seconds: int = 0,
    ) -> None:
        """
        Fire an AccountLifecycle event-handler in a short-lived DB session.

        Errors are swallowed and logged at DEBUG level so a lifecycle failure
        never interrupts the warmup loop.

        event values:
          "warmup_complete" -> on_warmup_complete
          "flood_wait"      -> on_flood_wait(seconds)
          "frozen"          -> on_frozen
          "session_dead"    -> on_session_dead
        """
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    lifecycle = AccountLifecycle(sess)
                    if event == "warmup_complete":
                        await lifecycle.on_warmup_complete(account_id)
                    elif event == "flood_wait":
                        await lifecycle.on_flood_wait(account_id, seconds)
                    elif event == "frozen":
                        await lifecycle.on_frozen(account_id)
                    elif event == "session_dead":
                        await lifecycle.on_session_dead(account_id)
        except Exception as exc:
            log.debug(
                f"WarmupEngine._run_lifecycle [{event}] "
                f"account={account_id}: {exc}"
            )


# ------------------------------------------------------------------
# Internal sentinel exceptions
# ------------------------------------------------------------------


class _QuarantineError(Exception):
    """FloodWaitError received — account must be quarantined."""

    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        super().__init__(f"FloodWait {seconds}s")


class _FrozenError(Exception):
    """FrozenMethodInvalidError received — account is frozen, stop immediately."""
