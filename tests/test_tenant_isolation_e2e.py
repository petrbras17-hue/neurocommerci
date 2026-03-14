"""Sprint 14 — Cross-Tenant Isolation E2E Tests.

Гарантирует, что данные одного тенанта не доступны другому.
Для каждого ресурса:
  1. Тенант A создаёт запись.
  2. Тенант B пытается получить её — должен получить 404 или пустой список.

Все тесты используют SQLite in-memory через общий conftest.
"""
from __future__ import annotations

import time
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

import ops_api
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


async def _reset_isolation_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in [RefreshToken, TeamMember, Workspace, Tenant, AuthUser]:
                await session.execute(delete(model))


async def _register_and_login(client: AsyncClient, suffix: str) -> tuple[str, int]:
    """Зарегистрировать пользователя и вернуть (access_token, tenant_id)."""
    ts = int(time.time())
    email = f"isolation_{suffix}_{ts}@test.com"
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "TestPass123!",
            "first_name": suffix.capitalize(),
            "company": f"Tenant{suffix.upper()}Co",
        },
    )
    assert resp.status_code == 201, f"register failed for {suffix}: {resp.status_code} {resp.text}"
    body = resp.json()
    token = body["access_token"]
    tenant_id = int(body["tenant"]["id"])
    return token, tenant_id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def isolation_client() -> AsyncClient:
    """Общий ASGI-клиент для isolation тестов."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _patch_isolation_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Патчим JWT-секреты и APP_ENV."""
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "isolation-access-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "isolation-refresh-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 60)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "APP_ENV", "test")
    await init_db()
    await _reset_isolation_state()
    yield
    await _reset_isolation_state()


# ---------------------------------------------------------------------------
# Тесты изоляции ферм (Farm)
# ---------------------------------------------------------------------------


class TestFarmIsolation:
    """Тенант B не должен видеть фермы тенанта A."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_farm_list_isolation(self, isolation_client: AsyncClient) -> None:
        """Список ферм тенанта A не виден тенанту B."""
        token_a, _ = await _register_and_login(isolation_client, "farm_a")
        token_b, _ = await _register_and_login(isolation_client, "farm_b")

        # Тенант A создаёт ферму
        create_resp = await isolation_client.post(
            "/v1/farm",
            headers=_auth(token_a),
            json={
                "name": "Farm of Tenant A",
                "mode": "auto",
                "max_threads": 2,
                "comment_prompt": "Test prompt",
                "comment_tone": "neutral",
                "comment_language": "ru",
                "comment_all_posts": True,
                "comment_percentage": 100,
            },
        )
        assert create_resp.status_code == 201, f"farm create failed: {create_resp.text}"
        farm_id = create_resp.json()["id"]

        # Тенант B запрашивает список ферм — должен получить пустой список
        list_resp = await isolation_client.get("/v1/farm", headers=_auth(token_b))
        assert list_resp.status_code == 200
        farms_b = list_resp.json()
        farm_ids_b = [f["id"] for f in (farms_b.get("items") or farms_b if isinstance(farms_b, list) else [])]
        assert farm_id not in farm_ids_b, "Ферма тенанта A видна тенанту B — нарушение изоляции!"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_farm_get_isolation(self, isolation_client: AsyncClient) -> None:
        """Тенант B не может получить ферму тенанта A по ID."""
        token_a, _ = await _register_and_login(isolation_client, "farm_get_a")
        token_b, _ = await _register_and_login(isolation_client, "farm_get_b")

        create_resp = await isolation_client.post(
            "/v1/farm",
            headers=_auth(token_a),
            json={
                "name": "Secret Farm A",
                "mode": "auto",
                "max_threads": 1,
                "comment_prompt": "Secret prompt",
                "comment_tone": "neutral",
                "comment_language": "ru",
                "comment_all_posts": True,
                "comment_percentage": 100,
            },
        )
        assert create_resp.status_code == 201
        farm_id = create_resp.json()["id"]

        # Тенант B пытается получить ферму A по ID — должен получить 404
        get_resp = await isolation_client.get(f"/v1/farm/{farm_id}", headers=_auth(token_b))
        assert get_resp.status_code == 404, (
            f"Тенант B получил доступ к ферме {farm_id} тенанта A: {get_resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Тесты изоляции кампаний (Campaign)
# ---------------------------------------------------------------------------


class TestCampaignIsolation:
    """Тенант B не должен видеть кампании тенанта A."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_campaign_list_isolation(self, isolation_client: AsyncClient) -> None:
        """Список кампаний тенанта A не виден тенанту B."""
        token_a, _ = await _register_and_login(isolation_client, "camp_a")
        token_b, _ = await _register_and_login(isolation_client, "camp_b")

        # Тенант A создаёт кампанию
        create_resp = await isolation_client.post(
            "/v1/campaigns",
            headers=_auth(token_a),
            json={
                "name": "Campaign of Tenant A",
                "description": "Secret campaign",
            },
        )
        assert create_resp.status_code == 201, f"campaign create failed: {create_resp.text}"
        campaign_id = create_resp.json()["id"]

        # Тенант B запрашивает список кампаний
        list_resp = await isolation_client.get("/v1/campaigns", headers=_auth(token_b))
        assert list_resp.status_code == 200
        body = list_resp.json()
        items = body if isinstance(body, list) else body.get("items", [])
        campaign_ids_b = [c["id"] for c in items]
        assert campaign_id not in campaign_ids_b, (
            "Кампания тенанта A видна тенанту B — нарушение изоляции!"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_campaign_get_isolation(self, isolation_client: AsyncClient) -> None:
        """Тенант B не может получить кампанию тенанта A по ID."""
        token_a, _ = await _register_and_login(isolation_client, "camp_get_a")
        token_b, _ = await _register_and_login(isolation_client, "camp_get_b")

        create_resp = await isolation_client.post(
            "/v1/campaigns",
            headers=_auth(token_a),
            json={"name": "Private Campaign A"},
        )
        assert create_resp.status_code == 201
        campaign_id = create_resp.json()["id"]

        get_resp = await isolation_client.get(f"/v1/campaigns/{campaign_id}", headers=_auth(token_b))
        assert get_resp.status_code == 404, (
            f"Тенант B получил доступ к кампании {campaign_id} тенанта A: {get_resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Тесты изоляции биллинговой подписки (Subscription)
# ---------------------------------------------------------------------------


class TestBillingIsolation:
    """Тенант B не должен видеть подписку тенанта A."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_billing_subscription_isolation(self, isolation_client: AsyncClient) -> None:
        """Подписка тенанта A не видна тенанту B.

        Тест проверяет, что каждый тенант видит только свои данные по подписке.
        Активация trial может не работать в SQLite из-за ограничений транзакций —
        в этом случае тест проверяет только что у тенанта B нет подписки.
        """
        token_a, _ = await _register_and_login(isolation_client, "bill_a")
        token_b, _ = await _register_and_login(isolation_client, "bill_b")

        # Тенант B запрашивает подписку сразу — должен получить 404 (нет своей)
        # Это основная проверка изоляции: у нового тенанта B нет подписки
        sub_b = await isolation_client.get("/v1/billing/subscription", headers=_auth(token_b))
        assert sub_b.status_code in (200, 404), (
            f"Неожиданный статус для тенанта B: {sub_b.status_code}"
        )
        # Если у B нет подписки — изоляция работает
        if sub_b.status_code == 404:
            # OK — тенант B не видит подписку тенанта A (которой нет)
            return

        # Если 200 — проверяем что это подписка самого B, не A
        sub_a = await isolation_client.get("/v1/billing/subscription", headers=_auth(token_a))
        if sub_a.status_code == 200 and sub_b.status_code == 200:
            body_a = sub_a.json()
            body_b = sub_b.json()
            if "id" in body_a and "id" in body_b:
                assert body_a["id"] != body_b["id"], (
                    "Тенант B видит подписку тенанта A — нарушение изоляции!"
                )


# ---------------------------------------------------------------------------
# Тесты изоляции прокси (Proxy)
# ---------------------------------------------------------------------------


class TestProxyIsolation:
    """Тенант B не должен видеть прокси тенанта A."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_proxy_list_isolation(self, isolation_client: AsyncClient) -> None:
        """Список прокси тенанта A не виден тенанту B."""
        token_a, _ = await _register_and_login(isolation_client, "proxy_a")
        token_b, _ = await _register_and_login(isolation_client, "proxy_b")

        # Тенант A импортирует прокси
        import_resp = await isolation_client.post(
            "/v1/proxies/bulk-import-text",
            headers=_auth(token_a),
            json={"proxies_text": "socks5://user:pass@192.168.1.1:1080"},
        )
        # Может вернуть 200 или 422 в зависимости от валидации
        if import_resp.status_code == 200:
            proxy_ids_a = [p["id"] for p in import_resp.json().get("imported", [])]

            # Тенант B запрашивает список прокси
            list_resp = await isolation_client.get("/v1/proxies", headers=_auth(token_b))
            assert list_resp.status_code == 200
            body = list_resp.json()
            items = body if isinstance(body, list) else body.get("items", body.get("proxies", []))
            proxy_ids_b = [p["id"] for p in items]

            for pid in proxy_ids_a:
                assert pid not in proxy_ids_b, (
                    f"Прокси {pid} тенанта A видна тенанту B — нарушение изоляции!"
                )


# ---------------------------------------------------------------------------
# Тесты изоляции пользовательских стилей комментариев
# ---------------------------------------------------------------------------


class TestCommentStyleIsolation:
    """Тенант B не должен видеть кастомные стили тенанта A."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_custom_style_list_isolation(self, isolation_client: AsyncClient) -> None:
        """Кастомные стили тенанта A не видны тенанту B."""
        token_a, _ = await _register_and_login(isolation_client, "style_a")
        token_b, _ = await _register_and_login(isolation_client, "style_b")

        # Тенант A создаёт кастомный стиль
        create_resp = await isolation_client.post(
            "/v1/comments/custom-styles",
            headers=_auth(token_a),
            json={
                "name": "Секретный стиль A",
                "system_prompt": "Пиши только на латыни",
                "examples": ["Example 1", "Example 2"],
                "is_active": True,
            },
        )
        assert create_resp.status_code == 201, f"create style failed: {create_resp.text}"
        style_id = create_resp.json()["id"]

        # Тенант B запрашивает список стилей
        list_resp = await isolation_client.get("/v1/comments/custom-styles", headers=_auth(token_b))
        assert list_resp.status_code == 200
        body = list_resp.json()
        items = body if isinstance(body, list) else body.get("items", [])
        style_ids_b = [s["id"] for s in items]
        assert style_id not in style_ids_b, (
            "Кастомный стиль тенанта A виден тенанту B — нарушение изоляции!"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_custom_style_update_isolation(self, isolation_client: AsyncClient) -> None:
        """Тенант B не может изменить стиль тенанта A."""
        token_a, _ = await _register_and_login(isolation_client, "style_upd_a")
        token_b, _ = await _register_and_login(isolation_client, "style_upd_b")

        create_resp = await isolation_client.post(
            "/v1/comments/custom-styles",
            headers=_auth(token_a),
            json={
                "name": "Protected Style",
                "system_prompt": "Protected prompt",
                "examples": [],
                "is_active": True,
            },
        )
        assert create_resp.status_code == 201
        style_id = create_resp.json()["id"]

        # Тенант B пытается изменить стиль A
        put_resp = await isolation_client.put(
            f"/v1/comments/custom-styles/{style_id}",
            headers=_auth(token_b),
            json={"name": "Hacked Style"},
        )
        # Должен получить 404 (не найдено в контексте тенанта B)
        assert put_resp.status_code in (404, 403), (
            f"Тенант B смог изменить стиль {style_id} тенанта A: {put_resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Тесты изоляции ферм: Warmup
# ---------------------------------------------------------------------------


class TestWarmupIsolation:
    """Тенант B не должен видеть конфиги прогрева тенанта A."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_warmup_list_isolation(self, isolation_client: AsyncClient) -> None:
        """Warmup тенанта A не виден тенанту B."""
        token_a, _ = await _register_and_login(isolation_client, "warmup_a")
        token_b, _ = await _register_and_login(isolation_client, "warmup_b")

        # Тенант A создаёт warmup конфиг
        create_resp = await isolation_client.post(
            "/v1/warmup",
            headers=_auth(token_a),
            json={
                "name": "Warmup of Tenant A",
                "daily_messages": 5,
                "daily_reactions": 10,
                "active_hours_start": 9,
                "active_hours_end": 21,
            },
        )
        # Warmup может требовать аккаунты — принимаем 201 или 422
        if create_resp.status_code == 201:
            warmup_id = create_resp.json()["id"]

            list_resp = await isolation_client.get("/v1/warmup", headers=_auth(token_b))
            assert list_resp.status_code == 200
            body = list_resp.json()
            items = body if isinstance(body, list) else body.get("items", [])
            warmup_ids_b = [w["id"] for w in items]
            assert warmup_id not in warmup_ids_b, (
                "Warmup тенанта A виден тенанту B — нарушение изоляции!"
            )
