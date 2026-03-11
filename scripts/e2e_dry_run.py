#!/usr/bin/env python3
"""Minimal end-to-end dry-run smoke scenario for CI."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("NEURO_DRY_RUN", "1")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from channels.monitor import ChannelMonitor
from comments.generator import CommentGenerator
from comments.poster import CommentPoster
from core.account_manager import AccountManager
from core.proxy_manager import ProxyManager
from core.rate_limiter import RateLimiter
from core.session_manager import SessionManager
from storage.sqlite_db import init_db, dispose_engine


async def _run() -> None:
    await init_db()

    session_mgr = SessionManager()
    proxy_mgr = ProxyManager()
    rate_limiter = RateLimiter()
    account_mgr = AccountManager(
        session_manager=session_mgr,
        proxy_manager=proxy_mgr,
        rate_limiter=rate_limiter,
    )
    monitor = ChannelMonitor(
        session_manager=session_mgr,
        account_manager=account_mgr,
        proxy_manager=proxy_mgr,
    )
    poster = CommentPoster(
        account_manager=account_mgr,
        session_manager=session_mgr,
        rate_limiter=rate_limiter,
        generator=CommentGenerator(),
        monitor=monitor,
    )

    await monitor.queue.add(
        {
            "post_db_id": 0,
            "channel_id": 0,
            "channel_telegram_id": 0,
            "channel_title": "smoke",
            "channel_topic": "vpn",
            "discussion_group_id": None,
            "telegram_post_id": 1,
            "text": "Нужен стабильный vpn для поездок и стриминга.",
        }
    )
    result = await poster.process_queue()
    if result not in {0, 1, -1}:
        raise RuntimeError(f"Unexpected process_queue result: {result}")

    print(f"e2e_dry_run_ok result={result} queue_size={monitor.queue.size}")


def main() -> int:
    try:
        asyncio.run(_run())
        return 0
    finally:
        try:
            asyncio.run(dispose_engine())
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
