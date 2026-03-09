"""Shared onboarding persistence for bot-first and CLI account setup."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from storage.models import (
    Account,
    AccountOnboardingRun,
    AccountOnboardingStep,
    AccountStageEvent,
)
from storage.sqlite_db import async_session
from utils.helpers import utcnow


STEP_TITLES = {
    "start": "Старт настройки",
    "upload_session": "Загружен .session",
    "upload_metadata": "Загружен .json",
    "upload_bundle": "Комплект файлов собран",
    "auth_check": "Проверка доступа",
    "profile_draft_generated": "Сгенерирован черновик профиля",
    "profile_applied": "Профиль подтверждён",
    "profile_apply_failed": "Подтверждение профиля остановлено",
    "channel_draft_generated": "Сгенерирован черновик канала",
    "channel_applied": "Канал подтверждён",
    "channel_apply_failed": "Подтверждение канала остановлено",
    "content_draft_generated": "Сгенерирован черновик поста",
    "content_applied": "Пост подтверждён",
    "content_apply_failed": "Подтверждение поста остановлено",
    "role_assigned": "Роль аккаунта назначена",
    "comment_draft_generated": "Сгенерирован черновик комментария",
    "comment_reviewed": "Черновик комментария проверен",
    "comment_approved": "Черновик комментария подтверждён",
    "packaging_queued": "Подготовка поставлена в очередь",
    "profile_ready": "Профиль готов",
    "channel_ready": "Канал готов",
    "content_ready": "Контент готов",
    "subscriptions_ready": "Подписки и вступления готовы",
    "gate_review_requested": "Отправлено на подтверждение",
    "returned_to_warmup": "Возврат на прогрев",
    "final_ready": "Аккаунт готов",
}


def _fit_short(value: str | None, *, limit: int = 32) -> str:
    text = str(value or "").strip()
    return text[:limit] if len(text) > limit else text


def _normalize_phone(raw: str | None) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return f"+{digits}" if digits else ""


def _step_title(step_key: str) -> str:
    return STEP_TITLES.get(step_key, step_key.replace("_", " "))


def _render_markdown_entry(
    *,
    phone: str,
    run_id: int,
    step_key: str,
    result: str,
    source: str,
    channel: str,
    actor: str,
    notes: str,
) -> str:
    timestamp = utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"## {timestamp} | {phone} | run #{run_id}",
        f"- step: `{step_key}` ({_step_title(step_key)})",
        f"- result: `{result}`",
        f"- source: `{source}` / `{channel}`",
        f"- actor: `{actor}`",
    ]
    if notes:
        lines.append(f"- notes: {notes.strip()}")
    return "\n".join(lines) + "\n\n"


def _append_markdown_memory(
    *,
    memory_path: Path,
    phone: str,
    run_id: int,
    step_key: str,
    result: str,
    source: str,
    channel: str,
    actor: str,
    notes: str,
) -> None:
    if not memory_path.exists():
        memory_path.write_text(
            "# Account Onboarding Memory\n\n"
            "Здесь сохраняется пошаговая история настройки аккаунтов.\n\n",
            encoding="utf-8",
        )
    with memory_path.open("a", encoding="utf-8") as fh:
        fh.write(
            _render_markdown_entry(
                phone=phone,
                run_id=run_id,
                step_key=step_key,
                result=result,
                source=source,
                channel=channel,
                actor=actor,
                notes=notes,
            )
        )


async def _get_account(phone: str, *, user_id: int | None = None) -> Account | None:
    normalized_phone = _normalize_phone(phone)
    if not normalized_phone:
        return None
    async with async_session() as session:
        query = select(Account).where(Account.phone == normalized_phone)
        if user_id is not None:
            query = query.where(Account.user_id == user_id)
        result = await session.execute(query)
        return result.scalar_one_or_none()


def _run_payload(run: AccountOnboardingRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "account_id": run.account_id,
        "user_id": run.user_id,
        "phone": run.phone,
        "status": run.status,
        "mode": run.mode,
        "source_channel": run.source_channel,
        "current_step": run.current_step,
        "last_result": run.last_result,
        "notes": run.notes,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _step_payload(step: AccountOnboardingStep) -> dict[str, Any]:
    payload: dict[str, Any] | None = None
    if step.payload_json:
        try:
            payload = json.loads(step.payload_json)
        except Exception:
            payload = {"raw": step.payload_json}
    return {
        "id": step.id,
        "run_id": step.run_id,
        "account_id": step.account_id,
        "user_id": step.user_id,
        "phone": step.phone,
        "step_key": step.step_key,
        "step_title": _step_title(step.step_key),
        "actor": step.actor,
        "source": step.source,
        "channel": step.channel,
        "result": step.result,
        "notes": step.notes,
        "payload": payload,
        "created_at": step.created_at.isoformat() if step.created_at else None,
    }


async def start_onboarding_run(
    phone: str,
    *,
    user_id: int | None = None,
    mode: str = "bot",
    channel: str = "bot",
    actor: str = "onboarding:start",
    notes: str = "",
) -> dict[str, Any]:
    normalized_phone = _normalize_phone(phone)
    if not normalized_phone:
        raise ValueError("invalid_phone")

    async with async_session() as session:
        query = select(Account).where(Account.phone == normalized_phone)
        if user_id is not None:
            query = query.where(Account.user_id == user_id)
        result = await session.execute(query)
        account = result.scalar_one_or_none()
        if account is None:
            raise RuntimeError("account_not_found")

        run_query = (
            select(AccountOnboardingRun)
            .where(
                AccountOnboardingRun.account_id == account.id,
                AccountOnboardingRun.status.in_(("active", "paused")),
            )
            .order_by(AccountOnboardingRun.updated_at.desc(), AccountOnboardingRun.id.desc())
        )
        run_result = await session.execute(run_query)
        run = run_result.scalars().first()
        now = utcnow()
        if run is None:
            run = AccountOnboardingRun(
                account_id=account.id,
                user_id=account.user_id,
                phone=account.phone,
                status="active",
                mode=mode,
                source_channel=channel,
                current_step="start",
                last_result="started",
                notes=notes or None,
                started_at=now,
                updated_at=now,
            )
            session.add(run)
            await session.flush()
        else:
            run.status = "active"
            run.mode = mode or run.mode
            run.source_channel = channel or run.source_channel
            run.current_step = "start"
            run.last_result = "started"
            run.updated_at = now
            if notes:
                run.notes = notes

        step = AccountOnboardingStep(
            run_id=run.id,
            account_id=account.id,
            user_id=account.user_id,
            phone=account.phone,
            step_key="start",
            actor=actor,
            source=mode,
            channel=channel,
            result="started",
            notes=notes or None,
        )
        session.add(step)
        session.add(
            AccountStageEvent(
                account_id=account.id,
                phone=account.phone,
                from_stage=account.lifecycle_stage,
                to_stage=account.lifecycle_stage,
                actor=actor,
                reason="account onboarding started",
            )
        )
        await session.commit()
        await session.refresh(run)
        await session.refresh(step)

    _append_markdown_memory(
        memory_path=settings.onboarding_memory_path,
        phone=normalized_phone,
        run_id=run.id,
        step_key="start",
        result="started",
        source=mode,
        channel=channel,
        actor=actor,
        notes=notes,
    )
    return {"ok": True, "run": _run_payload(run), "step": _step_payload(step)}


async def record_onboarding_step(
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
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    normalized_phone = _normalize_phone(phone)
    if not normalized_phone:
        raise ValueError("invalid_phone")
    if not step_key:
        raise ValueError("step_key_required")

    owns_session = session is None
    if session is None:
        session = async_session()
    try:
        if owns_session:
            await session.__aenter__()
        query = select(Account).where(Account.phone == normalized_phone)
        if user_id is not None:
            query = query.where(Account.user_id == user_id)
        result_obj = await session.execute(query)
        account = result_obj.scalar_one_or_none()
        if account is None:
            raise RuntimeError("account_not_found")

        run_query = (
            select(AccountOnboardingRun)
            .where(
                AccountOnboardingRun.account_id == account.id,
                AccountOnboardingRun.status.in_(("active", "paused")),
            )
            .order_by(AccountOnboardingRun.updated_at.desc(), AccountOnboardingRun.id.desc())
        )
        run_result = await session.execute(run_query)
        run = run_result.scalars().first()
        now = utcnow()
        if run is None:
            run = AccountOnboardingRun(
                account_id=account.id,
                user_id=account.user_id,
                phone=account.phone,
                status="active",
                mode=source,
                source_channel=channel,
                current_step=step_key,
                last_result=_fit_short(result),
                started_at=now,
                updated_at=now,
            )
            session.add(run)
            await session.flush()
        else:
            run.current_step = step_key
            run.last_result = _fit_short(result)
            run.mode = source or run.mode
            run.source_channel = channel or run.source_channel
            run.updated_at = now
            if notes:
                run.notes = notes
            if run_status:
                run.status = run_status
                if run_status == "completed":
                    run.completed_at = now

        step = AccountOnboardingStep(
            run_id=run.id,
            account_id=account.id,
            user_id=account.user_id,
            phone=account.phone,
            step_key=step_key,
            actor=actor,
            source=source,
            channel=channel,
            result=_fit_short(result),
            notes=notes or None,
            payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload else None,
        )
        session.add(step)
        session.add(
            AccountStageEvent(
                account_id=account.id,
                phone=account.phone,
                from_stage=account.lifecycle_stage,
                to_stage=account.lifecycle_stage,
                actor=actor,
                reason=f"onboarding step {step_key} ({result})",
            )
        )
        if owns_session:
            await session.commit()
        else:
            await session.flush()
        await session.refresh(run)
        await session.refresh(step)
    finally:
        if owns_session:
            await session.__aexit__(None, None, None)

    _append_markdown_memory(
        memory_path=settings.onboarding_memory_path,
        phone=normalized_phone,
        run_id=run.id,
        step_key=step_key,
        result=result,
        source=source,
        channel=channel,
        actor=actor,
        notes=notes,
    )
    return {"ok": True, "run": _run_payload(run), "step": _step_payload(step)}


async def get_onboarding_status(
    phone: str,
    *,
    user_id: int | None = None,
    limit_steps: int = 20,
) -> dict[str, Any]:
    normalized_phone = _normalize_phone(phone)
    if not normalized_phone:
        raise ValueError("invalid_phone")

    async with async_session() as session:
        query = select(Account).where(Account.phone == normalized_phone)
        if user_id is not None:
            query = query.where(Account.user_id == user_id)
        result = await session.execute(query)
        account = result.scalar_one_or_none()
        if account is None:
            raise RuntimeError("account_not_found")

        run_query = (
            select(AccountOnboardingRun)
            .where(AccountOnboardingRun.account_id == account.id)
            .order_by(AccountOnboardingRun.updated_at.desc(), AccountOnboardingRun.id.desc())
        )
        run_result = await session.execute(run_query)
        run = run_result.scalars().first()

        steps: list[AccountOnboardingStep] = []
        if run is not None:
            steps_query = (
                select(AccountOnboardingStep)
                .where(AccountOnboardingStep.run_id == run.id)
                .order_by(AccountOnboardingStep.created_at.desc(), AccountOnboardingStep.id.desc())
                .limit(max(1, int(limit_steps)))
            )
            steps_result = await session.execute(steps_query)
            steps = list(steps_result.scalars().all())

    return {
        "ok": True,
        "account": {
            "id": account.id,
            "phone": account.phone,
            "user_id": account.user_id,
            "status": account.status,
            "health_status": account.health_status,
            "lifecycle_stage": account.lifecycle_stage,
            "proxy_id": account.proxy_id,
            "restriction_reason": account.restriction_reason,
        },
        "run": _run_payload(run),
        "steps": [_step_payload(step) for step in steps],
    }


async def list_onboarding_runs(
    *,
    user_id: int | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    async with async_session() as session:
        query = select(AccountOnboardingRun).order_by(
            AccountOnboardingRun.updated_at.desc(),
            AccountOnboardingRun.id.desc(),
        )
        if user_id is not None:
            query = query.where(AccountOnboardingRun.user_id == user_id)
        if status:
            query = query.where(AccountOnboardingRun.status == status)
        result = await session.execute(query.limit(max(1, int(limit))))
        runs = list(result.scalars().all())
    return [_run_payload(run) for run in runs]
