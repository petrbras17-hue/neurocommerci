from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import ops_api
from ops_api import app
from storage.models import Lead
from storage.sqlite_db import async_session, init_db


@pytest_asyncio.fixture(loop_scope="session")
async def marketing_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_leads() -> None:
    await init_db()
    async with async_session() as session:
        async with session.begin():
            await session.execute(delete(Lead))
    yield
    async with async_session() as session:
        async with session.begin():
            await session.execute(delete(Lead))


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    ("path", "headline"),
    [
        ("/", "Telegram Growth OS для брендов"),
        ("/ecom", "Рост интернет-магазина"),
        ("/edtech", "Telegram-рост для онлайн-школ"),
        ("/saas", "Telegram Growth OS для SaaS-команд"),
    ],
)
async def test_marketing_pages_render(marketing_client: AsyncClient, path: str, headline: str) -> None:
    response = await marketing_client.get(path)
    assert response.status_code == 200
    assert headline in response.text
    assert "NEURO COMMENTING" in response.text
    assert "Получить доступ и growth-разбор" in response.text
    assert "Популярный выбор" in response.text


@pytest.mark.asyncio(loop_scope="session")
async def test_lead_form_submission_persists_record_and_triggers_side_effects(
    marketing_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_deliver(snapshot):
        captured["lead"] = snapshot
        return {
            "lead_id": snapshot.lead_id,
            "google_sheets": {"ok": True},
            "admin_bot": {"ok": True},
            "digest_bot": {"ok": True},
        }

    monkeypatch.setattr(ops_api, "deliver_lead_funnel", fake_deliver)

    payload = {
        "name": "Petr",
        "email": "petr@example.com",
        "company": "Slice Pizza",
        "telegram_username": "@petrbras",
        "use_case": "saas",
    }
    response = await marketing_client.post("/api/leads?utm_source=telegram", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["ok"] is True
    assert "48 часов" in body["message"]

    async with async_session() as session:
        saved = (
            await session.execute(select(Lead).where(Lead.email == "petr@example.com"))
        ).scalar_one()
    assert saved.name == "Petr"
    assert saved.company == "Slice Pizza"
    assert saved.telegram_username == "petrbras"
    assert saved.use_case == "saas"
    assert saved.utm_source == "telegram"
    assert captured["lead"].email == "petr@example.com"
    assert captured["lead"].telegram_username == "petrbras"


@pytest.mark.asyncio(loop_scope="session")
async def test_lead_form_works_without_telegram_username(
    marketing_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_deliver(_snapshot):
        return {"ok": True}

    monkeypatch.setattr(ops_api, "deliver_lead_funnel", fake_deliver)

    payload = {
        "name": "Irina",
        "email": "irina@example.com",
        "company": "NeuroSaaS",
        "use_case": "edtech",
    }

    response = await marketing_client.post("/api/leads", json=payload)
    assert response.status_code == 201

    async with async_session() as session:
        saved = (
            await session.execute(select(Lead).where(Lead.email == "irina@example.com"))
        ).scalar_one()
    assert saved.telegram_username is None


@pytest.mark.asyncio(loop_scope="session")
async def test_invalid_lead_payload_returns_validation_error(marketing_client: AsyncClient) -> None:
    response = await marketing_client.post(
        "/api/leads",
        json={
            "name": "",
            "email": "invalid",
            "company": "",
            "use_case": "",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
async def test_seo_routes_render(marketing_client: AsyncClient) -> None:
    robots = await marketing_client.get("/robots.txt")
    sitemap = await marketing_client.get("/sitemap.xml")

    assert robots.status_code == 200
    assert "Sitemap: /sitemap.xml" in robots.text
    assert sitemap.status_code == 200
    assert "<urlset" in sitemap.text
    assert "/ecom" in sitemap.text


@pytest.mark.asyncio(loop_scope="session")
async def test_internal_leads_endpoint_requires_internal_token_and_returns_recent_items(
    marketing_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_api.settings, "OPS_API_TOKEN", "internal-test-token")

    async def fake_deliver(_snapshot):
        return {"ok": True}

    monkeypatch.setattr(ops_api, "deliver_lead_funnel", fake_deliver)

    await marketing_client.post(
        "/api/leads",
        json={
            "name": "Lead One",
            "email": "lead1@example.com",
            "company": "Company One",
            "use_case": "ecom",
        },
    )

    unauthorized = await marketing_client.get("/v1/internal/leads")
    assert unauthorized.status_code == 401

    authorized = await marketing_client.get(
        "/v1/internal/leads",
        headers={"Authorization": "Bearer internal-test-token"},
    )
    assert authorized.status_code == 200
    body = authorized.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == "lead1@example.com"
