"""
Уведомления в Telegram — форвард ключевых событий в отдельный чат.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from aiogram import Bot

from config import settings
from utils.logger import log


class TelegramNotifier:
    """Отправка уведомлений о событиях в Telegram-чат."""

    def __init__(self, bot: Optional[Bot] = None):
        self._bot = bot
        self._chat_id = settings.ADMIN_TELEGRAM_ID
        self._enabled = True
        self._queue: list[str] = []
        self._stats = {"sent": 0, "failed": 0}

    def set_bot(self, bot: Bot):
        self._bot = bot

    @property
    def is_enabled(self) -> bool:
        return self._enabled and self._bot is not None and self._chat_id != 0

    async def notify(self, text: str, silent: bool = False):
        """Отправить уведомление админу."""
        if not self.is_enabled:
            return

        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode="HTML",
                disable_notification=silent,
            )
            self._stats["sent"] += 1
        except Exception as exc:
            self._stats["failed"] += 1
            log.debug(f"Ошибка отправки уведомления: {exc}")

    async def comment_sent(
        self,
        account_phone: str,
        channel_title: str,
        comment_text: str,
        scenario: str,
    ):
        """Уведомление об отправленном комментарии."""
        now = datetime.now().strftime("%H:%M:%S")
        text = (
            f"💬 <b>Комментарий отправлен</b>\n"
            f"⏱ {now}\n"
            f"👤 <code>{account_phone}</code>\n"
            f"📢 {channel_title}\n"
            f"🎯 Сценарий: {scenario}\n"
            f"💭 {comment_text[:200]}"
        )
        await self.notify(text, silent=True)

    async def error_occurred(self, account_phone: str, error_type: str, details: str = ""):
        """Уведомление об ошибке."""
        text = (
            f"⚠️ <b>Ошибка</b>\n"
            f"👤 <code>{account_phone}</code>\n"
            f"Тип: {error_type}\n"
            f"Детали: {details[:300]}"
        )
        await self.notify(text)

    async def session_dead(self, account_phone: str, error_type: str = "AuthKeyUnregistered"):
        """Уведомление о мёртвой сессии (auth key отозван Telegram)."""
        text = (
            f"💀 <b>СЕССИЯ МЕРТВА</b>\n"
            f"👤 <code>{account_phone}</code>\n"
            f"Ошибка: {error_type}\n"
            f"Auth key отозван сервером. Восстановление невозможно без SMS."
        )
        await self.notify(text)

    async def health_report(self, alive: int, dead: int, unknown: int):
        """Отчёт о здоровье сессий."""
        text = (
            f"🏥 <b>Здоровье сессий</b>\n"
            f"✅ Живых: {alive}\n"
            f"💀 Мёртвых: {dead}\n"
            f"❓ Неизвестно: {unknown}"
        )
        await self.notify(text, silent=True)

    async def account_banned(self, account_phone: str):
        """Уведомление о бане аккаунта."""
        text = (
            f"🚫 <b>АККАУНТ ЗАБАНЕН</b>\n"
            f"👤 <code>{account_phone}</code>\n"
            f"Требуется замена или разблокировка."
        )
        await self.notify(text)

    async def system_started(self, accounts: int, channels: int):
        """Уведомление о запуске системы."""
        text = (
            f"🚀 <b>Система запущена</b>\n"
            f"👤 Аккаунтов: {accounts}\n"
            f"📢 Каналов: {channels}\n"
            f"⏱ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        await self.notify(text)

    async def system_stopped(self, sent: int, failed: int):
        """Уведомление об остановке."""
        text = (
            f"⏸ <b>Система остановлена</b>\n"
            f"📨 Отправлено: {sent}\n"
            f"❌ Ошибок: {failed}"
        )
        await self.notify(text)

    async def daily_report(self, stats: dict):
        """Ежедневный отчёт."""
        text = (
            f"📊 <b>Ежедневный отчёт</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💬 Комментариев: {stats.get('comments_today', 0)}\n"
            f"✅ Успешных: {stats.get('sent', 0)}\n"
            f"❌ Ошибок: {stats.get('failed', 0)}\n"
            f"📢 Каналов: {stats.get('channels', 0)}\n"
            f"👤 Аккаунтов: {stats.get('accounts', 0)}"
        )
        await self.notify(text)

    def get_stats(self) -> dict:
        return dict(self._stats)


# Глобальный экземпляр
notifier = TelegramNotifier()
