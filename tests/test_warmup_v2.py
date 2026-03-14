"""Sprint 19: Warmup v2 + Operation Logs — Tests.

Tests operation log CRUD, warmup schedule update, progress endpoint,
admin-only gating, and stats aggregation.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import ops_api
from config import settings
from core.web_auth import make_access_token
from ops_api import app
from storage.models import (
    AdminOperationLog,
    AuthUser,
    OperationLog,
    TeamMember,
    Tenant,
    WarmupConfig,
    WarmupSession,
    Workspace,
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
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "test-warmup-v2-secret")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "test-warmup-v2-refresh")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "APP_ENV", "test")
    await init_db()
    async with async_session() as session:
        async with session.begin():
            for model in [
                OperationLog,
                WarmupSession,
                WarmupConfig,
                TeamMember,
                Workspace,
                Tenant,
                AuthUser,
            ]:
                await session.execute(delete(model))


async def _create_admin_user(is_admin: bool = True) -> tuple[int, int, int, str]:
    """Create auth_user + tenant + workspace, return (user_id, tenant_id, workspace_id, token)."""
    async with async_session() as session:
        async with session.begin():
            user = AuthUser(
                email=f"warmup_admin{'_yes' if is_admin else '_no'}@test.com",
                first_name="TestWarmup",
                is_platform_admin=is_admin,
                created_at=utcnow(),
            )
            session.add(user)
            await session.flush()

            tenant = Tenant(name="WarmupTenant", slug=f"warmup-{user.id}", status="active")
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


async def _create_warmup_config(tenant_id: int, workspace_id: int) -> int:
    """Create a warmup config and return its id."""
    async with async_session() as session:
        async with session.begin():
            cfg = WarmupConfig(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                name="Test Warmup",
                status="stopped",
                mode="conservative",
            )
            session.add(cfg)
            await session.flush()
            return cfg.id


async def _create_warmup_session(tenant_id: int, warmup_id: int, account_id: int = 1) -> int:
    """Create a warmup session and return its id."""
    async with async_session() as session:
        async with session.begin():
            ws = WarmupSession(
                tenant_id=tenant_id,
                warmup_id=warmup_id,
                account_id=account_id,
                status="running",
                actions_completed=5,
                channels_visited=3,
                stories_viewed=2,
                channels_joined=1,
                days_warmed=1,
                progress_pct=45,
            )
            session.add(ws)
            await session.flush()
            return ws.id


async def _create_operation_log(workspace_id: int, module: str = "warmup", status: str = "success") -> int:
    """Create an operation log entry and return its id."""
    async with async_session() as session:
        async with session.begin():
            entry = OperationLog(
                workspace_id=workspace_id,
                account_id=1,
                module=module,
                action="test_action",
                status=status,
                detail="test detail",
            )
            session.add(entry)
            await session.flush()
            return entry.id


# ── Tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_operation_log_creation(client: AsyncClient):
    """log_operation creates a record in the DB."""
    from core.operation_logger import log_operation

    _, _, ws_id, _ = await _create_admin_user()
    await log_operation(
        workspace_id=ws_id,
        account_id=42,
        module="warmup",
        action="story_viewed",
        status="success",
        detail="viewed 3 stories",
    )
    async with async_session() as session:
        async with session.begin():
            row = (
                await session.execute(
                    select(OperationLog).where(
                        OperationLog.workspace_id == ws_id,
                        OperationLog.action == "story_viewed",
                    )
                )
            ).scalar_one_or_none()
            assert row is not None
            assert row.module == "warmup"
            assert row.status == "success"
            assert row.account_id == 42


@pytest.mark.asyncio(loop_scope="session")
async def test_operation_logs_list(client: AsyncClient):
    """GET /v1/admin/operation-logs returns items."""
    _, tenant_id, ws_id, token = await _create_admin_user()
    await _create_operation_log(ws_id, module="warmup", status="success")
    await _create_operation_log(ws_id, module="farm", status="error")

    resp = await client.get(
        "/v1/admin/operation-logs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] >= 2


@pytest.mark.asyncio(loop_scope="session")
async def test_operation_logs_filter_by_module(client: AsyncClient):
    """Filtering by module works."""
    _, tenant_id, ws_id, token = await _create_admin_user()
    await _create_operation_log(ws_id, module="warmup")
    await _create_operation_log(ws_id, module="parser")

    resp = await client.get(
        "/v1/admin/operation-logs?module=warmup",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["module"] == "warmup"


@pytest.mark.asyncio(loop_scope="session")
async def test_warmup_schedule_update(client: AsyncClient):
    """PUT /v1/warmup/{config_id}/schedule updates settings."""
    _, tenant_id, ws_id, token = await _create_admin_user()
    config_id = await _create_warmup_config(tenant_id, ws_id)

    resp = await client.put(
        f"/v1/warmup/{config_id}/schedule",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "schedule_start_hour": 10,
            "schedule_end_hour": 20,
            "sessions_per_day": 5,
            "enable_story_viewing": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify in DB
    async with async_session() as session:
        async with session.begin():
            cfg = (
                await session.execute(
                    select(WarmupConfig).where(WarmupConfig.id == config_id)
                )
            ).scalar_one()
            assert cfg.schedule_start_hour == 10
            assert cfg.schedule_end_hour == 20
            assert cfg.sessions_per_day == 5
            assert cfg.enable_story_viewing is False


@pytest.mark.asyncio(loop_scope="session")
async def test_warmup_progress(client: AsyncClient):
    """GET /v1/warmup/{config_id}/progress returns session stats."""
    _, tenant_id, ws_id, token = await _create_admin_user()
    config_id = await _create_warmup_config(tenant_id, ws_id)
    await _create_warmup_session(tenant_id, config_id, account_id=1)

    resp = await client.get(
        f"/v1/warmup/{config_id}/progress",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["config_id"] == config_id
    assert data["total"] >= 1
    s = data["sessions"][0]
    assert s["actions_completed"] == 5
    assert s["stories_viewed"] == 2
    assert s["progress_pct"] == 45


@pytest.mark.asyncio(loop_scope="session")
async def test_non_admin_blocked(client: AsyncClient):
    """Non-admin user gets 403 on admin operation-logs endpoints."""
    _, _, _, token = await _create_admin_user(is_admin=False)
    resp = await client.get(
        "/v1/admin/operation-logs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_operation_logs_stats(client: AsyncClient):
    """Stats endpoint returns counts by module and status."""
    _, tenant_id, ws_id, token = await _create_admin_user()
    await _create_operation_log(ws_id, module="warmup", status="success")
    await _create_operation_log(ws_id, module="warmup", status="error")
    await _create_operation_log(ws_id, module="farm", status="success")

    resp = await client.get(
        "/v1/admin/operation-logs/stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "stats" in data
    assert len(data["stats"]) >= 2
