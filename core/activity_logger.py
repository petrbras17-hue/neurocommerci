"""Account activity logger — записывает каждое действие аккаунта в account_activity_logs.

Usage:
    from core.activity_logger import log_activity

    await log_activity(
        session=session,
        tenant_id=tenant_id,
        account_id=account_id,
        action_type="warmup_read",
        success=True,
        duration_ms=1234,
        details={"channel": "@example"},
    )
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import AccountActivityLog
from utils.helpers import utcnow

log = logging.getLogger(__name__)


async def log_activity(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int,
    action_type: str,
    success: bool = True,
    duration_ms: int | None = None,
    error_message: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Insert a single activity log row."""
    try:
        entry = AccountActivityLog(
            tenant_id=tenant_id,
            account_id=account_id,
            action_type=action_type,
            success=success,
            duration_ms=duration_ms,
            error_message=str(error_message)[:500] if error_message else None,
            details=details,
            created_at=utcnow(),
        )
        session.add(entry)
        await session.flush()
    except Exception as exc:
        log.warning("activity_logger: failed to log %s for account %s: %s", action_type, account_id, exc)


@asynccontextmanager
async def track_activity(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int,
    action_type: str,
    details: dict[str, Any] | None = None,
):
    """Context manager that auto-logs activity with timing and success/failure.

    Usage:
        async with track_activity(session, tenant_id=1, account_id=5, action_type="warmup_read") as ctx:
            # do the work
            ctx["details"] = {"channel": "@news"}
    """
    ctx: dict[str, Any] = {"details": details or {}}
    t0 = time.monotonic()
    try:
        yield ctx
        elapsed = int((time.monotonic() - t0) * 1000)
        await log_activity(
            session,
            tenant_id=tenant_id,
            account_id=account_id,
            action_type=action_type,
            success=True,
            duration_ms=elapsed,
            details=ctx.get("details"),
        )
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        await log_activity(
            session,
            tenant_id=tenant_id,
            account_id=account_id,
            action_type=action_type,
            success=False,
            duration_ms=elapsed,
            error_message=str(exc),
            details=ctx.get("details"),
        )
        raise
