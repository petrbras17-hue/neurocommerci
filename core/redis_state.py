"""Shared state across workers via Redis."""

from __future__ import annotations

import json
import time
from typing import Optional

import redis.asyncio as redis

from config import settings
from utils.logger import log


class RedisState:
    """Distributed shared state for rate limiting, health, worker claims."""

    def __init__(self):
        self._redis: Optional[redis.Redis] = None

    async def connect(self):
        if self._redis is None:
            self._redis = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )
            log.info("RedisState: connected")

    async def close(self):
        if self._redis:
            await self._redis.close()
            self._redis = None

    # --- Rate Limiter State ---

    async def get_rate_state(self, phone: str) -> dict:
        """Get rate limiter state for account."""
        data = await self._redis.hget("rate_state", phone)
        if data:
            return json.loads(data)
        return {}

    async def set_rate_state(self, phone: str, state: dict):
        """Set rate limiter state for account."""
        await self._redis.hset("rate_state", phone, json.dumps(state))

    async def increment_comments(self, phone: str) -> int:
        """Atomically increment daily comment counter. Returns new count."""
        key = f"comments_today:{phone}"
        count = await self._redis.incr(key)
        # Set TTL to expire at midnight UTC (auto-reset)
        if count == 1:
            # Calculate seconds until midnight
            now = time.time()
            midnight = (int(now) // 86400 + 1) * 86400
            ttl = int(midnight - now)
            await self._redis.expire(key, ttl)
        return count

    async def get_comments_today(self, phone: str) -> int:
        """Get today's comment count."""
        val = await self._redis.get(f"comments_today:{phone}")
        return int(val) if val else 0

    # --- Worker Claims ---

    async def claim_account(self, phone: str, worker_id: str, ttl: int = 300) -> bool:
        """Claim account for worker. Returns True if claimed."""
        key = f"worker_claim:{phone}"
        result = await self._redis.set(key, worker_id, nx=True, ex=ttl)
        return result is not None

    async def renew_claim(self, phone: str, worker_id: str, ttl: int = 300) -> bool:
        """Renew claim TTL. Only succeeds if we still own it."""
        key = f"worker_claim:{phone}"
        current = await self._redis.get(key)
        if current == worker_id:
            await self._redis.expire(key, ttl)
            return True
        return False

    async def release_claim(self, phone: str, worker_id: str):
        """Release account claim."""
        key = f"worker_claim:{phone}"
        current = await self._redis.get(key)
        if current == worker_id:
            await self._redis.delete(key)

    async def get_unclaimed_phones(self, all_phones: list[str]) -> list[str]:
        """Get phones not claimed by any worker."""
        pipe = self._redis.pipeline()
        for phone in all_phones:
            pipe.exists(f"worker_claim:{phone}")
        results = await pipe.execute()
        return [phone for phone, exists in zip(all_phones, results) if not exists]

    # --- Health Status ---

    async def set_health(self, phone: str, status: str):
        """Set account health status."""
        await self._redis.hset("health_status", phone, status)

    async def get_health(self, phone: str) -> str:
        """Get account health status."""
        return await self._redis.hget("health_status", phone) or "unknown"

    # --- Distributed Lock ---

    async def acquire_lock(self, key: str, ttl: int = 30) -> bool:
        """Acquire distributed lock. Returns True if acquired."""
        return await self._redis.set(f"lock:{key}", "1", nx=True, ex=ttl) is not None

    async def release_lock(self, key: str):
        """Release distributed lock."""
        await self._redis.delete(f"lock:{key}")

    # --- Worker Heartbeat ---

    async def worker_heartbeat(self, worker_id: str, account_count: int):
        """Report worker is alive."""
        await self._redis.hset("workers", worker_id, json.dumps({
            "accounts": account_count,
            "last_seen": time.time(),
        }))
        await self._redis.expire("workers", 600)

    async def get_active_workers(self) -> dict:
        """Get all active workers."""
        data = await self._redis.hgetall("workers")
        result = {}
        now = time.time()
        for worker_id, info_str in data.items():
            info = json.loads(info_str)
            if now - info["last_seen"] < 600:  # 10 min timeout
                result[worker_id] = info
        return result


# Global instance
redis_state = RedisState()
