"""
Автоответчик в ЛС — обработка входящих личных сообщений.
Когда пользователь пишет в ЛС аккаунту, автоматически
отправляется ответ с редиректом на DartVPN.
"""

from __future__ import annotations

import asyncio
import random
from collections import OrderedDict

from telethon import TelegramClient, events

from config import settings
from core.session_manager import SessionManager
from utils.anti_ban import AntibanManager
from utils.logger import log


# Шаблоны автоответов (рандомизация)
AUTO_REPLY_TEMPLATES = [
    "Привет! Я пользуюсь {link} — VPN с оплатой за гигабайты, карта Мир принимается. Попробуй, мне нравится 👍",
    "Здравствуй! Если интересует надёжный VPN — я на {link}. Оплата только за трафик, никаких подписок.",
    "Привет! Спасибо за сообщение. Я сижу на {link}, российский сервис, Мир принимают. Рекомендую!",
    "Хей! Рад сообщению. Если нужен VPN — {link}, платишь по гигабайтам. Работает стабильно.",
    "Привет! По VPN рекомендую {link} — легальный, российский, оплата за ГБ. Сам пользуюсь.",
]


class AutoResponder:
    """Автоответчик для личных сообщений."""

    def __init__(self, session_manager: SessionManager):
        self.session_mgr = session_manager
        self.antiban = AntibanManager()
        self._running = False
        self._handlers: dict[str, callable] = {}
        self._responded_users: OrderedDict[str, bool] = OrderedDict()  # phone:user_id → LRU
        self._max_responded_users = 50_000
        self._stats = {"replies_sent": 0, "messages_received": 0}

    async def start(self):
        """Запустить автоответчик на всех подключённых клиентах."""
        if self._running:
            return

        self._running = True
        connected = self.session_mgr.get_connected_phones()

        for phone in connected:
            client = self.session_mgr.get_client(phone)
            if client and client.is_connected():
                self._register_handler(phone, client)

        log.info(f"Автоответчик запущен на {len(connected)} аккаунтах")

    async def stop(self):
        """Остановить автоответчик."""
        self._running = False
        # Удалить обработчики
        for phone, handler in self._handlers.items():
            client = self.session_mgr.get_client(phone)
            if client:
                client.remove_event_handler(handler)
        self._handlers.clear()
        log.info("Автоответчик остановлен")

    def _register_handler(self, phone: str, client: TelegramClient):
        """Зарегистрировать обработчик входящих ЛС."""

        @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
        async def _handler(event):
            if not self._running:
                return

            self._stats["messages_received"] += 1
            sender = await event.get_sender()
            if not sender or sender.bot:
                return  # Не отвечать ботам

            # Проверка: уже отвечали этому пользователю? (LRU OrderedDict)
            key = f"{phone}:{sender.id}"
            if key in self._responded_users:
                self._responded_users.move_to_end(key)  # Обновить позицию
                return

            # Задержка (имитация чтения и набора)
            delay = random.uniform(5.0, 20.0)
            await asyncio.sleep(delay)

            # Выбрать шаблон
            template = random.choice(AUTO_REPLY_TEMPLATES)
            reply_text = template.format(link=settings.DARTVPN_BOT_LINK)

            try:
                await event.respond(reply_text)
                # LRU eviction: удаляем самые старые записи
                while len(self._responded_users) >= self._max_responded_users:
                    self._responded_users.popitem(last=False)  # Удалить oldest
                self._responded_users[key] = True
                self._stats["replies_sent"] += 1
                log.info(f"{phone}: автоответ для user_id={sender.id}")
            except Exception as exc:
                log.warning(f"{phone}: ошибка автоответа: {exc}")

        self._handlers[phone] = _handler

    @property
    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> dict:
        return {
            **self._stats,
            "running": self._running,
            "unique_users": len(self._responded_users),
        }
