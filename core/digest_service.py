"""Digest bot delivery for parser summaries and daily discovery reports."""

from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import datetime as _dt, timezone as _tz
from html import escape
from typing import Any

from aiogram import Bot
from sqlalchemy import select

from config import settings
from storage.models import Channel
from storage.sqlite_db import async_session

_log = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096


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


async def build_daily_digest_summary(*, user_id: int | None = None, limit: int = 5000) -> str:
    async with async_session() as session:
        query = select(Channel).order_by(Channel.created_at.desc(), Channel.id.desc()).limit(limit)
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


# ---------------------------------------------------------------------------
# DigestReporter — real-time event → Telegram delivery
# ---------------------------------------------------------------------------

try:
    from core.event_bus import EventBus  # noqa: E402
except ImportError:
    EventBus = None  # type: ignore[misc,assignment]

_CATEGORIES = {
    "deploy":  ("DEPLOY",  "\U0001f680"),   # rocket
    "account": ("ACCOUNT", "\U0001f464"),   # person
    "health":  ("HEALTH",  "\U0001f4ca"),   # chart
    "parsing": ("PARSING", "\U0001f50d"),   # magnifying glass
    "farm":    ("FARM",    "\U0001f69c"),   # tractor
    "error":   ("ERROR",   "\U0001f6a8"),   # alert
    "system":  ("SYSTEM",  "\u2699\ufe0f"), # gear
}

_SKIP_KEYS = frozenset({"ts", "type", "category"})


def format_event_message(channel: str, data: dict) -> str:
    """Format a Redis event into a Telegram-ready text message."""
    category = channel.replace("nc:event:", "").split(":")[0] if "nc:event:" in channel else "system"
    label, emoji = _CATEGORIES.get(category, ("SYSTEM", "\u2699\ufe0f"))

    ts = data.get("ts", "")
    if ts:
        try:
            dt_obj = _dt.fromisoformat(ts)
            time_str = dt_obj.strftime("%H:%M")
        except (ValueError, TypeError):
            time_str = str(ts)[:5]
    else:
        time_str = _dt.now(_tz.utc).strftime("%H:%M")

    header = f"{emoji} <b>{label}</b> | {time_str}"

    lines = [header]
    for key, value in data.items():
        if key in _SKIP_KEYS:
            continue
        display_key = key.replace("_", " ").capitalize()
        lines.append(f"{display_key}: {value}")

    return "\n".join(lines)


class DigestReporter:
    """Listens to Redis pub/sub events and sends formatted messages to Telegram digest chat.

    Batches messages within a time window to avoid Telegram rate limits.
    """

    def __init__(
        self,
        event_bus: EventBus,
        batch_window_sec: float = 2.0,
        max_per_minute: int = 30,
    ) -> None:
        self._bus = event_bus
        self._batch_window = batch_window_sec
        self._max_per_minute = max_per_minute
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._sent_count = 0
        self._sent_reset_at = 0.0
        self._dropped_count = 0

    async def start(self) -> None:
        """Start listener and sender tasks. Blocks forever."""
        await asyncio.gather(
            self._listen(),
            self._sender_loop(),
        )

    async def _listen(self) -> None:
        """Subscribe to all nc:event:* channels."""
        await self._bus.subscribe(
            ["nc:event:*"],
            self._on_event,
        )

    async def _on_event(self, channel: str, data: dict) -> None:
        """Format event and enqueue for batched sending. Drop if queue full."""
        text = format_event_message(channel, data)
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            self._dropped_count = min(self._dropped_count + 1, 1_000_000)

    async def _sender_loop(self) -> None:
        """Drain queue and send messages respecting rate limits."""
        loop = asyncio.get_running_loop()
        while True:
            messages: list[str] = []
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=10.0)
                messages.append(msg)
            except asyncio.TimeoutError:
                continue

            deadline = loop.time() + self._batch_window
            while loop.time() < deadline:
                try:
                    msg = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=max(0.01, deadline - loop.time()),
                    )
                    messages.append(msg)
                except asyncio.TimeoutError:
                    break

            now = _time.monotonic()
            if now - self._sent_reset_at > 60:
                self._sent_count = 0
                self._sent_reset_at = now

            if self._sent_count >= self._max_per_minute:
                self._dropped_count = min(self._dropped_count + len(messages), 1_000_000)
                _log.warning(
                    "digest_reporter: rate limit hit, dropping %d messages (total dropped: %d)",
                    len(messages), self._dropped_count,
                )
                # Sleep until window resets instead of busy-looping
                await asyncio.sleep(max(1, 60 - (now - self._sent_reset_at)))
                continue

            # Prepend drop count warning if any
            if self._dropped_count > 0:
                messages.insert(0, f"\u26a0\ufe0f Пропущено сообщений: {self._dropped_count}")
                self._dropped_count = 0

            combined = "\n\n".join(messages)
            if len(combined) > TELEGRAM_MESSAGE_LIMIT:
                for msg in messages:
                    if self._sent_count >= self._max_per_minute:
                        break
                    if digest_configured():
                        await send_digest_text(msg)
                    self._sent_count += 1
            else:
                if digest_configured():
                    await send_digest_text(combined)
                self._sent_count += 1
