"""
Redis-backed event bus for cross-service pub/sub.

Usage:
    from core.event_bus import publish_event, init_event_bus

    # At service startup:
    init_event_bus(redis_client)

    # Anywhere in code:
    await publish_event("account", {"phone": "+7999", "status": "frozen"})
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

CHANNEL_PREFIX = "nc:event:"


class EventBus:
    """Thin wrapper around Redis pub/sub for structured events."""

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client

    async def publish(self, category: str, data: dict[str, Any]) -> None:
        """Publish an event to nc:event:{category}. Fire-and-forget."""
        channel = f"{CHANNEL_PREFIX}{category}"
        payload = {**data, "ts": datetime.now(timezone.utc).isoformat()}
        try:
            await self._redis.publish(channel, json.dumps(payload, default=str))
        except Exception as exc:
            log.warning("event_bus: publish failed channel=%s error=%s", channel, exc)

    async def subscribe(
        self,
        patterns: list[str],
        callback: Callable[[str, dict[str, Any]], Coroutine],
    ) -> None:
        """Subscribe to channel patterns and call callback(channel, data) for each message.

        Blocks forever — run as an asyncio task.
        patterns: e.g. ["nc:event:*"] or ["nc:event:account", "nc:event:health"]
        """
        pubsub = self._redis.pubsub()
        if any("*" in p for p in patterns):
            await pubsub.psubscribe(*patterns)
        else:
            await pubsub.subscribe(*patterns)

        log.info("event_bus: subscribed to %s", patterns)
        try:
            async for message in pubsub.listen():
                if message["type"] not in ("message", "pmessage"):
                    continue
                channel = (message.get("channel") or b"").decode("utf-8", errors="replace")
                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    data = {"raw": str(message["data"])}
                try:
                    await callback(channel, data)
                except Exception as exc:
                    log.error("event_bus: callback error channel=%s error=%s", channel, exc)
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()


# ---------------------------------------------------------------------------
# Module-level convenience API
# ---------------------------------------------------------------------------

_global_bus: Optional[EventBus] = None


def init_event_bus(redis_client: aioredis.Redis) -> EventBus:
    """Initialize the global event bus. Call once at service startup."""
    global _global_bus
    _global_bus = EventBus(redis_client)
    return _global_bus


def get_event_bus() -> Optional[EventBus]:
    """Return the global EventBus instance, or None."""
    return _global_bus


async def publish_event(category: str, data: dict[str, Any]) -> None:
    """Publish via the global bus. No-op if bus is not initialized."""
    if _global_bus is not None:
        await _global_bus.publish(category, data)
