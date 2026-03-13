"""Shared audit, recovery-plan, and lifecycle-repair helpers."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select

from config import settings
from storage.models import Account, AccountStageEvent, User
from storage.sqlite_db import async_session
from utils.account_uploads import metadata_api_credentials, metadata_has_required_api_credentials
from utils.helpers import utcnow
from utils.proxy_bindings import cleanup_invalid_proxies, get_proxy_pool_summary, resolve_target_user_id
from utils.runtime_readiness import account_blockers
from utils.session_topology import (
    audit_session_topology,
    canonical_metadata_exists,
    canonical_session_exists,
    quarantine_noncanonical_assets,
)


INVALID_ACTIVE_HEALTH = {"dead", "restricted", "frozen", "expired"}


def app_hash_fingerprint(app_hash: str) -> str:
    value = str(app_hash or "").strip()
    if not value:
        return "missing"
    if len(value) <= 10:
        return value
    return f"{value[:6]}…{value[-4:]}"


def _account_metadata_path(sessions_dir: Path, account: Any) -> Path | None:
    if getattr(account, "user_id", None) is None:
        return None
    return sessions_dir / str(int(account.user_id)) / f"{str(account.phone).lstrip('+')}.json"


def derive_account_audit_status(
    account: Any,
    *,
    ready: bool,
) -> str:
    lifecycle = str(getattr(account, "lifecycle_stage", "") or "")
    status = str(getattr(account, "status", "") or "")
    health = str(getattr(account, "health_status", "") or "")

    if ready:
        return "ready"
    if lifecycle in {"active_commenting", "execution_ready"} and (status != "active" or health in INVALID_ACTIVE_HEALTH):
        return "stale-active"
    if health in {"restricted", "frozen"}:
        return "auth-valid restricted"
    if lifecycle == "uploaded":
        return "uploaded"
    return "unauthorized"


def derive_recovery_category(account: Any, *, audit_status: str) -> str:
    lifecycle = str(getattr(account, "lifecycle_stage", "") or "")
    health = str(getattr(account, "health_status", "") or "")
    restriction_reason = str(getattr(account, "restriction_reason", "") or "")
    previously_connected = bool(
        getattr(account, "last_active_at", None)
        or getattr(account, "last_probe_at", None)
        or int(getattr(account, "total_comments", 0) or 0) > 0
        or int(getattr(account, "days_active", 0) or 0) > 0
    )

    if audit_status == "ready":
        return "ready"
    if audit_status == "auth-valid restricted":
        return "auth-valid but restricted"
    if audit_status == "stale-active":
        return "stale active unauthorized"
    if lifecycle == "uploaded":
        return "uploaded unauthorized"
    if health == "expired" or restriction_reason == "unauthorized" or previously_connected:
        return "unauthorized but previously connected"
    return "manual review needed"


def recommended_next_action(
    account: Any,
    *,
    audit_status: str,
    recovery_category: str,
    session_present: bool,
    metadata_present: bool,
    metadata_creds_ok: bool,
    proxy_bound: bool,
    strict_proxy: bool,
) -> str:
    if not session_present:
        return "Загрузите файл .session заново."
    if not metadata_present:
        return "Загрузите файл .json с тем же номером."
    if not metadata_creds_ok:
        return "Замените .json: в нём должны быть app_id и app_hash."
    if strict_proxy and not proxy_bound:
        return "Загрузите новые прокси и повторите проверку доступа."
    if recovery_category == "auth-valid but restricted":
        return "Проверьте аккаунт вручную и затем снова запустите проверку доступа."
    if recovery_category == "stale active unauthorized":
        return "Обновите session-пару, затем исправьте этапы и повторите onboarding."
    if recovery_category == "uploaded unauthorized":
        return "Получите свежую session-пару и снова запустите проверку доступа."
    if recovery_category == "unauthorized but previously connected":
        return "Сделайте свежую session-пару для этого аккаунта и повторите проверку."
    if audit_status == "ready":
        return "Аккаунт готов к работе."
    return "Повторите проверку доступа и посмотрите логи аккаунта."


def lifecycle_reconcile_target(account: Any) -> tuple[str | None, str | None]:
    """Return target lifecycle stage and reason for stale accounts."""

    lifecycle = str(getattr(account, "lifecycle_stage", "") or "")
    status = str(getattr(account, "status", "") or "")
    health = str(getattr(account, "health_status", "") or "")
    if lifecycle not in {"active_commenting", "execution_ready"}:
        return None, None
    if status == "active" and health not in INVALID_ACTIVE_HEALTH:
        return None, None
    if health in {"restricted", "frozen"}:
        return "restricted", f"repair {lifecycle} -> restricted ({health})"
    return "uploaded", f"repair {lifecycle} -> uploaded ({status}/{health})"


def build_account_audit_records(
    accounts: Iterable[Any],
    *,
    sessions_dir: Path,
    strict_proxy: bool,
) -> list[dict[str, Any]]:
    pair_groups: dict[tuple[int | None, str], list[str]] = defaultdict(list)
    staged: list[dict[str, Any]] = []

    for account in accounts:
        session_present = canonical_session_exists(sessions_dir, account.user_id, account.phone)
        metadata_present = canonical_metadata_exists(sessions_dir, account.user_id, account.phone)
        metadata_path = _account_metadata_path(sessions_dir, account)
        metadata_creds_ok = metadata_has_required_api_credentials(metadata_path) if metadata_path else False
        app_id, app_hash = metadata_api_credentials(metadata_path) if metadata_path else (None, "")
        if app_id is not None and str(app_hash or "").strip():
            pair_groups[(app_id, str(app_hash or "").strip())].append(str(account.phone))
        staged.append(
            {
                "account": account,
                "session_present": session_present,
                "metadata_present": metadata_present,
                "metadata_path": metadata_path,
                "metadata_creds_ok": metadata_creds_ok,
                "app_id": app_id,
                "app_hash": str(app_hash or "").strip(),
            }
        )

    items: list[dict[str, Any]] = []
    for item in staged:
        account = item["account"]
        blockers_report = account_blockers(
            account,
            sessions_dir=sessions_dir,
            strict_proxy=bool(strict_proxy),
        )
        app_key = (item["app_id"], item["app_hash"])
        shared_phones = [
            phone
            for phone in pair_groups.get(app_key, [])
            if phone != str(account.phone)
        ] if item["metadata_creds_ok"] else []
        audit_status = derive_account_audit_status(account, ready=blockers_report.ready)
        recovery_category = derive_recovery_category(account, audit_status=audit_status)
        next_action = recommended_next_action(
            account,
            audit_status=audit_status,
            recovery_category=recovery_category,
            session_present=bool(item["session_present"]),
            metadata_present=bool(item["metadata_present"]),
            metadata_creds_ok=bool(item["metadata_creds_ok"]),
            proxy_bound=account.proxy_id is not None,
            strict_proxy=bool(strict_proxy),
        )
        items.append(
            {
                "id": account.id,
                "phone": account.phone,
                "user_id": account.user_id,
                "audit_status": audit_status,
                "recovery_category": recovery_category,
                "recommended_next_action": next_action,
                "status": account.status,
                "health_status": account.health_status,
                "lifecycle_stage": account.lifecycle_stage,
                "restriction_reason": account.restriction_reason,
                "proxy_binding": {
                    "bound": account.proxy_id is not None,
                    "proxy_id": account.proxy_id,
                },
                "session": {
                    "present": bool(item["session_present"]),
                    "metadata_present": bool(item["metadata_present"]),
                    "complete": bool(item["session_present"] and item["metadata_present"]),
                },
                "api_credentials": {
                    "present": bool(item["metadata_creds_ok"]),
                    "app_id": item["app_id"],
                    "app_hash_fingerprint": app_hash_fingerprint(item["app_hash"]),
                    "shared_usage_count": len(pair_groups.get(app_key, [])) if item["metadata_creds_ok"] else 0,
                    "shared_warning": bool(shared_phones),
                    "shared_with": shared_phones,
                },
                "readiness": {
                    "ready": blockers_report.ready,
                    "primary_blocker": blockers_report.primary,
                    "blockers": list(blockers_report.blockers),
                },
            }
        )

    items.sort(key=lambda row: (str(row["audit_status"]), str(row["phone"])))
    return items


def summarize_account_audit(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    ready = 0
    warnings = 0
    for row in records:
        status = str(row.get("audit_status") or "unknown")
        category = str(row.get("recovery_category") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
        if bool(((row.get("readiness") or {}).get("ready"))):
            ready += 1
        if bool(((row.get("api_credentials") or {}).get("shared_warning"))):
            warnings += 1
    return {
        "total": sum(status_counts.values()),
        "ready": ready,
        "needs_attention": max(0, sum(status_counts.values()) - ready),
        "status_counts": dict(sorted(status_counts.items())),
        "recovery_categories": dict(sorted(category_counts.items())),
        "shared_api_pair_warnings": warnings,
    }


def build_json_credentials_audit(
    accounts: Iterable[Any],
    *,
    sessions_dir: Path,
) -> dict[str, Any]:
    staged: list[dict[str, Any]] = []
    pair_groups: dict[tuple[int | None, str], list[str]] = defaultdict(list)

    for account in accounts:
        metadata_path = _account_metadata_path(sessions_dir, account)
        present = metadata_has_required_api_credentials(metadata_path) if metadata_path else False
        app_id, app_hash = metadata_api_credentials(metadata_path) if metadata_path else (None, "")
        app_hash = str(app_hash or "").strip()
        if present:
            pair_groups[(app_id, app_hash)].append(str(account.phone))
        staged.append(
            {
                "phone": account.phone,
                "user_id": account.user_id,
                "metadata_path": str(metadata_path) if metadata_path else None,
                "has_required_credentials": bool(present),
                "app_id": app_id,
                "app_hash": app_hash,
            }
        )

    items: list[dict[str, Any]] = []
    missing = 0
    shared_pairs = 0
    for row in staged:
        app_key = (row["app_id"], row["app_hash"])
        shared_with = [
            phone
            for phone in pair_groups.get(app_key, [])
            if phone != str(row["phone"])
        ] if row["has_required_credentials"] else []
        if not row["has_required_credentials"]:
            missing += 1
        if shared_with:
            shared_pairs += 1
        items.append(
            {
                "phone": row["phone"],
                "user_id": row["user_id"],
                "metadata_path": row["metadata_path"],
                "has_required_credentials": row["has_required_credentials"],
                "app_id": row["app_id"],
                "app_hash_fingerprint": app_hash_fingerprint(row["app_hash"]),
                "shared_usage_count": len(pair_groups.get(app_key, [])) if row["has_required_credentials"] else 0,
                "shared_warning": bool(shared_with),
                "shared_with": shared_with,
                "warning": (
                    "shared_app_pair"
                    if shared_with
                    else ("missing_app_credentials" if not row["has_required_credentials"] else "")
                ),
            }
        )

    items.sort(key=lambda row: str(row["phone"]))
    return {
        "items": items,
        "summary": {
            "total": len(items),
            "missing_required_credentials": missing,
            "unique_pairs": len(pair_groups),
            "shared_pair_accounts": shared_pairs,
            "largest_shared_pair": max((len(phones) for phones in pair_groups.values()), default=0),
        },
    }


def normalize_proxy_observability(summary: dict[str, Any], cleanup: dict[str, Any]) -> dict[str, Any]:
    summary = dict(summary or {})
    cleanup = dict(cleanup or {})
    return {
        "summary": summary,
        "cleanup": cleanup,
        "binding_uniqueness_ok": int(summary.get("duplicate_bound", 0)) == 0,
        "low_stock_warning": (
            f"Загрузите ещё {int(summary.get('recommended_topup', 0))} прокси."
            if summary.get("low_stock")
            else ""
        ),
    }


async def collect_accounts_for_user(user_id: int | None = None) -> list[Account]:
    target_user_id = await resolve_target_user_id(user_id)
    async with async_session() as session:
        query = select(Account).order_by(Account.created_at.asc(), Account.id.asc()).limit(10000)
        if target_user_id is not None:
            query = query.where(Account.user_id == target_user_id)
        result = await session.execute(query)
        return list(result.scalars().all())


async def known_user_ids_for_scope(user_id: int | None = None) -> list[int]:
    target_user_id = await resolve_target_user_id(user_id)
    if target_user_id is not None:
        return [int(target_user_id)]
    async with async_session() as session:
        result = await session.execute(select(User.id).order_by(User.id.asc()))
        return [int(row[0]) for row in result.all()]


async def collect_account_audit(*, user_id: int | None = None) -> dict[str, Any]:
    accounts = await collect_accounts_for_user(user_id)
    records = build_account_audit_records(
        accounts,
        sessions_dir=settings.sessions_path,
        strict_proxy=bool(settings.STRICT_PROXY_PER_ACCOUNT),
    )
    return {
        "items": records,
        "summary": summarize_account_audit(records),
    }


async def collect_json_credentials_audit(*, user_id: int | None = None) -> dict[str, Any]:
    accounts = await collect_accounts_for_user(user_id)
    return build_json_credentials_audit(accounts, sessions_dir=settings.sessions_path)


async def collect_session_topology_audit(*, user_id: int | None = None) -> dict[str, Any]:
    return audit_session_topology(
        settings.sessions_path,
        known_user_ids=await known_user_ids_for_scope(user_id),
    )


async def quarantine_session_duplicates(
    *,
    user_id: int | None = None,
    phones: Iterable[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return quarantine_noncanonical_assets(
        settings.sessions_path,
        known_user_ids=await known_user_ids_for_scope(user_id),
        phones=phones,
        dry_run=dry_run,
    )


async def collect_proxy_observability(*, user_id: int | None = None) -> dict[str, Any]:
    summary = await get_proxy_pool_summary(user_id=user_id)
    cleanup_preview = await cleanup_invalid_proxies(user_id=user_id, dry_run=True)
    return normalize_proxy_observability(summary, cleanup_preview)


async def reconcile_stale_lifecycle(
    *,
    user_id: int | None = None,
    actor: str = "audit_reconcile",
    dry_run: bool = False,
) -> dict[str, Any]:
    target_user_id = await resolve_target_user_id(user_id)
    repaired: list[dict[str, Any]] = []
    skipped = 0
    async with async_session() as session:
        query = select(Account).order_by(Account.created_at.asc(), Account.id.asc()).limit(10000)
        if target_user_id is not None:
            query = query.where(Account.user_id == target_user_id)
        result = await session.execute(query)
        accounts = list(result.scalars().all())
        for account in accounts:
            to_stage, reason = lifecycle_reconcile_target(account)
            if not to_stage:
                skipped += 1
                continue
            repaired.append(
                {
                    "phone": account.phone,
                    "from_stage": account.lifecycle_stage,
                    "to_stage": to_stage,
                    "status": account.status,
                    "health_status": account.health_status,
                    "reason": reason,
                }
            )
            if dry_run:
                continue
            old_stage = account.lifecycle_stage
            account.lifecycle_stage = to_stage
            account.last_active_at = utcnow()
            session.add(
                AccountStageEvent(
                    account_id=account.id,
                    phone=account.phone,
                    from_stage=old_stage,
                    to_stage=to_stage,
                    actor=actor,
                    reason=reason,
                )
            )
        if not dry_run:
            await session.commit()
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "target_user_id": target_user_id,
        "scanned": len(accounts),
        "repaired": len(repaired),
        "skipped": skipped,
        "items": repaired,
    }
