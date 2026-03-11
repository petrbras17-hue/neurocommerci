#!/usr/bin/env python3
"""CLI дубль bot-first onboarding для одного аккаунта."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.ops_service import (
    apply_channel_draft_for_phone,
    apply_content_draft_for_phone,
    apply_profile_draft_for_phone,
    assign_account_role_for_phone,
    create_channel_draft_for_phone,
    create_content_draft_for_phone,
    create_profile_draft_for_phone,
    get_account_onboarding,
    record_account_onboarding_step,
    run_auth_refresh_for_phone,
    start_account_onboarding,
)
from storage.sqlite_db import dispose_engine, init_db


async def _cmd_start(args) -> dict:
    return await start_account_onboarding(
        args.phone,
        user_id=args.user_id,
        mode="cli",
        channel="cli",
        actor="cli_onboarding_start",
        notes=args.notes or "",
    )


async def _cmd_auth_check(args) -> dict:
    return await run_auth_refresh_for_phone(
        args.phone,
        user_id=args.user_id,
        actor="cli_auth_check",
        source="cli",
        channel="cli",
        notes=args.notes or "Проверка доступа из терминала.",
    )


async def _cmd_profile_draft(args) -> dict:
    return await create_profile_draft_for_phone(
        args.phone,
        user_id=args.user_id,
        actor="cli_profile_draft",
        source="cli",
        channel="cli",
        style=args.style,
        variant_count=args.variant_count,
    )


async def _cmd_profile_apply(args) -> dict:
    return await apply_profile_draft_for_phone(
        args.phone,
        user_id=args.user_id,
        draft_id=args.draft_id,
        selected_variant=args.selected_variant,
        actor="cli_profile_apply",
        source="cli",
        channel="cli",
    )


async def _cmd_channel_draft(args) -> dict:
    return await create_channel_draft_for_phone(
        args.phone,
        user_id=args.user_id,
        style=args.style,
        variant_count=args.variant_count,
        actor="cli_channel_draft",
        source="cli",
        channel="cli",
    )


async def _cmd_channel_apply(args) -> dict:
    return await apply_channel_draft_for_phone(
        args.phone,
        user_id=args.user_id,
        draft_id=args.draft_id,
        selected_variant=args.selected_variant,
        actor="cli_channel_apply",
        source="cli",
        channel="cli",
    )


async def _cmd_content_draft(args) -> dict:
    return await create_content_draft_for_phone(
        args.phone,
        user_id=args.user_id,
        variant_count=args.variant_count,
        actor="cli_content_draft",
        source="cli",
        channel="cli",
    )


async def _cmd_content_apply(args) -> dict:
    return await apply_content_draft_for_phone(
        args.phone,
        user_id=args.user_id,
        draft_id=args.draft_id,
        selected_variant=args.selected_variant,
        actor="cli_content_apply",
        source="cli",
        channel="cli",
    )


async def _cmd_assign_role(args) -> dict:
    return await assign_account_role_for_phone(
        args.phone,
        role=args.role,
        user_id=args.user_id,
        actor="cli_assign_role",
        source="cli",
        channel="cli",
    )


async def _cmd_step(args) -> dict:
    payload = None
    if args.payload:
        payload = json.loads(args.payload)
    return await record_account_onboarding_step(
        args.phone,
        user_id=args.user_id,
        step_key=args.step_key,
        actor=args.actor or "cli_step",
        source="cli",
        channel="cli",
        result=args.result,
        notes=args.notes or "",
        payload=payload,
        run_status=args.run_status,
    )


async def _cmd_status(args) -> dict:
    return await get_account_onboarding(
        args.phone,
        user_id=args.user_id,
        limit_steps=args.limit_steps,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one-account onboarding flow from terminal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start or resume onboarding run")
    start.add_argument("--phone", required=True)
    start.add_argument("--user-id", type=int, default=None)
    start.add_argument("--notes", default="")

    auth_check = subparsers.add_parser("auth-check", help="Run per-account auth refresh")
    auth_check.add_argument("--phone", required=True)
    auth_check.add_argument("--user-id", type=int, default=None)
    auth_check.add_argument("--notes", default="")

    profile_draft = subparsers.add_parser("profile-draft", help="Generate profile draft")
    profile_draft.add_argument("--phone", required=True)
    profile_draft.add_argument("--user-id", type=int, default=None)
    profile_draft.add_argument("--style", default=None)
    profile_draft.add_argument("--variant-count", type=int, default=3)

    package = subparsers.add_parser("package", help="Alias for profile-draft")
    package.add_argument("--phone", required=True)
    package.add_argument("--user-id", type=int, default=None)
    package.add_argument("--style", default=None)
    package.add_argument("--variant-count", type=int, default=3)

    profile_apply = subparsers.add_parser("profile-apply", help="Apply selected profile draft")
    profile_apply.add_argument("--phone", required=True)
    profile_apply.add_argument("--user-id", type=int, default=None)
    profile_apply.add_argument("--draft-id", type=int, default=None)
    profile_apply.add_argument("--selected-variant", type=int, default=None)

    channel_draft = subparsers.add_parser("channel-draft", help="Generate channel draft")
    channel_draft.add_argument("--phone", required=True)
    channel_draft.add_argument("--user-id", type=int, default=None)
    channel_draft.add_argument("--style", default=None)
    channel_draft.add_argument("--variant-count", type=int, default=3)

    channel_apply = subparsers.add_parser("channel-apply", help="Apply selected channel draft")
    channel_apply.add_argument("--phone", required=True)
    channel_apply.add_argument("--user-id", type=int, default=None)
    channel_apply.add_argument("--draft-id", type=int, default=None)
    channel_apply.add_argument("--selected-variant", type=int, default=None)

    content_draft = subparsers.add_parser("content-draft", help="Generate pinned post draft")
    content_draft.add_argument("--phone", required=True)
    content_draft.add_argument("--user-id", type=int, default=None)
    content_draft.add_argument("--variant-count", type=int, default=3)

    content_apply = subparsers.add_parser("content-apply", help="Apply selected pinned post draft")
    content_apply.add_argument("--phone", required=True)
    content_apply.add_argument("--user-id", type=int, default=None)
    content_apply.add_argument("--draft-id", type=int, default=None)
    content_apply.add_argument("--selected-variant", type=int, default=None)

    role = subparsers.add_parser("assign-role", help="Assign account role")
    role.add_argument("--phone", required=True)
    role.add_argument("--user-id", type=int, default=None)
    role.add_argument("--role", required=True)

    step = subparsers.add_parser("step", help="Record a manual onboarding step")
    step.add_argument("--phone", required=True)
    step.add_argument("--user-id", type=int, default=None)
    step.add_argument("--step-key", required=True)
    step.add_argument("--result", default="ok")
    step.add_argument("--notes", default="")
    step.add_argument("--actor", default="")
    step.add_argument("--run-status", default=None)
    step.add_argument("--payload", default="")

    status = subparsers.add_parser("status", help="Show onboarding snapshot")
    status.add_argument("--phone", required=True)
    status.add_argument("--user-id", type=int, default=None)
    status.add_argument("--limit-steps", type=int, default=10)

    return parser


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    await init_db()
    try:
        if args.command == "start":
            payload = await _cmd_start(args)
        elif args.command == "auth-check":
            payload = await _cmd_auth_check(args)
        elif args.command in {"profile-draft", "package"}:
            payload = await _cmd_profile_draft(args)
        elif args.command == "profile-apply":
            payload = await _cmd_profile_apply(args)
        elif args.command == "channel-draft":
            payload = await _cmd_channel_draft(args)
        elif args.command == "channel-apply":
            payload = await _cmd_channel_apply(args)
        elif args.command == "content-draft":
            payload = await _cmd_content_draft(args)
        elif args.command == "content-apply":
            payload = await _cmd_content_apply(args)
        elif args.command == "assign-role":
            payload = await _cmd_assign_role(args)
        elif args.command == "step":
            payload = await _cmd_step(args)
        elif args.command == "status":
            payload = await _cmd_status(args)
        else:
            raise ValueError(f"unsupported command: {args.command}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
