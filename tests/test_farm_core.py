"""
Sprint 5 — Farm Core test suite.

Covers:
  1. Farm CRUD API
  2. Channel Database API
  3. Tenant isolation
  4. FarmThread state machine (unit)
  5. Profile template API
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta
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
    Workspace,
)
from storage.sqlite_db import async_session, init_db
from utils.helpers import utcnow


# ---------------------------------------------------------------------------
# Telegram login widget helpers (mirrors test_web_assistant.py)
# ---------------------------------------------------------------------------


def _telegram_payload(
    *, bot_token: str, telegram_user_id: int, username: str = "farmtestuser"
) -> dict[str, Any]:
    auth_date = int(utcnow().timestamp())
    payload: dict[str, Any] = {
        "id": telegram_user_id,
        "auth_date": auth_date,
        "username": username,
        "first_name": "Farm",
        "last_name": "Test",
    }
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    payload["hash"] = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return payload


# ---------------------------------------------------------------------------
# State cleanup
# ---------------------------------------------------------------------------

_FARM_MODELS_TO_DELETE = [
    FarmEvent,
    FarmThreadModel,
    FarmConfig,
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


async def _reset_farm_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in _FARM_MODELS_TO_DELETE:
                await session.execute(delete(model))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def farm_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[misc]
    monkeypatch.setattr(ops_api.settings, "ADMIN_BOT_TOKEN", "farm-test-token-123")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "farm-access-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "farm-refresh-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "CHANNELS_SPREADSHEET_ID", "")
    monkeypatch.setattr(ops_api.settings, "STATS_SPREADSHEET_ID", "")
    monkeypatch.setattr(ops_api.settings, "GOOGLE_SHEETS_CREDENTIALS_FILE", "")
    await init_db()
    await _reset_farm_state()
    yield
    await _reset_farm_state()


async def _create_authorized_session(
    client: AsyncClient, telegram_user_id: int, username_suffix: str = ""
) -> tuple[str, int, int]:
    """Register a new tenant via Telegram-first auth and return (access_token, tenant_id, workspace_id)."""
    username = f"farm{telegram_user_id}{username_suffix}"
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
            "email": f"farm{telegram_user_id}{username_suffix}@example.com",
            "company": f"FarmCo {telegram_user_id}{username_suffix}",
        },
    )
    assert complete.status_code == 200, f"complete-profile failed: {complete.text}"
    body = complete.json()
    return body["access_token"], int(body["tenant"]["id"]), int(body["workspace"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _farm_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "Test Farm",
        "comment_tone": "neutral",
        "comment_language": "auto",
        "max_threads": 5,
    }
    base.update(overrides)
    return base


# ===========================================================================
# 1. Farm CRUD API
# ===========================================================================


@pytest.mark.asyncio
async def test_create_farm(farm_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(farm_client, 8001)

    resp = await farm_client.post(
        "/v1/farm",
        headers=_auth(token),
        json=_farm_payload(name="Alpha Farm"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Alpha Farm"
    assert body["status"] == "stopped"
    assert "id" in body


@pytest.mark.asyncio
async def test_list_farms(farm_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(farm_client, 8002)

    # Create two farms
    for i in range(2):
        r = await farm_client.post(
            "/v1/farm",
            headers=_auth(token),
            json=_farm_payload(name=f"Farm {i}"),
        )
        assert r.status_code == 201

    resp = await farm_client.get("/v1/farm", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_list_farms_tenant_isolation_count(farm_client: AsyncClient) -> None:
    """A tenant only sees their own farms in the list."""
    token_a, _, _ = await _create_authorized_session(farm_client, 8003)
    token_b, _, _ = await _create_authorized_session(farm_client, 8004)

    await farm_client.post("/v1/farm", headers=_auth(token_a), json=_farm_payload(name="A Farm"))
    await farm_client.post("/v1/farm", headers=_auth(token_a), json=_farm_payload(name="A Farm 2"))
    await farm_client.post("/v1/farm", headers=_auth(token_b), json=_farm_payload(name="B Farm"))

    resp_a = await farm_client.get("/v1/farm", headers=_auth(token_a))
    resp_b = await farm_client.get("/v1/farm", headers=_auth(token_b))

    assert resp_a.json()["total"] == 2
    assert resp_b.json()["total"] == 1


@pytest.mark.asyncio
async def test_get_farm(farm_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(farm_client, 8005)

    create_resp = await farm_client.post(
        "/v1/farm", headers=_auth(token), json=_farm_payload(name="GetFarm")
    )
    farm_id = create_resp.json()["id"]

    resp = await farm_client.get(f"/v1/farm/{farm_id}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == farm_id
    assert "threads_summary" in body
    assert body["threads_summary"]["total"] == 0
    assert "recent_events" in body


@pytest.mark.asyncio
async def test_update_farm(farm_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(farm_client, 8006)

    create_resp = await farm_client.post(
        "/v1/farm", headers=_auth(token), json=_farm_payload(name="Before Update")
    )
    farm_id = create_resp.json()["id"]

    update_resp = await farm_client.put(
        f"/v1/farm/{farm_id}",
        headers=_auth(token),
        json={"name": "After Update", "comment_tone": "hater"},
    )
    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["name"] == "After Update"
    assert body["comment_tone"] == "hater"


@pytest.mark.asyncio
async def test_delete_stopped_farm(farm_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(farm_client, 8007)

    create_resp = await farm_client.post(
        "/v1/farm", headers=_auth(token), json=_farm_payload(name="ToDelete")
    )
    farm_id = create_resp.json()["id"]

    del_resp = await farm_client.delete(f"/v1/farm/{farm_id}", headers=_auth(token))
    assert del_resp.status_code == 204

    get_resp = await farm_client.get(f"/v1/farm/{farm_id}", headers=_auth(token))
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_running_farm_rejected(farm_client: AsyncClient) -> None:
    """DELETE on a non-stopped farm must return 400."""
    token, tenant_id, workspace_id = await _create_authorized_session(farm_client, 8008)

    create_resp = await farm_client.post(
        "/v1/farm", headers=_auth(token), json=_farm_payload(name="RunningFarm")
    )
    farm_id = create_resp.json()["id"]

    # Directly set status to "running" in the DB
    async with async_session() as session:
        async with session.begin():
            farm = await session.get(FarmConfig, farm_id)
            assert farm is not None
            farm.status = "running"

    del_resp = await farm_client.delete(f"/v1/farm/{farm_id}", headers=_auth(token))
    assert del_resp.status_code == 400
    assert "farm_must_be_stopped_before_deletion" in del_resp.json()["detail"]


# ===========================================================================
# 2. Channel Database API
# ===========================================================================


@pytest.mark.asyncio
async def test_create_channel_db(farm_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(farm_client, 8101)

    resp = await farm_client.post(
        "/v1/channel-db",
        headers=_auth(token),
        json={"name": "My Channel DB"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "My Channel DB"
    assert "id" in body


@pytest.mark.asyncio
async def test_import_channels(farm_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(farm_client, 8102)

    create_resp = await farm_client.post(
        "/v1/channel-db",
        headers=_auth(token),
        json={"name": "Import DB"},
    )
    db_id = create_resp.json()["id"]

    import_resp = await farm_client.post(
        f"/v1/channel-db/{db_id}/import",
        headers=_auth(token),
        json={
            "channels": [
                {"username": "techcorp", "title": "Tech Corp", "has_comments": True},
                {"username": "marketingnews", "title": "Marketing News", "has_comments": True},
                {"username": "startupworld", "title": "Startup World", "has_comments": False},
            ]
        },
    )
    assert import_resp.status_code == 200
    body = import_resp.json()
    assert body["imported"] == 3
    assert body["skipped"] == 0


@pytest.mark.asyncio
async def test_import_channels_deduplicates(farm_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(farm_client, 8103)

    create_resp = await farm_client.post(
        "/v1/channel-db", headers=_auth(token), json={"name": "Dedup DB"}
    )
    db_id = create_resp.json()["id"]

    payload = {"channels": [{"username": "dupchannel", "title": "Dup Channel", "has_comments": True}]}

    first = await farm_client.post(f"/v1/channel-db/{db_id}/import", headers=_auth(token), json=payload)
    assert first.json()["imported"] == 1

    second = await farm_client.post(f"/v1/channel-db/{db_id}/import", headers=_auth(token), json=payload)
    assert second.json()["imported"] == 0
    assert second.json()["skipped"] == 1


@pytest.mark.asyncio
async def test_list_channels(farm_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(farm_client, 8104)

    create_resp = await farm_client.post(
        "/v1/channel-db", headers=_auth(token), json={"name": "List DB"}
    )
    db_id = create_resp.json()["id"]

    await farm_client.post(
        f"/v1/channel-db/{db_id}/import",
        headers=_auth(token),
        json={
            "channels": [
                {"username": "chan1", "title": "Channel 1", "has_comments": True},
                {"username": "chan2", "title": "Channel 2", "has_comments": True},
            ]
        },
    )

    resp = await farm_client.get(f"/v1/channel-db/{db_id}/channels", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_blacklist_channel_toggles(farm_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(farm_client, 8105)

    create_resp = await farm_client.post(
        "/v1/channel-db", headers=_auth(token), json={"name": "BL DB"}
    )
    db_id = create_resp.json()["id"]

    await farm_client.post(
        f"/v1/channel-db/{db_id}/import",
        headers=_auth(token),
        json={"channels": [{"username": "toggler", "title": "Toggler", "has_comments": True}]},
    )

    channels_resp = await farm_client.get(
        f"/v1/channel-db/{db_id}/channels", headers=_auth(token)
    )
    channel_id = channels_resp.json()["items"][0]["id"]
    assert channels_resp.json()["items"][0]["blacklisted"] is False

    # Toggle ON
    bl_resp = await farm_client.post(
        f"/v1/channel-db/{db_id}/channels/{channel_id}/blacklist",
        headers=_auth(token),
    )
    assert bl_resp.status_code == 200
    assert bl_resp.json()["blacklisted"] is True

    # Toggle OFF
    bl_resp2 = await farm_client.post(
        f"/v1/channel-db/{db_id}/channels/{channel_id}/blacklist",
        headers=_auth(token),
    )
    assert bl_resp2.json()["blacklisted"] is False


# ===========================================================================
# 3. Tenant isolation
# ===========================================================================


@pytest.mark.asyncio
async def test_farm_cross_tenant_rejected(farm_client: AsyncClient) -> None:
    """Tenant B cannot GET/PUT/DELETE tenant A's farm."""
    token_a, _, _ = await _create_authorized_session(farm_client, 8201)
    token_b, _, _ = await _create_authorized_session(farm_client, 8202)

    create_resp = await farm_client.post(
        "/v1/farm", headers=_auth(token_a), json=_farm_payload(name="A's Secret Farm")
    )
    farm_id = create_resp.json()["id"]

    # B tries GET
    get_resp = await farm_client.get(f"/v1/farm/{farm_id}", headers=_auth(token_b))
    assert get_resp.status_code == 404

    # B tries PUT
    put_resp = await farm_client.put(
        f"/v1/farm/{farm_id}", headers=_auth(token_b), json={"name": "Hacked"}
    )
    assert put_resp.status_code == 404

    # B tries DELETE
    del_resp = await farm_client.delete(f"/v1/farm/{farm_id}", headers=_auth(token_b))
    assert del_resp.status_code == 404


@pytest.mark.asyncio
async def test_channel_db_cross_tenant_rejected(farm_client: AsyncClient) -> None:
    """Tenant B cannot access tenant A's channel database."""
    token_a, _, _ = await _create_authorized_session(farm_client, 8203)
    token_b, _, _ = await _create_authorized_session(farm_client, 8204)

    create_resp = await farm_client.post(
        "/v1/channel-db", headers=_auth(token_a), json={"name": "A's DB"}
    )
    db_id = create_resp.json()["id"]

    # B tries GET single DB
    get_resp = await farm_client.get(f"/v1/channel-db/{db_id}", headers=_auth(token_b))
    assert get_resp.status_code == 404

    # B tries import into A's DB
    import_resp = await farm_client.post(
        f"/v1/channel-db/{db_id}/import",
        headers=_auth(token_b),
        json={"channels": [{"username": "evilchan", "title": "Evil", "has_comments": True}]},
    )
    assert import_resp.status_code == 404

    # B tries list channels in A's DB
    channels_resp = await farm_client.get(
        f"/v1/channel-db/{db_id}/channels", headers=_auth(token_b)
    )
    assert channels_resp.status_code == 404


# ===========================================================================
# 4. FarmThread state machine (unit tests — no HTTP, no DB, no Telethon)
# ===========================================================================


class _DummyFarmConfig:
    """Plain-Python stand-in for FarmConfig used in unit tests (no DB/ORM)."""

    def __init__(self) -> None:
        self.delay_before_comment_min = 0
        self.delay_before_comment_max = 0
        self.delay_before_join_min = 0
        self.delay_before_join_max = 0
        self.comment_prompt = ""
        self.comment_tone = "neutral"
        self.comment_language = "auto"
        self.comment_percentage = 100


class _FakeRedis:
    """Minimal Redis stub for FarmThread unit tests."""

    async def get(self, key: str) -> None:
        return None

    async def set(self, key: str, value: str, **kwargs: Any) -> None:
        pass

    async def publish(self, channel: str, message: str) -> None:
        pass


async def _noop_publish(**kwargs: Any) -> None:
    pass


def _build_farm_thread(thread_id: int = 1, farm_id: int = 1, tenant_id: int = 1) -> Any:
    from core.farm_thread import FarmThread

    async def _fake_route(*args: Any, **kwargs: Any) -> Any:
        return None

    return FarmThread(
        thread_id=thread_id,
        account_id=1,
        phone="+79001234567",
        farm_id=farm_id,
        tenant_id=tenant_id,
        farm_config=_DummyFarmConfig(),
        assigned_channels=[],
        session_manager=None,
        ai_router_func=_fake_route,
        redis_client=_FakeRedis(),
        publish_event_func=_noop_publish,
    )


@pytest.mark.asyncio
async def test_thread_initial_state_is_idle() -> None:
    thread = _build_farm_thread()
    assert thread.status == "idle"


@pytest.mark.asyncio
async def test_thread_state_transitions() -> None:
    """Verify that _transition changes the internal state correctly."""
    from core.farm_thread import (
        STATE_COOLDOWN,
        STATE_MONITORING,
        STATE_QUARANTINE,
        STATE_STOPPED,
        STATE_SUBSCRIBING,
    )

    thread = _build_farm_thread()

    # Patch _flush_status_to_db so no real DB calls are made
    async def _noop_flush(status: str) -> None:
        pass

    thread._flush_status_to_db = _noop_flush  # type: ignore[method-assign]

    assert thread.status == "idle"
    await thread._transition(STATE_SUBSCRIBING)
    assert thread.status == STATE_SUBSCRIBING

    await thread._transition(STATE_MONITORING)
    assert thread.status == STATE_MONITORING

    await thread._transition(STATE_COOLDOWN)
    assert thread.status == STATE_COOLDOWN

    await thread._transition(STATE_MONITORING)
    assert thread.status == STATE_MONITORING

    await thread._transition(STATE_QUARANTINE)
    assert thread.status == STATE_QUARANTINE

    await thread._transition(STATE_STOPPED)
    assert thread.status == STATE_STOPPED


@pytest.mark.asyncio
async def test_thread_flood_wait_short_enters_cooldown() -> None:
    """flood_wait < 300s should transition to cooldown."""
    from core.farm_thread import STATE_COOLDOWN

    thread = _build_farm_thread()

    async def _noop_flush(status: str) -> None:
        pass

    thread._flush_status_to_db = _noop_flush  # type: ignore[method-assign]

    await thread.handle_flood_wait(seconds=60)
    assert thread.status == STATE_COOLDOWN
    assert thread._cooldown_until is not None
    assert thread._quarantine_until is None


@pytest.mark.asyncio
async def test_thread_flood_wait_long_enters_quarantine() -> None:
    """flood_wait >= 300s should transition to quarantine."""
    from core.farm_thread import STATE_QUARANTINE

    thread = _build_farm_thread()

    async def _noop_flush(status: str) -> None:
        pass

    async def _noop_quarantine_flush(until: Any) -> None:
        pass

    thread._flush_status_to_db = _noop_flush  # type: ignore[method-assign]
    thread._flush_quarantine_to_db = _noop_quarantine_flush  # type: ignore[method-assign]

    await thread.handle_flood_wait(seconds=300)
    assert thread.status == STATE_QUARANTINE
    assert thread._quarantine_until is not None
    assert thread._cooldown_until is None


@pytest.mark.asyncio
async def test_thread_pause_and_resume() -> None:
    thread = _build_farm_thread()
    # Initially unpaused
    assert thread._pause_event.is_set()

    thread.pause()
    assert not thread._pause_event.is_set()

    thread.resume()
    assert thread._pause_event.is_set()


@pytest.mark.asyncio
async def test_thread_stop_signal() -> None:
    thread = _build_farm_thread()
    assert not thread._stop_event.is_set()

    await thread.stop()
    assert thread._stop_event.is_set()
    # stop() also unblocks pause
    assert thread._pause_event.is_set()


@pytest.mark.asyncio
async def test_thread_comment_percentage_filter() -> None:
    """100% should always return True; 0% should always return False."""
    thread = _build_farm_thread()

    thread.farm_config.comment_percentage = 100
    # Run 20 times — must always be True
    results = [thread._should_comment_this_post() for _ in range(20)]
    assert all(results)

    thread.farm_config.comment_percentage = 0
    # 0 <= 0 is False in the implementation (random 1..100 > 0 means always false)
    results = [thread._should_comment_this_post() for _ in range(20)]
    assert not any(results)


# ===========================================================================
# 5. Profile template API
# ===========================================================================


@pytest.mark.asyncio
async def test_create_profile_template(farm_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(farm_client, 8301)

    resp = await farm_client.post(
        "/v1/profiles/templates",
        headers=_auth(token),
        json={
            "name": "RU Female Template",
            "gender": "female",
            "geo": "RU",
            "bio_template": "Люблю путешествия и книги.",
            "avatar_style": "ai_generated",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "RU Female Template"
    assert body["gender"] == "female"
    assert body["geo"] == "RU"
    assert "id" in body


@pytest.mark.asyncio
async def test_list_profile_templates(farm_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(farm_client, 8302)

    # Create two templates
    for i in range(2):
        r = await farm_client.post(
            "/v1/profiles/templates",
            headers=_auth(token),
            json={"name": f"Template {i}", "gender": "any", "geo": "RU"},
        )
        assert r.status_code == 201

    resp = await farm_client.get("/v1/profiles/templates", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_profile_templates_tenant_isolated(farm_client: AsyncClient) -> None:
    """Tenant B cannot see tenant A's profile templates."""
    token_a, _, _ = await _create_authorized_session(farm_client, 8303)
    token_b, _, _ = await _create_authorized_session(farm_client, 8304)

    await farm_client.post(
        "/v1/profiles/templates",
        headers=_auth(token_a),
        json={"name": "A Template", "gender": "male", "geo": "RU"},
    )

    resp_b = await farm_client.get("/v1/profiles/templates", headers=_auth(token_b))
    assert resp_b.status_code == 200
    assert resp_b.json()["total"] == 0


@pytest.mark.asyncio
async def test_unauthenticated_farm_request_rejected(farm_client: AsyncClient) -> None:
    """All /v1/farm endpoints require authentication."""
    resp = await farm_client.get("/v1/farm")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_unauthenticated_channel_db_request_rejected(farm_client: AsyncClient) -> None:
    """All /v1/channel-db endpoints require authentication."""
    resp = await farm_client.get("/v1/channel-db")
    assert resp.status_code in (401, 403)
