"""
AlertService — отправка алертов и дайджестов в Telegram-бот для автономной системы прогрева.

Использует DIGEST_BOT_TOKEN / DIGEST_CHAT_ID из окружения.
Fallback: ADMIN_BOT_TOKEN / ADMIN_TELEGRAM_ID.
Все методы async, никогда не поднимают исключений.
"""

from __future__ import annotations

import html
import logging
import os
from datetime import date
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

_TG_API = "https://api.telegram.org/bot{token}/sendMessage"

# Эмодзи для отображения фазы прогрева
_PHASE_EMOJI: dict[str, str] = {
    "registration": "🆕",
    "early_warmup": "🌱",
    "active_warmup": "🔥",
    "mature": "✅",
    "packaging": "📦",
    "quarantine": "🔴",
    "frozen": "🧊",
}


class AlertService:
    """
    Сервис алертов для автономной системы прогрева.

    Отправляет структурированные HTML-сообщения в Telegram-чат.
    Если токен или chat_id не заданы — тихо отключается (self.enabled = False).
    """

    def __init__(self) -> None:
        # Первый приоритет: DIGEST_BOT_TOKEN + DIGEST_CHAT_ID
        self.bot_token: str = os.environ.get("DIGEST_BOT_TOKEN", "").strip()
        self.chat_id: str = os.environ.get("DIGEST_CHAT_ID", "").strip()

        # Fallback: ADMIN_BOT_TOKEN + ADMIN_TELEGRAM_ID
        if not self.bot_token:
            self.bot_token = os.environ.get("ADMIN_BOT_TOKEN", "").strip()
        if not self.chat_id:
            admin_id = os.environ.get("ADMIN_TELEGRAM_ID", "").strip()
            if admin_id and admin_id != "0":
                self.chat_id = admin_id

        if self.bot_token and self.chat_id:
            self.enabled = True
            log.info("AlertService: включён, chat_id=%s", self.chat_id)
        else:
            self.enabled = False
            log.warning(
                "AlertService: отключён — не заданы DIGEST_BOT_TOKEN/DIGEST_CHAT_ID "
                "или ADMIN_BOT_TOKEN/ADMIN_TELEGRAM_ID"
            )

    # ------------------------------------------------------------------
    # Низкоуровневая отправка
    # ------------------------------------------------------------------

    async def send_alert(self, message: str) -> bool:
        """
        Отправить сообщение в Telegram.

        Возвращает True при успехе, False при любой ошибке.
        Никогда не поднимает исключений.
        """
        if not self.enabled:
            return False

        url = _TG_API.format(token=self.bot_token)
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return True
                    body = await resp.text()
                    log.error(
                        "AlertService: Telegram вернул %s — %s",
                        resp.status,
                        body[:200],
                    )
                    return False
        except Exception as exc:  # noqa: BLE001
            log.error("AlertService: ошибка отправки — %s", exc)
            return False

    # ------------------------------------------------------------------
    # Алерты по событиям
    # ------------------------------------------------------------------

    async def alert_frozen(
        self,
        phone: str,
        name: str,
        phase: str,
        day: int,
        last_action: str,
        health: int,
    ) -> None:
        """Алерт: аккаунт заморожен (обнаружена заморозка Telegram)."""
        if not self.enabled:
            return

        safe_phone = html.escape(phone)
        safe_name = html.escape(name)
        safe_phase = html.escape(phase)
        safe_action = html.escape(last_action)

        text = (
            f"🔴 <b>FROZEN</b>: {safe_phone} ({safe_name})\n"
            f"Фаза: {safe_phase} (день {day})\n"
            f"Последнее действие: {safe_action}\n"
            f"Health: {health} → 0"
        )
        await self.send_alert(text)

    async def alert_flood_wait(
        self,
        phone: str,
        name: str,
        wait_seconds: int,
        phase_from: str,
        phase_to: str,
        pause_hours: int,
        health_from: int,
        health_to: int,
    ) -> None:
        """Алерт: FloodWait — откат фазы и пауза прогрева."""
        if not self.enabled:
            return

        safe_phone = html.escape(phone)
        safe_name = html.escape(name)
        safe_from = html.escape(phase_from)
        safe_to = html.escape(phase_to)

        text = (
            f"🟡 <b>FLOOD_WAIT</b>: {safe_phone} ({safe_name}) — {wait_seconds}s\n"
            f"Откат: {safe_from} → {safe_to}\n"
            f"Пауза: {pause_hours} часов\n"
            f"Health: {health_from} → {health_to}"
        )
        await self.send_alert(text)

    async def alert_packaging_needed(
        self,
        phone: str,
        account_id: int,
        day: int,
    ) -> None:
        """Алерт: аккаунт готов к упаковке, но пресет не создан."""
        if not self.enabled:
            return

        safe_phone = html.escape(phone)

        text = (
            f"🟠 <b>PACKAGING</b>: {safe_phone} готов к упаковке (день {day})\n"
            f"Preset не создан — аккаунт ждёт\n"
            f"Создай preset через API или UI"
        )
        await self.send_alert(text)

    async def alert_quarantine_lifted(
        self,
        phone: str,
        name: str,
        was_hours: int,
        phase_from: str,
        phase_to: str,
    ) -> None:
        """Алерт: карантин снят, аккаунт возвращается в прогрев."""
        if not self.enabled:
            return

        safe_phone = html.escape(phone)
        safe_name = html.escape(name)
        safe_from = html.escape(phase_from)
        safe_to = html.escape(phase_to)

        text = (
            f"⚪ <b>QUARANTINE LIFTED</b>: {safe_phone} ({safe_name})\n"
            f"Была: {was_hours} часов\n"
            f"Фаза: {safe_from} → {safe_to}"
        )
        await self.send_alert(text)

    # ------------------------------------------------------------------
    # Дайджесты
    # ------------------------------------------------------------------

    async def send_daily_digest(self, stats: dict) -> None:
        """
        Отправить ежедневный дайджест состояния прогрева.

        Ожидаемые ключи stats:
            active_count    int
            total_count     int
            sessions_24h    int
            actions_24h     int
            success_count   int
            skip_count      int
            accounts        list[dict] — поля: phone, name, phase, day, health, sessions
            errors          dict — поля: flood, spam, frozen
            next_packaging  list[str]  — телефоны, готовые к упаковке
        """
        if not self.enabled:
            return

        today = date.today().strftime("%d.%m.%Y")
        active = stats.get("active_count", 0)
        total = stats.get("total_count", 0)
        sessions_24h = stats.get("sessions_24h", 0)
        actions_24h = stats.get("actions_24h", 0)
        success = stats.get("success_count", 0)
        skip = stats.get("skip_count", 0)

        errors: dict = stats.get("errors", {})
        flood = errors.get("flood", 0)
        spam = errors.get("spam", 0)
        frozen = errors.get("frozen", 0)

        lines: list[str] = [
            f"📊 <b>NEURO WARMUP</b> — {today}",
            "",
            f"Аккаунтов: {active}/{total}",
            f"Сессий за 24ч: {sessions_24h}",
            f"Действий: {actions_24h} ({success}✅ / {skip}⏭️)",
            "",
        ]

        accounts: list[dict] = stats.get("accounts", [])
        if accounts:
            for acc in accounts:
                acc_phone = html.escape(str(acc.get("phone", "")))
                acc_phase = html.escape(str(acc.get("phase", "?")))
                acc_day = acc.get("day", 0)
                acc_health = acc.get("health", 0)
                acc_sessions = acc.get("sessions", 0)
                emoji = _PHASE_EMOJI.get(acc_phase, "•")
                lines.append(
                    f"{emoji} {acc_phone} {acc_phase} d{acc_day} "
                    f"| hp:{acc_health} | s:{acc_sessions}"
                )
            lines.append("")

        lines.append(f"Ошибки: {flood}💧 {spam}🚫 {frozen}🧊")

        next_pkg: list[str] = stats.get("next_packaging", [])
        if next_pkg:
            safe_pkg = ", ".join(html.escape(p) for p in next_pkg)
            lines.append(f"📦 К упаковке: {safe_pkg}")

        await self.send_alert("\n".join(lines))

    async def send_weekly_report(self, stats: dict) -> None:
        """
        Отправить недельный отчёт по прогреву.

        Ожидаемые ключи stats:
            progressions        list[dict] — phone, from_phase, to_phase
            health_trend        dict — avg, min
            safety              dict — flood, spam, frozen
            total_actions       int
            actions_breakdown   dict — произвольный маппинг тип→количество
        """
        if not self.enabled:
            return

        today = date.today().strftime("%d.%m.%Y")

        total_actions = stats.get("total_actions", 0)
        health_trend: dict = stats.get("health_trend", {})
        avg_health = health_trend.get("avg", 0)
        min_health = health_trend.get("min", 0)

        safety: dict = stats.get("safety", {})
        flood = safety.get("flood", 0)
        spam = safety.get("spam", 0)
        frozen = safety.get("frozen", 0)

        lines: list[str] = [
            f"📋 <b>NEURO WARMUP — Недельный отчёт</b> ({today})",
            "",
            f"Всего действий: {total_actions}",
            f"Health: avg {avg_health} / min {min_health}",
            f"Безопасность: {flood}💧 flood | {spam}🚫 spam | {frozen}🧊 frozen",
            "",
        ]

        progressions: list[dict] = stats.get("progressions", [])
        if progressions:
            lines.append("<b>Переходы фаз:</b>")
            for prog in progressions:
                p_phone = html.escape(str(prog.get("phone", "")))
                p_from = html.escape(str(prog.get("from_phase", "?")))
                p_to = html.escape(str(prog.get("to_phase", "?")))
                lines.append(f"  {p_phone}: {p_from} → {p_to}")
            lines.append("")

        breakdown: dict = stats.get("actions_breakdown", {})
        if breakdown:
            lines.append("<b>Действия по типам:</b>")
            for action_type, count in breakdown.items():
                lines.append(f"  {html.escape(str(action_type))}: {count}")

        await self.send_alert("\n".join(lines))
