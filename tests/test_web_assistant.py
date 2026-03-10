from __future__ import annotations

import hashlib
import hmac

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import ops_api
from config import settings
from core.assistant_service import AssistantServiceError
from core.assistant_jobs import process_pending_jobs
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


@pytest.fixture(autouse=True)
def _mock_assistant_integrations(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_extract_updates(*args, **kwargs):
        return {}, {
            "ok": True,
            "provider": "test",
            "model_name": "test-worker",
            "fallback_used": False,
            "quality_score": 1.0,
            "reason_code": None,
        }

    async def fake_assistant_reply(*args, **kwargs):
        return (
            "Контекст обновлён. Продолжайте заполнять growth-brief.",
            {
                "ok": True,
                "provider": "test",
                "model_name": "test-manager",
                "fallback_used": False,
                "quality_score": 1.0,
                "reason_code": None,
            },
        )

    async def fake_creative_variants(*args, **kwargs):
        draft_type = kwargs.get("draft_type") or "post"
        variant_count = int(kwargs.get("variant_count") or 3)
        variants = [
            {
                "title": f"{draft_type} variant {idx + 1}",
                "content": f"TEST AUDIT creative variant {idx + 1} for {draft_type}",
            }
            for idx in range(max(1, min(variant_count, 3)))
        ]
        return variants, {
            "ok": True,
            "provider": "test",
            "model_name": "test-creative",
            "fallback_used": False,
            "quality_score": 1.0,
            "reason_code": None,
        }

    async def fake_send_digest_text(text: str) -> dict[str, object]:
        return {"ok": True, "text": text, "sink": "test-digest"}

    monkeypatch.setattr("core.assistant_service._gemini_extract_updates", fake_extract_updates)
    monkeypatch.setattr("core.assistant_service._assistant_reply", fake_assistant_reply)
    monkeypatch.setattr("core.assistant_service._gemini_creative_variants", fake_creative_variants)
    monkeypatch.setattr("core.assistant_service.send_digest_text", fake_send_digest_text)


@pytest.mark.asyncio
async def test_assistant_brief_flow_creates_thread_and_context(assistant_client: AsyncClient) -> None:
    access_token, _, _ = await _create_authorized_session(assistant_client, 9501)

    start_response = await assistant_client.post(
        "/v1/assistant/start-brief",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert start_response.status_code == 200
    start_job = start_response.json()
    assert start_job["status"] == "queued"
    await process_pending_jobs()

    start_status = await assistant_client.get(
        f"/v1/jobs/{start_job['job_id']}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert start_status.status_code == 200
    assert start_status.json()["status"] == "succeeded"

    thread_response = await assistant_client.get(
        "/v1/assistant/thread",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    thread_body = thread_response.json()
    assert thread_body["thread"]["thread_kind"] == "growth_brief"
    assert len(thread_body["messages"]) >= 1

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
    message_job = message_response.json()
    assert message_job["status"] == "queued"
    await process_pending_jobs()

    message_status = await assistant_client.get(
        f"/v1/jobs/{message_job['job_id']}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert message_status.status_code == 200
    assert message_status.json()["status"] == "succeeded"

    updated_response = await assistant_client.get(
        "/v1/assistant/thread",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    updated = updated_response.json()
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
    await process_pending_jobs()
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
    await process_pending_jobs()

    generate_response = await assistant_client.post(
        "/v1/creative/generate",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"draft_type": "post", "variant_count": 3},
    )
    assert generate_response.status_code == 200
    generate_job = generate_response.json()
    assert generate_job["status"] == "queued"
    await process_pending_jobs()

    generate_status = await assistant_client.get(
        f"/v1/jobs/{generate_job['job_id']}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert generate_status.status_code == 200
    assert generate_status.json()["status"] == "succeeded"

    drafts_response = await assistant_client.get(
        "/v1/creative/drafts",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert drafts_response.status_code == 200
    drafts_body = drafts_response.json()
    assert drafts_body["total"] >= 1
    draft_id = int(drafts_body["items"][0]["id"])

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
    await process_pending_jobs()
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
    await process_pending_jobs()

    confirm_response = await assistant_client.post(
        "/v1/context/confirm",
        headers={"Authorization": f"Bearer {access_a}"},
    )
    assert confirm_response.status_code == 200
    confirm_job = confirm_response.json()
    assert confirm_job["status"] == "queued"
    await process_pending_jobs()

    confirm_status = await assistant_client.get(
        f"/v1/jobs/{confirm_job['job_id']}",
        headers={"Authorization": f"Bearer {access_a}"},
    )
    assert confirm_status.status_code == 200
    assert confirm_status.json()["status"] == "succeeded"

    confirmed_context = await assistant_client.get(
        "/v1/context",
        headers={"Authorization": f"Bearer {access_a}"},
    )
    assert confirmed_context.status_code == 200
    assert confirmed_context.json()["brief"]["status"] == "confirmed"

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


@pytest.mark.asyncio
async def test_cross_tenant_refresh_token_rejected(assistant_client: AsyncClient) -> None:
    """Refresh token forged for a different tenant must not grant access at the service layer."""
    from core.web_auth import make_refresh_token, refresh_web_session, TelegramAuthError

    # Create two independent tenants
    access_a, tenant_a, workspace_a = await _create_authorized_session(assistant_client, 9601)
    access_b, tenant_b, workspace_b = await _create_authorized_session(assistant_client, 9602)
    assert tenant_a != tenant_b

    # Forge a refresh token that claims tenant_b's IDs but has no matching DB row
    forged_token = make_refresh_token(
        user_id=99999,  # Non-existent user
        tenant_id=tenant_b,
        workspace_id=workspace_b,
        role="owner",
    )

    # Attempting refresh with the forged token must fail:
    # no DB row matches (token_hash, tenant_b, user_id=99999)
    with pytest.raises(TelegramAuthError, match="refresh_token_revoked"):
        async with async_session() as session:
            async with session.begin():
                await refresh_web_session(session, forged_token)

    # Also forge a token using tenant_a's real user but claiming tenant_b
    # This must also fail — the DB row is scoped to tenant_a
    # First get tenant_a's auth_user_id from the access token
    import jwt as pyjwt
    claims_a = pyjwt.decode(access_a, settings.JWT_ACCESS_SECRET, algorithms=[settings.JWT_ALGORITHM])
    real_user_id_a = int(claims_a["sub"])

    cross_tenant_token = make_refresh_token(
        user_id=real_user_id_a,
        tenant_id=tenant_b,  # Wrong tenant
        workspace_id=workspace_b,
        role="owner",
    )

    with pytest.raises(TelegramAuthError, match="refresh_token_revoked"):
        async with async_session() as session:
            async with session.begin():
                await refresh_web_session(session, cross_tenant_token)


@pytest.mark.asyncio
async def test_assistant_job_failure_is_exposed_via_job_status(
    assistant_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_token, _, _ = await _create_authorized_session(assistant_client, 9505)

    async def fail_start(*args, **kwargs):
        raise AssistantServiceError("brief_bootstrap_failed")

    monkeypatch.setattr("core.assistant_jobs.start_business_brief", fail_start)

    response = await assistant_client.post(
        "/v1/assistant/start-brief",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    await process_pending_jobs()

    job_status = await assistant_client.get(
        f"/v1/jobs/{payload['job_id']}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert job_status.status_code == 200
    body = job_status.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "brief_bootstrap_failed"
