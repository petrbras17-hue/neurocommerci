"""Human-gated Gemini workflow for account setup and comment drafts."""

from __future__ import annotations

import json
import random
from typing import Any

from sqlalchemy import select

from comments.generator import CommentGenerator
from comments.scenarios import Scenario
from config import settings
from core.ai_orchestrator import AIOrchestrator
from core.onboarding_service import record_onboarding_step
from core.proxy_manager import ProxyManager
from core.rate_limiter import RateLimiter
from core.session_manager import SessionManager
from storage.models import Account, AccountDraftArtifact, AccountStageEvent
from storage.sqlite_db import async_session
from utils.account_packager import ALL_STYLES, AVATAR_PROMPTS, AccountPackager
from utils.channel_setup import ChannelSetup
from utils.helpers import utcnow
from utils.logger import log
from core.account_manager import AccountManager


ALLOWED_ACCOUNT_ROLES = {
    "parser_candidate",
    "parser_active",
    "comment_candidate",
    "execution_ready",
    "needs_attention",
}

ALLOWED_ARTIFACT_KINDS = {"profile", "channel", "content", "comment", "reply"}

BLOCKED_HEALTHS = {"restricted", "frozen", "expired", "dead"}
BLOCKED_STATUSES = {"banned", "error"}


session_mgr = SessionManager()
proxy_mgr = ProxyManager()
rate_limiter = RateLimiter()
account_mgr = AccountManager(session_mgr, proxy_mgr, rate_limiter)
packager = AccountPackager(session_mgr)
channel_setup = ChannelSetup(session_mgr, account_mgr)
comment_generator = CommentGenerator()
comment_reviewer = AIOrchestrator()


def _normalize_phone(raw: str | None) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return f"+{digits}" if digits else ""


def _dumps(payload: dict[str, Any] | None) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _variant_count(raw: int | None) -> int:
    try:
        count = int(raw or 3)
    except Exception:
        count = 3
    return max(1, min(count, 3))


def _scenario_value(value: Any) -> str:
    if isinstance(value, Scenario):
        return str(value.value)
    return str(value or "A")


def _classify_apply_failure(reason: str) -> tuple[str, str, str]:
    low = str(reason or "").lower()
    if any(token in low for token in ("frozen", "restricted", "quarantine")):
        return "restricted", "restricted", "stop_and_escalate"
    if any(token in low for token in ("unauthorized", "auth_key_unregistered")):
        return "expired", "expired", "reauth_required"
    return "packaging_error", "unknown", "apply_failed"


async def _load_account(phone: str, *, user_id: int | None = None) -> Account:
    normalized = _normalize_phone(phone)
    if not normalized:
        raise ValueError("invalid_phone")
    async with async_session() as session:
        query = select(Account).where(Account.phone == normalized)
        if user_id is not None:
            query = query.where(Account.user_id == user_id)
        result = await session.execute(query)
        account = result.scalar_one_or_none()
    if account is None:
        raise RuntimeError("account_not_found")
    return account


async def _update_account_stage(
    account_id: int,
    *,
    to_stage: str,
    actor: str,
    reason: str,
    status: str | None = None,
    health_status: str | None = None,
    account_role: str | None = None,
) -> Account:
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one()
        old_stage = account.lifecycle_stage
        account.lifecycle_stage = to_stage
        account.last_active_at = utcnow()
        if status is not None:
            account.status = status
        if health_status is not None:
            account.health_status = health_status
        if account_role is not None:
            account.account_role = account_role
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


async def _store_artifact(
    *,
    account: Account,
    artifact_kind: str,
    payload: dict[str, Any],
    status: str,
    selected_variant: int = 0,
    notes: str = "",
) -> AccountDraftArtifact:
    if artifact_kind not in ALLOWED_ARTIFACT_KINDS:
        raise ValueError("invalid_artifact_kind")
    async with async_session() as session:
        artifact = AccountDraftArtifact(
            account_id=account.id,
            user_id=account.user_id,
            phone=account.phone,
            artifact_kind=artifact_kind,
            status=status,
            selected_variant=int(selected_variant or 0),
            payload_json=_dumps(payload),
            notes=notes or None,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(artifact)
        await session.commit()
        await session.refresh(artifact)
        return artifact


async def _latest_artifact(
    phone: str,
    *,
    artifact_kind: str,
    user_id: int | None = None,
    allowed_statuses: tuple[str, ...] | None = None,
) -> AccountDraftArtifact | None:
    normalized = _normalize_phone(phone)
    async with async_session() as session:
        query = (
            select(AccountDraftArtifact)
            .where(
                AccountDraftArtifact.phone == normalized,
                AccountDraftArtifact.artifact_kind == artifact_kind,
            )
            .order_by(AccountDraftArtifact.updated_at.desc(), AccountDraftArtifact.id.desc())
        )
        if user_id is not None:
            query = query.where(AccountDraftArtifact.user_id == user_id)
        if allowed_statuses:
            query = query.where(AccountDraftArtifact.status.in_(allowed_statuses))
        result = await session.execute(query)
        return result.scalars().first()


def _ensure_account_can_draft(account: Account) -> None:
    if account.status in BLOCKED_STATUSES:
        raise RuntimeError(f"status_{account.status}")
    if account.status not in {"active", "cooldown", "flood_wait"}:
        raise RuntimeError(f"status_{account.status}")
    if account.health_status in BLOCKED_HEALTHS:
        raise RuntimeError(f"health_{account.health_status}")
    if account.health_status != "alive":
        raise RuntimeError(f"health_{account.health_status or 'unknown'}")


def _require_stage(account: Account, *allowed: str) -> None:
    current = str(account.lifecycle_stage or "")
    if current not in set(allowed):
        raise RuntimeError(f"stage_{current or 'unknown'}")


def _pick_variant(payload: dict[str, Any], selected_variant: int | None) -> tuple[dict[str, Any], int]:
    variants = list(payload.get("variants") or [])
    if not variants:
        raise RuntimeError("draft_payload_missing_variants")
    try:
        idx = int(selected_variant if selected_variant is not None else payload.get("selected_variant", 0))
    except Exception:
        idx = 0
    idx = max(0, min(idx, len(variants) - 1))
    variant = variants[idx]
    if not isinstance(variant, dict):
        raise RuntimeError("draft_variant_invalid")
    return variant, idx


async def generate_profile_draft(
    phone: str,
    *,
    user_id: int | None = None,
    style: str | None = None,
    variant_count: int = 3,
    actor: str = "ops_api_profile_draft",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    account = await _load_account(phone, user_id=user_id)
    _ensure_account_can_draft(account)
    _require_stage(account, "auth_verified", "profile_draft", "profile_applied", "packaging_error")
    count = _variant_count(variant_count)
    profile_style = style or account.persona_style or random.choice(ALL_STYLES)
    variants: list[dict[str, Any]] = []
    for idx in range(count):
        generated = await packager.generate_profile(profile_style)
        generated["avatar_prompt_index"] = random.randrange(len(AVATAR_PROMPTS))
        generated["style"] = profile_style
        variants.append(generated)
    payload = {
        "phone": account.phone,
        "artifact_kind": "profile",
        "variants": variants,
        "selected_variant": 0,
        "style": profile_style,
    }
    artifact = await _store_artifact(
        account=account,
        artifact_kind="profile",
        payload=payload,
        status="draft",
        selected_variant=0,
        notes="Gemini profile draft generated",
    )
    await _update_account_stage(
        account.id,
        to_stage="profile_draft",
        actor=actor,
        reason="profile draft generated",
        status="active",
        health_status="alive" if account.health_status == "alive" else account.health_status,
    )
    await record_onboarding_step(
        account.phone,
        user_id=account.user_id,
        step_key="profile_draft_generated",
        actor=actor,
        source=source,
        channel=channel,
        result="draft_ready",
        notes="Сгенерирован черновик профиля. Telegram-side действия ещё не выполнялись.",
        payload={"artifact_id": artifact.id, "variants": len(variants), "style": profile_style},
    )
    return {
        "ok": True,
        "artifact_id": artifact.id,
        "phone": account.phone,
        "lifecycle_stage": "profile_draft",
        "variants": variants,
        "selected_variant": 0,
    }


async def apply_profile_draft(
    phone: str,
    *,
    user_id: int | None = None,
    draft_id: int | None = None,
    selected_variant: int | None = None,
    actor: str = "ops_api_profile_apply",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    account = await _load_account(phone, user_id=user_id)
    _ensure_account_can_draft(account)
    _require_stage(account, "profile_draft", "profile_applied")
    artifact = await _latest_artifact(
        account.phone,
        artifact_kind="profile",
        user_id=account.user_id,
        allowed_statuses=("draft", "reviewed", "approved"),
    )
    if artifact is None or (draft_id is not None and int(artifact.id) != int(draft_id)):
        raise RuntimeError("profile_draft_not_found")
    payload = _loads(artifact.payload_json)
    variant, variant_idx = _pick_variant(payload, selected_variant)

    client = await session_mgr.connect_client_for_action(account.phone, user_id=account.user_id)
    if not client:
        raise RuntimeError("profile_apply_connect_failed")
    try:
        profile_ok = await packager.apply_profile(account.phone, variant)
        if not profile_ok:
            raise RuntimeError("profile_apply_failed")
        username = await packager.generate_username(account.phone, variant)
        username_applied = bool(username and await packager.apply_username(account.phone, username))
        avatar_path = await packager.generate_avatar(int(variant.get("avatar_prompt_index", 0) or 0))
        avatar_applied = bool(avatar_path and await packager.apply_avatar(account.phone, avatar_path))
    except Exception as exc:
        stage, health_status, result = _classify_apply_failure(str(exc))
        await _update_account_stage(
            account.id,
            to_stage=stage,
            actor=actor,
            reason=f"profile apply failed: {exc}",
            status="error" if stage != "expired" else "active",
            health_status=health_status,
            account_role="needs_attention" if stage in {"restricted", "packaging_error", "expired"} else None,
        )
        await record_onboarding_step(
            account.phone,
            user_id=account.user_id,
            step_key="profile_apply_failed",
            actor=actor,
            source=source,
            channel=channel,
            result=result,
            notes=f"Применение профиля остановлено: {exc}",
            payload={"artifact_id": artifact.id, "selected_variant": variant_idx},
            run_status="paused",
        )
        raise
    finally:
        await session_mgr.disconnect_client(account.phone)

    async with async_session() as session:
        result = await session.execute(select(AccountDraftArtifact).where(AccountDraftArtifact.id == artifact.id))
        row = result.scalar_one()
        row.status = "applied"
        row.selected_variant = variant_idx
        row.updated_at = utcnow()
        applied_payload = _loads(row.payload_json)
        applied_payload["selected_variant"] = variant_idx
        applied_payload["applied"] = {
            "username": username,
            "username_applied": bool(username_applied),
            "avatar_applied": bool(avatar_applied),
            "applied_at": utcnow().isoformat(),
        }
        row.payload_json = _dumps(applied_payload)
        await session.commit()

    await _update_account_stage(
        account.id,
        to_stage="profile_applied",
        actor=actor,
        reason="profile apply completed",
        status="active",
        health_status="alive",
    )
    await record_onboarding_step(
        account.phone,
        user_id=account.user_id,
        step_key="profile_applied",
        actor=actor,
        source=source,
        channel=channel,
        result="applied",
        notes="Профиль подтверждён и применён вручную.",
        payload={
            "artifact_id": artifact.id,
            "selected_variant": variant_idx,
            "username": username,
            "username_applied": bool(username_applied),
            "avatar_applied": bool(avatar_applied),
        },
    )
    return {
        "ok": True,
        "artifact_id": artifact.id,
        "phone": account.phone,
        "selected_variant": variant_idx,
        "username": username,
        "username_applied": bool(username_applied),
        "avatar_applied": bool(avatar_applied),
        "lifecycle_stage": "profile_applied",
    }


async def generate_channel_draft(
    phone: str,
    *,
    user_id: int | None = None,
    style: str | None = None,
    variant_count: int = 3,
    actor: str = "ops_api_channel_draft",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    account = await _load_account(phone, user_id=user_id)
    _ensure_account_can_draft(account)
    _require_stage(account, "profile_applied", "channel_draft", "channel_applied")
    count = _variant_count(variant_count)
    channel_style = style or account.persona_style or random.choice(ALL_STYLES)
    variants = [await channel_setup.generate_channel_content(style=channel_style) for _ in range(count)]
    payload = {
        "phone": account.phone,
        "artifact_kind": "channel",
        "variants": variants,
        "selected_variant": 0,
        "style": channel_style,
    }
    artifact = await _store_artifact(
        account=account,
        artifact_kind="channel",
        payload=payload,
        status="draft",
        selected_variant=0,
        notes="Gemini channel draft generated",
    )
    await _update_account_stage(
        account.id,
        to_stage="channel_draft",
        actor=actor,
        reason="channel draft generated",
        status="active",
        health_status=account.health_status,
    )
    await record_onboarding_step(
        account.phone,
        user_id=account.user_id,
        step_key="channel_draft_generated",
        actor=actor,
        source=source,
        channel=channel,
        result="draft_ready",
        notes="Сгенерирован черновик канала. Он ещё не применён в Telegram.",
        payload={"artifact_id": artifact.id, "variants": len(variants), "style": channel_style},
    )
    return {
        "ok": True,
        "artifact_id": artifact.id,
        "phone": account.phone,
        "lifecycle_stage": "channel_draft",
        "variants": variants,
        "selected_variant": 0,
    }


async def apply_channel_draft(
    phone: str,
    *,
    user_id: int | None = None,
    draft_id: int | None = None,
    selected_variant: int | None = None,
    actor: str = "ops_api_channel_apply",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    account = await _load_account(phone, user_id=user_id)
    _ensure_account_can_draft(account)
    _require_stage(account, "channel_draft", "channel_applied")
    artifact = await _latest_artifact(
        account.phone,
        artifact_kind="channel",
        user_id=account.user_id,
        allowed_statuses=("draft", "reviewed", "approved"),
    )
    if artifact is None or (draft_id is not None and int(artifact.id) != int(draft_id)):
        raise RuntimeError("channel_draft_not_found")
    payload = _loads(artifact.payload_json)
    variant, variant_idx = _pick_variant(payload, selected_variant)

    client = await session_mgr.connect_client_for_action(account.phone, user_id=account.user_id)
    if not client:
        raise RuntimeError("channel_apply_connect_failed")
    try:
        report = await channel_setup.create_channel_shell(account.phone, content=variant, style=str(payload.get("style") or "casual"))
        if not report.get("ok"):
            raise RuntimeError(str(report.get("error") or "channel_apply_failed"))
    except Exception as exc:
        stage, health_status, result = _classify_apply_failure(str(exc))
        await _update_account_stage(
            account.id,
            to_stage=stage,
            actor=actor,
            reason=f"channel apply failed: {exc}",
            status="error" if stage != "expired" else "active",
            health_status=health_status,
            account_role="needs_attention" if stage in {"restricted", "packaging_error", "expired"} else None,
        )
        await record_onboarding_step(
            account.phone,
            user_id=account.user_id,
            step_key="channel_apply_failed",
            actor=actor,
            source=source,
            channel=channel,
            result=result,
            notes=f"Применение канала остановлено: {exc}",
            payload={"artifact_id": artifact.id, "selected_variant": variant_idx},
            run_status="paused",
        )
        raise
    finally:
        await session_mgr.disconnect_client(account.phone)

    async with async_session() as session:
        result = await session.execute(select(AccountDraftArtifact).where(AccountDraftArtifact.id == artifact.id))
        row = result.scalar_one()
        row.status = "applied"
        row.selected_variant = variant_idx
        row.updated_at = utcnow()
        applied_payload = _loads(row.payload_json)
        applied_payload["selected_variant"] = variant_idx
        applied_payload["applied"] = report
        row.payload_json = _dumps(applied_payload)
        await session.commit()

    await _update_account_stage(
        account.id,
        to_stage="channel_applied",
        actor=actor,
        reason="channel apply completed",
        status="active",
        health_status="alive",
    )
    await record_onboarding_step(
        account.phone,
        user_id=account.user_id,
        step_key="channel_applied",
        actor=actor,
        source=source,
        channel=channel,
        result="applied",
        notes="Канал подтверждён и применён вручную.",
        payload={"artifact_id": artifact.id, "selected_variant": variant_idx, "channel": report},
    )
    return {
        "ok": True,
        "artifact_id": artifact.id,
        "phone": account.phone,
        "selected_variant": variant_idx,
        "channel": report,
        "lifecycle_stage": "channel_applied",
    }


async def generate_content_draft(
    phone: str,
    *,
    user_id: int | None = None,
    variant_count: int = 3,
    actor: str = "ops_api_content_draft",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    account = await _load_account(phone, user_id=user_id)
    _ensure_account_can_draft(account)
    _require_stage(account, "channel_applied", "content_draft", "content_applied")
    count = _variant_count(variant_count)
    variants: list[dict[str, Any]] = []
    channel_payload = {}
    latest_channel = await _latest_artifact(account.phone, artifact_kind="channel", user_id=account.user_id)
    if latest_channel is not None:
        channel_payload = _loads(latest_channel.payload_json)
    for idx in range(count):
        if channel_payload.get("variants"):
            base = channel_payload["variants"][idx % len(channel_payload["variants"])]
        else:
            base = await channel_setup.generate_channel_content(style=account.persona_style or "casual")
        variants.append(
            {
                "post": str(base.get("post") or "").strip(),
                "channel_name_hint": str(base.get("name") or "").strip(),
            }
        )
    payload = {
        "phone": account.phone,
        "artifact_kind": "content",
        "variants": variants,
        "selected_variant": 0,
    }
    artifact = await _store_artifact(
        account=account,
        artifact_kind="content",
        payload=payload,
        status="draft",
        selected_variant=0,
        notes="Gemini content draft generated",
    )
    await _update_account_stage(
        account.id,
        to_stage="content_draft",
        actor=actor,
        reason="content draft generated",
        status="active",
        health_status=account.health_status,
    )
    await record_onboarding_step(
        account.phone,
        user_id=account.user_id,
        step_key="content_draft_generated",
        actor=actor,
        source=source,
        channel=channel,
        result="draft_ready",
        notes="Сгенерирован черновик закреплённого поста. Он ещё не отправлен в Telegram.",
        payload={"artifact_id": artifact.id, "variants": len(variants)},
    )
    return {
        "ok": True,
        "artifact_id": artifact.id,
        "phone": account.phone,
        "lifecycle_stage": "content_draft",
        "variants": variants,
        "selected_variant": 0,
    }


async def apply_content_draft(
    phone: str,
    *,
    user_id: int | None = None,
    draft_id: int | None = None,
    selected_variant: int | None = None,
    actor: str = "ops_api_content_apply",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    account = await _load_account(phone, user_id=user_id)
    _ensure_account_can_draft(account)
    _require_stage(account, "content_draft", "content_applied")
    artifact = await _latest_artifact(
        account.phone,
        artifact_kind="content",
        user_id=account.user_id,
        allowed_statuses=("draft", "reviewed", "approved"),
    )
    if artifact is None or (draft_id is not None and int(artifact.id) != int(draft_id)):
        raise RuntimeError("content_draft_not_found")
    payload = _loads(artifact.payload_json)
    variant, variant_idx = _pick_variant(payload, selected_variant)

    latest_channel = await _latest_artifact(
        account.phone,
        artifact_kind="channel",
        user_id=account.user_id,
        allowed_statuses=("applied",),
    )
    if latest_channel is None:
        raise RuntimeError("channel_apply_required_first")
    channel_payload = _loads(latest_channel.payload_json)
    applied_channel = dict(channel_payload.get("applied") or {})
    channel_id = int(applied_channel.get("channel_id") or 0)
    if channel_id <= 0:
        raise RuntimeError("channel_apply_payload_missing")

    client = await session_mgr.connect_client_for_action(account.phone, user_id=account.user_id)
    if not client:
        raise RuntimeError("content_apply_connect_failed")
    try:
        report = await channel_setup.publish_content_to_channel(
            account.phone,
            channel_id=channel_id,
            post_text=str(variant.get("post") or ""),
            pin_post=True,
            attach_personal_channel=True,
        )
        if not report.get("ok"):
            raise RuntimeError(str(report.get("error") or "content_apply_failed"))
    except Exception as exc:
        stage, health_status, result = _classify_apply_failure(str(exc))
        await _update_account_stage(
            account.id,
            to_stage=stage,
            actor=actor,
            reason=f"content apply failed: {exc}",
            status="error" if stage != "expired" else "active",
            health_status=health_status,
            account_role="needs_attention" if stage in {"restricted", "packaging_error", "expired"} else None,
        )
        await record_onboarding_step(
            account.phone,
            user_id=account.user_id,
            step_key="content_apply_failed",
            actor=actor,
            source=source,
            channel=channel,
            result=result,
            notes=f"Применение поста остановлено: {exc}",
            payload={"artifact_id": artifact.id, "selected_variant": variant_idx},
            run_status="paused",
        )
        raise
    finally:
        await session_mgr.disconnect_client(account.phone)

    async with async_session() as session:
        result = await session.execute(select(AccountDraftArtifact).where(AccountDraftArtifact.id == artifact.id))
        row = result.scalar_one()
        row.status = "applied"
        row.selected_variant = variant_idx
        row.updated_at = utcnow()
        applied_payload = _loads(row.payload_json)
        applied_payload["selected_variant"] = variant_idx
        applied_payload["applied"] = report
        row.payload_json = _dumps(applied_payload)
        await session.commit()

    await _update_account_stage(
        account.id,
        to_stage="content_applied",
        actor=actor,
        reason="content apply completed",
        status="active",
        health_status="alive",
    )
    await record_onboarding_step(
        account.phone,
        user_id=account.user_id,
        step_key="content_applied",
        actor=actor,
        source=source,
        channel=channel,
        result="applied",
        notes="Закреплённый пост подтверждён и применён вручную.",
        payload={"artifact_id": artifact.id, "selected_variant": variant_idx, "content": report},
    )
    return {
        "ok": True,
        "artifact_id": artifact.id,
        "phone": account.phone,
        "selected_variant": variant_idx,
        "content": report,
        "lifecycle_stage": "content_applied",
    }


async def assign_account_role(
    phone: str,
    *,
    role: str,
    user_id: int | None = None,
    actor: str = "ops_api_assign_role",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    account = await _load_account(phone, user_id=user_id)
    normalized_role = str(role or "").strip()
    if normalized_role not in ALLOWED_ACCOUNT_ROLES:
        raise ValueError("invalid_role")

    target_stage = account.lifecycle_stage
    if normalized_role == "execution_ready":
        if account.lifecycle_stage not in {"content_applied", "execution_ready", "active_commenting"}:
            raise RuntimeError(f"stage_{account.lifecycle_stage}")
        target_stage = "execution_ready"
    elif normalized_role == "needs_attention":
        target_stage = "packaging_error"

    updated = await _update_account_stage(
        account.id,
        to_stage=target_stage,
        actor=actor,
        reason=f"account role assigned: {normalized_role}",
        status="active" if normalized_role != "needs_attention" else "error",
        health_status="alive" if normalized_role == "execution_ready" else account.health_status,
        account_role=normalized_role,
    )
    await record_onboarding_step(
        account.phone,
        user_id=account.user_id,
        step_key="role_assigned",
        actor=actor,
        source=source,
        channel=channel,
        result=normalized_role,
        notes=f"Аккаунту назначена роль {normalized_role}.",
        payload={"account_role": normalized_role, "lifecycle_stage": updated.lifecycle_stage},
    )
    return {
        "ok": True,
        "phone": account.phone,
        "account_role": normalized_role,
        "lifecycle_stage": updated.lifecycle_stage,
        "status": updated.status,
        "health_status": updated.health_status,
    }


async def create_comment_draft(
    *,
    phone: str,
    post_text: str,
    user_id: int | None = None,
    persona_style: str | None = None,
    actor: str = "ops_api_comment_draft",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    account = await _load_account(phone, user_id=user_id)
    _ensure_account_can_draft(account)
    _require_stage(account, "content_applied", "execution_ready", "active_commenting")
    generated = await comment_generator.generate(
        post_text=post_text,
        persona_style=persona_style or account.persona_style or "casual",
    )
    payload = {
        "phone": account.phone,
        "artifact_kind": "comment",
        "post_text": post_text,
        "variants": [{"text": generated["text"], "scenario": _scenario_value(generated["scenario"]), "persona": generated["persona"]}],
        "selected_variant": 0,
        "source": generated["source"],
    }
    artifact = await _store_artifact(
        account=account,
        artifact_kind="comment",
        payload=payload,
        status="draft",
        selected_variant=0,
        notes="Gemini comment draft generated",
    )
    await record_onboarding_step(
        account.phone,
        user_id=account.user_id,
        step_key="comment_draft_generated",
        actor=actor,
        source=source,
        channel=channel,
        result="draft_ready",
        notes="Комментарий сгенерирован как черновик и ещё не отправлен.",
        payload={"artifact_id": artifact.id},
    )
    return {
        "ok": True,
        "artifact_id": artifact.id,
        "phone": account.phone,
        "draft": payload["variants"][0],
    }


async def review_comment_draft(
    *,
    draft_id: int,
    user_id: int | None = None,
    actor: str = "ops_api_comment_review",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    async with async_session() as session:
        query = select(AccountDraftArtifact).where(
            AccountDraftArtifact.id == int(draft_id),
            AccountDraftArtifact.artifact_kind == "comment",
        )
        if user_id is not None:
            query = query.where(AccountDraftArtifact.user_id == user_id)
        result = await session.execute(query)
        artifact = result.scalar_one_or_none()
    if artifact is None:
        raise RuntimeError("comment_draft_not_found")
    payload = _loads(artifact.payload_json)
    variant, variant_idx = _pick_variant(payload, payload.get("selected_variant", 0))
    review = await comment_reviewer.review_comment(
        comment=str(variant.get("text") or ""),
        post_text=str(payload.get("post_text") or ""),
        scenario=Scenario.B if _scenario_value(variant.get("scenario")) == "B" else Scenario.A,
    )
    async with async_session() as session:
        result = await session.execute(select(AccountDraftArtifact).where(AccountDraftArtifact.id == artifact.id))
        row = result.scalar_one()
        updated_payload = _loads(row.payload_json)
        updated_payload["review"] = review or {}
        row.payload_json = _dumps(updated_payload)
        row.status = "reviewed"
        row.updated_at = utcnow()
        await session.commit()
    await record_onboarding_step(
        artifact.phone,
        user_id=artifact.user_id,
        step_key="comment_reviewed",
        actor=actor,
        source=source,
        channel=channel,
        result="reviewed",
        notes="Черновик комментария проверен Gemini review layer.",
        payload={"artifact_id": artifact.id, "selected_variant": variant_idx, "review": review or {}},
    )
    return {
        "ok": True,
        "artifact_id": artifact.id,
        "review": review or {},
    }


async def approve_comment_draft(
    *,
    draft_id: int,
    user_id: int | None = None,
    actor: str = "ops_api_comment_approve",
    source: str = "ops_api",
    channel: str = "ops_api",
) -> dict[str, Any]:
    async with async_session() as session:
        query = select(AccountDraftArtifact).where(
            AccountDraftArtifact.id == int(draft_id),
            AccountDraftArtifact.artifact_kind == "comment",
        )
        if user_id is not None:
            query = query.where(AccountDraftArtifact.user_id == user_id)
        result = await session.execute(query)
        artifact = result.scalar_one_or_none()
        if artifact is None:
            raise RuntimeError("comment_draft_not_found")
        artifact.status = "approved"
        artifact.updated_at = utcnow()
        await session.commit()
        payload = _loads(artifact.payload_json)
    await record_onboarding_step(
        artifact.phone,
        user_id=artifact.user_id,
        step_key="comment_approved",
        actor=actor,
        source=source,
        channel=channel,
        result="approved",
        notes="Черновик комментария подтверждён человеком и готов к controlled send path.",
        payload={"artifact_id": artifact.id},
    )
    return {
        "ok": True,
        "artifact_id": artifact.id,
        "draft": payload,
    }
