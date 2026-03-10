from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.ai_router import route_ai_task
from core.digest_service import send_digest_text
from storage.google_sheets import GoogleSheetsStorage
from storage.models import (
    AssistantMessage,
    AssistantRecommendation,
    AssistantThread,
    AuthUser,
    BusinessAsset,
    BusinessBrief,
    CreativeDraft,
    ManualAction,
)
from utils.helpers import utcnow
from utils.logger import log


BRIEF_FIELDS = (
    "product_name",
    "offer_summary",
    "target_audience",
    "competitors",
    "tone_of_voice",
    "pain_points",
    "telegram_goals",
    "website_url",
    "channel_url",
    "bot_url",
)

FIELD_TITLES = {
    "product_name": "продукт",
    "offer_summary": "оффер",
    "target_audience": "целевая аудитория",
    "competitors": "конкуренты",
    "tone_of_voice": "тон коммуникации",
    "pain_points": "боли клиентов",
    "telegram_goals": "цели в Telegram",
    "website_url": "сайт",
    "channel_url": "канал",
    "bot_url": "бот",
}

REQUIRED_CONTEXT_FIELDS = (
    "product_name",
    "offer_summary",
    "target_audience",
    "telegram_goals",
    "tone_of_voice",
)

DRAFT_TYPE_TITLES = {
    "post": "Пост",
    "comment": "Комментарий",
    "ad_copy": "Рекламный текст",
    "image_prompt": "Промпт для изображения",
}


class AssistantServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class BriefMirrorSnapshot:
    created_at: Any
    tenant_id: int
    workspace_id: int
    product_name: str
    company: str
    offer_summary: str
    target_audience: str
    tone_of_voice: str
    completeness_score: float
    telegram_goals: list[str]
    website_url: str
    channel_url: str
    bot_url: str


def _norm_text(value: Any, max_length: int = 1000) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:max_length]


def _norm_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[\n,;]+", str(value))
    return [str(item).strip() for item in items if str(item).strip()]


def _brief_value_present(value: Any) -> bool:
    if isinstance(value, list):
        return bool([item for item in value if str(item).strip()])
    return bool(str(value or "").strip())


def _brief_completeness(brief: BusinessBrief | None) -> tuple[float, list[str]]:
    if brief is None:
        return 0.0, list(REQUIRED_CONTEXT_FIELDS)
    missing = [field for field in REQUIRED_CONTEXT_FIELDS if not _brief_value_present(getattr(brief, field))]
    score = round((len(REQUIRED_CONTEXT_FIELDS) - len(missing)) / max(1, len(REQUIRED_CONTEXT_FIELDS)), 2)
    return score, missing


def _brief_summary_text(brief: BusinessBrief) -> str:
    goals = ", ".join(_norm_list(brief.telegram_goals))
    competitors = ", ".join(_norm_list(brief.competitors))
    pains = ", ".join(_norm_list(brief.pain_points))
    parts = [
        f"Продукт: {_norm_text(brief.product_name, 255) or '—'}",
        f"Оффер: {_norm_text(brief.offer_summary, 700) or '—'}",
        f"ЦА: {_norm_text(brief.target_audience, 700) or '—'}",
        f"Тон: {_norm_text(brief.tone_of_voice, 255) or '—'}",
        f"Цели Telegram: {goals or '—'}",
        f"Конкуренты: {competitors or '—'}",
        f"Боли: {pains or '—'}",
        f"Сайт: {_norm_text(brief.website_url, 500) or '—'}",
        f"Канал: {_norm_text(brief.channel_url, 500) or '—'}",
        f"Бот: {_norm_text(brief.bot_url, 500) or '—'}",
    ]
    return "\n".join(parts)


def _digest_safe(value: Any, max_length: int = 255) -> str:
    text = _norm_text(value, max_length=max_length)
    return escape(text) if text else "—"


def _initial_assistant_message() -> str:
    return (
        "Я ваш внутренний AI-ассистент по Telegram growth. Сначала соберём базовый growth-brief, "
        "чтобы дальше генерировать черновики, визуалы и рекомендации без потери контекста.\n\n"
        "Ответьте в свободной форме или по пунктам:\n"
        "1. Что за продукт?\n"
        "2. Какой главный оффер?\n"
        "3. Кто ваша ЦА?\n"
        "4. Какие цели у вас в Telegram?\n"
        "5. Какой тон коммуникации нужен?"
    )


def _next_question_text(brief: BusinessBrief | None) -> str:
    _, missing = _brief_completeness(brief)
    if not missing:
        return (
            "Бриф уже выглядит достаточно полным. Проверьте summary во вкладке «Контекст» и подтвердите его, "
            "после чего можно переходить к черновикам постов, комментариев и image prompts."
        )
    labels = ", ".join(FIELD_TITLES[field] for field in missing[:3])
    return (
        "Чтобы двигаться дальше, уточните ещё: "
        f"{labels}. Можно ответить одним сообщением обычным языком, я сам разложу это по полям."
    )


def _empty_brief_payload(tenant_id: int, workspace_id: int) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "status": "draft",
        "product_name": "",
        "offer_summary": "",
        "target_audience": "",
        "competitors": [],
        "tone_of_voice": "",
        "pain_points": [],
        "telegram_goals": [],
        "website_url": "",
        "channel_url": "",
        "bot_url": "",
        "summary_text": "",
        "completeness_score": 0.0,
        "confirmed_at": None,
        "created_at": None,
        "updated_at": None,
    }


def _brief_payload(brief: BusinessBrief | None, *, assets_count: int = 0, draft_count: int = 0) -> dict[str, Any]:
    if brief is None:
        payload = _empty_brief_payload(0, 0)
    else:
        payload = {
            "id": brief.id,
            "tenant_id": brief.tenant_id,
            "workspace_id": brief.workspace_id,
            "status": brief.status,
            "product_name": brief.product_name or "",
            "offer_summary": brief.offer_summary or "",
            "target_audience": brief.target_audience or "",
            "competitors": list(brief.competitors or []),
            "tone_of_voice": brief.tone_of_voice or "",
            "pain_points": list(brief.pain_points or []),
            "telegram_goals": list(brief.telegram_goals or []),
            "website_url": brief.website_url or "",
            "channel_url": brief.channel_url or "",
            "bot_url": brief.bot_url or "",
            "summary_text": brief.summary_text or "",
            "completeness_score": float(brief.completeness_score or 0.0),
            "confirmed_at": brief.confirmed_at.isoformat() if brief.confirmed_at else None,
            "created_at": brief.created_at.isoformat() if brief.created_at else None,
            "updated_at": brief.updated_at.isoformat() if brief.updated_at else None,
        }
    score, missing = _brief_completeness(brief)
    payload["completeness_score"] = float(payload.get("completeness_score") or score)
    payload["missing_fields"] = [FIELD_TITLES[field] for field in missing]
    payload["assistant_ready"] = score >= 0.8
    payload["assets_count"] = int(assets_count)
    payload["draft_count"] = int(draft_count)
    return payload


def _message_payload(message: AssistantMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "meta": message.meta or {},
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _recommendation_payload(item: AssistantRecommendation) -> dict[str, Any]:
    return {
        "id": item.id,
        "recommendation_type": item.recommendation_type,
        "title": item.title,
        "body": item.body,
        "payload": item.payload or {},
        "status": item.status,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _creative_payload(item: CreativeDraft) -> dict[str, Any]:
    meta = item.meta or {}
    variants = meta.get("variants") or []
    return {
        "id": item.id,
        "draft_type": item.draft_type,
        "status": item.status,
        "title": item.title,
        "input_prompt": item.input_prompt,
        "content_text": item.content_text,
        "variants": variants,
        "selected_variant": int(meta.get("selected_variant", 0) or 0),
        "meta": meta,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _extract_labeled_updates(message: str) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    patterns = {
        "product_name": r"(?:продукт|название)\s*:\s*(.+)",
        "offer_summary": r"(?:оффер|предложение)\s*:\s*(.+)",
        "target_audience": r"(?:ца|целевая аудитория|аудитория)\s*:\s*(.+)",
        "tone_of_voice": r"(?:тон|тональность)\s*:\s*(.+)",
        "website_url": r"(?:сайт|website)\s*:\s*(https?://\S+)",
        "channel_url": r"(?:канал)\s*:\s*(https?://\S+|t\.me/\S+)",
        "bot_url": r"(?:бот)\s*:\s*(https?://\S+|t\.me/\S+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            updates[key] = _norm_text(match.group(1))

    list_patterns = {
        "competitors": r"(?:конкуренты)\s*:\s*(.+)",
        "pain_points": r"(?:боли|боли клиентов)\s*:\s*(.+)",
        "telegram_goals": r"(?:цели|цели telegram|telegram цели)\s*:\s*(.+)",
    }
    for key, pattern in list_patterns.items():
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            updates[key] = _norm_list(match.group(1))
    return updates


@lru_cache(maxsize=1)
def _assistant_sheets_storage() -> GoogleSheetsStorage:
    spreadsheet_id = str(settings.CHANNELS_SPREADSHEET_ID or settings.STATS_SPREADSHEET_ID or "").strip()
    return GoogleSheetsStorage(
        credentials_file=settings.GOOGLE_SHEETS_CREDENTIALS_FILE,
        spreadsheet_id=spreadsheet_id,
    )


async def mirror_brief_to_google_sheets(snapshot: BriefMirrorSnapshot) -> dict[str, Any]:
    storage = _assistant_sheets_storage()
    if not storage.is_enabled:
        return {"ok": False, "skipped": True, "error": "sheets_not_configured"}
    await storage.append_business_brief(snapshot)
    return {"ok": True, "worksheet": "Брифы"}


async def _ensure_brief(session: AsyncSession, *, tenant_id: int, workspace_id: int, user_id: int | None) -> BusinessBrief:
    result = await session.execute(
        select(BusinessBrief).where(
            BusinessBrief.tenant_id == tenant_id,
            BusinessBrief.workspace_id == workspace_id,
        )
    )
    brief = result.scalar_one_or_none()
    if brief is not None:
        return brief
    brief = BusinessBrief(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        status="draft",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(brief)
    await session.flush()
    return brief


async def _ensure_thread(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
    brief_id: int | None,
) -> AssistantThread:
    result = await session.execute(
        select(AssistantThread)
        .where(
            AssistantThread.tenant_id == tenant_id,
            AssistantThread.workspace_id == workspace_id,
            AssistantThread.thread_kind == "growth_brief",
            AssistantThread.status == "active",
        )
        .order_by(AssistantThread.updated_at.desc(), AssistantThread.id.desc())
    )
    thread = result.scalars().first()
    if thread is not None:
        if brief_id and thread.brief_id is None:
            thread.brief_id = brief_id
        return thread
    thread = AssistantThread(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        brief_id=brief_id,
        thread_kind="growth_brief",
        status="active",
        title="Growth brief",
        last_step="start_brief",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(thread)
    await session.flush()
    return thread


async def _append_message(
    session: AsyncSession,
    *,
    thread: AssistantThread,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
    role: str,
    content: str,
    meta: dict[str, Any] | None = None,
) -> AssistantMessage:
    message = AssistantMessage(
        thread_id=thread.id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        role=role,
        content=content,
        meta=meta,
        created_at=utcnow(),
    )
    session.add(message)
    thread.updated_at = utcnow()
    await session.flush()
    return message


async def _replace_next_recommendation(
    session: AsyncSession,
    *,
    thread: AssistantThread,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
    title: str,
    body: str,
    payload: dict[str, Any] | None = None,
) -> AssistantRecommendation:
    rows = (
        await session.execute(
            select(AssistantRecommendation).where(
                AssistantRecommendation.thread_id == thread.id,
                AssistantRecommendation.recommendation_type == "next_step",
                AssistantRecommendation.status == "active",
            )
        )
    ).scalars().all()
    for row in rows:
        row.status = "archived"
        row.updated_at = utcnow()
    recommendation = AssistantRecommendation(
        thread_id=thread.id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        recommendation_type="next_step",
        title=title,
        body=body,
        payload=payload or {},
        status="active",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(recommendation)
    await session.flush()
    return recommendation


def _apply_brief_updates(brief: BusinessBrief, updates: dict[str, Any]) -> bool:
    changed = False
    for key in BRIEF_FIELDS:
        if key not in updates:
            continue
        value = updates[key]
        if key in {"competitors", "pain_points", "telegram_goals"}:
            normalized = _norm_list(value)
        else:
            normalized = _norm_text(value)
        if normalized and getattr(brief, key) != normalized:
            setattr(brief, key, normalized)
            changed = True
    if changed:
        brief.updated_at = utcnow()
        brief.completeness_score = _brief_completeness(brief)[0]
        brief.summary_text = _brief_summary_text(brief)
    return changed


async def _routed_json_task(
    session: AsyncSession,
    *,
    task_type: str,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
    prompt: str,
    max_output_tokens: int,
    surface: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    result = await route_ai_task(
        session,
        task_type=task_type,
        prompt=prompt,
        system_instruction=(
            "Ты внутренний AI-маркетинг-ассистент для SaaS. "
            "Возвращай только валидный JSON без markdown и пояснений."
        ),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        max_output_tokens=max_output_tokens,
        surface=surface,
    )
    return result.parsed, result.as_meta()


async def _gemini_extract_updates(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
    message: str,
    brief: BusinessBrief,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = (
        "Извлеки только структурированные поля growth-brief из сообщения пользователя.\n"
        "Верни JSON с любыми из ключей: product_name, offer_summary, target_audience, "
        "competitors, tone_of_voice, pain_points, telegram_goals, website_url, channel_url, bot_url.\n"
        "Для competitors/pain_points/telegram_goals верни массив строк.\n\n"
        f"Текущий бриф:\n{_brief_summary_text(brief)}\n\n"
        f"Сообщение пользователя:\n{message}"
    )
    parsed, trace = await _routed_json_task(
        session,
        task_type="brief_extraction",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        prompt=prompt,
        max_output_tokens=500,
        surface="assistant",
    )
    return parsed or {}, trace


async def _assistant_reply(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
    message: str,
    brief: BusinessBrief,
) -> tuple[str, dict[str, Any]]:
    prompt = (
        "Ты AI-маркетинг-ассистент для Telegram Growth OS. Ответь по-русски кратко: "
        "1) что уже понял из сообщения, 2) что нужно уточнить дальше, 3) какой следующий шаг лучше.\n\n"
        f"Текущий бриф:\n{_brief_summary_text(brief)}\n\n"
        f"Сообщение клиента:\n{message}"
    )
    parsed, trace = await _routed_json_task(
        session,
        task_type="assistant_reply",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        prompt=prompt,
        max_output_tokens=500,
        surface="assistant",
    )
    if parsed and isinstance(parsed.get("reply"), str):
        return _norm_text(parsed["reply"], 2000), trace
    return (
        "Я обновил контекст там, где это удалось определить из вашего сообщения. "
        + _next_question_text(brief)
    ), {**trace, "fallback_used": True, "reason_code": trace.get("reason_code") or "assistant_reply_fallback"}


async def start_business_brief(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
) -> dict[str, Any]:
    brief = await _ensure_brief(session, tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
    thread = await _ensure_thread(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        brief_id=brief.id,
    )
    existing_messages = (
        await session.execute(
            select(AssistantMessage)
            .where(AssistantMessage.thread_id == thread.id)
            .order_by(AssistantMessage.created_at.asc(), AssistantMessage.id.asc())
        )
    ).scalars().all()
    if not existing_messages:
        await _append_message(
            session,
            thread=thread,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            role="assistant",
            content=_initial_assistant_message(),
            meta={"kind": "intro"},
        )
        await _replace_next_recommendation(
            session,
            thread=thread,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            title="Заполните базовый growth-brief",
            body=_next_question_text(brief),
            payload={"missing_fields": _brief_payload(brief)["missing_fields"]},
        )
    return await get_assistant_thread(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )


async def post_assistant_message(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
    message: str,
) -> dict[str, Any]:
    content = _norm_text(message, 4000)
    if not content:
        raise AssistantServiceError("assistant_message_required")

    brief = await _ensure_brief(session, tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
    thread = await _ensure_thread(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        brief_id=brief.id,
    )
    await _append_message(
        session,
        thread=thread,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        role="user",
        content=content,
    )

    updates = _extract_labeled_updates(content)
    gemini_updates, extraction_trace = await _gemini_extract_updates(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        message=content,
        brief=brief,
    )
    for key, value in gemini_updates.items():
        updates.setdefault(key, value)
    _apply_brief_updates(brief, updates)

    reply, reply_trace = await _assistant_reply(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        message=content,
        brief=brief,
    )
    await _append_message(
        session,
        thread=thread,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        role="assistant",
        content=reply,
        meta={
            "captured_fields": sorted(updates.keys()),
            "brief_extraction_ai": extraction_trace,
            "assistant_reply_ai": reply_trace,
        },
    )
    await _replace_next_recommendation(
        session,
        thread=thread,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        title="Следующий лучший шаг",
        body=_next_question_text(brief),
        payload={"missing_fields": _brief_payload(brief)["missing_fields"]},
    )
    return await get_assistant_thread(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )


async def get_assistant_thread(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
) -> dict[str, Any]:
    brief = (
        await session.execute(
            select(BusinessBrief).where(
                BusinessBrief.tenant_id == tenant_id,
                BusinessBrief.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    thread = (
        await session.execute(
            select(AssistantThread)
            .where(
                AssistantThread.tenant_id == tenant_id,
                AssistantThread.workspace_id == workspace_id,
                AssistantThread.thread_kind == "growth_brief",
            )
            .order_by(AssistantThread.updated_at.desc(), AssistantThread.id.desc())
        )
    ).scalars().first()
    if thread is None:
        return {
            "thread": None,
            "messages": [],
            "recommendations": [],
            "brief": _brief_payload(brief),
        }
    messages = (
        await session.execute(
            select(AssistantMessage)
            .where(AssistantMessage.thread_id == thread.id)
            .order_by(AssistantMessage.created_at.asc(), AssistantMessage.id.asc())
        )
    ).scalars().all()
    recommendations = (
        await session.execute(
            select(AssistantRecommendation)
            .where(
                AssistantRecommendation.thread_id == thread.id,
                AssistantRecommendation.status == "active",
            )
            .order_by(AssistantRecommendation.created_at.desc(), AssistantRecommendation.id.desc())
        )
    ).scalars().all()
    assets_count = (
        await session.execute(
            select(func.count(BusinessAsset.id)).where(
                BusinessAsset.tenant_id == tenant_id,
                BusinessAsset.workspace_id == workspace_id,
                BusinessAsset.status == "active",
            )
        )
    ).scalar_one()
    draft_count = (
        await session.execute(
            select(func.count(CreativeDraft.id)).where(
                CreativeDraft.tenant_id == tenant_id,
                CreativeDraft.workspace_id == workspace_id,
            )
        )
    ).scalar_one()
    return {
        "thread": {
            "id": thread.id,
            "thread_kind": thread.thread_kind,
            "status": thread.status,
            "title": thread.title,
            "last_step": thread.last_step,
            "created_at": thread.created_at.isoformat() if thread.created_at else None,
            "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
        },
        "messages": [_message_payload(item) for item in messages],
        "recommendations": [_recommendation_payload(item) for item in recommendations],
        "brief": _brief_payload(brief, assets_count=int(assets_count or 0), draft_count=int(draft_count or 0)),
    }


async def get_context_payload(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
) -> dict[str, Any]:
    brief = (
        await session.execute(
            select(BusinessBrief).where(
                BusinessBrief.tenant_id == tenant_id,
                BusinessBrief.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    assets = (
        await session.execute(
            select(BusinessAsset)
            .where(
                BusinessAsset.tenant_id == tenant_id,
                BusinessAsset.workspace_id == workspace_id,
            )
            .order_by(BusinessAsset.created_at.desc(), BusinessAsset.id.desc())
        )
    ).scalars().all()
    draft_count = (
        await session.execute(
            select(func.count(CreativeDraft.id)).where(
                CreativeDraft.tenant_id == tenant_id,
                CreativeDraft.workspace_id == workspace_id,
            )
        )
    ).scalar_one()
    return {
        "brief": _brief_payload(brief, assets_count=len(assets), draft_count=int(draft_count or 0)),
        "assets": [
            {
                "id": asset.id,
                "asset_type": asset.asset_type,
                "title": asset.title,
                "value": asset.value,
                "meta": asset.meta or {},
                "status": asset.status,
                "created_at": asset.created_at.isoformat() if asset.created_at else None,
            }
            for asset in assets
        ],
    }


async def confirm_context(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
) -> dict[str, Any]:
    brief = await _ensure_brief(session, tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
    auth_user = None
    if user_id:
        auth_user = await session.get(AuthUser, int(user_id))
    brief.status = "confirmed"
    brief.completeness_score = _brief_completeness(brief)[0]
    brief.summary_text = _brief_summary_text(brief)
    brief.confirmed_at = utcnow()
    brief.updated_at = utcnow()
    session.add(
        ManualAction(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            action_type="approval",
            title="Контекст подтверждён",
            notes="Клиент или оператор подтвердил базовый business brief.",
            payload={"brief_id": brief.id},
            created_at=utcnow(),
        )
    )
    snapshot = BriefMirrorSnapshot(
        created_at=utcnow(),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        product_name=brief.product_name or "",
        company=str(getattr(auth_user, "company", "") or ""),
        offer_summary=brief.offer_summary or "",
        target_audience=brief.target_audience or "",
        tone_of_voice=brief.tone_of_voice or "",
        completeness_score=float(brief.completeness_score or 0.0),
        telegram_goals=list(brief.telegram_goals or []),
        website_url=brief.website_url or "",
        channel_url=brief.channel_url or "",
        bot_url=brief.bot_url or "",
    )
    mirror_result: dict[str, Any]
    try:
        mirror_result = await mirror_brief_to_google_sheets(snapshot)
    except Exception as exc:
        mirror_result = {"ok": False, "error": str(exc)}
    notification_result: dict[str, Any]
    try:
        notification_result = await send_digest_text(
            "\n".join(
                [
                    "🧠 <b>Контекст бизнеса подтверждён</b>",
                    "━━━━━━━━━━━━━━━━━━━━",
                    f"Tenant: <code>{tenant_id}</code>",
                    f"Workspace: <code>{workspace_id}</code>",
                    f"Компания: <b>{_digest_safe(snapshot.company)}</b>",
                    f"Продукт: <b>{_digest_safe(brief.product_name)}</b>",
                    f"Оффер: {_digest_safe(brief.offer_summary, 500)}",
                    f"ЦА: {_digest_safe(brief.target_audience, 500)}",
                    f"Тон: <b>{_digest_safe(brief.tone_of_voice)}</b>",
                    f"Цели Telegram: {_digest_safe(', '.join(list(brief.telegram_goals or [])), 500)}",
                    f"Completeness: <b>{float(brief.completeness_score or 0.0):.2f}</b>",
                ]
            )
        )
    except Exception as exc:
        notification_result = {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "brief": _brief_payload(brief),
        "google_sheets": mirror_result,
        "digest_notification": notification_result,
    }


def _fallback_creative_variants(brief: BusinessBrief, draft_type: str, variant_count: int) -> list[dict[str, Any]]:
    product = _norm_text(brief.product_name, 255) or "продукт"
    offer = _norm_text(brief.offer_summary, 400) or "ценностное предложение"
    audience = _norm_text(brief.target_audience, 400) or "целевую аудиторию"
    goals = _norm_list(brief.telegram_goals) or ["рост узнаваемости"]

    variants: list[dict[str, Any]] = []
    for idx in range(max(1, min(variant_count, 3))):
        if draft_type == "post":
            content = (
                f"Вариант {idx + 1}. {product}: {offer}. "
                f"Пост для аудитории {audience} с акцентом на цель «{goals[min(idx, len(goals) - 1)]}»."
            )
        elif draft_type == "comment":
            content = (
                f"Вариант {idx + 1}. Нативный комментарий о том, как {product} помогает аудитории {audience} "
                f"двигаться к цели «{goals[min(idx, len(goals) - 1)]}»."
            )
        elif draft_type == "ad_copy":
            content = (
                f"Вариант {idx + 1}. Короткий рекламный текст: {product} — {offer}. "
                f"Для {audience}, когда нужна цель «{goals[min(idx, len(goals) - 1)]}»."
            )
        else:
            content = (
                f"Вариант {idx + 1}. Промпт для изображения бренда {product}: современный premium Telegram growth стиль, "
                f"метафора оффера «{offer}», аудитория {audience}, визуальный акцент на «{goals[min(idx, len(goals) - 1)]}»."
            )
        variants.append({"title": f"{DRAFT_TYPE_TITLES.get(draft_type, draft_type)} {idx + 1}", "content": content})
    return variants


async def _gemini_creative_variants(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
    brief: BusinessBrief,
    draft_type: str,
    variant_count: int,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    prompt = (
        "Сгенерируй 2-3 варианта креатива по growth-brief. Верни только JSON вида "
        "{\"variants\":[{\"title\":\"...\",\"content\":\"...\"}]}\n\n"
        f"Тип: {draft_type}\n"
        f"Бриф:\n{_brief_summary_text(brief)}"
    )
    parsed, trace = await _routed_json_task(
        session,
        task_type="creative_variants",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        prompt=prompt,
        max_output_tokens=900,
        surface="creative",
    )
    variants = parsed.get("variants") if isinstance(parsed, dict) else None
    if not isinstance(variants, list):
        return None, {**trace, "fallback_used": True, "reason_code": trace.get("reason_code") or "creative_variants_fallback"}
    cleaned: list[dict[str, Any]] = []
    for item in variants[: max(1, min(variant_count, 3))]:
        if not isinstance(item, dict):
            continue
        title = _norm_text(item.get("title"), 255)
        content = _norm_text(item.get("content"), 4000)
        if title and content:
            cleaned.append({"title": title, "content": content})
    return cleaned or None, trace


async def list_creative_drafts(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(CreativeDraft)
            .where(
                CreativeDraft.tenant_id == tenant_id,
                CreativeDraft.workspace_id == workspace_id,
            )
            .order_by(CreativeDraft.updated_at.desc(), CreativeDraft.id.desc())
        )
    ).scalars().all()
    return {"items": [_creative_payload(item) for item in rows], "total": len(rows)}


async def generate_creative_draft(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
    draft_type: str,
    variant_count: int = 3,
) -> dict[str, Any]:
    if draft_type not in DRAFT_TYPE_TITLES:
        raise AssistantServiceError("invalid_draft_type")
    brief = await _ensure_brief(session, tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
    variants, ai_trace = await _gemini_creative_variants(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        brief=brief,
        draft_type=draft_type,
        variant_count=variant_count,
    )
    if not variants:
        variants = _fallback_creative_variants(brief, draft_type, variant_count)
        ai_trace = {
            **ai_trace,
            "fallback_used": True,
            "reason_code": ai_trace.get("reason_code") or "creative_variants_fallback",
        }
    draft = CreativeDraft(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        brief_id=brief.id,
        draft_type=draft_type,
        status="draft",
        title=f"{DRAFT_TYPE_TITLES[draft_type]} для growth workflow",
        input_prompt=_brief_summary_text(brief),
        content_text=variants[0]["content"],
        meta={"variants": variants, "selected_variant": 0, "ai_trace": ai_trace},
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(draft)
    session.add(
        ManualAction(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            action_type="manual_step",
            title=f"Сгенерирован {DRAFT_TYPE_TITLES[draft_type].lower()}",
            notes="AI-ассистент подготовил черновики для подтверждения.",
            payload={"draft_type": draft_type},
            created_at=utcnow(),
        )
    )
    await session.flush()
    return {"ok": True, "draft": _creative_payload(draft)}


async def approve_creative_draft(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
    draft_id: int,
    selected_variant: int | None = None,
) -> dict[str, Any]:
    draft = (
        await session.execute(
            select(CreativeDraft).where(
                CreativeDraft.id == int(draft_id),
                CreativeDraft.tenant_id == tenant_id,
                CreativeDraft.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if draft is None:
        raise AssistantServiceError("creative_draft_not_found")
    variants = list((draft.meta or {}).get("variants") or [])
    idx = 0
    if variants:
        idx = max(0, min(int(selected_variant or 0), len(variants) - 1))
        selected = variants[idx]
        draft.content_text = _norm_text(selected.get("content"), 4000)
        meta = dict(draft.meta or {})
        meta["selected_variant"] = idx
        draft.meta = meta
    draft.status = "approved"
    draft.updated_at = utcnow()
    session.add(
        BusinessAsset(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            brief_id=draft.brief_id,
            user_id=user_id,
            asset_type=draft.draft_type,
            title=draft.title or DRAFT_TYPE_TITLES.get(draft.draft_type, draft.draft_type),
            value=draft.content_text,
            meta={"source_draft_id": draft.id, "selected_variant": idx},
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    session.add(
        ManualAction(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            action_type="approval",
            title=f"Подтверждён {DRAFT_TYPE_TITLES.get(draft.draft_type, draft.draft_type).lower()}",
            notes="Черновик подтверждён оператором или клиентом.",
            payload={"draft_id": draft.id, "selected_variant": idx},
            created_at=utcnow(),
        )
    )
    await session.flush()
    return {"ok": True, "draft": _creative_payload(draft)}
