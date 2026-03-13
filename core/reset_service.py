"""Clean-slate reset for account/channels/tasks state while preserving archives."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from config import BASE_DIR, settings
from core.account_audit import (
    collect_account_audit,
    collect_json_credentials_audit,
    collect_proxy_observability,
    collect_session_topology_audit,
)
from core.redis_state import redis_state
from core.task_queue import task_queue
from storage.models import (
    Account,
    AccountOnboardingRun,
    AccountOnboardingStep,
    AccountRiskState,
    AccountStageEvent,
    Channel,
    Comment,
    PolicyEvent,
    Post,
)
from storage.sqlite_db import async_session
from utils.env_file import update_env_file
from utils.helpers import utcnow
from utils.session_topology import canonical_session_paths


RESET_QUEUES = ("comment_tasks", "packaging_tasks", "recovery_tasks", "channel_discovery", "post_candidates")


def _reset_archive_root() -> Path:
    path = BASE_DIR / "data" / "reset_archives"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _archive_file_path(timestamp: str) -> Path:
    archive_dir = _reset_archive_root() / timestamp
    archive_dir.mkdir(parents=True, exist_ok=True)
    return archive_dir / "snapshot.json"


def _session_archive_root(timestamp: str) -> Path:
    path = settings.sessions_path / "_quarantine" / f"reset-{timestamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _load_reset_scope(user_id: int | None = None) -> dict[str, Any]:
    async with async_session() as session:
        account_query = select(Account).order_by(Account.created_at.asc(), Account.id.asc()).limit(10000)
        channel_query = select(Channel).order_by(Channel.created_at.asc(), Channel.id.asc()).limit(10000)
        if user_id is not None:
            account_query = account_query.where(Account.user_id == user_id)
            channel_query = channel_query.where(Channel.user_id == user_id)
        accounts = list((await session.execute(account_query)).scalars().all())
        channels = list((await session.execute(channel_query)).scalars().all())
        account_ids = [int(account.id) for account in accounts]
        phones = [str(account.phone) for account in accounts]
        channel_ids = [int(channel.id) for channel in channels]

        stage_events = []
        onboarding_runs = []
        onboarding_steps = []
        policy_events = []
        if account_ids:
            stage_events = list(
                (
                    await session.execute(
                        select(AccountStageEvent).where(AccountStageEvent.account_id.in_(account_ids)).limit(50000)
                    )
                ).scalars().all()
            )
            onboarding_runs = list(
                (
                    await session.execute(
                        select(AccountOnboardingRun).where(AccountOnboardingRun.account_id.in_(account_ids)).limit(50000)
                    )
                ).scalars().all()
            )
            onboarding_steps = list(
                (
                    await session.execute(
                        select(AccountOnboardingStep).where(AccountOnboardingStep.account_id.in_(account_ids)).limit(50000)
                    )
                ).scalars().all()
            )
            policy_events = list(
                (
                    await session.execute(
                        select(PolicyEvent).where(
                            (PolicyEvent.account_id.in_(account_ids)) | (PolicyEvent.phone.in_(phones))
                        ).limit(50000)
                    )
                ).scalars().all()
            )

    return {
        "accounts": accounts,
        "channels": channels,
        "account_ids": account_ids,
        "phones": phones,
        "channel_ids": channel_ids,
        "stage_events": stage_events,
        "onboarding_runs": onboarding_runs,
        "onboarding_steps": onboarding_steps,
        "policy_events": policy_events,
    }


def _serialize_rows(rows: list[Any], *, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for field in fields:
            value = getattr(row, field, None)
            item[field] = value.isoformat() if hasattr(value, "isoformat") and value is not None else value
        serialized.append(item)
    return serialized


async def _write_reset_archive(timestamp: str, *, user_id: int | None, scope: dict[str, Any]) -> Path:
    archive = {
        "created_at": utcnow().isoformat(),
        "user_id": user_id,
        "account_audit": await collect_account_audit(user_id=user_id),
        "json_credentials": await collect_json_credentials_audit(user_id=user_id),
        "session_topology": await collect_session_topology_audit(user_id=user_id),
        "proxy_observability": await collect_proxy_observability(user_id=user_id),
        "history": {
            "stage_events": _serialize_rows(
                scope["stage_events"],
                fields=("account_id", "phone", "from_stage", "to_stage", "actor", "reason", "created_at"),
            ),
            "onboarding_runs": _serialize_rows(
                scope["onboarding_runs"],
                fields=("account_id", "user_id", "phone", "status", "mode", "current_step", "last_result", "updated_at"),
            ),
            "onboarding_steps": _serialize_rows(
                scope["onboarding_steps"],
                fields=("run_id", "account_id", "phone", "step_key", "actor", "source", "result", "created_at"),
            ),
            "policy_events": _serialize_rows(
                scope["policy_events"],
                fields=("account_id", "phone", "rule_id", "event_name", "decision", "severity", "created_at"),
            ),
        },
    }
    path = _archive_file_path(timestamp)
    path.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _move_canonical_sessions_to_archive(timestamp: str, *, accounts: list[Account], dry_run: bool) -> dict[str, Any]:
    archive_root = _session_archive_root(timestamp)
    moved_files = 0
    moved_phones: list[str] = []
    for account in accounts:
        if account.user_id is None:
            continue
        session_path, metadata_path = canonical_session_paths(settings.sessions_path, int(account.user_id), account.phone)
        target_dir = archive_root / str(int(account.user_id))
        target_dir.mkdir(parents=True, exist_ok=True)
        moved_this_phone = False
        for source in (session_path, metadata_path):
            if not source.exists():
                continue
            target = target_dir / source.name
            if dry_run:
                moved_files += 1
                moved_this_phone = True
                continue
            if target.exists():
                target.unlink()
            shutil.move(str(source), str(target))
            moved_files += 1
            moved_this_phone = True
        if moved_this_phone:
            moved_phones.append(str(account.phone))
    return {
        "moved_files": moved_files,
        "moved_phones": sorted(set(moved_phones)),
        "quarantine_dir": str(archive_root),
    }


async def _clear_runtime_state() -> dict[str, Any]:
    try:
        await task_queue.connect()
        await redis_state.connect()
        queue_cleanup = {}
        for queue_name in RESET_QUEUES:
            queue_cleanup[queue_name] = await task_queue.purge_queue(queue_name)
        claims_cleared = await redis_state.clear_all_claims()
        heartbeats_cleared = await redis_state.clear_worker_heartbeats()
        rate_state = await redis_state.clear_hash("rate_state")
        health_state = await redis_state.clear_hash("health_status")
        parser_tasks = await redis_state.clear_hash("parser_tasks")
        recovery_tasks = await redis_state.clear_hash("recovery_tasks")
        comments_today = await redis_state.delete_pattern("comments_today:*")
        runtime_flags = await redis_state.delete_pattern("runtime_flag:*")
        await redis_state.set_runtime_flag("autopilot_enabled", "0")
        return {
            "queues": queue_cleanup,
            "claims_cleared": claims_cleared,
            "worker_heartbeats_cleared": heartbeats_cleared,
            "rate_state_cleared": rate_state,
            "health_state_cleared": health_state,
            "parser_tasks_cleared": parser_tasks,
            "recovery_tasks_cleared": recovery_tasks,
            "comments_today_cleared": comments_today,
            "runtime_flags_cleared": runtime_flags,
            "autopilot_disabled": True,
        }
    except Exception as exc:
        return {
            "queues": {},
            "autopilot_disabled": False,
            "error": f"runtime_cleanup_unavailable:{exc.__class__.__name__}",
        }


async def reset_user_state(
    *,
    user_id: int | None = None,
    actor: str = "reset_user_state",
    dry_run: bool = False,
) -> dict[str, Any]:
    timestamp = utcnow().strftime("%Y%m%d-%H%M%S")
    scope = await _load_reset_scope(user_id=user_id)
    archive_path = await _write_reset_archive(timestamp, user_id=user_id, scope=scope)
    sessions_report = _move_canonical_sessions_to_archive(
        timestamp,
        accounts=list(scope["accounts"]),
        dry_run=dry_run,
    )

    deleted_counts = {
        "accounts": len(scope["account_ids"]),
        "channels": len(scope["channel_ids"]),
        "stage_events": len(scope["stage_events"]),
        "onboarding_runs": len(scope["onboarding_runs"]),
        "onboarding_steps": len(scope["onboarding_steps"]),
        "policy_events": len(scope["policy_events"]),
    }
    runtime_report = {}

    if not dry_run:
        async with async_session() as session:
            if scope["channel_ids"]:
                await session.execute(delete(Comment).where(Comment.post_id.in_(select(Post.id).where(Post.channel_id.in_(scope["channel_ids"])))))
                await session.execute(delete(Post).where(Post.channel_id.in_(scope["channel_ids"])))
                await session.execute(delete(Channel).where(Channel.id.in_(scope["channel_ids"])))
            if scope["account_ids"]:
                await session.execute(delete(Comment).where(Comment.account_id.in_(scope["account_ids"])))
                await session.execute(delete(AccountRiskState).where(AccountRiskState.account_id.in_(scope["account_ids"])))
                await session.execute(delete(AccountOnboardingStep).where(AccountOnboardingStep.account_id.in_(scope["account_ids"])))
                await session.execute(delete(AccountOnboardingRun).where(AccountOnboardingRun.account_id.in_(scope["account_ids"])))
                await session.execute(delete(AccountStageEvent).where(AccountStageEvent.account_id.in_(scope["account_ids"])))
                await session.execute(
                    delete(PolicyEvent).where(
                        (PolicyEvent.account_id.in_(scope["account_ids"])) | (PolicyEvent.phone.in_(scope["phones"]))
                    )
                )
                await session.execute(delete(Account).where(Account.id.in_(scope["account_ids"])))
            await session.commit()

        runtime_report = await _clear_runtime_state()
        try:
            update_env_file("PARSER_ONLY_PHONE", "")
        except FileNotFoundError:
            pass
        settings.PARSER_ONLY_PHONE = ""

    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "actor": actor,
        "user_id": user_id,
        "archive_path": str(archive_path),
        "session_archive": sessions_report,
        "deleted": deleted_counts,
        "runtime": runtime_report,
    }
