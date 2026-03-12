"""
Proxy Smart Routing test suite.

Covers:
  1. healthiest strategy selects best proxy
  2. round_robin distributes evenly
  3. 1:1 constraint: free proxies preferred over already-bound ones
  4. NoAvailableProxyError when pool is empty
  5. mass_assign distributes and skips already-proxied accounts
  6. rebind_on_failure replaces the failing proxy
  7. cleanup_dead_bindings unbinds dead/inactive proxies
  8. tenant isolation: cannot assign proxy belonging to another tenant
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

import ops_api
from config import settings
from ops_api import app
from core.proxy_router import NoAvailableProxyError, ProxyRouter
from storage.models import (
    Account,
    AuthUser,
    Proxy,
    RefreshToken,
    TeamMember,
    Tenant,
    Workspace,
)
from storage.sqlite_db import async_session, init_db
from utils.helpers import utcnow


# ---------------------------------------------------------------------------
# Auth helpers (mirrors test_farm_core.py)
# ---------------------------------------------------------------------------


def _telegram_payload(
    *, bot_token: str, telegram_user_id: int, username: str = "proxytestuser"
) -> dict[str, Any]:
    auth_date = int(utcnow().timestamp())
    payload: dict[str, Any] = {
        "id": telegram_user_id,
        "auth_date": auth_date,
        "username": username,
        "first_name": "Proxy",
        "last_name": "Test",
    }
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    payload["hash"] = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return payload


# ---------------------------------------------------------------------------
# Fixtures & cleanup
# ---------------------------------------------------------------------------

_MODELS_TO_DELETE = [
    Account,
    Proxy,
    RefreshToken,
    TeamMember,
    Workspace,
    Tenant,
    AuthUser,
]


async def _reset_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in _MODELS_TO_DELETE:
                await session.execute(delete(model))


@pytest_asyncio.fixture(loop_scope="session")
async def proxy_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[misc]
    monkeypatch.setattr(ops_api.settings, "ADMIN_BOT_TOKEN", "proxy-test-token-123")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "proxy-access-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "proxy-refresh-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "CHANNELS_SPREADSHEET_ID", "")
    monkeypatch.setattr(ops_api.settings, "STATS_SPREADSHEET_ID", "")
    monkeypatch.setattr(ops_api.settings, "GOOGLE_SHEETS_CREDENTIALS_FILE", "")
    await init_db()
    await _reset_state()
    yield
    await _reset_state()


async def _create_authorized_session(
    client: AsyncClient, telegram_user_id: int, username_suffix: str = ""
) -> tuple[str, int, int]:
    username = f"proxytest{telegram_user_id}{username_suffix}"
    verify = await client.post(
        "/auth/telegram/verify",
        json=_telegram_payload(
            bot_token=settings.ADMIN_BOT_TOKEN,
            telegram_user_id=telegram_user_id,
            username=username,
        ),
    )
    assert verify.status_code == 200, f"verify failed: {verify.text}"
    setup_token = verify.json()["setup_token"]

    complete = await client.post(
        "/auth/complete-profile",
        json={
            "setup_token": setup_token,
            "email": f"proxytest{telegram_user_id}{username_suffix}@example.com",
            "company": f"ProxyCo {telegram_user_id}{username_suffix}",
        },
    )
    assert complete.status_code == 200, f"complete-profile failed: {complete.text}"
    body = complete.json()
    return body["access_token"], int(body["tenant"]["id"]), int(body["workspace"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_proxy(
    session, *, tenant_id: int, workspace_id: int, host: str = "1.2.3.4",
    port: int = 1080, health_status: str = "alive", is_active: bool = True,
) -> Proxy:
    proxy = Proxy(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        proxy_type="http",
        host=host,
        port=port,
        username="u",
        password="p",
        is_active=is_active,
        health_status=health_status,
        consecutive_failures=0,
        created_at=utcnow(),
    )
    session.add(proxy)
    await session.flush()
    return proxy


async def _create_account(
    session, *, tenant_id: int, workspace_id: int, phone: str = "+71234567890",
    proxy_id: int | None = None,
) -> Account:
    account = Account(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        phone=phone,
        session_file=f"sessions/{phone}.session",
        status="active",
        health_status="alive",
        lifecycle_stage="active_commenting",
        proxy_id=proxy_id,
        created_at=utcnow(),
    )
    session.add(account)
    await session.flush()
    return account


# ===========================================================================
# 1. healthiest strategy selects best proxy
# ===========================================================================


@pytest.mark.asyncio
async def test_healthiest_picks_alive_proxy(proxy_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9001)

    async with async_session() as session:
        async with session.begin():
            # Two proxies: one "unknown", one "alive" — healthiest must pick "alive"
            p_unknown = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="10.0.0.1", port=1080, health_status="unknown",
            )
            p_alive = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="10.0.0.2", port=1081, health_status="alive",
            )
            account = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id, phone="+79001000001",
            )
            account_id = int(account.id)
            alive_id = int(p_alive.id)

    async with async_session() as session:
        async with session.begin():
            router = ProxyRouter(session, tenant_id=tenant_id)
            result = await router.assign_proxy(account_id, strategy="healthiest")

    assert result["ok"] is True
    assert result["proxy_id"] == alive_id
    assert result["strategy"] == "healthiest"


# ===========================================================================
# 2. round_robin distributes evenly
# ===========================================================================


@pytest.mark.asyncio
async def test_round_robin_distributes_evenly(proxy_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9002)

    async with async_session() as session:
        async with session.begin():
            p1 = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="20.0.0.1", port=1080, health_status="alive",
            )
            p2 = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="20.0.0.2", port=1081, health_status="alive",
            )
            acct1 = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id, phone="+79002000001",
            )
            acct2 = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id, phone="+79002000002",
            )
            a1_id, a2_id = int(acct1.id), int(acct2.id)
            p1_id, p2_id = int(p1.id), int(p2.id)

    # Mass assign with round_robin — each account should get a different proxy.
    async with async_session() as session:
        async with session.begin():
            router = ProxyRouter(session, tenant_id=tenant_id)
            result = await router.mass_assign([a1_id, a2_id], strategy="round_robin")

    assert len(result["assigned"]) == 2
    assigned_proxy_ids = {r["proxy_id"] for r in result["assigned"]}
    assert assigned_proxy_ids == {p1_id, p2_id}, "round_robin must use distinct proxies when available"


# ===========================================================================
# 3. 1:1 constraint: free proxies preferred over already-bound ones
# ===========================================================================


@pytest.mark.asyncio
async def test_free_proxy_preferred_over_bound(proxy_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9003)

    async with async_session() as session:
        async with session.begin():
            p_free = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="30.0.0.1", port=1080, health_status="alive",
            )
            p_busy = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="30.0.0.2", port=1081, health_status="alive",
            )
            # Bind one account to p_busy so it has bindings_count=1.
            await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                phone="+79003000099", proxy_id=int(p_busy.id),
            )
            # New account to be assigned.
            new_account = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id, phone="+79003000001",
            )
            free_id = int(p_free.id)
            new_id = int(new_account.id)

    async with async_session() as session:
        async with session.begin():
            router = ProxyRouter(session, tenant_id=tenant_id)
            result = await router.assign_proxy(new_id, strategy="healthiest")

    assert result["proxy_id"] == free_id, "Must prefer the free (unbound) proxy"


# ===========================================================================
# 4. NoAvailableProxyError when pool is empty
# ===========================================================================


@pytest.mark.asyncio
async def test_no_proxy_available_raises_error(proxy_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9004)

    async with async_session() as session:
        async with session.begin():
            account = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id, phone="+79004000001",
            )
            account_id = int(account.id)

    async with async_session() as session:
        async with session.begin():
            router = ProxyRouter(session, tenant_id=tenant_id)
            with pytest.raises(NoAvailableProxyError):
                await router.assign_proxy(account_id, strategy="healthiest")


@pytest.mark.asyncio
async def test_auto_assign_endpoint_409_when_no_proxy(proxy_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9005)

    async with async_session() as session:
        async with session.begin():
            account = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id, phone="+79005000001",
            )
            account_id = int(account.id)

    resp = await proxy_client.post(
        "/v1/proxies/auto-assign",
        headers=_auth(token),
        json={"account_id": account_id, "strategy": "healthiest"},
    )
    assert resp.status_code == 409, resp.text


# ===========================================================================
# 5. mass_assign distributes and skips already-proxied accounts
# ===========================================================================


@pytest.mark.asyncio
async def test_mass_assign_skips_already_proxied(proxy_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9006)

    async with async_session() as session:
        async with session.begin():
            p1 = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="60.0.0.1", port=1080, health_status="alive",
            )
            # Account already proxied.
            already = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                phone="+79006000001", proxy_id=int(p1.id),
            )
            # Account without proxy.
            fresh = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                phone="+79006000002",
            )
            already_id, fresh_id = int(already.id), int(fresh.id)

    async with async_session() as session:
        async with session.begin():
            router = ProxyRouter(session, tenant_id=tenant_id)
            result = await router.mass_assign([already_id, fresh_id], strategy="round_robin")

    assert already_id in result["skipped"]
    assert any(r["account_id"] == fresh_id for r in result["assigned"])


# ===========================================================================
# 6. rebind_on_failure replaces the failing proxy
# ===========================================================================


@pytest.mark.asyncio
async def test_rebind_on_failure_assigns_new_proxy(proxy_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9007)

    async with async_session() as session:
        async with session.begin():
            old_proxy = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="70.0.0.1", port=1080, health_status="alive",
            )
            new_proxy = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="70.0.0.2", port=1081, health_status="alive",
            )
            account = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                phone="+79007000001", proxy_id=int(old_proxy.id),
            )
            account_id = int(account.id)
            old_proxy_id = int(old_proxy.id)
            new_proxy_id = int(new_proxy.id)

    async with async_session() as session:
        async with session.begin():
            router = ProxyRouter(session, tenant_id=tenant_id)
            result = await router.rebind_on_failure(account_id)

    assert result is not None
    assert result["old_proxy_id"] == old_proxy_id
    assert result["proxy_id"] == new_proxy_id


@pytest.mark.asyncio
async def test_rebind_on_failure_returns_none_when_no_replacement(
    proxy_client: AsyncClient,
) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9008)

    async with async_session() as session:
        async with session.begin():
            only_proxy = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="80.0.0.1", port=1080, health_status="alive",
            )
            account = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                phone="+79008000001", proxy_id=int(only_proxy.id),
            )
            account_id = int(account.id)

    async with async_session() as session:
        async with session.begin():
            router = ProxyRouter(session, tenant_id=tenant_id)
            result = await router.rebind_on_failure(account_id)

    # The only proxy in the pool was the failing one — no replacement available.
    assert result is None


# ===========================================================================
# 7. cleanup_dead_bindings
# ===========================================================================


@pytest.mark.asyncio
async def test_cleanup_dead_bindings(proxy_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9009)

    async with async_session() as session:
        async with session.begin():
            dead_proxy = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="90.0.0.1", port=1080, health_status="dead", is_active=False,
            )
            alive_proxy = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="90.0.0.2", port=1081, health_status="alive",
            )
            dead_account = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                phone="+79009000001", proxy_id=int(dead_proxy.id),
            )
            ok_account = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                phone="+79009000002", proxy_id=int(alive_proxy.id),
            )
            dead_acc_id = int(dead_account.id)
            ok_acc_id = int(ok_account.id)

    async with async_session() as session:
        async with session.begin():
            router = ProxyRouter(session, tenant_id=tenant_id)
            result = await router.cleanup_dead_bindings()

    assert result["unbound_count"] == 1
    assert dead_acc_id in result["affected_accounts"]
    assert ok_acc_id not in result["affected_accounts"]


@pytest.mark.asyncio
async def test_cleanup_dead_endpoint(proxy_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9010)

    resp = await proxy_client.post(
        "/v1/proxies/cleanup-dead",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "unbound_count" in body
    assert "affected_accounts" in body


# ===========================================================================
# 8. Tenant isolation
# ===========================================================================


@pytest.mark.asyncio
async def test_tenant_isolation_assign_proxy(proxy_client: AsyncClient) -> None:
    """Proxy belonging to tenant_a cannot be assigned to account in tenant_b."""
    token_a, tenant_a_id, ws_a = await _create_authorized_session(proxy_client, 9101)
    token_b, tenant_b_id, ws_b = await _create_authorized_session(proxy_client, 9102)

    async with async_session() as session:
        async with session.begin():
            # Proxy belongs to tenant_a only.
            await _create_proxy(
                session, tenant_id=tenant_a_id, workspace_id=ws_a,
                host="100.0.0.1", port=1080, health_status="alive",
            )
            account_b = await _create_account(
                session, tenant_id=tenant_b_id, workspace_id=ws_b,
                phone="+79101000001",
            )
            account_b_id = int(account_b.id)

    # ProxyRouter for tenant_b must not see tenant_a's proxy.
    async with async_session() as session:
        async with session.begin():
            router = ProxyRouter(session, tenant_id=tenant_b_id)
            with pytest.raises(NoAvailableProxyError):
                await router.assign_proxy(account_b_id, strategy="healthiest")


@pytest.mark.asyncio
async def test_tenant_isolation_cannot_assign_other_tenant_account(
    proxy_client: AsyncClient,
) -> None:
    """Cannot assign a proxy to an account owned by a different tenant."""
    token_a, tenant_a_id, ws_a = await _create_authorized_session(proxy_client, 9103)
    _token_b, tenant_b_id, ws_b = await _create_authorized_session(proxy_client, 9104)

    async with async_session() as session:
        async with session.begin():
            await _create_proxy(
                session, tenant_id=tenant_a_id, workspace_id=ws_a,
                host="110.0.0.1", port=1080, health_status="alive",
            )
            account_b = await _create_account(
                session, tenant_id=tenant_b_id, workspace_id=ws_b,
                phone="+79103000001",
            )
            account_b_id = int(account_b.id)

    # Tenant-a's router cannot modify tenant-b's account.
    async with async_session() as session:
        async with session.begin():
            router = ProxyRouter(session, tenant_id=tenant_a_id)
            with pytest.raises(ValueError, match="account_not_found"):
                await router.assign_proxy(account_b_id, strategy="healthiest")


# ===========================================================================
# 9. API endpoint smoke tests
# ===========================================================================


@pytest.mark.asyncio
async def test_auto_assign_endpoint_ok(proxy_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9201)

    async with async_session() as session:
        async with session.begin():
            proxy = await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="200.0.0.1", port=1080, health_status="alive",
            )
            account = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id, phone="+79201000001",
            )
            account_id = int(account.id)
            proxy_id = int(proxy.id)

    resp = await proxy_client.post(
        "/v1/proxies/auto-assign",
        headers=_auth(token),
        json={"account_id": account_id, "strategy": "healthiest"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["proxy_id"] == proxy_id


@pytest.mark.asyncio
async def test_mass_assign_endpoint_ok(proxy_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9202)

    async with async_session() as session:
        async with session.begin():
            await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="201.0.0.1", port=1080, health_status="alive",
            )
            await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="201.0.0.2", port=1081, health_status="alive",
            )
            a1 = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id, phone="+79202000001",
            )
            a2 = await _create_account(
                session, tenant_id=tenant_id, workspace_id=workspace_id, phone="+79202000002",
            )
            ids = [int(a1.id), int(a2.id)]

    resp = await proxy_client.post(
        "/v1/proxies/mass-assign",
        headers=_auth(token),
        json={"account_ids": ids, "strategy": "round_robin"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["assigned"]) == 2
    assert body["skipped"] == []
    assert body["errors"] == []


@pytest.mark.asyncio
async def test_proxy_load_endpoint(proxy_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(proxy_client, 9203)

    async with async_session() as session:
        async with session.begin():
            await _create_proxy(
                session, tenant_id=tenant_id, workspace_id=workspace_id,
                host="202.0.0.1", port=1080, health_status="alive",
            )

    resp = await proxy_client.get("/v1/proxies/load", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert body["total"] >= 1
    first = body["items"][0]
    assert "proxy_id" in first
    assert "bindings_count" in first
    assert "health_status" in first
