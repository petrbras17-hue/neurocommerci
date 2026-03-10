from __future__ import annotations

import hashlib
import hmac

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import ops_api
from config import settings
from ops_api import app
from storage.models import (
    AssistantMessage,
    AssistantRecommendation,
    AssistantThread,
    AuthUser,
    BusinessAsset,
    BusinessBrief,
    CreativeDraft,
    ManualAction,
    RefreshToken,
    TeamMember,
    Tenant,
    Workspace,
)
from storage.sqlite_db import async_session, init_db
from utils.helpers import utcnow


def _telegram_payload(*, bot_token: str, telegram_user_id: int, username: str = "assistantuser") -> dict[str, object]:
    auth_date = int(utcnow().timestamp())
    payload: dict[str, object] = {
        "id": telegram_user_id,
        "auth_date": auth_date,
        "username": username,
        "first_name": "Assistant",
        "last_name": "User",
    }
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    payload["hash"] = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return payload


async def _reset_assistant_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in [
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
            ]:
                await session.execute(delete(model))


async def _create_authorized_session(client: AsyncClient, telegram_user_id: int) -> tuple[str, int, int]:
    verify = await client.post(
        "/auth/telegram/verify",
        json=_telegram_payload(
            bot_token=settings.ADMIN_BOT_TOKEN,
            telegram_user_id=telegram_user_id,
            username=f"assistant{telegram_user_id}",
        ),
    )
    setup_token = verify.json()["setup_token"]
    complete = await client.post(
        "/auth/complete-profile",
        json={
            "setup_token": setup_token,
            "email": f"assistant{telegram_user_id}@example.com",
            "company": f"Company {telegram_user_id}",
        },
    )
    body = complete.json()
    return body["access_token"], int(body["tenant"]["id"]), int(body["workspace"]["id"])


@pytest_asyncio.fixture(loop_scope="session")
async def assistant_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops_api.settings, "ADMIN_BOT_TOKEN", "telegram-test-token")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "web-auth-access-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "web-auth-refresh-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "CHANNELS_SPREADSHEET_ID", "")
    monkeypatch.setattr(ops_api.settings, "STATS_SPREADSHEET_ID", "")
    monkeypatch.setattr(ops_api.settings, "GOOGLE_SHEETS_CREDENTIALS_FILE", "")
    await init_db()
    await _reset_assistant_state()
    yield
    await _reset_assistant_state()


@pytest.mark.asyncio
async def test_assistant_brief_flow_creates_thread_and_context(assistant_client: AsyncClient) -> None:
    access_token, _, _ = await _create_authorized_session(assistant_client, 9501)

    start_response = await assistant_client.post(
        "/v1/assistant/start-brief",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert start_response.status_code == 200
    body = start_response.json()
    assert body["thread"]["thread_kind"] == "growth_brief"
    assert len(body["messages"]) >= 1

    message_response = await assistant_client.post(
        "/v1/assistant/message",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "message": (
                "Продукт: Slice Pizza CRM\n"
                "Оффер: платформа роста в Telegram для брендов\n"
                "ЦА: владельцы ecom и SaaS бизнесов\n"
                "Тон: уверенный, спокойный\n"
                "Цели Telegram: лиды, продажи\n"
                "Сайт: https://slice-pizza.ru"
            )
        },
    )
    assert message_response.status_code == 200
    updated = message_response.json()
    assert len(updated["messages"]) >= 3
    assert updated["brief"]["product_name"] == "Slice Pizza CRM"
    assert "цели в Telegram" not in " ".join(updated["brief"]["missing_fields"]).lower()

    context_response = await assistant_client.get(
        "/v1/context",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert context_response.status_code == 200
    context_body = context_response.json()
    assert context_body["brief"]["product_name"] == "Slice Pizza CRM"


@pytest.mark.asyncio
async def test_creative_generation_and_approval_use_business_context(assistant_client: AsyncClient) -> None:
    access_token, _, _ = await _create_authorized_session(assistant_client, 9502)

    await assistant_client.post("/v1/assistant/start-brief", headers={"Authorization": f"Bearer {access_token}"})
    await assistant_client.post(
        "/v1/assistant/message",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "message": (
                "Продукт: Neuro Growth OS\n"
                "Оффер: AI-помощник для Telegram-маркетинга\n"
                "ЦА: mid-market бренды\n"
                "Тон: премиальный и ясный\n"
                "Цели Telegram: узнаваемость, лиды"
            )
        },
    )

    generate_response = await assistant_client.post(
        "/v1/creative/generate",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"draft_type": "post", "variant_count": 3},
    )
    assert generate_response.status_code == 200
    draft_id = int(generate_response.json()["draft"]["id"])

    drafts_response = await assistant_client.get(
        "/v1/creative/drafts",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert drafts_response.status_code == 200
    assert drafts_response.json()["total"] >= 1

    approve_response = await assistant_client.post(
        "/v1/creative/approve",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"draft_id": draft_id, "selected_variant": 1},
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["draft"]["status"] == "approved"

    context_response = await assistant_client.get(
        "/v1/context",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert context_response.status_code == 200
    assert len(context_response.json()["assets"]) >= 1


@pytest.mark.asyncio
async def test_context_confirm_and_tenant_isolation(assistant_client: AsyncClient) -> None:
    access_a, _, _ = await _create_authorized_session(assistant_client, 9503)
    access_b, _, _ = await _create_authorized_session(assistant_client, 9504)

    await assistant_client.post("/v1/assistant/start-brief", headers={"Authorization": f"Bearer {access_a}"})
    await assistant_client.post(
        "/v1/assistant/message",
        headers={"Authorization": f"Bearer {access_a}"},
        json={
            "message": (
                "Продукт: Growth Pilot\n"
                "Оффер: ассистент роста в Telegram\n"
                "ЦА: edtech и SaaS компании\n"
                "Тон: экспертный\n"
                "Цели Telegram: спрос, доверие"
            )
        },
    )

    confirm_response = await assistant_client.post(
        "/v1/context/confirm",
        headers={"Authorization": f"Bearer {access_a}"},
    )
    assert confirm_response.status_code == 200
    assert confirm_response.json()["brief"]["status"] == "confirmed"
    assert "google_sheets" in confirm_response.json()
    assert "digest_notification" in confirm_response.json()

    foreign_thread = await assistant_client.get(
        "/v1/assistant/thread",
        headers={"Authorization": f"Bearer {access_b}"},
    )
    assert foreign_thread.status_code == 200
    assert foreign_thread.json()["messages"] == []

    foreign_context = await assistant_client.get(
        "/v1/context",
        headers={"Authorization": f"Bearer {access_b}"},
    )
    assert foreign_context.status_code == 200
    assert foreign_context.json()["brief"]["product_name"] == ""
