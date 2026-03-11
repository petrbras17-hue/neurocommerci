#!/usr/bin/env python3
"""Minimal policy replay checks for CI smoke."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from core.policy_engine import policy_engine


def check(expected: str, event: str, context: dict) -> None:
    decision = policy_engine.evaluate(event, context)
    if decision.action != expected:
        raise RuntimeError(
            f"{event}: expected={expected} got={decision.action} rule={decision.rule_id}"
        )


def main() -> int:
    original_mode = settings.COMPLIANCE_MODE
    settings.COMPLIANCE_MODE = "strict"
    try:
        check(
            "block",
            "comment_send_attempt",
            {"account": {"lifecycle_stage": "warming_up", "status": "active"}},
        )
        check(
            "allow",
            "comment_send_attempt",
            {"account": {"lifecycle_stage": "active_commenting", "status": "active"}},
        )
        check(
            "block",
            "parser_client_candidate",
            {"account": {"health_status": "restricted", "lifecycle_stage": "restricted"}},
        )
        check(
            "block",
            "proxy_assignment",
            {"strict_proxy": True, "proxy_assigned": False},
        )
        check(
            "quarantine",
            "session_duplicate_detected",
            {"duplicate": True},
        )
        check(
            "warn",
            "floodwait_detected",
            {"seconds": 60},
        )
        check(
            "block",
            "parser_without_parser_phone",
            {"strict_parser_only": True, "parser_phone_configured": False},
        )
        check(
            "block",
            "missing_pinned_phone",
            {"required": True, "pinned_phone": ""},
        )
        check(
            "block",
            "risky_feature_enabled_in_strict",
            {"strict_mode": True, "requested_enable": True, "emergency_flag": False},
        )
        check(
            "warn",
            "parser_search_blocked",
            {"blocked": True},
        )
    finally:
        settings.COMPLIANCE_MODE = original_mode

    print("policy_replay_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
