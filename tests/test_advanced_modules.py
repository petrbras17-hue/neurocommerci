"""
Sprint 7 — Mass Reactions, Neuro Chatting, Neuro Dialogs, User Parser, Folder Manager.

Covers:
  1. Reactions (3 tests)
  2. Chatting CRUD + control + delete-running guard (6 tests)
  3. Dialogs CRUD + control (5 tests)
  4. User Parser: parse + results (2 tests)
  5. Folders: create, list, delete (3 tests)
  6. Tenant isolation: cross-tenant chatting (1 test)
  7. Auth guard: unauthenticated request (1 test)
"""

from __future__ import annotations

import hashlib
import hmac
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
    *, bot_token: str, telegram_user_id: int, username: str = "advtestuser"
) -> dict[str, Any]:
    auth_date = int(utcnow().timestamp())
    payload: dict[str, Any] = {
        "id": telegram_user_id,
        "auth_date": auth_date,
        "username": username,
        "first_name": "Adv",
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
async def adv_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[misc]
    monkeypatch.setattr(ops_api.settings, "ADMIN_BOT_TOKEN", "adv-test-token-123")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "adv-access-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "adv-refresh-secret-1234567890")
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
    """Register a new tenant via Telegram-first auth and return (access_token, tenant_id, workspace_id)."""
    username = f"adv{telegram_user_id}{username_suffix}"
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
            "email": f"adv{telegram_user_id}{username_suffix}@example.com",
            "company": f"AdvCo {telegram_user_id}{username_suffix}",
        },
    )
    assert complete.status_code == 200, f"complete-profile failed: {complete.text}"
    body = complete.json()
    return body["access_token"], int(body["tenant"]["id"]), int(body["workspace"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_account(tenant_id: int, workspace_id: int, phone_suffix: str) -> int:
    """Insert a bare account row and return its id."""
    async with async_session() as session:
        async with session.begin():
            account = Account(
                phone=f"+7900{phone_suffix}",
                session_file=f"dummy_{phone_suffix}.session",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                status="active",
            )
            session.add(account)
            await session.flush()
            return int(account.id)


# ===========================================================================
# 1. Reactions
# ===========================================================================


@pytest.mark.asyncio
async def test_reaction_create(adv_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(adv_client, 8001)
    account_id = await _create_account(tenant_id, workspace_id, "8001001")

    resp = await adv_client.post(
        "/v1/reactions",
        headers=_auth(token),
        json={
            "channel_username": "test_channel",
            "reaction_type": "fire",
            "account_ids": [account_id],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["channel_username"] == "test_channel"
    assert body["reaction_type"] == "fire"
    assert body["status"] == "pending"
    assert "id" in body


@pytest.mark.asyncio
async def test_reaction_list(adv_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(adv_client, 8002)
    account_id = await _create_account(tenant_id, workspace_id, "8002001")

    for i in range(3):
        r = await adv_client.post(
            "/v1/reactions",
            headers=_auth(token),
            json={
                "channel_username": f"ch_{i}",
                "reaction_type": "random",
                "account_ids": [account_id],
            },
        )
        assert r.status_code == 201

    resp = await adv_client.get("/v1/reactions", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


@pytest.mark.asyncio
async def test_reaction_get(adv_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(adv_client, 8003)
    account_id = await _create_account(tenant_id, workspace_id, "8003001")

    create_resp = await adv_client.post(
        "/v1/reactions",
        headers=_auth(token),
        json={
            "channel_username": "detail_channel",
            "reaction_type": "heart",
            "account_ids": [account_id],
            "post_id": 42,
        },
    )
    assert create_resp.status_code == 201
    job_id = create_resp.json()["id"]

    resp = await adv_client.get(f"/v1/reactions/{job_id}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job_id
    assert body["channel_username"] == "detail_channel"
    assert body["post_id"] == 42


# ===========================================================================
# 2. Chatting
# ===========================================================================


@pytest.mark.asyncio
async def test_chatting_create(adv_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(adv_client, 8101)

    resp = await adv_client.post(
        "/v1/chatting",
        headers=_auth(token),
        json={
            "name": "Alpha Chat",
            "mode": "moderate",
            "target_channels": ["@chan1", "@chan2"],
            "max_messages_per_hour": 10,
            "min_delay_seconds": 60,
            "max_delay_seconds": 300,
            "account_ids": [],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Alpha Chat"
    assert body["status"] == "stopped"
    assert body["mode"] == "moderate"
    assert "id" in body


@pytest.mark.asyncio
async def test_chatting_list(adv_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(adv_client, 8102)

    for i in range(2):
        r = await adv_client.post(
            "/v1/chatting",
            headers=_auth(token),
            json={"name": f"Chat {i}", "min_delay_seconds": 60, "max_delay_seconds": 120, "account_ids": []},
        )
        assert r.status_code == 201

    resp = await adv_client.get("/v1/chatting", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_chatting_start(adv_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(adv_client, 8103)

    create_resp = await adv_client.post(
        "/v1/chatting",
        headers=_auth(token),
        json={"name": "StartChat", "min_delay_seconds": 60, "max_delay_seconds": 120, "account_ids": []},
    )
    config_id = create_resp.json()["id"]

    resp = await adv_client.post(f"/v1/chatting/{config_id}/start", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["config_id"] == config_id
    assert body["status"] == "running"


@pytest.mark.asyncio
async def test_chatting_stop(adv_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(adv_client, 8104)

    create_resp = await adv_client.post(
        "/v1/chatting",
        headers=_auth(token),
        json={"name": "StopChat", "min_delay_seconds": 60, "max_delay_seconds": 120, "account_ids": []},
    )
    config_id = create_resp.json()["id"]

    await adv_client.post(f"/v1/chatting/{config_id}/start", headers=_auth(token))

    resp = await adv_client.post(f"/v1/chatting/{config_id}/stop", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["config_id"] == config_id
    assert body["status"] == "stopped"


@pytest.mark.asyncio
async def test_chatting_delete(adv_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(adv_client, 8105)

    create_resp = await adv_client.post(
        "/v1/chatting",
        headers=_auth(token),
        json={"name": "DeleteChat", "min_delay_seconds": 60, "max_delay_seconds": 120, "account_ids": []},
    )
    config_id = create_resp.json()["id"]

    del_resp = await adv_client.delete(f"/v1/chatting/{config_id}", headers=_auth(token))
    assert del_resp.status_code == 204

    list_resp = await adv_client.get("/v1/chatting", headers=_auth(token))
    assert list_resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_chatting_delete_running_rejected(adv_client: AsyncClient) -> None:
    """DELETE on a running chatting config must be rejected with 409."""
    token, tenant_id, workspace_id = await _create_authorized_session(adv_client, 8106)

    create_resp = await adv_client.post(
        "/v1/chatting",
        headers=_auth(token),
        json={"name": "RunningChat", "min_delay_seconds": 60, "max_delay_seconds": 120, "account_ids": []},
    )
    config_id = create_resp.json()["id"]

    async with async_session() as session:
        async with session.begin():
            cfg = await session.get(ChattingConfig, config_id)
            assert cfg is not None
            cfg.status = "running"

    del_resp = await adv_client.delete(f"/v1/chatting/{config_id}", headers=_auth(token))
    assert del_resp.status_code == 409


# ===========================================================================
# 3. Dialogs
# ===========================================================================


@pytest.mark.asyncio
async def test_dialog_create(adv_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(adv_client, 8201)

    resp = await adv_client.post(
        "/v1/dialogs",
        headers=_auth(token),
        json={
            "name": "WarmupDialog",
            "dialog_type": "warmup",
            "messages_per_session": 3,
            "session_interval_hours": 6,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "WarmupDialog"
    assert body["status"] == "stopped"
    assert body["dialog_type"] == "warmup"
    assert "id" in body


@pytest.mark.asyncio
async def test_dialog_list(adv_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(adv_client, 8202)

    for i in range(2):
        r = await adv_client.post(
            "/v1/dialogs",
            headers=_auth(token),
            json={"name": f"Dialog {i}"},
        )
        assert r.status_code == 201

    resp = await adv_client.get("/v1/dialogs", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_dialog_start(adv_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(adv_client, 8203)

    create_resp = await adv_client.post(
        "/v1/dialogs",
        headers=_auth(token),
        json={"name": "StartDialog"},
    )
    config_id = create_resp.json()["id"]

    resp = await adv_client.post(f"/v1/dialogs/{config_id}/start", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["config_id"] == config_id
    assert body["status"] == "running"


@pytest.mark.asyncio
async def test_dialog_stop(adv_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(adv_client, 8204)

    create_resp = await adv_client.post(
        "/v1/dialogs",
        headers=_auth(token),
        json={"name": "StopDialog"},
    )
    config_id = create_resp.json()["id"]

    await adv_client.post(f"/v1/dialogs/{config_id}/start", headers=_auth(token))

    resp = await adv_client.post(f"/v1/dialogs/{config_id}/stop", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["config_id"] == config_id
    assert body["status"] == "stopped"


@pytest.mark.asyncio
async def test_dialog_delete(adv_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(adv_client, 8205)

    create_resp = await adv_client.post(
        "/v1/dialogs",
        headers=_auth(token),
        json={"name": "DeleteDialog"},
    )
    config_id = create_resp.json()["id"]

    del_resp = await adv_client.delete(f"/v1/dialogs/{config_id}", headers=_auth(token))
    assert del_resp.status_code == 204

    list_resp = await adv_client.get("/v1/dialogs", headers=_auth(token))
    assert list_resp.json()["total"] == 0


# ===========================================================================
# 4. User Parser
# ===========================================================================


@pytest.mark.asyncio
async def test_user_parser_parse(adv_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(adv_client, 8301)
    account_id = await _create_account(tenant_id, workspace_id, "8301001")

    resp = await adv_client.post(
        "/v1/user-parser/parse",
        headers=_auth(token),
        json={"channel_username": "@target_channel", "account_id": account_id},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["channel_username"] == "@target_channel"
    assert "job_id" in body


@pytest.mark.asyncio
async def test_user_parser_results_empty(adv_client: AsyncClient) -> None:
    token, _, _ = await _create_authorized_session(adv_client, 8302)

    resp = await adv_client.get("/v1/user-parser/results", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


# ===========================================================================
# 5. Folders
# ===========================================================================


@pytest.mark.asyncio
async def test_folder_create(adv_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(adv_client, 8401)
    account_id = await _create_account(tenant_id, workspace_id, "8401001")

    resp = await adv_client.post(
        "/v1/folders",
        headers=_auth(token),
        json={
            "account_id": account_id,
            "folder_name": "Marketing",
            "channel_usernames": ["@ch1", "@ch2"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["folder_name"] == "Marketing"
    assert body["account_id"] == account_id
    assert body["status"] == "active"
    assert "id" in body


@pytest.mark.asyncio
async def test_folder_list(adv_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(adv_client, 8402)
    account_id = await _create_account(tenant_id, workspace_id, "8402001")

    for i in range(2):
        r = await adv_client.post(
            "/v1/folders",
            headers=_auth(token),
            json={"account_id": account_id, "folder_name": f"Folder {i}"},
        )
        assert r.status_code == 201

    resp = await adv_client.get("/v1/folders", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_folder_delete(adv_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(adv_client, 8403)
    account_id = await _create_account(tenant_id, workspace_id, "8403001")

    create_resp = await adv_client.post(
        "/v1/folders",
        headers=_auth(token),
        json={"account_id": account_id, "folder_name": "ToDelete"},
    )
    folder_id = create_resp.json()["id"]

    del_resp = await adv_client.delete(f"/v1/folders/{folder_id}", headers=_auth(token))
    assert del_resp.status_code == 204

    list_resp = await adv_client.get("/v1/folders", headers=_auth(token))
    assert list_resp.json()["total"] == 0


# ===========================================================================
# 6. Tenant Isolation
# ===========================================================================


@pytest.mark.asyncio
async def test_chatting_tenant_isolation(adv_client: AsyncClient) -> None:
    """Tenant B cannot start, stop, or delete Tenant A's chatting config."""
    token_a, _, _ = await _create_authorized_session(adv_client, 8501)
    token_b, _, _ = await _create_authorized_session(adv_client, 8502)

    create_resp = await adv_client.post(
        "/v1/chatting",
        headers=_auth(token_a),
        json={"name": "A Secret Chat", "min_delay_seconds": 60, "max_delay_seconds": 120, "account_ids": []},
    )
    assert create_resp.status_code == 201
    config_id = create_resp.json()["id"]

    start_resp = await adv_client.post(f"/v1/chatting/{config_id}/start", headers=_auth(token_b))
    assert start_resp.status_code == 404

    stop_resp = await adv_client.post(f"/v1/chatting/{config_id}/stop", headers=_auth(token_b))
    assert stop_resp.status_code == 404

    del_resp = await adv_client.delete(f"/v1/chatting/{config_id}", headers=_auth(token_b))
    assert del_resp.status_code == 404


# ===========================================================================
# 7. Auth Guard
# ===========================================================================


@pytest.mark.asyncio
async def test_reactions_unauthenticated(adv_client: AsyncClient) -> None:
    """GET /v1/reactions without a token must return 401."""
    resp = await adv_client.get("/v1/reactions")
    assert resp.status_code in (401, 403)
