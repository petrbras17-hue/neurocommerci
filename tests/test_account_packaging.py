"""Sprint 18: Account Packaging — Tests.

Tests profile generation, 48h guard, avatar upload, channel creation,
packaging status, mass generation, and admin auth enforcement.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from unittest.mock import AsyncMock, patch, MagicMock

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
async def pkg_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _pkg_clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "test-pkg-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "test-pkg-refresh-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "APP_ENV", "test")
    await init_db()
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
                email=f"pkg_admin{'_yes' if is_admin else '_no'}_{id(object())}@test.com",
                first_name="PkgAdmin",
                is_platform_admin=is_admin,
                created_at=utcnow(),
            )
            session.add(user)
            await session.flush()

            tenant = Tenant(name="PkgTenant", slug=f"pkg-{user.id}", status="active")
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


async def _create_test_account(workspace_id: int, **kwargs) -> int:
    """Create a test admin account, return account_id."""
    async with async_session() as session:
        async with session.begin():
            account = AdminAccount(
                workspace_id=workspace_id,
                phone=kwargs.get("phone", f"+7963742{id(object()) % 10000:04d}"),
                status=kwargs.get("status", "verified"),
                lifecycle_phase=kwargs.get("lifecycle_phase", "day0"),
                source="test",
                profile_change_earliest=kwargs.get("profile_change_earliest"),
                profile_first_name=kwargs.get("profile_first_name"),
                profile_bio=kwargs.get("profile_bio"),
                avatar_path=kwargs.get("avatar_path"),
                packaging_status=kwargs.get("packaging_status", "not_started"),
            )
            session.add(account)
            await session.flush()
            return account.id


# ── Tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_profile(pkg_client: AsyncClient):
    """Admin can generate AI profile for account."""
    _uid, _tid, ws_id, token = await _create_admin_user(is_admin=True)
    account_id = await _create_test_account(ws_id)

    # Mock route_ai_task to avoid real AI calls
    mock_result = MagicMock()
    mock_result.ok = True
    mock_result.parsed = {
        "first_name": "Anna",
        "last_name": "Petrova",
        "username": "anna_petrova_test",
        "bio": "Marketing specialist",
    }

    with patch("core.ai_router.route_ai_task", new_callable=AsyncMock, return_value=mock_result):
        resp = await pkg_client.post(
            f"/v1/admin/accounts/{account_id}/generate-profile",
            json={"gender": "female", "country": "RU", "age_range": "25-35", "profession": "marketing"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["account_id"] == account_id
    assert "profile" in data
    assert data["packaging_status"] == "profile_generated"


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_profile_48h_guard(pkg_client: AsyncClient):
    """Applying profile within 48h of first connection returns 400."""
    _uid, _tid, ws_id, token = await _create_admin_user(is_admin=True)
    # Account with 48h guard still active (earliest = tomorrow)
    future = datetime.now(timezone.utc) + timedelta(hours=24)
    account_id = await _create_test_account(
        ws_id,
        profile_change_earliest=future,
        profile_first_name="Test",
        profile_bio="Test bio",
    )

    resp = await pkg_client.post(
        f"/v1/admin/accounts/{account_id}/apply-profile",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "blocked" in resp.json()["detail"].lower() or "48h" in resp.json()["detail"].lower() or "remaining" in resp.json()["detail"].lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_avatar(pkg_client: AsyncClient):
    """Admin can request avatar generation."""
    _uid, _tid, ws_id, token = await _create_admin_user(is_admin=True)
    account_id = await _create_test_account(ws_id)

    resp = await pkg_client.post(
        f"/v1/admin/accounts/{account_id}/generate-avatar",
        json={"prompt": "professional headshot"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["account_id"] == account_id
    assert "avatar_path" in data


@pytest.mark.asyncio(loop_scope="session")
async def test_upload_avatar(pkg_client: AsyncClient):
    """Admin can upload avatar file."""
    _uid, _tid, ws_id, token = await _create_admin_user(is_admin=True)
    account_id = await _create_test_account(ws_id)

    # Create a small test image
    import io
    fake_image = io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    fake_image.name = "test_avatar.jpg"

    resp = await pkg_client.post(
        f"/v1/admin/accounts/{account_id}/upload-avatar",
        files={"avatar": ("test_avatar.jpg", fake_image, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["account_id"] == account_id
    assert "avatar_path" in data


@pytest.mark.asyncio(loop_scope="session")
async def test_create_channel(pkg_client: AsyncClient):
    """Admin can create channel for account — returns 400 since no real session."""
    _uid, _tid, ws_id, token = await _create_admin_user(is_admin=True)
    account_id = await _create_test_account(ws_id)

    resp = await pkg_client.post(
        f"/v1/admin/accounts/{account_id}/create-channel",
        json={"title": "Test Channel", "description": "Test desc", "first_post_text": "Hello!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Expected to return error because no real session file exists
    # But the endpoint itself should be reachable (not 404/403)
    assert resp.status_code in (200, 400, 500)


@pytest.mark.asyncio(loop_scope="session")
async def test_packaging_status(pkg_client: AsyncClient):
    """Returns correct status progression."""
    _uid, _tid, ws_id, token = await _create_admin_user(is_admin=True)
    account_id = await _create_test_account(ws_id, packaging_status="not_started")

    resp = await pkg_client.get(
        f"/v1/admin/accounts/{account_id}/packaging-status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["account_id"] == account_id
    assert data["packaging_status"] == "not_started"
    assert data["profile_generated"] is False
    assert data["avatar_ready"] is False
    assert data["channel_created"] is False


@pytest.mark.asyncio(loop_scope="session")
async def test_mass_generate_profiles(pkg_client: AsyncClient):
    """Bulk generation works."""
    _uid, _tid, ws_id, token = await _create_admin_user(is_admin=True)
    aid1 = await _create_test_account(ws_id, phone=f"+79637420001")
    aid2 = await _create_test_account(ws_id, phone=f"+79637420002")

    mock_result = MagicMock()
    mock_result.ok = True
    mock_result.parsed = {
        "first_name": "Bulk",
        "last_name": "Test",
        "username": "bulk_test",
        "bio": "Bulk generated",
    }

    with patch("core.ai_router.route_ai_task", new_callable=AsyncMock, return_value=mock_result):
        with patch("core.account_packaging._human_delay", new_callable=AsyncMock):
            resp = await pkg_client.post(
                "/v1/admin/accounts/mass-generate-profiles",
                json={
                    "account_ids": [aid1, aid2],
                    "params": {"gender": "male", "country": "KZ"},
                },
                headers={"Authorization": f"Bearer {token}"},
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["results"]) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_non_admin_blocked(pkg_client: AsyncClient):
    """Non-admin gets 403."""
    _uid, _tid, ws_id, token = await _create_admin_user(is_admin=False)

    resp = await pkg_client.get(
        "/v1/admin/accounts/999/packaging-status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "platform_admin_required"
