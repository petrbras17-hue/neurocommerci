"""Tests for DigestReporter event formatting."""
from __future__ import annotations

import pytest

from core.digest_service import format_event_message


class TestFormatEventMessage:

    def test_deploy_event(self):
        msg = format_event_message("nc:event:deploy", {
            "commit": "abc1234",
            "sprint": "Sprint 5",
            "services": "ops_api, bot",
        })
        assert "DEPLOY" in msg
        assert "abc1234" in msg
        assert "ops_api" in msg

    def test_account_event(self):
        msg = format_event_message("nc:event:account", {
            "phone": "+79637428613",
            "name": "Ирина",
            "status": "frozen",
            "action": "paused liveliness loop",
        })
        assert "ACCOUNT" in msg
        assert "+79637428613" in msg
        assert "frozen" in msg

    def test_health_event(self):
        msg = format_event_message("nc:event:health", {
            "total_accounts": 3,
            "avg_health": 72,
            "below_threshold": 1,
        })
        assert "HEALTH" in msg
        assert "72" in msg

    def test_parsing_event(self):
        msg = format_event_message("nc:event:parsing", {
            "job_id": 42,
            "channels_found": 340,
            "russian": 312,
            "with_comments": 89,
        })
        assert "PARSING" in msg
        assert "340" in msg

    def test_error_event(self):
        msg = format_event_message("nc:event:error", {
            "service": "liveliness",
            "error": "connection timeout",
        })
        assert "ERROR" in msg
        assert "connection timeout" in msg

    def test_unknown_category(self):
        msg = format_event_message("nc:event:unknown_thing", {
            "data": "something",
        })
        # Should not crash, should use category name or SYSTEM
        assert "unknown_thing" in msg.lower() or "SYSTEM" in msg
