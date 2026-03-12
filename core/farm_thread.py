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

Integration notes (v2):
    - AntiDetection mode is selected from account.days_active at run-start.
      < 3 days → conservative; 3-30 days → moderate; > 30 days → aggressive.
    - CommentOrchestrator (smart_commenter) drives the full commenting pipeline,
      including the never-first-commenter rule and the emoji-first trick.
    - SessionPool (session_pool.py) is the preferred client acquisition path;
      the legacy session_manager fallback is retained for compatibility.
    - AccountLifecycle is called on FloodWaitError and SessionDeadError so that
      account stage transitions are recorded in the DB.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import redis.asyncio as aioredis
from sqlalchemy import select, update
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

# Account age thresholds for AntiDetection mode selection
_AGE_CONSERVATIVE_DAYS = 3
_AGE_MODERATE_DAYS = 30


def _anti_detection_mode_for_days(days_active: int) -> str:
    """Return AntiDetection mode string based on account age in days."""
    if days_active < _AGE_CONSERVATIVE_DAYS:
        return "conservative"
    if days_active < _AGE_MODERATE_DAYS:
        return "moderate"
    return "aggressive"


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
        channel_intel: Any = None,
        session_pool: Any = None,
        account_days_active: int = 0,
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
        self.session_pool = session_pool  # new SessionPool (preferred); optional
        self.route_ai_task = ai_router_func
        self.redis = redis_client
        self._publish_event = publish_event_func
        self.channel_intel = channel_intel

        # AntiDetection — mode set from account age; can be refreshed at run-start
        _mode = _anti_detection_mode_for_days(account_days_active)
        self._anti: Any = None  # initialised lazily in run() after possible DB load
        self._anti_mode: str = _mode
        self._account_days_active: int = account_days_active

        # CommentOrchestrator — created lazily in run()
        self._orchestrator: Any = None

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
            "anti_detection_mode": self._anti_mode,
        }

    async def should_comment(self, channel_id: int) -> tuple[bool, str]:
        """Check channel intelligence rules before commenting."""
        if not self.channel_intel:
            return True, "ok"
        rules = await self.channel_intel.get_rules(self.tenant_id, channel_id)
        if not rules:
            return True, "no_profile"
        if rules.get("ban_risk") == "critical":
            return False, "channel_too_risky"
        interval = rules.get("safe_comment_interval_sec", 0)
        if interval > 0 and self.redis:
            import time as _time
            key = f"ci:last_comment:{self.tenant_id}:{channel_id}:{self.account_id}"
            last = await self.redis.get(key)
            if last:
                elapsed = _time.time() - float(last)
                if elapsed < interval:
                    return False, "too_soon"
        return True, "ok"

    async def _record_comment_success(self, channel_id: int) -> None:
        """Record successful comment timestamp for rate limiting."""
        if self.redis:
            import time as _time
            key = f"ci:last_comment:{self.tenant_id}:{channel_id}:{self.account_id}"
            try:
                await self.redis.setex(key, 3600, str(_time.time()))
            except Exception:
                pass

    async def _record_ban(self, channel_id: int, ban_type: str) -> None:
        """Record ban event via Channel Intelligence."""
        if not self.channel_intel:
            return
        try:
            from core.channel_intelligence import BanPatternLearner
            learner = BanPatternLearner(
                redis_client=self.redis,
                ai_router_func=self.route_ai_task,
            )
            await learner.record_and_analyze(
                tenant_id=self.tenant_id,
                channel_telegram_id=channel_id,
                account_id=self.account_id,
                ban_type=ban_type,
            )
        except Exception as exc:
            log.debug("farm_thread: ban record failed: %s", exc)

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
    # Lazy initialisation helpers
    # ------------------------------------------------------------------

    async def _init_anti_detection(self) -> None:
        """
        Initialise AntiDetection instance.

        If account_days_active was 0 at construction time, attempt to load
        the real value from the DB so the mode is accurate from the first
        iteration.
        """
        from core.anti_detection import AntiDetection

        if self._account_days_active == 0:
            try:
                days = await self._load_account_days_active()
                if days > 0:
                    self._account_days_active = days
                    self._anti_mode = _anti_detection_mode_for_days(days)
            except Exception as exc:
                log.debug(
                    "Thread %s: could not load days_active from DB: %s",
                    self.thread_id, exc,
                )

        self._anti = AntiDetection(mode=self._anti_mode)
        log.info(
            "Thread %s: AntiDetection mode=%s (days_active=%d)",
            self.thread_id,
            self._anti_mode,
            self._account_days_active,
        )

    async def _load_account_days_active(self) -> int:
        """Load days_active from DB for this account."""
        from storage.sqlite_db import async_session as _async_session, apply_session_rls_context

        async with _async_session() as sess:
            async with sess.begin():
                await apply_session_rls_context(sess, tenant_id=self.tenant_id)
                row = await sess.execute(
                    select(Account.days_active).where(Account.id == self.account_id)
                )
                result = row.first()
                return int(result[0] or 0) if result else 0

    def _init_orchestrator(self) -> None:
        """Build a CommentOrchestrator from the current farm_config."""
        from core.smart_commenter import build_orchestrator, TONE_POSITIVE, FREQ_ALL

        tone = getattr(self.farm_config, "comment_tone", None) or TONE_POSITIVE
        language = getattr(self.farm_config, "comment_language", None) or "auto"
        custom_prompt = getattr(self.farm_config, "comment_prompt", None) or ""
        comment_pct = getattr(self.farm_config, "comment_percentage", None) or 100

        # Map comment_percentage to a frequency strategy
        if comment_pct is not None and comment_pct <= 30:
            frequency = "30pct"
        else:
            frequency = FREQ_ALL

        self._orchestrator = build_orchestrator(
            tenant_id=self.tenant_id,
            farm_id=self.farm_id,
            thread_id=self.thread_id,
            tone=tone,
            language=language,
            frequency=frequency,
            custom_prompt=custom_prompt,
        )
        log.debug(
            "Thread %s: CommentOrchestrator built (tone=%s, lang=%s, freq=%s)",
            self.thread_id, tone, language, frequency,
        )

    # ------------------------------------------------------------------
    # Client acquisition helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """
        Return a Telethon client from the session manager.

        Uses SessionPool's public try_get_cached() when available (thread-safe,
        respects the pool's per-account lock), otherwise falls back to the
        legacy phone-keyed session_manager.
        """
        if self.session_pool is not None:
            cached = self.session_pool.try_get_cached(self.account_id)
            if cached is not None:
                return cached

        return self.session_mgr.get_client(self.phone)

    def _release_client(self) -> None:
        """Release the client back to the SessionPool (no-op for legacy mgr)."""
        if self.session_pool is not None:
            self.session_pool.try_release_cached(self.account_id)

    # ------------------------------------------------------------------
    # Main loop — state machine driver
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """
        Main coroutine.  Drives the state machine until a stop signal is
        received or an unrecoverable error occurs.
        """
        # Initialise AntiDetection + CommentOrchestrator before starting
        await self._init_anti_detection()
        self._init_orchestrator()

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
                        channel_id = post.get("channel_id") or 0
                        allowed, reason = await self.should_comment(channel_id)
                        if not allowed:
                            log.info("Thread %s: skip comment reason=%s", self.thread_id, reason)
                            continue

                        # SmartCommenter pipeline: analyze → decide → generate
                        comment_text, decision = await self._smart_comment_pipeline(post)

                        if comment_text:
                            client = self._get_client()
                            try:
                                await self._post_comment_smart(
                                    client=client,
                                    post=post,
                                    comment_text=comment_text,
                                    decision=decision,
                                )
                                await self._record_comment_success(channel_id)
                            finally:
                                self._release_client()

                    except _FloodWaitException as exc:
                        await self._on_flood_wait_error(exc.seconds)
                        break  # exit inner post loop, re-enter outer loop
                    except _SessionDeadException:
                        await self._on_session_dead_error()
                        return  # account is dead — terminate this thread
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
                        # AntiDetection: inter-action delay after failure
                        await self._anti.inter_action_delay()

                    if self._state not in (STATE_COOLDOWN, STATE_QUARANTINE):
                        await self._transition(STATE_MONITORING)

                    # AntiDetection: per-account interval between posts
                    delay = self._anti.per_account_interval(
                        base_min_sec=self.farm_config.delay_before_comment_min,
                        base_max_sec=self.farm_config.delay_before_comment_max,
                        account_id=self.account_id,
                    )
                    await _interruptible_sleep(delay, self._stop_event)

                # Flush stats if dirty
                if self._stats_dirty >= _DB_STATS_FLUSH_EVERY:
                    await self._flush_stats()

                # AntiDetection: randomized monitoring iteration delay (day/night aware)
                base_delay = self._anti.per_account_interval(30, 120, self.account_id)
                night_factor = self._anti.night_activity_multiplier()
                if night_factor < 1.0:
                    base_delay = base_delay / max(night_factor, 0.1)
                await _interruptible_sleep(base_delay, self._stop_event)

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

        client = self._get_client()
        if client is None:
            log.warning(f"Thread {self.thread_id}: no client for {self.phone}, skipping subscribe")
            return

        for ch in channels:
            if self._stop_event.is_set():
                break

            identifier = ch.get("username") or ch.get("telegram_id")
            if not identifier:
                continue

            # AntiDetection: use mode-aware pre-join delay
            await self._anti.pre_join_delay()
            if self._stop_event.is_set():
                break

            try:
                entity = await client.get_entity(identifier)

                # AntiDetection: browse the channel a bit before joining
                await self._anti.simulate_channel_browse(client, entity, posts_to_read=3)

                await client(JoinChannelRequest(entity))
                await self._publish(
                    "channel_joined",
                    f"Joined channel {ch.get('title') or identifier}",
                    severity="info",
                    metadata={"channel": identifier},
                )

                # AntiDetection: inter-action delay after join
                await self._anti.inter_action_delay()

            except UserAlreadyParticipantError:
                pass  # already a member — fine
            except FloodWaitError as exc:
                await self.handle_flood_wait(exc.seconds)
                if self._state == STATE_QUARANTINE:
                    self._release_client()
                    return  # bail out of subscribe entirely
            except ChannelPrivateError:
                await self._publish(
                    "channel_unavailable",
                    f"Channel {identifier} is private or unavailable, skipping",
                    severity="warn",
                    metadata={"channel": identifier},
                )
            except Exception as exc:
                log.warning(f"Thread {self.thread_id}: subscribe error for {identifier}: {exc}")
                # AntiDetection: short delay on error before next channel
                await self._anti.inter_action_delay()

        self._release_client()

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

        client = self._get_client()
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
        finally:
            self._release_client()

    # ------------------------------------------------------------------
    # Post monitoring
    # ------------------------------------------------------------------

    async def monitor_new_posts(self) -> list[dict]:
        """
        Poll each assigned channel for new posts.
        Returns a list of post dicts that should receive a comment.
        """
        client = self._get_client()
        if client is None:
            return []

        new_posts: list[dict] = []

        try:
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

                        # AntiDetection: simulate reading messages before deciding
                        await self._anti.simulate_reading(client, messages[:3])

                        # AntiDetection: 30% chance to send a random reaction (passive engagement)
                        if not self._anti.should_skip_action(probability=0.7):
                            await self._anti.send_random_reaction(client, entity, skip_probability=0.5)

                        post_text = msg.text or msg.raw_text or ""
                        new_posts.append(
                            {
                                "channel": entity,
                                "channel_id": ch.get("id"),
                                "channel_title": ch.get("title") or str(identifier),
                                "channel_username": ch.get("username") or str(identifier),
                                "message_id": msg.id,
                                "text": post_text[:2000],
                                "replies_peer": getattr(msg.replies, "channel_id", None),
                                "replies_count": getattr(msg.replies, "replies", 0) or 0,
                                "posted_at": getattr(msg, "date", None),
                            }
                        )

                    if max_id_seen > last_seen_id:
                        await self.redis.set(last_seen_key, str(max_id_seen), ex=86400 * 7)

                    # AntiDetection: inter-action delay between channels
                    await self._anti.inter_action_delay()

                except Exception as exc:
                    log.debug(f"Thread {self.thread_id}: monitor channel {identifier}: {exc}")
        finally:
            self._release_client()

        return new_posts

    # ------------------------------------------------------------------
    # SmartCommenter pipeline
    # ------------------------------------------------------------------

    async def _smart_comment_pipeline(
        self,
        post: dict,
    ) -> tuple[Optional[str], Any]:
        """
        Run the full CommentOrchestrator pipeline for a single post.

        Returns (comment_text, decision).  comment_text=None means skip.

        The orchestrator enforces:
        - never-first-commenter rule (min_existing_comments=1)
        - hourly / daily rate limits
        - emoji-first trick decision
        - AI post analysis + comment generation
        """
        channel_info = {
            "title": post.get("channel_title", ""),
            "username": post.get("channel_username", ""),
        }

        # Fetch existing comment texts for the never-first rule.
        # We use replies_count from the post dict; if zero, skip (never first).
        existing_comments: list[str] = []
        existing_count = post.get("replies_count", 0)
        if existing_count and existing_count > 0:
            existing_comments = await self._fetch_existing_comments(
                post.get("channel"),
                post.get("message_id"),
            )

        comment_text, decision = await self._orchestrator.process_post(
            post=post,
            existing_comments=existing_comments,
            channel_info=channel_info,
        )
        return comment_text, decision

    async def _fetch_existing_comments(
        self,
        channel_entity: Any,
        message_id: Optional[int],
        limit: int = 10,
    ) -> list[str]:
        """
        Fetch up to `limit` existing comments from the post's discussion thread.
        Returns an empty list on any error — caller degrades gracefully.
        """
        if channel_entity is None or message_id is None:
            return []

        client = self._get_client()
        if client is None:
            return []

        try:
            msgs = await client.get_messages(
                channel_entity,
                reply_to=message_id,
                limit=limit,
            )
            return [
                (getattr(m, "text", None) or getattr(m, "raw_text", None) or "")
                for m in (msgs or [])
                if (getattr(m, "text", None) or getattr(m, "raw_text", None))
            ]
        except Exception as exc:
            log.debug(
                "Thread %s: _fetch_existing_comments failed: %s",
                self.thread_id,
                exc,
            )
            return []
        finally:
            self._release_client()

    # ------------------------------------------------------------------
    # Comment generation (legacy fallback — used if orchestrator not ready)
    # ------------------------------------------------------------------

    async def generate_comment(self, post_text: str) -> Optional[str]:
        """
        Generate a comment via the AI router (task_type='farm_comment').
        Returns the comment text or None on failure.

        This method is retained for backward compatibility.  New code should
        call _smart_comment_pipeline() which uses CommentOrchestrator.
        """
        if self._orchestrator is not None:
            post = {"text": post_text, "replies_count": 1}
            comment_text, _ = await self._smart_comment_pipeline(post)
            return comment_text

        # Legacy direct AI call (fallback when orchestrator unavailable)
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
                return None

            text = (result.parsed or {}).get("text", "")
            return text.strip() if text else None

        except Exception as exc:
            log.warning(f"Thread {self.thread_id}: AI generation failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # Comment posting (SmartCommenter-aware)
    # ------------------------------------------------------------------

    async def _post_comment_smart(
        self,
        client: Any,
        post: dict,
        comment_text: str,
        decision: Any,
    ) -> bool:
        """
        Post comment using the CommentOrchestrator decision.

        If decision.use_emoji_trick is True, delegates to
        orchestrator.apply_emoji_trick() which sends emoji first, waits,
        then edits to the real comment.

        Otherwise posts directly with AntiDetection typing simulation.

        Raises _FloodWaitException, _MuteException, _SessionDeadException.
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

        if client is None:
            return False

        channel = post.get("channel")
        message_id = post.get("message_id")
        channel_title = post.get("channel_title", "")

        # AntiDetection: respect pre-comment delay from strategy decision
        strategy_delay = getattr(decision, "delay_seconds", 0)
        if strategy_delay and strategy_delay > 0:
            await _interruptible_sleep(
                min(strategy_delay, 600.0),  # cap at 10 min
                self._stop_event,
            )
        else:
            # Fallback to mode-aware pre-comment delay
            await self._anti.pre_comment_delay()

        if self._stop_event.is_set():
            return False

        # AntiDetection: simulate typing before posting
        await self._anti.simulate_typing(client, channel)

        try:
            use_emoji_trick = getattr(decision, "use_emoji_trick", False)

            if use_emoji_trick and self._orchestrator is not None:
                # Emoji-first trick path
                success = await self._orchestrator.apply_emoji_trick(
                    client=client,
                    channel_entity=channel,
                    post_message_id=message_id,
                    real_comment=comment_text,
                    stop_event=self._stop_event,
                )
                if success:
                    self._comments_sent += 1
                    self._stats_dirty += 1
                    await self._publish(
                        "comment_sent",
                        f"Comment sent (emoji trick) to '{channel_title}' post #{message_id}",
                        severity="info",
                        metadata={
                            "channel": channel_title,
                            "message_id": message_id,
                            "comment_preview": comment_text[:80],
                            "method": "emoji_trick",
                        },
                    )
                    log.info(
                        "Thread %s: comment sent (emoji trick) to %s #%s",
                        self.thread_id, channel_title, message_id,
                    )
                return success
            else:
                # Direct post path
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
                    "Thread %s: comment sent to %s #%s",
                    self.thread_id, channel_title, message_id,
                )

                # AntiDetection: post-comment inter-action delay
                await self._anti.inter_action_delay()
                return True

        except FloodWaitError as exc:
            raise _FloodWaitException(exc.seconds) from exc

        except (ChatWriteForbiddenError, UserBannedInChannelError):
            await self._record_ban(post.get("channel_id") or 0, "write_forbidden")
            raise _MuteException() from None

        except SlowModeWaitError as exc:
            wait_secs = getattr(exc, "seconds", 60)
            log.info(f"Thread {self.thread_id}: slow mode wait {wait_secs}s for {channel_title}")
            await _interruptible_sleep(wait_secs, self._stop_event)
            return False

        except MsgIdInvalidError:
            log.debug(f"Thread {self.thread_id}: message {message_id} no longer valid")
            return False

        except Exception as exc:
            # Check for session-dead indicators
            cls = type(exc).__name__
            if cls in ("AuthKeyUnregisteredError", "SessionRevokedError", "AuthKeyDuplicatedError"):
                raise _SessionDeadException() from exc

            self._comments_failed += 1
            self._stats_dirty += 1
            log.warning(f"Thread {self.thread_id}: post_comment error: {exc}")
            await self._publish("comment_failed", f"Post error: {exc}", severity="warn")
            return False

    # ------------------------------------------------------------------
    # Comment posting (legacy public method — kept for backward compat)
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

        client = self._get_client()
        if client is None:
            return False

        message_id = post.get("message_id")
        channel_title = post.get("channel_title", "")

        # AntiDetection: typing simulation
        await self._anti.simulate_typing(client, channel)

        # AntiDetection: mode-aware pre-comment delay
        await self._anti.pre_comment_delay()

        if self._stop_event.is_set():
            self._release_client()
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

            # AntiDetection: inter-action delay after posting
            await self._anti.inter_action_delay()
            return True

        except FloodWaitError as exc:
            raise _FloodWaitException(exc.seconds) from exc

        except (ChatWriteForbiddenError, UserBannedInChannelError):
            raise _MuteException() from None

        except SlowModeWaitError as exc:
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

        finally:
            self._release_client()

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

    async def _on_flood_wait_error(self, seconds: int) -> None:
        """Handle FloodWait including AccountLifecycle transition."""
        await self.handle_flood_wait(seconds)
        # Lifecycle: move account to cooldown stage
        try:
            from core.account_lifecycle import AccountLifecycle
            from storage.sqlite_db import async_session as _async_session, apply_session_rls_context
            async with _async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=self.tenant_id)
                    lifecycle = AccountLifecycle(sess)
                    await lifecycle.on_flood_wait(self.account_id, seconds=seconds)
        except Exception as exc:
            log.debug(
                "Thread %s: lifecycle on_flood_wait failed: %s",
                self.thread_id, exc,
            )

    async def _on_session_dead_error(self) -> None:
        """Handle SessionDead: lifecycle → dead, then stop this thread."""
        log.warning(
            "Thread %s: session dead for account_id=%d phone=%s",
            self.thread_id, self.account_id, self.phone,
        )
        await self._transition(STATE_ERROR)
        await self._publish(
            "session_dead",
            f"Session dead for account {self.phone}, stopping thread",
            severity="error",
            metadata={"account_id": self.account_id, "phone": self.phone},
        )
        # Lifecycle: mark account as dead
        try:
            from core.account_lifecycle import AccountLifecycle
            from storage.sqlite_db import async_session as _async_session, apply_session_rls_context
            async with _async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=self.tenant_id)
                    lifecycle = AccountLifecycle(sess)
                    await lifecycle.on_session_dead(self.account_id)
        except Exception as exc:
            log.debug(
                "Thread %s: lifecycle on_session_dead failed: %s",
                self.thread_id, exc,
            )
        # Evict from SessionPool
        if self.session_pool is not None:
            try:
                await self.session_pool.disconnect_client(self.account_id)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Anti-detection helpers (legacy thin wrappers — kept for compat)
    # ------------------------------------------------------------------

    async def _simulate_browsing(self, client, entity, n_messages: int = 3) -> None:
        """Delegate to AntiDetection.simulate_channel_browse()."""
        if self._anti is not None:
            await self._anti.simulate_channel_browse(client, entity, posts_to_read=n_messages)
        else:
            try:
                await client.get_messages(entity, limit=n_messages)
                await asyncio.sleep(random.uniform(0.5, 2.0))
            except Exception:
                pass

    async def _simulate_typing(self, client, entity, seconds: float = 2.0) -> None:
        """Delegate to AntiDetection.simulate_typing()."""
        if self._anti is not None:
            await self._anti.simulate_typing(client, entity)
        else:
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


class _SessionDeadException(Exception):
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
