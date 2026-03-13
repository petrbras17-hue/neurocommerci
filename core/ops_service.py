"""Shared ops actions for bot, ops API, and background services."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update

from channels.channel_db import ChannelDB
from config import settings
from core.digest_service import send_daily_digest_summary
from core.onboarding_service import (
    get_onboarding_status,
    list_onboarding_runs,
    record_onboarding_step,
    start_onboarding_run,
)
from core.human_gated_workflow import (
    apply_channel_draft,
    apply_content_draft,
    apply_profile_draft,
    assign_account_role,
    approve_comment_draft,
    create_comment_draft,
    generate_channel_draft,
    generate_content_draft,
    generate_profile_draft,
    review_comment_draft,
)
from core.redis_state import redis_state
from core.reset_service import reset_user_state
from core.task_queue import task_queue
from scripts.session_auth_audit import audit_sessions
from storage.models import Account, AccountStageEvent, User
from storage.sqlite_db import async_session
from utils.helpers import utcnow
from utils.account_uploads import find_api_credential_conflicts, metadata_api_credentials, metadata_has_required_api_credentials
from utils.proxy_bindings import ensure_live_proxy_binding, resolve_target_user_id
from utils.session_topology import canonical_metadata_exists, canonical_session_exists


channel_db = ChannelDB()


def normalize_phone(raw: str | None) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return f"+{digits}" if digits else ""


def _eligible_for_packaging(account: Account) -> tuple[bool, str]:
    if account.user_id is None:
        return False, "no_owner"
    if not canonical_session_exists(settings.sessions_path, account.user_id, account.phone):
        return False, "session_missing"
    if not canonical_metadata_exists(settings.sessions_path, account.user_id, account.phone):
        return False, "metadata_missing"
    metadata_path = settings.sessions_path / str(int(account.user_id)) / f"{account.phone.lstrip('+')}.json"
    if not metadata_has_required_api_credentials(metadata_path):
        return False, "metadata_api_credentials_missing"
    if account.status == "banned":
        return False, "account_banned"
    if account.health_status == "dead":
        return False, "account_dead"
    if account.health_status in {"restricted", "frozen", "expired"}:
        return False, f"health_{account.health_status}"
    if account.status not in {"active", "cooldown", "flood_wait", "error"}:
        return False, f"status_{account.status}"
    if account.lifecycle_stage == "packaging":
        return False, "already_packaging"
    if account.lifecycle_stage not in {"uploaded", "packaging_error"}:
        return False, f"stage_{account.lifecycle_stage}"
    return True, ""


async def queue_packaging_phone(phone: str, *, user_id: int | None = None) -> dict[str, Any]:
    if settings.HUMAN_GATED_PACKAGING:
        raise RuntimeError("human_gated_packaging_enabled")
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        raise ValueError("invalid_phone")

    await task_queue.connect()
    async with async_session() as session:
        query = select(Account).where(Account.phone == normalized_phone)
        if user_id is not None:
            query = query.where(Account.user_id == user_id)
        result = await session.execute(query)
        account = result.scalar_one_or_none()
        if account is None:
            raise RuntimeError("account_not_found")

        eligible, reason = _eligible_for_packaging(account)
        if not eligible:
            raise RuntimeError(reason)

        payload = {
            "phone": account.phone,
            "user_id": account.user_id,
            "session_file": account.session_file,
        }
        task_id = await task_queue.enqueue("packaging_tasks", payload)
        account.lifecycle_stage = "packaging"
        await session.commit()

    queue_size = await task_queue.queue_size("packaging_tasks")
    return {
        "ok": True,
        "task_id": task_id,
        "phone": normalized_phone,
        "user_id": user_id,
        "queue_size": queue_size,
        "lifecycle_stage": "packaging",
    }


async def queue_packaging_phone_with_onboarding(
    phone: str,
    *,
    user_id: int | None = None,
    actor: str = "onboarding:packaging",
    source: str = "bot",
    channel: str = "bot",
    notes: str = "",
) -> dict[str, Any]:
    if settings.HUMAN_GATED_PACKAGING:
        draft = await create_profile_draft_for_phone(
            phone,
            user_id=user_id,
            actor=actor,
            source=source,
            channel=channel,
        )
        draft["mode"] = "human_gated_profile_draft"
        return draft
    report = await queue_packaging_phone(phone, user_id=user_id)
    await record_onboarding_step(
        phone,
        user_id=user_id,
        step_key="packaging_queued",
        actor=actor,
        source=source,
        channel=channel,
        result="queued",
        notes=notes or "Подготовка аккаунта поставлена в очередь.",
        payload=report,
    )
    return report


async def queue_packaging_bulk(*, user_id: int | None = None) -> dict[str, Any]:
    if settings.HUMAN_GATED_PACKAGING:
        raise RuntimeError("human_gated_packaging_enabled")
    await task_queue.connect()
    queued = 0
    skipped = 0
    failed = 0
    queued_phones: list[str] = []

    async with async_session() as session:
        query = select(Account)
        if user_id is not None:
            query = query.where(Account.user_id == user_id)
        result = await session.execute(query.order_by(Account.created_at.asc()).limit(10000))
        accounts = list(result.scalars().all())

        for account in accounts:
            eligible, _reason = _eligible_for_packaging(account)
            if not eligible:
                skipped += 1
                continue
            payload = {
                "phone": account.phone,
                "user_id": account.user_id,
                "session_file": account.session_file,
            }
            try:
                await task_queue.enqueue("packaging_tasks", payload)
                queued += 1
                queued_phones.append(account.phone)
            except Exception:
                failed += 1

        if queued_phones:
            await session.execute(
                update(Account)
                .where(Account.phone.in_(queued_phones))
                .values(lifecycle_stage="packaging")
            )
            await session.commit()

    queue_size = await task_queue.queue_size("packaging_tasks")
    return {
        "ok": True,
        "queued": queued,
        "skipped": skipped,
        "failed": failed,
        "queue_size": queue_size,
    }


async def run_auth_refresh(
    *,
    user_id: int | None = None,
    set_parser_first_authorized: bool = True,
    clear_worker_claims: bool = True,
) -> dict[str, Any]:
    return await audit_sessions(
        user_id=user_id,
        mark_unauthorized=True,
        reactivate_authorized=True,
        authorized_stage="auth_verified" if settings.HUMAN_GATED_PACKAGING else "active_commenting",
        authorized_status="active",
        authorized_health="alive",
        set_parser_first_authorized=bool(set_parser_first_authorized),
        clear_worker_claims=bool(clear_worker_claims),
        stage_actor="ops_api_auth_refresh",
    )


async def run_auth_refresh_for_phone(
    phone: str,
    *,
    user_id: int | None = None,
    actor: str = "onboarding:auth_check",
    source: str = "bot",
    channel: str = "bot",
    notes: str = "",
    set_parser_first_authorized: bool = False,
    clear_worker_claims: bool = False,
) -> dict[str, Any]:
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        raise ValueError("invalid_phone")
    target_user_id = await resolve_target_user_id(user_id)
    if target_user_id is None:
        report = {
            "count": 1,
            "authorized": 0,
            "unauthorized_like": 1,
            "updated_unauthorized": 0,
            "reactivated_authorized": 0,
            "parser_phone_before": str(settings.PARSER_ONLY_PHONE or ""),
            "parser_phone_after": str(settings.PARSER_ONLY_PHONE or ""),
            "parser_reassigned": False,
            "claims_cleared": 0,
            "worker_heartbeats_cleared": 0,
            "results": [
                {
                    "phone": normalized_phone,
                    "user_id": user_id,
                    "account_status": "error",
                    "health_status": "unknown",
                    "lifecycle_stage": "uploaded",
                    "authorized": False,
                    "probe_status": "no_owner",
                }
            ],
        }
        await record_onboarding_step(
            normalized_phone,
            user_id=user_id,
            step_key="auth_check",
            actor=actor,
            source=source,
            channel=channel,
            result="no_owner",
            notes="У аккаунта нет tenant ownership, поэтому проверка доступа не выполнялась.",
            payload=report,
        )
        return report
    metadata_path = settings.sessions_path / str(int(target_user_id)) / f"{normalized_phone.lstrip('+')}.json"
    if not metadata_has_required_api_credentials(metadata_path):
        report = {
            "count": 1,
            "authorized": 0,
            "unauthorized_like": 1,
            "updated_unauthorized": 0,
            "reactivated_authorized": 0,
            "parser_phone_before": str(settings.PARSER_ONLY_PHONE or ""),
            "parser_phone_after": str(settings.PARSER_ONLY_PHONE or ""),
            "parser_reassigned": False,
            "claims_cleared": 0,
            "worker_heartbeats_cleared": 0,
            "results": [
                {
                    "phone": normalized_phone,
                    "user_id": user_id,
                    "account_status": "error",
                    "health_status": "unknown",
                    "lifecycle_stage": "uploaded",
                    "authorized": False,
                    "probe_status": "metadata_api_credentials_missing",
                }
            ],
        }
        await record_onboarding_step(
            normalized_phone,
            user_id=user_id,
            step_key="auth_check",
            actor=actor,
            source=source,
            channel=channel,
            result="metadata_api_credentials_missing",
            notes="В JSON нет собственного app_id/app_hash для этого аккаунта.",
            payload=report,
        )
        return report
    app_id, app_hash = metadata_api_credentials(metadata_path)
    conflicts = (
        find_api_credential_conflicts(
            settings.sessions_path,
            user_id=int(target_user_id),
            expected_phone=normalized_phone,
            app_id=int(app_id or 0),
            app_hash=str(app_hash or ""),
        )
        if target_user_id is not None and app_id is not None and app_hash
        else []
    )
    proxy_preflight = await ensure_live_proxy_binding(normalized_phone, user_id=user_id)
    if not bool(proxy_preflight.get("ok")):
        report = {
            "count": 1,
            "authorized": 0,
            "unauthorized_like": 1,
            "updated_unauthorized": 0,
            "reactivated_authorized": 0,
            "parser_phone_before": str(settings.PARSER_ONLY_PHONE or ""),
            "parser_phone_after": str(settings.PARSER_ONLY_PHONE or ""),
            "parser_reassigned": False,
            "claims_cleared": 0,
            "worker_heartbeats_cleared": 0,
            "proxy_preflight": proxy_preflight,
            "results": [
                {
                    "phone": normalized_phone,
                    "user_id": user_id,
                    "account_status": "error",
                    "health_status": "unknown",
                    "lifecycle_stage": "uploaded",
                    "authorized": False,
                    "probe_status": "proxy_unavailable",
                }
            ],
        }
        await record_onboarding_step(
            normalized_phone,
            user_id=user_id,
            step_key="auth_check",
            actor=actor,
            source=source,
            channel=channel,
            result="proxy_unavailable",
            notes=(
                "Не удалось подобрать живой уникальный прокси. "
                "Загрузите новые прокси и повторите проверку."
            ),
            payload=report,
        )
        return report
    report = await audit_sessions(
        phone=normalized_phone,
        user_id=user_id,
        mark_unauthorized=True,
        reactivate_authorized=True,
        authorized_stage="",
        authorized_status="active",
        authorized_health="alive",
        set_parser_first_authorized=bool(set_parser_first_authorized),
        clear_worker_claims=bool(clear_worker_claims),
        stage_actor="ops_api_auth_refresh_one",
    )
    report["proxy_preflight"] = proxy_preflight
    if conflicts:
        report["api_credential_conflicts"] = conflicts
    first = dict((report.get("results") or [{}])[0] or {})
    if bool(first.get("authorized")):
        async with async_session() as session:
            query = select(Account).where(Account.phone == normalized_phone)
            if user_id is not None:
                query = query.where(Account.user_id == user_id)
            result = await session.execute(query)
            account = result.scalar_one_or_none()
            if account is not None and (account.lifecycle_stage or "uploaded") in {
                "uploaded",
                "packaging_error",
                "restricted",
                "expired",
            }:
                account.lifecycle_stage = "auth_verified"
                await session.commit()
                first["lifecycle_stage"] = "auth_verified"
                report["results"] = [first]
    probe_status = str(first.get("probe_status") or "unknown")
    step_result = "authorized" if bool(first.get("authorized")) else probe_status
    step_notes = notes or f"Проверка доступа: {probe_status}"
    await record_onboarding_step(
        normalized_phone,
        user_id=user_id,
        step_key="auth_check",
        actor=actor,
        source=source,
        channel=channel,
        result=step_result,
        notes=step_notes,
        payload=report,
    )
    return report


async def create_profile_draft_for_phone(
    phone: str,
    *,
    user_id: int | None = None,
    style: str | None = None,
    variant_count: int = 3,
    actor: str = "ops_api_profile_draft",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    return await generate_profile_draft(
        phone,
        user_id=user_id,
        style=style,
        variant_count=variant_count,
        actor=actor,
        source=source,
        channel=channel,
    )


async def apply_profile_draft_for_phone(
    phone: str,
    *,
    user_id: int | None = None,
    draft_id: int | None = None,
    selected_variant: int | None = None,
    actor: str = "ops_api_profile_apply",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    return await apply_profile_draft(
        phone,
        user_id=user_id,
        draft_id=draft_id,
        selected_variant=selected_variant,
        actor=actor,
        source=source,
        channel=channel,
    )


async def create_channel_draft_for_phone(
    phone: str,
    *,
    user_id: int | None = None,
    style: str | None = None,
    variant_count: int = 3,
    actor: str = "ops_api_channel_draft",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    return await generate_channel_draft(
        phone,
        user_id=user_id,
        style=style,
        variant_count=variant_count,
        actor=actor,
        source=source,
        channel=channel,
    )


async def apply_channel_draft_for_phone(
    phone: str,
    *,
    user_id: int | None = None,
    draft_id: int | None = None,
    selected_variant: int | None = None,
    actor: str = "ops_api_channel_apply",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    return await apply_channel_draft(
        phone,
        user_id=user_id,
        draft_id=draft_id,
        selected_variant=selected_variant,
        actor=actor,
        source=source,
        channel=channel,
    )


async def create_content_draft_for_phone(
    phone: str,
    *,
    user_id: int | None = None,
    variant_count: int = 3,
    actor: str = "ops_api_content_draft",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    return await generate_content_draft(
        phone,
        user_id=user_id,
        variant_count=variant_count,
        actor=actor,
        source=source,
        channel=channel,
    )


async def apply_content_draft_for_phone(
    phone: str,
    *,
    user_id: int | None = None,
    draft_id: int | None = None,
    selected_variant: int | None = None,
    actor: str = "ops_api_content_apply",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    return await apply_content_draft(
        phone,
        user_id=user_id,
        draft_id=draft_id,
        selected_variant=selected_variant,
        actor=actor,
        source=source,
        channel=channel,
    )


async def assign_account_role_for_phone(
    phone: str,
    *,
    role: str,
    user_id: int | None = None,
    actor: str = "ops_api_assign_role",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    return await assign_account_role(
        phone,
        role=role,
        user_id=user_id,
        actor=actor,
        source=source,
        channel=channel,
    )


async def create_comment_draft_for_phone(
    *,
    phone: str,
    post_text: str,
    user_id: int | None = None,
    persona_style: str | None = None,
    actor: str = "ops_api_comment_draft",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    return await create_comment_draft(
        phone=phone,
        post_text=post_text,
        user_id=user_id,
        persona_style=persona_style,
        actor=actor,
        source=source,
        channel=channel,
    )


async def review_comment_draft_by_id(
    *,
    draft_id: int,
    user_id: int | None = None,
    actor: str = "ops_api_comment_review",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    return await review_comment_draft(
        draft_id=draft_id,
        user_id=user_id,
        actor=actor,
        source=source,
        channel=channel,
    )


async def approve_comment_draft_by_id(
    *,
    draft_id: int,
    user_id: int | None = None,
    actor: str = "ops_api_comment_approve",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    return await approve_comment_draft(
        draft_id=draft_id,
        user_id=user_id,
        actor=actor,
        source=source,
        channel=channel,
    )


async def queue_recovery(
    *,
    user_id: int | None = None,
    migrate_layout: bool = False,
    set_parser_first_authorized: bool = True,
    clear_worker_claims: bool = True,
    authorized_stage: str = "auth_verified",
    authorized_status: str = "active",
    authorized_health: str = "alive",
) -> dict[str, Any]:
    await task_queue.connect()
    payload = {
        "user_id": user_id,
        "migrate_layout": bool(migrate_layout),
        "set_parser_first_authorized": bool(set_parser_first_authorized),
        "clear_worker_claims": bool(clear_worker_claims),
        "authorized_stage": authorized_stage,
        "authorized_status": authorized_status,
        "authorized_health": authorized_health,
    }
    task_id = await task_queue.enqueue("recovery_tasks", payload)
    return {"ok": True, "task_id": task_id, "payload": payload}


async def apply_channel_review(
    channel_id: int,
    *,
    review_state: str,
    publish_mode: str | None = None,
    permission_basis: str | None = None,
    review_note: str | None = None,
    user_id: int | None = None,
) -> bool:
    return await channel_db.set_review_state_by_db_id(
        channel_id,
        review_state=review_state,
        publish_mode=publish_mode,
        permission_basis=permission_basis,
        review_note=review_note,
        user_id=user_id,
    )


async def queue_parser_keyword_search(
    *,
    keywords: list[str],
    user_id: int | None = None,
    topic: str | None = None,
    min_subscribers: int | None = None,
    require_comments: bool | None = None,
    require_russian: bool | None = None,
    stage1_limit: int | None = None,
) -> dict[str, Any]:
    cleaned = [str(keyword).strip() for keyword in (keywords or []) if str(keyword).strip()]
    if not cleaned:
        raise ValueError("keywords_required")
    await task_queue.connect()
    payload = {
        "kind": "keyword_search",
        "keywords": cleaned,
        "user_id": user_id,
        "topic": topic,
        "min_subscribers": min_subscribers,
        "require_comments": require_comments,
        "require_russian": require_russian,
        "stage1_limit": stage1_limit,
    }
    task_id = await task_queue.enqueue("channel_discovery", payload)
    return {"ok": True, "task_id": task_id, "payload": payload}


async def queue_parser_similar_search(
    *,
    user_id: int | None = None,
    min_subscribers: int | None = None,
) -> dict[str, Any]:
    await task_queue.connect()
    payload = {
        "kind": "similar_search",
        "user_id": user_id,
        "min_subscribers": min_subscribers,
    }
    task_id = await task_queue.enqueue("channel_discovery", payload)
    return {"ok": True, "task_id": task_id, "payload": payload}


async def queue_manual_add_channel(
    *,
    ref: str,
    user_id: int | None = None,
) -> dict[str, Any]:
    cleaned = str(ref or "").strip()
    if not cleaned:
        raise ValueError("channel_ref_required")
    await task_queue.connect()
    payload = {
        "kind": "manual_add",
        "ref": cleaned,
        "user_id": user_id,
    }
    task_id = await task_queue.enqueue("channel_discovery", payload)
    return {"ok": True, "task_id": task_id, "payload": payload}


async def set_autopilot_enabled(enabled: bool) -> dict[str, Any]:
    await redis_state.connect()
    await redis_state.set_runtime_flag("autopilot_enabled", "1" if enabled else "0")
    return {"ok": True, "enabled": bool(enabled)}


async def send_digest_summary(*, user_id: int | None = None) -> dict[str, Any]:
    return await send_daily_digest_summary(user_id=user_id)


async def run_user_state_reset(
    *,
    user_id: int | None = None,
    actor: str = "ops_api_reset_user_state",
    dry_run: bool = False,
) -> dict[str, Any]:
    return await reset_user_state(user_id=user_id, actor=actor, dry_run=dry_run)


async def start_account_onboarding(
    phone: str,
    *,
    user_id: int | None = None,
    mode: str = "bot",
    channel: str = "bot",
    actor: str = "onboarding:start",
    notes: str = "",
) -> dict[str, Any]:
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        raise ValueError("invalid_phone")
    return await start_onboarding_run(
        normalized_phone,
        user_id=user_id,
        mode=mode,
        channel=channel,
        actor=actor,
        notes=notes,
    )


async def record_account_onboarding_step(
    phone: str,
    *,
    user_id: int | None = None,
    step_key: str,
    actor: str = "system",
    source: str = "bot",
    channel: str = "bot",
    result: str = "ok",
    notes: str = "",
    payload: dict[str, Any] | None = None,
    run_status: str | None = None,
) -> dict[str, Any]:
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        raise ValueError("invalid_phone")
    return await record_onboarding_step(
        normalized_phone,
        user_id=user_id,
        step_key=step_key,
        actor=actor,
        source=source,
        channel=channel,
        result=result,
        notes=notes,
        payload=payload,
        run_status=run_status,
    )


async def get_account_onboarding(
    phone: str,
    *,
    user_id: int | None = None,
    limit_steps: int = 20,
) -> dict[str, Any]:
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        raise ValueError("invalid_phone")
    return await get_onboarding_status(normalized_phone, user_id=user_id, limit_steps=limit_steps)


async def list_account_onboarding_runs(
    *,
    user_id: int | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    return await list_onboarding_runs(user_id=user_id, status=status, limit=limit)


async def set_account_gate_stage(
    account_id: int,
    *,
    to_stage: str,
    actor: str,
    reason: str,
    status: str | None = None,
    health_status: str | None = None,
    user_id: int | None = None,
) -> Account | None:
    async with async_session() as session:
        query = select(Account).where(Account.id == account_id)
        if user_id is not None:
            query = query.where(Account.user_id == user_id)
        result = await session.execute(query)
        account = result.scalar_one_or_none()
        if account is None:
            return None
        old_stage = account.lifecycle_stage
        account.lifecycle_stage = to_stage
        if status is not None:
            account.status = status
        if health_status is not None:
            account.health_status = health_status
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
        await session.commit()
        await session.refresh(account)
        return account


async def list_tenant_user_ids() -> list[int]:
    async with async_session() as session:
        result = await session.execute(
            select(User.id).join(Account, Account.user_id == User.id).group_by(User.id).order_by(User.id.asc())
        )
        return [int(row[0]) for row in result.all()]


async def parser_task_status(task_id: str) -> dict[str, Any] | None:
    await redis_state.connect()
    return await redis_state.get_json_hash_value("parser_tasks", task_id)


async def recovery_task_status(task_id: str) -> dict[str, Any] | None:
    await redis_state.connect()
    return await redis_state.get_json_hash_value("recovery_tasks", task_id)


async def store_task_status(namespace: str, task_id: str, payload: dict[str, Any]) -> None:
    await redis_state.connect()
    await redis_state.set_json_hash_value(namespace, task_id, payload)
