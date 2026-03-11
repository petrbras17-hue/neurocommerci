#!/usr/bin/env python3
"""Smoke checks for account readiness blockers."""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.runtime_readiness import account_blockers
from utils.session_topology import canonical_session_dir


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        account = SimpleNamespace(
            phone="+79990001122",
            session_file="79990001122.session",
            user_id=1,
            proxy_id=1,
            status="active",
            health_status="alive",
            lifecycle_stage="active_commenting",
        )
        session_dir = canonical_session_dir(base, 1, create=True)
        (session_dir / "79990001122.session").write_text("", encoding="utf-8")
        (session_dir / "79990001122.json").write_text(
            '{"phone":"+79990001122","session_file":"79990001122.session","app_id":2040,"app_hash":"0123456789abcdef0123456789abcdef","device":"Pixel 8","sdk":"Android 14","app_version":"12.4.3"}',
            encoding="utf-8",
        )

        ready = account_blockers(account, sessions_dir=base, strict_proxy=True)
        assert ready.ready, ready.blockers

        broken = SimpleNamespace(
            phone="+79990002233",
            session_file="79990002233.session",
            user_id=None,
            proxy_id=None,
            status="active",
            health_status="unknown",
            lifecycle_stage="uploaded",
        )
        report = account_blockers(broken, sessions_dir=base, strict_proxy=True)
        assert "no_owner" in report.blockers
        assert "no_proxy_binding" in report.blockers
        assert "stage_uploaded" in report.blockers

    print("readiness_test_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
