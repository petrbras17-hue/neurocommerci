from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import ops_api
from config import settings
from core.web_auth import make_access_token
from ops_api import app
from storage.models import AuthUser, RefreshToken, TeamMember, Tenant, Workspace
from storage.sqlite_db import async_session, init_db
from utils.helpers import utcnow


def _telegram_payload(*, bot_token: str, telegram_user_id: int, username: str = "testuser") -> dict[str, object]:
    auth_date = int(utcnow().timestamp())
    payload: dict[str, object] = {
        "id": telegram_user_id,
        "auth_date": auth_date,
        "username": username,
        "first_name": "Test",
        "last_name": "User",
    }
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    payload["hash"] = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return payload


async def _reset_web_auth_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in [RefreshToken, TeamMember, Workspace, Tenant, AuthUser]:
                await session.execute(delete(model))


@pytest_asyncio.fixture(loop_scope="session")
async def web_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True)
async def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops_api.settings, "ADMIN_BOT_TOKEN", "telegram-test-token")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "web-auth-access-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "web-auth-refresh-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    await init_db()
    await _reset_web_auth_state()
    yield
    await _reset_web_auth_state()


@pytest.mark.asyncio
async def test_telegram_verify_bootstraps_auth_user_and_membership(web_client: AsyncClient) -> None:
    payload = _telegram_payload(bot_token=settings.ADMIN_BOT_TOKEN, telegram_user_id=9001)

    response = await web_client.post("/auth/telegram/verify", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "profile_incomplete"
    assert body["setup_token"]
    assert body["tenant"]["role"] == "owner"
    assert body["workspace"]["name"] == "Основное пространство"

    async with async_session() as session:
        auth_user = (await session.execute(select(AuthUser).where(AuthUser.telegram_user_id == 9001))).scalar_one()
        memberships = list((await session.execute(select(TeamMember).where(TeamMember.user_id == auth_user.id))).scalars().all())
    assert len(memberships) == 1


@pytest.mark.asyncio
async def test_repeat_verify_is_idempotent(web_client: AsyncClient) -> None:
    payload = _telegram_payload(bot_token=settings.ADMIN_BOT_TOKEN, telegram_user_id=9002)

    first = await web_client.post("/auth/telegram/verify", json=payload)
    second = await web_client.post("/auth/telegram/verify", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200

    async with async_session() as session:
        auth_users = list((await session.execute(select(AuthUser).where(AuthUser.telegram_user_id == 9002))).scalars().all())
        memberships = list((await session.execute(select(TeamMember))).scalars().all())
    assert len(auth_users) == 1
    assert len(memberships) == 1


@pytest.mark.asyncio
async def test_complete_profile_issues_tokens_and_refresh_cookie(web_client: AsyncClient) -> None:
    payload = _telegram_payload(bot_token=settings.ADMIN_BOT_TOKEN, telegram_user_id=9003)
    verify_response = await web_client.post("/auth/telegram/verify", json=payload)
    setup_token = verify_response.json()["setup_token"]

    response = await web_client.post(
        "/auth/complete-profile",
        json={"setup_token": setup_token, "email": "owner@example.com", "company": "Neuro Co"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "authorized"
    assert body["access_token"]
    assert body["user"]["email"] == "owner@example.com"
    assert body["user"]["company"] == "Neuro Co"
    assert settings.WEBAPP_SESSION_COOKIE_NAME in response.headers.get("set-cookie", "")

    async with async_session() as session:
        tokens = list((await session.execute(select(RefreshToken))).scalars().all())
    assert len(tokens) == 1


@pytest.mark.asyncio
async def test_invalid_telegram_hash_is_rejected(web_client: AsyncClient) -> None:
    payload = _telegram_payload(bot_token=settings.ADMIN_BOT_TOKEN, telegram_user_id=9004)
    payload["hash"] = "deadbeef" * 8

    response = await web_client.post("/auth/telegram/verify", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_telegram_hash"


@pytest.mark.asyncio
async def test_refresh_without_cookie_returns_anonymous(web_client: AsyncClient) -> None:
    response = await web_client.post("/auth/refresh")

    assert response.status_code == 200
    assert response.json()["status"] == "anonymous"


@pytest.mark.asyncio
async def test_suspended_tenant_returns_403_for_me(web_client: AsyncClient) -> None:
    payload = _telegram_payload(bot_token=settings.ADMIN_BOT_TOKEN, telegram_user_id=9005)
    verify_response = await web_client.post("/auth/telegram/verify", json=payload)
    setup_token = verify_response.json()["setup_token"]
    completion_response = await web_client.post(
        "/auth/complete-profile",
        json={"setup_token": setup_token, "email": "suspend@example.com", "company": "Suspend Co"},
    )
    access_token = completion_response.json()["access_token"]

    async with async_session() as session:
        async with session.begin():
            tenant = (await session.execute(select(Tenant))).scalar_one()
            tenant.status = "suspended"

    response = await web_client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 403
    assert response.json()["detail"] == "tenant_suspended"


@pytest.mark.asyncio
async def test_refresh_rotates_token_and_logout_revokes_cookie(web_client: AsyncClient) -> None:
    payload = _telegram_payload(bot_token=settings.ADMIN_BOT_TOKEN, telegram_user_id=9006)
    verify_response = await web_client.post("/auth/telegram/verify", json=payload)
    setup_token = verify_response.json()["setup_token"]
    completion_response = await web_client.post(
        "/auth/complete-profile",
        json={"setup_token": setup_token, "email": "refresh@example.com", "company": "Refresh Co"},
    )
    first_cookie = completion_response.cookies.get(settings.WEBAPP_SESSION_COOKIE_NAME)
    assert first_cookie

    refreshed = await web_client.post("/auth/refresh")
    assert refreshed.status_code == 200
    second_cookie = refreshed.cookies.get(settings.WEBAPP_SESSION_COOKIE_NAME)
    assert second_cookie
    assert first_cookie != second_cookie

    async with async_session() as session:
        tokens = list((await session.execute(select(RefreshToken).order_by(RefreshToken.id.asc()))).scalars().all())
    assert len(tokens) == 2
    assert tokens[0].revoked_at is not None
    assert tokens[1].revoked_at is None

    logged_out = await web_client.post("/auth/logout")
    assert logged_out.status_code == 200
    assert "Max-Age=0" in logged_out.headers.get("set-cookie", "")

    async with async_session() as session:
        tokens = list((await session.execute(select(RefreshToken).order_by(RefreshToken.id.asc()))).scalars().all())
    assert tokens[-1].revoked_at is not None
