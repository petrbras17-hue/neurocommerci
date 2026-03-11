#!/usr/bin/env python3
"""Archive current user-state and reset accounts/channels/tasks to a clean slate."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.reset_service import reset_user_state
from storage.sqlite_db import dispose_engine, init_db


async def main() -> int:
    parser = argparse.ArgumentParser(description="Reset account/channel/task state while preserving archives")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Preview reset without deleting data")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    await init_db()
    try:
        payload = await reset_user_state(
            user_id=args.user_id,
            actor="scripts_reset_user_state",
            dry_run=bool(args.dry_run),
        )
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
