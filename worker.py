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
from utils.anti_ban import AntibanManager
from utils.logger import log

from sqlalchemy import select


WORKER_ID = os.environ.get("WORKER_ID", f"worker-{uuid.uuid4().hex[:6]}")
MAX_ACCOUNTS = settings.MAX_ACCOUNTS_PER_WORKER
CLAIM_TTL = 300  # 5 min, renewed every 2 min
HEARTBEAT_INTERVAL = 120  # 2 min


class AccountWorker:
    """Distributed account worker."""

    def __init__(self):
        self.worker_id = WORKER_ID
        self.session_mgr = SessionManager()
        self.proxy_mgr = ProxyManager()
        self.rate_limiter = RateLimiter()
        self.claimed_phones: list[str] = []
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        """Main entry point."""
        log.info(f"Worker {self.worker_id} starting (max {MAX_ACCOUNTS} accounts)...")

        # Initialize infrastructure
        await init_db()
        await task_queue.connect()
        await redis_state.connect()
        self.proxy_mgr.load_from_file()

        # Claim accounts
        await self._claim_accounts()
        if not self.claimed_phones:
            log.warning(f"Worker {self.worker_id}: no accounts to claim, waiting...")
            # Wait and retry
            while not self.claimed_phones:
                await asyncio.sleep(30)
                await self._claim_accounts()

        log.info(f"Worker {self.worker_id}: claimed {len(self.claimed_phones)} accounts")

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
        async with async_session() as session:
            result = await session.execute(
                select(Account.phone).where(
                    Account.status.in_(["active", "cooldown"]),
                    Account.health_status != "dead",
                )
            )
            all_phones = [r[0] for r in result.fetchall()]

        if not all_phones:
            return

        # Find unclaimed phones
        unclaimed = await redis_state.get_unclaimed_phones(all_phones)
        random.shuffle(unclaimed)

        # Claim up to MAX_ACCOUNTS
        for phone in unclaimed[:MAX_ACCOUNTS - len(self.claimed_phones)]:
            if await redis_state.claim_account(phone, self.worker_id, CLAIM_TTL):
                self.claimed_phones.append(phone)
                log.debug(f"Worker {self.worker_id}: claimed {phone}")

    async def _connect_accounts(self):
        """Connect all claimed accounts with proxies."""
        batch_size = 5
        for i in range(0, len(self.claimed_phones), batch_size):
            batch = self.claimed_phones[i:i + batch_size]
            tasks = []
            for phone in batch:
                proxy = self.proxy_mgr.assign_to_account(phone)
                tasks.append(self.session_mgr.connect_client(phone, proxy=proxy))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for phone, result in zip(batch, results):
                if isinstance(result, Exception):
                    log.warning(f"Worker {self.worker_id}: failed to connect {phone}: {result}")
                else:
                    log.info(f"Worker {self.worker_id}: connected {phone}")
            if i + batch_size < len(self.claimed_phones):
                await asyncio.sleep(random.uniform(3, 8))

    async def _heartbeat_loop(self):
        """Report worker health to Redis."""
        while self._running:
            try:
                connected = sum(
                    1 for p in self.claimed_phones
                    if self.session_mgr.get_client(p) and self.session_mgr.get_client(p).is_connected()
                )
                await redis_state.worker_heartbeat(self.worker_id, connected)
            except Exception as exc:
                log.debug(f"Heartbeat error: {exc}")
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _claim_renewal_loop(self):
        """Renew Redis claims before they expire."""
        while self._running:
            for phone in self.claimed_phones[:]:
                ok = await redis_state.renew_claim(phone, self.worker_id, CLAIM_TTL)
                if not ok:
                    log.warning(f"Worker {self.worker_id}: lost claim on {phone}")
                    self.claimed_phones.remove(phone)
            await asyncio.sleep(CLAIM_TTL // 2)

    async def _comment_loop(self):
        """Process commenting tasks from queue + local round-robin."""
        while self._running:
            if not AntibanManager.is_active_hours():
                await asyncio.sleep(300)
                continue

            # Try to get a task from queue first
            task = await task_queue.dequeue("comments", timeout=5)
            if task:
                phone = task.get("phone")
                if phone in self.claimed_phones:
                    await self._do_comment(phone, task)
                continue

            # Otherwise: round-robin through our accounts
            for phone in self.claimed_phones:
                if self.rate_limiter.can_comment(phone):
                    client = self.session_mgr.get_client(phone)
                    if client and client.is_connected():
                        # Check for pending posts in DB queue
                        break

            delay = self.rate_limiter.get_next_delay()
            await asyncio.sleep(delay)

    async def _do_comment(self, phone: str, task: dict):
        """Execute a single comment task."""
        # Placeholder — will be connected to CommentPoster
        log.info(f"Worker {self.worker_id}: comment task for {phone}")

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
            for phone in self.claimed_phones:
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


async def main():
    worker = AccountWorker()

    # Graceful shutdown on SIGTERM/SIGINT
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(worker.stop()))

    await worker.start()


if __name__ == "__main__":
    asyncio.run(main())
