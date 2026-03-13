"""
Channel Parser Service — discovers Telegram channels by keywords and filters.

Searches Telegram's global search, validates channels (open comments, active,
member count), and saves results to tenant-scoped channel databases.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.account_capabilities import is_frozen_error
from storage.models import Account, ChannelDatabase, ChannelEntry, ParsingJob
from utils.helpers import utcnow
from utils.logger import log

_RU_TEXT_RE = re.compile(r"[А-Яа-яЁё]")

# An active channel must have posted within this many days.
_ACTIVE_POST_DAYS = 7


def _detect_channel_language(channel_info: dict) -> str:
    """Detect language from title and description heuristic.

    Returns 'ru' if Cyrillic characters are detected, otherwise 'en'.
    """
    title = channel_info.get("title", "") or ""
    about = channel_info.get("about", "") or ""
    if _RU_TEXT_RE.search(f"{title}\n{about}"):
        return "ru"
    return "en"


def _estimate_activity_level(last_post_date: Optional[datetime]) -> str:
    """Estimate activity level from the last post date.

    Returns one of: 'high', 'medium', 'low', 'dead'.
    """
    if last_post_date is None:
        return "dead"
    now = datetime.now(timezone.utc)
    # Normalise to aware datetime if needed.
    if last_post_date.tzinfo is None:
        last_post_date = last_post_date.replace(tzinfo=timezone.utc)
    age_days = (now - last_post_date).days
    if age_days <= 2:
        return "high"
    if age_days <= 7:
        return "medium"
    if age_days <= 30:
        return "low"
    return "dead"


class ChannelParserService:
    """Parses Telegram channels by keywords and saves them to tenant databases."""

    def __init__(self, session_manager: Any, redis_client: Any = None) -> None:
        self.session_manager = session_manager
        self.redis_client = redis_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def parse_channels(
        self,
        job_id: int,
        account_id: int,
        keywords: list[str],
        filters: dict,
        max_results: int,
        session: AsyncSession,
    ) -> list[dict]:
        """Search Telegram for channels matching the given keywords and filters.

        Returns a list of validated channel dicts.
        Deduplicates by telegram_id.  Stops once max_results is reached.
        """
        cleaned_keywords = [kw.strip() for kw in keywords if kw and kw.strip()]
        if not cleaned_keywords:
            return []

        account = await self._load_account(account_id, session)
        if account is None:
            raise RuntimeError(f"account_not_found: account_id={account_id}")

        client = self.session_manager.get_client(account.phone)
        if client is None or not client.is_connected():
            raise RuntimeError(
                f"account_not_connected: phone={account.phone}"
            )

        # Update job to running
        job = await session.get(ParsingJob, job_id)
        if job is not None:
            job.status = "running"
            job.started_at = utcnow()
            await session.commit()

        discovered: dict[int, dict] = {}

        min_members: int = int(filters.get("min_members", 0))
        max_members: Optional[int] = (
            int(filters["max_members"]) if filters.get("max_members") else None
        )
        require_comments: bool = bool(filters.get("has_comments", True))
        language_filter: Optional[str] = filters.get("language")
        active_only: bool = bool(filters.get("active_only", True))

        for i, keyword in enumerate(cleaned_keywords):
            if len(discovered) >= max_results:
                break

            if i > 0:
                await asyncio.sleep(2.0)  # avoid FloodWait between keyword searches

            try:
                results = await self._search_one_keyword(client, keyword, limit=50)
            except RuntimeError as exc:
                log.warning(f"ChannelParserService: keyword '{keyword}' search failed: {exc}")
                continue

            for raw_channel in results:
                if len(discovered) >= max_results:
                    break

                tg_id = raw_channel.get("telegram_id")
                if tg_id is None or tg_id in discovered:
                    continue

                try:
                    validated = await self.validate_channel(
                        client,
                        raw_channel["_entity"],
                        min_members=min_members,
                        max_members=max_members,
                        require_comments=require_comments,
                        active_only=active_only,
                        language_filter=language_filter,
                    )
                except Exception as val_exc:
                    # FloodWait or frozen — stop this keyword batch
                    log.warning(
                        f"ChannelParserService: validate_channel error: {val_exc}"
                    )
                    break
                if validated is not None:
                    discovered[validated["telegram_id"]] = validated

        channels = list(discovered.values())

        # Update job results_count
        if job is not None:
            job.results_count = len(channels)
            await session.commit()

        return channels

    async def validate_channel(
        self,
        client: Any,
        channel: Any,
        min_members: int = 0,
        max_members: Optional[int] = None,
        require_comments: bool = True,
        active_only: bool = True,
        language_filter: Optional[str] = None,
    ) -> Optional[dict]:
        """Validate a single Telegram channel entity.

        Fetches full channel info, checks comments, activity, and member count.
        Returns a channel dict or None if the channel does not pass filters.
        """
        try:
            from telethon import functions as tl_functions
            from telethon.errors import FloodWaitError

            full = await client(
                tl_functions.channels.GetFullChannelRequest(channel=channel)
            )
        except FloodWaitError:
            raise  # let caller handle rate limiting
        except Exception as exc:
            if is_frozen_error(exc):
                raise RuntimeError(f"account_frozen: {exc}") from exc
            log.debug(
                f"ChannelParserService.validate_channel: skip "
                f"{getattr(channel, 'id', 'unknown')}: {exc}"
            )
            return None

        full_chat = full.full_chat
        participants_count = getattr(full_chat, "participants_count", None)
        if participants_count is None:
            participants_count = getattr(channel, "participants_count", 0) or 0

        if participants_count < min_members:
            return None
        if max_members is not None and participants_count > max_members:
            return None

        linked_chat_id = getattr(full_chat, "linked_chat_id", None)
        has_comments = bool(linked_chat_id)
        if require_comments and not has_comments:
            return None

        # Detect last post date from latest message if available.
        last_post_at: Optional[datetime] = None
        try:
            messages = await client.get_messages(channel, limit=1)
            if messages:
                last_post_at = getattr(messages[0], "date", None)
        except Exception:
            pass

        if active_only:
            if last_post_at is None:
                return None
            threshold = datetime.now(timezone.utc) - timedelta(days=_ACTIVE_POST_DAYS)
            if last_post_at.tzinfo is None:
                last_post_at = last_post_at.replace(tzinfo=timezone.utc)
            if last_post_at < threshold:
                return None

        about = (getattr(full_chat, "about", "") or "").strip()
        title = (getattr(channel, "title", "") or "").strip()
        username = getattr(channel, "username", None)
        telegram_id = int(channel.id)

        channel_dict = {
            "telegram_id": telegram_id,
            "username": username,
            "title": title or f"channel_{telegram_id}",
            "member_count": int(participants_count),
            "has_comments": has_comments,
            "about": about,
            "last_post_at": last_post_at,
            "language": _detect_channel_language({"title": title, "about": about}),
            "activity_level": _estimate_activity_level(last_post_at),
        }

        if language_filter and channel_dict["language"] != language_filter:
            return None

        return channel_dict

    async def save_to_database(
        self,
        channels: list[dict],
        database_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> int:
        """Insert validated channels into channel_entries, skip duplicates.

        Returns the count of newly inserted channels.
        """
        if not channels:
            return 0

        # Verify the database belongs to the tenant (basic RLS-equivalent check).
        db_row = await session.get(ChannelDatabase, database_id)
        if db_row is None or db_row.tenant_id != tenant_id:
            raise RuntimeError(
                f"channel_database_not_found: database_id={database_id} tenant_id={tenant_id}"
            )

        # Load existing telegram_ids for this database to detect duplicates.
        existing_result = await session.execute(
            select(ChannelEntry.telegram_id).where(
                and_(
                    ChannelEntry.database_id == database_id,
                    ChannelEntry.tenant_id == tenant_id,
                )
            )
        )
        existing_ids: set[int] = {
            row[0] for row in existing_result.all() if row[0] is not None
        }

        added = 0
        for ch in channels:
            tg_id = ch.get("telegram_id")
            if tg_id is not None and tg_id in existing_ids:
                continue
            entry = ChannelEntry(
                tenant_id=tenant_id,
                database_id=database_id,
                telegram_id=tg_id,
                username=ch.get("username"),
                title=ch.get("title"),
                member_count=ch.get("member_count"),
                has_comments=bool(ch.get("has_comments", True)),
                language=ch.get("language"),
                last_post_at=ch.get("last_post_at"),
                blacklisted=False,
            )
            session.add(entry)
            if tg_id is not None:
                existing_ids.add(tg_id)
            added += 1

        if added:
            await session.commit()

        log.info(
            f"ChannelParserService.save_to_database: "
            f"added={added} skipped={len(channels) - added} database_id={database_id}"
        )
        return added

    async def get_job_status(self, job_id: int, session: AsyncSession) -> dict:
        """Return current job status and results count."""
        job = await session.get(ParsingJob, job_id)
        if job is None:
            return {"found": False, "job_id": job_id}
        return {
            "found": True,
            "job_id": job.id,
            "status": job.status,
            "results_count": job.results_count,
            "keywords": job.keywords,
            "filters": job.filters,
            "max_results": job.max_results,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "error": job.error,
        }

    async def run_parsing_job(self, job_id: int, session: AsyncSession) -> None:
        """Load job from DB, run parsing, save to target database, update status.

        Handles errors gracefully and updates job.error on failure.
        """
        job = await session.get(ParsingJob, job_id)
        if job is None:
            log.error(f"ChannelParserService.run_parsing_job: job_id={job_id} not found")
            return

        if job.account_id is None:
            job.status = "failed"
            job.error = "no_account_assigned"
            job.completed_at = utcnow()
            await session.commit()
            return

        keywords: list[str] = list(job.keywords or [])
        filters: dict = dict(job.filters or {})
        max_results: int = int(job.max_results or 50)

        try:
            channels = await self.parse_channels(
                job_id=job_id,
                account_id=job.account_id,
                keywords=keywords,
                filters=filters,
                max_results=max_results,
                session=session,
            )

            if job.target_database_id is not None:
                await self.save_to_database(
                    channels=channels,
                    database_id=job.target_database_id,
                    tenant_id=job.tenant_id,
                    session=session,
                )

            # Re-fetch job after potential commit inside sub-methods.
            job = await session.get(ParsingJob, job_id)
            if job is not None:
                job.status = "completed"
                job.results_count = len(channels)
                job.completed_at = utcnow()
                await session.commit()

            log.info(
                f"ChannelParserService.run_parsing_job: job_id={job_id} "
                f"completed results={len(channels)}"
            )

        except Exception as exc:
            log.error(f"ChannelParserService.run_parsing_job: job_id={job_id} failed: {exc}")
            try:
                job = await session.get(ParsingJob, job_id)
                if job is not None:
                    job.status = "failed"
                    job.error = str(exc)[:1000]
                    job.completed_at = utcnow()
                    await session.commit()
            except Exception as inner:
                log.error(
                    f"ChannelParserService.run_parsing_job: "
                    f"could not update job error state: {inner}"
                )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _search_one_keyword(
        self, client: Any, keyword: str, limit: int = 50
    ) -> list[dict]:
        """Run a contacts.SearchRequest for one keyword.

        Returns a list of raw dicts containing at minimum 'telegram_id'
        and '_entity' for further validation.
        Raises RuntimeError prefixed with 'search_blocked_by_telegram:' on freeze.
        """
        try:
            from telethon import functions as tl_functions
            from telethon.tl.types import Channel as TLChannel

            result = await client(
                tl_functions.contacts.SearchRequest(q=keyword, limit=limit)
            )
        except Exception as exc:
            if is_frozen_error(exc):
                raise RuntimeError(f"search_blocked_by_telegram: {exc}") from exc
            log.warning(f"ChannelParserService._search_one_keyword '{keyword}': {exc}")
            return []

        channels: list[dict] = []
        for chat in getattr(result, "chats", []):
            if not isinstance(chat, TLChannel):
                continue
            if not getattr(chat, "broadcast", False):
                continue
            channels.append(
                {
                    "telegram_id": int(chat.id),
                    "_entity": chat,
                }
            )
        return channels

    @staticmethod
    async def _load_account(
        account_id: int, session: AsyncSession, *, tenant_id: Optional[int] = None
    ) -> Optional[Account]:
        stmt = select(Account).where(Account.id == account_id)
        if tenant_id is not None:
            stmt = stmt.where(Account.tenant_id == tenant_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
