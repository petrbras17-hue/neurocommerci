"""Sprint 21: Chatting v2 + Dialogs v2 — Tests.

Tests chatting config CRUD, DM inbox, auto-responder, presets,
and admin auth enforcement.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

import ops_api
from core.web_auth import make_access_token
from ops_api import app
from storage.models import (
    AuthUser,
    AutoResponderConfig,
    ChattingConfigV2,
    ChattingPreset,
    DmInbox,
    DmMessage,
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
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "test-chatv2-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "test-chatv2-refresh-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "APP_ENV", "test")
    await init_db()
    async with async_session() as session:
        async with session.begin():
            for model in [
                DmMessage, DmInbox, AutoResponderConfig,
                ChattingPreset, ChattingConfigV2,
                TeamMember, Workspace, Tenant, AuthUser,
            ]:
                await session.execute(delete(model))


async def _make_user(is_admin: bool = True) -> tuple[int, int, int, str]:
    """Create auth_user + tenant + workspace, return (user_id, tenant_id, workspace_id, token)."""
    async with async_session() as session:
        async with session.begin():
            user = AuthUser(
                email=f"chatv2_{'admin' if is_admin else 'user'}@test.com",
                first_name="ChatV2Test",
                is_platform_admin=is_admin,
                created_at=utcnow(),
            )
            session.add(user)
            await session.flush()

            tenant = Tenant(name="ChatV2Tenant", slug=f"chatv2-{user.id}", status="active")
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
async def test_create_chatting_config(client: AsyncClient):
    _, _, _, token = await _make_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/chatting/configs",
        json={
            "name": "Test Config",
            "mode": "interval",
            "interval_percent": 15,
            "product_name": "TestProduct",
            "mention_frequency": "subtle",
            "context_depth": 8,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Config"
    assert data["mode"] == "interval"
    assert data["interval_percent"] == 15
    assert data["context_depth"] == 8


@pytest.mark.asyncio(loop_scope="session")
async def test_update_chatting_config(client: AsyncClient):
    _, _, _, token = await _make_user(is_admin=True)
    # Create
    resp = await client.post(
        "/v1/admin/chatting/configs",
        json={"name": "Update Me", "mode": "keyword_trigger"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    cid = resp.json()["id"]
    # Update
    resp = await client.put(
        f"/v1/admin/chatting/configs/{cid}",
        json={"name": "Updated", "trigger_keywords": ["growth", "telegram"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Updated"
    assert data["trigger_keywords"] == ["growth", "telegram"]


@pytest.mark.asyncio(loop_scope="session")
async def test_list_chatting_configs(client: AsyncClient):
    _, _, _, token = await _make_user(is_admin=True)
    # Create two configs
    for name in ["Config A", "Config B"]:
        await client.post(
            "/v1/admin/chatting/configs",
            json={"name": name},
            headers={"Authorization": f"Bearer {token}"},
        )
    resp = await client.get(
        "/v1/admin/chatting/configs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 2


@pytest.mark.asyncio(loop_scope="session")
async def test_dm_inbox_empty(client: AsyncClient):
    _, _, _, token = await _make_user(is_admin=True)
    resp = await client.get(
        "/v1/admin/dialogs/inbox",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_send_dm_no_account(client: AsyncClient):
    _, _, _, token = await _make_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/dialogs/inbox/99999/12345/send",
        json={"text": "hello"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "account_not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_auto_responder_crud(client: AsyncClient):
    _, _, _, token = await _make_user(is_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    # Create
    resp = await client.post(
        "/v1/admin/auto-responder",
        json={"product_name": "MyProd", "tone": "professional", "max_responses_per_day": 10},
        headers=headers,
    )
    assert resp.status_code == 201
    rid = resp.json()["id"]
    assert resp.json()["tone"] == "professional"

    # List
    resp = await client.get("/v1/admin/auto-responder", headers=headers)
    assert resp.status_code == 200
    assert any(r["id"] == rid for r in resp.json()["items"])

    # Update
    resp = await client.put(
        f"/v1/admin/auto-responder/{rid}",
        json={"tone": "casual"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["tone"] == "casual"

    # Delete
    resp = await client.delete(f"/v1/admin/auto-responder/{rid}", headers=headers)
    assert resp.status_code == 204


@pytest.mark.asyncio(loop_scope="session")
async def test_save_preset(client: AsyncClient):
    _, _, _, token = await _make_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/chatting/presets",
        json={"name": "My Preset", "config": {"mode": "interval", "interval_percent": 20}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Preset"
    assert data["config"]["mode"] == "interval"


@pytest.mark.asyncio(loop_scope="session")
async def test_list_presets(client: AsyncClient):
    _, _, _, token = await _make_user(is_admin=True)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/v1/admin/chatting/presets",
        json={"name": "Preset A", "config": {"mode": "interval"}},
        headers=headers,
    )
    resp = await client.get("/v1/admin/chatting/presets", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_preset(client: AsyncClient):
    _, _, _, token = await _make_user(is_admin=True)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.post(
        "/v1/admin/chatting/presets",
        json={"name": "To Delete", "config": {}},
        headers=headers,
    )
    pid = resp.json()["id"]
    resp = await client.delete(f"/v1/admin/chatting/presets/{pid}", headers=headers)
    assert resp.status_code == 204


@pytest.mark.asyncio(loop_scope="session")
async def test_non_admin_blocked(client: AsyncClient):
    _, _, _, token = await _make_user(is_admin=False)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/v1/admin/chatting/configs", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "platform_admin_required"
