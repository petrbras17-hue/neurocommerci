"""
Health and survivability scorer for Telegram accounts.

Calculates two scores (0-100 each) per account per tenant and persists them
in the account_health_scores table via upsert.

Score formulas
--------------
health_score = 100
    - flood_wait_count * 8       (capped at -40)
    - spam_block_count * 15      (capped at -60)
    + successful_actions * 0.1   (capped at +20)
    + hours_without_error * 0.5  (capped at +15)
    + profile_completeness * 0.1 (capped at +10)
    - quarantine_penalty         (20 points if currently quarantined)
    clamped to [0, 100]

survivability_score = 100
    - spam_block_count * 20      (capped at -80)
    - flood_wait_count * 5       (capped at -30)
    + account_age_days * 0.3     (capped at +20)
    + successful_actions * 0.05  (capped at +10)
    clamped to [0, 100]
"""

from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Account, AccountHealthScore, FarmThread
from storage.sqlite_db import apply_session_rls_context
from utils.helpers import utcnow

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure math helpers (no I/O, easy to unit-test)
# ---------------------------------------------------------------------------

def _cap(value: float, floor: float, ceiling: float) -> float:
    """Clamp value to [floor, ceiling]."""
    return max(floor, min(ceiling, value))


def _compute_health_score(
    flood_wait_count: int,
    spam_block_count: int,
    successful_actions: int,
    hours_without_error: int,
    profile_completeness: int,
    is_quarantined: bool,
) -> tuple[int, dict]:
    """Return (health_score 0-100, factors dict)."""
    flood_penalty   = _cap(flood_wait_count * 8,        0, 40)
    spam_penalty    = _cap(spam_block_count * 15,       0, 60)
    action_bonus    = _cap(successful_actions * 0.1,    0, 20)
    uptime_bonus    = _cap(hours_without_error * 0.5,   0, 15)
    profile_bonus   = _cap(profile_completeness * 0.1,  0, 10)
    quarantine_pen  = 20 if is_quarantined else 0

    raw = (
        100
        - flood_penalty
        - spam_penalty
        + action_bonus
        + uptime_bonus
        + profile_bonus
        - quarantine_pen
    )
    score = int(_cap(raw, 0, 100))

    factors = {
        "flood_penalty":      -flood_penalty,
        "spam_penalty":       -spam_penalty,
        "action_bonus":        action_bonus,
        "uptime_bonus":        uptime_bonus,
        "profile_bonus":       profile_bonus,
        "quarantine_penalty": -quarantine_pen,
        "raw":                 raw,
        "final":               score,
    }
    return score, factors


def _compute_survivability_score(
    spam_block_count: int,
    flood_wait_count: int,
    account_age_days: int,
    successful_actions: int,
) -> int:
    """Return survivability_score 0-100."""
    spam_penalty  = _cap(spam_block_count * 20,      0, 80)
    flood_penalty = _cap(flood_wait_count * 5,        0, 30)
    age_bonus     = _cap(account_age_days * 0.3,      0, 20)
    action_bonus  = _cap(successful_actions * 0.05,   0, 10)

    raw = 100 - spam_penalty - flood_penalty + age_bonus + action_bonus
    return int(_cap(raw, 0, 100))


# ---------------------------------------------------------------------------
# HealthScorer class
# ---------------------------------------------------------------------------

class HealthScorer:
    """Calculates and persists health/survivability scores for accounts.

    All DB calls must be made inside an active transaction so that
    apply_session_rls_context can use SET LOCAL semantics.
    """

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _is_account_quarantined(
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> bool:
        """Return True if any FarmThread for this account is currently quarantined."""
        await apply_session_rls_context(session, tenant_id=tenant_id)
        result = await session.execute(
            select(FarmThread).where(
                FarmThread.account_id == account_id,
                FarmThread.tenant_id == tenant_id,
            )
        )
        rows: list[FarmThread] = list(result.scalars().all())
        if not rows:
            return False
        now = utcnow()
        return any(
            r.quarantine_until is not None and r.quarantine_until > now
            for r in rows
        )

    @staticmethod
    async def _fetch_existing(
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> Optional[AccountHealthScore]:
        await apply_session_rls_context(session, tenant_id=tenant_id)
        result = await session.execute(
            select(AccountHealthScore).where(
                AccountHealthScore.account_id == account_id,
                AccountHealthScore.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def calculate_score(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> AccountHealthScore:
        """Compute and upsert the health/survivability score for one account.

        Must be called inside an active transaction (apply_session_rls_context
        requires SET LOCAL inside a transaction block).
        """
        # Load the account to get age_days; raise if not found for this tenant.
        await apply_session_rls_context(session, tenant_id=tenant_id)
        account_result = await session.execute(
            select(Account).where(
                Account.id == account_id,
                Account.tenant_id == tenant_id,
            )
        )
        account: Optional[Account] = account_result.scalar_one_or_none()
        if account is None:
            raise ValueError(
                f"account_id={account_id} not found for tenant_id={tenant_id}"
            )

        # Pull existing score row (preserves manually updated counters).
        existing = await self._fetch_existing(account_id, tenant_id, session)

        flood_wait_count    = existing.flood_wait_count if existing and existing.flood_wait_count is not None else 0
        spam_block_count    = existing.spam_block_count if existing and existing.spam_block_count is not None else 0
        successful_actions  = existing.successful_actions if existing and existing.successful_actions is not None else 0
        hours_without_error = existing.hours_without_error if existing and existing.hours_without_error is not None else 0
        profile_completeness = existing.profile_completeness if existing and existing.profile_completeness is not None else 0
        _raw_age = getattr(account, "account_age_days", None)
        account_age_days    = _raw_age if _raw_age is not None else 0

        is_quarantined = await self._is_account_quarantined(
            account_id, tenant_id, session
        )

        health, factors = _compute_health_score(
            flood_wait_count=flood_wait_count,
            spam_block_count=spam_block_count,
            successful_actions=successful_actions,
            hours_without_error=hours_without_error,
            profile_completeness=profile_completeness,
            is_quarantined=is_quarantined,
        )
        survivability = _compute_survivability_score(
            spam_block_count=spam_block_count,
            flood_wait_count=flood_wait_count,
            account_age_days=account_age_days,
            successful_actions=successful_actions,
        )

        if existing is None:
            row = AccountHealthScore(
                tenant_id=tenant_id,
                account_id=account_id,
                health_score=health,
                survivability_score=survivability,
                flood_wait_count=flood_wait_count,
                spam_block_count=spam_block_count,
                successful_actions=successful_actions,
                hours_without_error=hours_without_error,
                profile_completeness=profile_completeness,
                account_age_days=account_age_days,
                last_calculated_at=utcnow(),
                factors=factors,
            )
            session.add(row)
        else:
            existing.health_score        = health
            existing.survivability_score = survivability
            existing.account_age_days    = account_age_days
            existing.last_calculated_at  = utcnow()
            existing.factors             = factors
            row = existing

        await session.flush()
        log.info(
            "health_scorer: account_id=%s tenant_id=%s health=%s survivability=%s",
            account_id,
            tenant_id,
            health,
            survivability,
        )
        return row

    async def recalculate_all(
        self,
        tenant_id: int,
        session: AsyncSession,
    ) -> None:
        """Recalculate scores for every account belonging to a tenant.

        Silently skips individual accounts that raise an error so one bad
        account does not abort the whole batch.
        """
        await apply_session_rls_context(session, tenant_id=tenant_id)
        result = await session.execute(
            select(Account).where(Account.tenant_id == tenant_id).limit(5000)
        )
        accounts: list[Account] = list(result.scalars().all())
        log.info(
            "health_scorer: recalculate_all tenant_id=%s accounts=%s",
            tenant_id,
            len(accounts),
        )
        for account in accounts:
            try:
                await self.calculate_score(account.id, tenant_id, session)
            except Exception as exc:
                log.error(
                    "health_scorer: failed account_id=%s tenant_id=%s error=%s",
                    account.id,
                    tenant_id,
                    exc,
                )

    async def get_score(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> Optional[AccountHealthScore]:
        """Return the stored AccountHealthScore row, or None if not yet calculated."""
        return await self._fetch_existing(account_id, tenant_id, session)

    async def list_scores(
        self,
        tenant_id: int,
        session: AsyncSession,
    ) -> List[AccountHealthScore]:
        """Return all AccountHealthScore rows for a tenant."""
        await apply_session_rls_context(session, tenant_id=tenant_id)
        result = await session.execute(
            select(AccountHealthScore).where(
                AccountHealthScore.tenant_id == tenant_id
            ).limit(5000)
        )
        return list(result.scalars().all())
