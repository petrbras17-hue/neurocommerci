"""Smart proxy routing with health-aware assignment strategies.

Tenant-scoped. All queries are filtered by tenant_id.
Uses an already-open AsyncSession passed by the caller (FastAPI DI).
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Account, Proxy
from utils.helpers import utcnow

if TYPE_CHECKING:
    pass


class NoAvailableProxyError(Exception):
    """Raised when no usable proxy can be found for assignment."""


# Health priority order: alive > unknown > failing. Dead proxies are excluded.
_HEALTH_PRIORITY: dict[str, int] = {
    "alive": 0,
    "unknown": 1,
    "failing": 2,
}


def _health_key(proxy: Proxy) -> int:
    """Lower is better."""
    return _HEALTH_PRIORITY.get(str(proxy.health_status or "unknown"), 99)


class ProxyRouter:
    """Smart proxy routing with health-aware assignment."""

    def __init__(self, session: AsyncSession, *, tenant_id: int) -> None:
        self._session = session
        self._tenant_id = tenant_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _usable_proxies(self) -> list[Proxy]:
        """Return all non-dead, active proxies for this tenant ordered by health."""
        result = await self._session.execute(
            select(Proxy)
            .where(
                Proxy.tenant_id == self._tenant_id,
                Proxy.is_active.is_(True),
                Proxy.health_status != "dead",
            )
            .order_by(Proxy.id.asc())
        )
        proxies = list(result.scalars().all())
        return proxies

    async def _binding_counts(self, proxy_ids: list[int]) -> dict[int, int]:
        """Return {proxy_id: bound_account_count} for the given proxy IDs."""
        if not proxy_ids:
            return {}
        result = await self._session.execute(
            select(Account.proxy_id, func.count(Account.id).label("cnt"))
            .where(
                Account.tenant_id == self._tenant_id,
                Account.proxy_id.in_(proxy_ids),
            )
            .group_by(Account.proxy_id)
        )
        return {int(row.proxy_id): int(row.cnt) for row in result}

    async def _account_for_tenant(self, account_id: int) -> Account:
        """Fetch account, enforcing tenant scope. Raises ValueError on miss."""
        result = await self._session.execute(
            select(Account).where(
                Account.id == account_id,
                Account.tenant_id == self._tenant_id,
            )
        )
        account = result.scalar_one_or_none()
        if account is None:
            raise ValueError("account_not_found")
        return account

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def assign_proxy(
        self, account_id: int, *, strategy: str = "healthiest"
    ) -> dict:
        """Auto-assign the best available proxy to *account_id*.

        Strategies:
        - ``healthiest`` — pick the proxy with best health and fewest bindings.
        - ``round_robin`` — pick the least-loaded proxy (cycle evenly).
        - ``random`` — pick a random usable proxy.

        Rules:
        - Proxies with health_status == "dead" are never considered.
        - Proxies already bound to another account are preferred last (1:1 rule).
        - If no usable proxy exists, ``NoAvailableProxyError`` is raised.

        Returns ``{ok, account_id, proxy_id, proxy_host, strategy}``.
        """
        account = await self._account_for_tenant(account_id)

        proxies = await self._usable_proxies()
        if not proxies:
            raise NoAvailableProxyError("no_usable_proxy_in_pool")

        proxy_ids = [int(p.id) for p in proxies]
        counts = await self._binding_counts(proxy_ids)

        # Free proxies = not bound to any account (1:1 preferred).
        free = [p for p in proxies if counts.get(int(p.id), 0) == 0]
        pool = free if free else proxies

        if strategy == "healthiest":
            chosen = min(pool, key=lambda p: (_health_key(p), counts.get(int(p.id), 0)))
        elif strategy == "round_robin":
            # Pick the proxy with fewest bindings, break ties by id.
            chosen = min(pool, key=lambda p: (counts.get(int(p.id), 0), int(p.id)))
        elif strategy == "random":
            chosen = random.choice(pool)
        else:
            raise ValueError(f"unknown_strategy: {strategy}")

        account.proxy_id = int(chosen.id)
        # No flush here — let the endpoint own the transaction.

        return {
            "ok": True,
            "account_id": account_id,
            "proxy_id": int(chosen.id),
            "proxy_host": str(chosen.host),
            "strategy": strategy,
        }

    async def mass_assign(
        self, account_ids: list[int], *, strategy: str = "round_robin"
    ) -> dict:
        """Assign proxies to multiple accounts at once.

        - Accounts that already have a proxy are skipped.
        - Distributes evenly across available proxies.

        Returns ``{assigned: [...], skipped: [...], errors: [...]}``.
        """
        proxies = await self._usable_proxies()
        if not proxies:
            raise NoAvailableProxyError("no_usable_proxy_in_pool")

        proxy_ids = [int(p.id) for p in proxies]
        counts = await self._binding_counts(proxy_ids)

        # Build a mutable sorted list for round-robin distribution.
        sorted_proxies = sorted(proxies, key=lambda p: (counts.get(int(p.id), 0), int(p.id)))

        assigned: list[dict] = []
        skipped: list[int] = []
        errors: list[dict] = []

        rr_index = 0  # pointer for round_robin

        for acct_id in account_ids:
            try:
                result = await self._session.execute(
                    select(Account).where(
                        Account.id == acct_id,
                        Account.tenant_id == self._tenant_id,
                    )
                )
                account = result.scalar_one_or_none()
                if account is None:
                    errors.append({"account_id": acct_id, "reason": "account_not_found"})
                    continue
                if account.proxy_id is not None:
                    skipped.append(acct_id)
                    continue

                if strategy == "healthiest":
                    chosen = min(
                        sorted_proxies,
                        key=lambda p: (_health_key(p), counts.get(int(p.id), 0)),
                    )
                elif strategy == "round_robin":
                    chosen = sorted_proxies[rr_index % len(sorted_proxies)]
                    rr_index += 1
                elif strategy == "random":
                    chosen = random.choice(sorted_proxies)
                else:
                    raise ValueError(f"unknown_strategy: {strategy}")

                account.proxy_id = int(chosen.id)
                counts[int(chosen.id)] = counts.get(int(chosen.id), 0) + 1
                assigned.append({"account_id": acct_id, "proxy_id": int(chosen.id)})

            except Exception as exc:
                errors.append({"account_id": acct_id, "reason": str(exc)})

        return {"assigned": assigned, "skipped": skipped, "errors": errors}

    async def rebind_on_failure(self, account_id: int) -> dict | None:
        """Replace the current proxy for *account_id* after a failure.

        Steps:
        1. Mark the old proxy as failing (increment consecutive_failures).
        2. Find the healthiest free proxy.
        3. Rebind and return the new binding.

        Returns the new binding dict or ``None`` if no replacement is available.
        """
        account = await self._account_for_tenant(account_id)
        old_proxy_id: int | None = int(account.proxy_id) if account.proxy_id else None

        if old_proxy_id is not None:
            old_result = await self._session.execute(
                select(Proxy).where(
                    Proxy.id == old_proxy_id,
                    Proxy.tenant_id == self._tenant_id,
                )
            )
            old_proxy = old_result.scalar_one_or_none()
            if old_proxy is not None:
                old_proxy.consecutive_failures = int(old_proxy.consecutive_failures or 0) + 1
                threshold = 3
                if int(old_proxy.consecutive_failures) >= threshold:
                    old_proxy.health_status = "dead"
                    old_proxy.is_active = False
                    old_proxy.invalidated_at = utcnow()
                else:
                    old_proxy.health_status = "failing"
                account.proxy_id = None

        # Find replacement — exclude the old proxy.
        proxies = await self._usable_proxies()
        proxies = [p for p in proxies if int(p.id) != (old_proxy_id or -1)]
        if not proxies:
            return None

        proxy_ids = [int(p.id) for p in proxies]
        counts = await self._binding_counts(proxy_ids)

        free = [p for p in proxies if counts.get(int(p.id), 0) == 0]
        pool = free if free else proxies
        chosen = min(pool, key=lambda p: (_health_key(p), counts.get(int(p.id), 0)))

        account.proxy_id = int(chosen.id)
        return {
            "ok": True,
            "account_id": account_id,
            "old_proxy_id": old_proxy_id,
            "proxy_id": int(chosen.id),
            "proxy_host": str(chosen.host),
        }

    async def get_proxy_load(self) -> list[dict]:
        """Return proxy utilization stats sorted by bindings_count ascending.

        ``[{proxy_id, host, bindings_count, health_status, last_checked}]``
        """
        proxies = await self._usable_proxies()
        if not proxies:
            return []

        proxy_ids = [int(p.id) for p in proxies]
        counts = await self._binding_counts(proxy_ids)

        rows = [
            {
                "proxy_id": int(p.id),
                "host": str(p.host),
                "port": int(p.port),
                "bindings_count": counts.get(int(p.id), 0),
                "health_status": str(p.health_status or "unknown"),
                "last_checked": p.last_checked.isoformat() if p.last_checked else None,
            }
            for p in proxies
        ]
        rows.sort(key=lambda r: (r["bindings_count"], r["proxy_id"]))
        return rows

    async def cleanup_dead_bindings(self) -> dict:
        """Unbind all dead or inactive proxies from accounts in this tenant.

        Returns ``{unbound_count, affected_accounts}``.
        """
        # Find all dead proxy IDs for this tenant.
        dead_result = await self._session.execute(
            select(Proxy.id).where(
                Proxy.tenant_id == self._tenant_id,
                Proxy.health_status == "dead",
            )
        )
        dead_ids = [int(row[0]) for row in dead_result]

        # Also include inactive proxies regardless of health_status.
        inactive_result = await self._session.execute(
            select(Proxy.id).where(
                Proxy.tenant_id == self._tenant_id,
                Proxy.is_active.is_(False),
            )
        )
        inactive_ids = [int(row[0]) for row in inactive_result]

        all_bad_ids = list(set(dead_ids + inactive_ids))
        if not all_bad_ids:
            return {"unbound_count": 0, "affected_accounts": []}

        # Find accounts bound to bad proxies.
        accounts_result = await self._session.execute(
            select(Account).where(
                Account.tenant_id == self._tenant_id,
                Account.proxy_id.in_(all_bad_ids),
            )
        )
        accounts = list(accounts_result.scalars().all())

        affected_ids: list[int] = []
        for account in accounts:
            account.proxy_id = None
            affected_ids.append(int(account.id))

        return {
            "unbound_count": len(accounts),
            "affected_accounts": affected_ids,
        }
