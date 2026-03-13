"""
Farm Orchestrator — manages multi-threaded neurocommenting farms.

Each farm has N threads, where 1 thread = 1 Telegram account + its own channels.
This is the central coordination layer that starts/stops/monitors farm threads.
"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime
from typing import Callable, Optional

import redis.asyncio as aioredis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import (
    Account,
    ChannelDatabase,
    ChannelEntry,
    FarmConfig,
    FarmEvent,
    FarmThread as FarmThreadModel,
)
from storage.sqlite_db import apply_session_rls_context
from utils.helpers import utcnow
from utils.logger import log


class FarmOrchestrator:
    """
    Central coordination layer: starts/stops/monitors farm threads.

    Dependency-injected: does not import global singletons so it is testable
    and safe to use inside the FastAPI request lifecycle.
    """

    def __init__(
        self,
        session_manager,
        task_queue,
        redis_client: aioredis.Redis,
    ) -> None:
        self.session_mgr = session_manager
        self.task_queue = task_queue
        self.redis = redis_client

        # farm_id -> {threads: list[FarmThread], status: str, started_at: datetime}
        self._active_farms: dict[int, dict] = {}

    # ------------------------------------------------------------------
    # Public lifecycle API
    # ------------------------------------------------------------------

    async def start_farm(self, farm_id: int, session: AsyncSession) -> dict:
        """Load farm config, create threads, assign channels, start monitoring."""
        if farm_id in self._active_farms:
            log.warning(f"Farm {farm_id}: already running, returning current status")
            return await self.get_farm_status(farm_id, session)

        farm = await self._load_farm(farm_id, session)
        if farm is None:
            raise ValueError(f"Farm {farm_id} not found")

        if farm.status == "running":
            log.warning(f"Farm {farm_id}: DB status already running but not in memory, resuming")

        # Load accounts assigned to this farm (via farm_threads rows)
        thread_rows = await self._load_thread_rows(farm_id, session)
        if not thread_rows:
            raise ValueError(
                f"Farm {farm_id}: no threads configured. "
                "Assign accounts to the farm first."
            )

        # Load channels for the farm's workspace
        channels = await self._load_channels_for_farm(farm, session)

        # Distribute channels evenly
        channel_chunks = _split_evenly(channels, len(thread_rows))

        # Import here to avoid circular imports at module level
        from core.farm_thread import FarmThread

        threads: list[FarmThread] = []
        for idx, (thread_row, channel_chunk) in enumerate(
            zip(thread_rows, channel_chunks)
        ):
            account = (await session.execute(
                select(Account).where(
                    Account.id == thread_row.account_id,
                    Account.tenant_id == farm.tenant_id,
                )
            )).scalar_one_or_none()
            if account is None:
                log.warning(
                    f"Farm {farm_id}: account {thread_row.account_id} not found, skipping"
                )
                continue

            thread = FarmThread(
                thread_id=thread_row.id,
                account_id=account.id,
                phone=account.phone,
                farm_id=farm_id,
                tenant_id=farm.tenant_id,
                farm_config=farm,
                assigned_channels=channel_chunk,
                session_manager=self.session_mgr,
                ai_router_func=_import_route_ai_task(),
                redis_client=self.redis,
                publish_event_func=self.publish_event,
            )
            threads.append(thread)

            # Update DB row: assigned channels + status + started_at
            await session.execute(
                update(FarmThreadModel)
                .where(FarmThreadModel.id == thread_row.id)
                .values(
                    assigned_channels=[_channel_to_dict(c) for c in channel_chunk],
                    status="subscribing",
                    started_at=utcnow(),
                    updated_at=utcnow(),
                )
            )

        # Mark farm running
        await session.execute(
            update(FarmConfig)
            .where(FarmConfig.id == farm_id)
            .values(status="running", updated_at=utcnow())
        )
        await session.commit()

        # Register in memory before spawning tasks so stop() can always find it
        self._active_farms[farm_id] = {
            "threads": threads,
            "status": "running",
            "started_at": utcnow(),
            "tasks": [],
            "tenant_id": farm.tenant_id,
        }

        # Spawn async tasks for each thread with error logging callbacks
        def _on_thread_done(t: asyncio.Task, fid: int = farm_id) -> None:
            exc = t.exception() if not t.cancelled() else None
            if exc:
                log.error("farm %s thread %s failed: %s", fid, t.get_name(), exc, exc_info=exc)

        tasks = []
        for thread in threads:
            task = asyncio.create_task(thread.run(), name=f"farm_{farm_id}_thread_{thread.thread_id}")
            task.add_done_callback(_on_thread_done)
            tasks.append(task)
        self._active_farms[farm_id]["tasks"] = tasks

        await self.publish_event(
            farm_id=farm_id,
            thread_id=None,
            event_type="farm_started",
            message=f"Farm '{farm.name}' started with {len(threads)} threads",
            severity="info",
            metadata={"thread_count": len(threads), "channel_count": len(channels)},
        )

        log.info(
            f"Farm {farm_id} started: {len(threads)} threads, {len(channels)} channels"
        )
        return await self.get_farm_status(farm_id, session)

    async def stop_farm(self, farm_id: int, session: AsyncSession) -> dict:
        """Signal all threads to stop gracefully, then update DB."""
        entry = self._active_farms.get(farm_id)
        if entry is None:
            log.warning(f"Farm {farm_id}: not in active registry, updating DB only")
            await session.execute(
                update(FarmConfig)
                .where(FarmConfig.id == farm_id)
                .values(status="stopped", updated_at=utcnow())
            )
            await session.commit()
            return {"farm_id": farm_id, "status": "stopped"}

        # Signal threads
        threads: list = entry.get("threads", [])
        for thread in threads:
            await thread.stop()

        # Wait for tasks with a timeout
        tasks = entry.get("tasks", [])
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=30.0)
            for t in pending:
                t.cancel()

        # Update DB
        await session.execute(
            update(FarmConfig)
            .where(FarmConfig.id == farm_id)
            .values(status="stopped", updated_at=utcnow())
        )
        await session.execute(
            update(FarmThreadModel)
            .where(FarmThreadModel.farm_id == farm_id)
            .values(status="stopped", updated_at=utcnow())
        )
        await session.commit()

        del self._active_farms[farm_id]

        await self.publish_event(
            farm_id=farm_id,
            thread_id=None,
            event_type="farm_stopped",
            message=f"Farm {farm_id} stopped",
            severity="info",
        )

        log.info(f"Farm {farm_id} stopped")
        return {"farm_id": farm_id, "status": "stopped"}

    async def pause_farm(self, farm_id: int, session: AsyncSession) -> dict:
        """Pause all threads (they will finish their current comment then sleep)."""
        entry = self._active_farms.get(farm_id)
        if entry is None:
            raise ValueError(f"Farm {farm_id} is not running")

        for thread in entry.get("threads", []):
            thread.pause()

        entry["status"] = "paused"
        await session.execute(
            update(FarmConfig)
            .where(FarmConfig.id == farm_id)
            .values(status="paused", updated_at=utcnow())
        )
        await session.commit()

        await self.publish_event(
            farm_id=farm_id,
            thread_id=None,
            event_type="farm_paused",
            message=f"Farm {farm_id} paused",
            severity="info",
        )
        return {"farm_id": farm_id, "status": "paused"}

    async def resume_farm(self, farm_id: int, session: AsyncSession) -> dict:
        """Resume all paused threads."""
        entry = self._active_farms.get(farm_id)
        if entry is None:
            raise ValueError(f"Farm {farm_id} is not running")

        for thread in entry.get("threads", []):
            thread.resume()

        entry["status"] = "running"
        await session.execute(
            update(FarmConfig)
            .where(FarmConfig.id == farm_id)
            .values(status="running", updated_at=utcnow())
        )
        await session.commit()

        await self.publish_event(
            farm_id=farm_id,
            thread_id=None,
            event_type="farm_resumed",
            message=f"Farm {farm_id} resumed",
            severity="info",
        )
        return {"farm_id": farm_id, "status": "running"}

    async def get_farm_status(self, farm_id: int, session: AsyncSession) -> dict:
        """Return aggregate: total threads, running, errored, comments sent, etc."""
        farm = await self._load_farm(farm_id, session)
        if farm is None:
            raise ValueError(f"Farm {farm_id} not found")

        thread_rows = await self._load_thread_rows(farm_id, session)

        total = len(thread_rows)
        by_status: dict[str, int] = {}
        comments_sent = 0
        comments_failed = 0
        for row in thread_rows:
            by_status[row.status] = by_status.get(row.status, 0) + 1
            comments_sent += row.stats_comments_sent or 0
            comments_failed += row.stats_comments_failed or 0

        return {
            "farm_id": farm_id,
            "name": farm.name,
            "status": farm.status,
            "total_threads": total,
            "threads_by_status": by_status,
            "total_comments_sent": comments_sent,
            "total_comments_failed": comments_failed,
            "in_memory": farm_id in self._active_farms,
        }

    async def distribute_channels(self, farm_id: int, session: AsyncSession) -> dict:
        """
        Load channel_entries for the farm's workspace and re-split them evenly
        across all active threads. Updates farm_threads.assigned_channels in DB.
        """
        farm = await self._load_farm(farm_id, session)
        if farm is None:
            raise ValueError(f"Farm {farm_id} not found")

        thread_rows = await self._load_thread_rows(farm_id, session)
        if not thread_rows:
            return {"distributed": 0, "threads": 0}

        channels = await self._load_channels_for_farm(farm, session)
        chunks = _split_evenly(channels, len(thread_rows))

        for thread_row, chunk in zip(thread_rows, chunks):
            ch_dicts = [_channel_to_dict(c) for c in chunk]
            await session.execute(
                update(FarmThreadModel)
                .where(FarmThreadModel.id == thread_row.id)
                .values(assigned_channels=ch_dicts, updated_at=utcnow())
            )

            # Also update the in-memory thread object if running
            entry = self._active_farms.get(farm_id)
            if entry:
                for t in entry.get("threads", []):
                    if t.thread_id == thread_row.id:
                        t.assigned_channels = chunk

        await session.commit()
        return {"distributed": len(channels), "threads": len(thread_rows)}

    async def redistribute_on_failure(
        self,
        farm_id: int,
        failed_thread_id: int,
        session: AsyncSession,
    ) -> dict:
        """
        Take channels from a failed thread and redistribute them to the remaining
        active threads.
        """
        farm = await self._load_farm(farm_id, session)
        if farm is None:
            raise ValueError(f"Farm {farm_id} not found")

        thread_rows = await self._load_thread_rows(farm_id, session)
        failed_row = next((r for r in thread_rows if r.id == failed_thread_id), None)
        if failed_row is None:
            return {"redistributed": 0}

        orphan_channels = list(failed_row.assigned_channels or [])
        if not orphan_channels:
            return {"redistributed": 0}

        active_rows = [
            r for r in thread_rows
            if r.id != failed_thread_id and r.status not in ("stopped", "error", "quarantine")
        ]
        if not active_rows:
            log.warning(
                f"Farm {farm_id}: no active threads to redistribute to after failure "
                f"of thread {failed_thread_id}"
            )
            return {"redistributed": 0}

        # Round-robin orphan channels across active threads
        for i, ch_dict in enumerate(orphan_channels):
            target = active_rows[i % len(active_rows)]
            current = list(target.assigned_channels or [])
            current.append(ch_dict)
            await session.execute(
                update(FarmThreadModel)
                .where(FarmThreadModel.id == target.id)
                .values(assigned_channels=current, updated_at=utcnow())
            )
            # Update in-memory
            entry = self._active_farms.get(farm_id)
            if entry:
                for t in entry.get("threads", []):
                    if t.thread_id == target.id:
                        t.assigned_channels = current  # type: ignore[assignment]

        await session.commit()

        await self.publish_event(
            farm_id=farm_id,
            thread_id=failed_thread_id,
            event_type="channels_redistributed",
            message=(
                f"Thread {failed_thread_id} failed; redistributed "
                f"{len(orphan_channels)} channels to {len(active_rows)} threads"
            ),
            severity="warn",
            metadata={
                "failed_thread_id": failed_thread_id,
                "redistributed_count": len(orphan_channels),
                "target_thread_ids": [r.id for r in active_rows],
            },
        )
        return {"redistributed": len(orphan_channels)}

    async def publish_event(
        self,
        farm_id: int,
        thread_id: Optional[int],
        event_type: str,
        message: str,
        severity: str = "info",
        metadata: Optional[dict] = None,
    ) -> None:
        """
        Insert a FarmEvent row and publish the same payload to Redis pub/sub
        `farm:{farm_id}:events`.

        This method is intentionally session-free: it opens its own short
        session so it can be called from thread coroutines that run outside
        the request lifecycle.
        """
        from storage.sqlite_db import async_session as _async_session

        payload = {
            "farm_id": farm_id,
            "thread_id": thread_id,
            "event_type": event_type,
            "severity": severity,
            "message": message,
            "metadata": metadata or {},
            "created_at": utcnow().isoformat(),
        }

        # Persist to DB
        try:
            async with _async_session() as sess:
                async with sess.begin():
                    # Determine tenant_id from in-memory state or DB lookup.
                    # Never fall back to 0 — skip RLS-unscoped writes.
                    tenant_id = None
                    farm_entry = self._active_farms.get(farm_id)
                    if farm_entry and farm_entry.get("tenant_id"):
                        tenant_id = farm_entry["tenant_id"]
                    else:
                        row = (await sess.execute(
                            select(FarmConfig.tenant_id).where(FarmConfig.id == farm_id)
                        )).scalar_one_or_none()
                        tenant_id = row if row else None

                    if not tenant_id:
                        log.warning(
                            f"FarmOrchestrator.publish_event: cannot resolve "
                            f"tenant_id for farm {farm_id}, skipping DB write"
                        )
                        return

                    await apply_session_rls_context(sess, tenant_id=tenant_id)

                    event = FarmEvent(
                        tenant_id=tenant_id,
                        farm_id=farm_id,
                        thread_id=thread_id,
                        event_type=event_type,
                        severity=severity,
                        message=message,
                        event_metadata=metadata or {},
                        created_at=utcnow(),
                    )
                    sess.add(event)
        except Exception as exc:
            log.warning(f"FarmOrchestrator.publish_event: DB write failed: {exc}")

        # Publish to Redis pub/sub
        try:
            channel = f"farm:{farm_id}:events"
            await self.redis.publish(channel, json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            log.warning(f"FarmOrchestrator.publish_event: Redis publish failed: {exc}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_farm(self, farm_id: int, session: AsyncSession) -> Optional[FarmConfig]:
        result = await session.execute(
            select(FarmConfig).where(FarmConfig.id == farm_id)
        )
        return result.scalar_one_or_none()

    async def _load_farm_no_session(self, farm_id: int, tenant_id: Optional[int] = None) -> Optional[FarmConfig]:
        """Load farm config without a caller-supplied session (for event publishing)."""
        from storage.sqlite_db import async_session as _async_session

        async with _async_session() as sess:
            async with sess.begin():
                if tenant_id is not None:
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                result = await sess.execute(
                    select(FarmConfig).where(FarmConfig.id == farm_id)
                )
                return result.scalar_one_or_none()

    async def _load_thread_rows(
        self, farm_id: int, session: AsyncSession
    ) -> list[FarmThreadModel]:
        result = await session.execute(
            select(FarmThreadModel).where(FarmThreadModel.farm_id == farm_id)
        )
        return list(result.scalars().all())

    async def _load_channels_for_farm(
        self, farm: FarmConfig, session: AsyncSession
    ) -> list[ChannelEntry]:
        """Load all active, non-blacklisted channel entries for the farm's workspace."""
        result = await session.execute(
            select(ChannelEntry)
            .join(ChannelDatabase, ChannelEntry.database_id == ChannelDatabase.id)
            .where(
                ChannelDatabase.workspace_id == farm.workspace_id,
                ChannelEntry.tenant_id == farm.tenant_id,
                ChannelEntry.blacklisted == False,  # noqa: E712
                ChannelEntry.has_comments == True,   # noqa: E712
            )
        )
        return list(result.scalars().all())


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _split_evenly(items: list, n: int) -> list[list]:
    """Split a list into n roughly equal chunks."""
    if n <= 0:
        return []
    if not items:
        return [[] for _ in range(n)]
    chunk_size = math.ceil(len(items) / n)
    return [items[i: i + chunk_size] for i in range(0, len(items), chunk_size)]


def _channel_to_dict(channel: ChannelEntry) -> dict:
    return {
        "id": channel.id,
        "telegram_id": channel.telegram_id,
        "username": channel.username,
        "title": channel.title,
        "has_comments": channel.has_comments,
    }


def _import_route_ai_task() -> Callable:
    """Lazy import to avoid circular dependency at module load time."""
    from core.ai_router import route_ai_task
    return route_ai_task
