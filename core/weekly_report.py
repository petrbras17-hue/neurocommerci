"""Weekly marketing report generator.

Generates natural language weekly reports using route_ai_task("weekly_marketing_report"),
saves them to the weekly_reports table, and optionally delivers them via Telegram digest.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import WeeklyReport
from utils.helpers import utcnow

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _week_bounds(reference: Any = None) -> tuple[str, str]:
    """Return (week_start, week_end) strings for the week containing *reference*.

    Defaults to last completed week (Mon-Sun) relative to utcnow().
    """
    from datetime import date as _date

    if reference is None:
        today = utcnow().date()
        # Last Monday
        monday = today - timedelta(days=today.weekday() + 7)
    else:
        monday = reference

    sunday = monday + timedelta(days=6)
    return str(monday), str(sunday)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_weekly_report(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    send_telegram: bool = True,
    week_start: str | None = None,
    week_end: str | None = None,
) -> WeeklyReport:
    """Generate and persist a weekly report for *tenant_id*.

    Steps:
    1. Collect metrics snapshot from analytics_events.
    2. Call route_ai_task("weekly_marketing_report") to generate text.
    3. Persist to weekly_reports.
    4. Optionally send via Telegram digest.

    Returns the saved WeeklyReport ORM object.
    """
    from core.analytics_pipeline import get_channel_comparison, get_daily_stats
    from core.ai_router import route_ai_task

    if week_start is None or week_end is None:
        week_start, week_end = _week_bounds()

    # Gather metrics
    daily_stats = await get_daily_stats(session, tenant_id=tenant_id, workspace_id=workspace_id, days=7)
    channel_stats = await get_channel_comparison(session, tenant_id=tenant_id, workspace_id=workspace_id, days=7, limit=5)

    total_comments = sum(d["comments"] for d in daily_stats)
    total_reactions = sum(d["reactions"] for d in daily_stats)
    total_errors = sum(d["errors"] for d in daily_stats)
    top_channels = [c["channel"] for c in channel_stats[:3]]

    metrics_snapshot: dict[str, Any] = {
        "week_start": week_start,
        "week_end": week_end,
        "total_comments": total_comments,
        "total_reactions": total_reactions,
        "total_errors": total_errors,
        "top_channels": top_channels,
        "daily_breakdown": daily_stats,
        "channel_comparison": channel_stats,
    }

    # Build AI prompt
    prompt_data = {
        "task": "Generate a concise weekly Telegram growth marketing report in Russian.",
        "metrics": metrics_snapshot,
        "instructions": (
            "Write 3-5 paragraphs. Include: key achievements, engagement metrics, "
            "top-performing channels, problem areas, and 2-3 actionable recommendations. "
            "Keep it positive but realistic. Use plain text without markdown."
        ),
    }

    report_text = "(отчёт не сгенерирован)"
    try:
        result = await route_ai_task(
            session=session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            surface="weekly_report",
            task_type="weekly_marketing_report",
            prompt=json.dumps(prompt_data, ensure_ascii=False),
        )
        if result.ok and result.parsed:
            report_text = str(
                result.parsed.get("report")
                or result.parsed.get("text")
                or result.parsed.get("content")
                or json.dumps(result.parsed, ensure_ascii=False)
            )
    except Exception as exc:
        log.warning("weekly_report ai call failed: %s", exc)

    now = utcnow()
    report = WeeklyReport(
        tenant_id=tenant_id,
        week_start=week_start,
        week_end=week_end,
        report_text=report_text,
        metrics_snapshot=metrics_snapshot,
        generated_at=now,
        created_at=now,
    )
    session.add(report)
    await session.flush()

    if send_telegram:
        try:
            from core.digest_service import digest_configured, send_digest_text
            if digest_configured():
                digest_text = (
                    f"<b>Еженедельный отчёт {week_start} — {week_end}</b>\n\n"
                    f"{report_text}\n\n"
                    f"<i>Комментариев: {total_comments} | Реакций: {total_reactions}</i>"
                )
                await send_digest_text(digest_text)
                report.sent_at = utcnow()
        except Exception as exc:
            log.warning("weekly_report telegram delivery failed: %s", exc)

    return report


async def list_weekly_reports(
    session: AsyncSession,
    *,
    tenant_id: int,
    limit: int = 10,
    offset: int = 0,
) -> list[WeeklyReport]:
    """Return paginated list of weekly reports for *tenant_id*, newest first."""
    result = await session.execute(
        select(WeeklyReport)
        .where(WeeklyReport.tenant_id == tenant_id)
        .order_by(WeeklyReport.week_start.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def get_weekly_report(
    session: AsyncSession,
    *,
    tenant_id: int,
    report_id: int,
) -> WeeklyReport | None:
    """Fetch a single weekly report, tenant-scoped."""
    result = await session.execute(
        select(WeeklyReport)
        .where(WeeklyReport.tenant_id == tenant_id, WeeklyReport.id == report_id)
    )
    return result.scalar_one_or_none()
