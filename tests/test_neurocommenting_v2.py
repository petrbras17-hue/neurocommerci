"""Sprint 20: Neurocommenting v2 — Tests.

Tests blacklist/whitelist CRUD, presets, auto-DM, language detection,
and admin auth enforcement.
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
    AuthUser,
    AutoDmConfig,
    ChannelBlacklist,
    ChannelWhitelist,
    FarmPreset,
    TeamMember,
    Tenant,
    Workspace,
)
from storage.sqlite_db import async_session, init_db
from utils.helpers import utcnow


# ── Fixtures ───────────────────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def nc_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _nc_clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "test-nc-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "test-nc-refresh-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "APP_ENV", "test")
    await init_db()
    async with async_session() as session:
        async with session.begin():
            for model in [AutoDmConfig, FarmPreset, ChannelWhitelist, ChannelBlacklist,
                          TeamMember, Workspace, Tenant, AuthUser]:
                await session.execute(delete(model))


async def _create_user(is_admin: bool = True) -> tuple[int, int, int, str]:
    """Create auth_user + tenant + workspace, return (user_id, tenant_id, workspace_id, token)."""
    async with async_session() as session:
        async with session.begin():
            suffix = "admin" if is_admin else "user"
            user = AuthUser(
                email=f"nc_{suffix}_{id(is_admin)}@test.com",
                first_name=f"NC{suffix}",
                is_platform_admin=is_admin,
                created_at=utcnow(),
            )
            session.add(user)
            await session.flush()

            tenant = Tenant(name="NCTestTenant", slug=f"nc-test-{user.id}", status="active")
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
async def test_add_to_blacklist(nc_client: AsyncClient):
    """Admin can add a channel to blacklist."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await nc_client.post(
        "/v1/admin/blacklist",
        json={"channel_id": 1001, "channel_username": "test_ch", "channel_title": "Test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["channel_id"] == 1001
    assert data["reason"] == "manual"


@pytest.mark.asyncio(loop_scope="session")
async def test_remove_from_blacklist(nc_client: AsyncClient):
    """Admin can remove a channel from blacklist."""
    _, _, _, token = await _create_user(is_admin=True)
    # Add first
    await nc_client.post(
        "/v1/admin/blacklist",
        json={"channel_id": 2001},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Remove
    resp = await nc_client.delete(
        "/v1/admin/blacklist/2001",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_blacklist(nc_client: AsyncClient):
    """Adding the same channel twice returns 409."""
    _, _, _, token = await _create_user(is_admin=True)
    await nc_client.post(
        "/v1/admin/blacklist",
        json={"channel_id": 3001},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await nc_client.post(
        "/v1/admin/blacklist",
        json={"channel_id": 3001},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio(loop_scope="session")
async def test_add_to_whitelist(nc_client: AsyncClient):
    """Admin can add a channel to whitelist."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await nc_client.post(
        "/v1/admin/whitelist",
        json={"channel_id": 4001, "channel_username": "wl_ch", "channel_title": "WL Test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["channel_id"] == 4001
    assert data["successful_comments"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_auto_dm_setup(nc_client: AsyncClient):
    """Admin can set up auto-DM for a farm."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await nc_client.post(
        "/v1/admin/farm/1/auto-dm",
        json={"message": "Hello! Thanks for your message.", "max_dms_per_day": 20},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Hello! Thanks for your message."
    assert data["max_dms_per_day"] == 20
    assert data["is_active"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_auto_dm_update(nc_client: AsyncClient):
    """Admin can update auto-DM message."""
    _, _, _, token = await _create_user(is_admin=True)
    # Setup first
    await nc_client.post(
        "/v1/admin/farm/2/auto-dm",
        json={"message": "Original message"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Update
    resp = await nc_client.put(
        "/v1/admin/farm/2/auto-dm",
        json={"message": "Updated message", "is_active": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Updated message"
    assert data["is_active"] is False


@pytest.mark.asyncio(loop_scope="session")
async def test_save_preset(nc_client: AsyncClient):
    """Admin can save a farm preset."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await nc_client.post(
        "/v1/admin/presets",
        json={
            "name": "Aggressive RU",
            "config": {"max_threads": 50, "delay_min": 10},
            "targeting_mode": "keyword_match",
            "targeting_params": {"keywords": ["crypto", "AI"]},
            "language": "ru",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Aggressive RU"
    assert data["targeting_mode"] == "keyword_match"


@pytest.mark.asyncio(loop_scope="session")
async def test_list_presets(nc_client: AsyncClient):
    """Admin can list presets."""
    _, _, _, token = await _create_user(is_admin=True)
    # Create one
    await nc_client.post(
        "/v1/admin/presets",
        json={"name": "List Test", "config": {"mode": "test"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await nc_client.get(
        "/v1/admin/presets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert len(data["items"]) >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_preset(nc_client: AsyncClient):
    """Admin can delete a preset."""
    _, _, _, token = await _create_user(is_admin=True)
    # Create
    create_resp = await nc_client.post(
        "/v1/admin/presets",
        json={"name": "To Delete", "config": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    preset_id = create_resp.json()["id"]
    # Delete
    resp = await nc_client.delete(
        f"/v1/admin/presets/{preset_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_detect_language(nc_client: AsyncClient):
    """Language detection returns a language code."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await nc_client.get(
        "/v1/admin/channels/12345/language",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "language" in data
    assert len(data["language"]) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_non_admin_blocked(nc_client: AsyncClient):
    """Non-admin user gets 403 on neurocommenting endpoints."""
    _, _, _, token = await _create_user(is_admin=False)
    resp = await nc_client.get(
        "/v1/admin/blacklist",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "platform_admin_required"
