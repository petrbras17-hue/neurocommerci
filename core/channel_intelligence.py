"""
Channel Intelligence Engine.

Three classes that together build a learning channel profile system:
  - ChannelProfiler  — fetches channel metadata via Telethon, extracts rules
                       via AI, and caches the result in Redis.
  - JoinRequestTracker — creates/updates ChannelJoinRequest rows and keeps a
                         fast Redis set for pending-join lookups.
  - BanPatternLearner  — records ban events, updates the channel profile stats,
                         and applies AI-derived rule adjustments.

All DB operations run inside explicit transaction blocks so that
apply_session_rls_context can use SET LOCAL semantics safely.
All Redis operations are fire-and-forget (wrapped in try/except).
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from storage.models import ChannelBanEvent, ChannelEntry, ChannelJoinRequest, ChannelMapEntry, ChannelProfile
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis key patterns
# ---------------------------------------------------------------------------

_KEY_CHANNEL_PROFILE = "ci:{tenant_id}:{telegram_id}"
_KEY_JOINS = "ci:joins:{tenant_id}:{account_id}"
_KEY_LAST_COMMENT = "ci:last_comment:{tenant_id}:{channel_id}:{account_id}"

# ---------------------------------------------------------------------------
# AI prompts
# ---------------------------------------------------------------------------

_RULES_EXTRACTION_PROMPT = (
    "You are a Telegram channel policy analyst. "
    "Analyze the following pinned message text from a Telegram channel "
    "and extract the posting rules. "
    "Return ONLY a valid JSON object with these keys:\n"
    "  max_messages_per_hour (integer or null),\n"
    "  topics_allowed (list of strings),\n"
    "  topics_banned (list of strings),\n"
    "  links_policy (string: 'allowed' | 'banned' | 'restricted' | 'unknown'),\n"
    "  language (string, e.g. 'ru', 'en', or null),\n"
    "  custom_rules (list of strings — any other notable rules).\n"
    "If a field cannot be determined, use null or an empty list. "
    "Do not include any explanation outside the JSON object."
)

_BAN_ANALYSIS_PROMPT = (
    "You are a Telegram anti-ban analyst. "
    "An account was banned or restricted in a Telegram channel. "
    "Analyze the provided context and return ONLY a valid JSON object with these keys:\n"
    "  cause (string — most likely reason for the ban),\n"
    "  new_rules (list of strings — posting rules inferred from this ban event),\n"
    "  safe_interval_sec (integer — recommended minimum seconds between comments),\n"
    "  risk_adjustment (float between -0.5 and 0.5 — delta to apply to the channel's "
    "success_rate; negative means the channel became riskier).\n"
    "Do not include any explanation outside the JSON object."
)

# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


def compute_ban_risk(
    success_rate: float,
    total_bans: int,  # noqa: ARG001 — reserved for future non-linear logic
    total_comments: int,
) -> str:
    """Classify channel ban risk level from statistics.

    Returns one of: "low", "medium", "high", "critical".
    """
    if total_comments == 0:
        return "low"
    if success_rate > 0.90:
        return "low"
    if success_rate > 0.70:
        return "medium"
    threshold = settings.CI_BAN_RISK_CRITICAL_THRESHOLD / 100.0
    if success_rate >= threshold:
        return "high"
    return "critical"


# ---------------------------------------------------------------------------
# ChannelProfiler
# ---------------------------------------------------------------------------


class ChannelProfiler:
    """Fetches and caches AI-enriched channel profiles.

    Parameters
    ----------
    redis_client:
        An async Redis client (e.g. redis.asyncio.Redis).  May be None in
        tests — all Redis operations are silently skipped when it is None.
    ai_router_func:
        Async callable with the same signature as ``core.ai_router.route_ai_task``.
        May be None; in that case AI extraction is skipped and the raw pinned
        text is stored without parsed rules.
    """

    def __init__(self, redis_client: Any, ai_router_func: Any) -> None:
        self.redis = redis_client
        self._route_ai = ai_router_func

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def profile_channel(
        self,
        client: Any,
        channel_entity: Any,
        tenant_id: int,
        channel_entry_id: int | None = None,
    ) -> ChannelProfile:
        """Build or refresh a ChannelProfile for the given Telegram channel.

        Uses Telethon to fetch live channel metadata, extracts rules from the
        pinned message via AI, and upserts the result in the database.  The
        finished profile is also cached in Redis.

        Parameters
        ----------
        client:
            An authenticated Telethon TelegramClient instance.
        channel_entity:
            A Telethon channel/chat entity (InputChannel, Channel, etc.).
        tenant_id:
            The current tenant's database ID.
        channel_entry_id:
            Optional FK to the channel_entries table row.
        """
        # Lazy Telethon imports so the module can be imported without Telethon
        # installed (e.g. in unit-test environments).
        try:
            from telethon.tl.functions.channels import GetFullChannelRequest
            from telethon.tl.types import InputMessagesFilterPinned
        except ImportError:
            GetFullChannelRequest = None  # type: ignore[assignment]
            InputMessagesFilterPinned = None  # type: ignore[assignment]

        telegram_id: int = getattr(channel_entity, "id", 0)
        username: str | None = getattr(channel_entity, "username", None)
        title: str | None = getattr(channel_entity, "title", None)
        slow_mode_seconds = 0
        linked_chat_id: int | None = None
        no_links = False
        no_forwards = False
        channel_type = "channel"
        is_private = False
        pinned_rules_text: str | None = None
        ai_extracted_rules: dict | None = None

        # ---- Telethon full channel request ----
        if client is not None and GetFullChannelRequest is not None:
            try:
                full = await client(GetFullChannelRequest(channel=channel_entity))
                chat = full.full_chat
                slow_mode_seconds = getattr(chat, "slowmode_seconds", 0) or 0
                linked_chat_id = getattr(chat, "linked_chat_id", None)
                default_banned = getattr(full.chats[0], "default_banned_rights", None)
                if default_banned is not None:
                    no_links = bool(getattr(default_banned, "embed_links", False))
                    no_forwards = bool(getattr(default_banned, "forward_messages", False))
                megagroup = getattr(full.chats[0], "megagroup", False)
                broadcast = getattr(full.chats[0], "broadcast", False)
                if broadcast:
                    channel_type = "channel"
                elif megagroup:
                    channel_type = "megagroup"
                else:
                    channel_type = "supergroup"
                is_private = not bool(getattr(full.chats[0], "username", None))
            except Exception as exc:
                log.warning(
                    "channel_profiler: GetFullChannelRequest failed telegram_id=%s: %s",
                    telegram_id,
                    exc,
                )

        # ---- Pinned message ----
        if client is not None and InputMessagesFilterPinned is not None:
            try:
                pinned_msgs = await client.get_messages(
                    channel_entity,
                    filter=InputMessagesFilterPinned,
                    limit=1,
                )
                if pinned_msgs:
                    pinned_rules_text = getattr(pinned_msgs[0], "text", None)
            except Exception as exc:
                log.warning(
                    "channel_profiler: pinned message fetch failed telegram_id=%s: %s",
                    telegram_id,
                    exc,
                )

        # ---- AI rules extraction ----
        if pinned_rules_text and self._route_ai is not None:
            async with async_session() as session:
                async with session.begin():
                    await apply_session_rls_context(session, tenant_id=tenant_id)
                    try:
                        result = await self._route_ai(
                            session,
                            task_type="channel_rules_extraction",
                            prompt=pinned_rules_text,
                            system_instruction=_RULES_EXTRACTION_PROMPT,
                            tenant_id=tenant_id,
                        )
                        if isinstance(result, dict) and result.get("parsed"):
                            ai_extracted_rules = result["parsed"]
                    except Exception as exc:
                        log.warning(
                            "channel_profiler: AI rules extraction failed telegram_id=%s: %s",
                            telegram_id,
                            exc,
                        )

        # ---- Upsert into DB ----
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(ChannelProfile).where(
                        ChannelProfile.tenant_id == tenant_id,
                        ChannelProfile.telegram_id == telegram_id,
                    )
                )
                profile: ChannelProfile | None = result.scalar_one_or_none()

                now = utcnow()
                if profile is None:
                    profile = ChannelProfile(
                        tenant_id=tenant_id,
                        channel_entry_id=channel_entry_id,
                        telegram_id=telegram_id,
                        username=username,
                        title=title,
                        channel_type=channel_type,
                        is_private=is_private,
                        slow_mode_seconds=slow_mode_seconds,
                        no_links=no_links,
                        no_forwards=no_forwards,
                        linked_chat_id=linked_chat_id,
                        pinned_rules_text=pinned_rules_text,
                        ai_extracted_rules=ai_extracted_rules,
                        last_profiled_at=now,
                    )
                    session.add(profile)
                else:
                    if channel_entry_id is not None:
                        profile.channel_entry_id = channel_entry_id
                    profile.username = username
                    profile.title = title
                    profile.channel_type = channel_type
                    profile.is_private = is_private
                    profile.slow_mode_seconds = slow_mode_seconds
                    profile.no_links = no_links
                    profile.no_forwards = no_forwards
                    profile.linked_chat_id = linked_chat_id
                    profile.pinned_rules_text = pinned_rules_text
                    if ai_extracted_rules is not None:
                        profile.ai_extracted_rules = ai_extracted_rules
                    profile.last_profiled_at = now

                await session.flush()
                await session.refresh(profile)

        # ---- Cache in Redis ----
        self._cache_profile(tenant_id, telegram_id, profile)
        return profile

    async def get_rules(self, tenant_id: int, telegram_id: int) -> dict:
        """Return channel rules dict.  Checks Redis first, falls back to DB.

        Returns a lightweight dict suitable for runtime decisions (not the
        full ORM object).
        """
        # 1. Redis
        key = _KEY_CHANNEL_PROFILE.format(
            tenant_id=tenant_id, telegram_id=telegram_id
        )
        if self.redis is not None:
            try:
                cached = await self.redis.get(key)
                if cached:
                    return json.loads(cached)
            except Exception as exc:
                log.debug("channel_profiler: redis get failed: %s", exc)

        # 2. DB fallback
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(ChannelProfile).where(
                        ChannelProfile.tenant_id == tenant_id,
                        ChannelProfile.telegram_id == telegram_id,
                    )
                )
                profile: ChannelProfile | None = result.scalar_one_or_none()

        if profile is None:
            return {}

        cache_dict = self._to_cache_dict(profile)
        # Re-cache on DB hit
        self._set_redis_cache(key, cache_dict)
        return cache_dict

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_cache_dict(profile: ChannelProfile) -> dict:
        """Convert an ORM ChannelProfile to a plain dict for Redis storage."""
        return {
            "id": profile.id,
            "telegram_id": profile.telegram_id,
            "channel_type": profile.channel_type,
            "slow_mode_seconds": profile.slow_mode_seconds,
            "no_links": profile.no_links,
            "no_forwards": profile.no_forwards,
            "ban_risk": profile.ban_risk,
            "success_rate": profile.success_rate,
            "total_comments": profile.total_comments,
            "total_bans": profile.total_bans,
            "safe_comment_interval_sec": profile.safe_comment_interval_sec,
            "ai_extracted_rules": profile.ai_extracted_rules or {},
            "learned_rules": profile.learned_rules or {},
        }

    def _cache_profile(
        self, tenant_id: int, telegram_id: int, profile: ChannelProfile
    ) -> None:
        """Fire-and-forget Redis cache write (sync wrapper; uses asyncio task)."""
        key = _KEY_CHANNEL_PROFILE.format(
            tenant_id=tenant_id, telegram_id=telegram_id
        )
        cache_dict = self._to_cache_dict(profile)
        self._set_redis_cache(key, cache_dict)

    def _set_redis_cache(self, key: str, data: dict) -> None:
        """Schedule an async Redis SET without awaiting — fire-and-forget."""
        if self.redis is None:
            return
        import asyncio

        async def _write() -> None:
            try:
                await self.redis.set(
                    key,
                    json.dumps(data),
                    ex=settings.CI_REDIS_CACHE_TTL_SEC,
                )
            except Exception as exc:
                log.debug("channel_profiler: redis set failed key=%s: %s", key, exc)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_write())
        except RuntimeError:
            pass  # No event loop — skip caching silently


# ---------------------------------------------------------------------------
# JoinRequestTracker
# ---------------------------------------------------------------------------


class JoinRequestTracker:
    """Creates and resolves ChannelJoinRequest rows.

    Also maintains a Redis set at ``ci:joins:{tenant_id}:{account_id}``
    that holds the set of pending telegram_ids for fast runtime checks.

    Parameters
    ----------
    redis_client:
        Async Redis client.  May be None — Redis operations are skipped.
    """

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_request(
        self,
        tenant_id: int,
        account_id: int,
        telegram_id: int,
        channel_profile_id: int | None = None,
    ) -> ChannelJoinRequest:
        """Create a new pending join request row."""
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                req = ChannelJoinRequest(
                    tenant_id=tenant_id,
                    account_id=account_id,
                    telegram_id=telegram_id,
                    channel_profile_id=channel_profile_id,
                    status="pending",
                    requested_at=utcnow(),
                )
                session.add(req)
                await session.flush()
                await session.refresh(req)

        self._redis_sadd_pending(tenant_id, account_id, telegram_id)
        log.info(
            "join_tracker: created request account_id=%s telegram_id=%s",
            account_id,
            telegram_id,
        )
        return req

    async def mark_accepted(
        self, tenant_id: int, account_id: int, telegram_id: int
    ) -> None:
        """Mark a pending join request as accepted."""
        await self._update_status(tenant_id, account_id, telegram_id, "accepted")

    async def mark_rejected(
        self, tenant_id: int, account_id: int, telegram_id: int
    ) -> None:
        """Mark a pending join request as rejected."""
        await self._update_status(tenant_id, account_id, telegram_id, "rejected")

    async def get_pending(
        self, tenant_id: int, account_id: int
    ) -> list[ChannelJoinRequest]:
        """Return all pending join requests for an account."""
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(ChannelJoinRequest).where(
                        ChannelJoinRequest.tenant_id == tenant_id,
                        ChannelJoinRequest.account_id == account_id,
                        ChannelJoinRequest.status == "pending",
                    ).limit(5000)
                )
                return list(result.scalars().all())

    async def expire_old_requests(
        self, tenant_id: int, max_age_days: int = 7
    ) -> int:
        """Set status='expired' on pending requests older than max_age_days.

        Returns the number of rows updated.
        """
        cutoff = utcnow() - timedelta(days=max_age_days)
        updated = 0
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(ChannelJoinRequest).where(
                        ChannelJoinRequest.tenant_id == tenant_id,
                        ChannelJoinRequest.status == "pending",
                        ChannelJoinRequest.requested_at < cutoff,
                    ).limit(10000)
                )
                rows = list(result.scalars().all())
                for row in rows:
                    row.status = "expired"
                    row.resolved_at = utcnow()
                    updated += 1
        log.info(
            "join_tracker: expired %s old requests tenant_id=%s", updated, tenant_id
        )
        return updated

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _update_status(
        self,
        tenant_id: int,
        account_id: int,
        telegram_id: int,
        new_status: str,
    ) -> None:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(ChannelJoinRequest).where(
                        ChannelJoinRequest.tenant_id == tenant_id,
                        ChannelJoinRequest.account_id == account_id,
                        ChannelJoinRequest.telegram_id == telegram_id,
                        ChannelJoinRequest.status == "pending",
                    )
                )
                req: ChannelJoinRequest | None = result.scalar_one_or_none()
                if req is not None:
                    req.status = new_status
                    req.resolved_at = utcnow()

        # Remove from Redis pending set on any resolution
        self._redis_srem_pending(tenant_id, account_id, telegram_id)
        log.info(
            "join_tracker: %s account_id=%s telegram_id=%s",
            new_status,
            account_id,
            telegram_id,
        )

    def _redis_sadd_pending(
        self, tenant_id: int, account_id: int, telegram_id: int
    ) -> None:
        if self.redis is None:
            return
        import asyncio

        key = _KEY_JOINS.format(tenant_id=tenant_id, account_id=account_id)

        async def _write() -> None:
            try:
                await self.redis.sadd(key, str(telegram_id))
            except Exception as exc:
                log.debug("join_tracker: redis sadd failed: %s", exc)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_write())
        except RuntimeError:
            pass

    def _redis_srem_pending(
        self, tenant_id: int, account_id: int, telegram_id: int
    ) -> None:
        if self.redis is None:
            return
        import asyncio

        key = _KEY_JOINS.format(tenant_id=tenant_id, account_id=account_id)

        async def _write() -> None:
            try:
                await self.redis.srem(key, str(telegram_id))
            except Exception as exc:
                log.debug("join_tracker: redis srem failed: %s", exc)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_write())
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# BanPatternLearner
# ---------------------------------------------------------------------------


class BanPatternLearner:
    """Records ban events and updates channel profile statistics and rules.

    On each ban event this class:
    1. Saves a ChannelBanEvent row.
    2. Updates the ChannelProfile's total_bans, success_rate, and ban_risk.
    3. Runs AI analysis to infer new rules or a safer comment interval.
    4. Merges AI-derived rules into the profile's learned_rules field.
    5. Invalidates the Redis cache entry for the affected channel.

    Parameters
    ----------
    redis_client:
        Async Redis client.  May be None.
    ai_router_func:
        Async callable matching ``route_ai_task`` signature.  May be None;
        AI analysis is skipped when it is None.
    """

    def __init__(self, redis_client: Any, ai_router_func: Any) -> None:
        self.redis = redis_client
        self._route_ai = ai_router_func

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def record_and_analyze(
        self,
        tenant_id: int,
        channel_telegram_id: int,
        account_id: int,
        ban_type: str,
        last_actions: dict | None = None,
    ) -> dict | None:
        """Record a ban event and trigger AI analysis.

        Returns the AI analysis dict, or None if AI was unavailable or failed.
        """
        # ---- Fetch the current profile ----
        profile = await self._get_profile(tenant_id, channel_telegram_id)
        if profile is None:
            log.warning(
                "ban_learner: no profile for telegram_id=%s tenant_id=%s — "
                "skipping analysis",
                channel_telegram_id,
                tenant_id,
            )
            return None

        # ---- Persist ban event ----
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                event_row = ChannelBanEvent(
                    tenant_id=tenant_id,
                    channel_profile_id=profile.id,
                    account_id=account_id,
                    ban_type=ban_type,
                    last_action_before_ban=last_actions,
                )
                session.add(event_row)
                await session.flush()

        # ---- Update profile stats ----
        await self._update_profile_stats(tenant_id, profile)

        # ---- AI analysis ----
        analysis: dict | None = None
        if self._route_ai is not None:
            analysis = await self._run_ai_analysis(
                tenant_id=tenant_id,
                channel_telegram_id=channel_telegram_id,
                ban_type=ban_type,
                last_actions=last_actions,
                profile=profile,
            )

        # ---- Apply AI results to profile ----
        if analysis:
            async with async_session() as session:
                async with session.begin():
                    await self._apply_ai_analysis(
                        tenant_id, channel_telegram_id, analysis, session
                    )

        # ---- Invalidate Redis cache ----
        self._invalidate_cache(tenant_id, channel_telegram_id)
        return analysis

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_profile(
        self, tenant_id: int, telegram_id: int
    ) -> ChannelProfile | None:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(ChannelProfile).where(
                        ChannelProfile.tenant_id == tenant_id,
                        ChannelProfile.telegram_id == telegram_id,
                    )
                )
                return result.scalar_one_or_none()

    async def _update_profile_stats(
        self, tenant_id: int, profile: ChannelProfile
    ) -> None:
        """Increment total_bans and recalculate success_rate and ban_risk."""
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(ChannelProfile).where(
                        ChannelProfile.id == profile.id,
                        ChannelProfile.tenant_id == tenant_id,
                    )
                )
                row: ChannelProfile | None = result.scalar_one_or_none()
                if row is None:
                    return
                row.total_bans = (row.total_bans or 0) + 1
                total = row.total_comments or 0
                bans = row.total_bans
                # success_rate = successful comments / total attempts
                # A ban implies one more failure; guard against divide-by-zero.
                denominator = max(total + bans, 1)
                row.success_rate = max(0.0, float(total) / denominator)
                row.ban_risk = compute_ban_risk(
                    success_rate=row.success_rate,
                    total_bans=bans,
                    total_comments=total,
                )
                row.last_ban_analysis_at = utcnow()

    async def _run_ai_analysis(
        self,
        tenant_id: int,
        channel_telegram_id: int,
        ban_type: str,
        last_actions: dict | None,
        profile: ChannelProfile,
    ) -> dict | None:
        prompt_context = (
            f"Ban type: {ban_type}\n"
            f"Channel telegram_id: {channel_telegram_id}\n"
            f"Channel type: {profile.channel_type}\n"
            f"Slow mode seconds: {profile.slow_mode_seconds}\n"
            f"No links: {profile.no_links}\n"
            f"Known rules: {json.dumps(profile.ai_extracted_rules or {})}\n"
            f"Learned rules: {json.dumps(profile.learned_rules or {})}\n"
            f"Last actions before ban: {json.dumps(last_actions or {})}\n"
        )
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                try:
                    result = await self._route_ai(
                        session,
                        task_type="ban_analysis",
                        prompt=prompt_context,
                        system_instruction=_BAN_ANALYSIS_PROMPT,
                        tenant_id=tenant_id,
                    )
                    if isinstance(result, dict) and result.get("parsed"):
                        return result["parsed"]
                except Exception as exc:
                    log.warning(
                        "ban_learner: AI analysis failed telegram_id=%s: %s",
                        channel_telegram_id,
                        exc,
                    )
        return None

    async def _apply_ai_analysis(
        self,
        tenant_id: int,
        telegram_id: int,
        analysis: dict,
        session: AsyncSession,
    ) -> None:
        """Merge AI-derived rules and safe interval into the ChannelProfile."""
        await apply_session_rls_context(session, tenant_id=tenant_id)
        result = await session.execute(
            select(ChannelProfile).where(
                ChannelProfile.tenant_id == tenant_id,
                ChannelProfile.telegram_id == telegram_id,
            )
        )
        profile: ChannelProfile | None = result.scalar_one_or_none()
        if profile is None:
            return

        new_rules: list = analysis.get("new_rules") or []
        if new_rules:
            current = list(profile.learned_rules or [])
            merged = list(dict.fromkeys(current + new_rules))  # deduplicate, preserve order
            profile.learned_rules = merged

        safe_interval = analysis.get("safe_interval_sec")
        if safe_interval is not None:
            try:
                new_interval = int(safe_interval)
                current_interval = profile.safe_comment_interval_sec or 0
                # Take the more conservative (larger) interval
                profile.safe_comment_interval_sec = max(current_interval, new_interval)
            except (TypeError, ValueError):
                pass

    def _invalidate_cache(self, tenant_id: int, telegram_id: int) -> None:
        """Fire-and-forget Redis cache deletion."""
        if self.redis is None:
            return
        import asyncio

        key = _KEY_CHANNEL_PROFILE.format(
            tenant_id=tenant_id, telegram_id=telegram_id
        )

        async def _delete() -> None:
            try:
                await self.redis.delete(key)
            except Exception as exc:
                log.debug("ban_learner: redis delete failed key=%s: %s", key, exc)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_delete())
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# ChannelMatcher — auto-match channels to tenant niche
# ---------------------------------------------------------------------------

_NICHE_MATCH_PROMPT = (
    "You are a Telegram channel discovery assistant. "
    "Given a tenant's niche description and a list of candidate channels, "
    "score each channel's relevance from 0.0 to 1.0 and explain why in one sentence. "
    "Return ONLY a valid JSON array of objects with keys: "
    '  "username" (string), "relevance_score" (float), "reason" (string). '
    "Sort by relevance_score descending. Do not include any explanation outside the JSON."
)

_QUALITY_SCORE_PROMPT = (
    "You are a Telegram channel quality analyst. "
    "Given channel metadata, estimate the following quality metrics: "
    "  engagement_rate (float 0-1): ratio of active commenters to members, "
    "  admin_response_time (string: 'fast' | 'slow' | 'unknown'): how quickly admins respond, "
    "  ban_risk (string: 'low' | 'medium' | 'high'): how risky it is to comment here. "
    "Return ONLY a valid JSON object with these three keys. "
    "No explanation outside the JSON."
)


class ChannelMatcher:
    """Matches channels to a tenant's niche using keyword search + AI scoring.

    Parameters
    ----------
    redis_client:
        Async Redis client. May be None.
    ai_router_func:
        Async callable matching ``route_ai_task`` signature. May be None.
    """

    def __init__(self, redis_client: Any, ai_router_func: Any) -> None:
        self.redis = redis_client
        self._route_ai = ai_router_func

    async def find_matching_channels(
        self,
        tenant_id: int,
        niche_description: str,
        keywords: list[str],
        limit: int = 50,
    ) -> list[dict]:
        """Return channels from channel_map_entries that match the tenant niche.

        Uses keyword search first, then optionally re-ranks with AI.

        Parameters
        ----------
        tenant_id:
            Tenant context for RLS.
        niche_description:
            Free-text description of the tenant's business/niche.
        keywords:
            List of keywords to search channels by title/category/tags.
        limit:
            Maximum channels to return.

        Returns
        -------
        List of channel dicts sorted by relevance score (highest first).
        """
        # Import here to avoid circular at module level
        try:
            from storage.models import ChannelMapEntry
        except ImportError:
            return []

        candidates: list[dict] = []
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                stmt = (
                    select(ChannelMapEntry)
                    .where(ChannelMapEntry.is_spam.is_(False))
                    .order_by(ChannelMapEntry.member_count.desc().nullslast())
                    .limit(200)
                )
                rows = (await session.execute(stmt)).scalars().all()
                for row in rows:
                    title = (row.title or "").lower()
                    tags = " ".join(row.topic_tags or []).lower() if hasattr(row, "topic_tags") else ""
                    category = (row.category or "").lower()
                    text_blob = f"{title} {category} {tags}"
                    if any(kw.lower() in text_blob for kw in keywords):
                        candidates.append(
                            {
                                "username": row.username or "",
                                "title": row.title or "",
                                "member_count": row.member_count or 0,
                                "category": row.category or "",
                                "language": row.language or "unknown",
                                "relevance_score": 0.5,  # placeholder before AI scoring
                                "reason": "keyword match",
                            }
                        )

        if not candidates:
            return []

        # Limit AI input to top 30 keyword-matched candidates
        top_candidates = candidates[:30]

        if self._route_ai is not None and niche_description:
            try:
                channel_list_text = "\n".join(
                    f"- @{c['username']}: {c['title']} ({c['member_count']} members, {c['category']})"
                    for c in top_candidates
                )
                prompt = (
                    f"Niche description: {niche_description}\n\n"
                    f"Candidate channels:\n{channel_list_text}"
                )
                async with async_session() as session:
                    async with session.begin():
                        await apply_session_rls_context(session, tenant_id=tenant_id)
                        result = await self._route_ai(
                            session,
                            task_type="farm_comment",
                            prompt=prompt,
                            system_instruction=_NICHE_MATCH_PROMPT,
                            tenant_id=tenant_id,
                            max_output_tokens=600,
                            temperature=0.2,
                            surface="channel_matcher",
                        )
                        if result.ok and result.parsed and isinstance(result.parsed, list):
                            scored_map: dict[str, dict] = {
                                item["username"]: item
                                for item in result.parsed
                                if isinstance(item, dict) and "username" in item
                            }
                            for c in top_candidates:
                                scored = scored_map.get(c["username"])
                                if scored:
                                    c["relevance_score"] = float(scored.get("relevance_score", 0.5))
                                    c["reason"] = str(scored.get("reason", "keyword match"))
            except Exception as exc:
                log.warning("channel_matcher: AI scoring failed: %s", exc)

        top_candidates.sort(key=lambda x: x["relevance_score"], reverse=True)
        return top_candidates[:limit]


# ---------------------------------------------------------------------------
# ChannelQualityScorer — score channels by engagement + admin response + ban risk
# ---------------------------------------------------------------------------


class ChannelQualityScorer:
    """Scores channels stored in channel_profiles by quality metrics.

    Computes an overall quality_score (0.0–1.0) from:
    - success_rate    (inverse of ban_risk)
    - member_count    (logarithmic scaling)
    - slow_mode_seconds (lower = better)
    - AI-estimated engagement_rate and admin_response_time

    Also manages blacklist: channels with ban_risk='critical' auto-blacklisted.

    Parameters
    ----------
    redis_client:
        Async Redis client. May be None.
    ai_router_func:
        Async callable. May be None.
    """

    def __init__(self, redis_client: Any, ai_router_func: Any) -> None:
        self.redis = redis_client
        self._route_ai = ai_router_func

    async def score_channel(
        self,
        tenant_id: int,
        profile: ChannelProfile,
    ) -> float:
        """Compute and persist the quality_score for a single channel profile.

        Returns the computed score (0.0–1.0).
        """
        import math

        # Base score from success_rate (0.0 is valid — means no successful comments)
        success = float(profile.success_rate) if profile.success_rate is not None else 1.0

        # Slow mode penalty: 0 sec = no penalty; 3600 sec (1hr) = heavy penalty
        slow_mode = min(int(profile.slow_mode_seconds or 0), 3600)
        slow_penalty = slow_mode / 7200.0  # max 0.5 penalty at 1-hour slow mode

        # Members bonus (logarithmic, capped at 0.3 bonus)
        member_count = 0
        if profile.channel_entry_id:
            try:
                async with async_session() as session:
                    async with session.begin():
                        await apply_session_rls_context(session, tenant_id=tenant_id)
                        entry_row = (await session.execute(
                            select(ChannelEntry).where(
                                ChannelEntry.id == profile.channel_entry_id,
                                ChannelEntry.tenant_id == tenant_id,
                            )
                        )).scalar_one_or_none()
                        if entry_row:
                            member_count = int(entry_row.member_count or 0)
            except Exception:
                pass
        member_bonus = min(math.log10(max(member_count, 1)) / 7.0, 0.3)  # log10(10M)/7 ≈ 1

        score = max(0.0, min(1.0, success - slow_penalty + member_bonus))

        # Persist score on the profile
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                row = (await session.execute(
                    select(ChannelProfile).where(
                        ChannelProfile.id == profile.id,
                        ChannelProfile.tenant_id == tenant_id,
                    )
                )).scalar_one_or_none()
                if row is not None:
                    row.quality_score = score
                    row.quality_scored_at = utcnow()

        # Auto-blacklist critical channels in their channel_entry
        if profile.ban_risk == "critical" and profile.channel_entry_id:
            await self._auto_blacklist_entry(tenant_id, profile.channel_entry_id)

        # Invalidate Redis cache
        if self.redis is not None:
            key = _KEY_CHANNEL_PROFILE.format(
                tenant_id=tenant_id,
                telegram_id=profile.telegram_id,
            )
            import asyncio

            async def _del() -> None:
                try:
                    await self.redis.delete(key)
                except Exception:
                    pass

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_del())
            except RuntimeError:
                pass

        return score

    async def score_all(self, tenant_id: int) -> int:
        """Re-score all channel profiles for the tenant.

        Returns the number of profiles scored.
        """
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                rows = (
                    await session.execute(
                        select(ChannelProfile).where(
                            ChannelProfile.tenant_id == tenant_id,
                        )
                    )
                ).scalars().all()

        count = 0
        for profile in rows:
            try:
                await self.score_channel(tenant_id, profile)
                count += 1
            except Exception as exc:
                log.warning(
                    "channel_quality_scorer: score_channel failed profile_id=%s: %s",
                    profile.id,
                    exc,
                )
        return count

    async def get_quality_rankings(
        self,
        tenant_id: int,
        limit: int = 50,
    ) -> list[dict]:
        """Return channel profiles sorted by quality_score descending."""
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                rows = (
                    await session.execute(
                        select(ChannelProfile)
                        .where(ChannelProfile.tenant_id == tenant_id)
                        .order_by(
                            ChannelProfile.quality_score.desc().nullslast()
                        )
                        .limit(limit)
                    )
                ).scalars().all()
                return [
                    {
                        "id": r.id,
                        "telegram_id": r.telegram_id,
                        "username": r.username,
                        "title": r.title,
                        "ban_risk": r.ban_risk,
                        "success_rate": round(float(r.success_rate) if r.success_rate is not None else 1.0, 3),
                        "total_comments": r.total_comments or 0,
                        "total_bans": r.total_bans or 0,
                        "quality_score": round(float(r.quality_score or 0.0), 3) if hasattr(r, "quality_score") else None,
                        "quality_scored_at": r.quality_scored_at.isoformat() if hasattr(r, "quality_scored_at") and r.quality_scored_at else None,
                        "slow_mode_seconds": r.slow_mode_seconds or 0,
                        "safe_comment_interval_sec": r.safe_comment_interval_sec or 0,
                        "channel_entry_id": r.channel_entry_id,
                    }
                    for r in rows
                ]

    async def blacklist_channel(
        self,
        tenant_id: int,
        channel_entry_id: int,
    ) -> bool:
        """Manually blacklist a channel entry.

        Returns True if the entry was found and updated, False otherwise.
        """
        return await self._auto_blacklist_entry(tenant_id, channel_entry_id)

    async def _auto_blacklist_entry(
        self,
        tenant_id: int,
        channel_entry_id: int,
    ) -> bool:
        """Set blacklisted=True on a channel_entry row."""
        try:
            async with async_session() as session:
                async with session.begin():
                    await apply_session_rls_context(session, tenant_id=tenant_id)
                    row = (await session.execute(
                        select(ChannelEntry).where(
                            ChannelEntry.id == channel_entry_id,
                            ChannelEntry.tenant_id == tenant_id,
                        )
                    )).scalar_one_or_none()
                    if row is None:
                        return False
                    row.blacklisted = True
                    log.info(
                        "channel_quality_scorer: auto-blacklisted entry_id=%s tenant_id=%s",
                        channel_entry_id,
                        tenant_id,
                    )
                    return True
        except Exception as exc:
            log.warning(
                "channel_quality_scorer: _auto_blacklist_entry failed entry_id=%s: %s",
                channel_entry_id,
                exc,
            )
            return False


# ---------------------------------------------------------------------------
# Micro-topic classification
# ---------------------------------------------------------------------------

_CLASSIFICATION_PROMPT_TEMPLATE = (
    "You are a Telegram channel topic classifier.\n"
    "Analyze the following channel and return ONLY a valid JSON object:\n"
    "{{\n"
    '  "main_category": "<primary category, e.g. Crypto, Marketing, Tech, Finance, Lifestyle>",\n'
    '  "subcategory": "<specific subcategory, e.g. Trading, DeFi, SaaS, E-commerce>",\n'
    '  "micro_topics": ["<topic1>", "<topic2>", "<topic3>"],\n'
    '  "language": "<2-letter language code, e.g. ru, en, uk>",\n'
    '  "audience_type": "<target audience, e.g. traders, developers, marketers, entrepreneurs>"\n'
    "}}\n\n"
    "Channel title: {title}\n"
    "Channel description: {description}\n\n"
    "Rules:\n"
    "- Return 3 to 7 micro_topics.\n"
    "- Use English for all field values.\n"
    "- Do not add any explanation outside the JSON object."
)

_CLASSIFICATION_SYSTEM = (
    "You are a precise channel taxonomy classifier. "
    "Return ONLY a JSON object with exactly these keys: "
    "main_category, subcategory, micro_topics, language, audience_type. "
    "Never include markdown code fences or extra text."
)


async def classify_channel_topics(
    session: AsyncSession,
    channel_id: int,
    tenant_id: int,
) -> dict:
    """Use AI to classify a channel into micro-topics based on title + description.

    Fetches the ChannelMapEntry by id, calls route_ai_task with
    task_type='channel_classification', updates category / subcategory /
    topic_tags on the row, and returns the parsed classification dict.

    channel_map_entries.tenant_id is nullable — this function handles both
    platform catalog rows (tenant_id IS NULL) and tenant-owned rows.
    """
    from core.ai_router import route_ai_task

    # Load the channel entry — works with both platform-level and tenant-owned rows.
    stmt = select(ChannelMapEntry).where(ChannelMapEntry.id == channel_id)
    row: ChannelMapEntry | None = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ValueError(f"ChannelMapEntry id={channel_id} not found")

    title = row.title or ""
    description = row.description or ""
    if not title and not description:
        return {
            "main_category": None,
            "subcategory": None,
            "micro_topics": [],
            "language": None,
            "audience_type": None,
            "channel_id": channel_id,
            "skipped": True,
            "reason": "no_title_or_description",
        }

    prompt = _CLASSIFICATION_PROMPT_TEMPLATE.format(
        title=title[:500],
        description=description[:1000],
    )

    result = await route_ai_task(
        session,
        task_type="channel_classification",
        prompt=prompt,
        system_instruction=_CLASSIFICATION_SYSTEM,
        tenant_id=tenant_id,
        max_output_tokens=400,
        temperature=0.2,
        surface="channel_map",
    )

    if not result.ok or not result.parsed:
        return {
            "main_category": None,
            "subcategory": None,
            "micro_topics": [],
            "language": None,
            "audience_type": None,
            "channel_id": channel_id,
            "skipped": True,
            "reason": result.reason_code or "ai_failed",
        }

    parsed = result.parsed
    main_category = parsed.get("main_category") or None
    subcategory = parsed.get("subcategory") or None
    micro_topics = parsed.get("micro_topics") or []
    language = parsed.get("language") or None
    audience_type = parsed.get("audience_type") or None

    # Persist classification back to the row
    if main_category:
        row.category = main_category
    if subcategory:
        row.subcategory = subcategory
    if micro_topics:
        existing_tags: list = list(row.topic_tags or [])
        # Merge, de-duplicate, preserve order
        for t in micro_topics:
            if t not in existing_tags:
                existing_tags.append(t)
        row.topic_tags = existing_tags
    if language and not row.language:
        row.language = language
    row.last_refreshed_at = utcnow()

    return {
        "main_category": main_category,
        "subcategory": subcategory,
        "micro_topics": micro_topics,
        "language": language,
        "audience_type": audience_type,
        "channel_id": channel_id,
        "ai_request_id": result.ai_request_id,
    }


async def classify_channels_batch(
    session: AsyncSession,
    channel_ids: list[int],
    tenant_id: int,
) -> list[dict]:
    """Classify up to 20 channels in one call.

    Processes each channel sequentially (AI calls are already async).
    Returns one result dict per channel_id in the same order.
    """
    if len(channel_ids) > 20:
        raise ValueError("classify_channels_batch: maximum 20 channels per call")

    results: list[dict] = []
    for cid in channel_ids:
        try:
            res = await classify_channel_topics(session, channel_id=cid, tenant_id=tenant_id)
        except Exception as exc:
            res = {
                "channel_id": cid,
                "skipped": True,
                "reason": str(exc),
            }
        results.append(res)
    return results
