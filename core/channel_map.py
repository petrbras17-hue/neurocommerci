"""
ChannelMapService — tenant-scoped channel index for search and targeting.

Stores channel metadata in channel_map_entries (tenant-scoped).
Used by campaigns to select target channels.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select

from storage.models import ChannelMapEntry
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow

log = logging.getLogger(__name__)


def _entry_to_dict(entry: ChannelMapEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "tenant_id": entry.tenant_id,
        "telegram_id": entry.telegram_id,
        "username": entry.username,
        "title": entry.title,
        "category": entry.category,
        "subcategory": entry.subcategory,
        "language": entry.language,
        "member_count": entry.member_count,
        "has_comments": entry.has_comments,
        "avg_post_reach": entry.avg_post_reach,
        "engagement_rate": entry.engagement_rate,
        "last_indexed_at": (
            entry.last_indexed_at.isoformat() if entry.last_indexed_at else None
        ),
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


class ChannelMapService:
    """CRUD and search for the tenant channel index."""

    # ------------------------------------------------------------------
    # List / search
    # ------------------------------------------------------------------

    async def list_channels(
        self,
        tenant_id: int,
        category: str | None = None,
        language: str | None = None,
        min_members: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                q = (
                    select(ChannelMapEntry)
                    .where(ChannelMapEntry.tenant_id == tenant_id)
                    .where(ChannelMapEntry.member_count >= min_members)
                    .order_by(ChannelMapEntry.member_count.desc())
                )
                if category:
                    q = q.where(ChannelMapEntry.category == category)
                if language:
                    q = q.where(ChannelMapEntry.language == language)

                count_q = select(func.count()).select_from(q.subquery())
                total = (await session.execute(count_q)).scalar_one()

                items_result = await session.execute(q.limit(limit))
                items = [_entry_to_dict(r) for r in items_result.scalars().all()]

        log.debug(
            "list_channels tenant=%s total=%s returned=%s", tenant_id, total, len(items)
        )
        return {"items": items, "total": total}

    async def search_channels(
        self,
        tenant_id: int,
        query: str | None = None,
        category: str | None = None,
        language: str | None = None,
        min_members: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                q = (
                    select(ChannelMapEntry)
                    .where(ChannelMapEntry.tenant_id == tenant_id)
                    .where(ChannelMapEntry.member_count >= min_members)
                    .order_by(ChannelMapEntry.member_count.desc())
                )
                if query:
                    safe_q = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    pattern = f"%{safe_q}%"
                    q = q.where(
                        ChannelMapEntry.username.ilike(pattern)
                        | ChannelMapEntry.title.ilike(pattern)
                    )
                if category:
                    q = q.where(ChannelMapEntry.category == category)
                if language:
                    q = q.where(ChannelMapEntry.language == language)

                count_q = select(func.count()).select_from(q.subquery())
                total = (await session.execute(count_q)).scalar_one()

                items_result = await session.execute(q.limit(limit))
                items = [_entry_to_dict(r) for r in items_result.scalars().all()]

        log.debug(
            "search_channels tenant=%s query=%r total=%s returned=%s",
            tenant_id,
            query,
            total,
            len(items),
        )
        return {"items": items, "total": total}

    # ------------------------------------------------------------------
    # Facets
    # ------------------------------------------------------------------

    async def get_categories(self, tenant_id: int) -> list[str]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(ChannelMapEntry.category)
                    .where(ChannelMapEntry.tenant_id == tenant_id)
                    .where(ChannelMapEntry.category.isnot(None))
                    .distinct()
                    .order_by(ChannelMapEntry.category)
                )
                return [row[0] for row in result.all()]

    async def get_stats(self, tenant_id: int) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)

                total_result = await session.execute(
                    select(func.count(ChannelMapEntry.id)).where(
                        ChannelMapEntry.tenant_id == tenant_id
                    )
                )
                total = total_result.scalar_one()

                cat_result = await session.execute(
                    select(ChannelMapEntry.category, func.count(ChannelMapEntry.id))
                    .where(ChannelMapEntry.tenant_id == tenant_id)
                    .where(ChannelMapEntry.category.isnot(None))
                    .group_by(ChannelMapEntry.category)
                    .order_by(func.count(ChannelMapEntry.id).desc())
                )
                by_category = {row[0]: row[1] for row in cat_result.all()}

                lang_result = await session.execute(
                    select(ChannelMapEntry.language, func.count(ChannelMapEntry.id))
                    .where(ChannelMapEntry.tenant_id == tenant_id)
                    .where(ChannelMapEntry.language.isnot(None))
                    .group_by(ChannelMapEntry.language)
                    .order_by(func.count(ChannelMapEntry.id).desc())
                )
                by_language = {row[0]: row[1] for row in lang_result.all()}

        return {"total": total, "by_category": by_category, "by_language": by_language}

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def add_channel(self, tenant_id: int, data: dict[str, Any]) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                entry = ChannelMapEntry(
                    tenant_id=tenant_id,
                    telegram_id=data.get("telegram_id"),
                    username=data.get("username"),
                    title=data.get("title"),
                    category=data.get("category"),
                    subcategory=data.get("subcategory"),
                    language=data.get("language"),
                    member_count=int(data.get("member_count") or 0),
                    has_comments=bool(data.get("has_comments", False)),
                    avg_post_reach=data.get("avg_post_reach"),
                    engagement_rate=data.get("engagement_rate"),
                    last_indexed_at=utcnow(),
                )
                session.add(entry)
                await session.flush()
                result = _entry_to_dict(entry)

        log.info(
            "add_channel tenant=%s channel_id=%s username=%s",
            tenant_id,
            result["id"],
            result["username"],
        )
        return result

    async def delete_channel(self, tenant_id: int, channel_id: int) -> bool:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(ChannelMapEntry).where(
                        ChannelMapEntry.id == channel_id,
                        ChannelMapEntry.tenant_id == tenant_id,
                    )
                )
                entry = result.scalar_one_or_none()
                if entry is None:
                    return False
                await session.delete(entry)

        log.info("delete_channel tenant=%s channel_id=%s", tenant_id, channel_id)
        return True
