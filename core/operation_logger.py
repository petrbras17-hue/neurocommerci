"""
Operation Logger — writes to operation_logs table and broadcasts via Redis pub/sub.

Usage:
    from core.operation_logger import log_operation
    await log_operation(workspace_id=1, account_id=42, module="warmup",
                        action="story_viewed", status="success", detail="viewed 3 stories")
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from config import settings
from storage.models import OperationLog
from storage.sqlite_db import async_session
from utils.helpers import utcnow
from utils.logger import log


async def log_operation(
    workspace_id: int,
    account_id: Optional[int],
    module: str,
    action: str,
    status: str,
    detail: Optional[str] = None,
) -> None:
    """Log an operation to the operation_logs table and broadcast via Redis."""
    try:
        async with async_session() as session:
            async with session.begin():
                entry = OperationLog(
                    workspace_id=workspace_id,
                    account_id=account_id,
                    module=module,
                    action=action,
                    status=status,
                    detail=detail,
                )
                session.add(entry)
    except Exception as exc:
        log.warning("operation_logger: failed to persist log: %s", exc)

    # Broadcast via Redis pub/sub (best-effort, never block caller)
    await _broadcast_log(workspace_id, module, action, status, detail, account_id)


async def _broadcast_log(
    workspace_id: int,
    module: str,
    action: str,
    status: str,
    detail: Optional[str],
    account_id: Optional[int],
) -> None:
    """Publish log entry to Redis channel for WebSocket consumers."""
    try:
        import redis.asyncio as aioredis

        redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        redis = aioredis.from_url(redis_url)
        try:
            payload = json.dumps(
                {
                    "workspace_id": workspace_id,
                    "module": module,
                    "action": action,
                    "status": status,
                    "detail": detail,
                    "account_id": account_id,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            )
            await redis.publish(f"operation_logs:{workspace_id}", payload)
        finally:
            await redis.aclose()
    except Exception as exc:
        log.debug("operation_logger: Redis broadcast failed: %s", exc)
