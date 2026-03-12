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
