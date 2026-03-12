"""
Account Lifecycle State Machine.

Manages validated transitions between lifecycle stages for Telegram accounts,
with full event logging to account_stage_events.

Public API
----------
lifecycle = AccountLifecycle(db_session)
result = await lifecycle.transition(account_id, "warming_up", reason="proxy bound", actor="operator")
result = await lifecycle.auto_advance(account_id)
await lifecycle.on_warmup_complete(account_id)
await lifecycle.on_flood_wait(account_id, seconds=300)
await lifecycle.on_frozen(account_id)
await lifecycle.on_ban(account_id)
await lifecycle.on_session_dead(account_id)
await lifecycle.on_appeal_success(account_id)
history = await lifecycle.get_stage_history(account_id, limit=50)
ok = AccountLifecycle.can_transition("uploaded", "warming_up")
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Account, AccountStageEvent
from utils.helpers import utcnow

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage enum
# ---------------------------------------------------------------------------


class LifecycleStage(str, Enum):
    UPLOADED = "uploaded"
    WARMING_UP = "warming_up"
    GATE_REVIEW = "gate_review"
    EXECUTION_READY = "execution_ready"
    ACTIVE_COMMENTING = "active_commenting"
    COOLDOWN = "cooldown"
    RESTRICTED = "restricted"
    FROZEN = "frozen"
    BANNED = "banned"
    DEAD = "dead"


# ---------------------------------------------------------------------------
# Valid transition map
# ---------------------------------------------------------------------------

TRANSITIONS: dict[str, list[str]] = {
    "uploaded": ["warming_up", "dead"],
    "warming_up": ["gate_review", "restricted", "frozen", "banned", "dead"],
    "gate_review": ["execution_ready", "warming_up"],
    "execution_ready": ["active_commenting", "warming_up"],
    "active_commenting": ["cooldown", "restricted", "frozen", "banned", "dead"],
    "cooldown": ["active_commenting", "warming_up", "restricted", "banned", "dead"],
    "restricted": ["warming_up", "frozen", "banned", "dead"],
    "frozen": ["warming_up", "banned", "dead"],
    "banned": ["dead"],
    "dead": [],
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LifecycleTransitionError(Exception):
    """Raised when a requested lifecycle transition is not valid."""


# ---------------------------------------------------------------------------
# AccountLifecycle
# ---------------------------------------------------------------------------


class AccountLifecycle:
    """Manages account lifecycle transitions with validation and event logging.

    The db_session must be active (inside a transaction or with autocommit
    disabled) when calling methods.  The caller is responsible for committing
    or rolling back.

    For one-shot fire-and-forget calls (on_ban, on_frozen, etc.) the class
    opens its own short-lived session so callers do not have to manage the
    session lifetime.
    """

    def __init__(self, db_session: AsyncSession, *, tenant_id: Optional[int] = None) -> None:
        self._session = db_session
        self._tenant_id = tenant_id

    # ------------------------------------------------------------------
    # Core transition
    # ------------------------------------------------------------------

    async def transition(
        self,
        account_id: int,
        target_stage: str,
        *,
        reason: str = "",
        actor: str = "system",
    ) -> dict:
        """Transition an account to target_stage.

        Validates that the transition is allowed, updates lifecycle_stage in
        the DB, and writes an AccountStageEvent row.

        Returns:
            {
                "ok": bool,
                "account_id": int,
                "old_stage": str | None,
                "new_stage": str,
                "reason": str,
            }

        Raises:
            LifecycleTransitionError if the transition is not valid.
            ValueError if the account is not found.
        """
        query = select(Account).where(Account.id == account_id)
        if self._tenant_id is not None:
            query = query.where(Account.tenant_id == self._tenant_id)
        result = await self._session.execute(query)
        account: Optional[Account] = result.scalar_one_or_none()
        if account is None:
            raise ValueError(f"account_id={account_id} not found")

        old_stage = account.lifecycle_stage
        if not self.can_transition(old_stage or "", target_stage):
            raise LifecycleTransitionError(
                f"transition '{old_stage}' -> '{target_stage}' is not allowed"
            )

        # Apply update.
        await self._session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(lifecycle_stage=target_stage)
        )

        # Log event.
        event = AccountStageEvent(
            account_id=account_id,
            phone=account.phone or "",
            from_stage=old_stage,
            to_stage=target_stage,
            actor=actor,
            reason=reason,
            created_at=utcnow(),
        )
        self._session.add(event)

        log.info(
            "account_lifecycle: account_id=%s %s -> %s actor=%s reason=%s",
            account_id,
            old_stage,
            target_stage,
            actor,
            reason,
        )

        return {
            "ok": True,
            "account_id": account_id,
            "old_stage": old_stage,
            "new_stage": target_stage,
            "reason": reason,
        }

    # ------------------------------------------------------------------
    # Auto-advance
    # ------------------------------------------------------------------

    async def auto_advance(self, account_id: int) -> Optional[dict]:
        """Check if an account should auto-advance based on its current state.

        Conditions evaluated (in order):
        - uploaded + proxy_id set -> warming_up
        - warming_up + days_active >= 15 -> gate_review
        - gate_review: no auto-advance (operator must approve)
        - execution_ready + proxy_id set -> active_commenting
        - cooldown + quarantined_until expired -> active_commenting
        - any non-terminal + health_status == "dead" -> dead
        - any non-terminal + status == "banned" -> banned (if allowed)

        Returns the transition result dict, or None if no advance was needed.
        """
        query = select(Account).where(Account.id == account_id)
        if self._tenant_id is not None:
            query = query.where(Account.tenant_id == self._tenant_id)
        result = await self._session.execute(query)
        account: Optional[Account] = result.scalar_one_or_none()
        if account is None:
            raise ValueError(f"account_id={account_id} not found")

        stage = account.lifecycle_stage or "uploaded"

        # Terminal stage — nothing to do.
        if stage == "dead":
            return None

        now = utcnow()

        # Health-based dead check (any non-terminal stage).
        if account.health_status == "dead" and self.can_transition(stage, "dead"):
            return await self.transition(
                account_id,
                "dead",
                reason="health_status=dead",
                actor="system",
            )

        # Ban check (any stage that can reach banned).
        if account.status == "banned" and self.can_transition(stage, "banned"):
            return await self.transition(
                account_id,
                "banned",
                reason="status=banned",
                actor="system",
            )

        # Stage-specific auto-advance rules.
        if stage == "uploaded":
            if account.proxy_id is not None:
                return await self.transition(
                    account_id,
                    "warming_up",
                    reason="proxy_bound",
                    actor="system",
                )

        elif stage == "warming_up":
            if (account.days_active or 0) >= 15:
                return await self.transition(
                    account_id,
                    "gate_review",
                    reason="warmup_complete",
                    actor="system",
                )

        elif stage == "execution_ready":
            if account.proxy_id is not None:
                return await self.transition(
                    account_id,
                    "active_commenting",
                    reason="farm_assigned",
                    actor="system",
                )

        elif stage == "cooldown":
            expired = (
                account.quarantined_until is None
                or account.quarantined_until <= now
            )
            if expired:
                return await self.transition(
                    account_id,
                    "active_commenting",
                    reason="cooldown_expired",
                    actor="system",
                )

        return None

    # ------------------------------------------------------------------
    # Event-driven handlers
    # ------------------------------------------------------------------

    async def on_warmup_complete(self, account_id: int) -> dict:
        """Called when warmup finishes — advance warming_up -> gate_review."""
        return await self.transition(
            account_id,
            "gate_review",
            reason="warmup_complete",
            actor="warmup_engine",
        )

    async def on_flood_wait(self, account_id: int, seconds: int) -> dict:
        """Called on FloodWaitError — transition to cooldown."""
        return await self.transition(
            account_id,
            "cooldown",
            reason=f"flood_wait_{seconds}s",
            actor="system",
        )

    async def on_frozen(self, account_id: int) -> dict:
        """Called on FrozenMethodInvalidError — transition to frozen."""
        return await self.transition(
            account_id,
            "frozen",
            reason="FrozenMethodInvalidError",
            actor="system",
        )

    async def on_ban(self, account_id: int) -> dict:
        """Called on UserDeactivatedBanError — transition to banned."""
        return await self.transition(
            account_id,
            "banned",
            reason="UserDeactivatedBanError",
            actor="system",
        )

    async def on_session_dead(self, account_id: int) -> dict:
        """Called on SessionRevokedError / AuthKeyUnregistered — transition to dead."""
        return await self.transition(
            account_id,
            "dead",
            reason="SessionRevokedError",
            actor="system",
        )

    async def on_appeal_success(self, account_id: int) -> dict:
        """Called when a SpamBot appeal succeeds — frozen -> warming_up."""
        return await self.transition(
            account_id,
            "warming_up",
            reason="appeal_success",
            actor="system",
        )

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def get_stage_history(
        self,
        account_id: int,
        limit: int = 50,
    ) -> list[dict]:
        """Return lifecycle event history for an account.

        Returns a list of dicts ordered newest-first.
        """
        # Verify account belongs to tenant if tenant_id is set.
        if self._tenant_id is not None:
            acct_check = await self._session.execute(
                select(Account.id).where(
                    Account.id == account_id,
                    Account.tenant_id == self._tenant_id,
                )
            )
            if acct_check.scalar_one_or_none() is None:
                raise ValueError(f"account_id={account_id} not found")
        result = await self._session.execute(
            select(AccountStageEvent)
            .where(AccountStageEvent.account_id == account_id)
            .order_by(AccountStageEvent.id.desc())
            .limit(max(1, min(limit, 500)))
        )
        rows = list(result.scalars().all())
        return [
            {
                "id": row.id,
                "account_id": row.account_id,
                "phone": row.phone,
                "from_stage": row.from_stage,
                "to_stage": row.to_stage,
                "actor": row.actor,
                "reason": row.reason,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def can_transition(current: str, target: str) -> bool:
        """Return True if a transition from current to target is valid."""
        return target in TRANSITIONS.get(current, [])
