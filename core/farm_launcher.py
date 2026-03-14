"""
Farm Launch Orchestrator — gradual scaling with health gating.

Manages launch plans that control how many actions a farm/account can perform
per day, ramping up over 30 days using different scaling curves.

Usage:
    from core.farm_launcher import (
        create_launch_plan, get_current_limit, advance_day,
        gaussian_delay, add_weekly_variation, add_active_hours_jitter,
    )
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import FarmLaunchPlan, ScalingHistory
from storage.sqlite_db import async_session, apply_session_rls_context
from utils.helpers import utcnow
from utils.logger import log


# ---------------------------------------------------------------------------
# Weekday multipliers for add_weekly_variation
# Monday=0 .. Sunday=6
# ---------------------------------------------------------------------------

_WEEKDAY_MULTIPLIERS: Dict[int, float] = {
    0: 1.3,   # Monday
    1: 1.1,   # Tuesday
    2: 1.0,   # Wednesday
    3: 0.9,   # Thursday
    4: 0.85,  # Friday
    5: 0.8,   # Saturday
    6: 1.2,   # Sunday
}


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------


async def create_launch_plan(
    workspace_id: int,
    farm_id: int,
    name: Optional[str] = None,
    scaling_curve: str = "gradual",
    *,
    custom_curve: Optional[List[Dict[str, Any]]] = None,
    day_1_limit: int = 2,
    day_3_limit: int = 5,
    day_7_limit: int = 10,
    day_14_limit: int = 20,
    day_30_limit: int = -1,
    health_gate_threshold: int = 40,
    auto_reduce_factor: float = 0.5,
) -> FarmLaunchPlan:
    """Create a new launch plan for a farm."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            plan = FarmLaunchPlan(
                workspace_id=workspace_id,
                farm_id=farm_id,
                name=name,
                scaling_curve=scaling_curve,
                custom_curve=custom_curve,
                day_1_limit=day_1_limit,
                day_3_limit=day_3_limit,
                day_7_limit=day_7_limit,
                day_14_limit=day_14_limit,
                day_30_limit=day_30_limit,
                current_day=0,
                is_active=True,
                health_gate_threshold=health_gate_threshold,
                auto_reduce_factor=auto_reduce_factor,
                started_at=utcnow(),
            )
            session.add(plan)
            await session.flush()
            plan_id = plan.id
    # Re-fetch to get server defaults
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            result = await session.execute(
                select(FarmLaunchPlan).where(FarmLaunchPlan.id == plan_id)
            )
            return result.scalar_one()


async def get_launch_plan(
    workspace_id: int,
    plan_id: int,
) -> Optional[FarmLaunchPlan]:
    """Get a single launch plan by ID (workspace-scoped via RLS)."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            result = await session.execute(
                select(FarmLaunchPlan).where(FarmLaunchPlan.id == plan_id)
            )
            return result.scalar_one_or_none()


async def list_launch_plans(workspace_id: int) -> List[FarmLaunchPlan]:
    """List all launch plans for a workspace."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            result = await session.execute(
                select(FarmLaunchPlan).order_by(FarmLaunchPlan.id.desc())
            )
            return list(result.scalars().all())


async def delete_launch_plan(workspace_id: int, plan_id: int) -> bool:
    """Delete a launch plan. Returns True if deleted."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            result = await session.execute(
                delete(FarmLaunchPlan).where(FarmLaunchPlan.id == plan_id)
            )
            return result.rowcount > 0


# ---------------------------------------------------------------------------
# Scaling curve math
# ---------------------------------------------------------------------------


def _gradual_limit(plan: FarmLaunchPlan, day: int) -> int:
    """Gradual (step-function) curve: day 1->2, day 3->5, day 7->10, day 14->20, day 30->unlimited."""
    if day >= 30:
        return plan.day_30_limit if plan.day_30_limit != -1 else 999999
    if day >= 14:
        return plan.day_14_limit
    if day >= 7:
        return plan.day_7_limit
    if day >= 3:
        return plan.day_3_limit
    return plan.day_1_limit


def _linear_limit(plan: FarmLaunchPlan, day: int) -> int:
    """Linear interpolation from day_1_limit to day_30_limit over 30 days."""
    d30 = plan.day_30_limit if plan.day_30_limit != -1 else 999999
    if day >= 30:
        return d30
    if day <= 0:
        return plan.day_1_limit
    raw = plan.day_1_limit + (d30 - plan.day_1_limit) * day / 30.0
    return max(1, int(raw))


def _exponential_limit(plan: FarmLaunchPlan, day: int) -> int:
    """Exponential curve: day_1_limit * 2^(day/7), capped at day_30_limit."""
    if day <= 0:
        return plan.day_1_limit
    raw = plan.day_1_limit * (2.0 ** (day / 7.0))
    d30 = plan.day_30_limit if plan.day_30_limit != -1 else 999999
    if day >= 30:
        return d30
    return max(1, min(int(raw), d30))


def _custom_limit(plan: FarmLaunchPlan, day: int) -> int:
    """Lookup from custom_curve JSON: [{day: N, max_comments: M}, ...]."""
    curve = plan.custom_curve or []
    if not curve:
        return plan.day_1_limit
    # Sort by day ascending
    sorted_curve = sorted(curve, key=lambda x: x.get("day", 0))
    result = sorted_curve[0].get("max_comments", plan.day_1_limit)
    for entry in sorted_curve:
        if day >= entry.get("day", 0):
            result = entry.get("max_comments", result)
        else:
            break
    if result == -1:
        return 999999
    return max(1, result)


_CURVE_MAP = {
    "gradual": _gradual_limit,
    "linear": _linear_limit,
    "exponential": _exponential_limit,
    "custom": _custom_limit,
}


def get_current_limit(
    plan: FarmLaunchPlan,
    health_score: Optional[int] = None,
) -> int:
    """
    Calculate current max actions based on day + curve + health gate.

    If health_score is provided and below health_gate_threshold,
    the limit is multiplied by auto_reduce_factor.
    """
    day = plan.current_day or 0
    curve_fn = _CURVE_MAP.get(plan.scaling_curve, _gradual_limit)
    limit = curve_fn(plan, day)

    # Health gating
    threshold = plan.health_gate_threshold if plan.health_gate_threshold is not None else 40
    factor = plan.auto_reduce_factor if plan.auto_reduce_factor is not None else 0.5
    if health_score is not None and health_score < threshold:
        limit = max(1, int(limit * factor))

    return limit


async def advance_day(workspace_id: int, plan_id: int) -> Optional[FarmLaunchPlan]:
    """Increment current_day by 1. Returns updated plan or None."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            result = await session.execute(
                select(FarmLaunchPlan).where(FarmLaunchPlan.id == plan_id)
            )
            plan = result.scalar_one_or_none()
            if plan is None:
                return None
            plan.current_day = (plan.current_day or 0) + 1
            await session.flush()
            return plan


async def record_scaling_event(
    workspace_id: int,
    farm_id: int,
    account_id: Optional[int],
    day_number: int,
    max_allowed: int,
    actual_performed: int,
    health_gated: bool = False,
    antifraud_gated: bool = False,
) -> ScalingHistory:
    """Record a scaling event in the history table."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            entry = ScalingHistory(
                workspace_id=workspace_id,
                farm_id=farm_id,
                account_id=account_id,
                day_number=day_number,
                max_allowed=max_allowed,
                actual_performed=actual_performed,
                was_health_gated=health_gated,
                was_antifraud_gated=antifraud_gated,
            )
            session.add(entry)
            await session.flush()
            return entry


async def get_scaling_history(
    workspace_id: int,
    plan_id: Optional[int] = None,
    farm_id: Optional[int] = None,
    limit: int = 100,
) -> List[ScalingHistory]:
    """Retrieve scaling history for a farm."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            q = select(ScalingHistory)
            if farm_id is not None:
                q = q.where(ScalingHistory.farm_id == farm_id)
            q = q.order_by(ScalingHistory.id.desc()).limit(limit)
            result = await session.execute(q)
            return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Human-like timing helpers
# ---------------------------------------------------------------------------


def gaussian_delay(mean_sec: float, std_dev_sec: float) -> float:
    """
    Return a Gaussian-distributed delay in seconds.

    More human-like than uniform random: clusters around the mean
    with natural variation. Always returns at least 1.0 second.
    """
    return max(1.0, random.gauss(mean_sec, std_dev_sec))


def add_weekly_variation(base_delay: float, weekday: int) -> float:
    """
    Multiply base_delay by a weekday-specific factor.

    Monday (0) has the highest multiplier (users are cautious after weekend),
    Friday-Saturday (4-5) have the lowest (higher engagement).
    """
    multiplier = _WEEKDAY_MULTIPLIERS.get(weekday, 1.0)
    return base_delay * multiplier


def add_active_hours_jitter(start_hour: int, end_hour: int) -> tuple:
    """
    Add +/-30min random jitter to active hours window.

    Returns (jittered_start, jittered_end) clamped to 0-23.
    """
    jitter_minutes = random.randint(-30, 30)
    jittered_start = start_hour + jitter_minutes / 60.0
    jittered_end = end_hour + random.randint(-30, 30) / 60.0
    return (
        max(0, min(23, int(jittered_start))),
        max(0, min(23, int(jittered_end))),
    )
