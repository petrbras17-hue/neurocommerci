"""
Farm Thread — state machine for a single account within a farm.

Each thread manages one Telegram account, monitors assigned channels for new posts,
generates AI comments, and posts them with anti-detection measures.

States:
    idle          — initial state before startup
    subscribing   — joining assigned channels
    monitoring    — polling channels for new posts
    commenting    — generating + posting a comment
    cooldown      — short pause after rate-limit or flood_wait < 300 s
    quarantine    — muted or long flood_wait (>= 300 s); waits for lift
    stopped       — graceful stop completed
    error         — unrecoverable error
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import redis.asyncio as aioredis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import FarmThread as FarmThreadModel, FarmConfig, Account
from utils.helpers import utcnow
from utils.logger import log

# ------------------------------------------------------------------
# State constants
# ------------------------------------------------------------------
STATE_IDLE = "idle"
STATE_SUBSCRIBING = "subscribing"
STATE_MONITORING = "monitoring"
STATE_COMMENTING = "commenting"
STATE_COOLDOWN = "cooldown"
STATE_QUARANTINE = "quarantine"
STATE_STOPPED = "stopped"
STATE_ERROR = "error"

# How often to persist DB stats (every N comments to reduce DB write load)
_DB_STATS_FLUSH_EVERY = 5

# Redis key template: tracks last seen message_id per channel for each thread
_LAST_SEEN_KEY = "farm:thread:{thread_id}:last_seen:{channel_id}"

# Cooldown bucket size: resume check interval in seconds
_COOLDOWN_CHECK_INTERVAL = 30


class FarmThread:
    """
    State machine for a single Telegram account inside a farm.

    Does NOT open its own DB sessions — the orchestrator passes an
    `AsyncSession` during construction only for initial setup.  Per-event
    DB writes (stats flush, status updates) happen through a fresh
    `async_session()` factory to avoid long-lived transactions.
    """

    def __init__(
        self,
        *,
        thread_id: int,
        account_id: int,
        phone: str,
        farm_id: int,
        tenant_id: int,
        farm_config: FarmConfig,
        assigned_channels: list,
        session_manager,
        ai_router_func: Callable,
        redis_client: aioredis.Redis,
        publish_event_func: Callable,
    ) -> None:
        self.thread_id = thread_id
        self.account_id = account_id
        self.phone = phone
        self.farm_id = farm_id
        self.tenant_id = tenant_id
        self.farm_config = farm_config
        self.assigned_channels: list[dict] = [
            c if isinstance(c, dict) else _channel_to_dict(c)
            for c in assigned_channels
        ]

        self.session_mgr = session_manager
        self.route_ai_task = ai_router_func
        self.redis = redis_client
        self._publish_event = publish_event_func

        # State machine
        self._state: str = STATE_IDLE
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # starts in "unpaused" state

        # Cooldown / quarantine tracking
        self._cooldown_until: Optional[datetime] = None
        self._quarantine_until: Optional[datetime] = None

        # Stats (flushed to DB periodically)
        self._comments_sent: int = 0
        self._comments_failed: int = 0
        self._reactions_sent: int = 0
        self._stats_dirty: int = 0  # increments since last flush

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def status(self) -> str:
        return self._state

    @property
    def stats(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "account_id": self.account_id,
            "phone": self.phone,
            "state": self._state,
            "comments_sent": self._comments_sent,
            "comments_failed": self._comments_failed,
            "reactions_sent": self._reactions_sent,
        }

    async def stop(self) -> None:
        """Signal the run loop to exit after the current operation."""
        self._stop_event.set()
        self._pause_event.set()  # unblock if paused so the loop can exit cleanly

    def pause(self) -> None:
        """Pause between iterations (does not interrupt current operation)."""
        self._pause_event.clear()

    def resume(self) -> None:
        """Unpause the thread."""
        self._pause_event.set()

    # ------------------------------------------------------------------
    # Main loop — state machine driver
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """
        Main coroutine.  Drives the state machine until a stop signal is
        received or an unrecoverable error occurs.
        """
        await self._transition(STATE_SUBSCRIBING)

        try:
            # Step 1: join channels
            await self.subscribe_to_channels(self.assigned_channels)

            if self._stop_event.is_set():
                await self._transition(STATE_STOPPED)
                return

            await self._transition(STATE_MONITORING)

            # Step 2: main monitoring + commenting loop
            while not self._stop_event.is_set():
                # Respect pause
                await self._pause_event.wait()
                if self._stop_event.is_set():
                    break

                # Respect cooldown
                if self._state == STATE_COOLDOWN:
                    await self._wait_for_cooldown()
                    if self._stop_event.is_set():
                        break
                    await self._transition(STATE_MONITORING)

                # Respect quarantine
                if self._state == STATE_QUARANTINE:
                    await self._wait_for_quarantine()
                    if self._stop_event.is_set():
                        break
                    await self._transition(STATE_MONITORING)

                # Poll for new posts
                try:
                    posts_to_comment = await self.monitor_new_posts()
                except Exception as exc:
                    log.warning(f"Thread {self.thread_id}: monitor error: {exc}")
                    await self._publish(
                        "error",
                        f"Monitor error: {exc}",
                        severity="warn",
                    )
                    await asyncio.sleep(60)
                    continue

                for post in posts_to_comment:
                    if self._stop_event.is_set():
                        break
                    await self._pause_event.wait()

                    await self._transition(STATE_COMMENTING)
                    try:
                        comment_text = await self.generate_comment(
                            post.get("text", "")
                        )
                        if comment_text:
                            await self.post_comment(
                                channel=post.get("channel"),
                                post=post,
                                comment_text=comment_text,
                            )
                    except _FloodWaitException as exc:
                        await self.handle_flood_wait(exc.seconds)
                        break  # exit inner post loop, re-enter outer loop
                    except _MuteException:
                        await self.handle_mute()
                        break
                    except Exception as exc:
                        self._comments_failed += 1
                        self._stats_dirty += 1
                        log.warning(
                            f"Thread {self.thread_id}: comment error: {exc}"
                        )
                        await self._publish("comment_failed", f"Comment error: {exc}", severity="warn")
                        await asyncio.sleep(
                            random.uniform(
                                self.farm_config.delay_before_comment_min,
                                self.farm_config.delay_before_comment_max,
                            )
                        )

                    if self._state not in (STATE_COOLDOWN, STATE_QUARANTINE):
                        await self._transition(STATE_MONITORING)

                    # Anti-detection: random delay between posts
                    delay = random.uniform(
                        self.farm_config.delay_before_comment_min,
                        self.farm_config.delay_before_comment_max,
                    )
                    await _interruptible_sleep(delay, self._stop_event)

                # Flush stats if dirty
                if self._stats_dirty >= _DB_STATS_FLUSH_EVERY:
                    await self._flush_stats()

                # Anti-detection: random monitoring iteration delay
                iter_delay = random.uniform(30, 120)
                await _interruptible_sleep(iter_delay, self._stop_event)

        except asyncio.CancelledError:
            log.info(f"Thread {self.thread_id}: cancelled")
        except Exception as exc:
            log.error(f"Thread {self.thread_id}: unrecoverable error: {exc}")
            await self._transition(STATE_ERROR)
            await self._publish("error", f"Unrecoverable error: {exc}", severity="error")
            return
        finally:
            # Always flush stats on exit
            try:
                await self._flush_stats()
            except Exception:
                pass

        await self._transition(STATE_STOPPED)
        await self._publish("thread_stopped", f"Thread {self.thread_id} stopped normally", severity="info")

    # ------------------------------------------------------------------
    # Channel subscription
    # ------------------------------------------------------------------

    async def subscribe_to_channels(self, channels: list[dict]) -> None:
        """
        Join each assigned channel.
        Handles FloodWaitError (enter cooldown) and ChannelPrivateError (skip).
        """
        try:
            from telethon.tl.functions.channels import JoinChannelRequest
            from telethon.errors import FloodWaitError, ChannelPrivateError, UserAlreadyParticipantError
        except ImportError:
            log.warning(f"Thread {self.thread_id}: Telethon not available, skipping subscribe")
            return

        client = self.session_mgr.get_client(self.phone)
        if client is None:
            log.warning(f"Thread {self.thread_id}: no client for {self.phone}, skipping subscribe")
            return

        for ch in channels:
            if self._stop_event.is_set():
                break

            identifier = ch.get("username") or ch.get("telegram_id")
            if not identifier:
                continue

            # Anti-detection: random delay before joining
            join_delay = random.uniform(
                self.farm_config.delay_before_join_min,
                self.farm_config.delay_before_join_max,
            )
            await _interruptible_sleep(join_delay, self._stop_event)
            if self._stop_event.is_set():
                break

            try:
                entity = await client.get_entity(identifier)
                await client(JoinChannelRequest(entity))
                await self._publish(
                    "channel_joined",
                    f"Joined channel {ch.get('title') or identifier}",
                    severity="info",
                    metadata={"channel": identifier},
                )
            except UserAlreadyParticipantError:
                pass  # already a member — fine
            except FloodWaitError as exc:
                await self.handle_flood_wait(exc.seconds)
                if self._state == STATE_QUARANTINE:
                    return  # bail out of subscribe entirely
                # cooldown handled, continue with next channel
            except ChannelPrivateError:
                await self._publish(
                    "channel_unavailable",
                    f"Channel {identifier} is private or unavailable, skipping",
                    severity="warn",
                    metadata={"channel": identifier},
                )
            except Exception as exc:
                log.warning(f"Thread {self.thread_id}: subscribe error for {identifier}: {exc}")

    async def subscribe_via_folder(self, invite_link: str) -> None:
        """
        Instant multi-join via a Telegram folder invite link.
        Falls back to per-channel subscribe_to_channels() on error.
        """
        try:
            from telethon.tl.functions.chatlists import CheckChatlistInviteRequest, JoinChatlistInviteRequest
            from telethon.errors import FloodWaitError
        except ImportError:
            log.warning(f"Thread {self.thread_id}: folder API not available in this Telethon version")
            return

        client = self.session_mgr.get_client(self.phone)
        if client is None:
            return

        # Strip the "https://t.me/addlist/" prefix if present
        slug = invite_link.split("/addlist/", 1)[-1].strip("/")
        try:
            result = await client(CheckChatlistInviteRequest(slug=slug))
            await client(JoinChatlistInviteRequest(slug=slug, peers=result.already_peers + result.peers))
            await self._publish(
                "folder_joined",
                f"Joined folder via invite: {invite_link}",
                severity="info",
            )
        except FloodWaitError as exc:
            await self.handle_flood_wait(exc.seconds)
        except Exception as exc:
            log.warning(f"Thread {self.thread_id}: folder join error: {exc}")

    # ------------------------------------------------------------------
    # Post monitoring
    # ------------------------------------------------------------------

    async def monitor_new_posts(self) -> list[dict]:
        """
        Poll each assigned channel for new posts.
        Returns a list of post dicts that should receive a comment.
        """
        client = self.session_mgr.get_client(self.phone)
        if client is None:
            return []

        new_posts: list[dict] = []

        for ch in self.assigned_channels:
            if self._stop_event.is_set():
                break

            identifier = ch.get("username") or ch.get("telegram_id")
            if not identifier:
                continue

            try:
                entity = await client.get_entity(identifier)
                messages = await client.get_messages(entity, limit=5)

                last_seen_key = _LAST_SEEN_KEY.format(
                    thread_id=self.thread_id,
                    channel_id=ch.get("id") or identifier,
                )
                raw = await self.redis.get(last_seen_key)
                last_seen_id: int = int(raw) if raw else 0

                max_id_seen = last_seen_id
                for msg in messages:
                    if msg.id <= last_seen_id:
                        continue
                    if msg.id > max_id_seen:
                        max_id_seen = msg.id

                    # Only posts that have a discussion/reply group
                    if not getattr(msg, "replies", None):
                        continue

                    # Apply comment_percentage filter
                    if not self._should_comment_this_post():
                        continue

                    # Anti-detection: simulate reading a few messages before deciding
                    await self._simulate_browsing(client, entity)

                    post_text = msg.text or msg.raw_text or ""
                    new_posts.append(
                        {
                            "channel": entity,
                            "channel_id": ch.get("id"),
                            "channel_title": ch.get("title") or str(identifier),
                            "message_id": msg.id,
                            "text": post_text[:2000],
                            "replies_peer": getattr(msg.replies, "channel_id", None),
                        }
                    )

                if max_id_seen > last_seen_id:
                    await self.redis.set(last_seen_key, str(max_id_seen), ex=86400 * 7)

            except Exception as exc:
                log.debug(f"Thread {self.thread_id}: monitor channel {identifier}: {exc}")

        return new_posts

    # ------------------------------------------------------------------
    # Comment generation
    # ------------------------------------------------------------------

    async def generate_comment(self, post_text: str) -> Optional[str]:
        """
        Generate a comment via the AI router (task_type='farm_comment').
        Returns the comment text or None on failure.
        """
        prompt = self.farm_config.comment_prompt or ""
        tone = self.farm_config.comment_tone or "neutral"
        system_instruction = (
            f"You are a Telegram channel commenter. "
            f"Write a short, natural comment in tone: {tone}. "
            f"Be concise (max 25 words). Do not mention AI or bots. "
            f"Language: {self.farm_config.comment_language or 'auto'}.\n"
            f"Custom instructions: {prompt}".strip()
        )

        full_prompt = (
            f"Post text:\n{post_text[:1000]}\n\n"
            "Write one short natural comment for this post."
        )

        from storage.sqlite_db import async_session as _async_session, apply_session_rls_context

        try:
            async with _async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=self.tenant_id)
                    result = await self.route_ai_task(
                        sess,
                        task_type="farm_comment",
                        prompt=full_prompt,
                        system_instruction=system_instruction,
                        tenant_id=self.tenant_id,
                        max_output_tokens=150,
                        temperature=0.85,
                        surface="farm",
                    )

            if not result.ok or not result.parsed:
                # Try raw text fallback
                return None

            # The AI router returns parsed JSON; farm_comment should return {"text": "..."}
            text = (result.parsed or {}).get("text", "")
            return text.strip() if text else None

        except Exception as exc:
            log.warning(f"Thread {self.thread_id}: AI generation failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # Comment posting
    # ------------------------------------------------------------------

    async def post_comment(
        self,
        channel,
        post: dict,
        comment_text: str,
    ) -> bool:
        """
        Post comment_text to the discussion group of the channel post.
        Handles Telethon errors: FloodWaitError, ChatWriteForbiddenError, SlowModeWaitError.
        Returns True on success.
        Raises _FloodWaitException or _MuteException for callers to handle.
        """
        try:
            from telethon.errors import (
                FloodWaitError,
                ChatWriteForbiddenError,
                UserBannedInChannelError,
                SlowModeWaitError,
                MsgIdInvalidError,
            )
        except ImportError:
            return False

        client = self.session_mgr.get_client(self.phone)
        if client is None:
            return False

        message_id = post.get("message_id")
        channel_title = post.get("channel_title", "")

        # Anti-detection: simulate typing before posting
        await self._simulate_typing(client, channel, int(random.uniform(1, 4)))

        # Random pre-comment delay
        pre_delay = random.uniform(
            self.farm_config.delay_before_comment_min,
            self.farm_config.delay_before_comment_max,
        )
        await _interruptible_sleep(pre_delay, self._stop_event)
        if self._stop_event.is_set():
            return False

        try:
            await client.send_message(
                channel,
                comment_text,
                comment_to=message_id,
            )

            self._comments_sent += 1
            self._stats_dirty += 1

            await self._publish(
                "comment_sent",
                f"Comment sent to '{channel_title}' post #{message_id}",
                severity="info",
                metadata={
                    "channel": channel_title,
                    "message_id": message_id,
                    "comment_preview": comment_text[:80],
                },
            )
            log.info(
                f"Thread {self.thread_id}: comment sent to {channel_title} #{message_id}"
            )
            return True

        except FloodWaitError as exc:
            raise _FloodWaitException(exc.seconds) from exc

        except (ChatWriteForbiddenError, UserBannedInChannelError):
            raise _MuteException() from None

        except SlowModeWaitError as exc:
            # Slow mode: treat as short cooldown
            wait_secs = getattr(exc, "seconds", 60)
            log.info(f"Thread {self.thread_id}: slow mode wait {wait_secs}s for {channel_title}")
            await _interruptible_sleep(wait_secs, self._stop_event)
            return False

        except MsgIdInvalidError:
            log.debug(f"Thread {self.thread_id}: message {message_id} no longer valid")
            return False

        except Exception as exc:
            self._comments_failed += 1
            self._stats_dirty += 1
            log.warning(f"Thread {self.thread_id}: post_comment error: {exc}")
            await self._publish("comment_failed", f"Post error: {exc}", severity="warn")
            return False

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------

    async def handle_flood_wait(self, seconds: int) -> None:
        """
        Short flood_wait (< 300s) → cooldown.
        Long flood_wait (>= 300s) → quarantine.
        """
        padded = int(seconds * 1.5)  # safety margin
        if seconds < 300:
            self._cooldown_until = utcnow() + timedelta(seconds=padded)
            await self._transition(STATE_COOLDOWN)
            await self._publish(
                "flood_wait",
                f"Flood wait {seconds}s, cooldown for {padded}s",
                severity="warn",
                metadata={"flood_wait_seconds": seconds, "cooldown_seconds": padded},
            )
        else:
            quarantine_end = utcnow() + timedelta(seconds=padded)
            self._quarantine_until = quarantine_end
            await self._transition(STATE_QUARANTINE)
            await self._flush_quarantine_to_db(quarantine_end)
            await self._publish(
                "quarantine_entered",
                f"Long flood wait {seconds}s, entering quarantine until {quarantine_end.isoformat()}",
                severity="error",
                metadata={"flood_wait_seconds": seconds, "quarantine_until": quarantine_end.isoformat()},
            )

    async def handle_mute(self) -> None:
        """Channel write is forbidden — enter quarantine."""
        quarantine_end = utcnow() + timedelta(hours=24)
        self._quarantine_until = quarantine_end
        await self._transition(STATE_QUARANTINE)
        await self._flush_quarantine_to_db(quarantine_end)
        await self._publish(
            "mute_detected",
            f"Write forbidden/mute detected, quarantine until {quarantine_end.isoformat()}",
            severity="error",
            metadata={"quarantine_until": quarantine_end.isoformat()},
        )

    # ------------------------------------------------------------------
    # Anti-detection helpers
    # ------------------------------------------------------------------

    async def _simulate_browsing(self, client, entity, n_messages: int = 3) -> None:
        """Read a few messages to simulate a real user browsing."""
        try:
            await client.get_messages(entity, limit=n_messages)
            await asyncio.sleep(random.uniform(0.5, 2.0))
        except Exception:
            pass

    async def _simulate_typing(self, client, entity, seconds: float = 2.0) -> None:
        """Send typing action for a short random duration."""
        try:
            async with client.action(entity, "typing"):
                await asyncio.sleep(min(seconds, 5.0))
        except Exception:
            pass

    def _should_comment_this_post(self) -> bool:
        """Apply comment_percentage filter to decide whether to comment."""
        pct = self.farm_config.comment_percentage
        if pct is None or pct >= 100:
            return True
        return random.randint(1, 100) <= pct

    # ------------------------------------------------------------------
    # Internal state helpers
    # ------------------------------------------------------------------

    async def _transition(self, new_state: str) -> None:
        old_state = self._state
        self._state = new_state
        if old_state != new_state:
            log.debug(f"Thread {self.thread_id}: {old_state} -> {new_state}")
            await self._flush_status_to_db(new_state)

    async def _tenant_session(self):
        """Create a short-lived DB session with RLS tenant context applied."""
        from storage.sqlite_db import async_session as _async_session, apply_session_rls_context

        sess = _async_session()
        return sess, apply_session_rls_context

    async def _flush_status_to_db(self, status: str) -> None:
        from storage.sqlite_db import async_session as _async_session, apply_session_rls_context

        try:
            async with _async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=self.tenant_id)
                    await sess.execute(
                        update(FarmThreadModel)
                        .where(FarmThreadModel.id == self.thread_id)
                        .values(status=status, updated_at=utcnow())
                    )
        except Exception as exc:
            log.warning(f"Thread {self.thread_id}: DB status flush failed: {exc}")

    async def _flush_stats(self) -> None:
        from storage.sqlite_db import async_session as _async_session, apply_session_rls_context

        self._stats_dirty = 0
        try:
            async with _async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=self.tenant_id)
                    updates: dict[str, Any] = {
                        "stats_comments_sent": self._comments_sent,
                        "stats_comments_failed": self._comments_failed,
                        "stats_reactions_sent": self._reactions_sent,
                        "updated_at": utcnow(),
                    }
                    if self._comments_sent > 0:
                        updates["stats_last_comment_at"] = utcnow()
                    await sess.execute(
                        update(FarmThreadModel)
                        .where(FarmThreadModel.id == self.thread_id)
                        .values(**updates)
                    )
        except Exception as exc:
            log.warning(f"Thread {self.thread_id}: DB stats flush failed: {exc}")

    async def _flush_quarantine_to_db(self, until: datetime) -> None:
        from storage.sqlite_db import async_session as _async_session, apply_session_rls_context

        try:
            async with _async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=self.tenant_id)
                    await sess.execute(
                        update(FarmThreadModel)
                        .where(FarmThreadModel.id == self.thread_id)
                        .values(
                            quarantine_until=until,
                            status=STATE_QUARANTINE,
                            updated_at=utcnow(),
                        )
                    )
        except Exception as exc:
            log.warning(f"Thread {self.thread_id}: quarantine DB flush failed: {exc}")

    async def _wait_for_cooldown(self) -> None:
        """Sleep until cooldown_until, checking stop_event regularly."""
        while True:
            if self._stop_event.is_set():
                return
            if self._cooldown_until is None:
                return
            now = utcnow()
            if now >= self._cooldown_until:
                self._cooldown_until = None
                return
            remaining = (self._cooldown_until - now).total_seconds()
            sleep_for = min(remaining, _COOLDOWN_CHECK_INTERVAL)
            await _interruptible_sleep(sleep_for, self._stop_event)

    async def _wait_for_quarantine(self) -> None:
        """Sleep until quarantine_until, checking stop_event regularly."""
        while True:
            if self._stop_event.is_set():
                return
            if self._quarantine_until is None:
                return
            now = utcnow()
            if now >= self._quarantine_until:
                self._quarantine_until = None
                await self._publish(
                    "quarantine_lifted",
                    f"Thread {self.thread_id} quarantine lifted",
                    severity="info",
                )
                return
            remaining = (self._quarantine_until - now).total_seconds()
            sleep_for = min(remaining, 60.0)
            await _interruptible_sleep(sleep_for, self._stop_event)

    async def _publish(
        self,
        event_type: str,
        message: str,
        severity: str = "info",
        metadata: Optional[dict] = None,
    ) -> None:
        """Delegate to the orchestrator's publish_event coroutine."""
        try:
            await self._publish_event(
                farm_id=self.farm_id,
                thread_id=self.thread_id,
                event_type=event_type,
                message=message,
                severity=severity,
                metadata=metadata,
            )
        except Exception as exc:
            log.debug(f"Thread {self.thread_id}: publish_event error: {exc}")


# ------------------------------------------------------------------
# Internal exception sentinels (not public API)
# ------------------------------------------------------------------


class _FloodWaitException(Exception):
    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        super().__init__(f"FloodWait {seconds}s")


class _MuteException(Exception):
    pass


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


async def _interruptible_sleep(seconds: float, stop_event: asyncio.Event) -> None:
    """Sleep for `seconds` but wake up immediately if stop_event is set."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


def _channel_to_dict(channel) -> dict:
    """Convert an ORM ChannelEntry to a plain dict (safe for JSON storage)."""
    return {
        "id": getattr(channel, "id", None),
        "telegram_id": getattr(channel, "telegram_id", None),
        "username": getattr(channel, "username", None),
        "title": getattr(channel, "title", None),
        "has_comments": getattr(channel, "has_comments", True),
    }
