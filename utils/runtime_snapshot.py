"""Shared runtime snapshot for distributed status screens and scripts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from config import settings
from core.account_audit import build_account_audit_records, build_json_credentials_audit, summarize_account_audit
from storage.models import Account, AccountRiskState, Channel, PolicyEvent, User
from storage.sqlite_db import async_session, dispose_engine, init_db
from utils.proxy_bindings import get_proxy_pool_summary
from utils.runtime_readiness import account_blockers, summarize_account_blockers
from utils.session_topology import discover_session_assets


async def _account_status_counts() -> dict[str, int]:
    async with async_session() as session:
        result = await session.execute(select(Account.status, func.count()).group_by(Account.status))
        return {str(status): int(count) for status, count in result.all()}


async def _account_lifecycle_counts() -> dict[str, int]:
    async with async_session() as session:
        result = await session.execute(
            select(Account.lifecycle_stage, func.count()).group_by(Account.lifecycle_stage)
        )
        return {str(stage or "unknown"): int(count) for stage, count in result.all()}


async def _account_total() -> int:
    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(Account))
        return int(result.scalar_one())


async def _accounts() -> list[Account]:
    async with async_session() as session:
        result = await session.execute(select(Account).order_by(Account.created_at.asc()))
        return list(result.scalars().all())


async def _channel_review_counts() -> dict[str, int]:
    async with async_session() as session:
        result = await session.execute(
            select(Channel.review_state, func.count()).group_by(Channel.review_state)
        )
        return {str(state or "unknown"): int(count) for state, count in result.all()}


async def _channel_publish_counts() -> dict[str, int]:
    async with async_session() as session:
        result = await session.execute(
            select(Channel.publish_mode, func.count()).group_by(Channel.publish_mode)
        )
        return {str(mode or "unknown"): int(count) for mode, count in result.all()}


async def _channel_publishable_count() -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count())
            .select_from(Channel)
            .where(
                Channel.is_active.is_(True),
                Channel.is_blacklisted.is_(False),
                Channel.review_state == "approved",
                Channel.publish_mode == "auto_allowed",
            )
        )
        return int(result.scalar_one() or 0)


async def _user_ids() -> list[int]:
    async with async_session() as session:
        result = await session.execute(select(User.id).order_by(User.id))
        return [int(row[0]) for row in result.fetchall()]


async def _policy_counts(window_days: int) -> dict[str, int]:
    since = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) - timedelta(
        days=max(1, window_days)
    )
    async with async_session() as session:
        result = await session.execute(
            select(PolicyEvent.decision, func.count())
            .where(PolicyEvent.created_at >= since)
            .group_by(PolicyEvent.decision)
        )
        return {str(decision): int(count) for decision, count in result.all()}


async def _quarantine_stats() -> dict[str, int]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as session:
        active_quarantine = await session.scalar(
            select(func.count(Account.id)).where(
                Account.quarantined_until.is_not(None),
                Account.quarantined_until > now,
            )
        )
        restricted = await session.scalar(
            select(func.count(Account.id)).where(Account.lifecycle_stage == "restricted")
        )
        high_risk = await session.scalar(
            select(func.count(AccountRiskState.id)).where(
                AccountRiskState.risk_level.in_(["high", "critical"])
            )
        )
    return {
        "quarantined_active": int(active_quarantine or 0),
        "restricted_accounts": int(restricted or 0),
        "high_or_critical_risk": int(high_risk or 0),
    }


async def _violations_24h() -> int:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    async with async_session() as session:
        result = await session.execute(
            select(func.count())
            .select_from(PolicyEvent)
            .where(
                PolicyEvent.created_at >= since,
                PolicyEvent.decision.in_(["warn", "block", "quarantine"]),
            )
        )
        return int(result.scalar_one() or 0)


async def _frozen_accounts_count() -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(Account).where(Account.health_status == "frozen")
        )
        return int(result.scalar_one() or 0)


def _normalize_phone(raw: str) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return f"+{digits}" if digits else ""


async def _parser_health() -> dict:
    phone = _normalize_phone(settings.PARSER_ONLY_PHONE)
    if not phone:
        return {"configured": False}
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.phone == phone))
        account = result.scalar_one_or_none()
    if account is None:
        return {"configured": True, "phone": phone, "exists": False}
    blocked = (
        account.status != "active"
        or account.health_status in {"dead", "restricted", "frozen", "expired"}
        or account.lifecycle_stage in {"restricted", "expired", "packaging_error"}
        or str(getattr(account, "account_role", "") or "") == "needs_attention"
    )
    return {
        "configured": True,
        "phone": phone,
        "exists": True,
        "status": account.status,
        "health_status": account.health_status,
        "lifecycle_stage": account.lifecycle_stage,
        "blocked": bool(blocked),
    }


async def collect_runtime_snapshot(
    *,
    initialize_db: bool = True,
    close_backends: bool = False,
    dispose_db: bool = False,
) -> dict:
    from core.redis_state import redis_state
    from core.task_queue import task_queue

    if initialize_db:
        await init_db()

    redis_available = False
    redis_error = ""
    workers: dict[str, dict] = {}
    claims_total = 0
    claims_by_worker: dict[str, int] = {}
    claims_map: dict[str, str] = {}
    comments_queue: dict = {"available": False}
    packaging_queue: dict = {"available": False}
    recovery_queue: dict = {"available": False}
    discovery_queue: dict = {"available": False}
    autopilot_enabled = False

    try:
        try:
            await task_queue.connect()
            await redis_state.connect()
            redis_available = True
            workers = await redis_state.get_active_workers()
            claims_total = await redis_state.count_claims()
            claims_by_worker = await redis_state.claims_by_worker()
            claims_map = await redis_state.claims_map()
            comments_queue = await task_queue.queue_sizes("comment_tasks")
            packaging_queue = await task_queue.queue_sizes("packaging_tasks")
            recovery_queue = await task_queue.queue_sizes("recovery_tasks")
            discovery_queue = await task_queue.queue_sizes("channel_discovery")
            autopilot_enabled = await redis_state.get_runtime_flag("autopilot_enabled", "0") == "1"
        except Exception as exc:
            redis_error = str(exc)
            comments_queue = {"available": False, "error": redis_error}
            packaging_queue = {"available": False, "error": redis_error}
            recovery_queue = {"available": False, "error": redis_error}
            discovery_queue = {"available": False, "error": redis_error}

        account_total = await _account_total()
        accounts = await _accounts()
        status_counts = await _account_status_counts()
        lifecycle_counts = await _account_lifecycle_counts()
        channel_review_counts = await _channel_review_counts()
        channel_publish_counts = await _channel_publish_counts()
        channel_publishable = await _channel_publishable_count()
        policy_counts = await _policy_counts(settings.STRICT_SLO_WINDOW_DAYS)
        quarantine = await _quarantine_stats()
        violations_24h = await _violations_24h()
        frozen_accounts_count = await _frozen_accounts_count()
        parser_health = await _parser_health()
        user_ids = await _user_ids()
        proxy_pool = await get_proxy_pool_summary()
    finally:
        if close_backends:
            await redis_state.close()
            await task_queue.close()
        if dispose_db:
            await dispose_engine()

    connected_total = 0
    dequeue_errors = 0
    queue_empty_loops = 0
    worker_metrics: dict[str, dict] = {}

    for worker_id, info in workers.items():
        connected_total += int(info.get("accounts", 0))
        metrics = info.get("metrics") or {}
        dequeue_errors += int(metrics.get("dequeue_errors", 0))
        queue_empty_loops += int(metrics.get("queue_empty_loops", 0))
        worker_metrics[worker_id] = {
            "accounts": int(info.get("accounts", 0)),
            "last_seen_unix": float(info.get("last_seen", 0)),
            "metrics": metrics,
        }

    pinning_issues: list[dict[str, str]] = []
    if account_total > 0:
        for worker_id, info in worker_metrics.items():
            if not (worker_id.startswith("worker-A") or worker_id.startswith("worker-B")):
                continue
            pinned_phone = str((info.get("metrics") or {}).get("pinned_phone") or "").strip()
            claimed = [phone for phone, owner in claims_map.items() if owner == worker_id]
            if not pinned_phone:
                pinning_issues.append({"worker": worker_id, "issue": "missing_pinned_phone"})
                continue
            if claimed and any(phone != pinned_phone for phone in claimed):
                pinning_issues.append(
                    {"worker": worker_id, "issue": "claimed_phone_mismatch", "expected": pinned_phone}
                )
            claim_blocker = str((info.get("metrics") or {}).get("claim_blocker") or "").strip()
            if claim_blocker:
                pinning_issues.append(
                    {"worker": worker_id, "issue": "claim_blocker", "expected": claim_blocker}
                )

    quarantine_rate = (float(quarantine["quarantined_active"]) / float(account_total)) if account_total else 0.0
    discovery = discover_session_assets(settings.sessions_path, known_user_ids=user_ids)
    account_audit = build_account_audit_records(
        accounts,
        sessions_dir=settings.sessions_path,
        strict_proxy=bool(settings.STRICT_PROXY_PER_ACCOUNT),
    )
    credentials_audit = build_json_credentials_audit(accounts, sessions_dir=settings.sessions_path)
    blocker_summary = summarize_account_blockers(
        accounts,
        sessions_dir=settings.sessions_path,
        strict_proxy=bool(settings.STRICT_PROXY_PER_ACCOUNT),
    )
    readiness_preview = []
    for account in accounts[:20]:
        readiness = account_blockers(
            account,
            sessions_dir=settings.sessions_path,
            strict_proxy=bool(settings.STRICT_PROXY_PER_ACCOUNT),
        )
        readiness_preview.append(
            {
                "phone": account.phone,
                "user_id": account.user_id,
                "status": account.status,
                "health_status": account.health_status,
                "lifecycle_stage": account.lifecycle_stage,
                "primary_blocker": readiness.primary,
                "blockers": readiness.blockers,
            }
        )
    parser_phone = str((parser_health or {}).get("phone") or "")
    parser_claim_conflict = bool(parser_phone and claims_map.get(parser_phone))

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "workers": {
            "redis_available": redis_available,
            "redis_error": redis_error,
            "active": len(workers),
            "connected_total": connected_total,
            "claims_total": claims_total,
            "claims_by_worker": claims_by_worker,
            "claims_map": claims_map,
            "dequeue_errors_total": dequeue_errors,
            "queue_empty_loops_total": queue_empty_loops,
            "details": worker_metrics,
            "pinning_ok": len(pinning_issues) == 0,
            "claim_conflicts": pinning_issues,
        },
        "queues": {
            "comments": comments_queue,
            "comment_tasks": comments_queue,
            "packaging_pending": packaging_queue,
            "packaging_tasks": packaging_queue,
            "recovery_tasks": recovery_queue,
            "channel_discovery": discovery_queue,
        },
        "autopilot": {
            "enabled": bool(autopilot_enabled),
        },
        "accounts": {
            "total": account_total,
            "status": status_counts,
            "lifecycle": lifecycle_counts,
            "audit": summarize_account_audit(account_audit),
            "blockers": blocker_summary,
            "readiness_preview": readiness_preview,
        },
        "sessions": {
            "discovered_total": len(discovery.assets),
            "duplicates": len(discovery.duplicates),
            "duplicate_phones": sorted(discovery.duplicates.keys()),
            "unowned_accounts": sum(1 for acc in accounts if acc.user_id is None),
        },
        "json_credentials": credentials_audit["summary"],
        "channels": {
            "review_state": channel_review_counts,
            "publish_mode": channel_publish_counts,
            "publishable": channel_publishable,
        },
        "proxies": proxy_pool,
        "policy": {
            "window_days": settings.STRICT_SLO_WINDOW_DAYS,
            "decisions": policy_counts,
            "violations_24h": violations_24h,
            "quarantine": quarantine,
            "quarantine_rate": round(quarantine_rate, 4),
            "frozen_accounts_count": frozen_accounts_count,
            "frozen_detected": frozen_accounts_count > 0,
            "parser_health": parser_health,
            "parser_claim_conflict": parser_claim_conflict,
        },
    }


def print_runtime_snapshot(report: dict):
    print(f"Runtime status @ {report['timestamp_utc']}")
    workers = report["workers"]
    queues = report["queues"]
    accounts = report["accounts"]

    print("\nWorkers")
    print(f"  active: {workers['active']}")
    print(f"  connected_total: {workers['connected_total']}")
    print(f"  claims_total: {workers['claims_total']}")
    print(f"  dequeue_errors_total: {workers['dequeue_errors_total']}")
    print(f"  queue_empty_loops_total: {workers['queue_empty_loops_total']}")
    print(f"  claims_by_worker: {workers['claims_by_worker']}")
    print(f"  pinning_ok: {workers.get('pinning_ok')}")
    print(f"  claim_conflicts: {workers.get('claim_conflicts')}")

    print("\nQueues")
    print(f"  comments: {queues['comments']}")
    print(f"  packaging_pending: {queues['packaging_pending']}")
    print(f"  recovery_tasks: {queues.get('recovery_tasks')}")
    print(f"  channel_discovery: {queues.get('channel_discovery')}")

    autopilot = report.get("autopilot", {})
    print("\nAutopilot")
    print(f"  enabled: {autopilot.get('enabled')}")

    print("\nAccounts")
    print(f"  total: {accounts['total']}")
    print(f"  status: {accounts['status']}")
    print(f"  lifecycle: {accounts['lifecycle']}")
    print(f"  audit: {accounts.get('audit')}")
    print(f"  blockers: {accounts.get('blockers')}")

    sessions = report["sessions"]
    print("\nSessions")
    print(f"  discovered_total: {sessions['discovered_total']}")
    print(f"  duplicates: {sessions['duplicates']}")
    print(f"  duplicate_phones: {sessions['duplicate_phones']}")
    print(f"  unowned_accounts: {sessions['unowned_accounts']}")

    print("\nJSON credentials")
    print(f"  summary: {report.get('json_credentials')}")

    channels = report["channels"]
    print("\nChannels")
    print(f"  review_state: {channels['review_state']}")
    print(f"  publish_mode: {channels['publish_mode']}")
    print(f"  publishable: {channels['publishable']}")

    policy = report["policy"]
    print("\nPolicy")
    print(f"  window_days: {policy['window_days']}")
    print(f"  decisions: {policy['decisions']}")
    print(f"  violations_24h: {policy.get('violations_24h', 0)}")
    print(f"  quarantine: {policy['quarantine']}")
    print(f"  quarantine_rate: {policy.get('quarantine_rate')}")
    print(f"  frozen_accounts_count: {policy.get('frozen_accounts_count')}")
    print(f"  parser_health: {policy['parser_health']}")
    print(f"  parser_claim_conflict: {policy.get('parser_claim_conflict')}")
