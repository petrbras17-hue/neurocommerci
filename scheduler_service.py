"""Control-plane scheduler for distributed autopilot orchestration."""

from __future__ import annotations

import asyncio
import os
import signal
from contextlib import suppress
from datetime import datetime, timezone

from channels.monitor import ChannelMonitor
from comments.generator import CommentGenerator
from comments.poster import CommentPoster
from config import settings
from core.account_manager import AccountManager
from core.digest_service import digest_configured
from core.engine import CommentingEngine
from core.ops_service import list_tenant_user_ids, queue_recovery, send_digest_summary
from core.proxy_manager import ProxyManager
from core.rate_limiter import RateLimiter
from core.redis_state import redis_state
from core.scheduler import TaskScheduler
from core.session_manager import SessionManager
from core.task_queue import task_queue
from storage.sqlite_db import init_db
from utils.channel_subscriber import ChannelSubscriber
from utils.logger import log


AUTOPILOT_RECONCILE_SEC = 15
SCHEDULER_HEARTBEAT_SEC = 30
PERIODIC_RECOVERY_SEC = 900
DIGEST_CHECK_SEC = 300


class SchedulerService:
    def __init__(self):
        self._running = False
        self._worker_id = str(os.environ.get("WORKER_ID") or "scheduler")
        self._heartbeat_task: asyncio.Task | None = None
        self._reconcile_lock = asyncio.Lock()
        self.proxy_mgr = ProxyManager()
        self.session_mgr = SessionManager()
        self.rate_limiter = RateLimiter()
        self.account_mgr = AccountManager(self.session_mgr, self.proxy_mgr, self.rate_limiter)
        self.channel_monitor = ChannelMonitor(self.session_mgr, self.account_mgr, self.proxy_mgr)
        self.comment_generator = CommentGenerator()
        self.comment_poster = CommentPoster(
            self.account_mgr,
            self.session_mgr,
            self.rate_limiter,
            self.comment_generator,
            self.channel_monitor,
        )
        self.channel_subscriber = ChannelSubscriber(self.session_mgr, self.account_mgr)
        self.engine = CommentingEngine(
            account_manager=self.account_mgr,
            session_manager=self.session_mgr,
            proxy_manager=self.proxy_mgr,
            rate_limiter=self.rate_limiter,
            poster=self.comment_poster,
            monitor=self.channel_monitor,
            subscriber=self.channel_subscriber,
        )
        self.scheduler = TaskScheduler()

    async def start(self):
        await init_db()
        await redis_state.connect()
        await task_queue.connect()
        await self.rate_limiter.load_from_db()
        if settings.proxy_list_path.exists():
            self.proxy_mgr.load_from_file()

        self.scheduler.add_custom_job(
            "autopilot_reconcile",
            self._reconcile_autopilot,
            interval_sec=AUTOPILOT_RECONCILE_SEC,
            name="Autopilot reconcile",
        )
        self.scheduler.add_custom_job(
            "periodic_recovery_enqueue",
            self._enqueue_periodic_recovery,
            interval_sec=PERIODIC_RECOVERY_SEC,
            name="Periodic recovery enqueue",
        )
        self.scheduler.add_custom_job(
            "periodic_digest_summary",
            self._maybe_send_daily_digest,
            interval_sec=DIGEST_CHECK_SEC,
            name="Periodic digest summary",
        )
        self.scheduler.start()
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="scheduler_heartbeat")
        log.info("SchedulerService: started")

        try:
            while self._running:
                await asyncio.sleep(60)
        finally:
            await self.stop()

    async def _heartbeat_loop(self):
        while self._running:
            enabled = await redis_state.get_runtime_flag("autopilot_enabled", "0")
            await redis_state.worker_heartbeat(
                self._worker_id,
                0,
                metrics={
                    "autopilot_enabled": enabled == "1",
                    "engine_running": self.engine.is_running,
                },
            )
            await asyncio.sleep(SCHEDULER_HEARTBEAT_SEC)

    async def _reconcile_autopilot(self):
        async with self._reconcile_lock:
            enabled = await redis_state.get_runtime_flag("autopilot_enabled", "0")
            enabled_now = enabled == "1"
            if enabled_now and not self.engine.is_running:
                log.info("SchedulerService: enabling distributed autopilot")
                await self.engine.start()
            elif (not enabled_now) and self.engine.is_running:
                log.info("SchedulerService: disabling distributed autopilot")
                await self.engine.stop()

    async def _enqueue_periodic_recovery(self):
        enabled = await redis_state.get_runtime_flag("autopilot_enabled", "0")
        if enabled != "1":
            return
        for user_id in await list_tenant_user_ids():
            await queue_recovery(
                user_id=user_id,
                migrate_layout=False,
                set_parser_first_authorized=False,
                clear_worker_claims=False,
            )

    async def _maybe_send_daily_digest(self):
        if not settings.DIGEST_SCHEDULE_ENABLED or not digest_configured():
            return
        now = datetime.now(timezone.utc)
        digest_time_reached = (
            now.hour > int(settings.DIGEST_DAILY_HOUR_UTC)
            or (
                now.hour == int(settings.DIGEST_DAILY_HOUR_UTC)
                and now.minute >= int(settings.DIGEST_DAILY_MINUTE_UTC)
            )
        )
        if not digest_time_reached:
            return

        user_ids = await list_tenant_user_ids()
        if not user_ids:
            user_ids = [None]
        today = now.strftime("%Y-%m-%d")
        for user_id in user_ids:
            flag_name = f"digest_last_daily:{user_id if user_id is not None else 'default'}"
            if await redis_state.get_runtime_flag(flag_name, "") == today:
                continue
            try:
                report = await send_digest_summary(user_id=user_id)
            except Exception as exc:
                log.error("digest send exception for user_id=%s: %s", user_id, exc)
                report = {"ok": False}
            # Always mark attempted to prevent repeat fires on failure
            await redis_state.set_runtime_flag(flag_name, today)
            if not report.get("ok"):
                log.warning("digest send failed for user_id=%s: %s", user_id, report)

    async def stop(self):
        if not self._running:
            return
        self._running = False
        self.scheduler.stop()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
        with suppress(Exception):
            if self.engine.is_running:
                await self.engine.stop()
        with suppress(Exception):
            await task_queue.close()
        with suppress(Exception):
            await redis_state.close()


async def main():
    service = SchedulerService()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(service.stop()))
    await service.start()


if __name__ == "__main__":
    asyncio.run(main())
