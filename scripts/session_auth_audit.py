#!/usr/bin/env python3
"""Probe session authorization and optionally persist unauthorized status."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from core.redis_state import redis_state
from core.session_manager import SessionManager
from storage.models import Account, AccountStageEvent
from storage.sqlite_db import async_session, dispose_engine, init_db
from utils.env_file import update_env_file
from utils.helpers import utcnow
from utils.proxy_bindings import get_bound_proxy_config


async def audit_sessions(
    *,
    phone: str | None = None,
    user_id: int | None = None,
    status: str | None = None,
    lifecycle_stage: str | None = None,
    mark_unauthorized: bool = False,
    reactivate_authorized: bool = False,
    authorized_stage: str = "auth_verified" if settings.HUMAN_GATED_PACKAGING else "active_commenting",
    authorized_status: str = "active",
    authorized_health: str = "alive",
    set_parser_first_authorized: bool = False,
    clear_worker_claims: bool = False,
    stage_actor: str = "session_auth_audit",
) -> dict:
    mgr = SessionManager()
    async with async_session() as session:
        query = select(Account).order_by(Account.phone.asc())
        if phone:
            query = query.where(Account.phone == phone)
        if user_id is not None:
            query = query.where(Account.user_id == user_id)
        if status:
            query = query.where(Account.status == status)
        if lifecycle_stage:
            query = query.where(Account.lifecycle_stage == lifecycle_stage)
        result = await session.execute(query)
        accounts = list(result.scalars().all())

    results: list[dict[str, object]] = []
    authorized_phones: list[str] = []
    unauthorized_phones: list[str] = []
    for account in accounts:
        proxy = await get_bound_proxy_config(account.phone)
        ok, auth_status = await mgr.probe_authorization(
            account.phone,
            proxy=proxy,
            user_id=account.user_id,
        )
        results.append(
            {
                "phone": account.phone,
                "user_id": account.user_id,
                "account_status": account.status,
                "health_status": account.health_status,
                "lifecycle_stage": account.lifecycle_stage,
                "authorized": ok,
                "probe_status": auth_status,
            }
        )
        if ok:
            authorized_phones.append(account.phone)
        if auth_status in {"unauthorized", "auth_key_unregistered"}:
            unauthorized_phones.append(account.phone)

    updated_unauthorized = 0
    reactivated = 0
    parser_phone_before = str(settings.PARSER_ONLY_PHONE or "").strip()
    parser_phone_after = parser_phone_before

    if mark_unauthorized and unauthorized_phones:
        async with async_session() as session:
            result = await session.execute(
                select(Account).where(Account.phone.in_(unauthorized_phones))
            )
            rows = list(result.scalars().all())
            now = utcnow()
            for account in rows:
                account.status = "error"
                account.health_status = "expired"
                account.restriction_reason = "unauthorized"
                account.last_health_check = now
                updated_unauthorized += 1
            await session.commit()

    if reactivate_authorized and authorized_phones:
        async with async_session() as session:
            result = await session.execute(
                select(Account).where(Account.phone.in_(authorized_phones))
            )
            rows = list(result.scalars().all())
            now = utcnow()
            for account in rows:
                old_stage = account.lifecycle_stage
                account.status = authorized_status
                account.health_status = authorized_health
                account.restriction_reason = None
                account.last_health_check = now
                account.last_active_at = now
                if authorized_stage:
                    account.lifecycle_stage = authorized_stage
                if authorized_stage and old_stage != account.lifecycle_stage:
                    session.add(
                        AccountStageEvent(
                            account_id=account.id,
                            phone=account.phone,
                            from_stage=old_stage,
                            to_stage=account.lifecycle_stage,
                            actor=stage_actor,
                            reason="reactivated after session authorization audit",
                        )
                    )
                reactivated += 1
            await session.commit()

    parser_reassigned = False
    if set_parser_first_authorized and authorized_phones:
        preferred_phone = parser_phone_before if parser_phone_before in authorized_phones else sorted(authorized_phones)[0]
        if preferred_phone and preferred_phone != parser_phone_before:
            update_env_file("PARSER_ONLY_PHONE", preferred_phone)
            settings.PARSER_ONLY_PHONE = preferred_phone
            parser_phone_after = preferred_phone
            parser_reassigned = True
        elif preferred_phone:
            parser_phone_after = preferred_phone

    claims_cleared = 0
    heartbeats_cleared = 0
    if clear_worker_claims:
        await redis_state.connect()
        try:
            claims_cleared = await redis_state.clear_all_claims()
            heartbeats_cleared = await redis_state.clear_worker_heartbeats()
        finally:
            await redis_state.close()

    return {
        "count": len(results),
        "authorized": sum(1 for item in results if bool(item["authorized"])),
        "unauthorized_like": len(unauthorized_phones),
        "updated_unauthorized": updated_unauthorized,
        "reactivated_authorized": reactivated,
        "parser_phone_before": parser_phone_before,
        "parser_phone_after": parser_phone_after,
        "parser_reassigned": parser_reassigned,
        "claims_cleared": claims_cleared,
        "worker_heartbeats_cleared": heartbeats_cleared,
        "results": results,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Audit session authorization")
    parser.add_argument("--phone", default=None, help="Filter by exact phone")
    parser.add_argument("--user-id", type=int, default=None, help="Filter by user id")
    parser.add_argument("--status", default=None, help="Filter by account.status")
    parser.add_argument("--lifecycle-stage", default=None, help="Filter by lifecycle stage")
    parser.add_argument(
        "--mark-unauthorized",
        action="store_true",
        help="Persist unauthorized/auth_key_unregistered accounts as status=error health_status=expired",
    )
    parser.add_argument(
        "--reactivate-authorized",
        action="store_true",
        help="Persist authorized accounts as active/alive and optionally move them to target lifecycle stage",
    )
    parser.add_argument(
        "--authorized-stage",
        default="auth_verified" if settings.HUMAN_GATED_PACKAGING else "active_commenting",
        help="Lifecycle stage to assign to authorized accounts when --reactivate-authorized is used",
    )
    parser.add_argument(
        "--authorized-status",
        default="active",
        help="Status to assign to authorized accounts when --reactivate-authorized is used",
    )
    parser.add_argument(
        "--authorized-health",
        default="alive",
        help="health_status to assign to authorized accounts when --reactivate-authorized is used",
    )
    parser.add_argument(
        "--set-parser-first-authorized",
        action="store_true",
        help="If current PARSER_ONLY_PHONE is not authorized, move parser assignment to the first authorized phone",
    )
    parser.add_argument(
        "--clear-worker-claims",
        action="store_true",
        help="Clear Redis worker claims and worker heartbeat map after status updates",
    )
    parser.add_argument(
        "--stage-actor",
        default="session_auth_audit",
        help="Actor value for lifecycle stage events",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    await init_db()
    try:
        report = await audit_sessions(
            phone=args.phone,
            user_id=args.user_id,
            status=args.status,
            lifecycle_stage=args.lifecycle_stage,
            mark_unauthorized=bool(args.mark_unauthorized),
            reactivate_authorized=bool(args.reactivate_authorized),
            authorized_stage=args.authorized_stage,
            authorized_status=args.authorized_status,
            authorized_health=args.authorized_health,
            set_parser_first_authorized=bool(args.set_parser_first_authorized),
            clear_worker_claims=bool(args.clear_worker_claims),
            stage_actor=args.stage_actor,
        )

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
