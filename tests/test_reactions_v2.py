"""Sprint 23: Reactions v2 + Monitoring — Tests.

Tests monitoring config CRUD, blacklist, account status, throughput,
dashboard summary, and admin auth enforcement.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

import ops_api
from config import settings
from core.web_auth import make_access_token
from ops_api import app
from storage.models import (
    AccountStatusLive, AuthUser, ModuleThroughput,
    ReactionBlacklist, ReactionMonitoringConfig,
    TeamMember, Tenant, Workspace,
)
from storage.sqlite_db import async_session, init_db
from utils.helpers import utcnow


# ── Fixtures ───────────────────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "test-reactions-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "test-reactions-refresh-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "APP_ENV", "test")
    await init_db()
    async with async_session() as session:
        async with session.begin():
            for model in [
                ModuleThroughput, AccountStatusLive,
                ReactionBlacklist, ReactionMonitoringConfig,
                TeamMember, Workspace, Tenant, AuthUser,
            ]:
                await session.execute(delete(model))


async def _create_user(is_admin: bool = True) -> tuple[int, int, int, str]:
    """Create auth_user + tenant + workspace, return (user_id, tenant_id, workspace_id, token)."""
    async with async_session() as session:
        async with session.begin():
            suffix = "admin" if is_admin else "user"
            user = AuthUser(
                email=f"react_{suffix}@test.com",
                first_name="TestReact",
                is_platform_admin=is_admin,
                created_at=utcnow(),
            )
            session.add(user)
            await session.flush()

            tenant = Tenant(name="TestTenant", slug=f"react-{user.id}", status="active")
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
async def test_create_monitoring_config(client: AsyncClient):
    """Creates a monitoring config."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/reactions/monitoring",
        json={
            "channel_id": -1001234567890,
            "channel_title": "Test Channel",
            "reaction_emoji": "\U0001f44d",
            "react_within_seconds": 20,
            "accounts_assigned": [1, 2, 3],
            "max_reactions_per_hour": 50,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["channel_id"] == -1001234567890
    assert data["reaction_emoji"] == "\U0001f44d"
    assert data["react_within_seconds"] == 20
    assert data["max_reactions_per_hour"] == 50


@pytest.mark.asyncio(loop_scope="session")
async def test_list_monitoring_configs(client: AsyncClient):
    """Lists monitoring configs."""
    _, _, _, token = await _create_user(is_admin=True)
    # Create two configs
    await client.post(
        "/v1/admin/reactions/monitoring",
        json={"channel_id": -100111},
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        "/v1/admin/reactions/monitoring",
        json={"channel_id": -100222},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get(
        "/v1/admin/reactions/monitoring",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2


@pytest.mark.asyncio(loop_scope="session")
async def test_update_monitoring_config(client: AsyncClient):
    """Updates a monitoring config."""
    _, _, _, token = await _create_user(is_admin=True)
    create_resp = await client.post(
        "/v1/admin/reactions/monitoring",
        json={"channel_id": -100333, "max_reactions_per_hour": 10},
        headers={"Authorization": f"Bearer {token}"},
    )
    config_id = create_resp.json()["id"]
    resp = await client.put(
        f"/v1/admin/reactions/monitoring/{config_id}",
        json={"max_reactions_per_hour": 99, "reaction_emoji": "\u2764\uFE0F"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["max_reactions_per_hour"] == 99


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_monitoring_config(client: AsyncClient):
    """Deletes a monitoring config."""
    _, _, _, token = await _create_user(is_admin=True)
    create_resp = await client.post(
        "/v1/admin/reactions/monitoring",
        json={"channel_id": -100444},
        headers={"Authorization": f"Bearer {token}"},
    )
    config_id = create_resp.json()["id"]
    resp = await client.delete(
        f"/v1/admin/reactions/monitoring/{config_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_reaction_blacklist_add(client: AsyncClient):
    """Adds a channel to the blacklist."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/reactions/blacklist",
        json={"channel_id": -100555, "channel_title": "Bad Channel", "reason": "spam"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["channel_id"] == -100555
    assert data["reason"] == "spam"


@pytest.mark.asyncio(loop_scope="session")
async def test_reaction_blacklist_duplicate(client: AsyncClient):
    """Duplicate blacklist entry returns 409."""
    _, _, _, token = await _create_user(is_admin=True)
    await client.post(
        "/v1/admin/reactions/blacklist",
        json={"channel_id": -100666},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.post(
        "/v1/admin/reactions/blacklist",
        json={"channel_id": -100666},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio(loop_scope="session")
async def test_reaction_blacklist_remove(client: AsyncClient):
    """Removes a channel from the blacklist."""
    _, _, _, token = await _create_user(is_admin=True)
    await client.post(
        "/v1/admin/reactions/blacklist",
        json={"channel_id": -100777},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.delete(
        "/v1/admin/reactions/blacklist/-100777",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_account_status_live(client: AsyncClient):
    """Monitoring accounts endpoint returns statuses."""
    _, _, ws_id, token = await _create_user(is_admin=True)
    # Seed a status directly
    from core.monitoring_service import update_account_status
    await update_account_status(ws_id, 42, "+79001234567", "warmup", "reading_stories")
    resp = await client.get(
        "/v1/admin/monitoring/accounts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    found = [s for s in data["items"] if s["account_id"] == 42]
    assert len(found) >= 1
    assert found[0]["current_module"] == "warmup"


@pytest.mark.asyncio(loop_scope="session")
async def test_throughput_stats(client: AsyncClient):
    """Throughput endpoint returns data."""
    _, _, ws_id, token = await _create_user(is_admin=True)
    from core.monitoring_service import record_throughput
    await record_throughput(ws_id, "farm", actions=10, errors=1, avg_latency=150)
    resp = await client.get(
        "/v1/admin/monitoring/throughput?hours=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "modules" in data
    farm_mods = [m for m in data["modules"] if m["module"] == "farm"]
    assert len(farm_mods) >= 1
    assert farm_mods[0]["total_actions"] >= 10


@pytest.mark.asyncio(loop_scope="session")
async def test_dashboard_summary(client: AsyncClient):
    """Dashboard endpoint returns summary."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.get(
        "/v1/admin/monitoring/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total_accounts" in data
    assert "active_accounts" in data
    assert "error_rate_percent" in data
    assert "module_breakdown" in data


@pytest.mark.asyncio(loop_scope="session")
async def test_non_admin_blocked(client: AsyncClient):
    """Non-admin user gets 403 on all Sprint 23 endpoints."""
    _, _, _, token = await _create_user(is_admin=False)
    endpoints = [
        ("GET", "/v1/admin/reactions/monitoring"),
        ("POST", "/v1/admin/reactions/monitoring"),
        ("GET", "/v1/admin/reactions/blacklist"),
        ("GET", "/v1/admin/monitoring/accounts"),
        ("GET", "/v1/admin/monitoring/throughput"),
        ("GET", "/v1/admin/monitoring/dashboard"),
    ]
    for method, path in endpoints:
        if method == "GET":
            resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
        else:
            resp = await client.post(path, json={}, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403, f"{method} {path} should be 403, got {resp.status_code}"
