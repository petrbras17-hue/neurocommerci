"""Tests for the Redis-backed EventBus."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from core.event_bus import EventBus, publish_event


class TestEventBus:
    """Unit tests for EventBus pub/sub wrapper."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.publish = AsyncMock(return_value=1)
        return redis

    @pytest.fixture
    def bus(self, mock_redis):
        return EventBus(redis_client=mock_redis)

    @pytest.mark.asyncio
    async def test_publish_event_formats_json(self, bus, mock_redis):
        await bus.publish("account", {"phone": "+7999", "status": "frozen"})
        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        assert call_args[0][0] == "nc:event:account"
        payload = json.loads(call_args[0][1])
        assert payload["phone"] == "+7999"
        assert payload["status"] == "frozen"
        assert "ts" in payload

    @pytest.mark.asyncio
    async def test_publish_event_adds_timestamp(self, bus, mock_redis):
        await bus.publish("deploy", {"commit": "abc123"})
        payload = json.loads(mock_redis.publish.call_args[0][1])
        assert "ts" in payload
        assert isinstance(payload["ts"], str)

    @pytest.mark.asyncio
    async def test_publish_event_channel_prefix(self, bus, mock_redis):
        await bus.publish("health", {"score": 75})
        channel = mock_redis.publish.call_args[0][0]
        assert channel == "nc:event:health"

    @pytest.mark.asyncio
    async def test_publish_event_handles_redis_error(self, bus, mock_redis):
        mock_redis.publish.side_effect = Exception("connection lost")
        # Should not raise — fire-and-forget semantics
        await bus.publish("error", {"msg": "test"})

    @pytest.mark.asyncio
    async def test_module_level_publish_without_init(self):
        """publish_event before set_redis should be a no-op, not crash."""
        import core.event_bus as eb
        old = eb._global_bus
        eb._global_bus = None
        try:
            await publish_event("test", {"x": 1})  # should not raise
        finally:
            eb._global_bus = old
