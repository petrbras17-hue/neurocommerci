"""
Sprint 8 — Intelligence & Scale.

Covers:
  1. Channel Map: list, search, categories, stats (4 tests)
  2. Campaigns: list, create, get, update (4 tests)
  3. Campaigns lifecycle: start, pause, resume, stop, delete (5 tests)
  4. Campaign sub-resources: runs, analytics (2 tests)
  5. Analytics: dashboard, roi (2 tests)
  6. Tenant isolation: channel-map, campaigns (2 tests)
  7. Validation: invalid campaign type (1 test)
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
    AnalyticsEvent,
    AssistantMessage,
    AssistantRecommendation,
    AssistantThread,
    AuthUser,
    BusinessAsset,
    BusinessBrief,
    Campaign,
    CampaignRun,
    ChannelDatabase,
    ChannelEntry,
    ChannelMapEntry,
    ChattingConfig,
    CreativeDraft,
    DialogConfig,
    FarmConfig,
    FarmEvent,
    FarmThread as FarmThreadModel,
    ManualAction,
    ParsingJob,
    ProfileTemplate,
    ReactionJob,
    RefreshToken,
    TeamMember,
    TelegramFolder,
    Tenant,
    UserParsingResult,
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
    *, bot_token: str, telegram_user_id: int, username: str = "isctestuser"
) -> dict[str, Any]:
    auth_date = int(utcnow().timestamp())
    payload: dict[str, Any] = {
        "id": telegram_user_id,
        "auth_date": auth_date,
        "username": username,
        "first_name": "Isc",
        "last_name": "Test",
    }
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    payload["hash"] = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return payload


# ---------------------------------------------------------------------------
# State cleanup
# ---------------------------------------------------------------------------


_MODELS_TO_DELETE = [
    AnalyticsEvent,
    CampaignRun,
    Campaign,
    ChannelMapEntry,
    TelegramFolder,
    UserParsingResult,
    ReactionJob,
    ChattingConfig,
    DialogConfig,
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


async def _reset_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in _MODELS_TO_DELETE:
                await session.execute(delete(model))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def isc_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[misc]
    monkeypatch.setattr(ops_api.settings, "ADMIN_BOT_TOKEN", "isc-test-token-999")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "isc-access-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "isc-refresh-secret-1234567890")
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
    """Register a new tenant via Telegram-first auth. Returns (access_token, tenant_id, workspace_id)."""
    username = f"isc{telegram_user_id}{username_suffix}"
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
            "email": f"isc{telegram_user_id}{username_suffix}@example.com",
            "company": f"IscCo {telegram_user_id}{username_suffix}",
        },
    )
    assert complete.status_code == 200, f"complete-profile failed: {complete.text}"
    body = complete.json()
    return body["access_token"], int(body["tenant"]["id"]), int(body["workspace"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_campaign(
    client: AsyncClient,
    token: str,
    *,
    name: str = "Test Campaign",
    campaign_type: str = "commenting",
) -> int:
    resp = await client.post(
        "/v1/campaigns",
        headers=_auth(token),
        json={"name": name, "campaign_type": campaign_type},
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


# ===========================================================================
# 1. Channel Map
# ===========================================================================


@pytest.mark.asyncio
async def test_list_channel_map_empty(isc_client: AsyncClient) -> None:
    """GET /v1/channel-map with no entries returns 200 with empty list."""
    token, _, _ = await _create_authorized_session(isc_client, 9001)
    resp = await isc_client.get("/v1/channel-map", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_search_channel_map(isc_client: AsyncClient) -> None:
    """POST /v1/channel-map/search returns 200 with items list."""
    token, _, _ = await _create_authorized_session(isc_client, 9002)
    resp = await isc_client.post(
        "/v1/channel-map/search",
        headers=_auth(token),
        json={"query": "tech", "limit": 20},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert isinstance(body["items"], list)


@pytest.mark.asyncio
async def test_get_channel_map_categories(isc_client: AsyncClient) -> None:
    """GET /v1/channel-map/categories returns 200 with categories list."""
    token, _, _ = await _create_authorized_session(isc_client, 9003)
    resp = await isc_client.get("/v1/channel-map/categories", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert "categories" in body
    assert isinstance(body["categories"], list)


@pytest.mark.asyncio
async def test_get_channel_map_stats(isc_client: AsyncClient) -> None:
    """GET /v1/channel-map/stats returns 200 with stat fields."""
    token, _, _ = await _create_authorized_session(isc_client, 9004)
    resp = await isc_client.get("/v1/channel-map/stats", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body


# ===========================================================================
# 2. Campaigns — CRUD
# ===========================================================================


@pytest.mark.asyncio
async def test_list_campaigns_empty(isc_client: AsyncClient) -> None:
    """GET /v1/campaigns for a fresh tenant returns empty list."""
    token, _, _ = await _create_authorized_session(isc_client, 9101)
    resp = await isc_client.get("/v1/campaigns", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_create_campaign(isc_client: AsyncClient) -> None:
    """POST /v1/campaigns creates a campaign and returns it with status=draft."""
    token, _, _ = await _create_authorized_session(isc_client, 9102)
    resp = await isc_client.post(
        "/v1/campaigns",
        headers=_auth(token),
        json={
            "name": "Brand Push March",
            "campaign_type": "commenting",
            "comment_prompt": "Комментируй нативно про AI-платформу",
            "comment_language": "ru",
            "budget_daily_actions": 50,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Brand Push March"
    assert body["status"] == "draft"
    assert body["campaign_type"] == "commenting"
    assert body["budget_daily_actions"] == 50
    assert "id" in body


@pytest.mark.asyncio
async def test_get_campaign(isc_client: AsyncClient) -> None:
    """GET /v1/campaigns/{id} returns the campaign with recent_runs field."""
    token, _, _ = await _create_authorized_session(isc_client, 9103)
    campaign_id = await _create_campaign(isc_client, token, name="GetCampaign")

    resp = await isc_client.get(f"/v1/campaigns/{campaign_id}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == campaign_id
    assert body["name"] == "GetCampaign"
    assert "recent_runs" in body


@pytest.mark.asyncio
async def test_update_campaign(isc_client: AsyncClient) -> None:
    """PUT /v1/campaigns/{id} updates allowed fields on a draft campaign."""
    token, _, _ = await _create_authorized_session(isc_client, 9104)
    campaign_id = await _create_campaign(isc_client, token, name="OriginalName")

    resp = await isc_client.put(
        f"/v1/campaigns/{campaign_id}",
        headers=_auth(token),
        json={"name": "UpdatedName", "budget_daily_actions": 200},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "UpdatedName"
    assert body["budget_daily_actions"] == 200


# ===========================================================================
# 3. Campaigns — Lifecycle
# ===========================================================================


@pytest.mark.asyncio
async def test_start_campaign(isc_client: AsyncClient) -> None:
    """POST /v1/campaigns/{id}/start transitions status to active."""
    token, _, _ = await _create_authorized_session(isc_client, 9201)
    campaign_id = await _create_campaign(isc_client, token, name="StartMe")

    resp = await isc_client.post(f"/v1/campaigns/{campaign_id}/start", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_pause_campaign(isc_client: AsyncClient) -> None:
    """POST /v1/campaigns/{id}/pause transitions status to paused."""
    token, _, _ = await _create_authorized_session(isc_client, 9202)
    campaign_id = await _create_campaign(isc_client, token, name="PauseMe")

    await isc_client.post(f"/v1/campaigns/{campaign_id}/start", headers=_auth(token))

    resp = await isc_client.post(f"/v1/campaigns/{campaign_id}/pause", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "paused"


@pytest.mark.asyncio
async def test_resume_campaign(isc_client: AsyncClient) -> None:
    """POST /v1/campaigns/{id}/resume transitions status back to active."""
    token, _, _ = await _create_authorized_session(isc_client, 9203)
    campaign_id = await _create_campaign(isc_client, token, name="ResumeMe")

    await isc_client.post(f"/v1/campaigns/{campaign_id}/start", headers=_auth(token))
    await isc_client.post(f"/v1/campaigns/{campaign_id}/pause", headers=_auth(token))

    resp = await isc_client.post(f"/v1/campaigns/{campaign_id}/resume", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_stop_campaign(isc_client: AsyncClient) -> None:
    """POST /v1/campaigns/{id}/stop transitions status to completed."""
    token, _, _ = await _create_authorized_session(isc_client, 9204)
    campaign_id = await _create_campaign(isc_client, token, name="StopMe")

    await isc_client.post(f"/v1/campaigns/{campaign_id}/start", headers=_auth(token))

    resp = await isc_client.post(f"/v1/campaigns/{campaign_id}/stop", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"


@pytest.mark.asyncio
async def test_delete_campaign(isc_client: AsyncClient) -> None:
    """DELETE /v1/campaigns/{id} removes a draft campaign (returns 204)."""
    token, _, _ = await _create_authorized_session(isc_client, 9205)
    campaign_id = await _create_campaign(isc_client, token, name="DeleteMe")

    del_resp = await isc_client.delete(f"/v1/campaigns/{campaign_id}", headers=_auth(token))
    assert del_resp.status_code == 204

    list_resp = await isc_client.get("/v1/campaigns", headers=_auth(token))
    assert list_resp.json()["total"] == 0


# ===========================================================================
# 4. Campaign sub-resources
# ===========================================================================


@pytest.mark.asyncio
async def test_list_campaign_runs(isc_client: AsyncClient) -> None:
    """GET /v1/campaigns/{id}/runs returns a list of runs after start."""
    token, _, _ = await _create_authorized_session(isc_client, 9301)
    campaign_id = await _create_campaign(isc_client, token, name="RunsTest")

    await isc_client.post(f"/v1/campaigns/{campaign_id}/start", headers=_auth(token))

    resp = await isc_client.get(f"/v1/campaigns/{campaign_id}/runs", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert body["total"] >= 1


@pytest.mark.asyncio
async def test_get_campaign_analytics(isc_client: AsyncClient) -> None:
    """GET /v1/campaigns/{id}/analytics returns campaign analytics summary."""
    token, _, _ = await _create_authorized_session(isc_client, 9302)
    campaign_id = await _create_campaign(isc_client, token, name="AnalyticsTest")

    resp = await isc_client.get(f"/v1/campaigns/{campaign_id}/analytics", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_id"] == campaign_id
    assert "total_actions_performed" in body
    assert "total_comments_sent" in body
    assert "runs_count" in body


# ===========================================================================
# 5. Analytics endpoints
# ===========================================================================


@pytest.mark.asyncio
async def test_get_analytics_dashboard(isc_client: AsyncClient) -> None:
    """GET /v1/analytics/dashboard returns dashboard data with required fields."""
    token, _, _ = await _create_authorized_session(isc_client, 9401)
    resp = await isc_client.get("/v1/analytics/dashboard?days=7", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert "total_comments" in body
    assert "daily_breakdown" in body


@pytest.mark.asyncio
async def test_get_analytics_roi(isc_client: AsyncClient) -> None:
    """GET /v1/analytics/roi returns ROI metrics."""
    token, _, _ = await _create_authorized_session(isc_client, 9402)
    resp = await isc_client.get("/v1/analytics/roi", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert "total_campaigns" in body
    assert "active_campaigns" in body


# ===========================================================================
# 6. Tenant isolation
# ===========================================================================


@pytest.mark.asyncio
async def test_channel_map_tenant_isolation(isc_client: AsyncClient) -> None:
    """Channel map entries of Tenant A are not visible to Tenant B."""
    token_a, tenant_id_a, _ = await _create_authorized_session(isc_client, 9501)
    token_b, _, _ = await _create_authorized_session(isc_client, 9502)

    async with async_session() as session:
        async with session.begin():
            entry = ChannelMapEntry(
                tenant_id=tenant_id_a,
                username="tenanta_exclusive",
                title="Tenant A Channel",
                category="tech",
                language="ru",
                member_count=5000,
            )
            session.add(entry)

    resp_a = await isc_client.get("/v1/channel-map", headers=_auth(token_a))
    assert resp_a.status_code == 200
    assert resp_a.json()["total"] >= 1

    resp_b = await isc_client.get("/v1/channel-map", headers=_auth(token_b))
    assert resp_b.status_code == 200
    usernames_b = [e["username"] for e in resp_b.json()["items"]]
    assert "tenanta_exclusive" not in usernames_b


@pytest.mark.asyncio
async def test_campaign_tenant_isolation(isc_client: AsyncClient) -> None:
    """Tenant B cannot access or mutate Tenant A's campaign."""
    token_a, _, _ = await _create_authorized_session(isc_client, 9601)
    token_b, _, _ = await _create_authorized_session(isc_client, 9602)

    campaign_id = await _create_campaign(isc_client, token_a, name="A Secret Campaign")

    get_resp = await isc_client.get(f"/v1/campaigns/{campaign_id}", headers=_auth(token_b))
    assert get_resp.status_code == 404

    start_resp = await isc_client.post(
        f"/v1/campaigns/{campaign_id}/start", headers=_auth(token_b)
    )
    assert start_resp.status_code == 404

    del_resp = await isc_client.delete(f"/v1/campaigns/{campaign_id}", headers=_auth(token_b))
    assert del_resp.status_code == 404


# ===========================================================================
# 7. Validation
# ===========================================================================


@pytest.mark.asyncio
async def test_create_campaign_validates_type(isc_client: AsyncClient) -> None:
    """POST /v1/campaigns with an invalid campaign_type returns 422."""
    token, _, _ = await _create_authorized_session(isc_client, 9701)
    resp = await isc_client.post(
        "/v1/campaigns",
        headers=_auth(token),
        json={"name": "Invalid Type Campaign", "campaign_type": "spamming"},
    )
    assert resp.status_code == 422, resp.text
