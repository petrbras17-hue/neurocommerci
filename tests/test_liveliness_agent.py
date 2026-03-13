"""Tests for LifelinessAgent scheduling and activity logic."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.liveliness_agent import (
    AccountLifeLoop,
    LifelinessAgent,
    _is_sleep_time,
    _jitter,
)


class TestHelpers:

    def test_is_sleep_time_night(self):
        dt = datetime(2026, 3, 12, 2, 0, tzinfo=timezone.utc)
        assert _is_sleep_time(dt, sleep_start=23, sleep_end=7) is True

    def test_is_sleep_time_day(self):
        dt = datetime(2026, 3, 12, 14, 0, tzinfo=timezone.utc)
        assert _is_sleep_time(dt, sleep_start=23, sleep_end=7) is False

    def test_is_sleep_time_boundary_start(self):
        dt = datetime(2026, 3, 12, 23, 30, tzinfo=timezone.utc)
        assert _is_sleep_time(dt, sleep_start=23, sleep_end=7) is True

    def test_jitter_within_range(self):
        for _ in range(100):
            val = _jitter(100.0, 0.3)
            assert 70.0 <= val <= 130.0


class TestAccountLifeLoop:

    @pytest.fixture
    def mock_deps(self):
        return {
            "client_factory": AsyncMock(),
            "event_bus": AsyncMock(),
            "anti_detection": MagicMock(),
            "health_scorer": AsyncMock(),
        }

    def test_init(self, mock_deps):
        loop = AccountLifeLoop(
            account_id=1,
            phone="+79637428613",
            session_file="data/sessions/79637428613.session",
            proxy={"host": "proxy.test", "port": 9200, "username": "u", "password": "p"},
            **mock_deps,
        )
        assert loop.phone == "+79637428613"
        assert loop.account_id == 1


class TestLifelinessAgent:

    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    def test_init(self, mock_redis):
        agent = LifelinessAgent(redis_client=mock_redis)
        assert agent._active_loops == {}
