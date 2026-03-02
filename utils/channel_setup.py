"""
Создание каналов-переходников для аккаунтов.

Для каждого аккаунта:
1. Создаёт приватный Telegram-канал
2. Ставит аватарку DartVPN
3. Публикует пост со скрытыми ссылками на @DartVPNBot (parse_mode='html')
4. Закрепляет пост в канале
5. Обновляет bio аккаунта — добавляет ссылку на канал-переходник

Это ключевой элемент Сценария A: аватарка → профиль → канал → пост → DartVPN.
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import CreateChannelRequest, EditPhotoRequest
from telethon.tl.functions.messages import (
    ExportChatInviteRequest,
    UpdatePinnedMessageRequest,
)
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.types import InputChatUploadedPhoto

from config import settings, BASE_DIR
from core.session_manager import SessionManager
from core.account_manager import AccountManager
from utils.logger import log


# ────────────────────────────────────────────────────────────
# Ссылка DartVPN (единая для всех постов и комментариев)
# ────────────────────────────────────────────────────────────
def _get_dartvpn_link() -> str:
    return settings.DARTVPN_BOT_LINK


def _link(text: str) -> str:
    """Обернуть текст в скрытую HTML-ссылку на DartVPN."""
    return f'<a href="{_get_dartvpn_link()}">{text}</a>'


# ────────────────────────────────────────────────────────────
# AI-промпт для генерации контента
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
1. Название — 2-4 слова, привлекательное, НЕ содержит "VPN" или "DartVPN"
2. Описание — 1-2 предложения, до 100 символов, интригующее

Ответь СТРОГО в формате:
CHANNEL_NAME: ...
CHANNEL_DESC: ...
"""


# ────────────────────────────────────────────────────────────
# 10 уникальных шаблонов постов для каналов-переходников
# Каждый пост содержит 2 скрытые HTML-ссылки (синий текст → бот)
# ────────────────────────────────────────────────────────────

FALLBACK_CHANNELS = [
    {
        "name": "Полезности | Интернет",
        "desc": "Советы, лайфхаки и сервисы для свободного интернета",
        "post": (
            f'🎯 {_link("DART VPN")} — интернет, как будто блокировок не существует!\n'
            '\n'
            'ꗃ VPN прямо в Telegram, созданный для тех, кто устал от сервисов, которые «ещё вчера работали».\n'
            '\n'
            'Почему выбирают DartVPN:\n'
            '🌐 Полный доступ ко всем ресурсам и YouTube в 4K\n'
            '☎️ Мессенджеры без обрывов — Telegram, WhatsApp, Discord\n'
            '⚡️ Скорость от 200 Мбит/с по Wi-Fi и мобильной сети\n'
            '🧠 Smart Connect — автовыбор самого быстрого сервера\n'
            '✅ Запускается за пару простых шагов\n'
            '🛡 Стабильно работает больше года, без привязки карты\n'
            '🎁 5 дней бесплатно + скидка 50% на следующий месяц\n'
            '\n'
            f'👇 Попробуйте бесплатно прямо сейчас\n'
            f'{_link("ПОПРОБОВАТЬ")}'
        ),
    },
    {
        "name": "Digital лайфхаки",
        "desc": "Технологии • Сервисы • Безопасность",
        "post": (
            f'🚀 {_link("DART VPN")} — летай без ограничений!\n'
            '\n'
            'Удобный и быстрый VPN прямо в Telegram-боте. Никаких приложений — всё в одном месте.\n'
            '\n'
            'Что получаешь:\n'
            '⚡️ Скорость от 200 Мбит/с — YouTube, стримы, игры\n'
            '🌐 Полный доступ к заблокированным сайтам и сервисам\n'
            '🧠 Технология Smart Connect — сервер подбирается автоматически\n'
            '☎️ Звонки и видео в мессенджерах без лагов\n'
            '🛡 Надёжная защита данных — работает стабильно больше года\n'
            '✅ Простая настройка — разберётся каждый за минуту\n'
            '🎁 Бесплатный пробный период 5 дней\n'
            '\n'
            f'👇 Подключайся бесплатно\n'
            f'{_link("ПОДКЛЮЧИТЬСЯ")}'
        ),
    },
    {
        "name": "Свободный интернет",
        "desc": "Обходим блокировки вместе — лайфхаки и инструменты",
        "post": (
            f'🔐 {_link("DART VPN")} — свобода в интернете начинается здесь\n'
            '\n'
            'VPN нового поколения, который работает прямо через Telegram. Забудь про сложные приложения.\n'
            '\n'
            'Преимущества:\n'
            '🌐 Доступ ко всем сайтам — YouTube, Instagram, Netflix в 4K\n'
            '⚡️ Молниеносная скорость — от 200 Мбит/с\n'
            '☎️ Стабильные звонки в WhatsApp и Discord без обрывов\n'
            '✅ Не нужно ничего скачивать — работает в Telegram\n'
            '🧠 Умный выбор сервера при каждом подключении\n'
            '🛡 Защита данных и анонимность, без привязки карты\n'
            '🎁 5 дней бесплатно + бонус 50% на первый месяц\n'
            '\n'
            f'👇 Начни пользоваться бесплатно\n'
            f'{_link("НАЧАТЬ")}'
        ),
    },
    {
        "name": "Tech советы",
        "desc": "Лучшие инструменты и digital-решения каждый день",
        "post": (
            f'💡 {_link("DART VPN")} — забудь про блокировки раз и навсегда\n'
            '\n'
            'ꗃ Быстрый и надёжный VPN, который живёт прямо в Telegram. Без лишних приложений и настроек.\n'
            '\n'
            'Почему это лучший выбор:\n'
            '🧠 Smart Connect — система сама выбирает быстрый сервер\n'
            '⚡️ Скорость от 200 Мбит/с — стримы и видео без буферизации\n'
            '🌐 Открывает доступ к любым заблокированным ресурсам\n'
            '☎️ Мессенджеры работают на максимум — без обрывов и задержек\n'
            '✅ Настройка за 2 минуты — справится любой\n'
            '🛡 Больше года стабильной работы, защита данных\n'
            '🎁 Бесплатный тест 5 дней + скидка 50%\n'
            '\n'
            f'👇 Попробуй бесплатно прямо сейчас\n'
            f'{_link("ПОПРОБОВАТЬ БЕСПЛАТНО")}'
        ),
    },
    {
        "name": "Интернет без границ",
        "desc": "Лайфхаки для свободного интернета без ограничений",
        "post": (
            f'🌍 {_link("DART VPN")} — весь интернет в твоём кармане\n'
            '\n'
            'Больше никаких «этот контент недоступен в вашем регионе». VPN работает через Telegram — просто и удобно.\n'
            '\n'
            'Что внутри:\n'
            '🌐 YouTube в 4K, Instagram, Netflix — всё без ограничений\n'
            '⚡️ 200+ Мбит/с — быстрее многих домашних провайдеров\n'
            '🧠 Автоматический выбор лучшего сервера\n'
            '☎️ Видеозвонки без лагов в любом мессенджере\n'
            '🛡 Год стабильной работы — никаких сбоев\n'
            '✅ Всё управление через бота в Telegram\n'
            '🎁 5 дней бесплатного доступа + 50% на месяц\n'
            '\n'
            f'👇 Открой свободный интернет\n'
            f'{_link("ОТКРЫТЬ ДОСТУП")}'
        ),
    },
    {
        "name": "Цифровая свобода",
        "desc": "Безопасность, приватность и свободный доступ к сети",
        "post": (
            f'🛡 {_link("DART VPN")} — твоя защита в цифровом мире\n'
            '\n'
            'Надёжный VPN-сервис, который работает как Telegram-бот. Подключение за секунды, без лишних программ.\n'
            '\n'
            'Главные фишки:\n'
            '🛡 Защита данных на уровне — больше года без утечек\n'
            '🌐 Полный доступ ко всему контенту без блокировок\n'
            '⚡️ Скорость до 200 Мбит/с — стримы, игры, загрузки\n'
            '🧠 Технология Smart Connect для стабильного соединения\n'
            '☎️ Звонки и видео без задержек — WhatsApp, Telegram, Discord\n'
            '✅ Ничего не нужно скачивать — всё в Telegram\n'
            '🎁 Пробный период 5 дней совершенно бесплатно\n'
            '\n'
            f'👇 Защити свой интернет уже сейчас\n'
            f'{_link("ЗАЩИТИТЬ")}'
        ),
    },
    {
        "name": "Онлайн лайфхаки",
        "desc": "Полезные сервисы и инструменты для повседневной жизни",
        "post": (
            f'✨ {_link("DART VPN")} — интернет без ограничений, прямо в Telegram\n'
            '\n'
            'Зачем платить за дорогие приложения, если VPN может быть простым и удобным? Всё через бота.\n'
            '\n'
            'Что получаешь:\n'
            '⚡️ Скорость от 200 Мбит/с — никаких тормозов\n'
            '🌐 Любые сайты и сервисы — YouTube, Instagram, Netflix\n'
            '🧠 Умное подключение — лучший сервер автоматически\n'
            '☎️ Стабильная связь во всех мессенджерах\n'
            '🛡 Безопасно и надёжно — без привязки банковской карты\n'
            '✅ Подключение за минуту — никаких инструкций\n'
            '🎁 5 дней бесплатно + бонус 50% скидка\n'
            '\n'
            f'👇 Попробуй — это бесплатно\n'
            f'{_link("ПОПРОБОВАТЬ")}'
        ),
    },
    {
        "name": "Нейро советы",
        "desc": "AI, технологии и полезные digital-инструменты",
        "post": (
            f'🔥 {_link("DART VPN")} — быстро, надёжно, без подписок\n'
            '\n'
            'ꗃ Современный VPN в формате Telegram-бота. Подключился — и забыл про блокировки навсегда.\n'
            '\n'
            'Возможности:\n'
            '🌐 Разблокировка всех сайтов — от YouTube до Netflix\n'
            '⚡️ 200 Мбит/с — смотри видео в 4K без задержек\n'
            '☎️ Видеозвонки без обрывов в Telegram, WhatsApp, Discord\n'
            '🧠 Smart Connect подбирает оптимальный сервер\n'
            '✅ Не нужно устанавливать приложения\n'
            '🛡 Проверенный сервис — стабильная работа больше года\n'
            '🎁 Бесплатный тест на 5 дней + скидка новым пользователям\n'
            '\n'
            f'👇 Подключайся бесплатно\n'
            f'{_link("ПОДКЛЮЧИТЬСЯ БЕСПЛАТНО")}'
        ),
    },
    {
        "name": "Полезные находки",
        "desc": "Делюсь лучшими сервисами и инструментами",
        "post": (
            f'📱 {_link("DART VPN")} — VPN, который просто работает\n'
            '\n'
            'Никаких сложных настроек. Открыл бота в Telegram — подключился — пользуешься. Всё.\n'
            '\n'
            'За что ценят DartVPN:\n'
            '✅ Простота — всё через Telegram-бота\n'
            '⚡️ Скорость от 200 Мбит/с — стримы и 4K без буфера\n'
            '🌐 Доступ к любым заблокированным ресурсам\n'
            '🧠 Автоматический выбор быстрого сервера\n'
            '☎️ Мессенджеры и звонки на максимальной скорости\n'
            '🛡 Без привязки карты, данные под защитой\n'
            '🎁 5 дней бесплатно + 50% скидка на первый месяц\n'
            '\n'
            f'👇 Начни пользоваться сейчас\n'
            f'{_link("НАЧАТЬ БЕСПЛАТНО")}'
        ),
    },
    {
        "name": "IT для всех",
        "desc": "Технологии простым языком — обзоры и рекомендации",
        "post": (
            f'⚡ {_link("DART VPN")} — доступ ко всему за пару кликов\n'
            '\n'
            'VPN нового формата: работает прямо в Telegram как бот. Не нужно ничего устанавливать.\n'
            '\n'
            'Почему стоит попробовать:\n'
            '🌐 Все сайты доступны — YouTube, Instagram, TikTok, Netflix\n'
            '⚡️ 200+ Мбит/с — как будто блокировок нет\n'
            '🧠 Smart Connect — система сама выберет лучший сервер\n'
            '☎️ Звонки и видео работают стабильно\n'
            '✅ Настройка за 2 минуты через бота\n'
            '🛡 Больше года работы, проверено тысячами пользователей\n'
            '🎁 Пробный доступ 5 дней бесплатно\n'
            '\n'
            f'👇 Получи бесплатный доступ\n'
            f'{_link("ПОЛУЧИТЬ ДОСТУП")}'
        ),
    },
]

STYLES_FOR_CHANNELS = ["expert", "casual", "business", "student", "tech",
                       "lifestyle", "blogger", "minimalist", "geek", "friendly"]


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
        if not self._ai_client:
            return self._get_fallback()

        prompt = CHANNEL_NAME_PROMPT.format(style=style)

        try:
            response = await asyncio.to_thread(
                self._ai_client.models.generate_content,
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.95,
                    max_output_tokens=300,
                ),
            )

            if not response or not response.text:
                return self._get_fallback()

            return self._parse_response(response.text)

        except Exception as exc:
            log.warning(f"Ошибка AI генерации канала: {exc}")
            return self._get_fallback()

    def _parse_response(self, text: str) -> dict:
        """Парсинг ответа AI (только name + desc, пост берём из фоллбэков)."""
        result = {"name": "", "desc": ""}

        for line in text.strip().split("\n"):
            line = line.strip()
            upper = line.upper()
            if upper.startswith("CHANNEL_NAME:"):
                result["name"] = line.split(":", 1)[1].strip()
            elif upper.startswith("CHANNEL_DESC:"):
                result["desc"] = line.split(":", 1)[1].strip()

        if not result["name"]:
            return self._get_fallback()

        if len(result["desc"]) > 255:
            result["desc"] = result["desc"][:252] + "..."

        # Пост всегда берём из фоллбэков (там правильные скрытые ссылки)
        fb = random.choice(FALLBACK_CHANNELS)
        result["post"] = fb["post"]
        return result

    @staticmethod
    def _get_fallback() -> dict:
        """Случайный фоллбэк-контент."""
        fb = random.choice(FALLBACK_CHANNELS)
        return {
            "name": fb["name"],
            "desc": fb["desc"],
            "post": fb["post"],
        }

    def _get_indexed_fallback(self, index: int) -> dict:
        """Фоллбэк по индексу (для уникальности — каждому аккаунту свой)."""
        fb = FALLBACK_CHANNELS[index % len(FALLBACK_CHANNELS)]
        return {
            "name": fb["name"],
            "desc": fb["desc"],
            "post": fb["post"],
        }

    def _get_avatar_path(self) -> Optional[Path]:
        """Получить путь к аватарке DartVPN."""
        path = BASE_DIR / settings.DARTVPN_AVATAR_PATH
        if path.exists():
            return path
        log.warning(f"Аватарка DartVPN не найдена: {path}")
        return None

    async def _set_channel_avatar(self, client: TelegramClient, entity) -> bool:
        """Установить аватарку канала из файла."""
        avatar_path = self._get_avatar_path()
        if not avatar_path:
            return False

        try:
            file = await client.upload_file(str(avatar_path))
            await client(EditPhotoRequest(
                channel=entity,
                photo=InputChatUploadedPhoto(file=file),
            ))
            log.info("Аватарка канала установлена")
            return True
        except Exception as exc:
            log.warning(f"Ошибка установки аватарки канала: {exc}")
            return False

    async def create_redirect_channel(
        self,
        phone: str,
        content: Optional[dict] = None,
        style: str = "casual",
        index: int = 0,
    ) -> dict:
        """
        Создать канал-переходник для одного аккаунта.

        1. Создаёт канал
        2. Ставит аватарку DartVPN
        3. Публикует HTML-пост со скрытыми ссылками
        4. Закрепляет пост
        5. Обновляет bio аккаунта (добавляет ссылку на канал)
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

        # Генерация контента — уникальный для каждого аккаунта
        if not content:
            content = self._get_indexed_fallback(index)

        channel_title = content["name"]
        channel_desc = content["desc"]
        post_text = content["post"]

        try:
            # ─── Шаг 1: Создаём канал ───
            log.info(f"{phone}: создаю канал-переходник «{channel_title}»")

            result = await client(CreateChannelRequest(
                title=channel_title,
                about=channel_desc[:255],
                broadcast=True,
            ))

            channel = result.chats[0]
            channel_id = channel.id

            log.info(f"{phone}: канал создан (id={channel_id})")
            await asyncio.sleep(random.uniform(1.0, 3.0))

            entity = await client.get_entity(channel_id)

            # ─── Шаг 2: Ставим аватарку DartVPN ───
            await self._set_channel_avatar(client, entity)
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # ─── Шаг 3: Публикуем пост со скрытыми ссылками (HTML) ───
            sent_message = await client.send_message(
                entity,
                post_text,
                parse_mode='html',
            )

            log.info(f"{phone}: пост опубликован (msg_id={sent_message.id})")
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # ─── Шаг 4: Закрепляем пост ───
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

            # ─── Шаг 5: Получаем ссылку на канал ───
            channel_link = await self._get_channel_link(client, entity, channel)

            # ─── Шаг 6: Обновляем bio аккаунта ───
            await self._update_bio_with_channel(client, phone, channel_link)

            if channel_link:
                # Сохранить ссылку в БД (per-account, а не global)
                await self._save_channel_link(phone, channel_link)

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
        if hasattr(channel, "username") and channel.username:
            return f"https://t.me/{channel.username}"

        try:
            invite = await client(ExportChatInviteRequest(
                peer=entity,
                legacy_revoke_permanent=True,
            ))
            return invite.link
        except Exception as exc:
            log.debug(f"Не удалось создать invite-link: {exc}")
            return ""

    async def _save_channel_link(self, phone: str, channel_link: str):
        """Сохранить ссылку на канал-переходник в БД (per-account)."""
        try:
            from sqlalchemy import update
            from storage.sqlite_db import async_session
            from storage.models import Account
            async with async_session() as session:
                await session.execute(
                    update(Account).where(Account.phone == phone).values(channel_link=channel_link)
                )
                await session.commit()
            log.info(f"{phone}: channel_link сохранён в БД: {channel_link}")
        except Exception as exc:
            log.warning(f"{phone}: не удалось сохранить channel_link: {exc}")

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
            me = await client.get_me()
            current_bio = me.about or ""

            if not current_bio:
                new_bio = f"👇 Подробнее в канале\n{channel_link}"
            elif channel_link in current_bio:
                return
            else:
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
        Каждый аккаунт получает уникальный шаблон (по индексу).
        """
        connected = self.session_mgr.get_connected_phones()
        if not connected:
            return {"total": 0, "success": 0, "failed": 0, "results": []}

        results = []
        success_count = 0

        for i, phone in enumerate(connected):
            style = STYLES_FOR_CHANNELS[i % len(STYLES_FOR_CHANNELS)]

            if progress_callback:
                await progress_callback(
                    phone,
                    f"⏳ [{i + 1}/{len(connected)}] Создаю канал для {phone}...",
                )

            result = await self.create_redirect_channel(phone, style=style, index=i)
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
        """Удалить канал-переходник."""
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
