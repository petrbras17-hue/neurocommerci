"""
CampaignManager — lifecycle management for neurocommenting campaigns.

Lifecycle: draft -> active -> paused/completed/archived.
Each start creates a CampaignRun and writes to analytics_events.
The background loop is managed as an asyncio.Task held in process memory.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from storage.models import AnalyticsEvent, Campaign, CampaignRun
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow

log = logging.getLogger(__name__)


def _now() -> datetime:
    return utcnow()


def _campaign_to_dict(c: Campaign) -> dict[str, Any]:
    return {
        "id": c.id,
        "tenant_id": c.tenant_id,
        "workspace_id": c.workspace_id,
        "name": c.name,
        "status": c.status,
        "campaign_type": c.campaign_type,
        "account_ids": c.account_ids,
        "channel_database_id": c.channel_database_id,
        "comment_prompt": c.comment_prompt,
        "comment_tone": c.comment_tone,
        "comment_language": c.comment_language,
        "schedule_type": c.schedule_type,
        "schedule_config": c.schedule_config,
        "budget_daily_actions": c.budget_daily_actions,
        "budget_total_actions": c.budget_total_actions,
        "total_actions_performed": c.total_actions_performed,
        "total_comments_sent": c.total_comments_sent,
        "total_reactions_sent": c.total_reactions_sent,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "completed_at": c.completed_at.isoformat() if c.completed_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _run_to_dict(r: CampaignRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "tenant_id": r.tenant_id,
        "campaign_id": r.campaign_id,
        "status": r.status,
        "actions_performed": r.actions_performed,
        "comments_sent": r.comments_sent,
        "reactions_sent": r.reactions_sent,
        "errors": r.errors,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "run_log": r.run_log,
    }


class CampaignManager:
    """
    Central campaign manager.

    All public methods own their sessions and apply RLS context.
    The background loop is intentionally stateless regarding DB sessions:
    it opens a fresh session per tick so that it is not pinned to a
    long-lived transaction.
    """

    def __init__(self) -> None:
        # campaign_id -> asyncio.Task
        self._running_tasks: dict[int, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def list_campaigns(self, tenant_id: int) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                q = (
                    select(Campaign)
                    .where(Campaign.tenant_id == tenant_id)
                    .order_by(Campaign.created_at.desc())
                )
                count_q = select(func.count()).select_from(q.subquery())
                total = (await session.execute(count_q)).scalar_one()
                result = await session.execute(q)
                items = [_campaign_to_dict(c) for c in result.scalars().all()]

        return {"items": items, "total": total}

    async def get_campaign(self, tenant_id: int, campaign_id: int) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                campaign = await self._load(session, tenant_id, campaign_id)
                runs_result = await session.execute(
                    select(CampaignRun)
                    .where(
                        CampaignRun.campaign_id == campaign_id,
                        CampaignRun.tenant_id == tenant_id,
                    )
                    .order_by(CampaignRun.id.desc())
                    .limit(20)
                )
                runs = [_run_to_dict(r) for r in runs_result.scalars().all()]

        data = _campaign_to_dict(campaign)
        data["runs"] = runs
        return data

    async def create_campaign(
        self,
        tenant_id: int,
        workspace_id: int,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                campaign = Campaign(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    name=data["name"],
                    status="draft",
                    campaign_type=data.get("campaign_type", "commenting"),
                    account_ids=data.get("account_ids"),
                    channel_database_id=data.get("channel_database_id"),
                    comment_prompt=data.get("comment_prompt"),
                    comment_tone=data.get("comment_tone"),
                    comment_language=data.get("comment_language", "ru"),
                    schedule_type=data.get("schedule_type", "continuous"),
                    schedule_config=data.get("schedule_config"),
                    budget_daily_actions=int(data.get("budget_daily_actions") or 100),
                    budget_total_actions=data.get("budget_total_actions"),
                )
                session.add(campaign)
                await session.flush()
                result = _campaign_to_dict(campaign)

        log.info(
            "create_campaign tenant=%s campaign_id=%s name=%r",
            tenant_id,
            result["id"],
            result["name"],
        )
        return result

    async def update_campaign(
        self,
        tenant_id: int,
        campaign_id: int,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                campaign = await self._load(session, tenant_id, campaign_id)
                updatable = (
                    "name",
                    "campaign_type",
                    "account_ids",
                    "channel_database_id",
                    "comment_prompt",
                    "comment_tone",
                    "comment_language",
                    "schedule_type",
                    "schedule_config",
                    "budget_daily_actions",
                    "budget_total_actions",
                )
                for field in updatable:
                    if field in data:
                        setattr(campaign, field, data[field])
                campaign.updated_at = _now()
                await session.flush()
                result = _campaign_to_dict(campaign)

        log.info("update_campaign tenant=%s campaign_id=%s", tenant_id, campaign_id)
        return result

    async def delete_campaign(self, tenant_id: int, campaign_id: int) -> bool:
        self._cancel_task(campaign_id)
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(Campaign).where(
                        Campaign.id == campaign_id,
                        Campaign.tenant_id == tenant_id,
                    )
                )
                campaign = result.scalar_one_or_none()
                if campaign is None:
                    return False
                await session.delete(campaign)

        log.info("delete_campaign tenant=%s campaign_id=%s", tenant_id, campaign_id)
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_campaign(self, tenant_id: int, campaign_id: int) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                campaign = await self._load(session, tenant_id, campaign_id)

                if campaign.status == "active":
                    log.warning(
                        "start_campaign: campaign %s already active", campaign_id
                    )
                    return _campaign_to_dict(campaign)

                run = CampaignRun(
                    tenant_id=tenant_id,
                    campaign_id=campaign_id,
                    status="running",
                    started_at=_now(),
                )
                session.add(run)

                campaign.status = "active"
                campaign.started_at = campaign.started_at or _now()
                campaign.updated_at = _now()
                await session.flush()

                event = AnalyticsEvent(
                    tenant_id=tenant_id,
                    workspace_id=campaign.workspace_id,
                    event_type="campaign_started",
                    campaign_id=campaign_id,
                )
                session.add(event)
                result = _campaign_to_dict(campaign)

        if campaign_id not in self._running_tasks:
            task = asyncio.create_task(
                self._campaign_loop(campaign_id, tenant_id),
                name=f"campaign_{campaign_id}",
            )
            self._running_tasks[campaign_id] = task
            log.info("start_campaign: background loop started for campaign %s", campaign_id)

        return result

    async def pause_campaign(self, tenant_id: int, campaign_id: int) -> dict[str, Any]:
        self._cancel_task(campaign_id)
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                campaign = await self._load(session, tenant_id, campaign_id)
                campaign.status = "paused"
                campaign.updated_at = _now()
                await session.flush()
                session.add(
                    AnalyticsEvent(
                        tenant_id=tenant_id,
                        workspace_id=campaign.workspace_id,
                        event_type="campaign_paused",
                        campaign_id=campaign_id,
                    )
                )
                result = _campaign_to_dict(campaign)

        log.info("pause_campaign tenant=%s campaign_id=%s", tenant_id, campaign_id)
        return result

    async def resume_campaign(self, tenant_id: int, campaign_id: int) -> dict[str, Any]:
        return await self.start_campaign(tenant_id, campaign_id)

    async def stop_campaign(self, tenant_id: int, campaign_id: int) -> dict[str, Any]:
        self._cancel_task(campaign_id)
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                campaign = await self._load(session, tenant_id, campaign_id)

                run_result = await session.execute(
                    select(CampaignRun)
                    .where(
                        CampaignRun.campaign_id == campaign_id,
                        CampaignRun.tenant_id == tenant_id,
                        CampaignRun.status == "running",
                    )
                    .order_by(CampaignRun.id.desc())
                    .limit(1)
                )
                run = run_result.scalar_one_or_none()
                if run:
                    run.status = "completed"
                    run.completed_at = _now()

                campaign.status = "completed"
                campaign.completed_at = _now()
                campaign.updated_at = _now()
                await session.flush()
                session.add(
                    AnalyticsEvent(
                        tenant_id=tenant_id,
                        workspace_id=campaign.workspace_id,
                        event_type="campaign_stopped",
                        campaign_id=campaign_id,
                    )
                )
                result = _campaign_to_dict(campaign)

        log.info("stop_campaign tenant=%s campaign_id=%s", tenant_id, campaign_id)
        return result

    # ------------------------------------------------------------------
    # Runs and analytics
    # ------------------------------------------------------------------

    async def list_runs(self, tenant_id: int, campaign_id: int) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                q = (
                    select(CampaignRun)
                    .where(
                        CampaignRun.campaign_id == campaign_id,
                        CampaignRun.tenant_id == tenant_id,
                    )
                    .order_by(CampaignRun.id.desc())
                )
                count_q = select(func.count()).select_from(q.subquery())
                total = (await session.execute(count_q)).scalar_one()
                result = await session.execute(q)
                items = [_run_to_dict(r) for r in result.scalars().all()]

        return {"items": items, "total": total}

    async def get_campaign_analytics(
        self, tenant_id: int, campaign_id: int
    ) -> dict[str, Any]:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)

                event_result = await session.execute(
                    select(
                        AnalyticsEvent.event_type,
                        func.count(AnalyticsEvent.id).label("cnt"),
                    )
                    .where(
                        AnalyticsEvent.tenant_id == tenant_id,
                        AnalyticsEvent.campaign_id == campaign_id,
                    )
                    .group_by(AnalyticsEvent.event_type)
                )
                event_counts: dict[str, int] = {
                    row.event_type: row.cnt for row in event_result.all()
                }

                runs_result = await session.execute(
                    select(CampaignRun)
                    .where(
                        CampaignRun.campaign_id == campaign_id,
                        CampaignRun.tenant_id == tenant_id,
                    )
                    .order_by(CampaignRun.id.desc())
                    .limit(20)
                )
                runs = runs_result.scalars().all()

                camp_result = await session.execute(
                    select(Campaign).where(
                        Campaign.id == campaign_id,
                        Campaign.tenant_id == tenant_id,
                    )
                )
                campaign = camp_result.scalar_one_or_none()

        return {
            "campaign_id": campaign_id,
            "campaign_name": campaign.name if campaign else None,
            "status": campaign.status if campaign else None,
            "event_counts": event_counts,
            "total_comments": campaign.total_comments_sent if campaign else 0,
            "total_reactions": campaign.total_reactions_sent if campaign else 0,
            "total_actions": campaign.total_actions_performed if campaign else 0,
            "runs_count": len(runs),
            "last_run": _run_to_dict(runs[0]) if runs else None,
        }

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def _campaign_loop(self, campaign_id: int, tenant_id: int) -> None:
        """
        Background loop for a running campaign.

        Opens a fresh session per tick to avoid long-lived transactions.
        Real Telegram execution is delegated to workers via the task queue
        (future sprint). Here we only maintain counters and analytics events.
        """
        log.info("_campaign_loop started campaign=%s tenant=%s", campaign_id, tenant_id)
        try:
            while True:
                async with async_session() as session:
                    async with session.begin():
                        await apply_session_rls_context(session, tenant_id=tenant_id)
                        result = await session.execute(
                            select(Campaign).where(
                                Campaign.id == campaign_id,
                                Campaign.tenant_id == tenant_id,
                            )
                        )
                        fresh = result.scalar_one_or_none()

                        if fresh is None or fresh.status != "active":
                            log.info(
                                "_campaign_loop: campaign %s no longer active, stopping",
                                campaign_id,
                            )
                            return

                        if (
                            fresh.budget_total_actions
                            and (fresh.total_actions_performed or 0)
                            >= fresh.budget_total_actions
                        ):
                            log.info(
                                "_campaign_loop: campaign %s budget exhausted", campaign_id
                            )
                            fresh.status = "completed"
                            fresh.completed_at = _now()
                            fresh.updated_at = _now()
                            return

                        if not self._is_within_schedule(fresh):
                            await asyncio.sleep(300)
                            continue

                        account_ids: list[int] = fresh.account_ids or []
                        if not account_ids:
                            await asyncio.sleep(120)
                            continue

                        picked = random.choice(account_ids)
                        action = self._pick_action(fresh.campaign_type)

                        fresh.total_actions_performed = (
                            fresh.total_actions_performed or 0
                        ) + 1
                        if action == "comment":
                            fresh.total_comments_sent = (
                                fresh.total_comments_sent or 0
                            ) + 1
                        elif action == "reaction":
                            fresh.total_reactions_sent = (
                                fresh.total_reactions_sent or 0
                            ) + 1
                        fresh.updated_at = _now()

                        session.add(
                            AnalyticsEvent(
                                tenant_id=tenant_id,
                                workspace_id=fresh.workspace_id,
                                event_type=f"{action}_sent",
                                account_id=picked,
                                campaign_id=campaign_id,
                                event_data={"action": action, "loop_tick": True},
                            )
                        )

                delay = self._compute_delay_seconds(fresh.budget_daily_actions or 100)
                await asyncio.sleep(delay)

        except asyncio.CancelledError:
            log.info("_campaign_loop cancelled campaign=%s", campaign_id)
        except Exception as exc:
            log.error("_campaign_loop error campaign=%s: %s", campaign_id, exc)
        finally:
            self._running_tasks.pop(campaign_id, None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _load(session, tenant_id: int, campaign_id: int) -> Campaign:
        result = await session.execute(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_id,
            )
        )
        campaign = result.scalar_one_or_none()
        if campaign is None:
            raise ValueError(
                f"Campaign {campaign_id} not found for tenant {tenant_id}"
            )
        return campaign

    def _cancel_task(self, campaign_id: int) -> None:
        task = self._running_tasks.pop(campaign_id, None)
        if task and not task.done():
            task.cancel()

    @staticmethod
    def _is_within_schedule(campaign: Campaign) -> bool:
        if campaign.schedule_type in ("continuous", "burst"):
            return True
        if campaign.schedule_type == "scheduled":
            config = campaign.schedule_config or {}
            now = _now()
            days = config.get("days_of_week")
            if days and now.weekday() not in days:
                return False
            start = config.get("start_time")
            end = config.get("end_time")
            if start and end:
                current_hm = now.strftime("%H:%M")
                if not (start <= current_hm <= end):
                    return False
        return True

    @staticmethod
    def _pick_action(campaign_type: str) -> str:
        if campaign_type == "commenting":
            return "comment"
        if campaign_type == "reactions":
            return "reaction"
        if campaign_type == "chatting":
            return "chat"
        return random.choice(["comment", "reaction"])

    @staticmethod
    def _compute_delay_seconds(daily_budget: int) -> float:
        return max(10.0, (16 * 3600) / max(1, daily_budget))
