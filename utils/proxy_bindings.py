"""DB-backed proxy sync, binding, and lifecycle helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from config import settings
from core.proxy_manager import ProxyConfig, ProxyManager, parse_proxy_line
from storage.models import Account, AccountStageEvent, Proxy, User
from storage.sqlite_db import async_session
from utils.helpers import utcnow
from utils.session_topology import canonical_session_exists


def _proxy_key(
    proxy_type: str,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
) -> tuple[str, str, int, str, str]:
    return (
        (proxy_type or "socks5").strip().lower(),
        (host or "").strip(),
        int(port),
        (username or "").strip(),
        (password or "").strip(),
    )


def proxy_config_from_row(proxy: Proxy) -> ProxyConfig:
    return ProxyConfig(
        proxy_type=str(proxy.proxy_type or "socks5"),
        host=str(proxy.host),
        port=int(proxy.port),
        username=str(proxy.username) if proxy.username else None,
        password=str(proxy.password) if proxy.password else None,
    )


def _recently_checked(proxy: Proxy) -> bool:
    if proxy.last_checked is None:
        return False
    delta = utcnow() - proxy.last_checked
    return delta.total_seconds() < max(60, int(settings.PROXY_RECHECK_COOLDOWN_SEC))


def _mark_proxy_alive(proxy: Proxy) -> None:
    now = utcnow()
    proxy.is_active = True
    proxy.health_status = "alive"
    proxy.consecutive_failures = 0
    proxy.last_error = None
    proxy.last_checked = now
    proxy.last_success_at = now
    proxy.invalidated_at = None


def _mark_proxy_failed(proxy: Proxy, *, error: str) -> bool:
    now = utcnow()
    proxy.consecutive_failures = int(proxy.consecutive_failures or 0) + 1
    proxy.last_error = error[:255]
    proxy.last_checked = now
    should_disable = proxy.consecutive_failures >= max(1, int(settings.PROXY_FAILURES_BEFORE_DISABLE))
    proxy.health_status = "dead" if should_disable else "failing"
    if should_disable:
        proxy.is_active = False
        proxy.invalidated_at = now
    return should_disable


async def _probe_proxy(proxy: Proxy) -> tuple[bool, str]:
    manager = ProxyManager()
    config = proxy_config_from_row(proxy)
    try:
        ok = await manager.validate_proxy(config, timeout=max(3, int(settings.PROXY_HEALTH_TIMEOUT_SEC)))
        return bool(ok), "ok" if ok else "probe_failed"
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


async def _clear_proxy_bindings(session, proxy_id: int) -> int:
    result = await session.execute(select(Account).where(Account.proxy_id == proxy_id))
    accounts = list(result.scalars().all())
    for account in accounts:
        account.proxy_id = None
    return len(accounts)


async def resolve_target_user_id(user_id: int | None = None) -> int | None:
    if user_id is not None:
        return int(user_id)
    async with async_session() as session:
        result = await session.execute(
            select(User.id).order_by(User.is_admin.desc(), User.id.asc()).limit(1)
        )
        row = result.first()
    return int(row[0]) if row else None


async def sync_proxies_from_file(path: Path, *, user_id: int | None = None) -> dict:
    target_user_id = await resolve_target_user_id(user_id)
    if not path.exists():
        return {
            "path": str(path),
            "target_user_id": target_user_id,
            "file_exists": False,
            "lines_total": 0,
            "valid": 0,
            "invalid": 0,
            "duplicates_in_file": 0,
            "added": 0,
            "reactivated": 0,
            "existing": 0,
        }

    raw_lines = path.read_text(encoding="utf-8").splitlines()
    parsed: list[ProxyConfig] = []
    invalid = 0
    seen: set[tuple[str, str, int, str, str]] = set()
    duplicates_in_file = 0

    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        proxy = parse_proxy_line(stripped, settings.PROXY_TYPE)
        if proxy is None:
            invalid += 1
            continue
        key = _proxy_key(
            proxy.proxy_type,
            proxy.host,
            proxy.port,
            proxy.username,
            proxy.password,
        )
        if key in seen:
            duplicates_in_file += 1
            continue
        seen.add(key)
        parsed.append(proxy)

    async with async_session() as session:
        query = select(Proxy)
        if target_user_id is None:
            query = query.where(Proxy.user_id.is_(None))
        else:
            query = query.where(Proxy.user_id == target_user_id)
        result = await session.execute(query)
        existing_rows = list(result.scalars().all())
        existing_by_key = {
            _proxy_key(row.proxy_type, row.host, row.port, row.username, row.password): row
            for row in existing_rows
        }

        added = 0
        reactivated = 0
        existing = 0
        now = utcnow()
        for proxy in parsed:
            key = _proxy_key(
                proxy.proxy_type,
                proxy.host,
                proxy.port,
                proxy.username,
                proxy.password,
            )
            row = existing_by_key.get(key)
            if row is None:
                row = Proxy(
                    user_id=target_user_id,
                    proxy_type=proxy.proxy_type,
                    host=proxy.host,
                    port=proxy.port,
                    username=proxy.username,
                    password=proxy.password,
                    is_active=True,
                    health_status="unknown",
                    consecutive_failures=0,
                    last_checked=now,
                )
                session.add(row)
                existing_by_key[key] = row
                added += 1
                continue

            existing += 1
            if not bool(row.is_active):
                row.is_active = True
                reactivated += 1
            if not str(row.health_status or "").strip():
                row.health_status = "unknown"
            row.last_checked = now

        await session.commit()

    return {
        "path": str(path),
        "target_user_id": target_user_id,
        "file_exists": True,
        "lines_total": len(raw_lines),
        "valid": len(parsed),
        "invalid": invalid,
        "duplicates_in_file": duplicates_in_file,
        "added": added,
        "reactivated": reactivated,
        "existing": existing,
    }


async def get_bound_proxy_config(phone: str) -> ProxyConfig | None:
    async with async_session() as session:
        result = await session.execute(
            select(Account)
            .options(selectinload(Account.proxy))
            .where(Account.phone == phone)
        )
        account = result.scalar_one_or_none()
    if account is None or account.proxy is None or not bool(account.proxy.is_active):
        return None
    return proxy_config_from_row(account.proxy)


async def get_proxy_pool_summary(*, user_id: int | None = None) -> dict:
    target_user_id = await resolve_target_user_id(user_id)
    async with async_session() as session:
        proxies_query = select(Proxy)
        accounts_query = select(Account)
        if target_user_id is not None:
            proxies_query = proxies_query.where((Proxy.user_id == target_user_id) | (Proxy.user_id.is_(None)))
            accounts_query = accounts_query.where(Account.user_id == target_user_id)
        proxies_result = await session.execute(proxies_query.order_by(Proxy.created_at.asc(), Proxy.id.asc()))
        accounts_result = await session.execute(accounts_query.order_by(Account.created_at.asc(), Account.id.asc()))
        proxies = list(proxies_result.scalars().all())
        accounts = list(accounts_result.scalars().all())

    bound_proxy_ids = [int(account.proxy_id) for account in accounts if account.proxy_id is not None]
    bound_unique = set(bound_proxy_ids)
    duplicate_bound = max(0, len(bound_proxy_ids) - len(bound_unique))
    total = len(proxies)
    active = [proxy for proxy in proxies if bool(proxy.is_active)]
    healthy = [proxy for proxy in active if str(proxy.health_status or "") == "alive"]
    unknown = [proxy for proxy in active if str(proxy.health_status or "") in {"", "unknown"}]
    failing = [proxy for proxy in active if str(proxy.health_status or "") == "failing"]
    dead = [proxy for proxy in proxies if str(proxy.health_status or "") == "dead" or not bool(proxy.is_active)]
    free_active = [proxy for proxy in active if int(proxy.id) not in bound_unique]
    free_healthy = [proxy for proxy in healthy if int(proxy.id) not in bound_unique]
    free_unknown = [proxy for proxy in unknown if int(proxy.id) not in bound_unique]
    account_total = len(accounts)
    usable_for_binding = len(free_healthy) + len(free_unknown)
    low_stock = usable_for_binding < max(1, int(settings.PROXY_MIN_FREE_POOL))
    deficit_vs_accounts = max(0, account_total - len(active))
    recommended_topup = max(0, max(int(settings.PROXY_MIN_FREE_POOL), deficit_vs_accounts) - usable_for_binding)
    return {
        "target_user_id": target_user_id,
        "total": total,
        "active": len(active),
        "healthy": len(healthy),
        "unknown": len(unknown),
        "failing": len(failing),
        "dead_or_disabled": len(dead),
        "bound_accounts": len(bound_proxy_ids),
        "bound_unique": len(bound_unique),
        "duplicate_bound": duplicate_bound,
        "free_active": len(free_active),
        "free_healthy": len(free_healthy),
        "free_unknown": len(free_unknown),
        "usable_for_binding": usable_for_binding,
        "account_total": account_total,
        "low_stock": low_stock,
        "recommended_topup": recommended_topup,
    }


async def cleanup_invalid_proxies(*, user_id: int | None = None, dry_run: bool = False) -> dict:
    target_user_id = await resolve_target_user_id(user_id)
    cutoff = utcnow() - timedelta(days=max(1, int(settings.PROXY_DELETE_INVALID_AFTER_DAYS)))
    async with async_session() as session:
        query = select(Proxy).where(
            Proxy.is_active.is_(False),
            Proxy.invalidated_at.is_not(None),
            Proxy.invalidated_at <= cutoff,
        )
        if target_user_id is not None:
            query = query.where((Proxy.user_id == target_user_id) | (Proxy.user_id.is_(None)))
        result = await session.execute(query)
        rows = list(result.scalars().all())
        removable_ids: list[int] = []
        skipped_bound = 0
        for row in rows:
            bound = await session.execute(select(func.count()).select_from(Account).where(Account.proxy_id == row.id))
            if int(bound.scalar_one() or 0) > 0:
                skipped_bound += 1
                continue
            removable_ids.append(int(row.id))
        deleted = 0
        if removable_ids and not dry_run:
            await session.execute(delete(Proxy).where(Proxy.id.in_(removable_ids)))
            deleted = len(removable_ids)
            await session.commit()
        elif removable_ids:
            deleted = len(removable_ids)
        return {
            "target_user_id": target_user_id,
            "candidates": len(rows),
            "deleted": deleted,
            "dry_run": bool(dry_run),
            "skipped_bound": skipped_bound,
            "cutoff_utc": cutoff.isoformat(),
        }


async def validate_proxy_pool(
    *,
    user_id: int | None = None,
    limit: int | None = None,
    include_inactive: bool = False,
) -> dict:
    target_user_id = await resolve_target_user_id(user_id)
    async with async_session() as session:
        query = select(Proxy).order_by(Proxy.created_at.asc(), Proxy.id.asc())
        if not include_inactive:
            query = query.where(Proxy.is_active.is_(True))
        if target_user_id is not None:
            query = query.where((Proxy.user_id == target_user_id) | (Proxy.user_id.is_(None)))
        if limit is not None:
            query = query.limit(max(1, int(limit)))
        result = await session.execute(query)
        proxies = list(result.scalars().all())

        alive = 0
        failed = 0
        disabled = 0
        bindings_cleared = 0
        checked = 0
        for proxy in proxies:
            checked += 1
            ok, error = await _probe_proxy(proxy)
            if ok:
                _mark_proxy_alive(proxy)
                alive += 1
                continue
            was_disabled = _mark_proxy_failed(proxy, error=error)
            failed += 1
            if was_disabled:
                bindings_cleared += await _clear_proxy_bindings(session, int(proxy.id))
                disabled += 1
        await session.commit()

    cleanup = await cleanup_invalid_proxies(user_id=target_user_id)
    summary = await get_proxy_pool_summary(user_id=target_user_id)
    return {
        "target_user_id": target_user_id,
        "checked": checked,
        "alive": alive,
        "failed": failed,
        "disabled": disabled,
        "bindings_cleared": bindings_cleared,
        "cleanup": cleanup,
        "summary": summary,
    }


async def ensure_live_proxy_binding(
    phone: str,
    *,
    user_id: int | None = None,
) -> dict:
    normalized_phone = str(phone or "").strip()
    if not normalized_phone:
        raise ValueError("invalid_phone")

    target_user_id = await resolve_target_user_id(user_id)
    async with async_session() as session:
        account_query = select(Account).options(selectinload(Account.proxy)).where(Account.phone == normalized_phone)
        if target_user_id is not None:
            account_query = account_query.where(Account.user_id == target_user_id)
        account_result = await session.execute(account_query)
        account = account_result.scalar_one_or_none()
        if account is None:
            raise RuntimeError("account_not_found")

        duplicate_binding = False
        if account.proxy_id is not None:
            duplicate_count = await session.scalar(
                select(func.count()).select_from(Account).where(
                    Account.proxy_id == account.proxy_id,
                    Account.id != account.id,
                )
            )
            duplicate_binding = int(duplicate_count or 0) > 0

        if account.proxy is not None and bool(account.proxy.is_active) and not duplicate_binding:
            if str(account.proxy.health_status or "") == "alive" and _recently_checked(account.proxy):
                summary = await get_proxy_pool_summary(user_id=target_user_id)
                return {
                    "ok": True,
                    "phone": normalized_phone,
                    "action": "kept",
                    "reason": "recently_checked_alive",
                    "proxy_id": int(account.proxy.id),
                    "proxy_url": account.proxy.url,
                    "pool": summary,
                }
            ok, error = await _probe_proxy(account.proxy)
            if ok:
                _mark_proxy_alive(account.proxy)
                await session.commit()
                summary = await get_proxy_pool_summary(user_id=target_user_id)
                return {
                    "ok": True,
                    "phone": normalized_phone,
                    "action": "kept",
                    "reason": "validated",
                    "proxy_id": int(account.proxy.id),
                    "proxy_url": account.proxy.url,
                    "pool": summary,
                }
            disabled = _mark_proxy_failed(account.proxy, error=error)
            if disabled:
                await _clear_proxy_bindings(session, int(account.proxy.id))
            else:
                account.proxy_id = None
            await session.commit()

        used_query = select(Account.proxy_id).where(Account.proxy_id.is_not(None), Account.id != account.id)
        if target_user_id is not None:
            used_query = used_query.where(Account.user_id == target_user_id)
        used_result = await session.execute(used_query)
        used_proxy_ids = {int(row[0]) for row in used_result.fetchall() if row[0] is not None}

        proxies_query = select(Proxy).where(Proxy.is_active.is_(True)).order_by(Proxy.created_at.asc(), Proxy.id.asc())
        if target_user_id is not None:
            proxies_query = proxies_query.where((Proxy.user_id == target_user_id) | (Proxy.user_id.is_(None)))
        proxy_result = await session.execute(proxies_query)
        candidates = list(proxy_result.scalars().all())

        for candidate in candidates:
            if int(candidate.id) in used_proxy_ids:
                continue
            if str(candidate.health_status or "") == "alive" and _recently_checked(candidate):
                account.proxy_id = int(candidate.id)
                await session.commit()
                summary = await get_proxy_pool_summary(user_id=target_user_id)
                return {
                    "ok": True,
                    "phone": normalized_phone,
                    "action": "rebound",
                    "reason": "healthy_candidate",
                    "proxy_id": int(candidate.id),
                    "proxy_url": candidate.url,
                    "pool": summary,
                }
            ok, error = await _probe_proxy(candidate)
            if ok:
                _mark_proxy_alive(candidate)
                account.proxy_id = int(candidate.id)
                await session.commit()
                summary = await get_proxy_pool_summary(user_id=target_user_id)
                return {
                    "ok": True,
                    "phone": normalized_phone,
                    "action": "rebound",
                    "reason": "validated_candidate",
                    "proxy_id": int(candidate.id),
                    "proxy_url": candidate.url,
                    "pool": summary,
                }
            disabled = _mark_proxy_failed(candidate, error=error)
            if disabled:
                await _clear_proxy_bindings(session, int(candidate.id))
            await session.commit()

    summary = await get_proxy_pool_summary(user_id=target_user_id)
    return {
        "ok": False,
        "phone": normalized_phone,
        "action": "missing",
        "reason": "no_live_unique_proxy",
        "proxy_id": None,
        "proxy_url": "",
        "pool": summary,
    }


async def get_live_proxy_config(
    phone: str,
    *,
    user_id: int | None = None,
) -> tuple[ProxyConfig | None, dict]:
    report = await ensure_live_proxy_binding(phone, user_id=user_id)
    if not bool(report.get("ok")):
        return None, report
    config = await get_bound_proxy_config(str(phone or "").strip())
    return config, report


async def bind_accounts_to_proxies(
    *,
    user_id: int | None = None,
    phones: Iterable[str] | None = None,
    rebind: bool = False,
) -> dict:
    target_user_id = await resolve_target_user_id(user_id)
    phones_set = {str(phone).strip() for phone in phones or [] if str(phone).strip()}

    async with async_session() as session:
        accounts_query = select(Account).order_by(Account.created_at.asc(), Account.id.asc())
        if target_user_id is not None:
            accounts_query = accounts_query.where(Account.user_id == target_user_id)
        if phones_set:
            accounts_query = accounts_query.where(Account.phone.in_(sorted(phones_set)))
        accounts_result = await session.execute(accounts_query)
        accounts = list(accounts_result.scalars().all())

        proxies_query = (
            select(Proxy)
            .where(Proxy.is_active.is_(True))
            .order_by(Proxy.created_at.asc(), Proxy.id.asc())
        )
        if target_user_id is not None:
            proxies_query = proxies_query.where(
                (Proxy.user_id == target_user_id) | (Proxy.user_id.is_(None))
            )
        proxy_result = await session.execute(proxies_query)
        proxies = list(proxy_result.scalars().all())

        used_proxy_ids: set[int] = set()
        used_result = await session.execute(
            select(Account.proxy_id).where(Account.proxy_id.is_not(None))
        )
        for row in used_result.fetchall():
            proxy_id = row[0]
            if proxy_id is not None:
                used_proxy_ids.add(int(proxy_id))

        if rebind:
            for account in accounts:
                if account.proxy_id is not None:
                    used_proxy_ids.discard(int(account.proxy_id))

        bound = 0
        already_bound = 0
        unavailable = 0

        rotating_proxy: Proxy | None = proxies[0] if settings.PROXY_ROTATING and proxies else None
        next_idx = 0

        for account in accounts:
            if account.proxy_id is not None and not rebind:
                already_bound += 1
                continue

            chosen: Proxy | None = None
            if settings.PROXY_ROTATING:
                chosen = rotating_proxy
            else:
                while next_idx < len(proxies):
                    candidate = proxies[next_idx]
                    next_idx += 1
                    if int(candidate.id) in used_proxy_ids:
                        continue
                    chosen = candidate
                    break

            if chosen is None:
                unavailable += 1
                continue

            account.proxy_id = int(chosen.id)
            used_proxy_ids.add(int(chosen.id))
            bound += 1

        await session.commit()

    return {
        "target_user_id": target_user_id,
        "accounts_scanned": len(accounts),
        "proxies_available": len(proxies),
        "bound": bound,
        "already_bound": already_bound,
        "unavailable": unavailable,
        "rebind": bool(rebind),
        "rotating_mode": bool(settings.PROXY_ROTATING),
    }


@dataclass(frozen=True)
class PromotionCheck:
    blockers: list[str]

    @property
    def ok(self) -> bool:
        return not self.blockers


def _promotion_check(account: Account, *, sessions_dir: Path) -> PromotionCheck:
    blockers: list[str] = []
    if account.user_id is None:
        blockers.append("no_owner")
    elif not canonical_session_exists(sessions_dir, int(account.user_id), account.phone):
        blockers.append("session_missing")
    if bool(settings.STRICT_PROXY_PER_ACCOUNT) and account.proxy_id is None:
        blockers.append("no_proxy_binding")
    if account.lifecycle_stage == "restricted":
        blockers.append("stage_restricted")
    if account.status in {"banned", "error"}:
        blockers.append(f"status_{account.status}")
    if account.health_status in {"dead", "restricted", "frozen", "expired"}:
        blockers.append(f"health_{account.health_status}")
    return PromotionCheck(blockers=blockers)


async def promote_accounts(
    *,
    user_id: int | None = None,
    from_stages: Iterable[str] | None = None,
    to_stage: str,
    phones: Iterable[str] | None = None,
    status: str | None = None,
    health_status: str | None = None,
    actor: str,
    reason: str,
    allow_status_override: bool = False,
    allow_health_override: bool = False,
) -> dict:
    target_user_id = await resolve_target_user_id(user_id)
    phones_set = {str(phone).strip() for phone in phones or [] if str(phone).strip()}
    from_stage_set = {str(stage).strip() for stage in from_stages or [] if str(stage).strip()}

    async with async_session() as session:
        query = select(Account).order_by(Account.created_at.asc(), Account.id.asc())
        if target_user_id is not None:
            query = query.where(Account.user_id == target_user_id)
        if phones_set:
            query = query.where(Account.phone.in_(sorted(phones_set)))
        if from_stage_set:
            query = query.where(Account.lifecycle_stage.in_(sorted(from_stage_set)))
        result = await session.execute(query)
        accounts = list(result.scalars().all())

        promoted = 0
        skipped: list[dict[str, object]] = []
        for account in accounts:
            check = _promotion_check(account, sessions_dir=settings.sessions_path)
            blockers = list(check.blockers)
            if allow_status_override:
                blockers = [item for item in blockers if not item.startswith("status_")]
            if allow_health_override:
                blockers = [item for item in blockers if not item.startswith("health_")]

            if blockers:
                skipped.append({"phone": account.phone, "blockers": blockers})
                continue

            old_stage = account.lifecycle_stage
            account.lifecycle_stage = to_stage
            if status is not None:
                account.status = status
            if health_status is not None:
                account.health_status = health_status

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
            promoted += 1

        await session.commit()

    return {
        "target_user_id": target_user_id,
        "accounts_scanned": len(accounts),
        "to_stage": to_stage,
        "promoted": promoted,
        "skipped": skipped,
        "skipped_count": len(skipped),
        "status_override": status,
        "health_override": health_status,
    }
