from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import ops_api
from config import settings
from storage.models import (
    Account,
    AccountOnboardingRun,
    AccountOnboardingStep,
    AuthUser,
    Proxy,
    RefreshToken,
    TeamMember,
    Tenant,
    User,
    Workspace,
)
from storage.sqlite_db import async_session, init_db
from utils.helpers import utcnow


def _telegram_payload(*, bot_token: str, telegram_user_id: int, username: str) -> dict[str, object]:
    auth_date = int(utcnow().timestamp())
    payload: dict[str, object] = {
        "id": telegram_user_id,
        "auth_date": auth_date,
        "username": username,
        "first_name": username.capitalize(),
    }
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    payload["hash"] = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return payload


async def _reset_web_account_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in [
                AccountOnboardingStep,
                AccountOnboardingRun,
                Account,
                Proxy,
                RefreshToken,
                TeamMember,
                Workspace,
                Tenant,
                AuthUser,
                User,
            ]:
                await session.execute(delete(model))


async def _create_authorized_session(client: AsyncClient, telegram_user_id: int) -> tuple[str, int, int]:
    verify = await client.post(
        "/auth/telegram/verify",
        json=_telegram_payload(bot_token=settings.ADMIN_BOT_TOKEN, telegram_user_id=telegram_user_id, username=f"user{telegram_user_id}"),
    )
    setup_token = verify.json()["setup_token"]
    complete = await client.post(
        "/auth/complete-profile",
        json={
            "setup_token": setup_token,
            "email": f"user{telegram_user_id}@example.com",
            "company": f"Company {telegram_user_id}",
        },
    )
    body = complete.json()
    return body["access_token"], int(body["tenant"]["id"]), int(body["workspace"]["id"])


@pytest_asyncio.fixture(loop_scope="session")
async def web_accounts_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=ops_api.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ops_api.settings, "ADMIN_BOT_TOKEN", "telegram-test-token")
    monkeypatch.setattr(ops_api.settings, "SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "web-auth-access-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "web-auth-refresh-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    await init_db()
    await _reset_web_account_state()
    yield
    await _reset_web_account_state()


@pytest.mark.asyncio
async def test_web_account_upload_bind_and_audit_flow(
    web_accounts_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_token, tenant_id, workspace_id = await _create_authorized_session(web_accounts_client, 9101)

    files = {
        "session_file": ("79991234567.session", b"sqlite-session-bytes"),
        "metadata_file": (
            "79991234567.json",
            json.dumps(
                {
                    "phone": "+79991234567",
                    "session_file": "79991234567.session",
                    "app_id": 123456,
                    "app_hash": "abcdef1234567890abcdef1234567890",
                    "device": "iPhone 15 Pro",
                    "sdk": "17.4",
                    "app_version": "11.2",
                }
            ).encode("utf-8"),
            "application/json",
        ),
    }
    upload_response = await web_accounts_client.post(
        "/v1/web/accounts/upload",
        headers={"Authorization": f"Bearer {access_token}"},
        files=files,
    )
    assert upload_response.status_code == 200
    upload_body = upload_response.json()
    assert upload_body["bundle_ready"] is True

    async with async_session() as session:
        async with session.begin():
            workspace = (
                await session.execute(
                    select(Workspace).where(Workspace.id == workspace_id, Workspace.tenant_id == tenant_id)
                )
            ).scalar_one()
            proxy = Proxy(
                user_id=workspace.runtime_user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                proxy_type="socks5",
                host="proxy.example.com",
                port=1080,
                username="u",
                password="p",
                is_active=True,
                health_status="alive",
            )
            session.add(proxy)

    accounts_response = await web_accounts_client.get(
        "/v1/web/accounts",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert accounts_response.status_code == 200
    account_row = accounts_response.json()["items"][0]
    assert account_row["phone"] == "+79991234567"

    proxies_response = await web_accounts_client.get(
        "/v1/web/proxies/available",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert proxies_response.status_code == 200
    proxy_id = proxies_response.json()["items"][0]["id"]

    bind_response = await web_accounts_client.post(
        f"/v1/web/accounts/{account_row['id']}/bind-proxy",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"proxy_id": proxy_id},
    )
    assert bind_response.status_code == 200
    assert bind_response.json()["proxy_id"] == proxy_id

    async def fake_auth_refresh_for_phone(*args, **kwargs):
        return {
            "count": 1,
            "authorized": 1,
            "results": [
                {
                    "phone": "+79991234567",
                    "authorized": True,
                    "probe_status": "authorized",
                }
            ],
        }

    monkeypatch.setattr("core.web_accounts.run_auth_refresh_for_phone", fake_auth_refresh_for_phone)

    audit_response = await web_accounts_client.post(
        f"/v1/web/accounts/{account_row['id']}/audit",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert audit_response.status_code == 200
    assert audit_response.json()["account"]["phone"] == "+79991234567"


@pytest.mark.asyncio
async def test_tenant_isolation_for_web_accounts_and_proxies(web_accounts_client: AsyncClient) -> None:
    access_a, tenant_a, workspace_a = await _create_authorized_session(web_accounts_client, 9201)
    access_b, tenant_b, workspace_b = await _create_authorized_session(web_accounts_client, 9202)

    async with async_session() as session:
        async with session.begin():
            workspace_a_row = (
                await session.execute(select(Workspace).where(Workspace.id == workspace_a))
            ).scalar_one()
            workspace_b_row = (
                await session.execute(select(Workspace).where(Workspace.id == workspace_b))
            ).scalar_one()
            session.add(
                Account(
                    phone="+70000000001",
                    session_file="70000000001.session",
                    user_id=workspace_a_row.runtime_user_id,
                    tenant_id=tenant_a,
                    workspace_id=workspace_a,
                    status="active",
                    health_status="alive",
                    lifecycle_stage="auth_verified",
                )
            )
            session.add(
                Account(
                    phone="+70000000002",
                    session_file="70000000002.session",
                    user_id=workspace_b_row.runtime_user_id,
                    tenant_id=tenant_b,
                    workspace_id=workspace_b,
                    status="active",
                    health_status="alive",
                    lifecycle_stage="auth_verified",
                )
            )
            session.add(
                Proxy(
                    user_id=workspace_a_row.runtime_user_id,
                    tenant_id=tenant_a,
                    workspace_id=workspace_a,
                    proxy_type="socks5",
                    host="tenant-a.proxy",
                    port=1080,
                    is_active=True,
                    health_status="alive",
                )
            )
            session.add(
                Proxy(
                    user_id=workspace_b_row.runtime_user_id,
                    tenant_id=tenant_b,
                    workspace_id=workspace_b,
                    proxy_type="socks5",
                    host="tenant-b.proxy",
                    port=1081,
                    is_active=True,
                    health_status="alive",
                )
            )

    accounts_a = await web_accounts_client.get("/v1/web/accounts", headers={"Authorization": f"Bearer {access_a}"})
    accounts_b = await web_accounts_client.get("/v1/web/accounts", headers={"Authorization": f"Bearer {access_b}"})
    proxies_a = await web_accounts_client.get("/v1/web/proxies/available", headers={"Authorization": f"Bearer {access_a}"})
    proxies_b = await web_accounts_client.get("/v1/web/proxies/available", headers={"Authorization": f"Bearer {access_b}"})

    assert [item["phone"] for item in accounts_a.json()["items"]] == ["+70000000001"]
    assert [item["phone"] for item in accounts_b.json()["items"]] == ["+70000000002"]
    assert [item["host"] for item in proxies_a.json()["items"]] == ["tenant-a.proxy"]
    assert [item["host"] for item in proxies_b.json()["items"]] == ["tenant-b.proxy"]


@pytest.mark.asyncio
async def test_upload_requires_both_files_and_returns_clean_error(web_accounts_client: AsyncClient) -> None:
    access_token, _, _ = await _create_authorized_session(web_accounts_client, 9301)
    response = await web_accounts_client.post(
        "/v1/web/accounts/upload",
        headers={"Authorization": f"Bearer {access_token}"},
        files={"session_file": ("700.session", b"abc")},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_account_notes_and_timeline_are_saved_and_scoped(
    web_accounts_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_a, tenant_a, workspace_a = await _create_authorized_session(web_accounts_client, 9401)
    access_b, tenant_b, workspace_b = await _create_authorized_session(web_accounts_client, 9402)

    files = {
        "session_file": ("79990000001.session", b"sqlite-session-bytes"),
        "metadata_file": (
            "79990000001.json",
            json.dumps(
                {
                    "phone": "+79990000001",
                    "session_file": "79990000001.session",
                    "app_id": 123456,
                    "app_hash": "abcdef1234567890abcdef1234567890",
                    "device": "iPhone 15 Pro",
                    "sdk": "17.4",
                    "app_version": "11.2",
                }
            ).encode("utf-8"),
            "application/json",
        ),
    }
    upload_response = await web_accounts_client.post(
        "/v1/web/accounts/upload",
        headers={"Authorization": f"Bearer {access_a}"},
        files=files,
    )
    account_id = int(upload_response.json()["account_id"])

    async with async_session() as session:
        async with session.begin():
            workspace_a_row = (
                await session.execute(select(Workspace).where(Workspace.id == workspace_a))
            ).scalar_one()
            workspace_b_row = (
                await session.execute(select(Workspace).where(Workspace.id == workspace_b))
            ).scalar_one()
            session.add(
                Proxy(
                    user_id=workspace_a_row.runtime_user_id,
                    tenant_id=tenant_a,
                    workspace_id=workspace_a,
                    proxy_type="socks5",
                    host="timeline-a.proxy",
                    port=1080,
                    is_active=True,
                    health_status="alive",
                )
            )
            session.add(
                Proxy(
                    user_id=workspace_b_row.runtime_user_id,
                    tenant_id=tenant_b,
                    workspace_id=workspace_b,
                    proxy_type="socks5",
                    host="timeline-b.proxy",
                    port=1081,
                    is_active=True,
                    health_status="alive",
                )
            )

    async def fake_auth_refresh_for_phone(*args, **kwargs):
        return {
            "count": 1,
            "authorized": 1,
            "results": [
                {
                    "phone": "+79990000001",
                    "authorized": True,
                    "probe_status": "authorized",
                }
            ],
        }

    monkeypatch.setattr("core.web_accounts.run_auth_refresh_for_phone", fake_auth_refresh_for_phone)
    await web_accounts_client.post(
        f"/v1/web/accounts/{account_id}/audit",
        headers={"Authorization": f"Bearer {access_a}"},
    )

    note_response = await web_accounts_client.post(
        f"/v1/web/accounts/{account_id}/notes",
        headers={"Authorization": f"Bearer {access_a}"},
        json={"notes": "Оператор проверил safe-shell путь и пока не запускает реальные account actions."},
    )
    assert note_response.status_code == 200
    assert note_response.json()["manual_notes"] == "Оператор проверил safe-shell путь и пока не запускает реальные account actions."

    accounts_response = await web_accounts_client.get(
        "/v1/web/accounts",
        headers={"Authorization": f"Bearer {access_a}"},
    )
    account_row = accounts_response.json()["items"][0]
    assert account_row["manual_notes"] == "Оператор проверил safe-shell путь и пока не запускает реальные account actions."
    assert isinstance(account_row["recent_steps"], list)

    timeline_response = await web_accounts_client.get(
        f"/v1/web/accounts/{account_id}/timeline",
        headers={"Authorization": f"Bearer {access_a}"},
    )
    assert timeline_response.status_code == 200
    titles = [item["title"] for item in timeline_response.json()["items"]]
    assert "Ручная заметка обновлена" in titles
    assert "Заметка оператора" in titles

    forbidden = await web_accounts_client.get(
        f"/v1/web/accounts/{account_id}/timeline",
        headers={"Authorization": f"Bearer {access_b}"},
    )
    assert forbidden.status_code == 400
    assert forbidden.json()["detail"] == "account_not_found"
