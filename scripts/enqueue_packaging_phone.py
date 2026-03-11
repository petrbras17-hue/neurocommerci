#!/usr/bin/env python3
"""Enqueue legacy packaging task by phone.

Usage:
  python scripts/enqueue_packaging_phone.py --phone +79637411890
  python scripts/enqueue_packaging_phone.py --phone 79637411890 --user-id 1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from storage.sqlite_db import init_db, dispose_engine


def normalize_phone(raw_phone: str) -> str:
    digits = "".join(ch for ch in str(raw_phone) if ch.isdigit())
    if not digits:
        return ""
    return f"+{digits}"


async def enqueue_one(phone: str, user_id: int | None = None) -> dict:
    from core.ops_service import queue_packaging_phone

    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        raise ValueError("Invalid --phone value")

    await init_db()
    try:
        return await queue_packaging_phone(normalized_phone, user_id=user_id)
    finally:
        await dispose_engine()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Enqueue one phone for legacy packaging queue")
    parser.add_argument("--phone", required=True, help="Phone number to enqueue")
    parser.add_argument("--user-id", type=int, default=None, help="Optional owner user_id")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    try:
        report = await enqueue_one(phone=args.phone, user_id=args.user_id)
    except RuntimeError as exc:
        if str(exc) == "human_gated_packaging_enabled":
            print("human_gated_packaging_enabled: use profile-draft/profile-apply flow instead")
            return 2
        raise
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"enqueued phone={report['phone']} task_id={report['task_id']} "
            f"queue_size={report['queue_size']}"
        )
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
