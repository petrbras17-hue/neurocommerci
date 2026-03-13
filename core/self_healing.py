"""
Self-Healing Engine — automatic platform recovery when faults are detected.

All DB queries run inside an active transaction (RLS requires SET LOCAL inside
an active transaction block).  Call each method with a session that is already
inside a transaction, or open a new transaction via async_session().begin().
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import (
    Account,
    AlertConfig,
    FarmThread,
    HealingAction,
    PlatformAlert,
    Proxy,
)
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Depletion prediction parameters
# ---------------------------------------------------------------------------

_ACCOUNT_BAN_RATE_WINDOW_DAYS = 7   # look-back window for computing ban rate
_PROXY_DEATH_RATE_WINDOW_DAYS = 7   # look-back window for proxy death rate

# Thresholds that trigger a low-resource alert
_LOW_ACCOUNT_THRESHOLD_PCT = 20     # alert if alive accounts < 20 % of total
_LOW_PROXY_THRESHOLD_PCT = 20       # alert if alive proxies < 20 % of total


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _log_healing_action(
    session: AsyncSession,
    tenant_id: int,
    action_type: str,
    target_type: str,
    target_id: int | None,
    details: dict[str, Any],
    outcome: str = "success",
) -> HealingAction:
    """Persist a HealingAction row (must be inside an active transaction)."""
    row = HealingAction(
        tenant_id=tenant_id,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        details=details,
        outcome=outcome,
        created_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def _create_alert(
    session: AsyncSession,
    tenant_id: int,
    alert_type: str,
    severity: str,
    message: str,
) -> PlatformAlert:
    """Upsert an unresolved alert (deduped by alert_type within the tenant)."""
    # Resolve any duplicate open alerts of the same type first.
    existing = (
        await session.execute(
            select(PlatformAlert).where(
                PlatformAlert.tenant_id == tenant_id,
                PlatformAlert.alert_type == alert_type,
                PlatformAlert.is_resolved == False,  # noqa: E712
            ).limit(1000)
        )
    ).scalars().all()
    for old in existing:
        old.is_resolved = True
        old.resolved_at = utcnow()

    alert = PlatformAlert(
        tenant_id=tenant_id,
        alert_type=alert_type,
        severity=severity,
        message=message,
        is_resolved=False,
        created_at=utcnow(),
    )
    session.add(alert)
    await session.flush()
    return alert


# ---------------------------------------------------------------------------
# SelfHealingEngine
# ---------------------------------------------------------------------------


class SelfHealingEngine:
    """Automatic platform recovery service.

    Every public method opens its own DB session and transaction so that
    callers do not need to worry about session lifecycle.
    """

    # ------------------------------------------------------------------
    # 1. Account ban handler
    # ------------------------------------------------------------------

    async def handle_account_ban(
        self, tenant_id: int, account_id: int
    ) -> dict[str, Any]:
        """Handle a confirmed account ban.

        Steps:
        1. Mark account status = 'banned'.
        2. Set all farm threads for this account to 'stopped'.
        3. Notify if alive account count drops below the alert threshold.
        4. Return a summary dict.
        """
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)

                # 1. Mark account banned.
                account_result = await session.execute(
                    select(Account).where(
                        Account.id == account_id,
                        Account.tenant_id == tenant_id,
                    )
                )
                account: Account | None = account_result.scalar_one_or_none()
                if account is None:
                    return {"error": "account_not_found", "account_id": account_id}

                prev_status = account.status
                account.status = "banned"
                account.health_status = "banned"
                account.lifecycle_stage = "restricted"
                account.updated_at = utcnow()

                # 2. Stop all farm threads for this account.
                await session.execute(
                    update(FarmThread)
                    .where(
                        FarmThread.account_id == account_id,
                        FarmThread.tenant_id == tenant_id,
                    )
                    .values(status="stopped", updated_at=utcnow())
                )

                # 3. Log healing action.
                await _log_healing_action(
                    session,
                    tenant_id=tenant_id,
                    action_type="handle_account_ban",
                    target_type="account",
                    target_id=account_id,
                    details={"prev_status": prev_status, "account_id": account_id},
                )

                # 4. Check if low-resource alert should fire.
                counts = (
                    await session.execute(
                        select(
                            func.count(Account.id).label("total"),
                            func.sum(
                                (Account.status == "active").cast(
                                    type_=Account.id.type.__class__
                                )
                            ).label("alive"),
                        ).where(Account.tenant_id == tenant_id)
                    )
                ).one()

                total = int(counts.total) if counts.total is not None else 0
                alive = int(counts.alive) if counts.alive is not None else 0
                pct = int(alive * 100 / total) if total else 100

                summary: dict[str, Any] = {
                    "account_id": account_id,
                    "status": "banned",
                    "accounts_alive": alive,
                    "accounts_total": total,
                    "alive_percent": pct,
                }

                # Load alert config to know the threshold.
                cfg_row = (
                    await session.execute(
                        select(AlertConfig).where(
                            AlertConfig.tenant_id == tenant_id,
                            AlertConfig.resource_type == "account",
                        )
                    )
                ).scalar_one_or_none()
                threshold = int(cfg_row.threshold_percent) if cfg_row else _LOW_ACCOUNT_THRESHOLD_PCT

                if pct <= threshold:
                    await _create_alert(
                        session,
                        tenant_id=tenant_id,
                        alert_type="low_accounts",
                        severity="critical" if pct <= 5 else "warning",
                        message=(
                            f"Живых аккаунтов {alive}/{total} ({pct}%) — "
                            f"ниже порога {threshold}%. Рекомендуется докупить аккаунты."
                        ),
                    )
                    summary["alert_fired"] = True

        log.info(
            "self_healing: handle_account_ban tenant=%s account=%s alive_pct=%s",
            tenant_id,
            account_id,
            pct,
        )
        return summary

    # ------------------------------------------------------------------
    # 2. Proxy death handler
    # ------------------------------------------------------------------

    async def handle_proxy_death(
        self, tenant_id: int, proxy_id: int
    ) -> dict[str, Any]:
        """Handle a dead proxy.

        Steps:
        1. Mark proxy health_status = 'dead'.
        2. Find a free alive proxy and rebind the affected account.
        3. Reset the account's farm thread to idle so it will restart.
        4. Alert if alive proxy count drops below threshold.
        """
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)

                proxy_result = await session.execute(
                    select(Proxy).where(
                        Proxy.id == proxy_id,
                        Proxy.tenant_id == tenant_id,
                    )
                )
                proxy: Proxy | None = proxy_result.scalar_one_or_none()
                if proxy is None:
                    return {"error": "proxy_not_found", "proxy_id": proxy_id}

                proxy.health_status = "dead"
                proxy.is_active = False

                # Find an account using this proxy.
                acc_result = await session.execute(
                    select(Account).where(
                        Account.proxy_id == proxy_id,
                        Account.tenant_id == tenant_id,
                        Account.status == "active",
                    ).limit(1000)
                )
                affected_accounts = list(acc_result.scalars().all())

                # Find a free alive proxy for rebinding.
                free_proxy_result = await session.execute(
                    select(Proxy).where(
                        Proxy.tenant_id == tenant_id,
                        Proxy.health_status == "alive",
                        Proxy.is_active == True,  # noqa: E712
                        Proxy.id != proxy_id,
                    ).limit(len(affected_accounts) + 1)
                )
                free_proxies = list(free_proxy_result.scalars().all())
                free_iter = iter(free_proxies)

                rebound_accounts: list[int] = []
                for acc in affected_accounts:
                    new_proxy = next(free_iter, None)
                    if new_proxy:
                        acc.proxy_id = new_proxy.id
                        # Reset farm thread to idle so it reconnects.
                        await session.execute(
                            update(FarmThread)
                            .where(
                                FarmThread.account_id == acc.id,
                                FarmThread.tenant_id == tenant_id,
                                FarmThread.status.notin_(["stopped", "quarantine"]),
                            )
                            .values(status="idle", updated_at=utcnow())
                        )
                        rebound_accounts.append(int(acc.id))

                await _log_healing_action(
                    session,
                    tenant_id=tenant_id,
                    action_type="handle_proxy_death",
                    target_type="proxy",
                    target_id=proxy_id,
                    details={
                        "proxy_id": proxy_id,
                        "rebound_accounts": rebound_accounts,
                    },
                )

                # Check alive proxy percentage.
                px_counts = (
                    await session.execute(
                        select(
                            func.count(Proxy.id).label("total"),
                        ).where(Proxy.tenant_id == tenant_id)
                    )
                ).one()
                px_alive_r = (
                    await session.execute(
                        select(func.count(Proxy.id)).where(
                            Proxy.tenant_id == tenant_id,
                            Proxy.health_status == "alive",
                            Proxy.is_active == True,  # noqa: E712
                        )
                    )
                ).scalar()

                total_px = int(px_counts.total) if px_counts.total is not None else 0
                alive_px = int(px_alive_r) if px_alive_r is not None else 0
                pct_px = int(alive_px * 100 / total_px) if total_px else 100

                cfg_row = (
                    await session.execute(
                        select(AlertConfig).where(
                            AlertConfig.tenant_id == tenant_id,
                            AlertConfig.resource_type == "proxy",
                        )
                    )
                ).scalar_one_or_none()
                threshold_px = int(cfg_row.threshold_percent) if cfg_row else _LOW_PROXY_THRESHOLD_PCT

                summary: dict[str, Any] = {
                    "proxy_id": proxy_id,
                    "rebound_accounts": rebound_accounts,
                    "proxies_alive": alive_px,
                    "proxies_total": total_px,
                    "alive_percent": pct_px,
                }

                if pct_px <= threshold_px:
                    await _create_alert(
                        session,
                        tenant_id=tenant_id,
                        alert_type="low_proxies",
                        severity="critical" if pct_px <= 5 else "warning",
                        message=(
                            f"Живых прокси {alive_px}/{total_px} ({pct_px}%) — "
                            f"ниже порога {threshold_px}%. Рекомендуется докупить прокси."
                        ),
                    )
                    summary["alert_fired"] = True

        log.info(
            "self_healing: handle_proxy_death tenant=%s proxy=%s alive_pct=%s",
            tenant_id,
            proxy_id,
            pct_px,
        )
        return summary

    # ------------------------------------------------------------------
    # 3. FloodWait handler
    # ------------------------------------------------------------------

    async def handle_flood_wait(
        self, tenant_id: int, account_id: int, wait_seconds: int
    ) -> dict[str, Any]:
        """Quarantine account for wait_seconds and redistribute channels."""
        until = utcnow() + timedelta(seconds=wait_seconds)
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)

                await session.execute(
                    update(FarmThread)
                    .where(
                        FarmThread.account_id == account_id,
                        FarmThread.tenant_id == tenant_id,
                    )
                    .values(
                        status="quarantine",
                        quarantine_until=until,
                        stats_last_error=f"FloodWait {wait_seconds}s",
                        updated_at=utcnow(),
                    )
                )

                await _log_healing_action(
                    session,
                    tenant_id=tenant_id,
                    action_type="handle_flood_wait",
                    target_type="account",
                    target_id=account_id,
                    details={"wait_seconds": wait_seconds, "quarantine_until": until.isoformat()},
                )

        log.info(
            "self_healing: handle_flood_wait tenant=%s account=%s wait=%ss",
            tenant_id,
            account_id,
            wait_seconds,
        )
        return {
            "account_id": account_id,
            "quarantine_until": until.isoformat(),
            "wait_seconds": wait_seconds,
        }

    # ------------------------------------------------------------------
    # 4. Account freeze handler
    # ------------------------------------------------------------------

    async def handle_freeze(
        self, tenant_id: int, account_id: int
    ) -> dict[str, Any]:
        """Handle a frozen account.

        Marks the account lifecycle_stage = 'frozen' and sets a 24 h cooldown.
        Channels will be redistributed by the farm orchestrator on its next cycle.
        """
        cooldown_hours = 24
        cooldown_until = utcnow() + timedelta(hours=cooldown_hours)

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)

                acc_result = await session.execute(
                    select(Account).where(
                        Account.id == account_id,
                        Account.tenant_id == tenant_id,
                    )
                )
                account: Account | None = acc_result.scalar_one_or_none()
                if account is None:
                    return {"error": "account_not_found", "account_id": account_id}

                account.lifecycle_stage = "frozen"
                account.cooldown_until = cooldown_until
                account.updated_at = utcnow()

                # Stop farm threads.
                await session.execute(
                    update(FarmThread)
                    .where(
                        FarmThread.account_id == account_id,
                        FarmThread.tenant_id == tenant_id,
                    )
                    .values(status="stopped", updated_at=utcnow())
                )

                await _log_healing_action(
                    session,
                    tenant_id=tenant_id,
                    action_type="handle_freeze",
                    target_type="account",
                    target_id=account_id,
                    details={
                        "cooldown_until": cooldown_until.isoformat(),
                        "cooldown_hours": cooldown_hours,
                    },
                )

        log.info(
            "self_healing: handle_freeze tenant=%s account=%s cooldown_until=%s",
            tenant_id,
            account_id,
            cooldown_until,
        )
        return {
            "account_id": account_id,
            "lifecycle_stage": "frozen",
            "cooldown_until": cooldown_until.isoformat(),
        }

    # ------------------------------------------------------------------
    # 5. Periodic health sweep
    # ------------------------------------------------------------------

    async def run_health_sweep(self, tenant_id: int) -> dict[str, Any]:
        """Check all accounts and proxies; handle discovered problems.

        Returns a report with counts of issues found and actions taken.
        """
        issues_found: list[dict[str, Any]] = []

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)

                # Collect banned accounts not yet marked.
                banned_result = await session.execute(
                    select(Account).where(
                        Account.tenant_id == tenant_id,
                        Account.status == "banned",
                        Account.health_status != "banned",
                    ).limit(5000)
                )
                newly_banned = list(banned_result.scalars().all())
                for acc in newly_banned:
                    acc.health_status = "banned"
                    issues_found.append(
                        {"type": "account_status_sync", "account_id": int(acc.id)}
                    )

                # Collect dead proxies still marked active.
                dead_proxies_result = await session.execute(
                    select(Proxy).where(
                        Proxy.tenant_id == tenant_id,
                        Proxy.health_status == "dead",
                        Proxy.is_active == True,  # noqa: E712
                    ).limit(5000)
                )
                dead_proxies = list(dead_proxies_result.scalars().all())
                for px in dead_proxies:
                    px.is_active = False
                    issues_found.append(
                        {"type": "proxy_deactivated", "proxy_id": int(px.id)}
                    )

                # Auto-lift expired quarantines.
                now = utcnow()
                expired_result = await session.execute(
                    select(FarmThread).where(
                        FarmThread.tenant_id == tenant_id,
                        FarmThread.status == "quarantine",
                        FarmThread.quarantine_until <= now,
                    ).limit(5000)
                )
                expired_threads = list(expired_result.scalars().all())
                for t in expired_threads:
                    t.status = "idle"
                    t.quarantine_until = None
                    t.updated_at = now
                    issues_found.append(
                        {"type": "quarantine_lifted", "thread_id": int(t.id)}
                    )

                await _log_healing_action(
                    session,
                    tenant_id=tenant_id,
                    action_type="health_sweep",
                    target_type="tenant",
                    target_id=tenant_id,
                    details={"issues_found": len(issues_found), "items": issues_found},
                )

        log.info(
            "self_healing: health_sweep tenant=%s issues=%s",
            tenant_id,
            len(issues_found),
        )
        return {
            "tenant_id": tenant_id,
            "issues_found": len(issues_found),
            "details": issues_found,
        }

    # ------------------------------------------------------------------
    # 6. Resource depletion prediction
    # ------------------------------------------------------------------

    async def predict_resource_depletion(
        self, tenant_id: int
    ) -> dict[str, Any]:
        """Estimate days until accounts and proxies are depleted.

        Uses a simple burn-rate model: ban/death events in the last
        _ACCOUNT_BAN_RATE_WINDOW_DAYS days divided by window length.
        """
        from storage.models import HealingAction  # local import avoids circular

        window_start = utcnow() - timedelta(days=_ACCOUNT_BAN_RATE_WINDOW_DAYS)

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)

                # Account totals.
                acc_total = (
                    await session.execute(
                        select(func.count(Account.id)).where(
                            Account.tenant_id == tenant_id
                        )
                    )
                ).scalar() or 0
                acc_alive = (
                    await session.execute(
                        select(func.count(Account.id)).where(
                            Account.tenant_id == tenant_id,
                            Account.status == "active",
                        )
                    )
                ).scalar() or 0

                # Proxy totals.
                px_total = (
                    await session.execute(
                        select(func.count(Proxy.id)).where(
                            Proxy.tenant_id == tenant_id
                        )
                    )
                ).scalar() or 0
                px_alive = (
                    await session.execute(
                        select(func.count(Proxy.id)).where(
                            Proxy.tenant_id == tenant_id,
                            Proxy.health_status == "alive",
                            Proxy.is_active == True,  # noqa: E712
                        )
                    )
                ).scalar() or 0

                # Ban events in window.
                acc_bans_in_window = (
                    await session.execute(
                        select(func.count(HealingAction.id)).where(
                            HealingAction.tenant_id == tenant_id,
                            HealingAction.action_type == "handle_account_ban",
                            HealingAction.created_at >= window_start,
                        )
                    )
                ).scalar() or 0

                # Proxy death events in window.
                px_deaths_in_window = (
                    await session.execute(
                        select(func.count(HealingAction.id)).where(
                            HealingAction.tenant_id == tenant_id,
                            HealingAction.action_type == "handle_proxy_death",
                            HealingAction.created_at >= window_start,
                        )
                    )
                ).scalar() or 0

        # Burn rate = events per day.
        acc_burn_rate = acc_bans_in_window / _ACCOUNT_BAN_RATE_WINDOW_DAYS
        px_burn_rate = px_deaths_in_window / _PROXY_DEATH_RATE_WINDOW_DAYS

        days_accounts = (
            int(acc_alive / acc_burn_rate) if acc_burn_rate > 0 else None
        )
        days_proxies = (
            int(px_alive / px_burn_rate) if px_burn_rate > 0 else None
        )

        return {
            "accounts_alive": int(acc_alive),
            "accounts_total": int(acc_total),
            "accounts_burn_rate_per_day": round(acc_burn_rate, 2),
            "days_until_accounts_depleted": days_accounts,
            "proxies_alive": int(px_alive),
            "proxies_total": int(px_total),
            "proxies_burn_rate_per_day": round(px_burn_rate, 2),
            "days_until_proxies_depleted": days_proxies,
        }
