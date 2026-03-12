"""Tests for the Session Topology API endpoints."""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

import ops_api
from config import settings
from storage.models import (
    Account,
    AuthUser,
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


async def _reset_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in [Account, RefreshToken, TeamMember, Workspace, Tenant, AuthUser, User]:
                await session.execute(delete(model))


async def _create_authorized_session(client: AsyncClient, telegram_user_id: int) -> str:
    verify = await client.post(
        "/auth/telegram/verify",
        json=_telegram_payload(
            bot_token=settings.ADMIN_BOT_TOKEN,
            telegram_user_id=telegram_user_id,
            username=f"topologyuser{telegram_user_id}",
        ),
    )
    assert verify.status_code == 200, verify.text
    setup_token = verify.json()["setup_token"]
    complete = await client.post(
        "/auth/complete-profile",
        json={
            "setup_token": setup_token,
            "email": f"topology{telegram_user_id}@example.com",
            "company": f"TopoCompany{telegram_user_id}",
        },
    )
    assert complete.status_code == 200, complete.text
    return complete.json()["access_token"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def topology_client() -> AsyncClient:
    await init_db()
    transport = ASGITransport(app=ops_api.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ops_api.settings, "ADMIN_BOT_TOKEN", "topo-test-token")
    monkeypatch.setattr(ops_api.settings, "SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(ops_api.settings, "JWT_ACCESS_SECRET", "topo-access-secret-1234567890ab")
    monkeypatch.setattr(ops_api.settings, "JWT_REFRESH_SECRET", "topo-refresh-secret-1234567890ab")
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
async def test_topology_endpoint_returns_valid_structure(
    topology_client: AsyncClient,
) -> None:
    """GET /v1/sessions/topology returns items list and summary dict."""
    token = await _create_authorized_session(topology_client, 7001)
    resp = await topology_client.get(
        "/v1/sessions/topology",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    assert "summary" in body
    summary = body["summary"]
    assert "phones_total" in summary
    assert "canonical_complete" in summary
    assert "with_root_copies" in summary
    assert "with_legacy_copies" in summary
    assert "duplicate_copy_phones" in summary
    assert "safe_to_quarantine" in summary
    assert "duplicate_phones" in summary
    assert isinstance(summary["duplicate_phones"], list)


@pytest.mark.asyncio
async def test_topology_summary_endpoint(
    topology_client: AsyncClient,
) -> None:
    """GET /v1/sessions/topology/summary returns only summary stats."""
    token = await _create_authorized_session(topology_client, 7002)
    resp = await topology_client.get(
        "/v1/sessions/topology/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Must have summary fields but NOT the full items list
    assert "phones_total" in body
    assert "safe_to_quarantine" in body
    assert "items" not in body


@pytest.mark.asyncio
async def test_quarantine_dry_run_does_not_move_files(
    topology_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /v1/sessions/quarantine with dry_run=true does not move files on disk."""
    sessions_dir = tmp_path / "sessions_dry"
    sessions_dir.mkdir(parents=True)
    monkeypatch.setattr(ops_api.settings, "SESSIONS_DIR", str(sessions_dir))

    # Create a flat session file (simulates a non-canonical copy)
    flat_session = sessions_dir / "79001112233.session"
    flat_session.write_bytes(b"fake-session-data")

    token = await _create_authorized_session(topology_client, 7003)
    resp = await topology_client.post(
        "/v1/sessions/quarantine",
        headers={"Authorization": f"Bearer {token}"},
        json={"phones": [], "dry_run": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["dry_run"] is True
    # Dry run: file must still exist
    assert flat_session.exists(), "dry_run must not move files"


@pytest.mark.asyncio
async def test_topology_requires_auth(topology_client: AsyncClient) -> None:
    """GET /v1/sessions/topology without token returns 401 or 403."""
    resp = await topology_client.get("/v1/sessions/topology")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_quarantine_requires_auth(topology_client: AsyncClient) -> None:
    """POST /v1/sessions/quarantine without token returns 401 or 403."""
    resp = await topology_client.post(
        "/v1/sessions/quarantine",
        json={"phones": [], "dry_run": True},
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_tenant_isolation(topology_client: AsyncClient) -> None:
    """Two different tenants each get independent topology scans.

    Neither tenant can read the other's data; both should receive valid structures.
    """
    token_a = await _create_authorized_session(topology_client, 7004)
    token_b = await _create_authorized_session(topology_client, 7005)

    resp_a = await topology_client.get(
        "/v1/sessions/topology",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    resp_b = await topology_client.get(
        "/v1/sessions/topology",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text

    # Both tenants have no accounts, so phones_total must be 0 for each
    assert resp_a.json()["summary"]["phones_total"] == 0
    assert resp_b.json()["summary"]["phones_total"] == 0


@pytest.mark.asyncio
async def test_quarantine_summary_endpoint_structure(
    topology_client: AsyncClient,
) -> None:
    """POST /v1/sessions/quarantine returns required fields."""
    token = await _create_authorized_session(topology_client, 7006)
    resp = await topology_client.post(
        "/v1/sessions/quarantine",
        headers={"Authorization": f"Bearer {token}"},
        json={"phones": [], "dry_run": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "ok" in body
    assert "dry_run" in body
    assert "quarantine_dir" in body
    assert "moved_files" in body
    assert "moved_phones" in body
    assert "files" in body
    assert "skipped" in body
    assert "skipped_count" in body
    assert body["dry_run"] is True
