"""
Integration tests for AntiDetection + SmartCommenter wiring in FarmThread.

All tests use mocks — no real Telegram connections or DB sessions.

Coverage:
  - AntiDetection mode selection by account age
  - CommentOrchestrator called during commenting phase
  - should_comment=False skips posting
  - never-first-commenter rule (min_existing_comments)
  - emoji-first trick probability (use_emoji_trick flag)
  - FloodWait → lifecycle.on_flood_wait
  - SessionDead → lifecycle.on_session_dead
  - AntiDetection delays called before each action
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# -------------------------------------------------------------------------
# Helpers to construct a minimal FarmThread without real infrastructure
# -------------------------------------------------------------------------

def _make_farm_config(
    delay_min: float = 5.0,
    delay_max: float = 10.0,
    comment_pct: int = 100,
    comment_tone: str = "positive",
    comment_language: str = "ru",
    comment_prompt: str = "",
) -> MagicMock:
    cfg = MagicMock()
    cfg.delay_before_comment_min = delay_min
    cfg.delay_before_comment_max = delay_max
    cfg.delay_before_join_min = 5.0
    cfg.delay_before_join_max = 10.0
    cfg.comment_percentage = comment_pct
    cfg.comment_tone = comment_tone
    cfg.comment_language = comment_language
    cfg.comment_prompt = comment_prompt
    return cfg


async def _noop_publish(**kwargs):
    pass


def _make_thread(
    account_days_active: int = 0,
    session_pool: Optional[object] = None,
) -> "FarmThread":
    from core.farm_thread import FarmThread

    session_mgr = MagicMock()
    session_mgr.get_client.return_value = MagicMock()

    redis_client = AsyncMock()
    redis_client.get.return_value = None

    return FarmThread(
        thread_id=1,
        account_id=42,
        phone="+79991234567",
        farm_id=10,
        tenant_id=5,
        farm_config=_make_farm_config(),
        assigned_channels=[],
        session_manager=session_mgr,
        ai_router_func=AsyncMock(),
        redis_client=redis_client,
        publish_event_func=_noop_publish,
        channel_intel=None,
        session_pool=session_pool,
        account_days_active=account_days_active,
    )


# -------------------------------------------------------------------------
# 1. AntiDetection mode selection by account age
# -------------------------------------------------------------------------

class TestAntiDetectionModeSelection:

    @pytest.mark.parametrize("days,expected_mode", [
        (0, "conservative"),
        (1, "conservative"),
        (2, "conservative"),
        (3, "moderate"),
        (15, "moderate"),
        (29, "moderate"),
        (30, "aggressive"),
        (100, "aggressive"),
    ])
    def test_mode_from_days_active(self, days: int, expected_mode: str) -> None:
        from core.farm_thread import _anti_detection_mode_for_days
        assert _anti_detection_mode_for_days(days) == expected_mode

    def test_thread_stores_mode(self) -> None:
        thread = _make_thread(account_days_active=5)
        assert thread._anti_mode == "moderate"

    def test_thread_conservative_for_new_account(self) -> None:
        thread = _make_thread(account_days_active=0)
        assert thread._anti_mode == "conservative"

    def test_thread_aggressive_for_old_account(self) -> None:
        thread = _make_thread(account_days_active=60)
        assert thread._anti_mode == "aggressive"

    @pytest.mark.asyncio
    async def test_init_anti_detection_creates_instance(self) -> None:
        thread = _make_thread(account_days_active=10)

        # Patch DB load so we don't need a real session
        with patch.object(thread, "_load_account_days_active", new=AsyncMock(return_value=10)):
            await thread._init_anti_detection()

        from core.anti_detection import AntiDetection
        assert isinstance(thread._anti, AntiDetection)
        assert thread._anti.mode == "moderate"

    @pytest.mark.asyncio
    async def test_init_anti_detection_upgrades_days_from_db(self) -> None:
        """If days_active=0 at construction, it should load from DB."""
        thread = _make_thread(account_days_active=0)

        with patch.object(thread, "_load_account_days_active", new=AsyncMock(return_value=45)):
            await thread._init_anti_detection()

        assert thread._account_days_active == 45
        assert thread._anti_mode == "aggressive"


# -------------------------------------------------------------------------
# 2. CommentOrchestrator called during commenting phase
# -------------------------------------------------------------------------

class TestSmartCommenterWiring:

    @pytest.mark.asyncio
    async def test_init_orchestrator_creates_instance(self) -> None:
        thread = _make_thread()
        thread._init_orchestrator()

        from core.smart_commenter import CommentOrchestrator
        assert isinstance(thread._orchestrator, CommentOrchestrator)

    @pytest.mark.asyncio
    async def test_smart_comment_pipeline_calls_process_post(self) -> None:
        thread = _make_thread()
        thread._init_orchestrator()

        mock_decision = MagicMock()
        mock_decision.should_comment = True
        mock_decision.use_emoji_trick = False
        mock_decision.delay_seconds = 0

        with patch.object(
            thread._orchestrator,
            "process_post",
            new=AsyncMock(return_value=("Great post!", mock_decision)),
        ) as mock_process:
            comment, decision = await thread._smart_comment_pipeline(
                post={
                    "text": "Test post text",
                    "channel": MagicMock(),
                    "channel_id": 1,
                    "channel_title": "Test Channel",
                    "channel_username": "testchan",
                    "message_id": 100,
                    "replies_count": 3,
                }
            )

        assert comment == "Great post!"
        assert mock_process.called

    @pytest.mark.asyncio
    async def test_smart_comment_pipeline_returns_none_when_skipped(self) -> None:
        thread = _make_thread()
        thread._init_orchestrator()

        mock_decision = MagicMock()
        mock_decision.should_comment = False
        mock_decision.delay_seconds = 0

        with patch.object(
            thread._orchestrator,
            "process_post",
            new=AsyncMock(return_value=(None, mock_decision)),
        ):
            comment, decision = await thread._smart_comment_pipeline(
                post={
                    "text": "Some post",
                    "replies_count": 0,
                }
            )

        assert comment is None


# -------------------------------------------------------------------------
# 3. Never-first-commenter rule
# -------------------------------------------------------------------------

class TestNeverFirstCommenter:

    @pytest.mark.asyncio
    async def test_zero_replies_produces_empty_existing_comments(self) -> None:
        """When replies_count=0, we should not try to fetch comments."""
        thread = _make_thread()
        thread._init_orchestrator()

        captured_args: list = []

        async def fake_process_post(post, existing_comments, channel_info):
            captured_args.append(existing_comments)
            mock_dec = MagicMock()
            mock_dec.should_comment = False
            mock_dec.delay_seconds = 0
            mock_dec.use_emoji_trick = False
            return None, mock_dec

        with patch.object(thread._orchestrator, "process_post", side_effect=fake_process_post):
            with patch.object(thread, "_fetch_existing_comments", new=AsyncMock(return_value=[])) as mock_fetch:
                await thread._smart_comment_pipeline(
                    post={"text": "Test", "replies_count": 0, "channel": None, "message_id": 1}
                )

        # _fetch_existing_comments must NOT be called when replies_count=0
        mock_fetch.assert_not_called()
        # existing_comments passed to process_post must be empty
        assert captured_args[0] == []

    @pytest.mark.asyncio
    async def test_nonzero_replies_fetches_existing_comments(self) -> None:
        thread = _make_thread()
        thread._init_orchestrator()

        async def fake_process_post(post, existing_comments, channel_info):
            mock_dec = MagicMock()
            mock_dec.should_comment = True
            mock_dec.delay_seconds = 0
            mock_dec.use_emoji_trick = False
            return "ok comment", mock_dec

        with patch.object(thread._orchestrator, "process_post", side_effect=fake_process_post):
            with patch.object(
                thread,
                "_fetch_existing_comments",
                new=AsyncMock(return_value=["existing comment 1"]),
            ) as mock_fetch:
                comment, _ = await thread._smart_comment_pipeline(
                    post={
                        "text": "Test",
                        "replies_count": 2,
                        "channel": MagicMock(),
                        "message_id": 99,
                    }
                )

        mock_fetch.assert_called_once()


# -------------------------------------------------------------------------
# 4. Emoji-first trick probability
# -------------------------------------------------------------------------

class TestEmojiTrick:

    @pytest.mark.asyncio
    async def test_emoji_trick_path_calls_apply_emoji_trick(self) -> None:
        thread = _make_thread(account_days_active=10)
        await thread._init_anti_detection()
        thread._init_orchestrator()

        mock_decision = MagicMock()
        mock_decision.use_emoji_trick = True
        mock_decision.delay_seconds = 0

        mock_client = AsyncMock()
        mock_channel = MagicMock()

        with patch.object(
            thread._orchestrator,
            "apply_emoji_trick",
            new=AsyncMock(return_value=True),
        ) as mock_emoji:
            with patch.object(thread._anti, "simulate_typing", new=AsyncMock()):
                with patch.object(thread._anti, "pre_comment_delay", new=AsyncMock()):
                    await thread._post_comment_smart(
                        client=mock_client,
                        post={
                            "channel": mock_channel,
                            "channel_id": 1,
                            "channel_title": "Chan",
                            "message_id": 55,
                        },
                        comment_text="Great!",
                        decision=mock_decision,
                    )

        mock_emoji.assert_called_once()
        call_kwargs = mock_emoji.call_args.kwargs
        assert call_kwargs["client"] == mock_client
        assert call_kwargs["real_comment"] == "Great!"

    @pytest.mark.asyncio
    async def test_no_emoji_trick_sends_directly(self) -> None:
        thread = _make_thread(account_days_active=10)
        await thread._init_anti_detection()
        thread._init_orchestrator()

        mock_decision = MagicMock()
        mock_decision.use_emoji_trick = False
        mock_decision.delay_seconds = 0

        mock_client = AsyncMock()
        mock_channel = MagicMock()

        with patch.object(
            thread._orchestrator,
            "apply_emoji_trick",
            new=AsyncMock(return_value=True),
        ) as mock_emoji:
            with patch.object(thread._anti, "simulate_typing", new=AsyncMock()):
                with patch.object(thread._anti, "pre_comment_delay", new=AsyncMock()):
                    with patch.object(thread._anti, "inter_action_delay", new=AsyncMock()):
                        await thread._post_comment_smart(
                            client=mock_client,
                            post={
                                "channel": mock_channel,
                                "channel_id": 1,
                                "channel_title": "Chan",
                                "message_id": 55,
                            },
                            comment_text="Direct comment",
                            decision=mock_decision,
                        )

        mock_emoji.assert_not_called()
        mock_client.send_message.assert_awaited_once()


# -------------------------------------------------------------------------
# 5. FloodWait → lifecycle.on_flood_wait
# -------------------------------------------------------------------------

class TestFloodWaitLifecycle:

    @pytest.mark.asyncio
    async def test_on_flood_wait_error_calls_lifecycle(self) -> None:
        """
        _on_flood_wait_error() must call AccountLifecycle.on_flood_wait.

        The lifecycle is imported lazily inside the method body.
        We patch the source module so the import resolution picks up the mock.
        """
        thread = _make_thread(account_days_active=10)

        mock_lifecycle_instance = AsyncMock()

        # Build a minimal async session context manager mock
        mock_begin_cm = AsyncMock()
        mock_begin_cm.__aenter__ = AsyncMock(return_value=mock_begin_cm)
        mock_begin_cm.__aexit__ = AsyncMock(return_value=None)

        mock_sess_cm = AsyncMock()
        mock_sess_cm.__aenter__ = AsyncMock(return_value=mock_sess_cm)
        mock_sess_cm.__aexit__ = AsyncMock(return_value=None)
        mock_sess_cm.begin = MagicMock(return_value=mock_begin_cm)

        with patch("core.account_lifecycle.AccountLifecycle", return_value=mock_lifecycle_instance):
            import storage.sqlite_db as _db
            original_async_session = _db.async_session
            original_apply_rls = _db.apply_session_rls_context

            def _fake_session():
                return mock_sess_cm

            async def _fake_rls(sess, **kwargs):
                pass

            _db.async_session = _fake_session
            _db.apply_session_rls_context = _fake_rls
            try:
                with patch.object(thread, "handle_flood_wait", new=AsyncMock()):
                    await thread._on_flood_wait_error(seconds=60)
            finally:
                _db.async_session = original_async_session
                _db.apply_session_rls_context = original_apply_rls

        mock_lifecycle_instance.on_flood_wait.assert_awaited_once_with(
            thread.account_id, seconds=60
        )

    @pytest.mark.asyncio
    async def test_flood_wait_short_sets_cooldown(self) -> None:
        thread = _make_thread()

        events_published: list = []

        async def capture_event(**kwargs):
            events_published.append(kwargs)

        thread._publish_event = capture_event

        with patch.object(thread, "_flush_status_to_db", new=AsyncMock()):
            await thread.handle_flood_wait(seconds=60)

        assert thread._state == "cooldown"
        assert thread._cooldown_until is not None
        published_types = [e["event_type"] for e in events_published]
        assert "flood_wait" in published_types

    @pytest.mark.asyncio
    async def test_flood_wait_long_sets_quarantine(self) -> None:
        thread = _make_thread()

        async def capture_event(**kwargs):
            pass

        thread._publish_event = capture_event

        with patch.object(thread, "_flush_status_to_db", new=AsyncMock()):
            with patch.object(thread, "_flush_quarantine_to_db", new=AsyncMock()):
                await thread.handle_flood_wait(seconds=400)

        assert thread._state == "quarantine"
        assert thread._quarantine_until is not None


# -------------------------------------------------------------------------
# 6. SessionDead → lifecycle.on_session_dead
# -------------------------------------------------------------------------

class TestSessionDeadLifecycle:

    def _patch_db_for_lifecycle(self, mock_lifecycle_instance):
        """
        Return a context-manager stack that patches storage.sqlite_db so the
        lazy imports inside _on_session_dead_error() pick up mocks.
        """
        import storage.sqlite_db as _db
        import contextlib

        mock_begin_cm = AsyncMock()
        mock_begin_cm.__aenter__ = AsyncMock(return_value=mock_begin_cm)
        mock_begin_cm.__aexit__ = AsyncMock(return_value=None)

        mock_sess_cm = AsyncMock()
        mock_sess_cm.__aenter__ = AsyncMock(return_value=mock_sess_cm)
        mock_sess_cm.__aexit__ = AsyncMock(return_value=None)
        mock_sess_cm.begin = MagicMock(return_value=mock_begin_cm)

        def _fake_session():
            return mock_sess_cm

        async def _fake_rls(sess, **kwargs):
            pass

        original_session = _db.async_session
        original_rls = _db.apply_session_rls_context

        @contextlib.contextmanager
        def _ctx():
            _db.async_session = _fake_session
            _db.apply_session_rls_context = _fake_rls
            try:
                yield
            finally:
                _db.async_session = original_session
                _db.apply_session_rls_context = original_rls

        return _ctx()

    @pytest.mark.asyncio
    async def test_on_session_dead_calls_lifecycle(self) -> None:
        thread = _make_thread(account_days_active=10)

        mock_lifecycle_instance = AsyncMock()

        with patch("core.account_lifecycle.AccountLifecycle", return_value=mock_lifecycle_instance):
            with self._patch_db_for_lifecycle(mock_lifecycle_instance):
                with patch.object(thread, "_flush_status_to_db", new=AsyncMock()):
                    await thread._on_session_dead_error()

        mock_lifecycle_instance.on_session_dead.assert_awaited_once_with(thread.account_id)

    @pytest.mark.asyncio
    async def test_on_session_dead_transitions_to_error(self) -> None:
        """
        Even when lifecycle raises, the thread must transition to error state.

        We make the DB session context manager raise so that the entire
        lifecycle block is skipped gracefully, and verify state=error.
        """
        thread = _make_thread()

        import storage.sqlite_db as _db
        original_session = _db.async_session

        def _exploding_session():
            raise RuntimeError("no db available")

        _db.async_session = _exploding_session
        try:
            with patch.object(thread, "_flush_status_to_db", new=AsyncMock()):
                await thread._on_session_dead_error()
        finally:
            _db.async_session = original_session

        assert thread._state == "error"

    @pytest.mark.asyncio
    async def test_on_session_dead_disconnects_pool(self) -> None:
        mock_pool = AsyncMock()
        mock_pool._pool = {}
        thread = _make_thread(session_pool=mock_pool)

        import storage.sqlite_db as _db
        original_session = _db.async_session

        def _exploding_session():
            raise RuntimeError("no db")

        _db.async_session = _exploding_session
        try:
            with patch.object(thread, "_flush_status_to_db", new=AsyncMock()):
                await thread._on_session_dead_error()
        finally:
            _db.async_session = original_session

        mock_pool.disconnect_client.assert_awaited_once_with(thread.account_id)


# -------------------------------------------------------------------------
# 7. AntiDetection delays called before actions
# -------------------------------------------------------------------------

class TestAntiDetectionDelays:

    @pytest.mark.asyncio
    async def test_simulate_typing_called_before_post_comment(self) -> None:
        thread = _make_thread(account_days_active=10)
        await thread._init_anti_detection()
        thread._init_orchestrator()

        mock_decision = MagicMock()
        mock_decision.use_emoji_trick = False
        mock_decision.delay_seconds = 0

        mock_client = AsyncMock()

        simulate_typing_calls: list = []

        async def fake_simulate_typing(client, peer, **kwargs):
            simulate_typing_calls.append("called")

        with patch.object(thread._anti, "simulate_typing", side_effect=fake_simulate_typing):
            with patch.object(thread._anti, "pre_comment_delay", new=AsyncMock()):
                with patch.object(thread._anti, "inter_action_delay", new=AsyncMock()):
                    await thread._post_comment_smart(
                        client=mock_client,
                        post={
                            "channel": MagicMock(),
                            "channel_id": 1,
                            "channel_title": "Chan",
                            "message_id": 42,
                        },
                        comment_text="Hello",
                        decision=mock_decision,
                    )

        assert len(simulate_typing_calls) >= 1, "simulate_typing must be called before posting"

    @pytest.mark.asyncio
    async def test_pre_comment_delay_called_when_no_strategy_delay(self) -> None:
        thread = _make_thread(account_days_active=10)
        await thread._init_anti_detection()
        thread._init_orchestrator()

        mock_decision = MagicMock()
        mock_decision.use_emoji_trick = False
        mock_decision.delay_seconds = 0  # no strategy delay → pre_comment_delay should run

        pre_comment_calls: list = []

        async def fake_pre_comment():
            pre_comment_calls.append("called")

        with patch.object(thread._anti, "simulate_typing", new=AsyncMock()):
            with patch.object(thread._anti, "pre_comment_delay", side_effect=fake_pre_comment):
                with patch.object(thread._anti, "inter_action_delay", new=AsyncMock()):
                    await thread._post_comment_smart(
                        client=AsyncMock(),
                        post={
                            "channel": MagicMock(),
                            "channel_id": 1,
                            "channel_title": "Chan",
                            "message_id": 42,
                        },
                        comment_text="Hi",
                        decision=mock_decision,
                    )

        assert len(pre_comment_calls) >= 1, "pre_comment_delay must be called"

    @pytest.mark.asyncio
    async def test_inter_action_delay_called_after_direct_post(self) -> None:
        thread = _make_thread(account_days_active=10)
        await thread._init_anti_detection()
        thread._init_orchestrator()

        mock_decision = MagicMock()
        mock_decision.use_emoji_trick = False
        mock_decision.delay_seconds = 0

        inter_action_calls: list = []

        async def fake_inter_action():
            inter_action_calls.append("called")

        with patch.object(thread._anti, "simulate_typing", new=AsyncMock()):
            with patch.object(thread._anti, "pre_comment_delay", new=AsyncMock()):
                with patch.object(thread._anti, "inter_action_delay", side_effect=fake_inter_action):
                    await thread._post_comment_smart(
                        client=AsyncMock(),
                        post={
                            "channel": MagicMock(),
                            "channel_id": 1,
                            "channel_title": "Chan",
                            "message_id": 42,
                        },
                        comment_text="After post",
                        decision=mock_decision,
                    )

        assert len(inter_action_calls) >= 1, "inter_action_delay must be called after posting"

    @pytest.mark.asyncio
    async def test_pre_join_delay_called_in_subscribe(self) -> None:
        """AntiDetection.pre_join_delay() must be called before each channel join."""
        thread = _make_thread(account_days_active=10)
        await thread._init_anti_detection()

        pre_join_calls: list = []

        async def fake_pre_join():
            pre_join_calls.append("called")

        mock_client = AsyncMock()

        # Simulate UserAlreadyParticipantError so we don't need real Telegram
        try:
            from telethon.errors import UserAlreadyParticipantError
            mock_client.get_entity.side_effect = UserAlreadyParticipantError(None)
        except ImportError:
            # Telethon not installed — skip the subscribe body
            mock_client.get_entity.side_effect = Exception("no telethon")

        thread.session_mgr.get_client.return_value = mock_client

        channels = [{"username": "testchan", "id": 1, "title": "Test"}]

        with patch.object(thread._anti, "pre_join_delay", side_effect=fake_pre_join):
            with patch.object(thread._anti, "simulate_channel_browse", new=AsyncMock()):
                with patch.object(thread._anti, "inter_action_delay", new=AsyncMock()):
                    try:
                        from telethon.tl.functions.channels import JoinChannelRequest
                        with patch("core.farm_thread.JoinChannelRequest", create=True):
                            await thread.subscribe_to_channels(channels)
                    except Exception:
                        # Telethon import failures are acceptable in unit tests
                        await thread.subscribe_to_channels(channels)

        # pre_join_delay must have been called at least once
        assert len(pre_join_calls) >= 1, "pre_join_delay must be called before join"


# -------------------------------------------------------------------------
# 8. Stats reflect comments_sent
# -------------------------------------------------------------------------

class TestStats:

    def test_stats_include_anti_detection_mode(self) -> None:
        thread = _make_thread(account_days_active=0)
        stats = thread.stats
        assert "anti_detection_mode" in stats
        assert stats["anti_detection_mode"] == "conservative"

    def test_stats_comment_counters_start_at_zero(self) -> None:
        thread = _make_thread()
        stats = thread.stats
        assert stats["comments_sent"] == 0
        assert stats["comments_failed"] == 0
