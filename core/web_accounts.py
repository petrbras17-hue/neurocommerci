from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from core.account_audit import build_account_audit_records
from core.onboarding_service import record_onboarding_step
from scripts.session_auth_audit import audit_sessions
from core.proxy_manager import parse_proxy_line
from storage.models import Account, Proxy, Workspace
from utils.account_uploads import (
    get_account_upload_bundle,
    validate_and_normalize_account_metadata,
    write_normalized_metadata,
)
from utils.helpers import utcnow
from utils.proxy_bindings import get_proxy_pool_summary
from utils.session_topology import canonical_session_paths


class WebOnboardingError(RuntimeError):
    pass


def normalize_phone(raw: str | None) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return f"+{digits}" if digits else ""


def _phone_digits_from_filename(file_name: str, suffix: str) -> str:
    lower_name = str(file_name or "").lower()
    if not lower_name.endswith(suffix):
        raise WebOnboardingError(f"invalid_{suffix.lstrip('.')}_file")
    digits = "".join(ch for ch in Path(file_name).stem if ch.isdigit())
    if not digits:
        raise WebOnboardingError("phone_missing_in_file_name")
    return digits


async def get_workspace_runtime_user(session: AsyncSession, *, tenant_id: int, workspace_id: int) -> Workspace:
    workspace_result = await session.execute(
        select(Workspace).where(
            Workspace.id == int(workspace_id),
            Workspace.tenant_id == int(tenant_id),
        )
    )
    workspace = workspace_result.scalar_one_or_none()
    if workspace is None:
        raise WebOnboardingError("workspace_not_found")
    if workspace.runtime_user_id is None:
        raise WebOnboardingError("workspace_runtime_user_missing")
    return workspace


async def upsert_account_from_session_upload(
    session: AsyncSession,
    *,
    phone: str,
    session_file: str,
    runtime_user_id: int,
    tenant_id: int | None = None,
    workspace_id: int | None = None,
    reset_runtime_state: bool = False,
) -> tuple[Account, str]:
    result = await session.execute(select(Account).where(Account.phone == phone))
    account = result.scalar_one_or_none()

    if account is None:
        account = Account(
            phone=phone,
            session_file=session_file,
            user_id=runtime_user_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            status="active",
            health_status="unknown",
            lifecycle_stage="uploaded",
            restriction_reason=None,
            created_at=utcnow(),
        )
        session.add(account)
        await session.flush()
        return account, "created"

    if tenant_id is not None and account.tenant_id not in (None, tenant_id):
        raise WebOnboardingError("account_belongs_to_another_workspace")
    if workspace_id is not None and account.workspace_id not in (None, workspace_id):
        raise WebOnboardingError("account_belongs_to_another_workspace")

    changed = False
    if account.session_file != session_file:
        account.session_file = session_file
        changed = True
    if account.user_id != runtime_user_id:
        account.user_id = runtime_user_id
        changed = True
    if tenant_id is not None and account.tenant_id != tenant_id:
        account.tenant_id = tenant_id
        changed = True
    if workspace_id is not None and account.workspace_id != workspace_id:
        account.workspace_id = workspace_id
        changed = True
    if account.lifecycle_stage in {"orphaned", "", None}:
        account.lifecycle_stage = "uploaded"
        changed = True

    if reset_runtime_state:
        if account.status != "banned" and account.status != "active":
            account.status = "active"
            changed = True
        if account.health_status != "unknown":
            account.health_status = "unknown"
            changed = True
        if account.lifecycle_stage != "uploaded":
            account.lifecycle_stage = "uploaded"
            changed = True
        if account.restriction_reason is not None:
            account.restriction_reason = None
            changed = True
        if account.last_health_check is not None:
            account.last_health_check = None
            changed = True
        if account.capabilities_json is not None:
            account.capabilities_json = None
            changed = True

    return account, "updated" if changed else "existing"


async def save_uploaded_account_pair(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    session_filename: str,
    session_bytes: bytes,
    metadata_filename: str,
    metadata_bytes: bytes,
    actor: str = "web:account_upload",
) -> dict[str, Any]:
    workspace = await get_workspace_runtime_user(session, tenant_id=tenant_id, workspace_id=workspace_id)
    runtime_user_id = int(workspace.runtime_user_id)

    session_digits = _phone_digits_from_filename(session_filename, ".session")
    metadata_digits = _phone_digits_from_filename(metadata_filename, ".json")
    if session_digits != metadata_digits:
        raise WebOnboardingError("session_and_json_phone_mismatch")

    phone = normalize_phone(session_digits)
    if not phone:
        raise WebOnboardingError("invalid_phone")

    session_path, metadata_path = canonical_session_paths(settings.sessions_path, runtime_user_id, phone)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_bytes(session_bytes)

    try:
        payload = json.loads(metadata_bytes.decode("utf-8"))
    except Exception as exc:
        session_path.unlink(missing_ok=True)
        raise WebOnboardingError("invalid_json_payload") from exc

    normalized_payload = validate_and_normalize_account_metadata(
        payload,
        expected_phone=phone,
        expected_session_file=f"{session_digits}.session",
    )
    write_normalized_metadata(metadata_path, normalized_payload)

    account, db_status = await upsert_account_from_session_upload(
        session,
        phone=phone,
        session_file=f"{session_digits}.session",
        runtime_user_id=runtime_user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        reset_runtime_state=True,
    )
    await session.flush()

    await record_onboarding_step(
        phone,
        user_id=runtime_user_id,
        step_key="upload_session",
        actor=actor,
        source="web",
        channel="web",
        result="saved",
        notes=f"Загружен файл {session_digits}.session.",
        session=session,
    )
    await record_onboarding_step(
        phone,
        user_id=runtime_user_id,
        step_key="upload_metadata",
        actor=actor,
        source="web",
        channel="web",
        result="saved",
        notes=f"Загружен файл {metadata_digits}.json.",
        session=session,
    )
    bundle = get_account_upload_bundle(settings.sessions_path, runtime_user_id, phone)
    if bundle.ready:
        await record_onboarding_step(
            phone,
            user_id=runtime_user_id,
            step_key="upload_bundle",
            actor=actor,
            source="web",
            channel="web",
            result="ready",
            notes="Пара .session + .json собрана через web onboarding.",
            session=session,
        )

    return {
        "account_id": account.id,
        "phone": account.phone,
        "status": account.status,
        "health_status": account.health_status,
        "lifecycle_stage": account.lifecycle_stage,
        "bundle_ready": bundle.ready,
        "db_status": db_status,
    }


async def list_web_accounts(session: AsyncSession, *, tenant_id: int, workspace_id: int) -> dict[str, Any]:
    result = await session.execute(
        select(Account)
        .options(selectinload(Account.proxy))
        .where(Account.tenant_id == tenant_id, Account.workspace_id == workspace_id)
        .order_by(Account.created_at.asc(), Account.id.asc())
    )
    accounts = list(result.scalars().all())
    audit_items = build_account_audit_records(
        accounts,
        sessions_dir=settings.sessions_path,
        strict_proxy=bool(settings.STRICT_PROXY_PER_ACCOUNT),
    )
    items: list[dict[str, Any]] = []
    for row in audit_items:
        account = next((acc for acc in accounts if acc.id == row["id"]), None)
        proxy = account.proxy if account is not None else None
        items.append(
            {
                "id": row["id"],
                "phone": row["phone"],
                "proxy": proxy.url if proxy is not None else None,
                "proxy_id": account.proxy_id if account is not None else None,
                "session_status": row["audit_status"],
                "last_active": account.last_active_at.isoformat() if account and account.last_active_at else None,
                "ban_risk_level": getattr(account, "risk_level", "low") if account is not None else "low",
                "status": row["status"],
                "health_status": row["health_status"],
                "lifecycle_stage": row["lifecycle_stage"],
                "restriction_reason": row["restriction_reason"],
                "readiness": row["readiness"],
                "recommended_next_action": row["recommended_next_action"],
                "session": row["session"],
                "api_credentials": row["api_credentials"],
            }
        )
    return {"items": items, "total": len(items)}


async def get_web_account_record(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    account_id: int,
) -> tuple[Account, dict[str, Any]]:
    payload = await list_web_accounts(session, tenant_id=tenant_id, workspace_id=workspace_id)
    for item in payload["items"]:
        if int(item["id"]) == int(account_id):
            result = await session.execute(
                select(Account)
                .options(selectinload(Account.proxy))
                .where(
                    Account.id == int(account_id),
                    Account.tenant_id == tenant_id,
                    Account.workspace_id == workspace_id,
                )
            )
            account = result.scalar_one_or_none()
            if account is None:
                break
            return account, item
    raise WebOnboardingError("account_not_found")


async def list_available_web_proxies(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
) -> dict[str, Any]:
    workspace = await get_workspace_runtime_user(session, tenant_id=tenant_id, workspace_id=workspace_id)
    result = await session.execute(
        select(Proxy)
        .where(
            or_(
                (Proxy.tenant_id == tenant_id) & (Proxy.workspace_id == workspace_id),
                (Proxy.tenant_id.is_(None)) & (Proxy.user_id == workspace.runtime_user_id),
            )
        )
        .order_by(Proxy.created_at.asc(), Proxy.id.asc())
    )
    rows = list(result.scalars().all())
    summary = await get_proxy_pool_summary(user_id=int(workspace.runtime_user_id))
    items = [
        {
            "id": row.id,
            "url": row.url,
            "host": row.host,
            "port": row.port,
            "proxy_type": row.proxy_type,
            "health_status": row.health_status,
            "is_active": bool(row.is_active),
            "tenant_owned": row.tenant_id == tenant_id,
        }
        for row in rows
    ]
    return {"items": items, "total": len(items), "summary": summary}


async def bind_proxy_for_account(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    account_id: int,
    proxy_id: int | None = None,
    proxy_string: str | None = None,
) -> dict[str, Any]:
    workspace = await get_workspace_runtime_user(session, tenant_id=tenant_id, workspace_id=workspace_id)
    account_result = await session.execute(
        select(Account).where(
            Account.id == int(account_id),
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
        )
    )
    account = account_result.scalar_one_or_none()
    if account is None:
        raise WebOnboardingError("account_not_found")

    proxy: Proxy | None = None
    if proxy_id is not None:
        proxy_result = await session.execute(
            select(Proxy).where(
                Proxy.id == int(proxy_id),
                or_(
                    (Proxy.tenant_id == tenant_id) & (Proxy.workspace_id == workspace_id),
                    (Proxy.tenant_id.is_(None)) & (Proxy.user_id == workspace.runtime_user_id),
                ),
            )
        )
        proxy = proxy_result.scalar_one_or_none()
        if proxy is None:
            raise WebOnboardingError("proxy_not_found")
    elif str(proxy_string or "").strip():
        parsed = parse_proxy_line(str(proxy_string).strip(), settings.PROXY_TYPE)
        if parsed is None:
            raise WebOnboardingError("invalid_proxy_string")
        existing_result = await session.execute(
            select(Proxy).where(
                Proxy.user_id == workspace.runtime_user_id,
                Proxy.host == parsed.host,
                Proxy.port == parsed.port,
                Proxy.proxy_type == parsed.proxy_type,
                Proxy.username == parsed.username,
                Proxy.password == parsed.password,
            )
        )
        proxy = existing_result.scalar_one_or_none()
        if proxy is None:
            proxy = Proxy(
                user_id=workspace.runtime_user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                proxy_type=parsed.proxy_type,
                host=parsed.host,
                port=parsed.port,
                username=parsed.username,
                password=parsed.password,
                is_active=True,
                health_status="unknown",
            )
            session.add(proxy)
            await session.flush()
    else:
        raise WebOnboardingError("proxy_input_required")

    proxy.user_id = workspace.runtime_user_id
    proxy.tenant_id = tenant_id
    proxy.workspace_id = workspace_id
    account.proxy_id = proxy.id

    await record_onboarding_step(
        account.phone,
        user_id=int(workspace.runtime_user_id),
        step_key="bind_proxy",
        actor="web:bind_proxy",
        source="web",
        channel="web",
        result="bound",
        notes=f"Прокси {proxy.host}:{proxy.port} привязан к аккаунту.",
        payload={"proxy_id": proxy.id},
        session=session,
    )

    return {
        "account_id": account.id,
        "proxy_id": proxy.id,
        "proxy_url": proxy.url,
        "health_status": proxy.health_status,
    }


async def audit_web_account(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    account_id: int,
) -> dict[str, Any]:
    workspace = await get_workspace_runtime_user(session, tenant_id=tenant_id, workspace_id=workspace_id)
    account, _ = await get_web_account_record(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        account_id=account_id,
    )
    report = await run_auth_refresh_for_phone(
        account.phone,
        user_id=int(workspace.runtime_user_id),
    )
    refreshed_account, record = await get_web_account_record(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        account_id=account_id,
    )
    return {
        "account": {
            "id": refreshed_account.id,
            "phone": refreshed_account.phone,
        },
        "audit": record,
        "report": report,
    }


async def run_auth_refresh_for_phone(
    phone: str,
    *,
    user_id: int | None = None,
) -> dict[str, Any]:
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        raise WebOnboardingError("invalid_phone")

    report = await audit_sessions(
        phone=normalized_phone,
        user_id=user_id,
        mark_unauthorized=True,
        reactivate_authorized=True,
        authorized_stage="auth_verified",
        authorized_status="active",
        authorized_health="alive",
        set_parser_first_authorized=False,
        clear_worker_claims=False,
        stage_actor="web_auth_audit",
    )

    primary = ((report.get("results") or [{}])[0] or {})
    await record_onboarding_step(
        normalized_phone,
        user_id=user_id,
        step_key="auth_check",
        actor="web:auth_check",
        source="web",
        channel="web",
        result=str(primary.get("probe_status") or "unknown"),
        notes="Проверка доступа из web onboarding.",
    )
    return report
