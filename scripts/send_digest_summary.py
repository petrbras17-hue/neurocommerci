#!/usr/bin/env python3
"""Send the current discovery/channel summary to the digest bot."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.digest_service import send_daily_digest_summary
from storage.sqlite_db import dispose_engine, init_db


async def main() -> int:
    parser = argparse.ArgumentParser(description="Send digest summary to the configured digest bot")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    await init_db()
    try:
        payload = await send_daily_digest_summary(user_id=args.user_id)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
