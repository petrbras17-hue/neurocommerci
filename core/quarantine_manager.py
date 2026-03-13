"""
Quarantine Manager — account quarantine lifecycle for the farm runtime.

Quarantine is stored in farm_threads.quarantine_until (per-thread, per-tenant).
All queries go through apply_session_rls_context for RLS enforcement.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Account, FarmThread
from storage.sqlite_db import apply_session_rls_context
from utils.helpers import utcnow

log = logging.getLogger(__name__)


class QuarantineManager:
    """Manages account quarantine state via farm_threads rows.

    All methods must be called inside an active transaction so that
    apply_session_rls_context can apply SET LOCAL settings.
    """

    # ------------------------------------------------------------------
    # Quarantine lifecycle
    # ------------------------------------------------------------------

    async def quarantine_account(
        self,
        account_id: int,
        tenant_id: int,
        reason: str,
        duration_hours: float,
        session: AsyncSession,
    ) -> None:
        """Set quarantine on all FarmThread rows for this account.

        Sets status='quarantine', quarantine_until=now+duration_hours,
        and stores the reason in stats_last_error.
        """
        await apply_session_rls_context(session, tenant_id=tenant_id)
        until = utcnow() + timedelta(hours=duration_hours)
        await session.execute(
            update(FarmThread)
            .where(
                FarmThread.account_id == account_id,
                FarmThread.tenant_id == tenant_id,
            )
            .values(
                status="quarantine",
                quarantine_until=until,
                stats_last_error=f"Quarantine: {reason}",
                updated_at=utcnow(),
            )
        )
        log.info(
            "quarantine_manager: account_id=%s tenant_id=%s quarantined for %.1fh reason=%s",
            account_id,
            tenant_id,
            duration_hours,
            reason,
        )

    async def lift_quarantine(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> None:
        """Clear quarantine_until on all FarmThread rows for this account."""
        await apply_session_rls_context(session, tenant_id=tenant_id)
        await session.execute(
            update(FarmThread)
            .where(
                FarmThread.account_id == account_id,
                FarmThread.tenant_id == tenant_id,
            )
            .values(
                status="idle",
                quarantine_until=None,
                updated_at=utcnow(),
            )
        )
        log.info(
            "quarantine_manager: quarantine lifted account_id=%s tenant_id=%s",
            account_id,
            tenant_id,
        )

    async def list_quarantined(
        self,
        tenant_id: int,
        session: AsyncSession,
    ) -> List[dict]:
        """Return quarantined accounts for a tenant.

        Each dict contains: account_id, phone, reason, quarantine_until.
        Multiple threads for the same account are collapsed — the latest
        quarantine_until wins.
        """
        await apply_session_rls_context(session, tenant_id=tenant_id)
        now = utcnow()

        # Join FarmThread with Account to get the phone number.
        result = await session.execute(
            select(FarmThread, Account)
            .join(Account, Account.id == FarmThread.account_id)
            .where(
                FarmThread.tenant_id == tenant_id,
                Account.tenant_id == tenant_id,
                FarmThread.status == "quarantine",
                FarmThread.quarantine_until > now,
            )
        )
        rows = result.all()

        # Collapse per account_id, keeping the latest quarantine_until.
        seen: dict[int, dict] = {}
        for thread, account in rows:
            acc_id = int(thread.account_id)
            entry = seen.get(acc_id)
            if entry is None:
                seen[acc_id] = {
                    "account_id": acc_id,
                    "phone": account.phone,
                    "reason": thread.stats_last_error,
                    "quarantine_until": thread.quarantine_until,
                }
            else:
                # Keep the furthest quarantine_until.
                if (
                    thread.quarantine_until is not None
                    and (
                        entry["quarantine_until"] is None
                        or thread.quarantine_until > entry["quarantine_until"]
                    )
                ):
                    entry["quarantine_until"] = thread.quarantine_until

        return list(seen.values())

    async def auto_lift_expired(
        self,
        tenant_id: int,
        session: AsyncSession,
    ) -> None:
        """Lift quarantine where quarantine_until < now for a tenant."""
        await apply_session_rls_context(session, tenant_id=tenant_id)
        now = utcnow()
        result = await session.execute(
            select(FarmThread).where(
                FarmThread.tenant_id == tenant_id,
                FarmThread.status == "quarantine",
                FarmThread.quarantine_until <= now,
            )
        )
        expired_threads: list[FarmThread] = list(result.scalars().all())
        released_account_ids: set[int] = set()
        for thread in expired_threads:
            thread.status = "idle"
            thread.quarantine_until = None
            thread.updated_at = now
            released_account_ids.add(int(thread.account_id))

        if released_account_ids:
            log.info(
                "quarantine_manager: auto_lift_expired tenant_id=%s released accounts=%s",
                tenant_id,
                sorted(released_account_ids),
            )

    async def is_quarantined(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> bool:
        """Return True if the account has at least one active FarmThread quarantine."""
        await apply_session_rls_context(session, tenant_id=tenant_id)
        now = utcnow()
        result = await session.execute(
            select(FarmThread).where(
                FarmThread.account_id == account_id,
                FarmThread.tenant_id == tenant_id,
                FarmThread.status == "quarantine",
                FarmThread.quarantine_until > now,
            )
        )
        rows = result.scalars().all()
        return len(list(rows)) > 0
