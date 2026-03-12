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

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event publishing helper (safe even if event_bus not yet created)
# ---------------------------------------------------------------------------

async def _publish(category: str, data: dict) -> None:
    try:
        from core.event_bus import publish_event
        await publish_event(category, data)
    except Exception:
        pass


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
    """Return base +/- pct random jitter."""
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
        route_ai_task: Optional[Callable] = None,
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
        self._route_ai_task = route_ai_task
        self._recent_actions: list[str] = []
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
            await _publish("account", {
                "phone": self.phone,
                "status": "connected",
                "action": "liveliness connect",
            })
            return True
        except Exception as exc:
            log.error("liveliness: connect failed %s: %s", self.phone, exc)
            await _publish("account", {
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
                    await asyncio.sleep(_jitter(1800, 0.3))  # sleep 30min +/- jitter
                    continue

                # Connect if needed
                if not await self._connect():
                    await asyncio.sleep(_jitter(300, 0.3))  # retry in 5min
                    continue

                # Go online
                await self._go_online()

                # Pick activity via AI (falls back to weighted random if AI unavailable)
                ai_action, _params = await self._pick_next_action_ai()
                # Map AI action names to internal activity names
                _action_map = {
                    "read_channel": "channel_reading",
                    "view_stories": "story_viewing",
                    "search": "search",
                    "idle": "inter_dialog",
                }
                activity = _action_map.get(ai_action, "channel_reading")

                await self._run_activity(activity)

                # Track recent actions (keep last 10)
                self._recent_actions.append(ai_action)
                if len(self._recent_actions) > 10:
                    self._recent_actions = self._recent_actions[-10:]

                # Inter-activity delay
                await self._ad.inter_action_delay()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("liveliness: loop error %s: %s", self.phone, exc)
                await _publish("error", {
                    "service": "liveliness",
                    "phone": self.phone,
                    "error": str(exc)[:300],
                })
                await asyncio.sleep(_jitter(120, 0.3))

    # ------------------------------------------------------------------
    # Action selection
    # ------------------------------------------------------------------

    async def _pick_next_action_ai(self) -> tuple[str, dict]:
        """Use AI to pick the next action. Falls back to weighted random."""
        if not self._route_ai_task:
            return self._pick_next_action_weighted()
        try:
            now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
            recent = self._recent_actions[-3:]
            prompt = (
                f"You are simulating a real Telegram user. "
                f"Time: {now_str}. "
                f"Recent actions: {recent}. "
                f"Mood: {random.choice(['active', 'lazy', 'curious', 'bored'])}. "
                f"Pick ONE action from: read_channel, view_stories, search, idle. "
                f'Return JSON: {{"action": "...", "duration_min": N}}'
            )
            result = await self._route_ai_task(
                task_type="life_next_action",
                tenant_id=self._tenant_id,
                payload={"prompt": prompt},
            )
            if result and getattr(result, "ok", False) and getattr(result, "parsed", None):
                action = result.parsed.get("action", "read_channel")
                valid_actions = {"read_channel", "view_stories", "search", "idle"}
                if action not in valid_actions:
                    action = "read_channel"
                duration = min(max(int(result.parsed.get("duration_min", 3)), 1), 15)
                return action, {"duration_min": duration}
        except Exception as exc:
            log.debug("life_loop: AI action pick failed, using weighted: %s", exc)
        return self._pick_next_action_weighted()

    def _pick_next_action_weighted(self) -> tuple[str, dict]:
        """Original weighted random selection as fallback."""
        roll = random.random()
        if roll < 0.40:
            return "read_channel", {}
        elif roll < 0.65:
            return "view_stories", {}
        elif roll < 0.85:
            return "search", {}
        else:
            return "idle", {}

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
                await _publish("account", {
                    "phone": self.phone,
                    "status": "frozen",
                    "action": "paused liveliness loop",
                })
                self._running = False
                return
            if "FloodWait" in error_str or "FLOOD_WAIT" in error_str:
                wait = 300
                try:
                    wait = int("".join(c for c in error_str.split("_")[-1] if c.isdigit()) or "300")
                except (ValueError, IndexError):
                    pass
                wait = int(wait * 1.5)
                log.warning("liveliness: FloodWait %s for %s, sleeping %ds", self.phone, activity, wait)
                await _publish("account", {
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

        to_read = random.sample(channels, min(random.randint(1, 3), len(channels)))
        for dialog in to_read:
            try:
                msgs = await self._client.get_messages(dialog.peer, limit=random.randint(3, 10))
                await self._ad.simulate_reading(self._client, msgs)
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
        """Read saved messages (mimics sharing/browsing behavior)."""
        if self._client is None:
            return
        try:
            me = await self._client.get_me()
            msgs = await self._client.get_messages(me, limit=5)
            if not msgs:
                return
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
        await _publish("system", {"action": "liveliness_agent_started"})

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
        await _publish("system", {"action": "liveliness_agent_stopped"})

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
                pass  # Proxy loading from DB would go here

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
