#!/usr/bin/env python3
"""Manual-gate behavior checks for CI smoke."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from core.policy_engine import policy_engine
from core.rate_limiter import RateLimiter


def resolve_post_warmup_stage(days_active: int) -> str:
    if int(days_active or 0) < 15:
        return "warming_up"
    if settings.MANUAL_GATE_REQUIRED:
        return "gate_review"
    return "execution_ready" if settings.HUMAN_GATED_PACKAGING else "active_commenting"


def main() -> int:
    expected = "gate_review" if settings.MANUAL_GATE_REQUIRED else ("execution_ready" if settings.HUMAN_GATED_PACKAGING else "active_commenting")
    resolved_early = resolve_post_warmup_stage(7)
    resolved_ready = resolve_post_warmup_stage(15)

    if resolved_early != "warming_up":
        raise RuntimeError(f"resolve_post_warmup_stage(7) expected warming_up, got {resolved_early}")
    if resolved_ready != expected:
        raise RuntimeError(
            f"resolve_post_warmup_stage(15) expected {expected}, got {resolved_ready}"
        )

    original_mode = settings.COMPLIANCE_MODE
    settings.COMPLIANCE_MODE = "strict"
    try:
        decision = policy_engine.evaluate(
            "comment_send_attempt",
            {"account": {"lifecycle_stage": resolved_ready, "status": "active"}},
        )
    finally:
        settings.COMPLIANCE_MODE = original_mode

    if settings.MANUAL_GATE_REQUIRED and decision.action != "block":
        raise RuntimeError(
            "manual gate mode expected comment block before gate approve"
        )
    if (not settings.MANUAL_GATE_REQUIRED) and decision.action not in {"allow", "warn"}:
        raise RuntimeError(
            f"non-manual gate mode expected allow/warn, got {decision.action}"
        )

    if (settings.NEW_ACCOUNT_LAUNCH_MODE or "").strip().lower() == "faster_1d":
        limiter = RateLimiter()
        d0 = limiter.get_daily_limit(0, account_age_days=999)
        d1 = limiter.get_daily_limit(1, account_age_days=999)
        if d0 != 0:
            raise RuntimeError(f"faster_1d expects D0 limit=0, got {d0}")
        if d1 <= 0:
            raise RuntimeError(f"faster_1d expects D1 limit>0, got {d1}")

    print("gate_test_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
