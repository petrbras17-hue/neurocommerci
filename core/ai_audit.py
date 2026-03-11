from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import AIRequest, AIRequestAttempt


def _attempt_payload(item: AIRequestAttempt) -> dict[str, Any]:
    return {
        "id": int(item.id),
        "attempt_number": int(item.attempt_number or 0),
        "provider": item.provider,
        "model_name": item.model_name,
        "status": item.status,
        "latency_ms": item.latency_ms,
        "prompt_tokens": int(item.prompt_tokens or 0),
        "completion_tokens": int(item.completion_tokens or 0),
        "estimated_cost_usd": float(item.estimated_cost_usd or 0.0),
        "fallback_used": bool(item.fallback_used),
        "reason_code": item.reason_code,
        "json_parse_failed": bool(item.json_parse_failed),
        "json_repair_applied": bool(item.json_repair_applied),
        "json_repair_strategy": item.json_repair_strategy,
        "parsed_without_repair": bool(item.parsed_without_repair),
        "response_meta": item.response_meta or {},
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _request_payload(item: AIRequest, attempts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": int(item.id),
        "surface": item.surface,
        "task_type": item.task_type,
        "agent_name": item.agent_name,
        "requested_model_tier": item.requested_model_tier,
        "executed_model_tier": item.executed_model_tier,
        "requested_provider": item.requested_provider,
        "executed_provider": item.executed_provider,
        "executed_model": item.executed_model,
        "status": item.status,
        "outcome": item.outcome,
        "latency_ms": item.latency_ms,
        "prompt_tokens": int(item.prompt_tokens or 0),
        "completion_tokens": int(item.completion_tokens or 0),
        "estimated_cost_usd": float(item.estimated_cost_usd or 0.0),
        "fallback_used": bool(item.fallback_used),
        "reason_code": item.reason_code,
        "json_parse_failed": bool(item.json_parse_failed),
        "json_repair_applied": bool(item.json_repair_applied),
        "json_repair_strategy": item.json_repair_strategy,
        "parsed_without_repair": bool(item.parsed_without_repair),
        "downgraded_by_budget_policy": bool(item.downgraded_by_budget_policy),
        "blocked_by_budget_policy": bool(item.blocked_by_budget_policy),
        "quality_score": float(item.quality_score or 0.0),
        "quality_flags": item.quality_flags or {},
        "meta": item.meta or {},
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "attempts": attempts or [],
    }


async def get_tenant_ai_quality_summary(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int | None = None,
) -> dict[str, Any]:
    conditions = [AIRequest.tenant_id == tenant_id]
    if workspace_id is not None:
        conditions.append(AIRequest.workspace_id == workspace_id)

    requests = (
        await session.execute(
            select(AIRequest)
            .where(*conditions)
            .order_by(AIRequest.created_at.desc(), AIRequest.id.desc())
            .limit(100)
        )
    ).scalars().all()

    latest_by_task: dict[str, dict[str, Any]] = {}
    for item in requests:
        if item.task_type in latest_by_task:
            continue
        latest_by_task[item.task_type] = {
            "task_type": item.task_type,
            "provider": item.executed_provider or item.requested_provider,
            "model": item.executed_model,
            "tier": item.executed_model_tier or item.requested_model_tier,
            "quality_score": float(item.quality_score or 0.0),
            "fallback_used": bool(item.fallback_used),
            "repair_applied": bool(item.json_repair_applied),
            "latency_ms": item.latency_ms,
            "estimated_cost_usd": float(item.estimated_cost_usd or 0.0),
            "outcome": item.outcome,
            "status": item.status,
            "reason_code": item.reason_code,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }

    total_count = len(requests)
    fallback_count = sum(1 for item in requests if item.fallback_used)
    repair_count = sum(1 for item in requests if item.json_repair_applied)
    avg_quality = 0.0
    if total_count:
        avg_quality = round(sum(float(item.quality_score or 0.0) for item in requests) / total_count, 3)

    last_request = _request_payload(requests[0]) if requests else None

    return {
        "overview": {
            "total_requests": total_count,
            "avg_quality_score": avg_quality,
            "fallback_rate": round(fallback_count / total_count, 3) if total_count else 0.0,
            "repair_rate": round(repair_count / total_count, 3) if total_count else 0.0,
            "last_request": last_request,
        },
        "latest_by_task": latest_by_task,
    }


async def list_internal_ai_audit(
    session: AsyncSession,
    *,
    tenant_id: int | None = None,
    workspace_id: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    conditions = []
    if tenant_id is not None:
        conditions.append(AIRequest.tenant_id == tenant_id)
    if workspace_id is not None:
        conditions.append(AIRequest.workspace_id == workspace_id)

    requests = (
        await session.execute(
            select(AIRequest)
            .where(*conditions)
            .order_by(AIRequest.created_at.desc(), AIRequest.id.desc())
            .limit(max(1, min(int(limit), 200)))
        )
    ).scalars().all()
    if not requests:
        return {"items": [], "total": 0}

    request_ids = [int(item.id) for item in requests]
    attempts = (
        await session.execute(
            select(AIRequestAttempt)
            .where(AIRequestAttempt.ai_request_id.in_(request_ids))
            .order_by(AIRequestAttempt.created_at.asc(), AIRequestAttempt.id.asc())
        )
    ).scalars().all()
    attempts_map: dict[int, list[dict[str, Any]]] = {}
    for item in attempts:
        attempts_map.setdefault(int(item.ai_request_id), []).append(_attempt_payload(item))

    return {
        "items": [_request_payload(item, attempts=attempts_map.get(int(item.id), [])) for item in requests],
        "total": len(requests),
    }
