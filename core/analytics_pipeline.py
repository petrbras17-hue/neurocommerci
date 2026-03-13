"""Analytics pipeline for NEURO COMMENTING SaaS.

Responsibilities:
- record_event: write an AnalyticsEvent row
- get_daily_stats: return aggregated comments/reactions/bans per day
- get_channel_comparison: rank channels by engagement
- get_heatmap_data: best hours for commenting (7 x 24 grid)

Redis cache with TTL=300 s is used for frequently read aggregations.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import AnalyticsEvent
from utils.helpers import utcnow

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis cache helpers (optional — gracefully degrades if Redis unavailable)
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 300


async def _cache_get(key: str) -> dict[str, Any] | None:
    try:
        from core.task_queue import task_queue
        await task_queue.connect()
        raw = await task_queue._redis.get(key)  # type: ignore[attr-defined]
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None


async def _cache_set(key: str, value: dict[str, Any]) -> None:
    try:
        from core.task_queue import task_queue
        await task_queue.connect()
        await task_queue._redis.set(key, json.dumps(value), ex=_CACHE_TTL_SECONDS)  # type: ignore[attr-defined]
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def record_event(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    channel_username: str | None = None,
    account_id: int | None = None,
    campaign_id: int | None = None,
) -> AnalyticsEvent:
    """Persist a single analytics event.

    Supported event_type values:
      comment_sent, reaction_sent, flood_wait, spam_block, account_frozen,
      reply_received, lead_generated, channel_joined, account_banned
    """
    event = AnalyticsEvent(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        event_type=event_type,
        account_id=account_id,
        campaign_id=campaign_id,
        channel_username=channel_username,
        event_data=payload or {},
        created_at=utcnow(),
    )
    session.add(event)
    await session.flush()
    return event


async def get_daily_stats(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Return per-day aggregated stats for the last *days* days.

    Returns a list sorted by date ascending:
      [{"date": "YYYY-MM-DD", "comments": N, "reactions": N, "errors": N}, ...]
    """
    cache_key = f"analytics:daily:{tenant_id}:{workspace_id}:{days}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached.get("rows", [])

    since = utcnow() - timedelta(days=days)
    result = await session.execute(
        select(
            func.date(AnalyticsEvent.created_at).label("day"),
            AnalyticsEvent.event_type,
            func.count(AnalyticsEvent.id).label("cnt"),
        )
        .where(
            AnalyticsEvent.tenant_id == tenant_id,
            AnalyticsEvent.workspace_id == workspace_id,
            AnalyticsEvent.created_at >= since,
        )
        .group_by(func.date(AnalyticsEvent.created_at), AnalyticsEvent.event_type)
        .order_by(func.date(AnalyticsEvent.created_at))
    )

    raw: dict[str, dict[str, int]] = {}
    for row in result.all():
        day_str = str(row.day)
        if day_str not in raw:
            raw[day_str] = {"comments": 0, "reactions": 0, "errors": 0}
        if row.event_type == "comment_sent":
            raw[day_str]["comments"] += row.cnt
        elif row.event_type == "reaction_sent":
            raw[day_str]["reactions"] += row.cnt
        elif row.event_type in ("flood_wait", "spam_block", "account_frozen", "account_banned"):
            raw[day_str]["errors"] += row.cnt

    rows = [{"date": d, **v} for d, v in sorted(raw.items())]
    await _cache_set(cache_key, {"rows": rows})
    return rows


async def get_channel_comparison(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    days: int = 30,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return channel comparison: comments, reactions, CTR, rank.

    CTR = reactions / max(1, comments).
    """
    cache_key = f"analytics:channels:{tenant_id}:{workspace_id}:{days}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached.get("rows", [])

    since = utcnow() - timedelta(days=days)
    result = await session.execute(
        select(
            AnalyticsEvent.channel_username,
            AnalyticsEvent.event_type,
            func.count(AnalyticsEvent.id).label("cnt"),
        )
        .where(
            AnalyticsEvent.tenant_id == tenant_id,
            AnalyticsEvent.workspace_id == workspace_id,
            AnalyticsEvent.channel_username.isnot(None),
            AnalyticsEvent.created_at >= since,
        )
        .group_by(AnalyticsEvent.channel_username, AnalyticsEvent.event_type)
        .limit(10000)
    )

    channel_data: dict[str, dict[str, int]] = {}
    for row in result.all():
        ch = row.channel_username
        if ch not in channel_data:
            channel_data[ch] = {"comments": 0, "reactions": 0}
        if row.event_type == "comment_sent":
            channel_data[ch]["comments"] += row.cnt
        elif row.event_type == "reaction_sent":
            channel_data[ch]["reactions"] += row.cnt

    rows = []
    for ch, stats in channel_data.items():
        comments = stats["comments"]
        reactions = stats["reactions"]
        ctr = round(reactions / max(1, comments), 3)
        rows.append({
            "channel": ch,
            "comments": comments,
            "reactions": reactions,
            "ctr": ctr,
            "total_actions": comments + reactions,
        })

    rows.sort(key=lambda r: r["total_actions"], reverse=True)
    rows = rows[:limit]
    for i, r in enumerate(rows, start=1):
        r["rank"] = i

    await _cache_set(cache_key, {"rows": rows})
    return rows


async def get_heatmap_data(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Return a 7 x 24 heatmap of comment activity.

    Returns a flat list of {"weekday": 0-6, "hour": 0-23, "count": N}
    where weekday 0 = Monday.
    """
    cache_key = f"analytics:heatmap:{tenant_id}:{workspace_id}:{days}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached.get("rows", [])

    since = utcnow() - timedelta(days=days)
    result = await session.execute(
        select(
            func.extract("dow", AnalyticsEvent.created_at).label("dow"),
            func.extract("hour", AnalyticsEvent.created_at).label("hour"),
            func.count(AnalyticsEvent.id).label("cnt"),
        )
        .where(
            AnalyticsEvent.tenant_id == tenant_id,
            AnalyticsEvent.workspace_id == workspace_id,
            AnalyticsEvent.event_type == "comment_sent",
            AnalyticsEvent.created_at >= since,
        )
        .group_by(
            func.extract("dow", AnalyticsEvent.created_at),
            func.extract("hour", AnalyticsEvent.created_at),
        )
    )

    # Initialize full 7x24 grid with zeros
    grid: dict[tuple[int, int], int] = {}
    for row in result.all():
        # PostgreSQL DOW: 0=Sunday … 6=Saturday -> convert to Mon=0
        dow_pg = int(row.dow)
        weekday = (dow_pg - 1) % 7  # Mon=0, …, Sun=6
        hour = int(row.hour)
        grid[(weekday, hour)] = int(row.cnt)

    rows = [
        {"weekday": wd, "hour": h, "count": grid.get((wd, h), 0)}
        for wd in range(7)
        for h in range(24)
    ]
    await _cache_set(cache_key, {"rows": rows})
    return rows


async def get_top_comments(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    days: int = 30,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return top-performing comment events (most reactions in event_data)."""
    since = utcnow() - timedelta(days=days)
    result = await session.execute(
        select(AnalyticsEvent)
        .where(
            AnalyticsEvent.tenant_id == tenant_id,
            AnalyticsEvent.workspace_id == workspace_id,
            AnalyticsEvent.event_type == "comment_sent",
            AnalyticsEvent.created_at >= since,
        )
        .order_by(AnalyticsEvent.created_at.desc())
        .limit(limit * 5)
    )
    events = result.scalars().all()

    rows = []
    for e in events:
        data = e.event_data or {}
        reactions = int(data.get("reactions", 0))
        rows.append({
            "id": e.id,
            "channel": e.channel_username or "",
            "text": str(data.get("text", ""))[:200],
            "reactions": reactions,
            "account_id": e.account_id,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })

    rows.sort(key=lambda r: r["reactions"], reverse=True)
    return rows[:limit]
