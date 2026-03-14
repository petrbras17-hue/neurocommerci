"""Sprint 24: Farm Launch Orchestration + Anti-Fraud Intelligence — Tests.

Tests launch plan CRUD, scaling curve math, health gating, antifraud scoring,
pattern detection, and admin auth enforcement.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import ops_api
from config import settings
from core.web_auth import make_access_token
from core.farm_launcher import (
    gaussian_delay,
    add_weekly_variation,
    get_current_limit,
)
from ops_api import app
from storage.models import (
    AntifraudScore,
    AuthUser,
    FarmLaunchPlan,
    PatternDetection,
    ScalingHistory,
    TeamMember,
    Tenant,
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
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "test-s24-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "test-s24-refresh-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "APP_ENV", "test")
    await init_db()
    async with async_session() as session:
        async with session.begin():
            for model in [
                ScalingHistory, AntifraudScore, PatternDetection,
                FarmLaunchPlan, TeamMember, Workspace, Tenant, AuthUser,
            ]:
                await session.execute(delete(model))


async def _create_user(is_admin: bool = True) -> tuple:
    """Create auth_user + tenant + workspace, return (user_id, tenant_id, workspace_id, token)."""
    async with async_session() as session:
        async with session.begin():
            user = AuthUser(
                email=f"s24_{'admin' if is_admin else 'user'}_{utcnow().timestamp()}@test.com",
                first_name="TestS24",
                is_platform_admin=is_admin,
                created_at=utcnow(),
            )
            session.add(user)
            await session.flush()

            tenant = Tenant(name="TestTenant", slug=f"s24-{user.id}", status="active")
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


# ── Launch Plan Tests ──────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_create_launch_plan(client: AsyncClient):
    """Creates plan with gradual curve."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/farm/launch-plans",
        json={"farm_id": 1, "name": "Test Plan", "scaling_curve": "gradual"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Plan"
    assert data["scaling_curve"] == "gradual"
    assert data["current_day"] == 0
    assert data["is_active"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_get_current_limit_gradual(client: AsyncClient):
    """Returns correct limit per day on gradual curve."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/farm/launch-plans",
        json={"farm_id": 2, "scaling_curve": "gradual", "day_1_limit": 2, "day_3_limit": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    plan_id = resp.json()["id"]

    # Day 0 -> day_1_limit
    resp = await client.get(
        f"/v1/admin/farm/launch-plans/{plan_id}/current-limit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["current_limit"] == 2

    # Advance to day 3
    for _ in range(3):
        await client.post(
            f"/v1/admin/farm/launch-plans/{plan_id}/advance-day",
            headers={"Authorization": f"Bearer {token}"},
        )
    resp = await client.get(
        f"/v1/admin/farm/launch-plans/{plan_id}/current-limit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json()["current_limit"] == 5


@pytest.mark.asyncio(loop_scope="session")
async def test_get_current_limit_linear():
    """Linear curve math works correctly."""
    plan = FarmLaunchPlan(
        workspace_id=1,
        farm_id=1,
        scaling_curve="linear",
        day_1_limit=2,
        day_30_limit=32,
        current_day=15,
        health_gate_threshold=40,
        auto_reduce_factor=0.5,
    )
    limit = get_current_limit(plan)
    # Linear: 2 + (32 - 2) * 15 / 30 = 2 + 15 = 17
    assert limit == 17


@pytest.mark.asyncio(loop_scope="session")
async def test_get_current_limit_health_gated():
    """Health below threshold reduces limit."""
    plan = FarmLaunchPlan(
        workspace_id=1,
        farm_id=1,
        scaling_curve="gradual",
        day_1_limit=2,
        day_3_limit=5,
        day_7_limit=10,
        day_14_limit=20,
        day_30_limit=-1,
        current_day=7,
        health_gate_threshold=40,
        auto_reduce_factor=0.5,
    )
    # Without health gating
    assert get_current_limit(plan) == 10
    # With health below threshold
    assert get_current_limit(plan, health_score=30) == 5
    # With health above threshold
    assert get_current_limit(plan, health_score=50) == 10


@pytest.mark.asyncio(loop_scope="session")
async def test_advance_day(client: AsyncClient):
    """Increments day counter."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/farm/launch-plans",
        json={"farm_id": 3, "name": "Advance Test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    plan_id = resp.json()["id"]

    resp = await client.post(
        f"/v1/admin/farm/launch-plans/{plan_id}/advance-day",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["current_day"] == 1

    resp = await client.post(
        f"/v1/admin/farm/launch-plans/{plan_id}/advance-day",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json()["current_day"] == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_scaling_history(client: AsyncClient):
    """Records scaling events."""
    _, _, ws_id, token = await _create_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/farm/launch-plans",
        json={"farm_id": 4, "name": "History Test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    plan_id = resp.json()["id"]

    # Record a scaling event directly
    from core.farm_launcher import record_scaling_event
    await record_scaling_event(
        workspace_id=ws_id,
        farm_id=4,
        account_id=100,
        day_number=1,
        max_allowed=2,
        actual_performed=2,
        health_gated=False,
        antifraud_gated=False,
    )

    resp = await client.get(
        f"/v1/admin/farm/launch-plans/{plan_id}/scaling-history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    assert items[0]["farm_id"] == 4


# ── Antifraud Tests ────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_score_action_risk(client: AsyncClient):
    """Returns risk score with factors."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/antifraud/score",
        json={"account_id": 1, "action_type": "comment"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "risk_score" in data
    assert 0.0 <= data["risk_score"] <= 1.0
    assert "risk_factors" in data
    assert data["decision"] in ("proceed", "delay", "skip", "alert")


@pytest.mark.asyncio(loop_scope="session")
async def test_detect_patterns(client: AsyncClient):
    """Pattern detection endpoint works."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/antifraud/detect-patterns",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "detected" in data
    assert "items" in data


@pytest.mark.asyncio(loop_scope="session")
async def test_risk_summary(client: AsyncClient):
    """Summary endpoint returns data."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.get(
        "/v1/admin/antifraud/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total_scores_today" in data
    assert "avg_risk_today" in data
    assert "unresolved_alerts" in data


@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_pattern(client: AsyncClient):
    """Resolving pattern changes is_resolved."""
    _, _, ws_id, token = await _create_user(is_admin=True)

    # Create a pattern manually
    async with async_session() as session:
        async with session.begin():
            det = PatternDetection(
                workspace_id=ws_id,
                pattern_type="identical_timing",
                accounts_involved=[1, 2],
                severity="medium",
                detail="test pattern",
                is_resolved=False,
            )
            session.add(det)
            await session.flush()
            det_id = det.id

    resp = await client.post(
        f"/v1/admin/antifraud/alerts/{det_id}/resolve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["resolved"] is True


# ── Timing Helper Tests ────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_gaussian_delay():
    """Returns positive float."""
    for _ in range(100):
        delay = gaussian_delay(30.0, 10.0)
        assert delay >= 1.0
        assert isinstance(delay, float)


@pytest.mark.asyncio(loop_scope="session")
async def test_weekly_variation():
    """Monday has higher multiplier than Friday."""
    base = 10.0
    monday = add_weekly_variation(base, 0)
    friday = add_weekly_variation(base, 4)
    assert monday > friday
    assert monday == 13.0  # 10 * 1.3
    assert friday == 8.5   # 10 * 0.85


# ── Auth Tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_non_admin_blocked(client: AsyncClient):
    """Non-admin gets 403."""
    _, _, _, token = await _create_user(is_admin=False)
    resp = await client.get(
        "/v1/admin/farm/launch-plans",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "platform_admin_required"
