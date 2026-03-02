"""
Создание каналов-переходников для аккаунтов.

Для каждого аккаунта:
1. Создаёт приватный Telegram-канал (например «Полезности | Интернет»)
2. Публикует пост со ссылкой на @DartVPNBot
3. Закрепляет пост в канале
4. Обновляет bio аккаунта — добавляет ссылку на канал-переходник

Это ключевой элемент Сценария A: аватарка → профиль → канал → пост → DartVPN.
"""

from __future__ import annotations

import asyncio
import random
from typing import Optional

from google import genai
from google.genai import types
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
)
from telethon.tl.functions.channels import (
    CreateChannelRequest,
    EditPhotoRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.messages import (
    ExportChatInviteRequest,
    UpdatePinnedMessageRequest,
)
from telethon.tl.functions.account import UpdateProfileRequest

from config import settings
from core.session_manager import SessionManager
from core.account_manager import AccountManager
from utils.logger import log


# ────────────────────────────────────────────────────────────
# AI-промпты для генерации контента канала-переходника
# ────────────────────────────────────────────────────────────

CHANNEL_NAME_PROMPT = """Сгенерируй название и описание для Telegram-канала.
Канал должен выглядеть как личный канал обычного пользователя, который делится полезной информацией.

Стиль: {style}

Тематики на выбор (выбери одну):
- Цифровая безопасность и VPN
- Полезные сервисы и приложения
- IT-лайфхаки и технологии
- Обход блокировок и интернет-свобода
- Нейросети и полезные инструменты

Требования:
1. Название — 2-4 слова, привлекательное, НЕ содержит слово "VPN" или "DartVPN"
2. Описание — 1-2 предложения, до 100 символов, интригующее
3. Текст поста — 150-300 символов, рассказ о полезном VPN-сервисе с оплатой по гигабайтам.
   Пост должен содержать ссылку {bot_link} и выглядеть как искренняя рекомендация.
   Добавь 2-3 эмодзи для живости.

Ответь СТРОГО в формате:
CHANNEL_NAME: ...
CHANNEL_DESC: ...
POST_TEXT: ...
"""

# Готовые варианты на случай если AI недоступен
FALLBACK_CHANNELS = [
    {
        "name": "Полезности | Интернет",
        "desc": "Делюсь полезными находками для свободного интернета",
        "post": "🔥 Нашёл крутой VPN-сервис — платишь только за гигабайты, а не помесячно!\n\nРоссийский, принимает Мир. Работает стабильно, скорости топ.\n\nВот бот: {bot_link}\n\nРекомендую попробовать, бесплатный тест есть 👍",
    },
    {
        "name": "Digital лайфхаки",
        "desc": "Технологии • Сервисы • Безопасность",
        "post": "⚡ Совет дня: если ваш VPN дорогой — попробуйте оплату по трафику!\n\nЯ перешёл на {bot_link} — платишь только за использованные ГБ. Карта Мир принимается.\n\nЗа месяц экономлю кучу денег 🔥",
    },
    {
        "name": "Свободный интернет",
        "desc": "Обходим блокировки вместе",
        "post": "Ребят, делюсь находкой 🔐\n\nЕсть VPN-сервис с оплатой по гигабайтам — не надо платить за целый месяц если мало используешь.\n\nПлюс российский, карта Мир ок: {bot_link}\n\nПользуюсь уже месяц, полёт нормальный ✌️",
    },
    {
        "name": "Tech советы",
        "desc": "Лучшие инструменты и сервисы каждый день",
        "post": "💡 Как платить за VPN меньше?\n\nОтвет: оплата по трафику! Не сидишь в VPN 24/7 — не платишь за месяц.\n\nЯ пользуюсь этим ботом: {bot_link}\n\nЛегальный, быстрый, принимает Мир 🇷🇺",
    },
    {
        "name": "Интернет без границ",
        "desc": "Лайфхаки для свободного интернета",
        "post": "Народ, кто ищет нормальный VPN — попробуйте {bot_link} 🔥\n\nФишка в том что платишь по гигабайтам, а не за месяц. Российский сервис, Мир принимают.\n\nУ меня работает без проблем уже пару недель 👌",
    },
    {
        "name": "Цифровой минимализм",
        "desc": "Простые решения для сложных задач",
        "post": "🛡 Сегодня про VPN без переплат.\n\nНашёл сервис где платишь только за гигабайты которые реально использовал.\n\nВот тут: {bot_link}\n\nКарта Мир работает, скорость норм. Рекомендую 👍",
    },
]

STYLES_FOR_CHANNELS = ["expert", "casual", "business", "student", "tech", "lifestyle"]


class ChannelSetup:
    """Создание и управление каналами-переходниками."""

    def __init__(
        self,
        session_manager: SessionManager,
        account_manager: AccountManager,
    ):
        self.session_mgr = session_manager
        self.account_mgr = account_manager
        self._ai_client: Optional[genai.Client] = None
        self._init_ai()

    def _init_ai(self):
        if settings.GEMINI_API_KEY:
            self._ai_client = genai.Client(api_key=settings.GEMINI_API_KEY)

    async def generate_channel_content(self, style: str = "casual") -> dict:
        """
        Сгенерировать контент для канала-переходника через AI.
        Возвращает {"name": str, "desc": str, "post": str}.
        """
        bot_link = settings.DARTVPN_BOT_LINK or "https://t.me/DartVPNBot"

        if not self._ai_client:
            return self._get_fallback(bot_link)

        prompt = CHANNEL_NAME_PROMPT.format(style=style, bot_link=bot_link)

        try:
            response = await asyncio.to_thread(
                self._ai_client.models.generate_content,
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.95,
                    max_output_tokens=500,
                ),
            )

            if not response or not response.text:
                return self._get_fallback(bot_link)

            return self._parse_response(response.text, bot_link)

        except Exception as exc:
            log.warning(f"Ошибка AI генерации канала: {exc}")
            return self._get_fallback(bot_link)

    def _parse_response(self, text: str, bot_link: str) -> dict:
        """Парсинг ответа AI."""
        result = {"name": "", "desc": "", "post": ""}

        for line in text.strip().split("\n"):
            line = line.strip()
            upper = line.upper()
            if upper.startswith("CHANNEL_NAME:"):
                result["name"] = line.split(":", 1)[1].strip()
            elif upper.startswith("CHANNEL_DESC:"):
                result["desc"] = line.split(":", 1)[1].strip()
            elif upper.startswith("POST_TEXT:"):
                result["post"] = line.split(":", 1)[1].strip()

        # Валидация
        if not result["name"] or not result["post"]:
            return self._get_fallback(bot_link)

        # Убедиться что ссылка на бота есть в посте
        if bot_link not in result["post"] and "@DartVPNBot" not in result["post"]:
            result["post"] += f"\n\n{bot_link}"

        if len(result["desc"]) > 255:
            result["desc"] = result["desc"][:252] + "..."

        return result

    @staticmethod
    def _get_fallback(bot_link: str) -> dict:
        """Случайный фоллбэк-контент."""
        fb = random.choice(FALLBACK_CHANNELS)
        return {
            "name": fb["name"],
            "desc": fb["desc"],
            "post": fb["post"].format(bot_link=bot_link),
        }

    async def create_redirect_channel(
        self,
        phone: str,
        content: Optional[dict] = None,
        style: str = "casual",
    ) -> dict:
        """
        Создать канал-переходник для одного аккаунта.

        1. Создаёт канал
        2. Публикует пост с DartVPN ссылкой
        3. Закрепляет пост
        4. Обновляет bio аккаунта (добавляет ссылку на канал)

        Возвращает:
        {
            "phone": str,
            "success": bool,
            "channel_title": str,
            "channel_link": str,
            "error": str | None,
        }
        """
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return {
                "phone": phone,
                "success": False,
                "channel_title": "",
                "channel_link": "",
                "error": "Клиент не подключён",
            }

        # Генерация контента если не передан
        if not content:
            content = await self.generate_channel_content(style)

        channel_title = content["name"]
        channel_desc = content["desc"]
        post_text = content["post"]

        try:
            # ─── Шаг 1: Создаём канал ───
            log.info(f"{phone}: создаю канал-переходник «{channel_title}»")

            result = await client(CreateChannelRequest(
                title=channel_title,
                about=channel_desc[:255],
                broadcast=True,  # Канал, не группа
            ))

            # Достаём entity канала
            channel = result.chats[0]
            channel_id = channel.id

            log.info(f"{phone}: канал создан (id={channel_id})")

            # Небольшая пауза для стабильности
            await asyncio.sleep(random.uniform(1.0, 3.0))

            # ─── Шаг 2: Публикуем пост со ссылкой ───
            entity = await client.get_entity(channel_id)
            sent_message = await client.send_message(entity, post_text)

            log.info(f"{phone}: пост опубликован (msg_id={sent_message.id})")
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # ─── Шаг 3: Закрепляем пост ───
            try:
                await client(UpdatePinnedMessageRequest(
                    peer=entity,
                    id=sent_message.id,
                    silent=True,
                ))
                log.info(f"{phone}: пост закреплён")
            except Exception as exc:
                log.debug(f"{phone}: не удалось закрепить пост: {exc}")

            await asyncio.sleep(random.uniform(0.5, 1.5))

            # ─── Шаг 4: Получаем ссылку на канал ───
            channel_link = await self._get_channel_link(client, entity, channel)

            # ─── Шаг 5: Обновляем bio аккаунта ───
            await self._update_bio_with_channel(client, phone, channel_link)

            # Сохраняем ссылку на канал-переходник в настройки
            if channel_link:
                settings.DARTVPN_CHANNEL_LINK = channel_link

            return {
                "phone": phone,
                "success": True,
                "channel_title": channel_title,
                "channel_link": channel_link,
                "error": None,
            }

        except FloodWaitError as e:
            log.warning(f"{phone}: FloodWait {e.seconds}с при создании канала")
            return {
                "phone": phone,
                "success": False,
                "channel_title": channel_title,
                "channel_link": "",
                "error": f"FloodWait {e.seconds}с",
            }

        except Exception as exc:
            log.error(f"{phone}: ошибка создания канала: {exc}")
            return {
                "phone": phone,
                "success": False,
                "channel_title": channel_title,
                "channel_link": "",
                "error": str(exc),
            }

    async def _get_channel_link(self, client: TelegramClient, entity, channel) -> str:
        """Получить ссылку на канал (username или invite link)."""
        # Если у канала есть username — используем его
        if hasattr(channel, "username") and channel.username:
            return f"https://t.me/{channel.username}"

        # Иначе создаём invite-link
        try:
            invite = await client(ExportChatInviteRequest(
                peer=entity,
                legacy_revoke_permanent=True,
            ))
            return invite.link
        except Exception as exc:
            log.debug(f"Не удалось создать invite-link: {exc}")
            return ""

    async def _update_bio_with_channel(
        self,
        client: TelegramClient,
        phone: str,
        channel_link: str,
    ):
        """Обновить bio аккаунта: добавить ссылку на канал-переходник."""
        if not channel_link:
            return

        try:
            # Получаем текущий профиль
            me = await client.get_me()
            current_bio = me.about or ""

            # Формируем новый bio со ссылкой
            # Если bio пустой — просто ставим ссылку
            if not current_bio:
                new_bio = f"👇 Подробнее в канале\n{channel_link}"
            elif channel_link in current_bio:
                # Ссылка уже есть
                return
            else:
                # Добавляем ссылку к существующему bio
                # Telegram bio макс 70 символов
                max_bio_for_link = 70 - len(channel_link) - 2
                if max_bio_for_link > 10:
                    trimmed_bio = current_bio[:max_bio_for_link].rstrip()
                    new_bio = f"{trimmed_bio}\n{channel_link}"
                else:
                    new_bio = channel_link

            await client(UpdateProfileRequest(about=new_bio[:70]))
            log.info(f"{phone}: bio обновлён с каналом-переходником")

        except Exception as exc:
            log.warning(f"{phone}: ошибка обновления bio: {exc}")

    async def setup_all_accounts(
        self,
        progress_callback=None,
    ) -> dict:
        """
        Создать канал-переходник для каждого подключённого аккаунта.

        Args:
            progress_callback: async callable(phone, status_text) для отчёта прогресса

        Returns:
            {"total": int, "success": int, "failed": int, "results": list}
        """
        connected = self.session_mgr.get_connected_phones()
        if not connected:
            return {"total": 0, "success": 0, "failed": 0, "results": []}

        results = []
        success_count = 0
        styles = STYLES_FOR_CHANNELS

        for i, phone in enumerate(connected):
            style = styles[i % len(styles)]

            if progress_callback:
                await progress_callback(
                    phone,
                    f"⏳ [{i + 1}/{len(connected)}] Создаю канал для {phone}...",
                )

            result = await self.create_redirect_channel(phone, style=style)
            results.append(result)

            if result["success"]:
                success_count += 1

            # Антибан пауза между аккаунтами
            if i < len(connected) - 1:
                delay = random.uniform(15.0, 30.0)
                log.debug(f"Пауза {delay:.0f}с перед следующим аккаунтом")
                await asyncio.sleep(delay)

        log.info(
            f"Каналы-переходники: {success_count}/{len(connected)} создано успешно"
        )

        return {
            "total": len(connected),
            "success": success_count,
            "failed": len(connected) - success_count,
            "results": results,
        }

    async def delete_redirect_channel(self, phone: str, channel_id: int) -> bool:
        """Удалить канал-переходник (на случай пересоздания)."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return False

        try:
            from telethon.tl.functions.channels import DeleteChannelRequest
            entity = await client.get_entity(channel_id)
            await client(DeleteChannelRequest(channel=entity))
            log.info(f"{phone}: канал {channel_id} удалён")
            return True
        except Exception as exc:
            log.warning(f"{phone}: ошибка удаления канала: {exc}")
            return False
