"""
Monitoring Service — real-time account status tracking + module throughput stats.

Public API
----------
update_account_status(...)       — upsert live status for an account
clear_account_status(...)        — set account module to 'free'
get_all_account_statuses(...)    — list all live account statuses
record_throughput(...)           — insert/update throughput for current 5-min period
get_throughput_stats(...)        — get per-module stats for last N hours
get_dashboard_summary(...)       — aggregated dashboard data
broadcast_status_change(...)     — publish status change to Redis pub/sub
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from storage.models import AccountStatusLive, ModuleThroughput
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log


# ---------------------------------------------------------------------------
# Account Status
# ---------------------------------------------------------------------------


async def update_account_status(
    workspace_id: int,
    account_id: int,
    phone: Optional[str],
    module: str,
    action: str,
) -> dict:
    """Upsert live status for an account."""
    now = utcnow()
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)

            # Try to find existing row
            existing = (await session.execute(
                select(AccountStatusLive)
                .where(AccountStatusLive.workspace_id == workspace_id)
                .where(AccountStatusLive.account_id == account_id)
            )).scalar_one_or_none()

            if existing:
                existing.current_module = module
                existing.current_action = action
                existing.last_heartbeat_at = now
                if existing.account_phone is None and phone:
                    existing.account_phone = phone
                if module != "free":
                    if existing.started_at is None:
                        existing.started_at = now
                else:
                    existing.started_at = None
                await session.flush()
                result = _serialize_status(existing)
            else:
                entry = AccountStatusLive(
                    workspace_id=workspace_id,
                    account_id=account_id,
                    account_phone=phone,
                    current_module=module,
                    current_action=action,
                    started_at=now if module != "free" else None,
                    last_heartbeat_at=now,
                )
                session.add(entry)
                await session.flush()
                result = _serialize_status(entry)

    # Best-effort broadcast
    await broadcast_status_change(workspace_id, account_id, module, action)
    return result


async def clear_account_status(workspace_id: int, account_id: int) -> None:
    """Set account module to 'free' and clear action."""
    await update_account_status(workspace_id, account_id, None, "free", "idle")


async def get_all_account_statuses(workspace_id: int) -> List[dict]:
    """Get all live account statuses for a workspace."""
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            rows = (await session.execute(
                select(AccountStatusLive)
                .where(AccountStatusLive.workspace_id == workspace_id)
                .order_by(AccountStatusLive.last_heartbeat_at.desc().nullslast())
            )).scalars().all()
            return [_serialize_status(r) for r in rows]


# ---------------------------------------------------------------------------
# Throughput
# ---------------------------------------------------------------------------


def _current_period_start() -> datetime:
    """Get the start of the current 5-minute period."""
    now = datetime.now(timezone.utc)
    minute = (now.minute // 5) * 5
    return now.replace(minute=minute, second=0, microsecond=0)


async def record_throughput(
    workspace_id: int,
    module: str,
    actions: int = 0,
    errors: int = 0,
    avg_latency: Optional[int] = None,
) -> None:
    """Insert or update throughput stats for the current 5-minute period."""
    period = _current_period_start()

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)

            existing = (await session.execute(
                select(ModuleThroughput)
                .where(ModuleThroughput.workspace_id == workspace_id)
                .where(ModuleThroughput.module == module)
                .where(ModuleThroughput.period_start == period)
            )).scalar_one_or_none()

            if existing:
                existing.actions_count = (existing.actions_count or 0) + actions
                existing.errors_count = (existing.errors_count or 0) + errors
                if avg_latency is not None:
                    # Running average
                    old_avg = existing.avg_latency_ms or avg_latency
                    old_count = max((existing.actions_count or 1) - actions, 1)
                    existing.avg_latency_ms = int(
                        (old_avg * old_count + avg_latency * actions) / (old_count + actions)
                    )
            else:
                entry = ModuleThroughput(
                    workspace_id=workspace_id,
                    module=module,
                    period_start=period,
                    actions_count=actions,
                    errors_count=errors,
                    avg_latency_ms=avg_latency,
                )
                session.add(entry)


async def get_throughput_stats(workspace_id: int, hours: int = 1) -> Dict[str, Any]:
    """Get per-module throughput stats for the last N hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            rows = (await session.execute(
                select(ModuleThroughput)
                .where(ModuleThroughput.workspace_id == workspace_id)
                .where(ModuleThroughput.period_start >= cutoff)
                .order_by(ModuleThroughput.period_start.desc())
            )).scalars().all()

    # Aggregate by module
    modules: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        mod = row.module or "unknown"
        if mod not in modules:
            modules[mod] = {
                "module": mod,
                "total_actions": 0,
                "total_errors": 0,
                "avg_latency_ms": None,
                "periods": [],
            }
        modules[mod]["total_actions"] += row.actions_count or 0
        modules[mod]["total_errors"] += row.errors_count or 0
        modules[mod]["periods"].append({
            "period_start": row.period_start.isoformat() if row.period_start else None,
            "actions_count": row.actions_count,
            "errors_count": row.errors_count,
            "avg_latency_ms": row.avg_latency_ms,
        })

    # Calculate avg latency per module
    for mod_data in modules.values():
        latencies = [p["avg_latency_ms"] for p in mod_data["periods"] if p["avg_latency_ms"] is not None]
        if latencies:
            mod_data["avg_latency_ms"] = int(sum(latencies) / len(latencies))

    return {
        "hours": hours,
        "modules": list(modules.values()),
    }


# ---------------------------------------------------------------------------
# Dashboard Summary
# ---------------------------------------------------------------------------


async def get_dashboard_summary(workspace_id: int) -> Dict[str, Any]:
    """Get full dashboard summary: active accounts, throughput, error rate."""
    statuses = await get_all_account_statuses(workspace_id)
    throughput = await get_throughput_stats(workspace_id, hours=1)

    # Active accounts by module
    module_counts: Dict[str, int] = {}
    active_count = 0
    for s in statuses:
        mod = s.get("current_module", "free")
        module_counts[mod] = module_counts.get(mod, 0) + 1
        if mod != "free":
            active_count += 1

    # Total actions and errors in last hour
    total_actions = sum(m["total_actions"] for m in throughput.get("modules", []))
    total_errors = sum(m["total_errors"] for m in throughput.get("modules", []))
    error_rate = (total_errors / total_actions * 100) if total_actions > 0 else 0.0

    # Module breakdown
    module_breakdown = []
    for m in throughput.get("modules", []):
        module_breakdown.append({
            "module": m["module"],
            "actions": m["total_actions"],
            "errors": m["total_errors"],
            "avg_latency_ms": m["avg_latency_ms"],
            "active_accounts": module_counts.get(m["module"], 0),
        })

    return {
        "total_accounts": len(statuses),
        "active_accounts": active_count,
        "module_counts": module_counts,
        "total_actions_1h": total_actions,
        "total_errors_1h": total_errors,
        "error_rate_percent": round(error_rate, 2),
        "module_breakdown": module_breakdown,
        "account_statuses": statuses,
    }


# ---------------------------------------------------------------------------
# Redis Broadcast
# ---------------------------------------------------------------------------


async def broadcast_status_change(
    workspace_id: int,
    account_id: int,
    module: str,
    action: str,
) -> None:
    """Publish status change to Redis channel for WebSocket consumers."""
    try:
        import redis.asyncio as aioredis

        redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        redis = aioredis.from_url(redis_url)
        try:
            payload = json.dumps(
                {
                    "workspace_id": workspace_id,
                    "account_id": account_id,
                    "module": module,
                    "action": action,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            )
            await redis.publish(f"status_updates:{workspace_id}", payload)
        finally:
            await redis.aclose()
    except Exception as exc:
        log.debug("monitoring_service: Redis broadcast failed: %s", exc)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _serialize_status(entry: AccountStatusLive) -> dict:
    return {
        "id": entry.id,
        "workspace_id": entry.workspace_id,
        "account_id": entry.account_id,
        "account_phone": entry.account_phone,
        "current_module": entry.current_module,
        "current_action": entry.current_action,
        "started_at": entry.started_at.isoformat() if entry.started_at else None,
        "last_heartbeat_at": entry.last_heartbeat_at.isoformat() if entry.last_heartbeat_at else None,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }
