from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from config import settings
from core.usage_events import log_usage_event
from ops_api import app
from storage.models import AuthUser, TeamMember, Tenant, UsageEvent, Workspace
from storage.sqlite_db import apply_session_rls_context, async_session, dispose_engine, init_db


pytestmark = [
    pytest.mark.skipif(
        "postgresql" not in settings.db_url,
        reason="tenant foundation tests require PostgreSQL",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


TENANT_A = 101
TENANT_B = 102
TENANT_SUSPENDED = 103
USER_A = 1001
USER_B = 1002
USER_SUSPENDED = 1003
WORKSPACE_A = 201
WORKSPACE_B = 202
WORKSPACE_SUSPENDED = 203


def _make_access_token(user_id: int, tenant_id: int, workspace_id: int | None = None, role: str = "member") -> str:
    payload = {
        "sub": str(user_id),
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "role": role,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
    }
    return jwt.encode(payload, settings.JWT_ACCESS_SECRET, algorithm=settings.JWT_ALGORITHM)


async def _seed_tenant(
    *,
    tenant_id: int,
    user_id: int,
    workspace_id: int,
    name: str,
    slug: str,
    status: str = "active",
) -> None:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=tenant_id, user_id=user_id)
            auth_user = AuthUser(
                id=user_id,
                email=f"user-{user_id}@example.com",
            )
            tenant = Tenant(
                id=tenant_id,
                name=name,
                slug=slug,
                status=status,
            )
            workspace = Workspace(
                id=workspace_id,
                tenant_id=tenant_id,
                name=f"{name} Workspace",
                settings={"plan": "test", "tenant": slug},
            )
            session.add(auth_user)
            session.add(tenant)
            session.add(workspace)
            await session.flush()
            session.add(
                TeamMember(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    role="owner",
                )
            )


async def _truncate_foundation_tables() -> None:
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                text(
                    """
                    TRUNCATE TABLE
                        usage_events,
                        team_members,
                        workspaces,
                        tenants,
                        auth_users
                    RESTART IDENTITY CASCADE
                    """
                )
            )


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _bootstrap_tenant_foundation() -> None:
    settings.JWT_ACCESS_SECRET = "test-access-secret-for-tenant-suite-32b"
    settings.JWT_REFRESH_SECRET = "test-refresh-secret-for-tenant-suite-32b"
    settings.JWT_ALGORITHM = "HS256"
    settings.OPS_API_TOKEN = "test-internal-token"
    await init_db()
    async with async_session() as session:
        result = await session.execute(
            text(
                """
                SELECT rolsuper, rolbypassrls
                FROM pg_roles
                WHERE rolname = current_user
                """
            )
        )
        role_row = result.one()
        assert role_row.rolsuper is False
        assert role_row.rolbypassrls is False
    yield
    await dispose_engine()


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def _clean_foundation_state() -> None:
    await _truncate_foundation_tables()
    yield
    await _truncate_foundation_tables()


@pytest_asyncio.fixture(loop_scope="session")
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


async def test_workspaces_are_isolated_per_tenant(client: AsyncClient) -> None:
    await _seed_tenant(
        tenant_id=TENANT_A,
        user_id=USER_A,
        workspace_id=WORKSPACE_A,
        name="Tenant A",
        slug="tenant-a",
    )
    await _seed_tenant(
        tenant_id=TENANT_B,
        user_id=USER_B,
        workspace_id=WORKSPACE_B,
        name="Tenant B",
        slug="tenant-b",
    )

    headers_a = {"Authorization": f"Bearer {_make_access_token(USER_A, TENANT_A, WORKSPACE_A, 'owner')}"}
    headers_b = {"Authorization": f"Bearer {_make_access_token(USER_B, TENANT_B, WORKSPACE_B, 'owner')}"}

    response_a = await client.get("/v1/workspaces", headers=headers_a)
    response_b = await client.get("/v1/workspaces", headers=headers_b)

    assert response_a.status_code == 200
    assert response_b.status_code == 200

    body_a = response_a.json()
    body_b = response_b.json()

    assert body_a["total"] == 1
    assert body_b["total"] == 1
    assert body_a["items"][0]["id"] == WORKSPACE_A
    assert body_b["items"][0]["id"] == WORKSPACE_B
    assert body_a["items"][0]["name"] == "Tenant A Workspace"
    assert body_b["items"][0]["name"] == "Tenant B Workspace"
    assert body_a["items"][0]["settings"] == {"plan": "test", "tenant": "tenant-a"}
    assert body_b["items"][0]["settings"] == {"plan": "test", "tenant": "tenant-b"}
    assert set(body_a["items"][0].keys()) == {"id", "name", "settings", "created_at"}
    assert set(body_b["items"][0].keys()) == {"id", "name", "settings", "created_at"}


async def test_rls_blocks_cross_tenant_select_and_insert() -> None:
    await _seed_tenant(
        tenant_id=TENANT_A,
        user_id=USER_A,
        workspace_id=WORKSPACE_A,
        name="Tenant A",
        slug="tenant-a",
    )
    await _seed_tenant(
        tenant_id=TENANT_B,
        user_id=USER_B,
        workspace_id=WORKSPACE_B,
        name="Tenant B",
        slug="tenant-b",
    )

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=TENANT_B, user_id=USER_B)
            visible_tenants = list((await session.execute(select(Tenant).order_by(Tenant.id))).scalars().all())
            visible_workspaces = list((await session.execute(select(Workspace).order_by(Workspace.id))).scalars().all())
            tenant_a_workspace = (
                await session.execute(select(Workspace).where(Workspace.id == WORKSPACE_A))
            ).scalar_one_or_none()

            assert [tenant.id for tenant in visible_tenants] == [TENANT_B]
            assert [workspace.id for workspace in visible_workspaces] == [WORKSPACE_B]
            assert tenant_a_workspace is None

    async with async_session() as session:
        with pytest.raises(DBAPIError) as exc_info:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=TENANT_B, user_id=USER_B)
                session.add(
                    Workspace(
                        id=999,
                        tenant_id=TENANT_A,
                        name="Cross Tenant Write",
                        settings={"blocked": True},
                    )
                )
                await session.flush()

    assert "row-level security" in str(exc_info.value).lower()


async def test_bootstrap_rls_context_allows_auth_user_lookup_without_tenant_settings() -> None:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, bootstrap=True)
            result = await session.execute(
                select(AuthUser).where(AuthUser.telegram_user_id == 440602963)
            )
            assert result.scalar_one_or_none() is None


async def test_log_usage_event_stores_correct_tenant_id() -> None:
    await _seed_tenant(
        tenant_id=TENANT_A,
        user_id=USER_A,
        workspace_id=WORKSPACE_A,
        name="Tenant A",
        slug="tenant-a",
    )

    await log_usage_event(TENANT_A, "workspace.list", {"source": "pytest"})

    saved_event = None
    for _ in range(20):
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=TENANT_A, user_id=USER_A)
                saved_event = (
                    await session.execute(
                        select(UsageEvent).where(UsageEvent.event_type == "workspace.list")
                    )
                ).scalar_one_or_none()
        if saved_event is not None:
            break
        await asyncio.sleep(0.05)

    assert saved_event is not None
    assert saved_event.tenant_id == TENANT_A
    assert saved_event.meta == {"source": "pytest"}


async def test_suspended_tenant_gets_403(client: AsyncClient) -> None:
    await _seed_tenant(
        tenant_id=TENANT_SUSPENDED,
        user_id=USER_SUSPENDED,
        workspace_id=WORKSPACE_SUSPENDED,
        name="Tenant Suspended",
        slug="tenant-suspended",
        status="suspended",
    )

    headers = {
        "Authorization": f"Bearer {_make_access_token(USER_SUSPENDED, TENANT_SUSPENDED, WORKSPACE_SUSPENDED, 'owner')}"
    }
    response = await client.get("/v1/workspaces", headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "tenant_suspended"


async def test_internal_ops_api_token_still_works(client: AsyncClient) -> None:
    response = await client.get(
        "/v1/accounts",
        headers={"Authorization": f"Bearer {settings.OPS_API_TOKEN}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body
