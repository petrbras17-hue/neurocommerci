#!/usr/bin/env python3
"""Repair stale active_commenting lifecycle against real auth state."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.account_audit import reconcile_stale_lifecycle
from storage.sqlite_db import dispose_engine, init_db


async def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile stale lifecycle against auth state")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    await init_db()
    payload = await reconcile_stale_lifecycle(
        user_id=args.user_id,
        actor="scripts_reconcile_lifecycle",
        dry_run=bool(args.dry_run),
    )
    await dispose_engine()

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
