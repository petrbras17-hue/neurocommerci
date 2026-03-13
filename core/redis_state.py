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
            await self._redis.aclose()
            self._redis = None

    # --- Rate Limiter State ---

    async def get_rate_state(self, phone: str) -> dict:
        """Get rate limiter state for account."""
        data = await self._redis.hget("rate_state", phone)
        if data:
            try:
                return json.loads(data)
            except (json.JSONDecodeError, TypeError):
                return {}
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

    async def acquire_owner_lock(self, key: str, owner: str, ttl: int = 30) -> bool:
        """Acquire a distributed lock owned by a specific instance."""
        return await self._redis.set(f"lock:{key}", owner, nx=True, ex=ttl) is not None

    async def get_lock_owner(self, key: str) -> str | None:
        """Return current lock owner, if any."""
        owner = await self._redis.get(f"lock:{key}")
        return str(owner) if owner else None

    async def renew_owner_lock(self, key: str, owner: str, ttl: int = 30) -> bool:
        """Renew lock TTL only if the caller still owns the lock."""
        result = await self._redis.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('expire', KEYS[1], tonumber(ARGV[2]))
            end
            return 0
            """,
            1,
            f"lock:{key}",
            owner,
            ttl,
        )
        return bool(result)

    async def release_owner_lock(self, key: str, owner: str) -> bool:
        """Release lock only if the caller still owns it."""
        result = await self._redis.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """,
            1,
            f"lock:{key}",
            owner,
        )
        return bool(result)

    # --- Worker Heartbeat ---

    async def worker_heartbeat(
        self,
        worker_id: str,
        account_count: int,
        metrics: Optional[dict] = None,
    ):
        """Report worker is alive."""
        payload = {
            "accounts": account_count,
            "last_seen": time.time(),
        }
        if metrics:
            payload["metrics"] = metrics
        await self._redis.hset("workers", worker_id, json.dumps(payload))
        await self._redis.expire("workers", 600)

    async def get_active_workers(self) -> dict:
        """Get all active workers."""
        data = await self._redis.hgetall("workers")
        result = {}
        now = time.time()
        for worker_id, info_str in data.items():
            try:
                info = json.loads(info_str)
            except (json.JSONDecodeError, TypeError):
                continue
            if now - info["last_seen"] < 600:  # 10 min timeout
                result[worker_id] = info
        return result

    async def count_claims(self) -> int:
        """Count claimed accounts across all workers."""
        count = 0
        async for _key in self._redis.scan_iter(match="worker_claim:*"):
            count += 1
        return count

    async def claims_by_worker(self) -> dict[str, int]:
        """Count claimed accounts grouped by worker id."""
        result: dict[str, int] = {}
        async for key in self._redis.scan_iter(match="worker_claim:*"):
            worker_id = await self._redis.get(key)
            if not worker_id:
                continue
            result[worker_id] = result.get(worker_id, 0) + 1
        return result

    async def claims_map(self) -> dict[str, str]:
        """Return {phone: worker_id} map for current claims."""
        result: dict[str, str] = {}
        async for key in self._redis.scan_iter(match="worker_claim:*"):
            worker_id = await self._redis.get(key)
            if not worker_id:
                continue
            phone = str(key).split("worker_claim:", 1)[-1]
            result[phone] = worker_id
        return result

    async def clear_all_claims(self) -> int:
        """Delete all worker_claim:* keys and return removed count."""
        keys: list[str] = []
        async for key in self._redis.scan_iter(match="worker_claim:*"):
            keys.append(str(key))
        if not keys:
            return 0
        removed = await self._redis.delete(*keys)
        return int(removed or 0)

    async def clear_worker_heartbeats(self) -> int:
        """Delete worker heartbeat hash and return removed-key count."""
        removed = await self._redis.delete("workers")
        return int(removed or 0)

    async def clear_hash(self, hash_name: str) -> int:
        removed = await self._redis.delete(hash_name)
        return int(removed or 0)

    async def delete_pattern(self, pattern: str) -> int:
        keys: list[str] = []
        async for key in self._redis.scan_iter(match=pattern):
            keys.append(str(key))
        if not keys:
            return 0
        removed = await self._redis.delete(*keys)
        return int(removed or 0)

    # --- Runtime flags ---

    async def set_runtime_flag(self, name: str, value: str):
        await self._redis.set(f"runtime_flag:{name}", value)

    async def get_runtime_flag(self, name: str, default: str = "") -> str:
        value = await self._redis.get(f"runtime_flag:{name}")
        return str(value) if value is not None else default

    # --- Generic JSON hashes ---

    async def set_json_hash_value(self, hash_name: str, key: str, payload: dict):
        await self._redis.hset(hash_name, key, json.dumps(payload, ensure_ascii=False))

    async def get_json_hash_value(self, hash_name: str, key: str) -> dict | None:
        raw = await self._redis.hget(hash_name, key)
        if not raw:
            return None
        return json.loads(raw)


# Global instance
redis_state = RedisState()
