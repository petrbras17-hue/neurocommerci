#!/usr/bin/env python3
"""Diagnose parser readiness and optional stage1/stage2 run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from channels.discovery import ChannelDiscovery
from core.account_capabilities import is_frozen_error
from core.session_manager import SessionManager
from core.proxy_manager import ProxyManager
from core.rate_limiter import RateLimiter
from core.account_manager import AccountManager
from storage.models import Account
from storage.sqlite_db import async_session, init_db, dispose_engine


def normalize_phone(raw: str) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return f"+{digits}" if digits else ""


async def get_parser_account() -> dict:
    phone = normalize_phone(settings.PARSER_ONLY_PHONE)
    if not phone:
        return {"configured": False}

    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.phone == phone))
        account = result.scalar_one_or_none()

    if not account:
        return {"configured": True, "phone": phone, "exists": False}

    return {
        "configured": True,
        "phone": phone,
        "exists": True,
        "status": account.status,
        "health_status": account.health_status,
        "lifecycle_stage": account.lifecycle_stage,
        "risk_level": account.risk_level,
        "quarantined_until": account.quarantined_until.isoformat() if account.quarantined_until else None,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Parser diagnostics")
    parser.add_argument("--keywords", default="", help="Optional comma-separated stage1/stage2 check")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    await init_db()
    try:
        report = {
            "parser_only_phone": await get_parser_account(),
            "checks": {},
        }

        if args.keywords.strip():
            kws = [x.strip() for x in args.keywords.split(",") if x.strip()]
            session_mgr = SessionManager()
            proxy_mgr = ProxyManager()
            proxy_mgr.load_from_file()
            account_mgr = AccountManager(session_mgr, proxy_mgr, RateLimiter())
            discovery = ChannelDiscovery(session_mgr, account_mgr, proxy_mgr)

            try:
                found = await discovery.search_by_keywords(kws)
                report["checks"]["search_ok"] = True
                report["checks"]["search_status"] = "ok"
                report["checks"]["keywords"] = kws
                report["checks"]["stage2_count"] = len(found)
                report["checks"]["filter_stats"] = discovery.last_filter_stats
            except Exception as exc:
                report["checks"]["search_ok"] = False
                text = str(exc)
                status = "error"
                if "parser_not_configured" in text:
                    status = "parser_not_configured"
                elif "frozen_probe_failed" in text or is_frozen_error(text):
                    status = "frozen_probe_failed"
                elif "Policy blocked" in text or "TG-R00" in text:
                    status = "policy_blocked"
                elif "search_blocked_by_telegram" in text or "SearchRequest" in text:
                    status = "search_blocked_by_telegram"
                report["checks"]["search_status"] = status
                report["checks"]["error"] = str(exc)

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("Parser diagnose")
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
