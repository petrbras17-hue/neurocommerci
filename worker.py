"""
Account Worker — distributed Telethon client manager.

Each worker claims MAX_ACCOUNTS_PER_WORKER accounts via Redis,
connects them, and runs comment/warmup/health/keepalive loops.

Scale: 3 workers × 50 accounts = 150 accounts.
"""

from __future__ import annotations

import asyncio
import os
import random
import signal
import sys
import uuid
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from config import settings, BASE_DIR
from core.task_queue import task_queue
from core.redis_state import redis_state
from storage.sqlite_db import init_db, async_session
from storage.models import Account
from core.session_manager import SessionManager
from core.proxy_manager import ProxyManager
from core.rate_limiter import RateLimiter
from core.account_manager import AccountManager
from channels.monitor import ChannelMonitor
from comments.generator import CommentGenerator
from comments.poster import CommentPoster
from core.policy_engine import policy_engine
from utils.anti_ban import AntibanManager
from utils.logger import log
from utils.helpers import utcnow
from utils.proxy_bindings import get_bound_proxy_config
from utils.runtime_readiness import account_blockers

from sqlalchemy import select, update, or_


def _normalize_phone(raw_phone: str) -> str:
    digits = "".join(ch for ch in str(raw_phone) if ch.isdigit())
    return f"+{digits}" if digits else ""


_hostname = os.environ.get("HOSTNAME")
_default_worker_id = f"worker-{_hostname}" if _hostname else f"worker-{uuid.uuid4().hex[:6]}"
_env_worker_id = (os.environ.get("WORKER_ID") or "").strip()
if _env_worker_id and _hostname and not settings.PINNED_PHONE:
    WORKER_ID = f"{_env_worker_id}-{_hostname}"
elif _env_worker_id:
    WORKER_ID = _env_worker_id
else:
    WORKER_ID = _default_worker_id
PINNED_PHONE = _normalize_phone(settings.PINNED_PHONE)
PARSER_PHONE = _normalize_phone(settings.PARSER_ONLY_PHONE)
MAX_ACCOUNTS = settings.MAX_ACCOUNTS_PER_WORKER
CLAIM_TTL = 300  # 5 min, renewed every 2 min
HEARTBEAT_INTERVAL = 120  # 2 min
CONNECT_BATCH_SIZE = max(1, settings.WORKER_CONNECT_BATCH_SIZE)
DEQUEUE_TIMEOUT = max(1, settings.WORKER_DEQUEUE_TIMEOUT_SEC)
COMMENT_LEASE_SEC = 300
CLAIMABLE_LIFECYCLE_STAGES = ("warming_up", "gate_review", "active_commenting", "execution_ready")
BLOCKED_HEALTH_STATUSES = ("dead", "restricted", "frozen", "expired")


class AccountWorker:
    """Distributed account worker."""

    def __init__(self):
        self.worker_id = WORKER_ID
        self.session_mgr = SessionManager()
        self.proxy_mgr = ProxyManager()
        self.rate_limiter = RateLimiter()
        self.account_mgr = AccountManager(
            session_manager=self.session_mgr,
            proxy_manager=self.proxy_mgr,
            rate_limiter=self.rate_limiter,
        )
        # Local in-worker post queue used by CommentPoster internals.
        self.monitor = ChannelMonitor(
            session_manager=self.session_mgr,
            account_manager=self.account_mgr,
            proxy_manager=self.proxy_mgr,
        )
        self.comment_poster = CommentPoster(
            account_manager=self.account_mgr,
            session_manager=self.session_mgr,
            rate_limiter=self.rate_limiter,
            generator=CommentGenerator(),
            monitor=self.monitor,
        )
        self.claimed_phones: list[str] = []
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._recent_comment_actions: list[float] = []
        self._metrics = {
            "claimed": 0,
            "connected": 0,
            "dequeue_errors": 0,
            "queue_empty_loops": 0,
            "pinned_phone": PINNED_PHONE,
            "claim_blocker": "",
        }

    async def start(self):
        """Main entry point."""
        mode = f"pinned={PINNED_PHONE}" if PINNED_PHONE else "dynamic"
        log.info(f"Worker {self.worker_id} starting (max {MAX_ACCOUNTS} accounts, {mode})...")

        # Initialize infrastructure
        await init_db()

        if (
            settings.PINNED_PHONE_REQUIRED
            and not PINNED_PHONE
            and (self.worker_id.startswith("worker-A") or self.worker_id.startswith("worker-B"))
        ):
            await policy_engine.check(
                "missing_pinned_phone",
                {
                    "worker_id": self.worker_id,
                    "pinned_phone": PINNED_PHONE,
                    "required": True,
                },
                worker_id=self.worker_id,
            )
            raise RuntimeError(
                f"Worker {self.worker_id}: PINNED_PHONE is required in strict mode for pinned workers"
            )

        await task_queue.connect()
        await redis_state.connect()
        self.proxy_mgr.load_from_file()

        if MAX_ACCOUNTS <= 0:
            log.warning(
                f"Worker {self.worker_id}: MAX_ACCOUNTS_PER_WORKER={MAX_ACCOUNTS}. "
                "Worker runs in disabled-claim mode."
            )
            self._running = True
            self._tasks = [
                asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
                asyncio.create_task(self._idle_loop(), name="idle"),
            ]
            try:
                await asyncio.gather(*self._tasks)
            except asyncio.CancelledError:
                pass
            return

        # Claim accounts
        await self._claim_accounts()
        if not self.claimed_phones:
            log.warning(f"Worker {self.worker_id}: no accounts to claim, waiting...")
            # Wait and retry
            while not self.claimed_phones:
                self._metrics["claimed"] = 0
                self._metrics["connected"] = 0
                try:
                    await redis_state.worker_heartbeat(
                        self.worker_id,
                        0,
                        metrics=self._metrics,
                    )
                except Exception as exc:
                    log.debug(f"Pre-claim heartbeat error: {exc}")
                await asyncio.sleep(30)
                await self._claim_accounts()

        log.info(f"Worker {self.worker_id}: claimed {len(self.claimed_phones)} accounts")
        self._metrics["claimed"] = len(self.claimed_phones)

        # Connect all claimed accounts
        await self._connect_accounts()

        # Start loops
        self._running = True
        self._tasks = [
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
            asyncio.create_task(self._claim_renewal_loop(), name="claim_renewal"),
            asyncio.create_task(self._comment_loop(), name="comments"),
            asyncio.create_task(self._health_loop(), name="health"),
            asyncio.create_task(self._keepalive_loop(), name="keepalive"),
        ]

        log.info(f"Worker {self.worker_id}: all loops started")

        # Wait for shutdown
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Graceful shutdown."""
        log.info(f"Worker {self.worker_id}: stopping...")
        self._running = False

        # Cancel tasks
        for task in self._tasks:
            task.cancel()

        # Release claims
        for phone in self.claimed_phones:
            await redis_state.release_claim(phone, self.worker_id)

        # Disconnect clients
        for phone in self.claimed_phones:
            try:
                await self.session_mgr.disconnect_client(phone)
            except Exception:
                pass

        await task_queue.close()
        await redis_state.close()
        log.info(f"Worker {self.worker_id}: stopped")

    async def _claim_accounts(self):
        """Claim unclaimed accounts from DB."""
        claim_capacity = MAX_ACCOUNTS - len(self.claimed_phones)
        if claim_capacity <= 0:
            self._set_claim_blocker("")
            return

        async with async_session() as session:
            query = select(Account.phone).where(
                Account.status == "active",
                Account.lifecycle_stage.in_(CLAIMABLE_LIFECYCLE_STAGES),
                or_(
                    Account.health_status.is_(None),
                    Account.health_status.notin_(BLOCKED_HEALTH_STATUSES),
                ),
                or_(Account.quarantined_until.is_(None), Account.quarantined_until <= utcnow()),
            )
            if settings.STRICT_PARSER_ONLY and PARSER_PHONE:
                query = query.where(Account.phone != PARSER_PHONE)
            if settings.STRICT_PROXY_PER_ACCOUNT:
                query = query.where(Account.proxy_id.is_not(None))
            if PINNED_PHONE:
                query = query.where(Account.phone == PINNED_PHONE)
            result = await session.execute(query)
            all_phones = [r[0] for r in result.fetchall()]

        if not all_phones:
            if PINNED_PHONE:
                self._set_claim_blocker(await self._describe_pinned_claim_blocker())
            else:
                self._set_claim_blocker("no_eligible_accounts")
            return

        # Find unclaimed phones
        unclaimed = await redis_state.get_unclaimed_phones(all_phones)
        if PINNED_PHONE:
            unclaimed = [phone for phone in unclaimed if phone == PINNED_PHONE]
            if not unclaimed and PINNED_PHONE not in self.claimed_phones:
                self._set_claim_blocker("already_claimed_by_other_worker")
                return
        else:
            random.shuffle(unclaimed)
        self._set_claim_blocker("")

        # Claim up to MAX_ACCOUNTS
        for phone in unclaimed[:claim_capacity]:
            if await redis_state.claim_account(phone, self.worker_id, CLAIM_TTL):
                self.claimed_phones.append(phone)
                self._set_claim_blocker("")
                log.debug(f"Worker {self.worker_id}: claimed {phone}")
            elif PINNED_PHONE:
                self._set_claim_blocker("already_claimed_by_other_worker")

    async def _connect_accounts(self):
        """Connect all claimed accounts with proxies."""
        batch_size = CONNECT_BATCH_SIZE
        connected_count = 0
        for i in range(0, len(self.claimed_phones), batch_size):
            batch = self.claimed_phones[i:i + batch_size]
            async with async_session() as session:
                result = await session.execute(
                    select(Account.phone, Account.user_id).where(Account.phone.in_(batch))
                )
                user_ids = {str(phone): int(user_id) if user_id is not None else None for phone, user_id in result.all()}
            tasks = []
            task_phones: list[str] = []
            for phone in batch:
                proxy = await get_bound_proxy_config(phone)
                if proxy is None and not settings.STRICT_PROXY_PER_ACCOUNT:
                    proxy = self.proxy_mgr.assign_to_account(phone)
                if settings.STRICT_PROXY_PER_ACCOUNT and proxy is None:
                    await policy_engine.check(
                        "proxy_assignment",
                        {
                            "strict_proxy": True,
                            "proxy_assigned": False,
                            "phone": phone,
                        },
                        phone=phone,
                        worker_id=self.worker_id,
                    )
                    log.warning(
                        f"Worker {self.worker_id}: strict proxy mode blocks {phone} (no dedicated proxy)"
                    )
                    continue
                tasks.append(
                    self.session_mgr.connect_client(
                        phone,
                        proxy=proxy,
                        user_id=user_ids.get(phone),
                    )
                )
                task_phones.append(phone)
            if not tasks:
                continue
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for phone, result in zip(task_phones, results):
                if isinstance(result, Exception):
                    log.warning(f"Worker {self.worker_id}: failed to connect {phone}: {result}")
                    continue
                if result is None:
                    log.warning(f"Worker {self.worker_id}: connect returned None for {phone}")
                    continue
                log.info(f"Worker {self.worker_id}: connected {phone}")
                connected_count += 1
                await self._mark_connected_stage(phone)
            if i + batch_size < len(self.claimed_phones):
                await asyncio.sleep(random.uniform(3, 8))
        self._metrics["connected"] = connected_count

    async def _mark_connected_stage(self, phone: str):
        """Update lifecycle stage after successful worker-side connection."""
        async with async_session() as session:
            result = await session.execute(
                select(Account).where(Account.phone == phone)
            )
            account = result.scalar_one_or_none()
            if account is None:
                return
            if account.lifecycle_stage in {"gate_review", "active_commenting", "execution_ready", "restricted"}:
                return
            days_active = account.days_active or 0
            next_stage = AccountManager.resolve_post_warmup_stage(days_active)
            await session.execute(
                update(Account)
                .where(Account.phone == phone)
                .values(lifecycle_stage=next_stage)
            )
            await session.commit()

    async def _heartbeat_loop(self):
        """Report worker health to Redis."""
        while self._running:
            try:
                phones_snapshot = list(self.claimed_phones)
                connected = sum(
                    1 for p in phones_snapshot
                    if self.session_mgr.get_client(p) and self.session_mgr.get_client(p).is_connected()
                )
                self._metrics["connected"] = connected
                self._metrics["claimed"] = len(self.claimed_phones)
                await redis_state.worker_heartbeat(
                    self.worker_id,
                    connected,
                    metrics=self._metrics,
                )
            except Exception as exc:
                log.debug(f"Heartbeat error: {exc}")
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _claim_renewal_loop(self):
        """Renew Redis claims before they expire."""
        while self._running:
            try:
                for phone in self.claimed_phones[:]:
                    try:
                        ok = await redis_state.renew_claim(phone, self.worker_id, CLAIM_TTL)
                    except Exception as exc:
                        log.error(f"Worker {self.worker_id}: claim renewal error for {phone}: {exc}")
                        continue
                    if not ok:
                        log.warning(f"Worker {self.worker_id}: lost claim on {phone}")
                        self.claimed_phones.remove(phone)
                        if PINNED_PHONE and phone == PINNED_PHONE:
                            self._set_claim_blocker("claim_lost")
            except Exception as exc:
                log.error(f"Worker {self.worker_id}: claim renewal loop error: {exc}")
            await asyncio.sleep(CLAIM_TTL // 2)

    async def _comment_loop(self):
        """Process commenting tasks from queue + local round-robin."""
        while self._running:
            if not AntibanManager.is_active_hours():
                await asyncio.sleep(300)
                continue

            ready_claimed_phones = await self._comment_ready_claimed_phones(self.claimed_phones)
            if not ready_claimed_phones:
                await asyncio.sleep(30)
                continue

            try:
                task = await task_queue.reserve(
                    "comment_tasks",
                    consumer_id=self.worker_id,
                    timeout=DEQUEUE_TIMEOUT,
                    lease_sec=COMMENT_LEASE_SEC,
                )
            except Exception as exc:
                self._metrics["dequeue_errors"] += 1
                log.warning(f"Worker {self.worker_id}: comment queue dequeue failed: {exc}")
                await asyncio.sleep(5)
                continue

            if task:
                burst = self._mark_comment_action()
                if burst:
                    await policy_engine.check(
                        "action_rate_burst",
                        {"burst": True, "worker_id": self.worker_id},
                        worker_id=self.worker_id,
                    )
                    await asyncio.sleep(10)
                    continue
                await self._do_comment(task)
                continue

            self._metrics["queue_empty_loops"] += 1
            delay = self.rate_limiter.get_next_delay()
            await asyncio.sleep(delay)

    def _mark_comment_action(self) -> bool:
        """Track recent actions and return True when burst pattern is detected."""
        now = time.time()
        window_sec = 60
        self._recent_comment_actions = [t for t in self._recent_comment_actions if now - t <= window_sec]
        self._recent_comment_actions.append(now)
        return len(self._recent_comment_actions) >= 6

    async def _do_comment(self, task: dict):
        """Execute one comment task from Redis queue."""
        task_id = str(task.get("_task_id") or "")
        post_data = task.get("post_data")
        if not isinstance(post_data, dict):
            log.warning(f"Worker {self.worker_id}: invalid comment task payload: {task}")
            if task_id:
                await task_queue.dead_letter(
                    "comment_tasks",
                    task_id,
                    task,
                    reason="invalid_post_data",
                )
            return

        task_user_id = task.get("user_id")
        allowed_phones = self.claimed_phones
        if task_user_id is not None:
            allowed_phones = await self._claimed_phones_for_user(int(task_user_id))
            if not allowed_phones:
                if task_id:
                    payload = dict(task)
                    payload["_attempts"] = int(payload.get("_attempts", 0)) + 1
                    await task_queue.requeue(
                        "comment_tasks",
                        task_id,
                        payload,
                        reason="no_claimed_phones_for_user",
                    )
                return
        comment_ready_phones = await self._comment_ready_claimed_phones(allowed_phones)
        if not comment_ready_phones:
            if task_id:
                payload = dict(task)
                payload["_attempts"] = int(payload.get("_attempts", 0)) + 1
                await task_queue.requeue(
                    "comment_tasks",
                    task_id,
                    payload,
                    reason="no_comment_ready_phones",
                )
            return

        outcome = await self.comment_poster.process_post(post_data, account_subset=comment_ready_phones)
        if outcome.code == "sent":
            log.info(
                f"Worker {self.worker_id}: comment sent for "
                f"post={post_data.get('telegram_post_id')} channel={post_data.get('channel_title')}"
            )
            if task_id:
                await task_queue.ack("comment_tasks", task_id)
            return

        if outcome.code == "retry":
            retries = int(task.get("_retries", 0))
            if retries >= 3:
                if task_id:
                    await task_queue.dead_letter(
                        "comment_tasks",
                        task_id,
                        task,
                        reason=f"{outcome.reason or 'comment_processing_retry'}_max_retries",
                    )
                return
            payload = {
                "post_data": post_data,
                "_retries": retries + 1,
                "_attempts": int(task.get("_attempts", 0)) + 1,
                "user_id": task.get("user_id"),
            }
            if task_id:
                await task_queue.requeue(
                    "comment_tasks",
                    task_id,
                    payload,
                    reason=outcome.reason or "comment_processing_retry",
                )
            log.warning(
                f"Worker {self.worker_id}: task requeued "
                f"(reason={outcome.reason or 'retry'}) for post={post_data.get('telegram_post_id')}"
            )
            return

        if outcome.code == "failed":
            if task_id:
                await task_queue.dead_letter(
                    "comment_tasks",
                    task_id,
                    task,
                    reason=outcome.reason or "comment_processing_failed",
                )
            return

        if task_id:
            await task_queue.ack("comment_tasks", task_id)

    async def _claimed_phones_for_user(self, user_id: int) -> list[str]:
        """Filter claimed phones by tenant owner."""
        async with async_session() as session:
            result = await session.execute(
                select(Account.phone).where(
                    Account.phone.in_(self.claimed_phones),
                    Account.user_id == user_id,
                )
            )
            return [row[0] for row in result.fetchall()]

    async def _comment_ready_claimed_phones(self, phones: list[str]) -> list[str]:
        """Claimed phones that are currently eligible to send comments."""
        if not phones:
            return []
        async with async_session() as session:
            result = await session.execute(
                select(Account.phone).where(
                    Account.phone.in_(phones),
                    Account.status == "active",
                    Account.lifecycle_stage.in_(("active_commenting", "execution_ready")),
                    or_(
                        Account.health_status.is_(None),
                        Account.health_status.notin_(BLOCKED_HEALTH_STATUSES),
                    ),
                    or_(Account.quarantined_until.is_(None), Account.quarantined_until <= utcnow()),
                )
            )
            return [row[0] for row in result.fetchall()]

    async def _health_loop(self):
        """Check account health periodically."""
        await asyncio.sleep(random.uniform(60, 300))  # Stagger start
        while self._running:
            for phone in self.claimed_phones[:]:
                client = self.session_mgr.get_client(phone)
                if not client or not client.is_connected():
                    await redis_state.set_health(phone, "disconnected")
                    continue
                try:
                    me = await client.get_me()
                    if me:
                        await redis_state.set_health(phone, "alive")
                    else:
                        await redis_state.set_health(phone, "dead")
                except Exception as exc:
                    error_name = type(exc).__name__
                    if "AuthKeyUnregistered" in error_name or "UserDeactivatedBan" in error_name:
                        await redis_state.set_health(phone, "dead")
                        log.error(f"Worker {self.worker_id}: {phone} is DEAD ({error_name})")
                    else:
                        log.debug(f"Health check error for {phone}: {exc}")
                await asyncio.sleep(random.uniform(10, 30))
            await asyncio.sleep(settings.SESSION_HEALTH_CHECK_HOURS * 3600)

    async def _keepalive_loop(self):
        """Keep-alive activity for all accounts."""
        await asyncio.sleep(random.uniform(300, 900))  # Stagger
        while self._running:
            for phone in list(self.claimed_phones):
                client = self.session_mgr.get_client(phone)
                if not client or not client.is_connected():
                    continue
                try:
                    await client.get_me()
                    # 30% chance: read some dialogs
                    if random.random() < 0.3:
                        async for dialog in client.iter_dialogs(limit=3):
                            pass
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(5, 30))
            await asyncio.sleep(settings.KEEP_ALIVE_INTERVAL_HOURS * 3600)

    async def _idle_loop(self):
        """Idle loop when account claiming is intentionally disabled."""
        while self._running:
            await asyncio.sleep(300)

    def _set_claim_blocker(self, blocker: str):
        blocker = str(blocker or "").strip()
        if self._metrics.get("claim_blocker") == blocker:
            return
        self._metrics["claim_blocker"] = blocker
        if PINNED_PHONE and blocker:
            log.warning(
                f"Worker {self.worker_id}: pinned phone {PINNED_PHONE} claim blocker -> {blocker}"
            )

    async def _describe_pinned_claim_blocker(self) -> str:
        async with async_session() as session:
            result = await session.execute(
                select(Account).where(Account.phone == PINNED_PHONE)
            )
            account = result.scalar_one_or_none()
        if account is None:
            return "account_missing"
        readiness = account_blockers(
            account,
            sessions_dir=settings.sessions_path,
            strict_proxy=bool(settings.STRICT_PROXY_PER_ACCOUNT),
        )
        blocker = readiness.primary
        return blocker if blocker != "ready" else "not_claimable"


async def main():
    worker = AccountWorker()

    # Graceful shutdown on SIGTERM/SIGINT
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(worker.stop()))

    await worker.start()


if __name__ == "__main__":
    asyncio.run(main())
