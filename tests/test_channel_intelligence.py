"""Tests for Channel Intelligence Engine."""
import pytest
from storage.models import ChannelProfile, ChannelBanEvent, ChannelJoinRequest


def test_channel_profile_model_exists():
    assert ChannelProfile.__tablename__ == "channel_profiles"
    cols = {c.name for c in ChannelProfile.__table__.columns}
    assert "tenant_id" in cols
    assert "telegram_id" in cols
    assert "channel_type" in cols
    assert "is_private" in cols
    assert "slow_mode_seconds" in cols
    assert "ai_extracted_rules" in cols
    assert "learned_rules" in cols
    assert "ban_risk" in cols
    assert "success_rate" in cols


def test_channel_ban_event_model_exists():
    assert ChannelBanEvent.__tablename__ == "channel_ban_events"
    cols = {c.name for c in ChannelBanEvent.__table__.columns}
    assert "tenant_id" in cols
    assert "channel_profile_id" in cols
    assert "account_id" in cols
    assert "ban_type" in cols
    assert "ai_analysis" in cols


def test_channel_join_request_model_exists():
    assert ChannelJoinRequest.__tablename__ == "channel_join_requests"
    cols = {c.name for c in ChannelJoinRequest.__table__.columns}
    assert "tenant_id" in cols
    assert "account_id" in cols
    assert "telegram_id" in cols
    assert "status" in cols


# ---------------------------------------------------------------------------
# Channel Intelligence Engine unit tests
# ---------------------------------------------------------------------------

from core.channel_intelligence import (
    ChannelProfiler,
    JoinRequestTracker,
    BanPatternLearner,
    compute_ban_risk,
    _RULES_EXTRACTION_PROMPT,
    _BAN_ANALYSIS_PROMPT,
)


def test_compute_ban_risk_low():
    assert compute_ban_risk(success_rate=0.95, total_bans=1, total_comments=100) == "low"

def test_compute_ban_risk_medium():
    assert compute_ban_risk(success_rate=0.80, total_bans=5, total_comments=25) == "medium"

def test_compute_ban_risk_high():
    assert compute_ban_risk(success_rate=0.60, total_bans=10, total_comments=25) == "high"

def test_compute_ban_risk_critical():
    assert compute_ban_risk(success_rate=0.30, total_bans=20, total_comments=30) == "critical"

def test_compute_ban_risk_no_data():
    assert compute_ban_risk(success_rate=1.0, total_bans=0, total_comments=0) == "low"

def test_rules_extraction_prompt_exists():
    assert "JSON" in _RULES_EXTRACTION_PROMPT
    assert "max_messages_per_hour" in _RULES_EXTRACTION_PROMPT

def test_ban_analysis_prompt_exists():
    assert "JSON" in _BAN_ANALYSIS_PROMPT
    assert "cause" in _BAN_ANALYSIS_PROMPT

def test_channel_profiler_init():
    profiler = ChannelProfiler(redis_client=None, ai_router_func=None)
    assert profiler is not None
    assert profiler.redis is None

def test_join_request_tracker_init():
    tracker = JoinRequestTracker(redis_client=None)
    assert tracker is not None

def test_ban_pattern_learner_init():
    learner = BanPatternLearner(redis_client=None, ai_router_func=None)
    assert learner is not None
