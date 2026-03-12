"""
Account Lifecycle State Machine — test suite.

Covers:
  1. Static can_transition helper
  2. Valid transitions (happy path)
  3. Invalid transitions (LifecycleTransitionError)
  4. auto_advance logic (proxy bound, days_active, cooldown expiry, health=dead)
  5. Event logging (AccountStageEvent rows written)
  6. on_* event handlers
  7. get_stage_history
  8. API endpoint POST /v1/accounts/{id}/lifecycle
  9. API endpoint GET /v1/accounts/{id}/lifecycle/history
 10. Tenant isolation (operator cannot mutate another tenant's account)
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import ops_api
from config import settings
from core.account_lifecycle import (
    AccountLifecycle,
    LifecycleTransitionError,
    TRANSITIONS,
    LifecycleStage,
)
from ops_api import app
from storage.models import (
    Account,
    AccountStageEvent,
    AuthUser,
    RefreshToken,
    TeamMember,
    Tenant,
    Workspace,
)
from storage.sqlite_db import async_session, init_db
from utils.helpers import utcnow


# ---------------------------------------------------------------------------
# Telegram login widget helper
# ---------------------------------------------------------------------------


def _telegram_payload(
    *, bot_token: str, telegram_user_id: int, username: str = "lctestuser"
) -> dict[str, Any]:
    auth_date = int(utcnow().timestamp())
    payload: dict[str, Any] = {
        "id": telegram_user_id,
        "auth_date": auth_date,
        "username": username,
        "first_name": "LC",
        "last_name": "Test",
    }
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    payload["hash"] = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return payload


# ---------------------------------------------------------------------------
# State cleanup
# ---------------------------------------------------------------------------

_MODELS_TO_DELETE = [
    AccountStageEvent,
    Account,
    RefreshToken,
    TeamMember,
    Workspace,
    Tenant,
    AuthUser,
]


async def _reset_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in _MODELS_TO_DELETE:
                await session.execute(delete(model))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def lc_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[misc]
    monkeypatch.setattr(ops_api.settings, "ADMIN_BOT_TOKEN", "lc-test-token-123")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "lc-access-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "lc-refresh-secret-1234567890")
    monkeypatch.setattr(ops_api.settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_TTL_MINUTES", 15)
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_TTL_DAYS", 7)
    monkeypatch.setattr(ops_api.settings, "CHANNELS_SPREADSHEET_ID", "")
    monkeypatch.setattr(ops_api.settings, "STATS_SPREADSHEET_ID", "")
    monkeypatch.setattr(ops_api.settings, "GOOGLE_SHEETS_CREDENTIALS_FILE", "")
    await init_db()
    await _reset_state()
    yield
    await _reset_state()


async def _create_session(
    client: AsyncClient, telegram_user_id: int, suffix: str = ""
) -> tuple[str, int, int]:
    """Register via Telegram auth; return (access_token, tenant_id, workspace_id)."""
    username = f"lctest{telegram_user_id}{suffix}"
    verify = await client.post(
        "/auth/telegram/verify",
        json=_telegram_payload(
            bot_token=settings.ADMIN_BOT_TOKEN,
            telegram_user_id=telegram_user_id,
            username=username,
        ),
    )
    assert verify.status_code == 200, f"verify: {verify.text}"
    setup_token = verify.json()["setup_token"]

    complete = await client.post(
        "/auth/complete-profile",
        json={
            "setup_token": setup_token,
            "email": f"lctest{telegram_user_id}{suffix}@example.com",
            "company": f"LCCo {telegram_user_id}",
        },
    )
    assert complete.status_code == 200, f"complete-profile: {complete.text}"
    body = complete.json()
    return body["access_token"], int(body["tenant"]["id"]), int(body["workspace"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_account(
    tenant_id: int,
    workspace_id: int,
    phone: str,
    *,
    lifecycle_stage: str = "uploaded",
    days_active: int = 0,
    proxy_id: int | None = None,
    status: str = "active",
    health_status: str = "unknown",
    quarantined_until=None,
) -> int:
    """Insert an Account row directly and return its id."""
    async with async_session() as session:
        async with session.begin():
            account = Account(
                phone=phone,
                session_file=f"dummy_{phone}.session",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                status=status,
                health_status=health_status,
                lifecycle_stage=lifecycle_stage,
                days_active=days_active,
                proxy_id=proxy_id,
                quarantined_until=quarantined_until,
            )
            session.add(account)
            await session.flush()
            return int(account.id)


# ===========================================================================
# 1. Static can_transition helper
# ===========================================================================


def test_can_transition_valid() -> None:
    assert AccountLifecycle.can_transition("uploaded", "warming_up") is True
    assert AccountLifecycle.can_transition("warming_up", "gate_review") is True
    assert AccountLifecycle.can_transition("gate_review", "execution_ready") is True
    assert AccountLifecycle.can_transition("execution_ready", "active_commenting") is True
    assert AccountLifecycle.can_transition("active_commenting", "cooldown") is True
    assert AccountLifecycle.can_transition("cooldown", "active_commenting") is True
    assert AccountLifecycle.can_transition("frozen", "warming_up") is True
    assert AccountLifecycle.can_transition("frozen", "banned") is True
    assert AccountLifecycle.can_transition("banned", "dead") is True


def test_can_transition_invalid() -> None:
    # Cannot go backward
    assert AccountLifecycle.can_transition("active_commenting", "uploaded") is False
    # Cannot skip stages
    assert AccountLifecycle.can_transition("uploaded", "execution_ready") is False
    # Terminal
    assert AccountLifecycle.can_transition("dead", "warming_up") is False
    assert AccountLifecycle.can_transition("dead", "dead") is False
    # Unknown stage
    assert AccountLifecycle.can_transition("nonexistent", "warming_up") is False


def test_transitions_map_coverage() -> None:
    """Every LifecycleStage value must appear as a key in TRANSITIONS."""
    for stage in LifecycleStage:
        assert stage.value in TRANSITIONS, f"{stage.value} missing from TRANSITIONS"


# ===========================================================================
# 2. Valid transition — happy path (unit, no HTTP)
# ===========================================================================


@pytest.mark.asyncio
async def test_transition_valid_unit(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10001)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010001", lifecycle_stage="uploaded"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.transition(account_id, "warming_up", reason="test", actor="pytest")

    assert result["ok"] is True
    assert result["old_stage"] == "uploaded"
    assert result["new_stage"] == "warming_up"

    async with async_session() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert account.lifecycle_stage == "warming_up"


# ===========================================================================
# 3. Invalid transition — LifecycleTransitionError
# ===========================================================================


@pytest.mark.asyncio
async def test_transition_invalid_raises(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10002)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010002", lifecycle_stage="uploaded"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            with pytest.raises(LifecycleTransitionError):
                await lc.transition(account_id, "execution_ready")  # skip steps


@pytest.mark.asyncio
async def test_transition_from_dead_raises(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10003)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010003", lifecycle_stage="dead"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            with pytest.raises(LifecycleTransitionError):
                await lc.transition(account_id, "warming_up")


@pytest.mark.asyncio
async def test_transition_unknown_account_raises(lc_client: AsyncClient) -> None:
    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            with pytest.raises(ValueError, match="not found"):
                await lc.transition(999999, "warming_up")


# ===========================================================================
# 4. auto_advance logic
# ===========================================================================


@pytest.mark.asyncio
async def test_auto_advance_proxy_bound(lc_client: AsyncClient) -> None:
    """uploaded + proxy_id set -> warming_up."""
    _, tenant_id, workspace_id = await _create_session(lc_client, 10010)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010010",
        lifecycle_stage="uploaded",
        proxy_id=1,  # any non-None value
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.auto_advance(account_id)

    assert result is not None
    assert result["new_stage"] == "warming_up"


@pytest.mark.asyncio
async def test_auto_advance_no_proxy_no_advance(lc_client: AsyncClient) -> None:
    """uploaded without proxy_id -> no advance."""
    _, tenant_id, workspace_id = await _create_session(lc_client, 10011)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010011",
        lifecycle_stage="uploaded",
        proxy_id=None,
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.auto_advance(account_id)

    assert result is None


@pytest.mark.asyncio
async def test_auto_advance_warmup_days_threshold(lc_client: AsyncClient) -> None:
    """warming_up + days_active >= 15 -> gate_review."""
    _, tenant_id, workspace_id = await _create_session(lc_client, 10012)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010012",
        lifecycle_stage="warming_up",
        days_active=15,
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.auto_advance(account_id)

    assert result is not None
    assert result["new_stage"] == "gate_review"


@pytest.mark.asyncio
async def test_auto_advance_cooldown_expired(lc_client: AsyncClient) -> None:
    """cooldown + quarantined_until in the past -> active_commenting."""
    _, tenant_id, workspace_id = await _create_session(lc_client, 10013)
    past = utcnow() - timedelta(seconds=10)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010013",
        lifecycle_stage="cooldown",
        quarantined_until=past,
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.auto_advance(account_id)

    assert result is not None
    assert result["new_stage"] == "active_commenting"


@pytest.mark.asyncio
async def test_auto_advance_cooldown_not_expired(lc_client: AsyncClient) -> None:
    """cooldown + quarantined_until in the future -> no advance."""
    _, tenant_id, workspace_id = await _create_session(lc_client, 10014)
    future = utcnow() + timedelta(hours=2)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010014",
        lifecycle_stage="cooldown",
        quarantined_until=future,
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.auto_advance(account_id)

    assert result is None


@pytest.mark.asyncio
async def test_auto_advance_health_dead(lc_client: AsyncClient) -> None:
    """active_commenting + health_status='dead' -> dead."""
    _, tenant_id, workspace_id = await _create_session(lc_client, 10015)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010015",
        lifecycle_stage="active_commenting",
        health_status="dead",
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.auto_advance(account_id)

    assert result is not None
    assert result["new_stage"] == "dead"


@pytest.mark.asyncio
async def test_auto_advance_dead_is_terminal(lc_client: AsyncClient) -> None:
    """dead stage returns None — no further advance."""
    _, tenant_id, workspace_id = await _create_session(lc_client, 10016)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010016",
        lifecycle_stage="dead",
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.auto_advance(account_id)

    assert result is None


# ===========================================================================
# 5. Event logging
# ===========================================================================


@pytest.mark.asyncio
async def test_event_is_logged_on_transition(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10020)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010020", lifecycle_stage="uploaded"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            await lc.transition(account_id, "warming_up", reason="proxy added", actor="test_actor")

    async with async_session() as session:
        result = await session.execute(
            select(AccountStageEvent)
            .where(AccountStageEvent.account_id == account_id)
            .order_by(AccountStageEvent.id.desc())
        )
        events = list(result.scalars().all())

    assert len(events) >= 1
    latest = events[0]
    assert latest.from_stage == "uploaded"
    assert latest.to_stage == "warming_up"
    assert latest.actor == "test_actor"
    assert "proxy added" in (latest.reason or "")


@pytest.mark.asyncio
async def test_multiple_events_logged(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10021)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010021", lifecycle_stage="uploaded"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            await lc.transition(account_id, "warming_up", reason="step1")
            await lc.transition(account_id, "gate_review", reason="step2")
            await lc.transition(account_id, "execution_ready", reason="step3")

    async with async_session() as session:
        result = await session.execute(
            select(AccountStageEvent)
            .where(AccountStageEvent.account_id == account_id)
            .order_by(AccountStageEvent.id.asc())
        )
        events = list(result.scalars().all())

    assert len(events) == 3
    assert events[0].to_stage == "warming_up"
    assert events[1].to_stage == "gate_review"
    assert events[2].to_stage == "execution_ready"


# ===========================================================================
# 6. on_* event handlers
# ===========================================================================


@pytest.mark.asyncio
async def test_on_warmup_complete(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10030)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010030", lifecycle_stage="warming_up"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.on_warmup_complete(account_id)

    assert result["new_stage"] == "gate_review"


@pytest.mark.asyncio
async def test_on_flood_wait(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10031)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010031", lifecycle_stage="active_commenting"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.on_flood_wait(account_id, seconds=300)

    assert result["new_stage"] == "cooldown"
    assert "300" in (result.get("reason") or "")


@pytest.mark.asyncio
async def test_on_frozen(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10032)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010032", lifecycle_stage="active_commenting"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.on_frozen(account_id)

    assert result["new_stage"] == "frozen"


@pytest.mark.asyncio
async def test_on_ban(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10033)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010033", lifecycle_stage="active_commenting"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.on_ban(account_id)

    assert result["new_stage"] == "banned"


@pytest.mark.asyncio
async def test_on_session_dead(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10034)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010034", lifecycle_stage="active_commenting"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.on_session_dead(account_id)

    assert result["new_stage"] == "dead"


@pytest.mark.asyncio
async def test_on_appeal_success(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10035)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010035", lifecycle_stage="frozen"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            result = await lc.on_appeal_success(account_id)

    assert result["new_stage"] == "warming_up"
    assert result["reason"] == "appeal_success"


# ===========================================================================
# 7. get_stage_history
# ===========================================================================


@pytest.mark.asyncio
async def test_get_stage_history(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10040)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010040", lifecycle_stage="uploaded"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            await lc.transition(account_id, "warming_up")

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            history = await lc.get_stage_history(account_id, limit=10)

    assert len(history) == 1
    entry = history[0]
    assert entry["from_stage"] == "uploaded"
    assert entry["to_stage"] == "warming_up"
    assert "created_at" in entry


@pytest.mark.asyncio
async def test_get_stage_history_empty(lc_client: AsyncClient) -> None:
    _, tenant_id, workspace_id = await _create_session(lc_client, 10041)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010041", lifecycle_stage="uploaded"
    )

    async with async_session() as session:
        async with session.begin():
            lc = AccountLifecycle(session)
            history = await lc.get_stage_history(account_id, limit=10)

    assert history == []


# ===========================================================================
# 8. API endpoint POST /v1/accounts/{id}/lifecycle
# ===========================================================================


@pytest.mark.asyncio
async def test_api_lifecycle_transition_valid(lc_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_session(lc_client, 10050)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010050", lifecycle_stage="uploaded"
    )

    resp = await lc_client.post(
        f"/v1/accounts/{account_id}/lifecycle",
        headers=_auth(token),
        json={"target_stage": "warming_up", "reason": "proxy ready"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["old_stage"] == "uploaded"
    assert body["new_stage"] == "warming_up"
    assert body["reason"] == "proxy ready"


@pytest.mark.asyncio
async def test_api_lifecycle_transition_invalid(lc_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_session(lc_client, 10051)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010051", lifecycle_stage="uploaded"
    )

    resp = await lc_client.post(
        f"/v1/accounts/{account_id}/lifecycle",
        headers=_auth(token),
        json={"target_stage": "execution_ready"},  # skips required steps
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_api_lifecycle_account_not_found(lc_client: AsyncClient) -> None:
    token, _, _ = await _create_session(lc_client, 10052)

    resp = await lc_client.post(
        "/v1/accounts/999999/lifecycle",
        headers=_auth(token),
        json={"target_stage": "warming_up"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_api_lifecycle_unauthenticated(lc_client: AsyncClient) -> None:
    resp = await lc_client.post(
        "/v1/accounts/1/lifecycle",
        json={"target_stage": "warming_up"},
    )
    assert resp.status_code in (401, 403)


# ===========================================================================
# 9. API endpoint GET /v1/accounts/{id}/lifecycle/history
# ===========================================================================


@pytest.mark.asyncio
async def test_api_lifecycle_history(lc_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_session(lc_client, 10060)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010060", lifecycle_stage="uploaded"
    )

    # Do a transition via the API.
    trans_resp = await lc_client.post(
        f"/v1/accounts/{account_id}/lifecycle",
        headers=_auth(token),
        json={"target_stage": "warming_up", "reason": "from history test"},
    )
    assert trans_resp.status_code == 200

    resp = await lc_client.get(
        f"/v1/accounts/{account_id}/lifecycle/history",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["account_id"] == account_id
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["to_stage"] == "warming_up"


@pytest.mark.asyncio
async def test_api_lifecycle_history_empty(lc_client: AsyncClient) -> None:
    token, tenant_id, workspace_id = await _create_session(lc_client, 10061)
    account_id = await _create_account(
        tenant_id, workspace_id, "+79000010061", lifecycle_stage="uploaded"
    )

    resp = await lc_client.get(
        f"/v1/accounts/{account_id}/lifecycle/history",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


# ===========================================================================
# 10. Tenant isolation
# ===========================================================================


@pytest.mark.asyncio
async def test_tenant_isolation_cannot_transition_other_tenant(lc_client: AsyncClient) -> None:
    """Tenant B cannot transition an account belonging to Tenant A."""
    token_a, tenant_id_a, workspace_id_a = await _create_session(lc_client, 10070)
    token_b, _, _ = await _create_session(lc_client, 10071)

    account_id_a = await _create_account(
        tenant_id_a, workspace_id_a, "+79000010070", lifecycle_stage="uploaded"
    )

    resp = await lc_client.post(
        f"/v1/accounts/{account_id_a}/lifecycle",
        headers=_auth(token_b),
        json={"target_stage": "warming_up"},
    )
    # Must be 404 (account not visible to tenant B) rather than 200.
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_tenant_isolation_cannot_read_other_tenant_history(lc_client: AsyncClient) -> None:
    """Tenant B cannot read history for an account belonging to Tenant A."""
    token_a, tenant_id_a, workspace_id_a = await _create_session(lc_client, 10072)
    token_b, _, _ = await _create_session(lc_client, 10073)

    account_id_a = await _create_account(
        tenant_id_a, workspace_id_a, "+79000010072", lifecycle_stage="uploaded"
    )

    resp = await lc_client.get(
        f"/v1/accounts/{account_id_a}/lifecycle/history",
        headers=_auth(token_b),
    )
    assert resp.status_code == 404, resp.text
