"""Digest bot delivery for parser summaries and daily discovery reports."""

from __future__ import annotations

from html import escape
from typing import Any

from aiogram import Bot
from sqlalchemy import select

from config import settings
from storage.models import Channel
from storage.sqlite_db import async_session


def digest_configured() -> bool:
    return bool(str(settings.DIGEST_BOT_TOKEN or "").strip()) and bool(str(settings.DIGEST_CHAT_ID or "").strip())


async def _send_text(text: str) -> dict[str, Any]:
    token = str(settings.DIGEST_BOT_TOKEN or "").strip()
    chat_id = str(settings.DIGEST_CHAT_ID or "").strip()
    if not token:
        return {"ok": False, "error": "digest_bot_token_missing"}
    if not chat_id:
        return {"ok": False, "error": "digest_chat_id_missing"}
    bot = Bot(token=token)
    try:
        message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return {
            "ok": True,
            "chat_id": chat_id,
            "message_id": int(message.message_id),
        }
    finally:
        await bot.session.close()


async def send_digest_text(text: str) -> dict[str, Any]:
    """Public helper for sending arbitrary digest messages to the configured chat."""
    payload = await _send_text(text)
    payload["kind"] = "custom_text"
    return payload


def _format_channel_line(item: dict[str, Any]) -> str:
    title = escape(str(item.get("title") or "Без названия"))
    username = str(item.get("username") or "").strip()
    username_part = f" @{escape(username)}" if username else ""
    subscribers = int(item.get("subscribers") or 0)
    return f"• <b>{title}</b>{username_part} — {subscribers} подписчиков"


def build_parser_task_digest(task: dict[str, Any], report: dict[str, Any]) -> str:
    kind = str(report.get("kind") or task.get("kind") or "discovery").strip()
    user_id = task.get("user_id")
    heading = {
        "keyword_search": "🔎 <b>Новые результаты поиска по ключевым словам</b>",
        "similar_search": "🔗 <b>Новые похожие каналы</b>",
        "manual_add": "➕ <b>Канал добавлен вручную</b>",
    }.get(kind, "🧠 <b>Новая сводка поиска каналов</b>")
    lines = [
        heading,
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    if user_id is not None:
        lines.append(f"Пользователь: <code>{int(user_id)}</code>")
    if kind == "keyword_search":
        keywords = ", ".join(str(item).strip() for item in list(report.get("keywords") or []) if str(item).strip())
        if keywords:
            lines.append(f"Запрос: <b>{escape(keywords)}</b>")
    lines.append(f"Найдено: <b>{int(report.get('found', 0))}</b>")
    lines.append(f"Сохранено: <b>{int(report.get('saved', 0))}</b>")

    items = list(report.get("items") or [])
    if items:
        lines.extend(["", "<b>Первые результаты:</b>"])
        for item in items[: max(1, int(settings.DIGEST_MAX_ITEMS))]:
            lines.append(_format_channel_line(item))
    elif kind == "manual_add" and report.get("title"):
        lines.extend(
            [
                "",
                _format_channel_line(
                    {
                        "title": report.get("title"),
                        "username": "",
                        "subscribers": report.get("subscribers", 0),
                    }
                ),
            ]
        )
    return "\n".join(lines)


async def send_parser_task_digest(task: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    text = build_parser_task_digest(task, report)
    payload = await _send_text(text)
    payload["kind"] = "parser_task"
    return payload


async def build_daily_digest_summary(*, user_id: int | None = None) -> str:
    async with async_session() as session:
        query = select(Channel).order_by(Channel.created_at.desc(), Channel.id.desc())
        if user_id is not None:
            query = query.where(Channel.user_id == user_id)
        result = await session.execute(query)
        channels = list(result.scalars().all())

    discovered = [channel for channel in channels if (channel.review_state or "discovered") == "discovered"]
    candidates = [channel for channel in channels if (channel.review_state or "") == "candidate"]
    approved = [
        channel for channel in channels
        if (channel.review_state or "") == "approved" and (channel.publish_mode or "research_only") == "auto_allowed"
    ]

    lines = [
        "🗞 <b>Ежедневная сводка по каналам</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Всего в базе: <b>{len(channels)}</b>",
        f"Новые найденные: <b>{len(discovered)}</b>",
        f"Ждут проверки: <b>{len(candidates)}</b>",
        f"Готовы к работе: <b>{len(approved)}</b>",
    ]
    if approved:
        lines.extend(["", "<b>Готовые каналы:</b>"])
        for channel in approved[: max(1, int(settings.DIGEST_MAX_ITEMS))]:
            lines.append(
                _format_channel_line(
                    {
                        "title": channel.title,
                        "username": channel.username,
                        "subscribers": channel.subscribers,
                    }
                )
            )
    if candidates:
        lines.extend(["", "<b>Кандидаты для проверки:</b>"])
        for channel in candidates[: max(1, int(settings.DIGEST_MAX_ITEMS))]:
            lines.append(
                _format_channel_line(
                    {
                        "title": channel.title,
                        "username": channel.username,
                        "subscribers": channel.subscribers,
                    }
                )
            )
    return "\n".join(lines)


async def send_daily_digest_summary(*, user_id: int | None = None) -> dict[str, Any]:
    text = await build_daily_digest_summary(user_id=user_id)
    payload = await send_digest_text(text)
    payload["kind"] = "daily_summary"
    return payload
