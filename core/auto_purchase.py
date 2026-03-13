"""
Auto-Purchase Framework — admin-gated purchasing of proxies and accounts.

Purchase execution is always gated by admin approval.
Provider integrations are stubs: they document the interface but do not
call any real external API.  Replace the stub bodies with real provider
SDKs when credentials become available.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Account, AlertConfig, HealingAction, Proxy, PurchaseRequest
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider ABCs
# ---------------------------------------------------------------------------


class ProxyProvider(ABC):
    """Abstract interface for a proxy reseller."""

    @abstractmethod
    async def purchase_proxies(
        self,
        quantity: int,
        geo: str,
        proxy_type: str,
    ) -> list[dict[str, Any]]:
        """Purchase proxies and return a list of proxy config dicts.

        Each dict must contain: host, port, username, password, proxy_type.
        """
        ...

    @abstractmethod
    async def get_price_estimate(self, quantity: int, geo: str) -> float:
        """Return estimated cost in USD for quantity proxies in geo."""
        ...


class AccountProvider(ABC):
    """Abstract interface for a Telegram account reseller."""

    @abstractmethod
    async def purchase_accounts(
        self,
        quantity: int,
        geo: str,
        account_type: str,
    ) -> list[dict[str, Any]]:
        """Purchase accounts and return a list of account descriptor dicts.

        Each dict must contain: phone, session_string (or session_file path).
        """
        ...

    @abstractmethod
    async def get_price_estimate(self, quantity: int, geo: str) -> float:
        """Return estimated cost in USD for quantity accounts in geo."""
        ...


# ---------------------------------------------------------------------------
# Concrete provider stubs
# ---------------------------------------------------------------------------


class WebshareProvider(ProxyProvider):
    """Stub for Webshare.io proxy purchasing.  Implement with their REST API."""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    async def purchase_proxies(
        self, quantity: int, geo: str, proxy_type: str
    ) -> list[dict[str, Any]]:
        log.warning("WebshareProvider.purchase_proxies: stub — not implemented")
        return []

    async def get_price_estimate(self, quantity: int, geo: str) -> float:
        # Webshare static pricing stub: ~$0.5 per shared proxy/month.
        return quantity * 0.50


class GrizzlySMSProvider(AccountProvider):
    """Stub for GrizzlySMS account purchasing.  Implement with their REST API."""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    async def purchase_accounts(
        self, quantity: int, geo: str, account_type: str
    ) -> list[dict[str, Any]]:
        log.warning("GrizzlySMSProvider.purchase_accounts: stub — not implemented")
        return []

    async def get_price_estimate(self, quantity: int, geo: str) -> float:
        # GrizzlySMS stub pricing: ~$2.00 per RU/KZ account.
        return quantity * 2.00


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_PROXY_PROVIDERS: dict[str, ProxyProvider] = {
    "webshare": WebshareProvider(),
}

_ACCOUNT_PROVIDERS: dict[str, AccountProvider] = {
    "grizzlysms": GrizzlySMSProvider(),
}


def list_providers() -> dict[str, Any]:
    """Return the available provider names grouped by resource type."""
    return {
        "proxy": list(_PROXY_PROVIDERS.keys()),
        "account": list(_ACCOUNT_PROVIDERS.keys()),
    }


# ---------------------------------------------------------------------------
# AutoPurchaseManager
# ---------------------------------------------------------------------------


class AutoPurchaseManager:
    """Admin-gated resource purchase manager.

    Execution flow:
        1. check_resource_levels() — called periodically.
        2. create_purchase_request() — creates a PurchaseRequest in 'pending' state.
        3. approve_purchase() — admin sets status = 'approved'.
        4. execute_purchase() — actually calls the provider stub.
    """

    # ------------------------------------------------------------------
    # 1. Check resource levels
    # ------------------------------------------------------------------

    async def check_resource_levels(self, tenant_id: int) -> dict[str, Any]:
        """Check account and proxy levels; create purchase requests if below thresholds."""
        created_requests: list[int] = []

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)

                # Load alert configs.
                cfg_result = await session.execute(
                    select(AlertConfig).where(AlertConfig.tenant_id == tenant_id)
                )
                configs: dict[str, AlertConfig] = {
                    c.resource_type: c for c in cfg_result.scalars().all()
                }

                for resource_type in ("account", "proxy"):
                    cfg = configs.get(resource_type)
                    if cfg is None or not cfg.auto_purchase_enabled:
                        continue

                    threshold = int(cfg.threshold_percent)

                    if resource_type == "account":
                        from sqlalchemy import func
                        total_r = (
                            await session.execute(
                                select(func.count(Account.id)).where(
                                    Account.tenant_id == tenant_id
                                )
                            )
                        ).scalar() or 0
                        alive_r = (
                            await session.execute(
                                select(func.count(Account.id)).where(
                                    Account.tenant_id == tenant_id,
                                    Account.status == "active",
                                )
                            )
                        ).scalar() or 0
                    else:
                        from sqlalchemy import func
                        total_r = (
                            await session.execute(
                                select(func.count(Proxy.id)).where(
                                    Proxy.tenant_id == tenant_id
                                )
                            )
                        ).scalar() or 0
                        alive_r = (
                            await session.execute(
                                select(func.count(Proxy.id)).where(
                                    Proxy.tenant_id == tenant_id,
                                    Proxy.health_status == "alive",
                                    Proxy.is_active == True,  # noqa: E712
                                )
                            )
                        ).scalar() or 0

                    pct = int(alive_r * 100 / total_r) if total_r else 100
                    if pct > threshold:
                        continue

                    # How many to buy: restore to 110 % of current total.
                    quantity = max(10, int(total_r * 1.1) - int(alive_r))
                    provider_name = (
                        "grizzlysms" if resource_type == "account" else "webshare"
                    )

                    # Skip if there is already an open pending request.
                    pending_check = (
                        await session.execute(
                            select(PurchaseRequest).where(
                                PurchaseRequest.tenant_id == tenant_id,
                                PurchaseRequest.resource_type == resource_type,
                                PurchaseRequest.status == "pending",
                            )
                        )
                    ).scalar_one_or_none()
                    if pending_check:
                        continue

                    req = PurchaseRequest(
                        tenant_id=tenant_id,
                        resource_type=resource_type,
                        quantity=quantity,
                        provider_name=provider_name,
                        status="pending",
                        details={
                            "alive_pct": pct,
                            "threshold": threshold,
                            "alive": int(alive_r),
                            "total": int(total_r),
                        },
                        created_at=utcnow(),
                    )
                    session.add(req)
                    await session.flush()
                    created_requests.append(int(req.id))
                    log.info(
                        "auto_purchase: created purchase_request id=%s resource=%s qty=%s",
                        req.id,
                        resource_type,
                        quantity,
                    )

        return {"created_requests": created_requests}

    # ------------------------------------------------------------------
    # 2. Create purchase request (manual)
    # ------------------------------------------------------------------

    async def create_purchase_request(
        self,
        tenant_id: int,
        resource_type: str,
        quantity: int,
        provider_name: str,
        requested_by: int | None = None,
        estimated_cost_usd: float | None = None,
        details: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> PurchaseRequest:
        """Create a manual purchase request.  Returns the new row."""
        async def _do(s: AsyncSession) -> PurchaseRequest:
            await apply_session_rls_context(s, tenant_id=tenant_id)
            req = PurchaseRequest(
                tenant_id=tenant_id,
                resource_type=resource_type,
                quantity=quantity,
                provider_name=provider_name,
                status="pending",
                requested_by=requested_by,
                estimated_cost_usd=estimated_cost_usd,
                details=details or {},
                created_at=utcnow(),
            )
            s.add(req)
            await s.flush()
            return req

        if session is not None:
            return await _do(session)

        async with async_session() as s:
            async with s.begin():
                return await _do(s)

    # ------------------------------------------------------------------
    # 3. Approve purchase
    # ------------------------------------------------------------------

    async def approve_purchase(
        self,
        request_id: int,
        approved_by: int,
        session: AsyncSession,
    ) -> PurchaseRequest:
        """Set status = 'approved'.  Must be called inside an active transaction."""
        req: PurchaseRequest | None = await session.get(PurchaseRequest, request_id)
        if req is None:
            raise ValueError(f"purchase_request {request_id} not found")
        if req.status != "pending":
            raise ValueError(
                f"purchase_request {request_id} is {req.status}, cannot approve"
            )
        req.status = "approved"
        req.approved_by = approved_by
        req.approved_at = utcnow()
        await session.flush()
        log.info("auto_purchase: approved request_id=%s by user=%s", request_id, approved_by)
        return req

    # ------------------------------------------------------------------
    # 4. Reject purchase
    # ------------------------------------------------------------------

    async def reject_purchase(
        self,
        request_id: int,
        rejected_by: int,
        session: AsyncSession,
    ) -> PurchaseRequest:
        """Set status = 'rejected'.  Must be called inside an active transaction."""
        req: PurchaseRequest | None = await session.get(PurchaseRequest, request_id)
        if req is None:
            raise ValueError(f"purchase_request {request_id} not found")
        if req.status not in ("pending",):
            raise ValueError(
                f"purchase_request {request_id} is {req.status}, cannot reject"
            )
        req.status = "rejected"
        req.approved_by = rejected_by  # reusing approved_by as "decided_by"
        req.approved_at = utcnow()
        await session.flush()
        log.info("auto_purchase: rejected request_id=%s by user=%s", request_id, rejected_by)
        return req

    # ------------------------------------------------------------------
    # 5. Execute purchase (approved requests only)
    # ------------------------------------------------------------------

    async def execute_purchase(self, request_id: int, tenant_id: int) -> dict[str, Any]:
        """Execute an approved purchase request via the configured provider stub."""
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                req: PurchaseRequest | None = await session.get(PurchaseRequest, request_id)
                if req is None:
                    return {"error": "not_found", "request_id": request_id}
                if req.status != "approved":
                    return {"error": "not_approved", "status": req.status}

                req.status = "completed"
                req.completed_at = utcnow()

                # Call provider stub.
                result: list[dict[str, Any]] = []
                try:
                    if req.resource_type == "proxy":
                        provider = _PROXY_PROVIDERS.get(req.provider_name)
                        if provider:
                            result = await provider.purchase_proxies(
                                quantity=int(req.quantity),
                                geo="ru",
                                proxy_type="socks5",
                            )
                    elif req.resource_type == "account":
                        provider_a = _ACCOUNT_PROVIDERS.get(req.provider_name)
                        if provider_a:
                            result = await provider_a.purchase_accounts(
                                quantity=int(req.quantity),
                                geo="ru",
                                account_type="fresh",
                            )
                except Exception as exc:
                    req.status = "failed"
                    log.error(
                        "auto_purchase: execute failed request_id=%s error=%s",
                        request_id,
                        exc,
                    )
                    return {"error": str(exc), "request_id": request_id}

                if req.details is None:
                    req.details = {}
                req.details = {**req.details, "result_count": len(result)}

        log.info(
            "auto_purchase: executed request_id=%s resource=%s qty=%s items=%s",
            request_id,
            req.resource_type,
            req.quantity,
            len(result),
        )
        return {
            "request_id": request_id,
            "status": req.status,
            "items_received": len(result),
        }
