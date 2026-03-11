"""
AnalyticsService — event logging and dashboard aggregation.

Reads from analytics_events and builds:
  - operator dashboard with daily breakdown
  - per-campaign analytics
  - ROI summary
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from storage.models import Account, AnalyticsEvent, Campaign
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow

log = logging.getLogger(__name__)


def _days_ago(n: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=n)


class AnalyticsService:
    """
    Tenant-safe analytics service.

    All public methods own their sessions and apply RLS context.
    """

    # ------------------------------------------------------------------
    # Event logging
    # ------------------------------------------------------------------

    async def log_event(
        self,
        tenant_id: int,
        workspace_id: int,
        event_type: str,
        account_id: int | None = None,
        campaign_id: int | None = None,
        channel_username: str | None = None,
        event_data: dict | None = None,
    ) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                event = AnalyticsEvent(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    event_type=event_type,
                    account_id=account_id,
                    campaign_id=campaign_id,
                    channel_username=channel_username,
                    event_data=event_data,
                )
                session.add(event)
                await session.flush()
                result = {
                    "id": event.id,
                    "tenant_id": event.tenant_id,
                    "workspace_id": event.workspace_id,
                    "event_type": event.event_type,
                    "account_id": event.account_id,
                    "campaign_id": event.campaign_id,
                    "channel_username": event.channel_username,
                    "event_data": event.event_data,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }

        log.debug(
            "log_event tenant=%s event_type=%s account_id=%s",
            tenant_id,
            event_type,
            account_id,
        )
        return result

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    async def get_dashboard(self, tenant_id: int, days: int = 7) -> dict[str, Any]:
        since = _days_ago(days)

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                base_filter = [
                    AnalyticsEvent.tenant_id == tenant_id,
                    AnalyticsEvent.created_at >= since,
                ]

                # --- Totals ---
                totals_result = await session.execute(
                    select(
                        AnalyticsEvent.event_type,
                        func.count(AnalyticsEvent.id).label("cnt"),
                    )
                    .where(
                        *base_filter,
                        AnalyticsEvent.event_type.in_(
                            [
                                "comment_sent",
                                "reaction_sent",
                                "flood_wait",
                                "spam_block",
                            ]
                        ),
                    )
                    .group_by(AnalyticsEvent.event_type)
                )
                totals: dict[str, int] = {
                    row.event_type: row.cnt for row in totals_result.all()
                }

                # --- Daily breakdown ---
                daily_result = await session.execute(
                    select(
                        func.date(AnalyticsEvent.created_at).label("day"),
                        AnalyticsEvent.event_type,
                        func.count(AnalyticsEvent.id).label("cnt"),
                    )
                    .where(*base_filter)
                    .group_by(
                        func.date(AnalyticsEvent.created_at),
                        AnalyticsEvent.event_type,
                    )
                    .order_by(func.date(AnalyticsEvent.created_at))
                )

                daily_raw: dict[str, dict[str, int]] = {}
                for row in daily_result.all():
                    day_str = str(row.day)
                    if day_str not in daily_raw:
                        daily_raw[day_str] = {
                            "comments": 0,
                            "reactions": 0,
                            "errors": 0,
                        }
                    if row.event_type == "comment_sent":
                        daily_raw[day_str]["comments"] += row.cnt
                    elif row.event_type == "reaction_sent":
                        daily_raw[day_str]["reactions"] += row.cnt
                    elif row.event_type in ("flood_wait", "spam_block", "account_frozen"):
                        daily_raw[day_str]["errors"] += row.cnt

                daily_breakdown = [
                    {"date": d, **v} for d, v in sorted(daily_raw.items())
                ]

                # --- Top channels ---
                chan_result = await session.execute(
                    select(
                        AnalyticsEvent.channel_username,
                        func.count(AnalyticsEvent.id).label("total_actions"),
                    )
                    .where(
                        *base_filter,
                        AnalyticsEvent.channel_username.isnot(None),
                        AnalyticsEvent.event_type.in_(
                            ["comment_sent", "reaction_sent"]
                        ),
                    )
                    .group_by(AnalyticsEvent.channel_username)
                    .order_by(func.count(AnalyticsEvent.id).desc())
                    .limit(10)
                )
                top_channels = [
                    {"channel": row.channel_username, "actions": row.total_actions}
                    for row in chan_result.all()
                ]

                # --- Account activity ---
                acc_result = await session.execute(
                    select(
                        AnalyticsEvent.account_id,
                        func.count(AnalyticsEvent.id).label("total_actions"),
                    )
                    .where(
                        *base_filter,
                        AnalyticsEvent.account_id.isnot(None),
                    )
                    .group_by(AnalyticsEvent.account_id)
                    .order_by(func.count(AnalyticsEvent.id).desc())
                    .limit(20)
                )
                account_activity = [
                    {"account_id": row.account_id, "actions": row.total_actions}
                    for row in acc_result.all()
                ]

        return {
            "period_days": days,
            "total_comments": totals.get("comment_sent", 0),
            "total_reactions": totals.get("reaction_sent", 0),
            "total_flood_waits": totals.get("flood_wait", 0),
            "total_spam_blocks": totals.get("spam_block", 0),
            "daily_breakdown": daily_breakdown,
            "top_channels": top_channels,
            "account_activity": account_activity,
        }

    # ------------------------------------------------------------------
    # ROI
    # ------------------------------------------------------------------

    async def get_roi(self, tenant_id: int) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                since = _days_ago(30)

                total_result = await session.execute(
                    select(func.count(AnalyticsEvent.id)).where(
                        AnalyticsEvent.tenant_id == tenant_id,
                        AnalyticsEvent.created_at >= since,
                        AnalyticsEvent.event_type.in_(
                            ["comment_sent", "reaction_sent"]
                        ),
                    )
                )
                total_actions = total_result.scalar_one() or 0

                # Count distinct active accounts that fired events
                acc_result = await session.execute(
                    select(func.count(func.distinct(AnalyticsEvent.account_id))).where(
                        AnalyticsEvent.tenant_id == tenant_id,
                        AnalyticsEvent.created_at >= since,
                        AnalyticsEvent.account_id.isnot(None),
                    )
                )
                total_accounts = acc_result.scalar_one() or 0

                # Active campaigns
                camp_result = await session.execute(
                    select(
                        Campaign.id,
                        Campaign.name,
                        Campaign.status,
                        Campaign.total_actions_performed,
                        Campaign.total_comments_sent,
                        Campaign.total_reactions_sent,
                    )
                    .where(
                        Campaign.tenant_id == tenant_id,
                        Campaign.status == "active",
                    )
                    .order_by(Campaign.started_at.desc())
                )
                active_campaigns = [
                    {
                        "id": row.id,
                        "name": row.name,
                        "status": row.status,
                        "total_actions_performed": row.total_actions_performed,
                        "total_comments_sent": row.total_comments_sent,
                        "total_reactions_sent": row.total_reactions_sent,
                    }
                    for row in camp_result.all()
                ]

        avg_per_account = (
            round(total_actions / total_accounts, 1) if total_accounts else 0.0
        )

        return {
            "total_actions": total_actions,
            "total_accounts": total_accounts,
            "avg_actions_per_account": avg_per_account,
            "active_campaigns": len(active_campaigns),
            "campaign_summary": active_campaigns,
        }
