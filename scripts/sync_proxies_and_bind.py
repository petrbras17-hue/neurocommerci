#!/usr/bin/env python3
"""Sync proxies from file into DB and bind them to accounts."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from storage.sqlite_db import dispose_engine, init_db
from utils.proxy_bindings import bind_accounts_to_proxies, sync_proxies_from_file


async def main() -> int:
    parser = argparse.ArgumentParser(description="Sync proxies into DB and bind accounts")
    parser.add_argument("--user-id", type=int, default=None, help="Target user id")
    parser.add_argument("--path", default=str(settings.proxy_list_path), help="Proxy list file")
    parser.add_argument("--rebind", action="store_true", help="Reassign already bound accounts")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    await init_db()
    try:
        sync_report = await sync_proxies_from_file(Path(args.path), user_id=args.user_id)
        bind_report = await bind_accounts_to_proxies(user_id=args.user_id, rebind=bool(args.rebind))
    finally:
        await dispose_engine()

    report = {
        "sync": sync_report,
        "bind": bind_report,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Proxy sync")
        print(json.dumps(sync_report, ensure_ascii=False, indent=2))
        print("\nProxy bind")
        print(json.dumps(bind_report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
