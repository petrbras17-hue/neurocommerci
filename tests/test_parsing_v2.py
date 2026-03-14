"""Sprint 22: Parsing v2 — Tests.

Tests group parsing, message parsing, AI keyword suggestions,
templates CRUD, export, and admin-only access.
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
    AuthUser, GroupParsingJob, MessageParsingJob, MessageParsingResult,
    ParsingTemplate, TeamMember, Tenant, Workspace,
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
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "test-pv2-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "test-pv2-refresh-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "APP_ENV", "test")
    await init_db()
    async with async_session() as session:
        async with session.begin():
            for model in [
                MessageParsingResult, MessageParsingJob, GroupParsingJob,
                ParsingTemplate, TeamMember, Workspace, Tenant, AuthUser,
            ]:
                await session.execute(delete(model))


async def _create_user(is_admin: bool = True) -> tuple[int, int, int, str]:
    async with async_session() as session:
        async with session.begin():
            user = AuthUser(
                email=f"pv2_{'admin' if is_admin else 'user'}@test.com",
                first_name="PV2Test",
                is_platform_admin=is_admin,
                created_at=utcnow(),
            )
            session.add(user)
            await session.flush()

            tenant = Tenant(name="PV2Tenant", slug=f"pv2-{user.id}", status="active")
            session.add(tenant)
            await session.flush()

            ws = Workspace(tenant_id=tenant.id, name="Main", settings={})
            session.add(ws)
            await session.flush()

            tm = TeamMember(
                tenant_id=tenant.id, workspace_id=ws.id,
                user_id=user.id, role="owner",
            )
            session.add(tm)
            await session.flush()

            token = make_access_token(
                user_id=user.id, tenant_id=tenant.id,
                workspace_id=ws.id, role="owner",
            )
            return user.id, tenant.id, ws.id, token


# ── Tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_start_group_parsing(client: AsyncClient):
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/parser/groups",
        json={"keywords": ["крипто", "биткоин"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    assert data["keywords"] == ["крипто", "биткоин"]


@pytest.mark.asyncio(loop_scope="session")
async def test_list_group_jobs(client: AsyncClient):
    _, _, _, token = await _create_user(is_admin=True)
    # Create a job first
    await client.post(
        "/v1/admin/parser/groups",
        json={"keywords": ["test"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get(
        "/v1/admin/parser/groups",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio(loop_scope="session")
async def test_cancel_group_job(client: AsyncClient):
    _, _, _, token = await _create_user(is_admin=True)
    create_resp = await client.post(
        "/v1/admin/parser/groups",
        json={"keywords": ["cancel_test"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    job_id = create_resp.json()["id"]
    resp = await client.delete(
        f"/v1/admin/parser/groups/{job_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


@pytest.mark.asyncio(loop_scope="session")
async def test_start_message_parsing(client: AsyncClient):
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/parser/messages",
        json={"channel_id": -1001234567890, "keywords": ["python"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    assert data["channel_id"] == -1001234567890


@pytest.mark.asyncio(loop_scope="session")
async def test_list_message_jobs(client: AsyncClient):
    _, _, _, token = await _create_user(is_admin=True)
    await client.post(
        "/v1/admin/parser/messages",
        json={"channel_id": -1001111111111},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get(
        "/v1/admin/parser/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio(loop_scope="session")
async def test_suggest_keywords(client: AsyncClient):
    """AI keyword expansion — falls back to seed if ai_router unavailable."""
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/parser/suggest-keywords",
        json={"seed_keywords": ["маркетинг", "smm"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "keywords" in data
    assert isinstance(data["keywords"], list)
    assert len(data["keywords"]) >= 2


@pytest.mark.asyncio(loop_scope="session")
async def test_list_templates(client: AsyncClient):
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.get(
        "/v1/admin/parser/templates",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "system" in data
    assert "user" in data


@pytest.mark.asyncio(loop_scope="session")
async def test_create_user_template(client: AsyncClient):
    _, _, _, token = await _create_user(is_admin=True)
    resp = await client.post(
        "/v1/admin/parser/templates",
        json={
            "name": "My Custom Template",
            "category": "crypto",
            "keywords": ["btc", "eth"],
            "description": "Test template",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Custom Template"
    assert data["is_system"] is False


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_user_template(client: AsyncClient):
    _, _, _, token = await _create_user(is_admin=True)
    create_resp = await client.post(
        "/v1/admin/parser/templates",
        json={"name": "ToDelete", "keywords": ["x", "y"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    tmpl_id = create_resp.json()["id"]
    resp = await client.delete(
        f"/v1/admin/parser/templates/{tmpl_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_export_results(client: AsyncClient):
    """Export with empty results returns valid structure."""
    _, _, _, token = await _create_user(is_admin=True)
    create_resp = await client.post(
        "/v1/admin/parser/messages",
        json={"channel_id": -1009999999999},
        headers={"Authorization": f"Bearer {token}"},
    )
    job_id = create_resp.json()["id"]
    # Export as JSON
    resp = await client.post(
        f"/v1/admin/parser/messages/{job_id}/export",
        json={"format": "json"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_non_admin_blocked(client: AsyncClient):
    _, _, _, token = await _create_user(is_admin=False)
    resp = await client.get(
        "/v1/admin/parser/groups",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
