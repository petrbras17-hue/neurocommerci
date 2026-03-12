"""
Tests for account deduplication check and bulk export endpoints.

Coverage:
- POST /v1/accounts/check-duplicates
  - detects existing phones for the tenant
  - returns new phones when no duplicates exist
  - tenant isolation: cannot see another tenant's accounts
- GET /v1/accounts/export
  - CSV format: correct columns and data
  - JSON format: correct structure
  - tenant isolation: cannot export another tenant's accounts
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _telegram_payload(*, bot_token: str, telegram_user_id: int, username: str) -> dict:
    auth_date = int(utcnow().timestamp())
    payload: dict = {
        "id": telegram_user_id,
        "auth_date": auth_date,
        "username": username,
        "first_name": username.capitalize(),
    }
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hashlib.sha256(bot_token.encode()).digest()
    payload["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return payload


async def _create_authorized_session(
    client: AsyncClient, telegram_user_id: int
) -> tuple[str, int, int]:
    verify = await client.post(
        "/auth/telegram/verify",
        json=_telegram_payload(
            bot_token=settings.ADMIN_BOT_TOKEN,
            telegram_user_id=telegram_user_id,
            username=f"user{telegram_user_id}",
        ),
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


async def _reset_state() -> None:
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


async def _insert_account(
    *,
    phone: str,
    tenant_id: int,
    workspace_id: int,
    status: str = "active",
    lifecycle_stage: str = "uploaded",
) -> int:
    """Insert a bare Account row directly; returns the new account id."""
    async with async_session() as session:
        async with session.begin():
            acc = Account(
                phone=phone,
                session_file=f"{''.join(ch for ch in phone if ch.isdigit())}.session",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                status=status,
                health_status="unknown",
                lifecycle_stage=lifecycle_stage,
                created_at=utcnow(),
            )
            session.add(acc)
        await session.refresh(acc)
        return int(acc.id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def dedup_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=ops_api.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ops_api.settings, "ADMIN_BOT_TOKEN", "dedup-test-token")
    monkeypatch.setattr(ops_api.settings, "SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "dedup-access-secret-1234567890ab")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "dedup-refresh-secret-1234567890ab")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    await init_db()
    await _reset_state()
    yield
    await _reset_state()


# ---------------------------------------------------------------------------
# Duplicate detection tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_duplicates_detects_existing_phone(
    dedup_client: AsyncClient,
) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(dedup_client, 55001)
    await _insert_account(phone="+79991110001", tenant_id=tenant_id, workspace_id=workspace_id)

    resp = await dedup_client.post(
        "/v1/accounts/check-duplicates",
        headers={"Authorization": f"Bearer {token}"},
        json={"phones": ["+79991110001", "+79991110002"]},
    )
    assert resp.status_code == 200
    body = resp.json()

    dup_phones = [d["phone"] for d in body["duplicates"]]
    assert "+79991110001" in dup_phones
    assert "+79991110002" not in dup_phones
    assert "+79991110002" in body["new"]

    # existing_account_id must be present and non-zero
    entry = next(d for d in body["duplicates"] if d["phone"] == "+79991110001")
    assert isinstance(entry["existing_account_id"], int)
    assert entry["existing_account_id"] > 0


@pytest.mark.asyncio
async def test_check_duplicates_all_new(
    dedup_client: AsyncClient,
) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(dedup_client, 55002)

    resp = await dedup_client.post(
        "/v1/accounts/check-duplicates",
        headers={"Authorization": f"Bearer {token}"},
        json={"phones": ["+79990000001", "+79990000002"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["duplicates"] == []
    assert set(body["new"]) == {"+79990000001", "+79990000002"}


@pytest.mark.asyncio
async def test_check_duplicates_tenant_isolation(
    dedup_client: AsyncClient,
) -> None:
    """Tenant A's account must NOT appear as duplicate for Tenant B."""
    token_a, tenant_id_a, workspace_id_a = await _create_authorized_session(dedup_client, 55003)
    token_b, tenant_id_b, workspace_id_b = await _create_authorized_session(dedup_client, 55004)

    # Insert account under Tenant A
    await _insert_account(phone="+79998880001", tenant_id=tenant_id_a, workspace_id=workspace_id_a)

    # Tenant B checks the same phone — should appear as NEW, not duplicate
    resp = await dedup_client.post(
        "/v1/accounts/check-duplicates",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"phones": ["+79998880001"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["duplicates"] == []
    assert "+79998880001" in body["new"]


# ---------------------------------------------------------------------------
# CSV export tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_csv_format(
    dedup_client: AsyncClient,
) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(dedup_client, 55005)
    await _insert_account(phone="+79990011001", tenant_id=tenant_id, workspace_id=workspace_id, status="active")
    await _insert_account(phone="+79990011002", tenant_id=tenant_id, workspace_id=workspace_id, status="frozen")

    resp = await dedup_client.get(
        "/v1/accounts/export?format=csv",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    assert "attachment" in resp.headers.get("content-disposition", "")

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 2

    phones = {r["phone"] for r in rows}
    assert "+79990011001" in phones
    assert "+79990011002" in phones

    # Verify required columns are present
    expected_cols = {
        "id", "phone", "status", "lifecycle_stage", "health_status",
        "risk_level", "proxy_id", "account_age_days", "created_at",
        "last_active_at", "last_health_check", "manual_notes",
    }
    assert expected_cols <= set(reader.fieldnames or [])


@pytest.mark.asyncio
async def test_export_csv_status_filter(
    dedup_client: AsyncClient,
) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(dedup_client, 55006)
    await _insert_account(phone="+79990022001", tenant_id=tenant_id, workspace_id=workspace_id, status="active")
    await _insert_account(phone="+79990022002", tenant_id=tenant_id, workspace_id=workspace_id, status="banned")

    resp = await dedup_client.get(
        "/v1/accounts/export?format=csv&status=active",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    phones = {r["phone"] for r in rows}
    assert "+79990022001" in phones
    assert "+79990022002" not in phones


# ---------------------------------------------------------------------------
# JSON export tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_json_format(
    dedup_client: AsyncClient,
) -> None:
    token, tenant_id, workspace_id = await _create_authorized_session(dedup_client, 55007)
    await _insert_account(phone="+79990033001", tenant_id=tenant_id, workspace_id=workspace_id)

    resp = await dedup_client.get(
        "/v1/accounts/export?format=json",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "application/json" in resp.headers.get("content-type", "")
    assert "attachment" in resp.headers.get("content-disposition", "")

    data = json.loads(resp.text)
    assert isinstance(data, list)
    assert len(data) >= 1

    entry = next(d for d in data if d["phone"] == "+79990033001")
    expected_keys = {
        "id", "phone", "status", "lifecycle_stage", "health_status",
        "risk_level", "proxy_id", "account_age_days", "created_at",
        "last_active_at", "last_health_check", "manual_notes",
    }
    assert expected_keys <= set(entry.keys())


@pytest.mark.asyncio
async def test_export_tenant_isolation(
    dedup_client: AsyncClient,
) -> None:
    """Tenant A must not appear in Tenant B's export."""
    token_a, tenant_id_a, workspace_id_a = await _create_authorized_session(dedup_client, 55008)
    token_b, _tenant_id_b, _workspace_id_b = await _create_authorized_session(dedup_client, 55009)

    await _insert_account(phone="+79990044001", tenant_id=tenant_id_a, workspace_id=workspace_id_a)

    resp = await dedup_client.get(
        "/v1/accounts/export?format=json",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 200
    data = json.loads(resp.text)
    phones = [d["phone"] for d in data]
    assert "+79990044001" not in phones
