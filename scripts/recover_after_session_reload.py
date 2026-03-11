#!/usr/bin/env python3
"""One-shot recovery pipeline after replacing .session/.json files."""

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
from scripts.reconcile_accounts_with_sessions import reconcile
from scripts.session_auth_audit import audit_sessions
from storage.sqlite_db import dispose_engine, init_db
from utils.proxy_bindings import bind_accounts_to_proxies, sync_proxies_from_file


async def main() -> int:
    parser = argparse.ArgumentParser(description="Recover runtime after replacing session files")
    parser.add_argument("--user-id", type=int, default=None, help="Optional user_id scope")
    parser.add_argument(
        "--migrate-layout",
        action="store_true",
        help="Move flat/legacy session files into canonical data/sessions/<user_id>/ layout",
    )
    parser.add_argument(
        "--authorized-stage",
        default="auth_verified" if settings.HUMAN_GATED_PACKAGING else "active_commenting",
        help="Lifecycle stage to assign to authorized accounts",
    )
    parser.add_argument(
        "--authorized-status",
        default="active",
        help="Status to assign to authorized accounts",
    )
    parser.add_argument(
        "--authorized-health",
        default="alive",
        help="health_status to assign to authorized accounts",
    )
    parser.add_argument(
        "--set-parser-first-authorized",
        action="store_true",
        help="Reassign parser-only phone to an authorized account if needed",
    )
    parser.add_argument(
        "--clear-worker-claims",
        action="store_true",
        help="Clear stale Redis worker claims/heartbeats at the end of audit",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    await init_db()
    try:
        reconcile_report = await reconcile(
            user_id=args.user_id,
            dry_run=False,
            migrate_layout=bool(args.migrate_layout),
        )
        sync_report = await sync_proxies_from_file(settings.proxy_list_path, user_id=args.user_id)
        bind_report = await bind_accounts_to_proxies(user_id=args.user_id)
        audit_report = await audit_sessions(
            user_id=args.user_id,
            mark_unauthorized=True,
            reactivate_authorized=True,
            authorized_stage=args.authorized_stage,
            authorized_status=args.authorized_status,
            authorized_health=args.authorized_health,
            set_parser_first_authorized=bool(args.set_parser_first_authorized),
            clear_worker_claims=bool(args.clear_worker_claims),
            stage_actor="recover_after_session_reload",
        )
    finally:
        await dispose_engine()

    report = {
        "reconcile": reconcile_report,
        "proxy_sync": sync_report,
        "proxy_bind": bind_report,
        "session_auth_audit": audit_report,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
