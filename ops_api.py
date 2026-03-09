from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any

import jwt
import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from channels.channel_db import ChannelDB
from config import settings
from core.account_audit import (
    collect_account_audit,
    collect_json_credentials_audit,
    collect_proxy_observability,
    collect_session_topology_audit,
    quarantine_session_duplicates,
    reconcile_stale_lifecycle,
)
from core.ops_service import (
    apply_channel_review,
    apply_channel_draft_for_phone,
    apply_content_draft_for_phone,
    apply_profile_draft_for_phone,
    approve_comment_draft_by_id,
    assign_account_role_for_phone,
    create_channel_draft_for_phone,
    create_comment_draft_for_phone,
    create_content_draft_for_phone,
    create_profile_draft_for_phone,
    get_account_onboarding,
    list_account_onboarding_runs,
    normalize_phone,
    parser_task_status,
    queue_manual_add_channel,
    queue_packaging_bulk,
    queue_packaging_phone,
    queue_parser_keyword_search,
    queue_parser_similar_search,
    queue_recovery,
    record_account_onboarding_step,
    recovery_task_status,
    run_user_state_reset,
    run_auth_refresh,
    run_auth_refresh_for_phone,
    review_comment_draft_by_id,
    send_digest_summary,
    set_account_gate_stage,
    set_autopilot_enabled,
    start_account_onboarding,
)
from core.redis_state import redis_state
from core.task_queue import task_queue
from core.usage_events import log_usage_event
from storage.models import Account, Channel, Workspace, Tenant
from storage.sqlite_db import apply_session_rls_context, async_session, dispose_engine, init_db
from utils.runtime_snapshot import collect_runtime_snapshot


channel_db = ChannelDB()


@dataclass
class TenantContext:
    user_id: int
    tenant_id: int
    workspace_id: int | None
    role: str
    token_type: str


def _json(data: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=data, status_code=status_code)


def _bearer_token(request: Request) -> str:
    return request.headers.get("Authorization", "").removeprefix("Bearer ").strip()


async def _read_json(request: Request) -> dict[str, Any]:
    if request.method in {"GET", "HEAD"}:
        return {}
    try:
        body = await request.body()
        if not body:
            return {}
        payload = await request.json()
        return payload if isinstance(payload, dict) else {}
    except (JSONDecodeError, ValueError):
        return {}


async def require_internal_access(request: Request) -> None:
    token = str(settings.OPS_API_TOKEN or "").strip()
    if not token:
        return
    provided = _bearer_token(request)
    if provided != token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


async def get_tenant_context(request: Request) -> TenantContext:
    token = _bearer_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer_token")
    internal = str(settings.OPS_API_TOKEN or "").strip()
    if internal and token == internal:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="jwt_required")
    if not settings.JWT_ACCESS_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="jwt_not_configured")
    try:
        payload = jwt.decode(token, settings.JWT_ACCESS_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_jwt") from exc

    token_type = str(payload.get("type") or "access")
    if token_type != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token_type")

    raw_sub = payload.get("sub")
    raw_tenant = payload.get("tenant_id")
    if raw_sub is None or raw_tenant is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="tenant_claims_missing")
    try:
        ctx = TenantContext(
            user_id=int(raw_sub),
            tenant_id=int(raw_tenant),
            workspace_id=int(payload["workspace_id"]) if payload.get("workspace_id") is not None else None,
            role=str(payload.get("role") or "member"),
            token_type=token_type,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_tenant_claims") from exc

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, ctx.tenant_id, ctx.user_id)
            result = await session.execute(select(Tenant).where(Tenant.id == ctx.tenant_id))
            tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant_not_found")
    if str(tenant.status or "active") != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant_suspended")
    request.state.tenant_context = ctx
    return ctx


async def get_tenant_session(ctx: TenantContext = Depends(get_tenant_context)):
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, ctx.tenant_id, ctx.user_id)
            yield session


def _query_user_id(request: Request) -> int | None:
    raw = request.query_params.get("user_id")
    return int(raw) if raw and raw.isdigit() else None


async def handle_health() -> JSONResponse:
    return _json({"ok": True})


async def handle_runtime_summary() -> JSONResponse:
    summary = await collect_runtime_snapshot(
        initialize_db=False,
        close_backends=False,
        dispose_db=False,
    )
    autopilot = await redis_state.get_runtime_flag("autopilot_enabled", "0")
    summary["autopilot"] = {"enabled": autopilot == "1"}
    return _json(summary)


async def handle_accounts() -> JSONResponse:
    async with async_session() as session:
        result = await session.execute(select(Account).order_by(Account.created_at.asc()))
        accounts = list(result.scalars().all())
    payload = [
        {
            "id": account.id,
            "phone": account.phone,
            "user_id": account.user_id,
            "status": account.status,
            "health_status": account.health_status,
            "lifecycle_stage": account.lifecycle_stage,
            "account_role": account.account_role,
            "proxy_id": account.proxy_id,
            "comments_today": account.comments_today,
            "total_comments": account.total_comments,
            "last_active_at": account.last_active_at.isoformat() if account.last_active_at else None,
            "restriction_reason": account.restriction_reason,
        }
        for account in accounts
    ]
    return _json({"items": payload, "total": len(payload)})


async def handle_channels() -> JSONResponse:
    async with async_session() as session:
        result = await session.execute(select(Channel).order_by(Channel.created_at.desc()))
        channels = list(result.scalars().all())
    payload = [
        {
            "id": channel.id,
            "user_id": channel.user_id,
            "telegram_id": channel.telegram_id,
            "title": channel.title,
            "username": channel.username,
            "subscribers": channel.subscribers,
            "topic": channel.topic,
            "review_state": channel.review_state,
            "publish_mode": channel.publish_mode,
            "permission_basis": channel.permission_basis,
            "comments_enabled": channel.comments_enabled,
            "is_active": channel.is_active,
            "is_blacklisted": channel.is_blacklisted,
        }
        for channel in channels
    ]
    return _json({"items": payload, "total": len(payload)})


async def handle_workers() -> JSONResponse:
    summary = await collect_runtime_snapshot(
        initialize_db=False,
        close_backends=False,
        dispose_db=False,
    )
    return _json(summary["workers"])


async def handle_queues() -> JSONResponse:
    summary = await collect_runtime_snapshot(
        initialize_db=False,
        close_backends=False,
        dispose_db=False,
    )
    return _json(summary["queues"])


async def handle_account_audit(request: Request) -> JSONResponse:
    payload = await collect_account_audit(user_id=_query_user_id(request))
    return _json(payload)


async def handle_json_credentials_audit(request: Request) -> JSONResponse:
    payload = await collect_json_credentials_audit(user_id=_query_user_id(request))
    return _json(payload)


async def handle_session_topology_audit(request: Request) -> JSONResponse:
    payload = await collect_session_topology_audit(user_id=_query_user_id(request))
    return _json(payload)


async def handle_session_topology_quarantine(request: Request) -> JSONResponse:
    body = await _read_json(request)
    payload = await quarantine_session_duplicates(
        user_id=body.get("user_id"),
        phones=body.get("phones") if isinstance(body.get("phones"), list) else None,
        dry_run=bool(body.get("dry_run", False)),
    )
    return _json(payload)


async def handle_proxy_audit(request: Request) -> JSONResponse:
    payload = await collect_proxy_observability(user_id=_query_user_id(request))
    return _json(payload)


async def handle_reset_user_state(request: Request) -> JSONResponse:
    body = await _read_json(request)
    payload = await run_user_state_reset(
        user_id=body.get("user_id"),
        actor=str(body.get("actor") or "ops_api_reset_user_state"),
        dry_run=bool(body.get("dry_run", False)),
    )
    return _json(payload)


async def handle_reconcile_lifecycle(request: Request) -> JSONResponse:
    body = await _read_json(request)
    payload = await reconcile_stale_lifecycle(
        user_id=body.get("user_id"),
        actor=str(body.get("actor") or "ops_api_reconcile"),
        dry_run=bool(body.get("dry_run", False)),
    )
    return _json(payload)


async def handle_queue_packaging(phone: str) -> JSONResponse:
    phone = normalize_phone(phone)
    if not phone:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    try:
        result = await queue_packaging_phone(phone)
    except ValueError:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    except RuntimeError as exc:
        error = str(exc)
        if error == "human_gated_packaging_enabled":
            return _json({"ok": False, "error": error, "next_action": "use_profile_draft"}, status_code=409)
        return _json({"ok": False, "error": error}, status_code=404 if error == "account_not_found" else 409)
    return _json(result)


async def handle_profile_draft(phone: str, request: Request) -> JSONResponse:
    phone = normalize_phone(phone)
    if not phone:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    body = await _read_json(request)
    try:
        payload = await create_profile_draft_for_phone(
            phone,
            user_id=body.get("user_id"),
            style=body.get("style"),
            variant_count=int(body.get("variant_count", 3)),
            actor=str(body.get("actor") or "ops_api_profile_draft"),
            source=str(body.get("source") or "ops_api"),
            channel=str(body.get("channel") or "ops_api"),
        )
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        error = str(exc)
        return _json({"ok": False, "error": error}, status_code=404 if error == "account_not_found" else 409)
    return _json(payload)


async def handle_profile_apply(phone: str, request: Request) -> JSONResponse:
    phone = normalize_phone(phone)
    if not phone:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    body = await _read_json(request)
    try:
        payload = await apply_profile_draft_for_phone(
            phone,
            user_id=body.get("user_id"),
            draft_id=body.get("draft_id"),
            selected_variant=body.get("selected_variant"),
            actor=str(body.get("actor") or "ops_api_profile_apply"),
            source=str(body.get("source") or "ops_api"),
            channel=str(body.get("channel") or "ops_api"),
        )
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        error = str(exc)
        status_code = 404 if error in {"account_not_found", "profile_draft_not_found"} else 409
        return _json({"ok": False, "error": error}, status_code=status_code)
    return _json(payload)


async def handle_channel_draft(phone: str, request: Request) -> JSONResponse:
    phone = normalize_phone(phone)
    if not phone:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    body = await _read_json(request)
    try:
        payload = await create_channel_draft_for_phone(
            phone,
            user_id=body.get("user_id"),
            style=body.get("style"),
            variant_count=int(body.get("variant_count", 3)),
            actor=str(body.get("actor") or "ops_api_channel_draft"),
            source=str(body.get("source") or "ops_api"),
            channel=str(body.get("channel") or "ops_api"),
        )
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        error = str(exc)
        return _json({"ok": False, "error": error}, status_code=404 if error == "account_not_found" else 409)
    return _json(payload)


async def handle_channel_apply(phone: str, request: Request) -> JSONResponse:
    phone = normalize_phone(phone)
    if not phone:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    body = await _read_json(request)
    try:
        payload = await apply_channel_draft_for_phone(
            phone,
            user_id=body.get("user_id"),
            draft_id=body.get("draft_id"),
            selected_variant=body.get("selected_variant"),
            actor=str(body.get("actor") or "ops_api_channel_apply"),
            source=str(body.get("source") or "ops_api"),
            channel=str(body.get("channel") or "ops_api"),
        )
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        error = str(exc)
        status_code = 404 if error in {"account_not_found", "channel_draft_not_found"} else 409
        return _json({"ok": False, "error": error}, status_code=status_code)
    return _json(payload)


async def handle_content_draft(phone: str, request: Request) -> JSONResponse:
    phone = normalize_phone(phone)
    if not phone:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    body = await _read_json(request)
    try:
        payload = await create_content_draft_for_phone(
            phone,
            user_id=body.get("user_id"),
            variant_count=int(body.get("variant_count", 3)),
            actor=str(body.get("actor") or "ops_api_content_draft"),
            source=str(body.get("source") or "ops_api"),
            channel=str(body.get("channel") or "ops_api"),
        )
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        error = str(exc)
        return _json({"ok": False, "error": error}, status_code=404 if error == "account_not_found" else 409)
    return _json(payload)


async def handle_content_apply(phone: str, request: Request) -> JSONResponse:
    phone = normalize_phone(phone)
    if not phone:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    body = await _read_json(request)
    try:
        payload = await apply_content_draft_for_phone(
            phone,
            user_id=body.get("user_id"),
            draft_id=body.get("draft_id"),
            selected_variant=body.get("selected_variant"),
            actor=str(body.get("actor") or "ops_api_content_apply"),
            source=str(body.get("source") or "ops_api"),
            channel=str(body.get("channel") or "ops_api"),
        )
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        error = str(exc)
        status_code = 404 if error in {"account_not_found", "content_draft_not_found"} else 409
        return _json({"ok": False, "error": error}, status_code=status_code)
    return _json(payload)


async def handle_assign_role(phone: str, request: Request) -> JSONResponse:
    phone = normalize_phone(phone)
    if not phone:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    body = await _read_json(request)
    try:
        payload = await assign_account_role_for_phone(
            phone,
            role=str(body.get("role") or ""),
            user_id=body.get("user_id"),
            actor=str(body.get("actor") or "ops_api_assign_role"),
            source=str(body.get("source") or "ops_api"),
            channel=str(body.get("channel") or "ops_api"),
        )
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        error = str(exc)
        return _json({"ok": False, "error": error}, status_code=404 if error == "account_not_found" else 409)
    return _json(payload)


async def handle_comment_draft(request: Request) -> JSONResponse:
    body = await _read_json(request)
    try:
        payload = await create_comment_draft_for_phone(
            phone=str(body.get("phone") or ""),
            post_text=str(body.get("post_text") or ""),
            user_id=body.get("user_id"),
            persona_style=body.get("persona_style"),
            actor=str(body.get("actor") or "ops_api_comment_draft"),
            source=str(body.get("source") or "ops_api"),
            channel=str(body.get("channel") or "ops_api"),
        )
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        error = str(exc)
        return _json({"ok": False, "error": error}, status_code=404 if error == "account_not_found" else 409)
    return _json(payload)


async def handle_comment_review(request: Request) -> JSONResponse:
    body = await _read_json(request)
    if not body.get("draft_id"):
        return _json({"ok": False, "error": "draft_id_required"}, status_code=400)
    try:
        payload = await review_comment_draft_by_id(
            draft_id=int(body.get("draft_id")),
            user_id=body.get("user_id"),
            actor=str(body.get("actor") or "ops_api_comment_review"),
            source=str(body.get("source") or "ops_api"),
            channel=str(body.get("channel") or "ops_api"),
        )
    except RuntimeError as exc:
        error = str(exc)
        return _json({"ok": False, "error": error}, status_code=404 if error == "comment_draft_not_found" else 409)
    return _json(payload)


async def handle_comment_approve(request: Request) -> JSONResponse:
    body = await _read_json(request)
    if not body.get("draft_id"):
        return _json({"ok": False, "error": "draft_id_required"}, status_code=400)
    try:
        payload = await approve_comment_draft_by_id(
            draft_id=int(body.get("draft_id")),
            user_id=body.get("user_id"),
            actor=str(body.get("actor") or "ops_api_comment_approve"),
            source=str(body.get("source") or "ops_api"),
            channel=str(body.get("channel") or "ops_api"),
        )
    except RuntimeError as exc:
        error = str(exc)
        return _json({"ok": False, "error": error}, status_code=404 if error == "comment_draft_not_found" else 409)
    return _json(payload)


async def handle_queue_packaging_bulk(request: Request) -> JSONResponse:
    body = await _read_json(request)
    try:
        result = await queue_packaging_bulk(user_id=body.get("user_id"))
    except RuntimeError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=409)
    return _json(result)


async def handle_auth_refresh(request: Request) -> JSONResponse:
    body = await _read_json(request)
    report = await run_auth_refresh(
        user_id=body.get("user_id"),
        set_parser_first_authorized=bool(body.get("set_parser_first_authorized", True)),
        clear_worker_claims=bool(body.get("clear_worker_claims", True)),
    )
    return _json(report)


async def handle_auth_refresh_all(request: Request) -> JSONResponse:
    return await handle_auth_refresh(request)


async def handle_auth_refresh_one(phone: str, request: Request) -> JSONResponse:
    phone = normalize_phone(phone)
    if not phone:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    body = await _read_json(request)
    try:
        report = await run_auth_refresh_for_phone(
            phone,
            user_id=body.get("user_id"),
            actor=str(body.get("actor") or "ops_api_auth_refresh_one"),
            source=str(body.get("source") or "ops_api"),
            channel=str(body.get("channel") or "ops_api"),
            notes=str(body.get("notes") or ""),
            set_parser_first_authorized=bool(body.get("set_parser_first_authorized", False)),
            clear_worker_claims=bool(body.get("clear_worker_claims", False)),
        )
    except ValueError:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    return _json(report)


async def handle_account_gate(account_id: int, request: Request) -> JSONResponse:
    body = await _read_json(request)
    account = await set_account_gate_stage(
        account_id,
        to_stage=str(body.get("to_stage") or ""),
        actor=str(body.get("actor") or "ops_api_gate"),
        reason=str(body.get("reason") or "stage update via ops api"),
        status=body.get("status"),
        health_status=body.get("health_status"),
        user_id=body.get("user_id"),
    )
    if account is None:
        return _json({"ok": False, "error": "account_not_found"}, status_code=404)
    return _json(
        {
            "ok": True,
            "account_id": account.id,
            "phone": account.phone,
            "lifecycle_stage": account.lifecycle_stage,
            "status": account.status,
            "health_status": account.health_status,
        }
    )


async def handle_recover(request: Request) -> JSONResponse:
    body = await _read_json(request)
    report = await queue_recovery(
        user_id=body.get("user_id"),
        migrate_layout=bool(body.get("migrate_layout", False)),
        set_parser_first_authorized=bool(body.get("set_parser_first_authorized", True)),
        clear_worker_claims=bool(body.get("clear_worker_claims", True)),
        authorized_stage=str(body.get("authorized_stage") or "active_commenting"),
        authorized_status=str(body.get("authorized_status") or "active"),
        authorized_health=str(body.get("authorized_health") or "alive"),
    )
    return _json(report)


async def handle_recovery_task_status(task_id: str) -> JSONResponse:
    payload = await recovery_task_status(str(task_id or "").strip())
    if payload is None:
        return _json({"ok": False, "error": "task_not_found"}, status_code=404)
    return _json(payload)


async def handle_channel_review(channel_id: int, request: Request) -> JSONResponse:
    body = await _read_json(request)
    ok = await apply_channel_review(
        channel_id,
        review_state=str(body.get("review_state") or "candidate"),
        publish_mode=body.get("publish_mode"),
        permission_basis=body.get("permission_basis"),
        review_note=body.get("review_note"),
        user_id=body.get("user_id"),
    )
    if not ok:
        return _json({"ok": False, "error": "channel_not_found"}, status_code=404)
    return _json({"ok": True, "channel_id": channel_id})


async def handle_parser_search(request: Request) -> JSONResponse:
    body = await _read_json(request)
    try:
        report = await queue_parser_keyword_search(
            keywords=list(body.get("keywords") or []),
            user_id=body.get("user_id"),
            topic=body.get("topic"),
            min_subscribers=body.get("min_subscribers"),
            require_comments=body.get("require_comments"),
            require_russian=body.get("require_russian"),
            stage1_limit=body.get("stage1_limit"),
        )
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)
    return _json(report)


async def handle_parser_similar(request: Request) -> JSONResponse:
    body = await _read_json(request)
    report = await queue_parser_similar_search(
        user_id=body.get("user_id"),
        min_subscribers=body.get("min_subscribers"),
    )
    return _json(report)


async def handle_parser_manual_add(request: Request) -> JSONResponse:
    body = await _read_json(request)
    try:
        report = await queue_manual_add_channel(
            ref=str(body.get("ref") or ""),
            user_id=body.get("user_id"),
        )
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)
    return _json(report)


async def handle_parser_run_discovery(request: Request) -> JSONResponse:
    body = await _read_json(request)
    kind = str(body.get("kind") or "keyword_search").strip()
    if kind == "keyword_search":
        return await handle_parser_search(request)
    if kind == "similar_search":
        return await handle_parser_similar(request)
    if kind == "manual_add":
        return await handle_parser_manual_add(request)
    return _json({"ok": False, "error": "unknown_parser_task_kind"}, status_code=400)


async def handle_parser_task_status(task_id: str) -> JSONResponse:
    payload = await parser_task_status(str(task_id or "").strip())
    if payload is None:
        return _json({"ok": False, "error": "task_not_found"}, status_code=404)
    return _json(payload)


async def handle_autopilot_toggle(request: Request) -> JSONResponse:
    body = await _read_json(request)
    enabled = bool(body.get("enabled", True))
    return _json(await set_autopilot_enabled(enabled))


async def handle_digest_send_summary(request: Request) -> JSONResponse:
    body = await _read_json(request)
    payload = await send_digest_summary(user_id=body.get("user_id"))
    return _json(payload, status_code=200 if payload.get("ok") else 409)


async def handle_onboarding_start(phone: str, request: Request) -> JSONResponse:
    phone = normalize_phone(phone)
    if not phone:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    body = await _read_json(request)
    try:
        payload = await start_account_onboarding(
            phone,
            user_id=body.get("user_id"),
            mode=str(body.get("mode") or "bot"),
            channel=str(body.get("channel") or "bot"),
            actor=str(body.get("actor") or "ops_api_onboarding_start"),
            notes=str(body.get("notes") or ""),
        )
    except ValueError:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    except RuntimeError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=404)
    return _json(payload)


async def handle_onboarding_step(phone: str, request: Request) -> JSONResponse:
    phone = normalize_phone(phone)
    if not phone:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    body = await _read_json(request)
    step_key = str(body.get("step_key") or "").strip()
    if not step_key:
        return _json({"ok": False, "error": "step_key_required"}, status_code=400)
    try:
        payload = await record_account_onboarding_step(
            phone,
            user_id=body.get("user_id"),
            step_key=step_key,
            actor=str(body.get("actor") or "ops_api_onboarding_step"),
            source=str(body.get("source") or "bot"),
            channel=str(body.get("channel") or "bot"),
            result=str(body.get("result") or "ok"),
            notes=str(body.get("notes") or ""),
            payload=body.get("payload") if isinstance(body.get("payload"), dict) else None,
            run_status=body.get("run_status"),
        )
    except ValueError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=404)
    return _json(payload)


async def handle_onboarding_get(phone: str, request: Request) -> JSONResponse:
    phone = normalize_phone(phone)
    if not phone:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    user_id_raw = request.query_params.get("user_id")
    limit_raw = request.query_params.get("limit_steps")
    user_id = int(user_id_raw) if user_id_raw and user_id_raw.isdigit() else None
    limit_steps = int(limit_raw) if limit_raw and limit_raw.isdigit() else 20
    try:
        payload = await get_account_onboarding(phone, user_id=user_id, limit_steps=limit_steps)
    except ValueError:
        return _json({"ok": False, "error": "invalid_phone"}, status_code=400)
    except RuntimeError as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=404)
    return _json(payload)


async def handle_onboarding_runs(request: Request) -> JSONResponse:
    user_id_raw = request.query_params.get("user_id")
    status_value = request.query_params.get("status")
    limit_raw = request.query_params.get("limit")
    user_id = int(user_id_raw) if user_id_raw and user_id_raw.isdigit() else None
    limit = int(limit_raw) if limit_raw and limit_raw.isdigit() else 20
    items = await list_account_onboarding_runs(user_id=user_id, status=status_value, limit=limit)
    return _json({"items": items, "total": len(items)})


async def handle_workspaces(
    ctx: TenantContext = Depends(get_tenant_context),
    session=Depends(get_tenant_session),
) -> JSONResponse:
    # RLS on the request transaction scopes visible rows by tenant_id,
    # so this query intentionally does not add an explicit tenant WHERE clause.
    result = await session.execute(select(Workspace).order_by(Workspace.created_at.asc()))
    workspaces = list(result.scalars().all())
    await log_usage_event(
        ctx.tenant_id,
        "workspace.list",
        {
            "user_id": ctx.user_id,
            "workspace_id": ctx.workspace_id,
            "role": ctx.role,
            "count": len(workspaces),
        },
    )
    payload = [
        {
            "id": workspace.id,
            "name": workspace.name,
            "settings": workspace.settings,
            "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
        }
        for workspace in workspaces
    ]
    return _json({"items": payload, "total": len(payload)})


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await task_queue.connect()
    await redis_state.connect()
    try:
        yield
    finally:
        await redis_state.close()
        await task_queue.close()
        await dispose_engine()


def build_app() -> FastAPI:
    app = FastAPI(title="NEURO COMMENTING Ops API", lifespan=lifespan)

    internal = APIRouter(dependencies=[Depends(require_internal_access)])
    tenant = APIRouter()

    app.get("/healthz")(handle_health)
    app.get("/v1/workspaces")(handle_workspaces)

    internal.get("/v1/runtime/summary")(handle_runtime_summary)
    internal.get("/v1/accounts")(handle_accounts)
    internal.get("/v1/audit/accounts")(handle_account_audit)
    internal.get("/v1/audit/json-credentials")(handle_json_credentials_audit)
    internal.get("/v1/audit/sessions")(handle_session_topology_audit)
    internal.post("/v1/audit/sessions/quarantine")(handle_session_topology_quarantine)
    internal.post("/v1/sessions/quarantine-duplicates")(handle_session_topology_quarantine)
    internal.get("/v1/audit/proxies")(handle_proxy_audit)
    internal.post("/v1/reset/user-state")(handle_reset_user_state)
    internal.get("/v1/channels")(handle_channels)
    internal.get("/v1/workers")(handle_workers)
    internal.get("/v1/queues")(handle_queues)
    internal.post("/v1/accounts/{phone}/packaging")(handle_queue_packaging)
    internal.post("/v1/accounts/packaging/bulk")(handle_queue_packaging_bulk)
    internal.post("/v1/accounts/{phone}/profile-draft")(handle_profile_draft)
    internal.post("/v1/accounts/{phone}/profile-apply")(handle_profile_apply)
    internal.post("/v1/accounts/{phone}/channel-draft")(handle_channel_draft)
    internal.post("/v1/accounts/{phone}/channel-apply")(handle_channel_apply)
    internal.post("/v1/accounts/{phone}/content-draft")(handle_content_draft)
    internal.post("/v1/accounts/{phone}/content-apply")(handle_content_apply)
    internal.post("/v1/accounts/{phone}/assign-role")(handle_assign_role)
    internal.post("/v1/accounts/auth-refresh")(handle_auth_refresh)
    internal.post("/v1/accounts/auth-refresh-all")(handle_auth_refresh_all)
    internal.post("/v1/accounts/{phone}/auth-refresh")(handle_auth_refresh_one)
    internal.post("/v1/accounts/reconcile-lifecycle")(handle_reconcile_lifecycle)
    internal.post("/v1/accounts/{account_id}/stage")(handle_account_gate)
    internal.post("/v1/accounts/recover")(handle_recover)
    internal.get("/v1/accounts/recover/{task_id}")(handle_recovery_task_status)
    internal.post("/v1/accounts/{phone}/onboarding/start")(handle_onboarding_start)
    internal.post("/v1/accounts/{phone}/onboarding/step")(handle_onboarding_step)
    internal.get("/v1/accounts/{phone}/onboarding")(handle_onboarding_get)
    internal.get("/v1/onboarding/runs")(handle_onboarding_runs)
    internal.post("/v1/channels/{channel_id}/review")(handle_channel_review)
    internal.post("/v1/comments/draft")(handle_comment_draft)
    internal.post("/v1/comments/review")(handle_comment_review)
    internal.post("/v1/comments/approve")(handle_comment_approve)
    internal.post("/v1/parser/search")(handle_parser_search)
    internal.post("/v1/parser/similar")(handle_parser_similar)
    internal.post("/v1/channels/manual-add")(handle_parser_manual_add)
    internal.post("/v1/parser/run-discovery")(handle_parser_run_discovery)
    internal.get("/v1/parser/tasks/{task_id}")(handle_parser_task_status)
    internal.post("/v1/digest/send-summary")(handle_digest_send_summary)
    internal.post("/v1/autopilot/toggle")(handle_autopilot_toggle)

    app.include_router(internal)
    app.include_router(tenant)
    return app


app = build_app()


def main() -> None:
    uvicorn.run(app, host=settings.OPS_API_HOST, port=int(settings.OPS_API_PORT))


if __name__ == "__main__":
    main()
