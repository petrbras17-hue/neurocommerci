"""Sprint 14 — E2E Smoke Test Suite.

Проверяет все публичные и аутентифицированные эндпоинты API на корректные
HTTP-статусы. Не вызывает реальные AI/Telegram операции.

Запуск:
    pytest tests/test_e2e_smoke.py -v
"""
from __future__ import annotations

import time
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

import ops_api
from config import settings
from ops_api import app
from storage.models import (
    AuthUser,
    RefreshToken,
    TeamMember,
    Tenant,
    Workspace,
)
from storage.sqlite_db import async_session, init_db


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


async def _reset_smoke_state() -> None:
    """Очистить состояние БД перед каждым тестовым классом."""
    async with async_session() as session:
        async with session.begin():
            for model in [RefreshToken, TeamMember, Workspace, Tenant, AuthUser]:
                await session.execute(delete(model))


async def _register_user(client: AsyncClient, email: str, password: str = "TestPass123!") -> dict[str, Any]:
    """Зарегистрировать нового пользователя и вернуть тело ответа."""
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": "Smoke",
            "company": "SmokeTestCo",
        },
    )
    assert resp.status_code == 201, f"register failed: {resp.status_code} {resp.text}"
    return resp.json()


async def _login_user(client: AsyncClient, email: str, password: str = "TestPass123!") -> dict[str, Any]:
    """Войти и вернуть тело ответа с access_token."""
    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, f"login failed: {resp.status_code} {resp.text}"
    return resp.json()


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def smoke_client() -> AsyncClient:
    """Общий ASGI-клиент для smoke тестов."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Патчим JWT-секреты и APP_ENV чтобы обойти rate limiting."""
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "e2e-smoke-access-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "e2e-smoke-refresh-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 60)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    # APP_ENV=test отключает rate limiting
    monkeypatch.setattr(ops_api.settings, "APP_ENV", "test")
    await init_db()
    await _reset_smoke_state()
    yield
    await _reset_smoke_state()


# ---------------------------------------------------------------------------
# Группа 1: Публичные эндпоинты (без авторизации) — ожидаем 200
# ---------------------------------------------------------------------------


class TestPublicEndpoints:
    """Публичные маршруты не требуют токена."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_healthz(self, smoke_client: AsyncClient) -> None:
        resp = await smoke_client.get("/healthz")
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_health(self, smoke_client: AsyncClient) -> None:
        # В тестовой среде Redis недоступен — ожидаем 200 или 503
        resp = await smoke_client.get("/health")
        assert resp.status_code in (200, 503)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_landing_root(self, smoke_client: AsyncClient) -> None:
        resp = await smoke_client.get("/")
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_landing_ecom(self, smoke_client: AsyncClient) -> None:
        resp = await smoke_client.get("/ecom")
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_landing_edtech(self, smoke_client: AsyncClient) -> None:
        resp = await smoke_client.get("/edtech")
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_landing_saas(self, smoke_client: AsyncClient) -> None:
        resp = await smoke_client.get("/saas")
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_robots_txt(self, smoke_client: AsyncClient) -> None:
        resp = await smoke_client.get("/robots.txt")
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_sitemap_xml(self, smoke_client: AsyncClient) -> None:
        resp = await smoke_client.get("/sitemap.xml")
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_billing_plans_public(self, smoke_client: AsyncClient) -> None:
        """GET /v1/billing/plans публичный (не требует авторизации)."""
        resp = await smoke_client.get("/v1/billing/plans")
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_channel_map_category_tree_requires_auth(self, smoke_client: AsyncClient) -> None:
        """GET /v1/channel-map/category-tree требует авторизации."""
        resp = await smoke_client.get("/v1/channel-map/category-tree")
        # Эндпоинт требует JWT — должен вернуть 401
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Группа 2: Аутентификационные эндпоинты
# ---------------------------------------------------------------------------


class TestAuthEndpoints:
    """Регистрация, логин, refresh, logout, /auth/me."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_register_creates_user(self, smoke_client: AsyncClient) -> None:
        ts = int(time.time())
        resp = await smoke_client.post(
            "/auth/register",
            json={
                "email": f"e2e_smoke_{ts}_a@test.com",
                "password": "TestPass123!",
                "first_name": "Smoke",
                "company": "SmokeTestCo",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "access_token" in body
        assert body.get("user") is not None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_register_duplicate_email_fails(self, smoke_client: AsyncClient) -> None:
        ts = int(time.time())
        email = f"e2e_dup_{ts}@test.com"
        await smoke_client.post(
            "/auth/register",
            json={"email": email, "password": "TestPass123!", "first_name": "A", "company": "X"},
        )
        resp2 = await smoke_client.post(
            "/auth/register",
            json={"email": email, "password": "TestPass123!", "first_name": "B", "company": "Y"},
        )
        # Повторная регистрация должна провалиться
        assert resp2.status_code in (409, 422)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_login_returns_token(self, smoke_client: AsyncClient) -> None:
        ts = int(time.time())
        email = f"e2e_login_{ts}@test.com"
        await _register_user(smoke_client, email)
        body = await _login_user(smoke_client, email)
        assert body.get("access_token")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_login_wrong_password_401(self, smoke_client: AsyncClient) -> None:
        ts = int(time.time())
        email = f"e2e_wrong_{ts}@test.com"
        await _register_user(smoke_client, email)
        resp = await smoke_client.post(
            "/auth/login",
            json={"email": email, "password": "WrongPassword!"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio(loop_scope="session")
    async def test_auth_me_with_token(self, smoke_client: AsyncClient) -> None:
        ts = int(time.time())
        email = f"e2e_me_{ts}@test.com"
        reg = await _register_user(smoke_client, email)
        token = reg["access_token"]
        resp = await smoke_client.get("/auth/me", headers=_auth_headers(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["user"]["email"] == email

    @pytest.mark.asyncio(loop_scope="session")
    async def test_auth_me_without_token_401(self, smoke_client: AsyncClient) -> None:
        resp = await smoke_client.get("/auth/me")
        assert resp.status_code == 401

    @pytest.mark.asyncio(loop_scope="session")
    async def test_auth_refresh_without_cookie_returns_anonymous(self, smoke_client: AsyncClient) -> None:
        resp = await smoke_client.post("/auth/refresh")
        assert resp.status_code == 200
        assert resp.json().get("status") == "anonymous"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_auth_logout(self, smoke_client: AsyncClient) -> None:
        ts = int(time.time())
        email = f"e2e_logout_{ts}@test.com"
        await _register_user(smoke_client, email)
        resp = await smoke_client.post("/auth/logout")
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_telegram_widget_config(self, smoke_client: AsyncClient) -> None:
        resp = await smoke_client.get("/auth/telegram/widget-config")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Группа 3: Аутентифицированные GET-эндпоинты — ожидаем 200
# ---------------------------------------------------------------------------


class TestAuthenticatedGetEndpoints:
    """Все защищённые GET-маршруты должны вернуть 200 с валидным токеном."""

    @pytest_asyncio.fixture(loop_scope="session")
    async def token(self, smoke_client: AsyncClient) -> str:
        """Создаём и держим одного пользователя на всё время тестового класса."""
        ts = int(time.time())
        email = f"e2e_authed_{ts}@test.com"
        reg = await _register_user(smoke_client, email)
        return reg["access_token"]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_farm_list(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/farm", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_farm_stats_live(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/farm/stats/live", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_farm_comment_quality(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/farm/comment-quality", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_parser_jobs(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/parser/jobs", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_warmup_list(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/warmup", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_health_scores(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/health/scores", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_web_accounts(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/web/accounts", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_proxies_list(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/proxies", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_folders_list(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/folders", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_user_parser_results(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/user-parser/results", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_comments_styles(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/comments/styles", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_comments_custom_styles(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/comments/custom-styles", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_analytics_heatmap(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/analytics/heatmap", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_billing_subscription(self, smoke_client: AsyncClient, token: str) -> None:
        # Нет активной подписки — ожидаем 200 или 404
        resp = await smoke_client.get("/v1/billing/subscription", headers=_auth_headers(token))
        assert resp.status_code in (200, 404)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_billing_payments(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/billing/payments", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_channel_map(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/channel-map", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_channel_map_clusters(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/channel-map/clusters?zoom=3", headers=_auth_headers(token))
        # 200 — OK, 422 может быть если SQLite не поддерживает функцию math.floor в контексте
        assert resp.status_code in (200, 422), f"clusters returned {resp.status_code}: {resp.text[:300]}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_channel_map_stats(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/channel-map/stats", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_channel_map_categories(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/channel-map/categories", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_campaigns_list(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/campaigns", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_system_resource_estimate(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/system/resource-estimate", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_assistant_thread(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/assistant/thread", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_creative_drafts(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/creative/drafts", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_context(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/context", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_analytics_dashboard(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/analytics/dashboard", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reactions_list(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/reactions", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_chatting_list(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/chatting", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_dialogs_list(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/dialogs", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_profiles_templates(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/profiles/templates", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_channel_db_list(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/channel-db", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reports_weekly(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/reports/weekly", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_healing_log(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/healing/log", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_purchases_requests(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/purchases/requests", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_platform_alerts(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/platform/alerts", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_comments_feed(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/comments/feed", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_me_workspace(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/me/workspace", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_me_team(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/me/team", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_ai_quality_summary(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/ai/quality-summary", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_commenting_strategies(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/commenting/strategies", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_channel_map_category_tree(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/channel-map/category-tree", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_accounts_stats(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/accounts/stats", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_health_quarantine(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/health/quarantine", headers=_auth_headers(token))
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="session")
    async def test_auth_sessions(self, smoke_client: AsyncClient, token: str) -> None:
        resp = await smoke_client.get("/v1/auth/sessions" if False else "/auth/sessions", headers=_auth_headers(token))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Группа 4: Защищённые эндпоинты без токена — ожидаем 401
# ---------------------------------------------------------------------------


class TestUnauthenticatedReturns401:
    """Все защищённые маршруты без токена должны вернуть 401."""

    PROTECTED_ENDPOINTS = [
        "/v1/farm",
        "/v1/farm/stats/live",
        "/v1/parser/jobs",
        "/v1/warmup",
        "/v1/health/scores",
        "/v1/web/accounts",
        "/v1/proxies",
        "/v1/folders",
        "/v1/user-parser/results",
        "/v1/comments/styles",
        "/v1/comments/custom-styles",
        "/v1/analytics/heatmap",
        "/v1/billing/subscription",
        "/v1/billing/payments",
        "/v1/channel-map",
        "/v1/channel-map/stats",
        "/v1/channel-map/categories",
        "/v1/campaigns",
        "/v1/system/resource-estimate",
        "/v1/assistant/thread",
        "/v1/creative/drafts",
        "/v1/context",
        "/v1/me/workspace",
        "/v1/me/team",
    ]

    @pytest.mark.asyncio(loop_scope="session")
    @pytest.mark.parametrize("endpoint", PROTECTED_ENDPOINTS)
    async def test_protected_endpoint_requires_auth(
        self, smoke_client: AsyncClient, endpoint: str
    ) -> None:
        resp = await smoke_client.get(endpoint)
        assert resp.status_code == 401, (
            f"Эндпоинт {endpoint} вернул {resp.status_code}, ожидался 401"
        )


# ---------------------------------------------------------------------------
# Группа 5: Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Проверка rate limiting на /auth/login (только в production-режиме).

    В test-режиме rate limiting отключён, поэтому этот тест намеренно
    проверяет только то, что 429 не появляется в тестовой среде.
    """

    @pytest.mark.asyncio(loop_scope="session")
    async def test_rate_limit_not_triggered_in_test_mode(self, smoke_client: AsyncClient) -> None:
        """В APP_ENV=test rate limiting должен быть отключён."""
        ts = int(time.time())
        email = f"e2e_rl_{ts}@test.com"
        password = "TestPass123!"
        await _register_user(smoke_client, email, password)

        # Делаем 12 попыток логина — в test-режиме все должны вернуть 200, а не 429
        statuses = []
        for _ in range(12):
            resp = await smoke_client.post(
                "/auth/login",
                json={"email": email, "password": password},
            )
            statuses.append(resp.status_code)

        # В test-режиме никаких 429
        assert 429 not in statuses, (
            f"Rate limiting не должен срабатывать в APP_ENV=test, статусы: {statuses}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_invalid_login_returns_401_not_429(self, smoke_client: AsyncClient) -> None:
        """Неверный пароль возвращает 401, а не 429 (в test-режиме)."""
        resp = await smoke_client.post(
            "/auth/login",
            json={"email": "nonexistent@test.com", "password": "wrong"},
        )
        # 401 — неверные данные, 422 — email не существует
        assert resp.status_code in (401, 422)
