"""
Sprint 6 — Warmup, Health, and Quarantine test suite.

Covers:
  1. Warmup CRUD (6 tests)
  2. Warmup Control (3 tests)
  3. Health Scores (3 tests)
  4. Quarantine (2 tests)
  5. Tenant Isolation (2 tests)
  6. Auth Guard (1 test)
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import ops_api
from config import settings
from ops_api import app
from storage.models import (
    AIAgentRun,
    AIBudgetCounter,
    AIBudgetLimit,
    AIEscalation,
    AIRequest,
    AIRequestAttempt,
    AITaskPolicy,
    Account,
    AccountHealthScore,
    AssistantMessage,
    AssistantRecommendation,
    AssistantThread,
    AuthUser,
    BusinessAsset,
    BusinessBrief,
    ChannelDatabase,
    ChannelEntry,
    CreativeDraft,
    FarmConfig,
    FarmEvent,
    FarmThread as FarmThreadModel,
    ManualAction,
    ParsingJob,
    ProfileTemplate,
    RefreshToken,
    TeamMember,
    Tenant,
    WarmupConfig,
    WarmupSession,
    Workspace,
)
from storage.sqlite_db import async_session, init_db
from utils.helpers import utcnow


# ---------------------------------------------------------------------------
# Telegram login widget helpers
# ---------------------------------------------------------------------------


def _telegram_payload(
    *, bot_token: str, telegram_user_id: int, username: str = "warmuptestuser"
) -> dict[str, Any]:
    auth_date = int(utcnow().timestamp())
    payload: dict[str, Any] = {
        "id": telegram_user_id,
        "auth_date": auth_date,
        "username": username,
        "first_name": "Warmup",
        "last_name": "Test",
    }
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    payload["hash"] = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return payload


# ---------------------------------------------------------------------------
# State cleanup
# ---------------------------------------------------------------------------

_WARMUP_MODELS_TO_DELETE = [
    FarmEvent,
    FarmThreadModel,
    FarmConfig,
    WarmupSession,
    WarmupConfig,
    AccountHealthScore,
    Account,
    ChannelEntry,
    ChannelDatabase,
    ParsingJob,
    ProfileTemplate,
    AIAgentRun,
    AIBudgetCounter,
    AIBudgetLimit,
    AIEscalation,
    AIRequestAttempt,
    AIRequest,
    AITaskPolicy,
    AssistantRecommendation,
    AssistantMessage,
    AssistantThread,
    BusinessAsset,
    CreativeDraft,
    BusinessBrief,
    ManualAction,
    RefreshToken,
    TeamMember,
    Workspace,
    Tenant,
    AuthUser,
]


async def _reset_warmup_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in _WARMUP_MODELS_TO_DELETE:
                await session.execute(delete(model))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def warmup_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[misc]
    monkeypatch.setattr(ops_api.settings, "ADMIN_BOT_TOKEN", "warmup-test-token-123")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "warmup-access-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "warmup-refresh-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "CHANNELS_SPREADSHEET_ID", "")
    monkeypatch.setattr(ops_api.settings, "STATS_SPREADSHEET_ID", "")
    monkeypatch.setattr(ops_api.settings, "GOOGLE_SHEETS_CREDENTIALS_FILE", "")
    await init_db()
    await _reset_warmup_state()
    yield
    await _reset_warmup_state()


async def _create_authorized_session(
    client: AsyncClient, telegram_user_id: int, username_suffix: str = ""
) -> tuple[str, int, int]:
    """Register a new tenant via Telegram-first auth and return (access_token, tenant_id, workspace_id)."""
    username = f"warmup{telegram_user_id}{username_suffix}"
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
            "email": f"warmup{telegram_user_id}{username_suffix}@example.com",
            "company": f"WarmupCo {telegram_user_id}{username_suffix}",
        },
    )
    assert complete.status_code == 200, f"complete-profile failed: {complete.text}"
    body = complete.json()
    return body["access_token"], int(body["tenant"]["id"]), int(body["workspace"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _warmup_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "Test Warmup",
        "mode": "conservative",
        "safety_limit_actions_per_hour": 5,
        "active_hours_start": 9,
        "active_hours_end": 23,
        "warmup_duration_minutes": 30,
        "interval_between_sessions_hours": 6,
    }
    base.update(overrides)
    return base


# ===========================================================================
# 1. Warmup CRUD
# ===========================================================================


@pytest.mark.asyncio
async def test_warmup_create(warmup_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(warmup_client, 9001)

    resp = await warmup_client.post(
        "/v1/warmup",
        headers=_auth(token),
        json=_warmup_payload(name="Alpha Warmup"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Alpha Warmup"
    assert body["status"] == "stopped"
    assert "id" in body


@pytest.mark.asyncio
async def test_warmup_list(warmup_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(warmup_client, 9002)

    for i in range(2):
        r = await warmup_client.post(
            "/v1/warmup",
            headers=_auth(token),
            json=_warmup_payload(name=f"Warmup {i}"),
        )
        assert r.status_code == 201

    resp = await warmup_client.get("/v1/warmup", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_warmup_get(warmup_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(warmup_client, 9003)

    create_resp = await warmup_client.post(
        "/v1/warmup",
        headers=_auth(token),
        json=_warmup_payload(name="GetWarmup"),
    )
    warmup_id = create_resp.json()["id"]

    resp = await warmup_client.get(f"/v1/warmup/{warmup_id}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == warmup_id
    assert body["name"] == "GetWarmup"
    assert body["status"] == "stopped"
    assert "session_duration_minutes" in body


@pytest.mark.asyncio
async def test_warmup_update(warmup_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(warmup_client, 9004)

    create_resp = await warmup_client.post(
        "/v1/warmup",
        headers=_auth(token),
        json=_warmup_payload(name="Before Update"),
    )
    warmup_id = create_resp.json()["id"]

    update_resp = await warmup_client.put(
        f"/v1/warmup/{warmup_id}",
        headers=_auth(token),
        json={"name": "After Update", "mode": "aggressive"},
    )
    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["name"] == "After Update"
    assert body["mode"] == "aggressive"


@pytest.mark.asyncio
async def test_warmup_delete(warmup_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(warmup_client, 9005)

    create_resp = await warmup_client.post(
        "/v1/warmup",
        headers=_auth(token),
        json=_warmup_payload(name="ToDelete"),
    )
    warmup_id = create_resp.json()["id"]

    del_resp = await warmup_client.delete(f"/v1/warmup/{warmup_id}", headers=_auth(token))
    assert del_resp.status_code == 204

    get_resp = await warmup_client.get(f"/v1/warmup/{warmup_id}", headers=_auth(token))
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_warmup_delete_running_rejected(warmup_client: AsyncClient) -> None:
    """DELETE on a running warmup config must be rejected."""
    token, tenant_id, workspace_id = await _create_authorized_session(warmup_client, 9006)

    create_resp = await warmup_client.post(
        "/v1/warmup",
        headers=_auth(token),
        json=_warmup_payload(name="RunningWarmup"),
    )
    warmup_id = create_resp.json()["id"]

    async with async_session() as session:
        async with session.begin():
            cfg = await session.get(WarmupConfig, warmup_id)
            assert cfg is not None
            cfg.status = "running"

    del_resp = await warmup_client.delete(f"/v1/warmup/{warmup_id}", headers=_auth(token))
    # _v6 handler returns 409 for running warmup; plain handler returns 400
    assert del_resp.status_code in (400, 409)


# ===========================================================================
# 2. Warmup Control
# ===========================================================================


@pytest.mark.asyncio
async def test_warmup_start(warmup_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(warmup_client, 9101)

    create_resp = await warmup_client.post(
        "/v1/warmup",
        headers=_auth(token),
        json=_warmup_payload(name="StartWarmup"),
    )
    warmup_id = create_resp.json()["id"]

    start_resp = await warmup_client.post(
        f"/v1/warmup/{warmup_id}/start", headers=_auth(token)
    )
    assert start_resp.status_code == 200, start_resp.text
    body = start_resp.json()
    # _v6 returns {"warmup_id": ..., "status": "running", ...}
    # plain returns {"status": "starting", "job_id": ...}
    assert body.get("status") in ("running", "starting")


@pytest.mark.asyncio
async def test_warmup_stop(warmup_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(warmup_client, 9102)

    create_resp = await warmup_client.post(
        "/v1/warmup",
        headers=_auth(token),
        json=_warmup_payload(name="StopWarmup"),
    )
    warmup_id = create_resp.json()["id"]

    async with async_session() as session:
        async with session.begin():
            cfg = await session.get(WarmupConfig, warmup_id)
            assert cfg is not None
            cfg.status = "running"

    stop_resp = await warmup_client.post(
        f"/v1/warmup/{warmup_id}/stop", headers=_auth(token)
    )
    assert stop_resp.status_code == 200, stop_resp.text
    body = stop_resp.json()
    assert body.get("status") in ("stopped", "stopping")


@pytest.mark.asyncio
async def test_warmup_sessions_list(warmup_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(warmup_client, 9103)

    create_resp = await warmup_client.post(
        "/v1/warmup",
        headers=_auth(token),
        json=_warmup_payload(name="SessionsWarmup"),
    )
    warmup_id = create_resp.json()["id"]

    resp = await warmup_client.get(
        f"/v1/warmup/{warmup_id}/sessions", headers=_auth(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


# ===========================================================================
# 3. Health Scores
# ===========================================================================


@pytest.mark.asyncio
async def test_health_scores_list_empty(warmup_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(warmup_client, 9201)

    resp = await warmup_client.get("/v1/health/scores", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_health_scores_list_with_data(warmup_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(warmup_client, 9202)

    async with async_session() as session:
        async with session.begin():
            account = Account(
                phone="+79009202001",
                session_file="dummy_9202.session",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                status="active",
            )
            session.add(account)
            await session.flush()

            hs = AccountHealthScore(
                tenant_id=tenant_id,
                account_id=account.id,
                health_score=75,
                survivability_score=80,
                flood_wait_count=2,
                spam_block_count=1,
            )
            session.add(hs)

    resp = await warmup_client.get("/v1/health/scores", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["health_score"] == 75
    assert item["survivability_score"] == 80
    assert item["flood_wait_count"] == 2
    assert item["spam_block_count"] == 1


@pytest.mark.asyncio
async def test_health_score_detail(warmup_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(warmup_client, 9203)

    async with async_session() as session:
        async with session.begin():
            account = Account(
                phone="+79009203001",
                session_file="dummy_9203.session",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                status="active",
            )
            session.add(account)
            await session.flush()
            account_id = account.id

            hs = AccountHealthScore(
                tenant_id=tenant_id,
                account_id=account.id,
                health_score=60,
                survivability_score=70,
                flood_wait_count=3,
                spam_block_count=0,
                factors={"age": 10, "violations": 0},
            )
            session.add(hs)

    resp = await warmup_client.get(
        f"/v1/health/scores/{account_id}", headers=_auth(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["account_id"] == account_id
    assert body["health_score"] == 60
    assert "factors" in body
    assert "recent_events" in body


# ===========================================================================
# 4. Quarantine
# ===========================================================================


@pytest.mark.asyncio
async def test_quarantine_list_empty(warmup_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(warmup_client, 9301)

    resp = await warmup_client.get("/v1/health/quarantine", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_quarantine_lift(warmup_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(warmup_client, 9302)

    async with async_session() as session:
        async with session.begin():
            account = Account(
                phone="+79009302001",
                session_file="dummy_9302.session",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                status="active",
            )
            session.add(account)
            await session.flush()
            account_id = account.id

            farm = FarmConfig(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                name="QuarantineFarm",
                status="running",
            )
            session.add(farm)
            await session.flush()

            thread = FarmThreadModel(
                tenant_id=tenant_id,
                farm_id=farm.id,
                account_id=account.id,
                thread_index=0,
                status="quarantine",
                quarantine_until=utcnow() + timedelta(hours=2),
                health_score=40,
            )
            session.add(thread)

    lift_resp = await warmup_client.post(
        f"/v1/health/quarantine/{account_id}/lift", headers=_auth(token)
    )
    assert lift_resp.status_code == 200, lift_resp.text
    body = lift_resp.json()
    assert body.get("threads_updated", body.get("status")) is not None
    # Accept either _v6 format (threads_updated) or plain format (status)
    assert body.get("threads_updated", 1) >= 1 or body.get("status") == "quarantine_lifted"


# ===========================================================================
# 5. Tenant Isolation
# ===========================================================================


@pytest.mark.asyncio
async def test_warmup_tenant_isolation(warmup_client: AsyncClient) -> None:
    """Tenant B cannot GET, PUT, or DELETE tenant A's warmup config."""
    token_a, _, _ = await _create_authorized_session(warmup_client, 9401)
    token_b, _, _ = await _create_authorized_session(warmup_client, 9402)

    create_resp = await warmup_client.post(
        "/v1/warmup",
        headers=_auth(token_a),
        json=_warmup_payload(name="A Secret Warmup"),
    )
    assert create_resp.status_code == 201
    warmup_id = create_resp.json()["id"]

    get_resp = await warmup_client.get(
        f"/v1/warmup/{warmup_id}", headers=_auth(token_b)
    )
    assert get_resp.status_code == 404

    put_resp = await warmup_client.put(
        f"/v1/warmup/{warmup_id}",
        headers=_auth(token_b),
        json={"name": "Hacked"},
    )
    assert put_resp.status_code == 404

    del_resp = await warmup_client.delete(
        f"/v1/warmup/{warmup_id}", headers=_auth(token_b)
    )
    assert del_resp.status_code == 404


@pytest.mark.asyncio
async def test_health_score_tenant_isolation(warmup_client: AsyncClient) -> None:
    """Tenant B cannot see tenant A's health scores."""
    token_a, tenant_id_a, workspace_id_a = await _create_authorized_session(warmup_client, 9403)
    token_b, _, _ = await _create_authorized_session(warmup_client, 9404)

    async with async_session() as session:
        async with session.begin():
            account = Account(
                phone="+79009403001",
                session_file="dummy_9403.session",
                tenant_id=tenant_id_a,
                workspace_id=workspace_id_a,
                status="active",
            )
            session.add(account)
            await session.flush()

            hs = AccountHealthScore(
                tenant_id=tenant_id_a,
                account_id=account.id,
                health_score=90,
                survivability_score=95,
            )
            session.add(hs)

    resp_b = await warmup_client.get("/v1/health/scores", headers=_auth(token_b))
    assert resp_b.status_code == 200
    assert resp_b.json()["total"] == 0


# ===========================================================================
# 6. Auth Guard
# ===========================================================================


@pytest.mark.asyncio
async def test_warmup_unauthenticated(warmup_client: AsyncClient) -> None:
    """GET /v1/warmup without a token must return 401."""
    resp = await warmup_client.get("/v1/warmup")
    assert resp.status_code in (401, 403)
