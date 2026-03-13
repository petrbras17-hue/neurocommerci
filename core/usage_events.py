from __future__ import annotations

import asyncio
from typing import Any, Set

from loguru import logger

from storage.models import UsageEvent
from storage.sqlite_db import apply_session_rls_context, async_session

# Strong references to background tasks to prevent GC before completion
_background_tasks: Set[asyncio.Task] = set()


def _normalize_meta(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise ValueError("meta_must_be_dict")
    return meta


async def _insert_usage_event(tenant_id: int, event_type: str, meta: dict[str, Any] | None = None) -> None:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, int(tenant_id))
            session.add(
                UsageEvent(
                    tenant_id=int(tenant_id),
                    event_type=str(event_type),
                    meta=_normalize_meta(meta),
                )
            )


async def _safe_insert_usage_event(tenant_id: int, event_type: str, meta: dict[str, Any] | None = None) -> None:
    try:
        await _insert_usage_event(tenant_id, event_type, meta)
    except Exception as exc:
        logger.warning(f"Usage event insert failed: {exc}")


async def log_usage_event(tenant_id: int, event_type: str, meta: dict[str, Any] | None = None) -> None:
    """Fire-and-forget usage event logging."""
    try:
        task = asyncio.create_task(_safe_insert_usage_event(int(tenant_id), str(event_type), meta))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except Exception as exc:
        logger.warning(f"Usage event scheduling failed: {exc}")
