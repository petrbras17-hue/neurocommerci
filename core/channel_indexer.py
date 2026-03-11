"""
ChannelIndexer — fetches Telegram channel metadata via Telethon and saves
entries to the platform-level channel_map_entries catalog (tenant_id = NULL).

Gracefully handles the case where no Telethon sessions are available by
returning stub entries with a warning log.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import ChannelMapEntry
from utils.helpers import utcnow
from utils.logger import log


# ---------------------------------------------------------------------------
# Language detection helpers (mirrors channel_parser_service)
# ---------------------------------------------------------------------------

import re

_RU_TEXT_RE = re.compile(r"[А-Яа-яЁё]")
_ACTIVE_POST_DAYS = 7
_CIS_LANGUAGES = {"ru", "uk", "kz", "uz", "by"}


def _detect_language(title: str, about: str) -> str:
    if _RU_TEXT_RE.search(f"{title}\n{about}"):
        return "ru"
    return "en"


def _estimate_post_frequency(messages: list[Any]) -> Optional[float]:
    """Estimate average posts per day from the last N messages."""
    if len(messages) < 2:
        return None
    try:
        dates = sorted(
            [getattr(m, "date", None) for m in messages if getattr(m, "date", None)],
            reverse=True,
        )
        if len(dates) < 2:
            return None
        newest = dates[0]
        oldest = dates[-1]
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        days = max((newest - oldest).total_seconds() / 86400, 1)
        return round(len(dates) / days, 2)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ChannelIndexer
# ---------------------------------------------------------------------------


class ChannelIndexer:
    """
    Indexes Telegram channels into the platform-level channel_map_entries
    catalog (tenant_id = NULL).

    session_manager: optional — when None (or no live clients), index_channel
    falls back to stub mode and logs a warning.
    """

    def __init__(self, session_manager: Any = None) -> None:
        self.session_manager = session_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def index_channel(
        self,
        username_or_id: str,
        session: AsyncSession,
    ) -> ChannelMapEntry:
        """Fetch Telegram metadata for a single channel and upsert into
        channel_map_entries with tenant_id = NULL.

        Falls back to a stub entry when no Telethon client is available.
        """
        client = await self._get_client()
        if client is None:
            log.warning(
                f"ChannelIndexer.index_channel: no Telethon session available, "
                f"returning stub for '{username_or_id}'"
            )
            return await self._upsert_stub(str(username_or_id), session)

        return await self._fetch_and_upsert(client, username_or_id, session)

    async def bulk_index(
        self,
        usernames: list[str],
        session: AsyncSession,
        delay_seconds: float = 2.0,
    ) -> list[ChannelMapEntry]:
        """Batch-index a list of channel usernames with inter-request rate
        limiting.

        Returns the list of upserted ChannelMapEntry objects.
        """
        results: list[ChannelMapEntry] = []
        for i, username in enumerate(usernames):
            if i > 0:
                await asyncio.sleep(delay_seconds)
            try:
                entry = await self.index_channel(username, session)
                results.append(entry)
                log.info(
                    f"ChannelIndexer.bulk_index: {i + 1}/{len(usernames)} "
                    f"indexed '{username}' id={entry.id}"
                )
            except Exception as exc:
                log.warning(
                    f"ChannelIndexer.bulk_index: failed '{username}': {exc}"
                )
        return results

    async def refresh_stale(
        self,
        max_age_days: int = 7,
        limit: int = 100,
        session: AsyncSession = None,  # type: ignore[assignment]
    ) -> int:
        """Re-index platform catalog entries whose last_refreshed_at is older
        than max_age_days (or NULL).

        Requires an external AsyncSession passed in; returns the count of
        entries successfully refreshed.
        """
        if session is None:
            raise ValueError("refresh_stale requires an AsyncSession")

        cutoff = utcnow() - timedelta(days=max_age_days)
        stale_q = (
            select(ChannelMapEntry)
            .where(
                ChannelMapEntry.tenant_id.is_(None),
                or_(
                    ChannelMapEntry.last_refreshed_at.is_(None),
                    ChannelMapEntry.last_refreshed_at < cutoff,
                ),
                ChannelMapEntry.username.isnot(None),
            )
            .order_by(ChannelMapEntry.last_refreshed_at.asc().nullsfirst())
            .limit(limit)
        )
        stale_rows = (await session.execute(stale_q)).scalars().all()

        if not stale_rows:
            log.info("ChannelIndexer.refresh_stale: no stale entries found")
            return 0

        refreshed = 0
        client = await self._get_client()

        for entry in stale_rows:
            if entry.username is None:
                continue
            try:
                if client is not None:
                    updated = await self._fetch_and_upsert(
                        client, entry.username, session
                    )
                else:
                    # Bump refresh timestamp even without live data so we don't
                    # hammer the refresh queue on every run.
                    entry.last_refreshed_at = utcnow()
                    await session.commit()
                    updated = entry
                refreshed += 1
                log.debug(
                    f"ChannelIndexer.refresh_stale: refreshed '{entry.username}' id={updated.id}"
                )
                await asyncio.sleep(1.5)
            except Exception as exc:
                log.warning(
                    f"ChannelIndexer.refresh_stale: failed '{entry.username}': {exc}"
                )

        log.info(
            f"ChannelIndexer.refresh_stale: refreshed {refreshed}/{len(stale_rows)} entries"
        )
        return refreshed

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> Optional[Any]:
        """Return the first connected Telethon client, or None."""
        if self.session_manager is None:
            return None
        try:
            phones = self.session_manager.get_connected_phones()
            for phone in phones:
                client = self.session_manager.get_client(phone)
                if client and client.is_connected():
                    return client
        except Exception as exc:
            log.warning(f"ChannelIndexer._get_client: {exc}")
        return None

    async def _fetch_and_upsert(
        self,
        client: Any,
        username_or_id: str,
        session: AsyncSession,
    ) -> ChannelMapEntry:
        """Fetch full channel info from Telegram and upsert the ORM row."""
        from telethon import functions as tl_functions
        from telethon.tl.types import Channel as TLChannel

        try:
            entity = await client.get_entity(username_or_id)
        except Exception as exc:
            log.warning(
                f"ChannelIndexer._fetch_and_upsert: get_entity '{username_or_id}' failed: {exc}"
            )
            return await self._upsert_stub(str(username_or_id), session)

        if not isinstance(entity, TLChannel) or not getattr(entity, "broadcast", False):
            log.warning(
                f"ChannelIndexer._fetch_and_upsert: '{username_or_id}' is not a broadcast channel"
            )
            return await self._upsert_stub(str(username_or_id), session)

        # Full channel details
        try:
            full = await client(tl_functions.channels.GetFullChannelRequest(channel=entity))
            full_chat = full.full_chat
        except Exception as exc:
            log.warning(
                f"ChannelIndexer._fetch_and_upsert: GetFullChannelRequest failed "
                f"for '{username_or_id}': {exc}"
            )
            return await self._upsert_stub(str(username_or_id), session)

        about = (getattr(full_chat, "about", "") or "").strip()
        linked_chat_id = getattr(full_chat, "linked_chat_id", None)
        participants_count = getattr(full_chat, "participants_count", None)
        if participants_count is None:
            participants_count = getattr(entity, "participants_count", 0) or 0

        title = (getattr(entity, "title", "") or "").strip()
        username = getattr(entity, "username", None) or str(username_or_id).lstrip("@")
        telegram_id = int(entity.id)
        language = _detect_language(title, about)

        # Estimate post frequency from last 20 messages
        post_freq: Optional[float] = None
        avg_comments: Optional[float] = None
        last_post_at: Optional[datetime] = None
        try:
            messages = await client.get_messages(entity, limit=20)
            post_freq = _estimate_post_frequency(messages)
            if messages:
                last_post_at = getattr(messages[0], "date", None)
        except Exception:
            pass

        now = utcnow()
        channel_data = {
            "telegram_id": telegram_id,
            "username": username,
            "title": title or f"channel_{telegram_id}",
            "description": about or None,
            "language": language,
            "member_count": int(participants_count),
            "has_comments": bool(linked_chat_id),
            "comments_enabled": bool(linked_chat_id),
            "post_frequency_daily": post_freq,
            "avg_comments_per_post": None,  # requires comment history — not cheap
            "source": "parsed",
            "last_indexed_at": now,
            "last_refreshed_at": now,
        }

        return await self._upsert_entry(channel_data, session)

    async def _upsert_stub(
        self, username: str, session: AsyncSession
    ) -> ChannelMapEntry:
        """Create or return a minimal stub entry when live data is unavailable."""
        clean_username = username.lstrip("@")
        existing = await self._find_by_username(clean_username, session)
        if existing is not None:
            existing.last_refreshed_at = utcnow()
            await session.commit()
            return existing

        entry = ChannelMapEntry(
            tenant_id=None,
            username=clean_username,
            title=clean_username,
            source="stub",
            last_indexed_at=utcnow(),
            last_refreshed_at=utcnow(),
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        log.info(f"ChannelIndexer._upsert_stub: created stub entry for '{clean_username}'")
        return entry

    async def _upsert_entry(
        self, data: dict, session: AsyncSession
    ) -> ChannelMapEntry:
        """Upsert a channel_map_entries row keyed by (username, tenant_id=NULL)."""
        username = data.get("username")
        telegram_id = data.get("telegram_id")

        existing: Optional[ChannelMapEntry] = None
        if telegram_id:
            existing = await self._find_by_telegram_id(telegram_id, session)
        if existing is None and username:
            existing = await self._find_by_username(username, session)

        if existing is not None:
            for key, value in data.items():
                if value is not None:
                    setattr(existing, key, value)
            await session.commit()
            await session.refresh(existing)
            return existing

        entry = ChannelMapEntry(tenant_id=None, **data)
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return entry

    async def _find_by_username(
        self, username: str, session: AsyncSession
    ) -> Optional[ChannelMapEntry]:
        result = await session.execute(
            select(ChannelMapEntry).where(
                ChannelMapEntry.tenant_id.is_(None),
                ChannelMapEntry.username == username,
            )
        )
        return result.scalar_one_or_none()

    async def _find_by_telegram_id(
        self, telegram_id: int, session: AsyncSession
    ) -> Optional[ChannelMapEntry]:
        result = await session.execute(
            select(ChannelMapEntry).where(
                ChannelMapEntry.tenant_id.is_(None),
                ChannelMapEntry.telegram_id == telegram_id,
            )
        )
        return result.scalar_one_or_none()
