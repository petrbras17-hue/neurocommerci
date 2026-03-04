"""Redis-backed task queue for distributed account operations."""

from __future__ import annotations

import json
import uuid
from typing import Optional

import redis.asyncio as redis

from config import settings
from utils.logger import log


class TaskQueue:
    """Redis-backed task queue for account operations."""

    def __init__(self):
        self._redis: Optional[redis.Redis] = None

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

    async def enqueue(self, queue_name: str, payload: dict) -> str:
        """Add task to queue. Returns task_id."""
        task_id = str(uuid.uuid4())[:8]
        payload["_task_id"] = task_id
        await self._redis.lpush(f"queue:{queue_name}", json.dumps(payload))
        log.debug(f"TaskQueue: enqueued {queue_name} task {task_id}")
        return task_id

    async def dequeue(self, queue_name: str, timeout: int = 0) -> Optional[dict]:
        """Pop task from queue. Blocks for `timeout` seconds (0 = forever)."""
        result = await self._redis.brpop(f"queue:{queue_name}", timeout=timeout)
        if result is None:
            return None
        _, data = result
        return json.loads(data)

    async def queue_size(self, queue_name: str) -> int:
        """Get number of pending tasks."""
        return await self._redis.llen(f"queue:{queue_name}")

    async def publish(self, channel: str, message: dict):
        """Publish event to PUB/SUB channel."""
        await self._redis.publish(channel, json.dumps(message))

    async def subscribe(self, channel: str):
        """Subscribe to PUB/SUB channel. Returns async iterator."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        return pubsub


# Global instance
task_queue = TaskQueue()
