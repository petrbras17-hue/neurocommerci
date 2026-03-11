#!/usr/bin/env python3
"""Set parser-only account phone in .env with validation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from storage.models import Account
from storage.sqlite_db import async_session, init_db, dispose_engine
from utils.env_file import update_env_file


def normalize_phone(raw: str) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return f"+{digits}" if digits else ""


async def main() -> int:
    parser = argparse.ArgumentParser(description="Set parser-only account")
    parser.add_argument("--phone", required=True, help="Phone number")
    parser.add_argument("--force", action="store_true", help="Allow setting even when account looks unhealthy")
    args = parser.parse_args()

    phone = normalize_phone(args.phone)
    if not phone:
        raise SystemExit("invalid --phone")

    await init_db()
    try:
        async with async_session() as session:
            result = await session.execute(select(Account).where(Account.phone == phone))
            account = result.scalar_one_or_none()
        if account is None:
            raise SystemExit(f"account not found: {phone}")

        unhealthy = account.health_status in {"dead", "restricted"} or account.lifecycle_stage == "restricted"
        if unhealthy and not args.force:
            raise SystemExit(
                f"account {phone} is unhealthy (health={account.health_status}, lifecycle={account.lifecycle_stage}); "
                "use --force to set anyway"
            )

        update_env_file("PARSER_ONLY_PHONE", phone)
        print(
            f"parser_only_phone={phone} status={account.status} "
            f"health={account.health_status} lifecycle={account.lifecycle_stage}"
        )
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
