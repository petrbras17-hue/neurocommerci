"""
Tests for the account approval gate endpoints.

Covers:
- approve transitions lifecycle_stage to execution_ready
- reject transitions back to warming_up
- approve fails for non-gate_review accounts
- bulk approve
- tenant isolation
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Tuple

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import ops_api
from config import settings
from storage.models import (
    Account,
    AccountStageEvent,
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
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    payload["hash"] = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return payload


async def _create_authorized_session(
    client: AsyncClient,
    telegram_user_id: int,
) -> Tuple[str, int, int]:
    """Create a tenant session and return (access_token, tenant_id, workspace_id)."""
    verify = await client.post(
        "/auth/telegram/verify",
        json=_telegram_payload(
            bot_token=settings.ADMIN_BOT_TOKEN,
            telegram_user_id=telegram_user_id,
            username=f"approvaluser{telegram_user_id}",
        ),
    )
    assert verify.status_code == 200, verify.text
    setup_token = verify.json()["setup_token"]
    complete = await client.post(
        "/auth/complete-profile",
        json={
            "setup_token": setup_token,
            "email": f"approvaluser{telegram_user_id}@example.com",
            "company": f"ApprovalCo {telegram_user_id}",
        },
    )
    assert complete.status_code == 200, complete.text
    body = complete.json()
    return body["access_token"], int(body["tenant"]["id"]), int(body["workspace"]["id"])


async def _create_gate_review_account(
    *,
    tenant_id: int,
    workspace_id: int,
    phone: str,
) -> int:
    """Insert a gate_review account directly and return its id."""
    async with async_session() as session:
        async with session.begin():
            workspace_row = (
                await session.execute(
                    select(Workspace).where(Workspace.id == workspace_id, Workspace.tenant_id == tenant_id)
                )
            ).scalar_one()
            account = Account(
                phone=phone,
                session_file=f"{phone.lstrip('+')}.session",
                user_id=workspace_row.runtime_user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                status="active",
                health_status="alive",
                lifecycle_stage="gate_review",
            )
            session.add(account)
            await session.flush()
            return int(account.id)


async def _reset_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in [
                AccountStageEvent,
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def approval_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=ops_api.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops_api.settings, "ADMIN_BOT_TOKEN", "telegram-approval-test-token")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "approval-gate-access-secret-123456")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "approval-gate-refresh-secret-123456")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    await init_db()
    await _reset_state()
    yield
    await _reset_state()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_transitions_to_execution_ready(approval_client: AsyncClient) -> None:
    """Approving a gate_review account sets lifecycle_stage to execution_ready."""
    token, tenant_id, workspace_id = await _create_authorized_session(approval_client, 6001)
    account_id = await _create_gate_review_account(
        tenant_id=tenant_id, workspace_id=workspace_id, phone="+76001000001"
    )

    resp = await approval_client.post(
        f"/v1/accounts/{account_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["account_id"] == account_id
    assert body["new_stage"] == "execution_ready"

    # Verify DB state.
    async with async_session() as session:
        async with session.begin():
            account = (await session.execute(select(Account).where(Account.id == account_id))).scalar_one()
            assert account.lifecycle_stage == "execution_ready"

            events = (
                await session.execute(
                    select(AccountStageEvent)
                    .where(AccountStageEvent.account_id == account_id)
                    .order_by(AccountStageEvent.id.desc())
                )
            ).scalars().all()
            assert len(events) >= 1
            latest = events[0]
            assert latest.from_stage == "gate_review"
            assert latest.to_stage == "execution_ready"
            assert "operator" in latest.actor


@pytest.mark.asyncio
async def test_reject_transitions_back_to_warming_up(approval_client: AsyncClient) -> None:
    """Rejecting a gate_review account sets lifecycle_stage back to warming_up."""
    token, tenant_id, workspace_id = await _create_authorized_session(approval_client, 6002)
    account_id = await _create_gate_review_account(
        tenant_id=tenant_id, workspace_id=workspace_id, phone="+76002000001"
    )

    resp = await approval_client.post(
        f"/v1/accounts/{account_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "Недостаточно прогрева, вернуть на дополнительный цикл"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["new_stage"] == "warming_up"
    assert "Недостаточно прогрева" in body["reason"]

    async with async_session() as session:
        async with session.begin():
            account = (await session.execute(select(Account).where(Account.id == account_id))).scalar_one()
            assert account.lifecycle_stage == "warming_up"

            events = (
                await session.execute(
                    select(AccountStageEvent)
                    .where(AccountStageEvent.account_id == account_id)
                    .order_by(AccountStageEvent.id.desc())
                )
            ).scalars().all()
            assert len(events) >= 1
            latest = events[0]
            assert latest.from_stage == "gate_review"
            assert latest.to_stage == "warming_up"
            assert "rejected" in (latest.reason or "")


@pytest.mark.asyncio
async def test_approve_fails_for_non_gate_review_account(approval_client: AsyncClient) -> None:
    """Approving an account that is not in gate_review returns 409."""
    token, tenant_id, workspace_id = await _create_authorized_session(approval_client, 6003)

    # Insert account in warming_up stage, not gate_review.
    async with async_session() as session:
        async with session.begin():
            workspace_row = (
                await session.execute(select(Workspace).where(Workspace.id == workspace_id))
            ).scalar_one()
            account = Account(
                phone="+76003000001",
                session_file="76003000001.session",
                user_id=workspace_row.runtime_user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                status="active",
                health_status="alive",
                lifecycle_stage="warming_up",
            )
            session.add(account)
            await session.flush()
            account_id = int(account.id)

    resp = await approval_client.post(
        f"/v1/accounts/{account_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text
    assert "gate_review" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_reject_fails_for_non_gate_review_account(approval_client: AsyncClient) -> None:
    """Rejecting an account not in gate_review returns 409."""
    token, tenant_id, workspace_id = await _create_authorized_session(approval_client, 6004)

    async with async_session() as session:
        async with session.begin():
            workspace_row = (
                await session.execute(select(Workspace).where(Workspace.id == workspace_id))
            ).scalar_one()
            account = Account(
                phone="+76004000001",
                session_file="76004000001.session",
                user_id=workspace_row.runtime_user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                status="active",
                health_status="alive",
                lifecycle_stage="execution_ready",
            )
            session.add(account)
            await session.flush()
            account_id = int(account.id)

    resp = await approval_client.post(
        f"/v1/accounts/{account_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "some reason"},
    )
    assert resp.status_code == 409, resp.text
    assert "gate_review" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_bulk_approve_all_gate_review(approval_client: AsyncClient) -> None:
    """Bulk approve transitions all gate_review accounts to execution_ready."""
    token, tenant_id, workspace_id = await _create_authorized_session(approval_client, 6005)
    id1 = await _create_gate_review_account(
        tenant_id=tenant_id, workspace_id=workspace_id, phone="+76005000001"
    )
    id2 = await _create_gate_review_account(
        tenant_id=tenant_id, workspace_id=workspace_id, phone="+76005000002"
    )

    resp = await approval_client.post(
        "/v1/accounts/bulk-approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"account_ids": [id1, id2]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["approved_count"] == 2
    assert body["errors"] == []

    async with async_session() as session:
        async with session.begin():
            for aid in [id1, id2]:
                acct = (await session.execute(select(Account).where(Account.id == aid))).scalar_one()
                assert acct.lifecycle_stage == "execution_ready"


@pytest.mark.asyncio
async def test_bulk_approve_partial_errors(approval_client: AsyncClient) -> None:
    """Bulk approve returns errors for non-gate_review accounts and approves the rest."""
    token, tenant_id, workspace_id = await _create_authorized_session(approval_client, 6006)
    good_id = await _create_gate_review_account(
        tenant_id=tenant_id, workspace_id=workspace_id, phone="+76006000001"
    )

    # Account in wrong stage.
    async with async_session() as session:
        async with session.begin():
            workspace_row = (
                await session.execute(select(Workspace).where(Workspace.id == workspace_id))
            ).scalar_one()
            bad_account = Account(
                phone="+76006000002",
                session_file="76006000002.session",
                user_id=workspace_row.runtime_user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                status="active",
                health_status="alive",
                lifecycle_stage="uploaded",
            )
            session.add(bad_account)
            await session.flush()
            bad_id = int(bad_account.id)

    resp = await approval_client.post(
        "/v1/accounts/bulk-approve",
        headers={"Authorization": f"Bearer {token}"},
        json={"account_ids": [good_id, bad_id, 999999]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["approved_count"] == 1
    assert len(body["errors"]) == 2

    error_ids = {e["account_id"] for e in body["errors"]}
    assert bad_id in error_ids
    assert 999999 in error_ids


@pytest.mark.asyncio
async def test_pending_review_list_returns_only_gate_review(approval_client: AsyncClient) -> None:
    """GET /v1/accounts/pending-review returns only gate_review accounts."""
    token, tenant_id, workspace_id = await _create_authorized_session(approval_client, 6007)
    gate_id = await _create_gate_review_account(
        tenant_id=tenant_id, workspace_id=workspace_id, phone="+76007000001"
    )

    # Add a non-gate_review account for the same tenant.
    async with async_session() as session:
        async with session.begin():
            workspace_row = (
                await session.execute(select(Workspace).where(Workspace.id == workspace_id))
            ).scalar_one()
            other = Account(
                phone="+76007000002",
                session_file="76007000002.session",
                user_id=workspace_row.runtime_user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                status="active",
                health_status="alive",
                lifecycle_stage="warming_up",
            )
            session.add(other)

    resp = await approval_client.get(
        "/v1/accounts/pending-review",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == gate_id
    assert body["items"][0]["lifecycle_stage"] == "gate_review"


@pytest.mark.asyncio
async def test_tenant_isolation_approve(approval_client: AsyncClient) -> None:
    """Tenant A cannot approve an account belonging to Tenant B."""
    token_a, tenant_a, workspace_a = await _create_authorized_session(approval_client, 6008)
    _token_b, tenant_b, workspace_b = await _create_authorized_session(approval_client, 6009)

    # Create a gate_review account under tenant B.
    account_id_b = await _create_gate_review_account(
        tenant_id=tenant_b, workspace_id=workspace_b, phone="+76009000001"
    )

    # Tenant A tries to approve tenant B's account.
    resp = await approval_client.post(
        f"/v1/accounts/{account_id_b}/approve",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    # Should be 404 because RLS/workspace scoping hides the account.
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "account_not_found"


@pytest.mark.asyncio
async def test_tenant_isolation_pending_review(approval_client: AsyncClient) -> None:
    """Tenant A's pending-review list does not include Tenant B's accounts."""
    token_a, tenant_a, workspace_a = await _create_authorized_session(approval_client, 6010)
    _token_b, tenant_b, workspace_b = await _create_authorized_session(approval_client, 6011)

    # Each tenant gets one gate_review account.
    await _create_gate_review_account(
        tenant_id=tenant_a, workspace_id=workspace_a, phone="+76010000001"
    )
    await _create_gate_review_account(
        tenant_id=tenant_b, workspace_id=workspace_b, phone="+76011000001"
    )

    resp_a = await approval_client.get(
        "/v1/accounts/pending-review",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_a.status_code == 200
    phones_a = [item["phone"] for item in resp_a.json()["items"]]
    assert "+76010000001" in phones_a
    assert "+76011000001" not in phones_a
