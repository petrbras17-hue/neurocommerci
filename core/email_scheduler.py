"""
Email Scheduler — Welcome Sequence automation.

Checks user registration dates and sends the appropriate Welcome Sequence
email (7 emails over 14 days).  Designed to be called on a 60-minute
interval from a lifespan background task or external cron.

Usage:
    import asyncio
    from core.email_scheduler import check_and_send_welcome_emails
    await check_and_send_welcome_emails()

Integration with FastAPI lifespan (example):
    async def _email_scheduler_loop():
        while True:
            await check_and_send_welcome_emails()
            await asyncio.sleep(3600)   # every 60 minutes
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text

from config import settings
from core.email_service import (
    WELCOME_SEQUENCE_DAYS,
    is_valid_email,
    send_welcome_sequence_email,
)
from storage.sqlite_db import async_session

log = logging.getLogger("uvicorn.error")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _days_since(dt: datetime) -> int:
    """Return whole days elapsed since *dt* (timezone-aware or naive-UTC)."""
    now = _utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).days


# ---------------------------------------------------------------------------
# State tracking table name — stores (user_id, day) pairs so we never
# double-send.  Created lazily on first run via raw DDL so no Alembic
# migration is required for this operational bookkeeping table.
# ---------------------------------------------------------------------------

_TRACKING_TABLE = "welcome_email_log"

_CREATE_TRACKING_TABLE_PG = f"""
CREATE TABLE IF NOT EXISTS {_TRACKING_TABLE} (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    day         INTEGER NOT NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, day)
);
"""

_CREATE_TRACKING_TABLE_SQLITE = f"""
CREATE TABLE IF NOT EXISTS {_TRACKING_TABLE} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    day         INTEGER NOT NULL,
    sent_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, day)
);
"""


async def _ensure_tracking_table() -> None:
    """Create the tracking table if it does not exist yet."""
    is_pg = "postgresql" in settings.db_url
    ddl = _CREATE_TRACKING_TABLE_PG if is_pg else _CREATE_TRACKING_TABLE_SQLITE
    async with async_session() as session:
        async with session.begin():
            await session.execute(text(ddl))


async def _already_sent(session: Any, user_id: int, day: int) -> bool:
    """Return True if we already sent day-N email to this user."""
    result = await session.execute(
        text(
            f"SELECT 1 FROM {_TRACKING_TABLE} WHERE user_id = :uid AND day = :day LIMIT 1"
        ),
        {"uid": user_id, "day": day},
    )
    return result.scalar() is not None


async def _record_sent(session: Any, user_id: int, day: int) -> None:
    """Insert a tracking row (idempotent — ignores duplicates)."""
    is_pg = "postgresql" in settings.db_url
    if is_pg:
        stmt = text(
            f"INSERT INTO {_TRACKING_TABLE} (user_id, day) "
            f"VALUES (:uid, :day) ON CONFLICT (user_id, day) DO NOTHING"
        )
    else:
        stmt = text(
            f"INSERT OR IGNORE INTO {_TRACKING_TABLE} (user_id, day) "
            f"VALUES (:uid, :day)"
        )
    await session.execute(stmt, {"uid": user_id, "day": day})


# ---------------------------------------------------------------------------
# Core scheduler
# ---------------------------------------------------------------------------

async def check_and_send_welcome_emails() -> int:
    """
    Scan all users registered in the last 14 days and send any pending
    Welcome Sequence emails.

    Returns the number of emails dispatched in this run.
    """
    try:
        await _ensure_tracking_table()
    except Exception as exc:  # noqa: BLE001
        log.error("email_scheduler: failed to ensure tracking table: %s", exc)
        return 0

    sent_count = 0
    cutoff = _utcnow() - timedelta(days=15)  # small margin

    try:
        async with async_session() as session:
            async with session.begin():
                # Fetch recent users who have an email address.
                result = await session.execute(
                    text(
                        "SELECT id, email, first_name, created_at "
                        "FROM auth_users "
                        "WHERE email IS NOT NULL "
                        "  AND email != '' "
                        "  AND created_at >= :cutoff "
                        "ORDER BY id"
                    ),
                    {"cutoff": cutoff},
                )
                rows = result.fetchall()

            # Process each user outside the transaction to avoid holding it
            # during SMTP calls.
            for row in rows:
                user_id = row[0]
                email = row[1]
                name = row[2] or ""
                created_at = row[3]

                if not is_valid_email(email):
                    continue

                days_elapsed = _days_since(created_at)

                # Determine which day emails should have been sent by now.
                for day, _template in sorted(WELCOME_SEQUENCE_DAYS.items()):
                    if days_elapsed < day:
                        # Not yet time for this email.
                        break

                    # Check idempotency.
                    async with async_session() as check_sess:
                        async with check_sess.begin():
                            if await _already_sent(check_sess, user_id, day):
                                continue

                    # Build context for the template.
                    ctx: dict[str, Any] = {"name": name}
                    if day == 7:
                        # Attempt to fetch comment/channel stats for day-7 email.
                        stats = await _fetch_user_stats(user_id)
                        ctx.update(stats)
                    elif day == 13:
                        ctx["trial_days_left"] = max(0, 14 - days_elapsed)

                    # Send (fire-and-forget internally, but we await to respect
                    # ordering and rate limits).
                    try:
                        await send_welcome_sequence_email(
                            to=email, day=day, **ctx,
                        )
                        # Record in tracking table.
                        async with async_session() as rec_sess:
                            async with rec_sess.begin():
                                await _record_sent(rec_sess, user_id, day)
                        sent_count += 1
                        log.info(
                            "email_scheduler: sent welcome_day_%d to user_id=%d email=%s",
                            day, user_id, email,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "email_scheduler: failed welcome_day_%d to user_id=%d: %s",
                            day, user_id, exc,
                        )

    except Exception as exc:  # noqa: BLE001
        log.error("email_scheduler: unexpected error: %s", exc)

    if sent_count:
        log.info("email_scheduler: dispatched %d welcome emails this run", sent_count)
    return sent_count


# ---------------------------------------------------------------------------
# Stats helper for day-7 email
# ---------------------------------------------------------------------------

async def _fetch_user_stats(user_id: int) -> dict[str, Any]:
    """
    Fetch total_comments and total_channels for a user's tenant.

    Returns a dict suitable for merging into template context.
    Falls back to zeros on any error (non-critical).
    """
    stats: dict[str, Any] = {
        "total_comments": 0,
        "total_channels": 0,
    }
    try:
        async with async_session() as session:
            async with session.begin():
                # Find the user's tenant_id via tenant_users or workspace.
                tenant_row = await session.execute(
                    text(
                        "SELECT tenant_id FROM tenant_users "
                        "WHERE user_id = :uid LIMIT 1"
                    ),
                    {"uid": user_id},
                )
                t = tenant_row.scalar()
                if not t:
                    return stats

                # Total comments from farm_events where event_type indicates a comment.
                comment_row = await session.execute(
                    text(
                        "SELECT COUNT(*) FROM farm_events "
                        "WHERE workspace_id IN "
                        "  (SELECT id FROM workspaces WHERE tenant_id = :tid) "
                        "AND event_type = 'comment_posted'"
                    ),
                    {"tid": t},
                )
                stats["total_comments"] = comment_row.scalar() or 0

                # Total distinct channels with activity.
                channel_row = await session.execute(
                    text(
                        "SELECT COUNT(DISTINCT channel_id) FROM farm_threads "
                        "WHERE workspace_id IN "
                        "  (SELECT id FROM workspaces WHERE tenant_id = :tid)"
                    ),
                    {"tid": t},
                )
                stats["total_channels"] = channel_row.scalar() or 0
    except Exception as exc:  # noqa: BLE001
        log.debug("email_scheduler: _fetch_user_stats user_id=%d error: %s", user_id, exc)

    return stats


# ---------------------------------------------------------------------------
# Standalone runner (for cron or manual invocation)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = asyncio.run(check_and_send_welcome_emails())
    print(f"Sent {count} welcome emails.")
