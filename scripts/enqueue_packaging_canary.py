#!/usr/bin/env python3
"""Enqueue packaging tasks in controlled canary batches.

Examples:
  python scripts/enqueue_packaging_canary.py --count 1
  python scripts/enqueue_packaging_canary.py --count 5 --stages uploaded,packaging_error
  python scripts/enqueue_packaging_canary.py --count 10 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select, update

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from storage.models import Account
from storage.sqlite_db import async_session, init_db, dispose_engine


def parse_stages(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


async def enqueue_batch(
    count: int,
    stages: set[str],
    user_id: int | None = None,
    dry_run: bool = False,
) -> dict:
    from core.task_queue import task_queue

    await init_db()
    await task_queue.connect()

    queued = 0
    failed = 0
    selected: list[dict] = []
    queued_phones: list[str] = []

    try:
        async with async_session() as session:
            query = (
                select(Account)
                .where(Account.status != "banned")
                .where(Account.lifecycle_stage.in_(stages))
                .order_by(Account.created_at.asc())
                .limit(count)
            )
            if user_id is not None:
                query = query.where((Account.user_id == user_id) | (Account.user_id.is_(None)))

            result = await session.execute(query)
            accounts = list(result.scalars().all())

            for account in accounts:
                selected.append(
                    {
                        "phone": account.phone,
                        "user_id": account.user_id,
                        "lifecycle_stage": account.lifecycle_stage,
                    }
                )
                if dry_run:
                    continue
                payload = {
                    "phone": account.phone,
                    "user_id": account.user_id,
                    "session_file": account.session_file,
                }
                try:
                    await task_queue.enqueue("packaging:pending", payload)
                    queued += 1
                    queued_phones.append(account.phone)
                except Exception:
                    failed += 1

            if not dry_run and queued:
                await session.execute(
                    update(Account)
                    .where(Account.phone.in_(queued_phones))
                    .values(lifecycle_stage="packaging")
                )
                await session.commit()
    finally:
        try:
            pending_size = await task_queue.queue_size("packaging:pending")
        except Exception:
            pending_size = -1
        await task_queue.close()
        await dispose_engine()

    return {
        "requested_count": count,
        "selected": len(selected),
        "queued": queued,
        "failed": failed,
        "dry_run": dry_run,
        "user_id": user_id,
        "allowed_stages": sorted(stages),
        "packaging_queue_size": pending_size,
        "accounts": selected,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Enqueue packaging tasks for canary rollout")
    parser.add_argument("--count", type=int, required=True, help="How many accounts to enqueue")
    parser.add_argument(
        "--stages",
        default="uploaded,packaging_error,orphaned",
        help="Comma-separated lifecycle stages eligible for enqueue",
    )
    parser.add_argument("--user-id", type=int, default=None, help="Optional user scope")
    parser.add_argument("--dry-run", action="store_true", help="Preview without enqueue")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if args.count <= 0:
        raise SystemExit("--count must be > 0")

    report = await enqueue_batch(
        count=args.count,
        stages=parse_stages(args.stages),
        user_id=args.user_id,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Packaging canary enqueue")
        print(f"  requested_count: {report['requested_count']}")
        print(f"  selected: {report['selected']}")
        print(f"  queued: {report['queued']}")
        print(f"  failed: {report['failed']}")
        print(f"  dry_run: {report['dry_run']}")
        print(f"  allowed_stages: {report['allowed_stages']}")
        print(f"  packaging_queue_size: {report['packaging_queue_size']}")
        for item in report["accounts"]:
            print(f"    - {item['phone']} ({item['lifecycle_stage']})")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
