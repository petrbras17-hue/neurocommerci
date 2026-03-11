"""
Создание каналов-переходников для аккаунтов.

Для каждого аккаунта:
1. Создаёт приватный Telegram-канал
2. Ставит аватарку продукта
3. Публикует пост со скрытыми ссылками (parse_mode='html')
4. Закрепляет пост в канале
5. Обновляет bio аккаунта — добавляет ссылку на канал-переходник

Это ключевой элемент Сценария A: аватарка → профиль → канал → пост → продукт.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
from pathlib import Path
from typing import Callable, Optional, TypedDict

from PIL import Image

from google import genai
from google.genai import types
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import CreateChannelRequest, EditPhotoRequest, UpdateUsernameRequest as ChannelUpdateUsernameRequest
from telethon.tl.functions.messages import (
    ExportChatInviteRequest,
    UpdatePinnedMessageRequest,
)
from telethon.tl.functions.account import UpdateProfileRequest, UpdatePersonalChannelRequest
from telethon.tl.types import InputChatUploadedPhoto
from sqlalchemy import select, update

from config import settings, BASE_DIR
from core.gemini_models import get_text_model_candidates
from core.session_manager import SessionManager
from core.account_manager import AccountManager
from storage.sqlite_db import async_session
from storage.models import Account
from utils.cache import SettingsCache
from utils.logger import log


# ────────────────────────────────────────────────────────────
# TypedDict — типизированные словари для результатов операций
# ────────────────────────────────────────────────────────────

class ChannelResult(TypedDict):
    """Результат создания канала-переходника (create_redirect_channel)."""
    phone: str
    success: bool
    channel_title: str
    channel_link: str
    personal_channel_set: bool
    bio_fallback_used: bool
    error: Optional[str]


class PersonalChannelResult(TypedDict):
    """Результат установки персонального канала для одного аккаунта."""
    phone: str
    success: bool


class BatchResult(TypedDict):
    """Общий результат пакетной операции (setup_all_accounts, set_personal_channel_all)."""
    total: int
    success: int
    failed: int
    results: list


# ────────────────────────────────────────────────────────────
# Ссылка на продукт (единая для всех постов и комментариев)
# ────────────────────────────────────────────────────────────
def _link(text: str) -> str:
    """Обернуть текст в скрытую HTML-ссылку на продукт."""
    return f'<a href="{settings.PRODUCT_BOT_LINK}">{text}</a>'


# ────────────────────────────────────────────────────────────
# AI-промпт для генерации контента
# ────────────────────────────────────────────────────────────

_CHANNEL_THEMES: dict[str, list[str]] = {
    "VPN": [
        "Цифровая безопасность и VPN",
        "Свободный интернет",
        "Обход блокировок и интернет-свобода",
    ],
    "AI": [
        "Нейросети и AI-инструменты",
        "Искусственный интеллект для всех",
        "Технологии будущего",
    ],
    "Bot": [
        "Полезные Telegram-боты",
        "Автоматизация и инструменты",
        "Лайфхаки и технологии",
    ],
    "Service": [
        "Полезные сервисы и инструменты",
        "Технологии и лайфхаки",
        "Цифровые инструменты",
    ],
}

_COMMON_THEMES = [
    "Полезные сервисы и приложения",
    "IT-лайфхаки и технологии",
]
_COMMON_THEMES_BLOCK = "\n".join(f"- {t}" for t in _COMMON_THEMES)


def get_channel_name_prompt(style: str) -> str:
    """Промпт для генерации названия канала с актуальными settings."""
    themes = _CHANNEL_THEMES.get(settings.PRODUCT_CATEGORY, _CHANNEL_THEMES["Service"])
    themes_block = "\n".join(f"- {t}" for t in themes)
    return f"""Сгенерируй название и описание для Telegram-канала.
Канал должен выглядеть как личный канал обычного пользователя, который делится полезной информацией.

Стиль: {style}

Тематики на выбор (выбери одну):
{themes_block}
{_COMMON_THEMES_BLOCK}

Требования:
1. Название — 2-4 слова, привлекательное, НЕ содержит "{settings.PRODUCT_NAME}"
2. Описание — 1-2 предложения, до 100 символов, интригующее

Ответь СТРОГО в формате:
CHANNEL_NAME: ...
CHANNEL_DESC: ...
"""


# ────────────────────────────────────────────────────────────
# Посты для каналов-переходников
# Загружаются из data/product_posts.json (кастомные)
# или генерируются из настроек продукта (generic)
# ────────────────────────────────────────────────────────────

def _build_fallback_channels() -> list[dict]:
    """Построить список постов для каналов-переходников."""

    # 1. Кастомные посты из JSON (приоритет)
    custom_path = BASE_DIR / "data" / "product_posts.json"
    if custom_path.exists():
        try:
            data = json.loads(custom_path.read_text(encoding="utf-8"))
            link = settings.PRODUCT_BOT_LINK

            # Формат 1: {"post_template": "...", "channels": [...]}
            if isinstance(data, dict) and "post_template" in data and "channels" in data:
                post = data["post_template"].replace("{LINK}", link)
                channels = [
                    {"name": ch["name"], "desc": ch["desc"], "post": post}
                    for ch in data["channels"]
                ]
                log.info(f"Загружено {len(channels)} кастомных постов из product_posts.json")
                return channels

            # Формат 2 (legacy): [{name, desc, post}, ...]
            if isinstance(data, list) and data:
                for item in data:
                    if "post" in item:
                        item["post"] = item["post"].replace("{LINK}", link)
                log.info(f"Загружено {len(data)} кастомных постов из product_posts.json")
                return data
        except Exception as exc:
            log.warning(f"Ошибка чтения product_posts.json: {exc}")

    # 2. Генерация из настроек продукта (blockquote + emoji-per-line формат)
    name = settings.PRODUCT_NAME
    features = settings.PRODUCT_FEATURES
    desc = settings.PRODUCT_SHORT_DESC

    # Разбить фичи в список буллетов с эмодзи
    feature_emojis = ["🌐", "☎️", "⚡️", "🧠", "🌍", "✅", "🛡", "📱", "🔐", "💡"]
    feature_list = [f.strip() for f in features.split(",") if f.strip()]
    feature_bullets = "\n".join(
        f"{feature_emojis[i % len(feature_emojis)]} {f}" for i, f in enumerate(feature_list)
    )

    channel_names = [
        ("Полезности | Интернет", "Советы, лайфхаки и полезные сервисы"),
        ("Digital лайфхаки", "Технологии и сервисы"),
        ("Свободный интернет", "Лайфхаки и инструменты"),
        ("Tech советы", "Лучшие digital-решения"),
        ("Интернет без границ", "Полезные инструменты"),
        ("Цифровая свобода", "Безопасность и приватность"),
        ("Онлайн лайфхаки", "Полезные сервисы"),
        ("Нейро советы", "AI и digital-инструменты"),
        ("Полезные находки", "Лучшие сервисы"),
        ("IT для всех", "Обзоры и рекомендации"),
    ]

    channels = []
    for ch_name, ch_desc in channel_names:
        post = (
            f'🎯 {_link(name.upper())} — {desc}\n'
            f'\n'
            f'<blockquote>Удобный сервис прямо в Telegram.\n'
            f'\n'
            f'{feature_bullets}\n'
            f'\n'
            f'🎁 Попробуйте бесплатно</blockquote>\n'
            f'\n'
            f'👇 Попробуйте бесплатно прямо сейчас\n'
            f'\n'
            f'{_link("ПОПРОБОВАТЬ")}\n'
            f'{_link("ПОПРОБОВАТЬ")}\n'
            f'{_link("ПОПРОБОВАТЬ")}'
        )
        channels.append({"name": ch_name, "desc": ch_desc, "post": post})

    return channels


_fallback_channels_settings_cache = SettingsCache(
    key_fn=lambda: (
        f"{settings.PRODUCT_NAME}|{settings.PRODUCT_FEATURES}"
        f"|{settings.PRODUCT_BOT_LINK}|{settings.PRODUCT_SHORT_DESC}"
    ),
    build_fn=_build_fallback_channels,
)


def get_fallback_channels() -> list[dict]:
    """
    Посты для каналов-переходников с актуальными настройками продукта.
    Кэширует результат; сбрасывает кэш при изменении product settings.
    """
    return _fallback_channels_settings_cache.get()


STYLES_FOR_CHANNELS = ["expert", "casual", "business", "student", "tech",
                       "lifestyle", "blogger", "minimalist", "geek", "friendly"]


# ────────────────────────────────────────────────────────────
# Подготовка квадратной аватарки для канала
# ────────────────────────────────────────────────────────────
# Telegram отображает аватарки каналов как круги.
# Баннер просто растягивается до квадрата size×size.
# Небольшое вертикальное растяжение незаметно в круговой аватарке,
# зато нет ни паддингов, ни обрезки текста.

def prepare_square_avatar(banner_path: Path, size: int = 800) -> Path:
    """
    Конвертировать баннер в квадратную аватарку для Telegram-канала.

    Растягивает баннер до size×size. Небольшое вертикальное растяжение
    (для 16:9 → 1:1) незаметно в круговой аватарке Telegram.
    """
    square_path = banner_path.parent / f"{banner_path.stem}_square.png"

    # Кэш: не пересоздаём если уже есть и свежее оригинала
    if square_path.exists() and square_path.stat().st_mtime >= banner_path.stat().st_mtime:
        return square_path

    with Image.open(banner_path) as img:
        rgb_img = img.convert("RGB")
        resized = rgb_img.resize((size, size), Image.LANCZOS)
        resized.save(str(square_path), quality=95)
        log.info(f"Квадратная аватарка создана: {square_path.name} ({size}x{size})")
        return square_path


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

    @staticmethod
    def _model_candidates() -> list[str]:
        return get_text_model_candidates(
            settings.GEMINI_MODEL,
            settings.GEMINI_FLASH_MODEL,
        )

    async def generate_channel_content(self, style: str = "casual") -> dict:
        """
        Сгенерировать контент для канала-переходника через AI.
        Возвращает {"name": str, "desc": str, "post": str}.
        """
        if not self._ai_client:
            return self._get_fallback()

        prompt = get_channel_name_prompt(style)

        for model_name in self._model_candidates():
            try:
                response = await asyncio.to_thread(
                    self._ai_client.models.generate_content,
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.95,
                        max_output_tokens=300,
                    ),
                )
                if response and response.text:
                    return self._parse_response(response.text)
            except Exception as exc:
                log.warning(f"Ошибка AI генерации канала (model={model_name}): {exc}")

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
        channels = get_fallback_channels()
        fb = random.choice(channels)
        result["post"] = fb["post"]
        return result

    @staticmethod
    def _get_fallback() -> dict:
        """Случайный фоллбэк-контент."""
        channels = get_fallback_channels()
        fb = random.choice(channels)
        return {
            "name": fb["name"],
            "desc": fb["desc"],
            "post": fb["post"],
        }

    def _get_indexed_fallback(self, index: int) -> dict:
        """Фоллбэк по индексу (для уникальности — каждому аккаунту свой)."""
        channels = get_fallback_channels()
        fb = channels[index % len(channels)]
        return {
            "name": fb["name"],
            "desc": fb["desc"],
            "post": fb["post"],
        }

    def _get_avatar_path(self) -> Optional[Path]:
        """Получить путь к аватарке продукта."""
        path = BASE_DIR / settings.PRODUCT_AVATAR_PATH
        if path.exists():
            return path
        log.warning(f"Аватарка продукта не найдена: {path}")
        return None

    async def _set_channel_avatar(
        self,
        client: TelegramClient,
        entity,
        square_avatar: Optional[Path] = None,
    ) -> bool:
        """Установить аватарку канала (квадратную версию баннера).

        Args:
            square_avatar: предварительно подготовленная квадратная аватарка.
                           Если None — создаётся из PRODUCT_AVATAR_PATH.
        """
        if square_avatar is None:
            avatar_path = self._get_avatar_path()
            if not avatar_path:
                return False
            square_avatar = prepare_square_avatar(avatar_path)

        try:
            file = await client.upload_file(str(square_avatar))
            await client(EditPhotoRequest(
                channel=entity,
                photo=InputChatUploadedPhoto(file=file),
            ))
            log.info("Аватарка канала установлена (квадратная)")
            return True
        except Exception as exc:
            log.warning(f"Ошибка установки аватарки канала: {exc}")
            return False

    async def _assign_channel_username(
        self,
        client: TelegramClient,
        entity,
        phone: str,
    ) -> bool:
        """
        Назначить публичный username каналу.
        Нужен для UpdatePersonalChannelRequest (требует PUBLIC_BROADCAST).
        """
        prefix = settings.PRODUCT_CHANNEL_PREFIX
        base_names = [
            f"{prefix}_{phone[-4:]}",
            f"{prefix}_ch_{phone[-4:]}",
            f"{prefix}_{phone[-6:]}",
            f"{prefix}_go_{phone[-4:]}",
        ]
        suffixes = ["", "_01", "_02", "_rf", "_go", "_24", "_25", "_26"]

        for base in base_names:
            for suffix in suffixes:
                candidate = base + suffix
                if len(candidate) < 5 or len(candidate) > 32:
                    continue
                try:
                    await client(ChannelUpdateUsernameRequest(
                        channel=entity,
                        username=candidate,
                    ))
                    log.info(f"{phone}: канал получил username @{candidate}")
                    return True
                except Exception as exc:
                    err = str(exc).upper()
                    if "USERNAME_OCCUPIED" in err or "USERNAME_NOT_MODIFIED" in err:
                        continue
                    if "FLOOD" in err:
                        log.warning(f"{phone}: FloodWait при назначении username канала")
                        await asyncio.sleep(10)
                        continue
                    log.debug(f"{phone}: username @{candidate} не подошёл: {exc}")
                    continue

        log.warning(f"{phone}: не удалось назначить username каналу")
        return False

    async def create_channel_shell(
        self,
        phone: str,
        *,
        content: Optional[dict] = None,
        style: str = "casual",
        index: int = 0,
    ) -> dict:
        """Создать только канал и его базовое оформление без публикации поста."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return {"ok": False, "error": "client_not_connected", "phone": phone}

        if not content:
            content = await self.generate_channel_content(style=style)
        channel_title = str(content.get("name") or "").strip() or self._get_indexed_fallback(index)["name"]
        channel_desc = str(content.get("desc") or "").strip()[:255]

        try:
            result = await client(CreateChannelRequest(
                title=channel_title,
                about=channel_desc,
                broadcast=True,
            ))
            channel = result.chats[0]
            entity = await client.get_entity(channel.id)
            await asyncio.sleep(random.uniform(0.5, 1.5))
            await self._assign_channel_username(client, entity, phone)
            await asyncio.sleep(random.uniform(0.5, 1.0))
            await self._set_channel_avatar(client, entity)
            channel_link = await self._get_channel_link(client, entity, channel)
            if channel_link:
                await self._save_channel_link(phone, channel_link)
            return {
                "ok": True,
                "phone": phone,
                "channel_id": int(channel.id),
                "channel_title": channel_title,
                "channel_desc": channel_desc,
                "channel_link": channel_link,
            }
        except FloodWaitError as exc:
            return {
                "ok": False,
                "phone": phone,
                "error": f"flood_wait_{exc.seconds}s",
                "channel_title": channel_title,
            }
        except Exception as exc:
            log.error(f"{phone}: ошибка create_channel_shell: {exc}")
            return {
                "ok": False,
                "phone": phone,
                "error": str(exc),
                "channel_title": channel_title,
            }

    async def publish_content_to_channel(
        self,
        phone: str,
        *,
        channel_id: int,
        post_text: str,
        pin_post: bool = True,
        attach_personal_channel: bool = True,
        allow_bio_fallback: bool | None = None,
    ) -> dict:
        """Опубликовать и при необходимости закрепить пост в уже созданном канале."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return {"ok": False, "error": "client_not_connected", "phone": phone}

        try:
            entity = await client.get_entity(int(channel_id))
            sent_message = await client.send_message(entity, post_text, parse_mode="html")
            pinned = False
            if pin_post:
                try:
                    await client(UpdatePinnedMessageRequest(
                        peer=entity,
                        id=sent_message.id,
                        silent=True,
                    ))
                    pinned = True
                except Exception as exc:
                    log.debug(f"{phone}: не удалось закрепить пост: {exc}")

            personal_set = False
            if attach_personal_channel:
                personal_set = await self.set_personal_channel(phone, entity)
                if not personal_set and (allow_bio_fallback if allow_bio_fallback is not None else settings.PACKAGING_ALLOW_BIO_FALLBACK):
                    channel_link = await self._get_channel_link(client, entity, entity)
                    if channel_link:
                        await self._update_bio_with_channel(client, phone, channel_link)

            return {
                "ok": True,
                "phone": phone,
                "channel_id": int(channel_id),
                "message_id": int(sent_message.id),
                "pinned": bool(pinned),
                "personal_channel_set": bool(personal_set),
            }
        except FloodWaitError as exc:
            return {
                "ok": False,
                "phone": phone,
                "channel_id": int(channel_id),
                "error": f"flood_wait_{exc.seconds}s",
            }
        except Exception as exc:
            log.error(f"{phone}: ошибка publish_content_to_channel: {exc}")
            return {
                "ok": False,
                "phone": phone,
                "channel_id": int(channel_id),
                "error": str(exc),
            }

    async def create_redirect_channel(
        self,
        phone: str,
        content: Optional[dict] = None,
        style: str = "casual",
        index: int = 0,
    ) -> ChannelResult:
        """
        Создать канал-переходник для одного аккаунта.

        1. Создаёт канал
        2. Ставит аватарку продукта
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
                "personal_channel_set": False,
                "bio_fallback_used": False,
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

            # ─── Шаг 1.5: Назначаем публичный username (нужен для персонального канала) ───
            await self._assign_channel_username(client, entity, phone)
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # ─── Шаг 2: Ставим аватарку продукта ───
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

            # ─── Шаг 6: Закрепляем канал в профиле как виджет ───
            personal_set = await self.set_personal_channel(phone, entity)

            # ─── Шаг 7: Обновляем bio (без ссылки на канал, если виджет установлен) ───
            bio_fallback_used = False
            if not personal_set:
                if settings.PACKAGING_ALLOW_BIO_FALLBACK:
                    # Фоллбэк: если персональный канал не удалось — ставим ссылку в bio
                    await self._update_bio_with_channel(client, phone, channel_link)
                    bio_fallback_used = True
                else:
                    log.warning(
                        f"{phone}: personal channel не установлен, "
                        "bio fallback отключён (PACKAGING_ALLOW_BIO_FALLBACK=false)"
                    )

            if channel_link:
                # Сохранить ссылку в БД (per-account, а не global)
                await self._save_channel_link(phone, channel_link)

            return {
                "phone": phone,
                "success": True,
                "channel_title": channel_title,
                "channel_link": channel_link,
                "personal_channel_set": bool(personal_set),
                "bio_fallback_used": bio_fallback_used,
                "error": None,
            }

        except FloodWaitError as e:
            log.warning(f"{phone}: FloodWait {e.seconds}с при создании канала")
            return {
                "phone": phone,
                "success": False,
                "channel_title": channel_title,
                "channel_link": "",
                "personal_channel_set": False,
                "bio_fallback_used": False,
                "error": f"FloodWait {e.seconds}с",
            }

        except Exception as exc:
            log.error(f"{phone}: ошибка создания канала: {exc}")
            return {
                "phone": phone,
                "success": False,
                "channel_title": channel_title,
                "channel_link": "",
                "personal_channel_set": False,
                "bio_fallback_used": False,
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
                # Если ссылка + текст не влезают — ставим только ссылку
                fallback_text = f"👇 Подробнее в канале\n{channel_link}"
                if len(fallback_text) <= 70:
                    new_bio = fallback_text
                else:
                    new_bio = channel_link[:70]
            elif channel_link in current_bio:
                return
            else:
                max_bio_for_link = 70 - len(channel_link) - 1  # \n = 1 символ
                if max_bio_for_link > 10:
                    trimmed_bio = current_bio[:max_bio_for_link].rstrip()
                    new_bio = f"{trimmed_bio}\n{channel_link}"
                else:
                    new_bio = channel_link[:70]

            await client(UpdateProfileRequest(about=new_bio[:70]))
            log.info(f"{phone}: bio обновлён с каналом-переходником")

        except Exception as exc:
            log.warning(f"{phone}: ошибка обновления bio: {exc}")

    async def _run_for_all_connected(
        self,
        per_account_fn: Callable,
        delay_range: tuple[float, float],
        progress_callback: Optional[Callable] = None,
        user_id: int = None,
    ) -> BatchResult:
        """
        Общий хелпер: выполнить async-функцию для каждого подключённого аккаунта.

        Args:
            per_account_fn: async (phone, index) -> dict с ключом "success".
                            Возвращённый dict пробрасывается в results as-is.
            delay_range: (min, max) антибан-задержка между аккаунтами (секунды).
            progress_callback: optional async (phone, index, total) -> None.
            user_id: фильтр по пользователю.

        Returns:
            BatchResult: {"total", "success", "failed", "results"}.
        """
        connected = self.session_mgr.get_connected_phones(user_id=user_id)
        if not connected:
            return {"total": 0, "success": 0, "failed": 0, "results": []}

        results: list[dict] = []
        success_count = 0

        for i, phone in enumerate(connected):
            if progress_callback:
                await progress_callback(phone, i, len(connected))

            result = await per_account_fn(phone, i)
            results.append(result)

            if result.get("success"):
                success_count += 1

            # Антибан пауза между аккаунтами
            if i < len(connected) - 1:
                delay = random.uniform(*delay_range)
                log.debug(f"Пауза {delay:.0f}с перед следующим аккаунтом")
                await asyncio.sleep(delay)

        return {
            "total": len(connected),
            "success": success_count,
            "failed": len(connected) - success_count,
            "results": results,
        }

    async def setup_all_accounts(
        self,
        progress_callback=None,
        user_id: int = None,
    ) -> BatchResult:
        """
        Создать канал-переходник для каждого подключённого аккаунта.
        Каждый аккаунт получает уникальный шаблон (по индексу).
        """

        async def _per_account(phone: str, index: int) -> ChannelResult:
            style = STYLES_FOR_CHANNELS[index % len(STYLES_FOR_CHANNELS)]
            return await self.create_redirect_channel(phone, style=style, index=index)

        async def _progress(phone: str, i: int, total: int) -> None:
            await progress_callback(
                phone,
                f"⏳ [{i + 1}/{total}] Создаю канал для {phone}...",
            )

        result = await self._run_for_all_connected(
            per_account_fn=_per_account,
            delay_range=(15.0, 30.0),
            progress_callback=_progress if progress_callback else None,
            user_id=user_id,
        )

        log.info(
            f"Каналы-переходники: {result['success']}/{result['total']} создано успешно"
        )

        return result

    async def set_personal_channel(self, phone: str, channel_entity=None) -> bool:
        """
        Установить канал-переходник как персональный канал в профиле.
        Канал отображается как виджет/карточка в профиле (не текстовая ссылка в bio).
        """
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            log.warning(f"{phone}: не подключён для установки персонального канала")
            return False

        try:
            # Если entity не передан — ищем канал из БД
            if channel_entity is None:
                channel_entity = await self._find_account_channel(client, phone)
                if channel_entity is None:
                    log.warning(f"{phone}: канал-переходник не найден")
                    return False

            # Получить InputChannel
            input_channel = await client.get_input_entity(channel_entity)

            try:
                await client(UpdatePersonalChannelRequest(channel=input_channel))
            except Exception as e:
                if "PUBLIC_BROADCAST_EXPECTED" in str(e):
                    # Канал приватный — нужно назначить username
                    log.info(f"{phone}: канал приватный, назначаю username...")
                    if await self._assign_channel_username(client, channel_entity, phone):
                        # Перечитать entity с обновлённым username
                        input_channel = await client.get_input_entity(channel_entity)
                        await client(UpdatePersonalChannelRequest(channel=input_channel))
                    else:
                        raise
                else:
                    raise

            log.info(f"{phone}: персональный канал установлен в профиле")
            return True

        except Exception as exc:
            log.error(f"{phone}: ошибка установки персонального канала: {exc}")
            return False

    async def _find_account_channel(self, client: TelegramClient, phone: str):
        """Найти канал-переходник аккаунта по channel_link из БД."""
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Account.channel_link).where(Account.phone == phone)
                )
                row = result.scalar_one_or_none()

            if not row:
                log.debug(f"{phone}: channel_link не найден в БД")
                return None

            entity = await client.get_entity(row)
            return entity

        except Exception as exc:
            log.warning(f"{phone}: ошибка поиска канала: {exc}")
            return None

    async def set_personal_channel_all(self, progress_callback=None, user_id: int = None) -> BatchResult:
        """Установить персональный канал для всех подключённых аккаунтов."""
        # Предзагрузка channel_links до цикла (1 запрос вместо N)
        connected = self.session_mgr.get_connected_phones(user_id=user_id)
        channel_links = await self._batch_load_channel_links(connected) if connected else {}

        async def _per_account(phone: str, _index: int) -> PersonalChannelResult:
            # Предрезолвим entity из предзагруженной ссылки
            channel_entity = None
            link = channel_links.get(phone)
            if link:
                client = self.session_mgr.get_client(phone)
                if client and client.is_connected():
                    try:
                        channel_entity = await client.get_entity(link)
                    except Exception as exc:
                        log.debug(f"{phone}: не удалось резолвить канал '{link}': {exc}")

            ok = await self.set_personal_channel(phone, channel_entity)
            return {"phone": phone, "success": ok}

        async def _progress(phone: str, i: int, total: int) -> None:
            await progress_callback(
                phone,
                f"⏳ [{i + 1}/{total}] Устанавливаю канал в профиль {phone}...",
            )

        return await self._run_for_all_connected(
            per_account_fn=_per_account,
            delay_range=(5.0, 15.0),
            progress_callback=_progress if progress_callback else None,
            user_id=user_id,
        )

    async def _batch_load_channel_links(self, phones: list[str]) -> dict[str, str]:
        """Загрузить channel_link для нескольких аккаунтов одним запросом."""
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Account.phone, Account.channel_link).where(
                        Account.phone.in_(phones)
                    )
                )
                return {row.phone: row.channel_link for row in result if row.channel_link}
        except Exception as exc:
            log.warning(f"Ошибка batch-загрузки channel_links: {exc}")
            return {}

    async def update_all_avatars(
        self,
        avatar_path: Path,
        progress_callback: Optional[Callable] = None,
        user_id: int = None,
    ) -> BatchResult:
        """
        Обновить аватарки на всех каналах-переходниках.

        Использует channel_link из БД (вместо GetDialogsRequest)
        и _set_channel_avatar() для установки.
        """
        square_path = prepare_square_avatar(avatar_path)
        connected = self.session_mgr.get_connected_phones(user_id=user_id)
        channel_links = await self._batch_load_channel_links(connected) if connected else {}

        async def _per_account(phone: str, _index: int) -> dict:
            client = self.session_mgr.get_client(phone)
            if not client or not client.is_connected():
                return {"phone": phone, "success": False}

            link = channel_links.get(phone)
            if not link:
                log.debug(f"{phone}: channel_link не найден, пропускаю")
                return {"phone": phone, "success": False}

            try:
                entity = await client.get_entity(link)
            except Exception as exc:
                log.warning(f"{phone}: не удалось найти канал '{link}': {exc}")
                return {"phone": phone, "success": False}

            ok = await self._set_channel_avatar(client, entity, square_avatar=square_path)
            if ok:
                log.info(f"{phone}: аватарка канала обновлена")
            return {"phone": phone, "success": ok}

        async def _progress(phone: str, i: int, total: int) -> None:
            await progress_callback(
                phone,
                f"⏳ [{i + 1}/{total}] Обновляю аватарку для {phone}...",
            )

        return await self._run_for_all_connected(
            per_account_fn=_per_account,
            delay_range=(2.0, 5.0),
            progress_callback=_progress if progress_callback else None,
            user_id=user_id,
        )

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
