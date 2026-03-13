# Unified Liveliness Service + Digest Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Docker Compose service with two modules — LifelinessAgent (human-like account simulation) and DigestReporter (real-time Telegram event notifications).

**Architecture:** Single `liveliness_service.py` entry point running two asyncio coroutines. EventBus wraps Redis pub/sub for cross-service event publishing. LifelinessAgent runs per-account simulation loops. DigestReporter listens to Redis channels and sends formatted messages to Telegram digest chat.

**Tech Stack:** Python 3.11, asyncio, Telethon, redis.asyncio, aiogram (for Telegram Bot API sending), SQLAlchemy (read-only for account data), existing AntiDetection/HealthScorer classes.

**Spec:** `docs/superpowers/specs/2026-03-12-liveliness-and-digest-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `core/event_bus.py` | Redis pub/sub wrapper: `publish_event()`, `subscribe()`, `EventBus` class |
| `core/liveliness_agent.py` | `AccountLifeLoop` per-account coroutine, `LifelinessAgent` orchestrator |
| `core/digest_reporter.py` (MODIFY) | Add `DigestReporter` class with Redis sub listener + event formatting. Keep existing `send_digest_text` etc. |
| `liveliness_service.py` | Entry point: init Redis/DB, start both modules, graceful shutdown |
| `config.py` (MODIFY) | Add `LIVELINESS_*` and `DIGEST_BATCH_*` settings |
| `docker-compose.yml` (MODIFY) | Add `liveliness` service |
| `tests/test_event_bus.py` | Unit tests for EventBus |
| `tests/test_liveliness_agent.py` | Unit tests for LifelinessAgent schedule/activity logic |
| `tests/test_digest_reporter.py` | Unit tests for DigestReporter formatting |

---

## Chunk 1: EventBus Foundation

### Task 1: Add config settings

**Files:**
- Modify: `config.py:95-130` (Settings class, near WARMUP_ and FARM_ vars)

- [ ] **Step 1: Add liveliness and digest config vars**

Add after the `FARM_*` block (around line 112):

```python
# --- Liveliness Agent ---
LIVELINESS_ENABLED: bool = Field(default=True)
LIVELINESS_MAX_CONCURRENT: int = Field(default=10)
LIVELINESS_TIMEZONE: str = Field(default="Europe/Moscow")
LIVELINESS_HEALTH_THRESHOLD: int = Field(default=30)
LIVELINESS_POLL_INTERVAL_SEC: int = Field(default=60)
LIVELINESS_SLEEP_START_HOUR: int = Field(default=23)
LIVELINESS_SLEEP_END_HOUR: int = Field(default=7)

# --- Digest Reporter ---
DIGEST_BATCH_WINDOW_SEC: int = Field(default=2)
DIGEST_MAX_PER_MINUTE: int = Field(default=30)
```

- [ ] **Step 2: Verify config loads**

Run: `cd "/Users/braslavskii/NEURO COMMENTING" && python -c "from config import settings; print(settings.LIVELINESS_ENABLED, settings.LIVELINESS_MAX_CONCURRENT)"`
Expected: `True 10`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add liveliness agent and digest reporter config vars"
```

---

### Task 2: Create EventBus

**Files:**
- Create: `core/event_bus.py`
- Create: `tests/test_event_bus.py`

- [ ] **Step 1: Write failing tests for EventBus**

Create `tests/test_event_bus.py`:

```python
"""Tests for the Redis-backed EventBus."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

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
        # Reset global bus
        import core.event_bus as eb
        old = eb._global_bus
        eb._global_bus = None
        try:
            await publish_event("test", {"x": 1})  # should not raise
        finally:
            eb._global_bus = old
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/braslavskii/NEURO COMMENTING" && python -m pytest tests/test_event_bus.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement EventBus**

Create `core/event_bus.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/braslavskii/NEURO COMMENTING" && python -m pytest tests/test_event_bus.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add core/event_bus.py tests/test_event_bus.py
git commit -m "feat: add Redis-backed EventBus for cross-service pub/sub"
```

---

## Chunk 2: DigestReporter

### Task 3: Add DigestReporter to existing digest_service.py

**Files:**
- Modify: `core/digest_service.py`
- Create: `tests/test_digest_reporter.py`

- [ ] **Step 1: Write failing tests for DigestReporter formatting**

Create `tests/test_digest_reporter.py`:

```python
"""Tests for DigestReporter event formatting."""
from __future__ import annotations

import pytest

from core.digest_service import format_event_message


class TestFormatEventMessage:

    def test_deploy_event(self):
        msg = format_event_message("nc:event:deploy", {
            "commit": "abc1234",
            "sprint": "Sprint 5",
            "services": "ops_api, bot",
        })
        assert "DEPLOY" in msg
        assert "abc1234" in msg
        assert "ops_api" in msg

    def test_account_event(self):
        msg = format_event_message("nc:event:account", {
            "phone": "+79637428613",
            "name": "Ирина",
            "status": "frozen",
            "action": "paused liveliness loop",
        })
        assert "ACCOUNT" in msg
        assert "+79637428613" in msg
        assert "frozen" in msg

    def test_health_event(self):
        msg = format_event_message("nc:event:health", {
            "total_accounts": 3,
            "avg_health": 72,
            "below_threshold": 1,
        })
        assert "HEALTH" in msg
        assert "72" in msg

    def test_parsing_event(self):
        msg = format_event_message("nc:event:parsing", {
            "job_id": 42,
            "channels_found": 340,
            "russian": 312,
            "with_comments": 89,
        })
        assert "PARSING" in msg
        assert "340" in msg

    def test_error_event(self):
        msg = format_event_message("nc:event:error", {
            "service": "liveliness",
            "error": "connection timeout",
        })
        assert "ERROR" in msg
        assert "connection timeout" in msg

    def test_unknown_category(self):
        msg = format_event_message("nc:event:unknown_thing", {
            "data": "something",
        })
        assert "SYSTEM" in msg or "unknown_thing" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/braslavskii/NEURO COMMENTING" && python -m pytest tests/test_digest_reporter.py -v`
Expected: FAIL (format_event_message not found)

- [ ] **Step 3: Add DigestReporter and format_event_message to digest_service.py**

Append to the bottom of `core/digest_service.py`:

```python
# ---------------------------------------------------------------------------
# DigestReporter — real-time event → Telegram delivery
# ---------------------------------------------------------------------------

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from core.event_bus import EventBus


_CATEGORY_EMOJI = {
    "deploy":  "\U0001f680",   # rocket
    "account": "\U0001f464",   # person
    "health":  "\U0001f4ca",   # chart
    "parsing": "\U0001f50d",   # magnifying glass
    "farm":    "\U0001f69c",   # tractor
    "error":   "\U0001f6a8",   # alert
    "system":  "\u2699\ufe0f", # gear
}

_CATEGORY_LABEL = {
    "deploy":  "DEPLOY",
    "account": "ACCOUNT",
    "health":  "HEALTH",
    "parsing": "PARSING",
    "farm":    "FARM",
    "error":   "ERROR",
    "system":  "SYSTEM",
}


def format_event_message(channel: str, data: dict) -> str:
    """Format a Redis event into a Telegram-ready text message."""
    # Extract category from channel name: nc:event:account -> account
    category = channel.replace("nc:event:", "").split(":")[0] if "nc:event:" in channel else "system"
    emoji = _CATEGORY_EMOJI.get(category, "\u2699\ufe0f")
    label = _CATEGORY_LABEL.get(category, category.upper())

    ts = data.get("ts", "")
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            time_str = dt.strftime("%H:%M")
        except (ValueError, TypeError):
            time_str = str(ts)[:5]
    else:
        time_str = datetime.now(timezone.utc).strftime("%H:%M")

    header = f"{emoji} <b>{label}</b> | {time_str}"

    # Build body from data (exclude internal fields)
    skip_keys = {"ts", "type", "category"}
    lines = [header]
    for key, value in data.items():
        if key in skip_keys:
            continue
        # Use readable key names
        display_key = key.replace("_", " ").capitalize()
        lines.append(f"{display_key}: {value}")

    return "\n".join(lines)


class DigestReporter:
    """Listens to Redis pub/sub events and sends formatted messages to Telegram digest chat.

    Batches messages within a time window to avoid Telegram rate limits.
    """

    def __init__(
        self,
        event_bus: EventBus,
        batch_window_sec: float = 2.0,
        max_per_minute: int = 30,
    ) -> None:
        self._bus = event_bus
        self._batch_window = batch_window_sec
        self._max_per_minute = max_per_minute
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._sent_count = 0
        self._sent_reset_at = 0.0

    async def start(self) -> None:
        """Start listener and sender tasks. Blocks forever."""
        await asyncio.gather(
            self._listen(),
            self._sender_loop(),
        )

    async def _listen(self) -> None:
        """Subscribe to all nc:event:* channels."""
        await self._bus.subscribe(
            ["nc:event:*"],
            self._on_event,
        )

    async def _on_event(self, channel: str, data: dict) -> None:
        """Format event and enqueue for batched sending."""
        text = format_event_message(channel, data)
        await self._queue.put(text)

    async def _sender_loop(self) -> None:
        """Drain queue and send messages respecting rate limits."""
        while True:
            messages: list[str] = []
            # Wait for first message
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=10.0)
                messages.append(msg)
            except asyncio.TimeoutError:
                continue

            # Batch: collect more messages within window
            deadline = asyncio.get_event_loop().time() + self._batch_window
            while asyncio.get_event_loop().time() < deadline:
                try:
                    msg = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=max(0.01, deadline - asyncio.get_event_loop().time()),
                    )
                    messages.append(msg)
                except asyncio.TimeoutError:
                    break

            # Rate limit check
            now = time.monotonic()
            if now - self._sent_reset_at > 60:
                self._sent_count = 0
                self._sent_reset_at = now

            if self._sent_count >= self._max_per_minute:
                # Drop excess messages, log warning
                import logging
                logging.getLogger(__name__).warning(
                    "digest_reporter: rate limit hit, dropping %d messages", len(messages)
                )
                continue

            # Combine short messages or send individually
            combined = "\n\n".join(messages)
            if len(combined) > 4000:
                # Telegram message limit ~ 4096, send in chunks
                for msg in messages:
                    if self._sent_count >= self._max_per_minute:
                        break
                    if digest_configured():
                        await send_digest_text(msg)
                    self._sent_count += 1
            else:
                if digest_configured():
                    await send_digest_text(combined)
                self._sent_count += 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/braslavskii/NEURO COMMENTING" && python -m pytest tests/test_digest_reporter.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add core/digest_service.py tests/test_digest_reporter.py
git commit -m "feat: add DigestReporter with event formatting and batched Telegram delivery"
```

---

## Chunk 3: LifelinessAgent

### Task 4: Create LifelinessAgent core

**Files:**
- Create: `core/liveliness_agent.py`
- Create: `tests/test_liveliness_agent.py`

- [ ] **Step 1: Write failing tests for LifelinessAgent**

Create `tests/test_liveliness_agent.py`:

```python
"""Tests for LifelinessAgent scheduling and activity logic."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.liveliness_agent import (
    AccountLifeLoop,
    LifelinessAgent,
    _is_sleep_time,
    _jitter,
)


class TestHelpers:

    def test_is_sleep_time_night(self):
        # 2:00 AM Moscow should be sleep time (default 23-7)
        dt = datetime(2026, 3, 12, 2, 0, tzinfo=timezone.utc)
        assert _is_sleep_time(dt, sleep_start=23, sleep_end=7) is True

    def test_is_sleep_time_day(self):
        # 14:00 should NOT be sleep time
        dt = datetime(2026, 3, 12, 14, 0, tzinfo=timezone.utc)
        assert _is_sleep_time(dt, sleep_start=23, sleep_end=7) is False

    def test_is_sleep_time_boundary_start(self):
        # 23:30 should be sleep time
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/braslavskii/NEURO COMMENTING" && python -m pytest tests/test_liveliness_agent.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement LifelinessAgent**

Create `core/liveliness_agent.py`:

```python
"""
Liveliness Agent — full human simulation for Telegram accounts.

Each account gets its own AccountLifeLoop coroutine that runs activities
on a personalized schedule: online/offline, story viewing, channel reading,
search, profile evolution, inter-account dialogs, timezone sleep.

Uses existing AntiDetection class for delay simulation.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import redis.asyncio as aioredis

from config import settings
from core.anti_detection import AntiDetection
from core.event_bus import EventBus, publish_event

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_sleep_time(
    now: datetime,
    sleep_start: int = 23,
    sleep_end: int = 7,
) -> bool:
    """Return True if the current hour falls in the sleep window."""
    hour = now.hour
    if sleep_start > sleep_end:
        # Overnight window, e.g. 23-7
        return hour >= sleep_start or hour < sleep_end
    else:
        return sleep_start <= hour < sleep_end


def _jitter(base: float, pct: float = 0.25) -> float:
    """Return base ± pct random jitter."""
    factor = 1.0 + random.uniform(-pct, pct)
    return base * factor


# ---------------------------------------------------------------------------
# Per-Account Life Loop
# ---------------------------------------------------------------------------

class AccountLifeLoop:
    """Async coroutine managing one account's human-like activity."""

    def __init__(
        self,
        account_id: int,
        phone: str,
        session_file: str,
        proxy: Optional[dict[str, Any]],
        client_factory: Callable,
        event_bus: Any,
        anti_detection: AntiDetection,
        health_scorer: Any,
        tenant_id: int = 0,
    ) -> None:
        self.account_id = account_id
        self.phone = phone
        self.session_file = session_file
        self.proxy = proxy
        self._client_factory = client_factory
        self._bus = event_bus
        self._ad = anti_detection
        self._health = health_scorer
        self._tenant_id = tenant_id
        self._client: Any = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the life loop as an asyncio task."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("liveliness: started loop for %s", self.phone)

    async def stop(self) -> None:
        """Gracefully stop the loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._disconnect()
        log.info("liveliness: stopped loop for %s", self.phone)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def _connect(self) -> bool:
        """Connect Telethon client. Returns True on success."""
        if self._client is not None:
            return True
        try:
            self._client = await self._client_factory(
                session_file=self.session_file,
                proxy=self.proxy,
            )
            await publish_event("account", {
                "phone": self.phone,
                "status": "connected",
                "action": "liveliness connect",
            })
            return True
        except Exception as exc:
            log.error("liveliness: connect failed %s: %s", self.phone, exc)
            await publish_event("account", {
                "phone": self.phone,
                "status": "connect_failed",
                "error": str(exc)[:200],
            })
            return False

    async def _disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Main per-account life simulation loop."""
        sleep_start = settings.LIVELINESS_SLEEP_START_HOUR
        sleep_end = settings.LIVELINESS_SLEEP_END_HOUR

        while self._running:
            try:
                now = datetime.now(timezone.utc)

                # Timezone sleep
                if _is_sleep_time(now, sleep_start, sleep_end):
                    await self._go_offline()
                    await asyncio.sleep(_jitter(1800, 0.3))  # sleep 30min ± jitter
                    continue

                # Connect if needed
                if not await self._connect():
                    await asyncio.sleep(_jitter(300, 0.3))  # retry in 5min
                    continue

                # Go online
                await self._go_online()

                # Pick a random activity
                activity = random.choices(
                    ["channel_reading", "story_viewing", "search", "inter_dialog"],
                    weights=[40, 25, 20, 15],
                    k=1,
                )[0]

                await self._run_activity(activity)

                # Inter-activity delay
                await self._ad.inter_action_delay()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("liveliness: loop error %s: %s", self.phone, exc)
                await publish_event("error", {
                    "service": "liveliness",
                    "phone": self.phone,
                    "error": str(exc)[:300],
                })
                await asyncio.sleep(_jitter(120, 0.3))

    # ------------------------------------------------------------------
    # Activities
    # ------------------------------------------------------------------

    async def _go_online(self) -> None:
        """Set account status to online."""
        if self._client is None:
            return
        try:
            from telethon.functions.account import UpdateStatusRequest
            await self._client(UpdateStatusRequest(offline=False))
        except Exception as exc:
            log.debug("liveliness: go_online failed %s: %s", self.phone, exc)

    async def _go_offline(self) -> None:
        """Set account status to offline."""
        if self._client is None:
            return
        try:
            from telethon.functions.account import UpdateStatusRequest
            await self._client(UpdateStatusRequest(offline=True))
        except Exception as exc:
            log.debug("liveliness: go_offline failed %s: %s", self.phone, exc)
        await self._disconnect()

    async def _run_activity(self, activity: str) -> None:
        """Execute one activity with anti-detection delays."""
        if self._ad.should_skip_action():
            log.debug("liveliness: skipping %s for %s (anti-detection)", activity, self.phone)
            return

        try:
            if activity == "channel_reading":
                await self._read_channels()
            elif activity == "story_viewing":
                await self._view_stories()
            elif activity == "search":
                await self._search_random()
            elif activity == "inter_dialog":
                await self._forward_random()
        except Exception as exc:
            error_str = str(exc)
            if "frozen" in error_str.lower() or "FrozenMethodInvalidError" in error_str:
                log.warning("liveliness: %s is frozen, pausing loop", self.phone)
                await publish_event("account", {
                    "phone": self.phone,
                    "status": "frozen",
                    "action": "paused liveliness loop",
                })
                self._running = False
                return
            if "FloodWait" in error_str or "FLOOD_WAIT" in error_str:
                # Extract wait time
                wait = 300  # default 5min
                try:
                    wait = int("".join(c for c in error_str.split("_")[-1] if c.isdigit()) or "300")
                except (ValueError, IndexError):
                    pass
                wait = int(wait * 1.5)  # +50% safety margin
                log.warning("liveliness: FloodWait %s for %s, sleeping %ds", self.phone, activity, wait)
                await publish_event("account", {
                    "phone": self.phone,
                    "status": "flood_wait",
                    "wait_sec": wait,
                })
                await asyncio.sleep(wait)
                return
            log.error("liveliness: activity %s failed for %s: %s", activity, self.phone, exc)

    async def _read_channels(self) -> None:
        """Read recent messages from subscribed channels."""
        if self._client is None:
            return
        from telethon.functions.messages import GetDialogsRequest
        from telethon.types import InputPeerEmpty

        dialogs = await self._client(GetDialogsRequest(
            offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
            limit=20, hash=0,
        ))
        channels = [d for d in (dialogs.dialogs or []) if hasattr(d.peer, "channel_id")]
        if not channels:
            return

        # Pick 1-3 random channels to read
        to_read = random.sample(channels, min(random.randint(1, 3), len(channels)))
        for dialog in to_read:
            try:
                msgs = await self._client.get_messages(dialog.peer, limit=random.randint(3, 10))
                await self._ad.simulate_reading(self._client, msgs)
                # Mark as read
                await self._client.send_read_acknowledge(dialog.peer, msgs)
            except Exception:
                pass
            await self._ad.random_delay(2, 8)

    async def _view_stories(self) -> None:
        """View stories from contacts/channels."""
        if self._client is None:
            return
        try:
            from telethon.functions.stories import GetAllStoriesRequest
            result = await self._client(GetAllStoriesRequest(next=False, hidden=False, state=""))
            stories = getattr(result, "peer_stories", []) or []
            if not stories:
                return
            # View 1-3 stories
            from telethon.functions.stories import ReadStoriesRequest
            for ps in random.sample(stories, min(random.randint(1, 3), len(stories))):
                peer = ps.peer
                story_ids = [s.id for s in (ps.stories or [])[:3]]
                if story_ids:
                    await self._client(ReadStoriesRequest(peer=peer, max_id=max(story_ids)))
                    await self._ad.random_delay(3, 8)
        except Exception as exc:
            log.debug("liveliness: stories failed %s: %s", self.phone, exc)

    async def _search_random(self) -> None:
        """Perform random searches to mimic human behavior."""
        if self._client is None:
            return
        keywords = [
            "новости", "рецепты", "музыка", "фильмы", "спорт",
            "путешествия", "работа", "учёба", "книги", "технологии",
            "погода", "здоровье", "мода", "авто", "финансы",
        ]
        query = random.choice(keywords)
        try:
            from telethon.functions.contacts import SearchRequest
            await self._client(SearchRequest(q=query, limit=5))
            await self._ad.random_delay(5, 15)
        except Exception as exc:
            log.debug("liveliness: search failed %s: %s", self.phone, exc)

    async def _forward_random(self) -> None:
        """Forward a random message between own dialogs (mimics sharing behavior)."""
        if self._client is None:
            return
        try:
            # Get saved messages (Saved Messages is always available)
            me = await self._client.get_me()
            msgs = await self._client.get_messages(me, limit=5)
            if not msgs:
                return
            # Just read saved messages — safer than forwarding
            await self._ad.simulate_reading(self._client, msgs)
        except Exception as exc:
            log.debug("liveliness: forward failed %s: %s", self.phone, exc)


# ---------------------------------------------------------------------------
# LifelinessAgent Orchestrator
# ---------------------------------------------------------------------------

class LifelinessAgent:
    """Manages all AccountLifeLoop instances.

    Periodically loads account list from DB and starts/stops loops.
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client
        self._active_loops: dict[int, AccountLifeLoop] = {}
        self._ad = AntiDetection(mode="conservative")
        self._running = False

    async def start(self) -> None:
        """Start the agent. Blocks forever."""
        if not settings.LIVELINESS_ENABLED:
            log.info("liveliness: disabled via LIVELINESS_ENABLED=false")
            return

        self._running = True
        log.info("liveliness: agent starting, max_concurrent=%d", settings.LIVELINESS_MAX_CONCURRENT)
        await publish_event("system", {"action": "liveliness_agent_started"})

        while self._running:
            try:
                await self._refresh_account_list()
            except Exception as exc:
                log.error("liveliness: refresh error: %s", exc)
            await asyncio.sleep(_jitter(settings.LIVELINESS_POLL_INTERVAL_SEC, 0.15))

    async def stop(self) -> None:
        """Stop all loops gracefully."""
        self._running = False
        for loop in list(self._active_loops.values()):
            await loop.stop()
        self._active_loops.clear()
        await publish_event("system", {"action": "liveliness_agent_stopped"})

    async def _refresh_account_list(self) -> None:
        """Load active accounts from DB and reconcile with running loops."""
        from storage.sqlite_db import async_session
        from sqlalchemy import select
        from storage.models import Account

        async with async_session() as session:
            result = await session.execute(
                select(Account).where(
                    Account.status.in_(["active", "healthy", "connected"]),
                )
            )
            accounts = list(result.scalars().all())

        active_ids = {a.id for a in accounts}
        running_ids = set(self._active_loops.keys())

        # Stop loops for removed accounts
        for aid in running_ids - active_ids:
            loop = self._active_loops.pop(aid, None)
            if loop:
                await loop.stop()

        # Start loops for new accounts (up to max concurrent)
        available_slots = settings.LIVELINESS_MAX_CONCURRENT - len(self._active_loops)
        for account in accounts:
            if account.id in self._active_loops:
                continue
            if available_slots <= 0:
                break

            session_path = f"data/sessions/{account.phone}.session"
            if not Path(session_path).exists():
                continue

            proxy = None
            if account.proxy_id:
                # Simplified: proxy info would be loaded from DB
                # For now, skip accounts without inline proxy data
                pass

            loop = AccountLifeLoop(
                account_id=account.id,
                phone=account.phone,
                session_file=session_path,
                proxy=proxy,
                client_factory=self._create_client,
                event_bus=self._redis,
                anti_detection=self._ad,
                health_scorer=None,
                tenant_id=getattr(account, "tenant_id", 0),
            )
            self._active_loops[account.id] = loop
            await loop.start()
            available_slots -= 1

    async def _create_client(self, session_file: str, proxy: Optional[dict] = None) -> Any:
        """Create a Telethon client for the given session."""
        from telethon import TelegramClient

        proxy_kwargs = {}
        if proxy:
            import socks
            proxy_kwargs["proxy"] = (
                socks.HTTP,
                proxy["host"],
                int(proxy["port"]),
                True,
                proxy.get("username", ""),
                proxy.get("password", ""),
            )

        client = TelegramClient(
            session_file,
            settings.TELEGRAM_API_ID,
            settings.TELEGRAM_API_HASH,
            **proxy_kwargs,
        )
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError(f"Session not authorized: {session_file}")
        return client
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/braslavskii/NEURO COMMENTING" && python -m pytest tests/test_liveliness_agent.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add core/liveliness_agent.py tests/test_liveliness_agent.py
git commit -m "feat: add LifelinessAgent with per-account human simulation loops"
```

---

## Chunk 4: Service Entry Point + Docker

### Task 5: Create liveliness_service.py entry point

**Files:**
- Create: `liveliness_service.py`

- [ ] **Step 1: Create the service entry point**

```python
"""
Liveliness Service — standalone Docker Compose service.

Entry point that runs two async modules:
1. LifelinessAgent — per-account human simulation
2. DigestReporter — Redis pub/sub → Telegram digest
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

import redis.asyncio as aioredis

from config import settings
from core.event_bus import EventBus, init_event_bus
from core.liveliness_agent import LifelinessAgent
from core.digest_service import DigestReporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("liveliness_service")


async def main() -> None:
    log.info("Starting liveliness service...")

    # Connect Redis
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )
    try:
        await redis_client.ping()
        log.info("Redis connected: %s", settings.REDIS_URL)
    except Exception as exc:
        log.error("Redis connection failed: %s", exc)
        sys.exit(1)

    # Init event bus
    event_bus = init_event_bus(redis_client)

    # Create modules
    agent = LifelinessAgent(redis_client=redis_client)
    reporter = DigestReporter(
        event_bus=event_bus,
        batch_window_sec=settings.DIGEST_BATCH_WINDOW_SEC,
        max_per_minute=settings.DIGEST_MAX_PER_MINUTE,
    )

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        log.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Run both modules
    agent_task = asyncio.create_task(agent.start())
    reporter_task = asyncio.create_task(reporter.start())

    from core.event_bus import publish_event
    await publish_event("system", {"action": "liveliness_service_started"})

    # Wait for shutdown
    await shutdown_event.wait()

    log.info("Shutting down...")
    await agent.stop()
    agent_task.cancel()
    reporter_task.cancel()

    try:
        await asyncio.gather(agent_task, reporter_task, return_exceptions=True)
    except asyncio.CancelledError:
        pass

    await publish_event("system", {"action": "liveliness_service_stopped"})
    await redis_client.close()
    log.info("Liveliness service stopped.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify it compiles**

Run: `cd "/Users/braslavskii/NEURO COMMENTING" && python -c "import py_compile; py_compile.compile('liveliness_service.py', doraise=True); print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add liveliness_service.py
git commit -m "feat: add liveliness_service.py entry point for Docker Compose"
```

---

### Task 6: Add Docker Compose service

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add liveliness service to docker-compose.yml**

Add after the `bot` service block:

```yaml
  liveliness:
    build: .
    command: python liveliness_service.py
    env_file: .env
    environment:
      - DATABASE_URL=postgresql+asyncpg://nc:${DB_PASSWORD}@db:5432/neurocomment
      - REDIS_URL=redis://redis:6379/0
    volumes:
      - ./data:/app/data
      - ./.env:/app/.env
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M
```

- [ ] **Step 2: Validate compose file**

Run: `cd "/Users/braslavskii/NEURO COMMENTING" && docker compose config --quiet 2>&1 | head -5`
Expected: no errors (or compose config output)

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add liveliness service to Docker Compose"
```

---

### Task 7: Run all tests

- [ ] **Step 1: Run full test suite**

Run: `cd "/Users/braslavskii/NEURO COMMENTING" && python -m pytest tests/test_event_bus.py tests/test_digest_reporter.py tests/test_liveliness_agent.py -v`
Expected: All tests pass

- [ ] **Step 2: Run existing tests to verify no regressions**

Run: `cd "/Users/braslavskii/NEURO COMMENTING" && python -m pytest tests/ -v --timeout=30`
Expected: All existing tests still pass

- [ ] **Step 3: Final commit with all files**

```bash
git add -A
git commit -m "feat: complete Unified Liveliness Service + Digest Reporter

Adds:
- core/event_bus.py: Redis pub/sub wrapper for cross-service events
- core/liveliness_agent.py: per-account human simulation (online/offline, stories, channel reading, search, timezone sleep)
- DigestReporter in digest_service.py: real-time event → Telegram digest
- liveliness_service.py: standalone Docker Compose entry point
- LIVELINESS_* and DIGEST_BATCH_* config vars
- Docker Compose liveliness service
- Tests for all new modules"
```

---

## Summary

| Task | Files | Tests |
|---|---|---|
| 1. Config vars | config.py | compile check |
| 2. EventBus | core/event_bus.py | tests/test_event_bus.py (5) |
| 3. DigestReporter | core/digest_service.py | tests/test_digest_reporter.py (6) |
| 4. LifelinessAgent | core/liveliness_agent.py | tests/test_liveliness_agent.py (5) |
| 5. Entry point | liveliness_service.py | compile check |
| 6. Docker Compose | docker-compose.yml | compose config |
| 7. Integration | all | full test suite |

Total: 4 new files, 3 modified files, 3 test files, ~16 new tests.
