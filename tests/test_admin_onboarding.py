"""Sprint 17: Admin Panel Foundation — Tests.

Tests platform admin auth, onboarding endpoints, proxy management,
and operations log.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

import ops_api
from config import settings
from core.web_auth import make_access_token
from ops_api import app
from storage.models import (
    AdminAccount, AdminOperationLog, AdminProxy,
    AuthUser, TeamMember, Tenant, Workspace,
)
from storage.sqlite_db import async_session, init_db
from utils.helpers import utcnow


# ── Fixtures ───────────────────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def admin_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _admin_clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "test-admin-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "test-admin-refresh-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "APP_ENV", "test")
    await init_db()
    # Clean admin tables
    async with async_session() as session:
        async with session.begin():
            for model in [AdminOperationLog, AdminProxy, AdminAccount,
                          TeamMember, Workspace, Tenant, AuthUser]:
                await session.execute(delete(model))


async def _create_admin_user(is_admin: bool = True) -> tuple[int, int, int, str]:
    """Create auth_user + tenant + workspace, return (user_id, tenant_id, workspace_id, token)."""
    async with async_session() as session:
        async with session.begin():
            user = AuthUser(
                email=f"admin{'_yes' if is_admin else '_no'}@test.com",
                first_name="TestAdmin",
                is_platform_admin=is_admin,
                created_at=utcnow(),
            )
            session.add(user)
            await session.flush()

            tenant = Tenant(name="TestTenant", slug=f"test-{user.id}", status="active")
            session.add(tenant)
            await session.flush()

            ws = Workspace(tenant_id=tenant.id, name="Main", settings={})
            session.add(ws)
            await session.flush()

            tm = TeamMember(
                tenant_id=tenant.id,
                workspace_id=ws.id,
                user_id=user.id,
                role="owner",
            )
            session.add(tm)
            await session.flush()

            token = make_access_token(
                user_id=user.id,
                tenant_id=tenant.id,
                workspace_id=ws.id,
                role="owner",
            )
            return user.id, tenant.id, ws.id, token


# ── Tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_platform_admin_required(admin_client: AsyncClient):
    """Non-admin user gets 403 on admin endpoints."""
    _user_id, _tenant_id, _ws_id, token = await _create_admin_user(is_admin=False)
    resp = await admin_client.get(
        "/v1/admin/onboarding/accounts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "platform_admin_required"


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_can_list_accounts(admin_client: AsyncClient):
    """Admin user can list (empty) accounts."""
    _user_id, _tenant_id, _ws_id, token = await _create_admin_user(is_admin=True)
    resp = await admin_client.get(
        "/v1/admin/onboarding/accounts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_import_proxies(admin_client: AsyncClient):
    """Admin can import proxies."""
    _user_id, _tenant_id, _ws_id, token = await _create_admin_user(is_admin=True)
    resp = await admin_client.post(
        "/v1/admin/proxies/import",
        json={
            "lines": ["1.2.3.4:1080:user1:pass1", "5.6.7.8:1081:user2:pass2"],
            "proxy_type": "socks5",
            "country": "KZ",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 2
    assert len(data["proxies"]) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_list_proxies(admin_client: AsyncClient):
    """Admin can list proxies."""
    _user_id, _tenant_id, _ws_id, token = await _create_admin_user(is_admin=True)
    # Import first
    await admin_client.post(
        "/v1/admin/proxies/import",
        json={"lines": ["10.0.0.1:9000:u:p"], "proxy_type": "socks5"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await admin_client.get(
        "/v1/admin/proxies",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert len(data["items"]) >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_onboarding_stats(admin_client: AsyncClient):
    """Admin can get stats."""
    _user_id, _tenant_id, _ws_id, token = await _create_admin_user(is_admin=True)
    resp = await admin_client.get(
        "/v1/admin/onboarding/stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "accounts" in data
    assert "proxies" in data
    assert "total" in data["accounts"]
    assert "alive" in data["proxies"]


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_operations_log(admin_client: AsyncClient):
    """Admin can view operations log."""
    _user_id, _tenant_id, _ws_id, token = await _create_admin_user(is_admin=True)
    resp = await admin_client.get(
        "/v1/admin/operations-log",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_delete_proxy_bound_fails(admin_client: AsyncClient):
    """Cannot delete bound proxy."""
    _user_id, _tenant_id, ws_id, token = await _create_admin_user(is_admin=True)

    # Create proxy + account manually
    async with async_session() as session:
        async with session.begin():
            proxy = AdminProxy(
                workspace_id=ws_id,
                host="9.9.9.9",
                port=1234,
                status="alive",
                bound_account_id=999,  # fake binding
            )
            session.add(proxy)
            await session.flush()
            proxy_id = proxy.id

    resp = await admin_client.delete(
        f"/v1/admin/proxies/{proxy_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Should fail because proxy is bound
    assert resp.status_code in (400, 500)
