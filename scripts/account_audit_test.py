#!/usr/bin/env python3
"""Smoke checks for account/json/proxy audit helpers."""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.account_audit import (
    build_account_audit_records,
    build_json_credentials_audit,
    lifecycle_reconcile_target,
    normalize_proxy_observability,
)
from utils.session_topology import canonical_session_dir


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        session_dir = canonical_session_dir(base, 1, create=True)

        _write(session_dir / "79990001111.session")
        _write(
            session_dir / "79990001111.json",
            '{"phone":"+79990001111","session_file":"79990001111.session","app_id":2040,"app_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","device":"Pixel 8","sdk":"Android 14","app_version":"12.4.3"}',
        )
        _write(session_dir / "79990002222.session")
        _write(
            session_dir / "79990002222.json",
            '{"phone":"+79990002222","session_file":"79990002222.session","app_id":2040,"app_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","device":"Pixel 8","sdk":"Android 14","app_version":"12.4.3"}',
        )
        _write(session_dir / "79990003333.session")
        _write(
            session_dir / "79990003333.json",
            '{"phone":"+79990003333","session_file":"79990003333.session","device":"Pixel 8","sdk":"Android 14","app_version":"12.4.3"}',
        )

        shared_a = SimpleNamespace(
            id=1,
            phone="+79990001111",
            user_id=1,
            proxy_id=1,
            status="active",
            health_status="alive",
            lifecycle_stage="active_commenting",
            restriction_reason=None,
            last_active_at=None,
            last_probe_at=None,
            total_comments=0,
            days_active=0,
        )
        shared_b = SimpleNamespace(
            id=2,
            phone="+79990002222",
            user_id=1,
            proxy_id=2,
            status="active",
            health_status="alive",
            lifecycle_stage="active_commenting",
            restriction_reason=None,
            last_active_at=None,
            last_probe_at=None,
            total_comments=0,
            days_active=0,
        )
        missing = SimpleNamespace(
            id=3,
            phone="+79990003333",
            user_id=1,
            proxy_id=None,
            status="error",
            health_status="unknown",
            lifecycle_stage="uploaded",
            restriction_reason="unauthorized",
            last_active_at=None,
            last_probe_at=None,
            total_comments=0,
            days_active=0,
        )

        records = build_account_audit_records(
            [shared_a, shared_b, missing],
            sessions_dir=base,
            strict_proxy=True,
        )
        by_phone = {row["phone"]: row for row in records}
        assert by_phone["+79990001111"]["api_credentials"]["shared_warning"] is True
        assert by_phone["+79990002222"]["api_credentials"]["shared_warning"] is True
        assert by_phone["+79990001111"]["readiness"]["ready"] is True
        assert by_phone["+79990002222"]["readiness"]["ready"] is True
        assert by_phone["+79990003333"]["readiness"]["primary_blocker"] == "metadata_api_credentials_missing"

        creds = build_json_credentials_audit([shared_a, shared_b, missing], sessions_dir=base)
        assert creds["summary"]["shared_pair_accounts"] == 2
        assert creds["summary"]["missing_required_credentials"] == 1

        stale = SimpleNamespace(
            lifecycle_stage="active_commenting",
            status="error",
            health_status="expired",
        )
        assert lifecycle_reconcile_target(stale)[0] == "uploaded"

        restricted = SimpleNamespace(
            lifecycle_stage="active_commenting",
            status="active",
            health_status="restricted",
        )
        assert lifecycle_reconcile_target(restricted)[0] == "restricted"

        proxy_payload = normalize_proxy_observability(
            {
                "total": 10,
                "active": 8,
                "healthy": 3,
                "unknown": 2,
                "failing": 1,
                "dead_or_disabled": 4,
                "usable_for_binding": 1,
                "duplicate_bound": 2,
                "low_stock": True,
                "recommended_topup": 7,
            },
            {"deleted": 3},
        )
        assert proxy_payload["binding_uniqueness_ok"] is False
        assert "7" in proxy_payload["low_stock_warning"]

    print("account_audit_test_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
