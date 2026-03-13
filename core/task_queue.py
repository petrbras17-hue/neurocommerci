"""Redis-backed task queue for distributed account operations."""

from __future__ import annotations

import json
import time
import uuid
from typing import Optional

import redis.asyncio as redis

from config import settings
from utils.logger import log


_QUEUE_ALIASES = {
    "comments": "comment_tasks",
    "comment_tasks": "comment_tasks",
    "packaging:pending": "packaging_tasks",
    "packaging_tasks": "packaging_tasks",
    "recovery_tasks": "recovery_tasks",
    "channel_discovery": "channel_discovery",
    "post_candidates": "post_candidates",
    "assistant_tasks": "assistant_tasks",
    "creative_tasks": "creative_tasks",
    "context_tasks": "context_tasks",
}


class TaskQueue:
    """Redis-backed task queue for account operations."""

    def __init__(self):
        self._redis: Optional[redis.Redis] = None

    @staticmethod
    def _queue_key(queue_name: str) -> str:
        normalized = _QUEUE_ALIASES.get(str(queue_name or "").strip(), str(queue_name or "").strip())
        return f"queue:{normalized}"

    async def connect(self):
        """Connect to Redis."""
        if self._redis is None:
            self._redis = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )
            # Test connection
            await self._redis.ping()
            log.info(f"TaskQueue: connected to Redis ({settings.REDIS_URL})")

    async def close(self):
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None

    async def cache_get(self, key: str) -> Optional[str]:
        """Get a value from Redis cache."""
        await self.connect()
        if self._redis is None:
            return None
        return await self._redis.get(key)

    async def cache_set(self, key: str, value: str, ex: int = 3600) -> None:
        """Set a value in Redis cache with expiry."""
        await self.connect()
        if self._redis is not None:
            await self._redis.set(key, value, ex=ex)

    async def cache_delete(self, key: str) -> None:
        """Delete a key from Redis cache."""
        await self.connect()
        if self._redis is not None:
            await self._redis.delete(key)

    async def enqueue(self, queue_name: str, payload: dict) -> str:
        """Add task to queue. Returns task_id."""
        payload = dict(payload)
        task_id = str(payload.get("_task_id") or str(uuid.uuid4())[:8])
        payload["_task_id"] = task_id
        payload["_attempts"] = int(payload.get("_attempts", 0))
        await self._redis.lpush(self._queue_key(queue_name), json.dumps(payload, ensure_ascii=False))
        log.debug(f"TaskQueue: enqueued {queue_name} task {task_id}")
        return task_id

    async def dequeue(self, queue_name: str, timeout: int = 0) -> Optional[dict]:
        """Pop task from queue. Blocks for `timeout` seconds (0 = forever)."""
        result = await self._redis.brpop(self._queue_key(queue_name), timeout=timeout)
        if result is None:
            return None
        _, data = result
        return json.loads(data)

    async def reserve(
        self,
        queue_name: str,
        *,
        consumer_id: str,
        timeout: int = 0,
        lease_sec: int = 300,
    ) -> Optional[dict]:
        """Atomically reserve a task with lease semantics."""
        await self.recover_expired_leases(queue_name)

        queue_key = self._queue_key(queue_name)
        source = queue_key
        inflight = f"{queue_key}:processing"
        raw = await self._redis.brpoplpush(source, inflight, timeout=timeout)
        if raw is None:
            return None

        payload = json.loads(raw)
        task_id = str(payload.get("_task_id") or str(uuid.uuid4())[:8])
        payload["_task_id"] = task_id
        payload["_attempts"] = int(payload.get("_attempts", 0))
        lease = {
            "task_id": task_id,
            "queue_name": queue_name,
            "consumer_id": consumer_id,
            "reserved_at": time.time(),
            "deadline": time.time() + max(1, lease_sec),
            "raw": raw,
        }
        await self._redis.hset(f"{queue_key}:leases", task_id, json.dumps(lease))
        payload["_lease"] = {
            "consumer_id": consumer_id,
            "lease_sec": max(1, lease_sec),
        }
        return payload

    async def ack(self, queue_name: str, task_id: str) -> bool:
        """Acknowledge and remove an in-flight task."""
        queue_key = self._queue_key(queue_name)
        lease_key = f"{queue_key}:leases"
        inflight = f"{queue_key}:processing"
        lease_raw = await self._redis.hget(lease_key, task_id)
        if not lease_raw:
            return False
        lease = json.loads(lease_raw)
        await self._redis.lrem(inflight, 1, lease.get("raw"))
        await self._redis.hdel(lease_key, task_id)
        return True

    async def requeue(
        self,
        queue_name: str,
        task_id: str,
        payload: dict,
        *,
        reason: str = "",
    ) -> bool:
        """Return an in-flight task to the pending queue."""
        queue_key = self._queue_key(queue_name)
        lease_key = f"{queue_key}:leases"
        inflight = f"{queue_key}:processing"
        lease_raw = await self._redis.hget(lease_key, task_id)
        if not lease_raw:
            return False
        lease = json.loads(lease_raw)
        await self._redis.lrem(inflight, 1, lease.get("raw"))
        await self._redis.hdel(lease_key, task_id)

        payload = dict(payload)
        payload["_task_id"] = task_id
        payload["_attempts"] = int(payload.get("_attempts", 0))
        if reason:
            payload["_last_requeue_reason"] = reason
        await self._redis.lpush(queue_key, json.dumps(payload, ensure_ascii=False))
        return True

    async def dead_letter(
        self,
        queue_name: str,
        task_id: str,
        payload: dict,
        *,
        reason: str,
    ) -> bool:
        """Move an in-flight task to DLQ."""
        queue_key = self._queue_key(queue_name)
        lease_key = f"{queue_key}:leases"
        inflight = f"{queue_key}:processing"
        lease_raw = await self._redis.hget(lease_key, task_id)
        if not lease_raw:
            return False
        lease = json.loads(lease_raw)
        await self._redis.lrem(inflight, 1, lease.get("raw"))
        await self._redis.hdel(lease_key, task_id)

        payload = dict(payload)
        payload["_task_id"] = task_id
        payload["_dead_letter_reason"] = reason
        payload["_dead_lettered_at"] = time.time()
        await self._redis.lpush(
            f"{queue_key}:dlq",
            json.dumps(payload, ensure_ascii=False),
        )
        return True

    async def recover_expired_leases(self, queue_name: str) -> int:
        """Return expired leases back to the pending queue."""
        now = time.time()
        queue_key = self._queue_key(queue_name)
        lease_key = f"{queue_key}:leases"
        inflight = f"{queue_key}:processing"
        recovered = 0
        leases = await self._redis.hgetall(lease_key)
        for task_id, lease_raw in leases.items():
            try:
                lease = json.loads(lease_raw)
            except Exception:
                await self._redis.hdel(lease_key, task_id)
                continue
            if float(lease.get("deadline", 0)) > now:
                continue
            raw = lease.get("raw")
            removed = await self._redis.lrem(inflight, 1, raw)
            await self._redis.hdel(lease_key, task_id)
            if removed:
                payload = json.loads(raw)
                payload["_task_id"] = str(payload.get("_task_id") or task_id)
                payload["_attempts"] = int(payload.get("_attempts", 0)) + 1
                payload["_lease_expired"] = True
                await self._redis.lpush(
                    queue_key,
                    json.dumps(payload, ensure_ascii=False),
                )
                recovered += 1
        return recovered

    async def queue_size(self, queue_name: str) -> int:
        """Get number of pending tasks."""
        return await self._redis.llen(self._queue_key(queue_name))

    async def queue_sizes(self, queue_name: str) -> dict[str, int]:
        """Return pending/inflight/dlq sizes for a queue."""
        queue_key = self._queue_key(queue_name)
        pending = await self._redis.llen(queue_key)
        inflight = await self._redis.llen(f"{queue_key}:processing")
        dlq = await self._redis.llen(f"{queue_key}:dlq")
        leases = await self._redis.hlen(f"{queue_key}:leases")
        return {
            "pending": int(pending or 0),
            "inflight": int(inflight or 0),
            "dlq": int(dlq or 0),
            "leases": int(leases or 0),
        }

    async def purge_queue(self, queue_name: str) -> dict[str, int]:
        """Delete pending/inflight/dlq/lease keys for a queue."""
        queue_key = self._queue_key(queue_name)
        sizes = await self.queue_sizes(queue_name)
        deleted = await self._redis.delete(
            queue_key,
            f"{queue_key}:processing",
            f"{queue_key}:dlq",
            f"{queue_key}:leases",
        )
        return {
            "pending": int(sizes.get("pending", 0)),
            "inflight": int(sizes.get("inflight", 0)),
            "dlq": int(sizes.get("dlq", 0)),
            "leases": int(sizes.get("leases", 0)),
            "keys_deleted": int(deleted or 0),
        }

    async def publish(self, channel: str, message: dict):
        """Publish event to PUB/SUB channel."""
        await self._redis.publish(channel, json.dumps(message))

    async def subscribe(self, channel: str):
        """Subscribe to PUB/SUB channel. Returns async iterator."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        return pubsub

    async def ping(self) -> bool:
        """Return True if Redis is reachable."""
        if self._redis is None:
            return False
        try:
            await self._redis.ping()
            return True
        except Exception:
            return False


# Global instance
task_queue = TaskQueue()
