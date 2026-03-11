#!/usr/bin/env python3
"""Promote account lifecycle stages in bulk."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.sqlite_db import dispose_engine, init_db
from utils.proxy_bindings import promote_accounts


async def main() -> int:
    parser = argparse.ArgumentParser(description="Promote account lifecycle stage")
    parser.add_argument("--user-id", type=int, default=None, help="Target user id")
    parser.add_argument(
        "--from-stage",
        action="append",
        dest="from_stages",
        default=[],
        help="Only promote accounts currently in this stage. Repeatable.",
    )
    parser.add_argument("--to-stage", required=True, help="Destination lifecycle stage")
    parser.add_argument("--phone", action="append", dest="phones", default=[], help="Target phone")
    parser.add_argument("--status", default=None, help="Optional status override")
    parser.add_argument("--health-status", default=None, help="Optional health_status override")
    parser.add_argument(
        "--allow-status-override",
        action="store_true",
        help="Ignore status_* blockers if --status is provided",
    )
    parser.add_argument(
        "--allow-health-override",
        action="store_true",
        help="Ignore health_* blockers if --health-status is provided",
    )
    parser.add_argument("--actor", default="script:lifecycle_promote", help="Stage event actor")
    parser.add_argument("--reason", default="bulk lifecycle promotion", help="Stage event reason")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    await init_db()
    try:
        report = await promote_accounts(
            user_id=args.user_id,
            from_stages=args.from_stages,
            to_stage=args.to_stage,
            phones=args.phones,
            status=args.status,
            health_status=args.health_status,
            actor=args.actor,
            reason=args.reason,
            allow_status_override=bool(args.allow_status_override),
            allow_health_override=bool(args.allow_health_override),
        )
    finally:
        await dispose_engine()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
