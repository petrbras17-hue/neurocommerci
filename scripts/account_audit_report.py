#!/usr/bin/env python3
"""Account/json/session/proxy audit report."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.account_audit import (
    collect_account_audit,
    collect_json_credentials_audit,
    collect_proxy_observability,
    collect_session_topology_audit,
)
from storage.sqlite_db import dispose_engine, init_db


async def main() -> int:
    parser = argparse.ArgumentParser(description="Account audit report")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    await init_db()
    account_audit = await collect_account_audit(user_id=args.user_id)
    json_audit = await collect_json_credentials_audit(user_id=args.user_id)
    session_audit = await collect_session_topology_audit(user_id=args.user_id)
    proxy_audit = await collect_proxy_observability(user_id=args.user_id)
    await dispose_engine()

    payload = {
        "accounts": account_audit,
        "json_credentials": json_audit,
        "sessions": session_audit,
        "proxies": proxy_audit,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("Account audit")
    print(json.dumps(account_audit["summary"], ensure_ascii=False, indent=2))
    print("\nJSON credentials")
    print(json.dumps(json_audit["summary"], ensure_ascii=False, indent=2))
    print("\nSession topology")
    print(json.dumps(session_audit["summary"], ensure_ascii=False, indent=2))
    print("\nProxy observability")
    print(json.dumps(proxy_audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
