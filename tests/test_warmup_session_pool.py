"""
WarmupEngine + SessionPool integration tests.

All tests are mock-based — no real Telegram connections are made.

Coverage:
  1. SessionPool is used when provided (not legacy SessionManager).
  2. AntiDetection mode matches warmup config mode.
  3. Fallback channels used when target_channels not configured.
  4. Actions are randomly skipped based on skip_chance (should_skip_action).
  5. lifecycle.on_flood_wait() called on FloodWaitError.
  6. lifecycle.on_frozen() called on FrozenMethodInvalidError.
  7. lifecycle.on_session_dead() called on SessionDeadError.
  8. lifecycle.on_warmup_complete() called on successful completion.
  9. SessionPool.release_client() always called in finally block.
 10. PoolCapacityError returns STATUS_FAILED with reason "pool_capacity".
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from core.session_pool import PoolCapacityError, SessionDeadError
from core.warmup_engine import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    WarmupEngine,
    _FALLBACK_READ_CHANNELS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_config(
    *,
    mode: str = "conservative",
    enable_read_channels: bool = True,
    enable_reactions: bool = False,
    enable_dialogs_between_accounts: bool = False,
    target_channels: list | None = None,
    safety_limit_actions_per_hour: int = 10,
    active_hours_start: int = 0,
    active_hours_end: int = 23,
    warmup_duration_minutes: int = 30,
    interval_between_sessions_hours: int = 6,
) -> MagicMock:
    cfg = MagicMock()
    cfg.mode = mode
    cfg.enable_read_channels = enable_read_channels
    cfg.enable_reactions = enable_reactions
    cfg.enable_dialogs_between_accounts = enable_dialogs_between_accounts
    cfg.target_channels = target_channels
    cfg.safety_limit_actions_per_hour = safety_limit_actions_per_hour
    cfg.active_hours_start = active_hours_start
    cfg.active_hours_end = active_hours_end
    cfg.warmup_duration_minutes = warmup_duration_minutes
    cfg.interval_between_sessions_hours = interval_between_sessions_hours
    return cfg


def _make_warmup_session(account_id: int = 42, warmup_id: int = 1) -> MagicMock:
    ws = MagicMock()
    ws.id = 99
    ws.account_id = account_id
    ws.warmup_id = warmup_id
    ws.status = "pending"
    ws.started_at = None
    ws.completed_at = None
    ws.actions_performed = 0
    ws.next_session_at = None
    return ws


def _make_account(account_id: int = 42, lifecycle_stage: str = "warming_up") -> MagicMock:
    acc = MagicMock()
    acc.id = account_id
    acc.phone = "+79001234567"
    acc.lifecycle_stage = lifecycle_stage
    return acc


def _make_db_session(warmup_session: MagicMock, account: MagicMock, config: MagicMock) -> AsyncMock:
    """Build an AsyncSession mock that returns the right objects for select() queries."""
    db = AsyncMock()

    # Support both session.get() (legacy) and session.execute(select(...)) (new RLS-safe pattern)
    async def _get(model_class, pk):
        name = model_class.__name__ if hasattr(model_class, "__name__") else str(model_class)
        if "WarmupSession" in name:
            return warmup_session
        if "WarmupConfig" in name:
            return config
        if "Account" in name:
            return account
        return None

    db.get = _get

    # Mock session.execute() to return a result whose .scalar_one_or_none() returns the right object
    _orig_execute = db.execute

    async def _execute(stmt, *args, **kwargs):
        stmt_str = str(stmt)
        result = MagicMock()
        if "warmup_sessions" in stmt_str:
            result.scalar_one_or_none = MagicMock(return_value=warmup_session)
        elif "warmup_configs" in stmt_str:
            result.scalar_one_or_none = MagicMock(return_value=config)
        elif "accounts" in stmt_str:
            result.scalar_one_or_none = MagicMock(return_value=account)
        else:
            result.scalar_one_or_none = MagicMock(return_value=None)
        return result

    db.execute = _execute
    db.flush = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# 1. SessionPool is used instead of legacy session_manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_pool_is_used_not_legacy_manager():
    """When session_pool is provided it must be called; legacy manager ignored."""
    mock_pool = AsyncMock()
    mock_client = MagicMock()
    mock_pool.get_client = AsyncMock(return_value=mock_client)
    mock_pool.release_client = AsyncMock()

    legacy_mgr = MagicMock()  # should NOT be called

    config = _make_config(
        enable_read_channels=True,
        enable_reactions=False,
        enable_dialogs_between_accounts=False,
    )
    ws = _make_warmup_session()
    account = _make_account()
    db = _make_db_session(ws, account, config)

    engine = WarmupEngine(session_pool=mock_pool, session_manager=legacy_mgr)

    # Patch action helpers so no real Telethon calls happen.
    with (
        patch.object(engine, "_do_read_channel", new_callable=AsyncMock, return_value=True),
        patch.object(engine, "_do_reaction", new_callable=AsyncMock, return_value=False),
        patch.object(engine, "_do_dialog_read", new_callable=AsyncMock, return_value=False),
        patch("core.warmup_engine.AccountLifecycle") as mock_lifecycle_cls,
    ):
        mock_lifecycle = AsyncMock()
        mock_lifecycle_cls.return_value = mock_lifecycle
        mock_lifecycle.on_warmup_complete = AsyncMock()

        await engine.run_warmup_session(session_id=ws.id, db_session=db)

    mock_pool.get_client.assert_called_once_with(account.id, db_session=db)
    # Legacy manager must not have been called.
    legacy_mgr.get_client.assert_not_called()


# ---------------------------------------------------------------------------
# 2. AntiDetection mode matches warmup config mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["conservative", "moderate"])
async def test_anti_detection_mode_matches_config(mode: str):
    """AntiDetection must be initialised with the same mode as config.mode."""
    mock_pool = AsyncMock()
    mock_client = MagicMock()
    mock_pool.get_client = AsyncMock(return_value=mock_client)
    mock_pool.release_client = AsyncMock()

    config = _make_config(mode=mode)
    ws = _make_warmup_session()
    account = _make_account()
    db = _make_db_session(ws, account, config)

    engine = WarmupEngine(session_pool=mock_pool)

    captured_modes: list[str] = []

    original_init = __import__(
        "core.anti_detection", fromlist=["AntiDetection"]
    ).AntiDetection.__init__

    def capturing_init(self, mode="conservative"):
        captured_modes.append(mode)
        original_init(self, mode)

    with (
        patch("core.warmup_engine.AntiDetection.__init__", capturing_init),
        patch.object(engine, "_do_read_channel", new_callable=AsyncMock, return_value=True),
        patch("core.warmup_engine.AccountLifecycle") as mock_lifecycle_cls,
    ):
        mock_lifecycle = AsyncMock()
        mock_lifecycle_cls.return_value = mock_lifecycle
        mock_lifecycle.on_warmup_complete = AsyncMock()

        await engine.run_warmup_session(session_id=ws.id, db_session=db)

    assert mode in captured_modes, (
        f"Expected AntiDetection to be initialised with mode='{mode}', "
        f"got: {captured_modes}"
    )


# ---------------------------------------------------------------------------
# 3. Fallback channels used when target_channels is None / empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("target_channels", [None, [], ""])
async def test_fallback_channels_used_when_none_configured(target_channels: Any):
    """_do_read_channel / _do_reaction must receive at least one of the fallback channels."""
    mock_pool = AsyncMock()
    mock_client = MagicMock()
    mock_pool.get_client = AsyncMock(return_value=mock_client)
    mock_pool.release_client = AsyncMock()

    config = _make_config(
        enable_read_channels=True,
        target_channels=target_channels,
    )
    ws = _make_warmup_session()
    account = _make_account()
    db = _make_db_session(ws, account, config)

    engine = WarmupEngine(session_pool=mock_pool)

    # Capture what channels _do_read_channel is called with by inspecting config.
    # The fallback is selected inside _do_read_channel itself; we verify it
    # falls through to _FALLBACK_READ_CHANNELS by letting the real method run
    # with a mocked client.
    channels_used: list[str] = []

    async def fake_get_entity(channel_id):
        channels_used.append(channel_id)
        return MagicMock()

    async def fake_get_messages(entity, limit=10):
        return []

    mock_client.get_entity = fake_get_entity
    mock_client.get_messages = fake_get_messages

    with patch("core.warmup_engine.AccountLifecycle") as mock_lifecycle_cls:
        mock_lifecycle = AsyncMock()
        mock_lifecycle_cls.return_value = mock_lifecycle
        mock_lifecycle.on_warmup_complete = AsyncMock()

        with patch("core.warmup_engine.AntiDetection") as mock_anti_cls:
            mock_anti = MagicMock()
            mock_anti.should_skip_action.return_value = False
            mock_anti.inter_action_delay = AsyncMock()
            mock_anti.simulate_reading = AsyncMock()
            mock_anti_cls.return_value = mock_anti

            await engine.run_warmup_session(session_id=ws.id, db_session=db)

    # At least one channel from the fallback list must have been used.
    assert channels_used, "get_entity was never called — no channel was resolved"
    for ch in channels_used:
        assert ch in _FALLBACK_READ_CHANNELS, (
            f"Channel '{ch}' is not in the fallback list"
        )


# ---------------------------------------------------------------------------
# 4. Actions are randomly skipped when should_skip_action returns True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_action_skipped_when_skip_chance_returns_true():
    """
    When AntiDetection.should_skip_action() returns True inside _execute_one_action,
    no concrete Telegram action is executed and the method returns False.

    This test drives _execute_one_action directly because the skip logic lives
    there (used by the background loop).  The sync run_warmup_session path calls
    action helpers directly without the skip gate.
    """
    mock_client = MagicMock()
    config = _make_config(enable_read_channels=True)

    engine = WarmupEngine()

    # Build a real AntiDetection that always returns True from should_skip_action.
    mock_anti = MagicMock()
    mock_anti.should_skip_action.return_value = True
    mock_anti.inter_action_delay = AsyncMock()

    with patch.object(engine, "_do_read_channel", new_callable=AsyncMock) as mock_read:
        result = await engine._execute_one_action(
            client=mock_client,
            config=config,
            anti_detection=mock_anti,
            stop_event=asyncio.Event(),
        )

    # When skip is forced the action must be skipped and False returned.
    mock_read.assert_not_called()
    assert result is False


# ---------------------------------------------------------------------------
# 5. lifecycle.on_flood_wait() called on FloodWaitError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_on_flood_wait_called():
    """_QuarantineError (FloodWaitError) must trigger lifecycle.on_flood_wait."""
    from core.warmup_engine import _QuarantineError

    mock_pool = AsyncMock()
    mock_client = MagicMock()
    mock_pool.get_client = AsyncMock(return_value=mock_client)
    mock_pool.release_client = AsyncMock()

    config = _make_config(enable_read_channels=True)
    ws = _make_warmup_session()
    account = _make_account()
    db = _make_db_session(ws, account, config)

    engine = WarmupEngine(session_pool=mock_pool)

    with patch("core.warmup_engine.AccountLifecycle") as mock_lifecycle_cls:
        mock_lifecycle = AsyncMock()
        mock_lifecycle_cls.return_value = mock_lifecycle
        mock_lifecycle.on_flood_wait = AsyncMock()

        with patch.object(
            engine,
            "_do_read_channel",
            new_callable=AsyncMock,
            side_effect=_QuarantineError(120),
        ):
            result = await engine.run_warmup_session(
                session_id=ws.id, db_session=db
            )

    assert result["status"] == STATUS_FAILED
    assert result["reason"] == "flood_wait"
    mock_lifecycle.on_flood_wait.assert_called_once_with(account.id, 120)


# ---------------------------------------------------------------------------
# 6. lifecycle.on_frozen() called on FrozenMethodInvalidError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_on_frozen_called():
    """_FrozenError must trigger lifecycle.on_frozen."""
    from core.warmup_engine import _FrozenError

    mock_pool = AsyncMock()
    mock_client = MagicMock()
    mock_pool.get_client = AsyncMock(return_value=mock_client)
    mock_pool.release_client = AsyncMock()

    config = _make_config(enable_read_channels=True)
    ws = _make_warmup_session()
    account = _make_account()
    db = _make_db_session(ws, account, config)

    engine = WarmupEngine(session_pool=mock_pool)

    with patch("core.warmup_engine.AccountLifecycle") as mock_lifecycle_cls:
        mock_lifecycle = AsyncMock()
        mock_lifecycle_cls.return_value = mock_lifecycle
        mock_lifecycle.on_frozen = AsyncMock()

        with patch.object(
            engine,
            "_do_read_channel",
            new_callable=AsyncMock,
            side_effect=_FrozenError(),
        ):
            result = await engine.run_warmup_session(
                session_id=ws.id, db_session=db
            )

    assert result["status"] == STATUS_FAILED
    assert result["reason"] == "account_frozen"
    mock_lifecycle.on_frozen.assert_called_once_with(account.id)


# ---------------------------------------------------------------------------
# 7. lifecycle.on_session_dead() called on SessionDeadError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_on_session_dead_called():
    """SessionDeadError from pool.get_client must trigger lifecycle.on_session_dead."""
    mock_pool = AsyncMock()
    mock_pool.get_client = AsyncMock(side_effect=SessionDeadError("session revoked"))
    mock_pool.release_client = AsyncMock()

    config = _make_config(enable_read_channels=True)
    ws = _make_warmup_session()
    account = _make_account()
    db = _make_db_session(ws, account, config)

    engine = WarmupEngine(session_pool=mock_pool)

    with patch("core.warmup_engine.AccountLifecycle") as mock_lifecycle_cls:
        mock_lifecycle = AsyncMock()
        mock_lifecycle_cls.return_value = mock_lifecycle
        mock_lifecycle.on_session_dead = AsyncMock()

        result = await engine.run_warmup_session(
            session_id=ws.id, db_session=db
        )

    assert result["status"] == STATUS_FAILED
    assert result["reason"] == "session_dead"
    mock_lifecycle.on_session_dead.assert_called_once_with(account.id)


# ---------------------------------------------------------------------------
# 8. lifecycle.on_warmup_complete() called on successful completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_on_warmup_complete_called_on_success():
    """Successful session completion must call lifecycle.on_warmup_complete."""
    mock_pool = AsyncMock()
    mock_client = MagicMock()
    mock_pool.get_client = AsyncMock(return_value=mock_client)
    mock_pool.release_client = AsyncMock()

    config = _make_config(enable_read_channels=True)
    ws = _make_warmup_session()
    account = _make_account()
    db = _make_db_session(ws, account, config)

    engine = WarmupEngine(session_pool=mock_pool)

    with patch("core.warmup_engine.AccountLifecycle") as mock_lifecycle_cls:
        mock_lifecycle = AsyncMock()
        mock_lifecycle_cls.return_value = mock_lifecycle
        mock_lifecycle.on_warmup_complete = AsyncMock()

        with patch.object(engine, "_do_read_channel", new_callable=AsyncMock, return_value=True):
            result = await engine.run_warmup_session(
                session_id=ws.id, db_session=db
            )

    assert result["status"] == STATUS_COMPLETED
    mock_lifecycle.on_warmup_complete.assert_called_once_with(account.id)


# ---------------------------------------------------------------------------
# 9. release_client() always called in finally block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_client_called_even_on_error():
    """pool.release_client must be called even when an action raises an error."""
    from core.warmup_engine import _QuarantineError

    mock_pool = AsyncMock()
    mock_client = MagicMock()
    mock_pool.get_client = AsyncMock(return_value=mock_client)
    mock_pool.release_client = AsyncMock()

    config = _make_config(enable_read_channels=True)
    ws = _make_warmup_session()
    account = _make_account()
    db = _make_db_session(ws, account, config)

    engine = WarmupEngine(session_pool=mock_pool)

    with patch("core.warmup_engine.AccountLifecycle") as mock_lifecycle_cls:
        mock_lifecycle = AsyncMock()
        mock_lifecycle_cls.return_value = mock_lifecycle
        mock_lifecycle.on_flood_wait = AsyncMock()

        with patch.object(
            engine,
            "_do_read_channel",
            new_callable=AsyncMock,
            side_effect=_QuarantineError(60),
        ):
            await engine.run_warmup_session(session_id=ws.id, db_session=db)

    mock_pool.release_client.assert_called_with(account.id)


# ---------------------------------------------------------------------------
# 10. PoolCapacityError returns STATUS_FAILED with reason "pool_capacity"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_capacity_error_returns_failed():
    """PoolCapacityError from pool.get_client must return STATUS_FAILED/pool_capacity."""
    mock_pool = AsyncMock()
    mock_pool.get_client = AsyncMock(
        side_effect=PoolCapacityError("pool full (20/20)")
    )
    mock_pool.release_client = AsyncMock()

    config = _make_config(enable_read_channels=True)
    ws = _make_warmup_session()
    account = _make_account()
    db = _make_db_session(ws, account, config)

    engine = WarmupEngine(session_pool=mock_pool)

    result = await engine.run_warmup_session(session_id=ws.id, db_session=db)

    assert result["status"] == STATUS_FAILED
    assert result["reason"] == "pool_capacity"
    # release_client must NOT be called when get_client raises.
    mock_pool.release_client.assert_not_called()
