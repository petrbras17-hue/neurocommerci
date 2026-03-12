"""
Unit tests for core/session_pool.SessionPool.

All tests use mocks — no real Telegram connections required.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from core.session_pool import (
    PoolCapacityError,
    SessionDeadError,
    SessionPool,
    _ClientStatus,
    _PoolEntry,
)


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _make_mock_client(
    *,
    connected: bool = True,
    authorized: bool = True,
) -> MagicMock:
    """Return a mock TelegramClient with controllable state."""
    client = MagicMock()
    client.is_connected = MagicMock(return_value=connected)
    client.is_user_authorized = AsyncMock(return_value=authorized)
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    return client


def _make_mock_account(
    account_id: int = 1,
    phone: str = "79001112233",
    session_file: str = "79001112233.session",
    user_id: int = 1,
    proxy_id: int | None = None,
    api_id: int = 12345,
) -> MagicMock:
    acct = MagicMock()
    acct.id = account_id
    acct.phone = phone
    acct.session_file = session_file
    acct.user_id = user_id
    acct.proxy_id = proxy_id
    acct.api_id = api_id
    return acct


def _make_pool(
    tmp_path: Path,
    max_concurrent: int = 5,
    idle_timeout_sec: float = 600.0,
) -> SessionPool:
    return SessionPool(
        sessions_dir=tmp_path,
        max_concurrent=max_concurrent,
        idle_timeout_sec=idle_timeout_sec,
    )


def _seed_pool(pool: SessionPool, account_id: int, phone: str, client) -> _PoolEntry:
    """Directly insert a pre-built entry into the pool (bypasses DB / Telethon)."""
    entry = _PoolEntry(
        account_id=account_id,
        phone=phone,
        client=client,
        status=_ClientStatus.IDLE,
    )
    pool._pool[account_id] = entry
    return entry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pool(tmp_path: Path) -> SessionPool:
    return _make_pool(tmp_path)


@pytest.fixture
def mock_db_session() -> AsyncMock:
    return AsyncMock()


# ---------------------------------------------------------------------------
# Test: pool creation
# ---------------------------------------------------------------------------


class TestPoolCreation:
    def test_empty_on_init(self, pool: SessionPool) -> None:
        assert pool.active_count == 0

    def test_stats_on_empty(self, pool: SessionPool) -> None:
        stats = pool.pool_stats
        assert stats["total"] == 0
        assert stats["idle"] == 0
        assert stats["in_use"] == 0
        assert stats["max_concurrent"] == 5

    def test_capacity_used_pct_zero(self, pool: SessionPool) -> None:
        assert pool.pool_stats["capacity_used_pct"] == 0.0

    def test_custom_idle_timeout(self, tmp_path: Path) -> None:
        p = SessionPool(sessions_dir=tmp_path, idle_timeout_sec=30.0)
        assert p._idle_timeout_sec == 30.0


# ---------------------------------------------------------------------------
# Test: get_client returns same instance for same account_id
# ---------------------------------------------------------------------------


class TestGetClientCaching:
    @pytest.mark.asyncio
    async def test_pool_hit_returns_same_client(self, pool: SessionPool) -> None:
        client = _make_mock_client(connected=True, authorized=True)
        _seed_pool(pool, account_id=1, phone="79001112233", client=client)

        returned = await pool.get_client(account_id=1)
        assert returned is client

    @pytest.mark.asyncio
    async def test_pool_hit_does_not_call_connect(self, pool: SessionPool) -> None:
        client = _make_mock_client(connected=True, authorized=True)
        _seed_pool(pool, account_id=1, phone="79001112233", client=client)

        await pool.get_client(account_id=1)
        client.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_pool_hit_marks_in_use(self, pool: SessionPool) -> None:
        client = _make_mock_client(connected=True, authorized=True)
        entry = _seed_pool(pool, account_id=1, phone="79001112233", client=client)

        await pool.get_client(account_id=1)
        assert entry.status == _ClientStatus.IN_USE

    @pytest.mark.asyncio
    async def test_two_gets_same_account_same_object(self, pool: SessionPool) -> None:
        client = _make_mock_client(connected=True, authorized=True)
        _seed_pool(pool, account_id=7, phone="79007777777", client=client)

        c1 = await pool.get_client(account_id=7)
        # Release between calls so status goes back to IDLE.
        await pool.release_client(account_id=7)
        c2 = await pool.get_client(account_id=7)
        assert c1 is c2

    @pytest.mark.asyncio
    async def test_pool_miss_requires_db_session(self, pool: SessionPool) -> None:
        with pytest.raises(RuntimeError, match="db_session is required"):
            await pool.get_client(account_id=99)

    @pytest.mark.asyncio
    async def test_pool_miss_builds_client(self, pool: SessionPool, tmp_path: Path) -> None:
        """Pool miss path creates a new client via _build_and_connect."""
        session_file = tmp_path / "79002223344.session"
        session_file.write_bytes(b"")

        account = _make_mock_account(
            account_id=2,
            phone="79002223344",
            session_file=session_file.name,
            user_id=1,
            proxy_id=None,
            api_id=99999,
        )
        mock_client = _make_mock_client(connected=True, authorized=True)

        db = AsyncMock()
        # First execute → Account lookup
        _mock_result_account(db, account)
        # Second execute → phone lookup (called via _resolve_phone before _build_and_connect)
        # Actually _resolve_phone is called separately; let's patch _build_and_connect instead.

        with patch.object(
            pool,
            "_build_and_connect",
            new=AsyncMock(return_value=mock_client),
        ), patch.object(
            pool,
            "_resolve_phone",
            new=AsyncMock(return_value="79002223344"),
        ):
            result = await pool.get_client(account_id=2, db_session=db)

        assert result is mock_client
        assert pool.active_count == 1


# ---------------------------------------------------------------------------
# Test: disconnect removes from pool
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_removes_entry(self, pool: SessionPool) -> None:
        client = _make_mock_client()
        _seed_pool(pool, account_id=3, phone="79003334455", client=client)
        assert pool.active_count == 1

        await pool.disconnect_client(account_id=3)
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_disconnect_calls_client_disconnect(self, pool: SessionPool) -> None:
        client = _make_mock_client(connected=True)
        _seed_pool(pool, account_id=4, phone="79004445566", client=client)

        await pool.disconnect_client(account_id=4)
        client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_is_noop(self, pool: SessionPool) -> None:
        # Should not raise.
        await pool.disconnect_client(account_id=9999)

    @pytest.mark.asyncio
    async def test_disconnect_all_clears_pool(self, pool: SessionPool) -> None:
        for i in range(3):
            client = _make_mock_client()
            _seed_pool(pool, account_id=i + 10, phone=f"7900000000{i}", client=client)
        assert pool.active_count == 3

        await pool.disconnect_all()
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_disconnect_all_calls_each_client_disconnect(
        self, pool: SessionPool
    ) -> None:
        clients = []
        for i in range(3):
            c = _make_mock_client(connected=True)
            clients.append(c)
            _seed_pool(pool, account_id=i + 20, phone=f"7900000010{i}", client=c)

        await pool.disconnect_all()
        for c in clients:
            c.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# Test: max_concurrent limit
# ---------------------------------------------------------------------------


class TestMaxConcurrent:
    @pytest.mark.asyncio
    async def test_pool_full_raises_capacity_error(self, tmp_path: Path) -> None:
        pool = _make_pool(tmp_path, max_concurrent=2)
        for i in range(2):
            _seed_pool(pool, account_id=i + 100, phone=f"7910000000{i}", client=MagicMock())

        with pytest.raises(PoolCapacityError):
            await pool.get_client(account_id=999, db_session=AsyncMock())

    @pytest.mark.asyncio
    async def test_capacity_error_not_raised_for_already_pooled(
        self, tmp_path: Path
    ) -> None:
        pool = _make_pool(tmp_path, max_concurrent=1)
        client = _make_mock_client(connected=True, authorized=True)
        _seed_pool(pool, account_id=200, phone="79200000000", client=client)

        # account_id=200 is already in pool — should succeed (pool hit path).
        result = await pool.get_client(account_id=200)
        assert result is client

    @pytest.mark.asyncio
    async def test_after_disconnect_slot_is_freed(self, tmp_path: Path) -> None:
        pool = _make_pool(tmp_path, max_concurrent=1)
        client = _make_mock_client()
        _seed_pool(pool, account_id=300, phone="79300000000", client=client)

        await pool.disconnect_client(account_id=300)

        # Now pool has room — pool miss should proceed (but we mock out the build).
        with patch.object(pool, "_build_and_connect", new=AsyncMock(return_value=MagicMock())):
            with patch.object(pool, "_resolve_phone", new=AsyncMock(return_value="79300000001")):
                await pool.get_client(account_id=301, db_session=AsyncMock())
        assert pool.active_count == 1


# ---------------------------------------------------------------------------
# Test: pool_stats accuracy
# ---------------------------------------------------------------------------


class TestPoolStats:
    @pytest.mark.asyncio
    async def test_stats_reflect_status(self, pool: SessionPool) -> None:
        c1 = _make_mock_client(connected=True, authorized=True)
        c2 = _make_mock_client(connected=True, authorized=True)
        e1 = _seed_pool(pool, account_id=400, phone="79400000000", client=c1)
        e2 = _seed_pool(pool, account_id=401, phone="79400000001", client=c2)

        # Both idle initially.
        stats = pool.pool_stats
        assert stats["total"] == 2
        assert stats["idle"] == 2
        assert stats["in_use"] == 0

        # Mark one in-use.
        e1.status = _ClientStatus.IN_USE
        stats = pool.pool_stats
        assert stats["idle"] == 1
        assert stats["in_use"] == 1

    def test_capacity_used_pct(self, tmp_path: Path) -> None:
        pool = _make_pool(tmp_path, max_concurrent=4)
        for i in range(2):
            _seed_pool(pool, account_id=i + 500, phone=f"7950000000{i}", client=MagicMock())
        assert pool.pool_stats["capacity_used_pct"] == 50.0

    def test_active_count_property(self, pool: SessionPool) -> None:
        assert pool.active_count == 0
        _seed_pool(pool, account_id=600, phone="79600000000", client=MagicMock())
        assert pool.active_count == 1


# ---------------------------------------------------------------------------
# Test: release_client
# ---------------------------------------------------------------------------


class TestReleaseClient:
    @pytest.mark.asyncio
    async def test_release_sets_idle(self, pool: SessionPool) -> None:
        client = _make_mock_client(connected=True, authorized=True)
        entry = _seed_pool(pool, account_id=700, phone="79700000000", client=client)
        entry.status = _ClientStatus.IN_USE

        await pool.release_client(account_id=700)
        assert entry.status == _ClientStatus.IDLE

    @pytest.mark.asyncio
    async def test_release_nonexistent_is_noop(self, pool: SessionPool) -> None:
        await pool.release_client(account_id=9999)  # must not raise


# ---------------------------------------------------------------------------
# Test: health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_not_in_pool(self, pool: SessionPool) -> None:
        result = await pool.health_check(account_id=800)
        assert result["in_pool"] is False
        assert result["connected"] is False
        assert result["authorized"] is False

    @pytest.mark.asyncio
    async def test_in_pool_connected_authorized(self, pool: SessionPool) -> None:
        client = _make_mock_client(connected=True, authorized=True)
        _seed_pool(pool, account_id=801, phone="79800000001", client=client)

        result = await pool.health_check(account_id=801)
        assert result["in_pool"] is True
        assert result["connected"] is True
        assert result["authorized"] is True

    @pytest.mark.asyncio
    async def test_in_pool_not_authorized(self, pool: SessionPool) -> None:
        client = _make_mock_client(connected=True, authorized=False)
        _seed_pool(pool, account_id=802, phone="79800000002", client=client)

        result = await pool.health_check(account_id=802)
        assert result["authorized"] is False


# ---------------------------------------------------------------------------
# Test: idle eviction
# ---------------------------------------------------------------------------


class TestIdleEviction:
    @pytest.mark.asyncio
    async def test_evict_idle_removes_expired(self, tmp_path: Path) -> None:
        pool = _make_pool(tmp_path, idle_timeout_sec=0.01)  # 10 ms
        client = _make_mock_client()
        entry = _seed_pool(pool, account_id=900, phone="79900000000", client=client)
        # Age the entry beyond the timeout.
        entry.last_used_at = time.monotonic() - 1.0

        evicted = await pool.evict_idle_clients()
        assert evicted == 1
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_evict_idle_skips_in_use(self, tmp_path: Path) -> None:
        pool = _make_pool(tmp_path, idle_timeout_sec=0.01)
        client = _make_mock_client()
        entry = _seed_pool(pool, account_id=901, phone="79900000001", client=client)
        entry.status = _ClientStatus.IN_USE
        entry.last_used_at = time.monotonic() - 1.0

        evicted = await pool.evict_idle_clients()
        assert evicted == 0
        assert pool.active_count == 1  # still there

    @pytest.mark.asyncio
    async def test_evict_idle_skips_recent(self, tmp_path: Path) -> None:
        pool = _make_pool(tmp_path, idle_timeout_sec=600.0)
        client = _make_mock_client()
        _seed_pool(pool, account_id=902, phone="79900000002", client=client)

        evicted = await pool.evict_idle_clients()
        assert evicted == 0
        assert pool.active_count == 1


# ---------------------------------------------------------------------------
# Test: SessionDeadError on reconnect auth failure
# ---------------------------------------------------------------------------


class TestSessionDeadError:
    @pytest.mark.asyncio
    async def test_dead_error_on_build(self, pool: SessionPool) -> None:
        """_build_and_connect raising SessionDeadError propagates to get_client."""
        with patch.object(
            pool,
            "_build_and_connect",
            new=AsyncMock(side_effect=SessionDeadError("revoked")),
        ), patch.object(
            pool,
            "_resolve_phone",
            new=AsyncMock(return_value="79001000000"),
        ):
            with pytest.raises(SessionDeadError, match="revoked"):
                await pool.get_client(account_id=999, db_session=AsyncMock())

        # Pool must not contain the dead entry.
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_reconnect_auth_failure_evicts(self, pool: SessionPool) -> None:
        """
        If a pooled client is not authorized on re-check (_ensure_alive returns False),
        the entry is evicted and get_client falls through to _build_and_connect.
        """
        # Unauthorized client already in pool.
        client = _make_mock_client(connected=True, authorized=False)
        _seed_pool(pool, account_id=50, phone="79005000000", client=client)

        good_client = _make_mock_client(connected=True, authorized=True)
        with patch.object(
            pool,
            "_build_and_connect",
            new=AsyncMock(return_value=good_client),
        ), patch.object(
            pool,
            "_resolve_phone",
            new=AsyncMock(return_value="79005000000"),
        ):
            result = await pool.get_client(account_id=50, db_session=AsyncMock())

        assert result is good_client


# ---------------------------------------------------------------------------
# Test: concurrent access to same account_id is serialized
# ---------------------------------------------------------------------------


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_get_same_account_returns_same_client(
        self, pool: SessionPool
    ) -> None:
        """
        Concurrent get_client calls for the same account_id should not create
        two connections.  Both must return the same client object.
        """
        build_count = 0
        client_obj = _make_mock_client(connected=True, authorized=True)

        async def _fake_build(*args, **kwargs):
            nonlocal build_count
            await asyncio.sleep(0.01)  # simulate latency
            build_count += 1
            return client_obj

        with patch.object(pool, "_build_and_connect", new=_fake_build), patch.object(
            pool,
            "_resolve_phone",
            new=AsyncMock(return_value="79006000000"),
        ):
            results = await asyncio.gather(
                pool.get_client(account_id=60, db_session=AsyncMock()),
                pool.get_client(account_id=60, db_session=AsyncMock()),
            )

        assert results[0] is client_obj
        assert results[1] is client_obj
        # The per-account lock ensures _build_and_connect runs exactly once.
        assert build_count == 1


# ---------------------------------------------------------------------------
# Private helper
# ---------------------------------------------------------------------------


def _mock_result_account(db: AsyncMock, account: Any) -> None:
    """Configure db.execute to return a scalars result with the given account."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=account)
    db.execute = AsyncMock(return_value=mock_result)
