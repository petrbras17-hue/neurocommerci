"""
AI-упаковка профилей аккаунтов через Gemini.
Генерация имён, описаний, выбор аватарок.
"""

from __future__ import annotations

import asyncio
import random
from typing import Optional

from google import genai
from google.genai import types
from telethon import TelegramClient
from telethon.tl.functions.account import UpdateProfileRequest

from config import settings
from core.session_manager import SessionManager
from utils.logger import log


PACKAGING_PROMPT = """Ты — маркетолог, специализирующийся на упаковке Telegram-аккаунтов для продвижения VPN-сервиса.

Сгенерируй данные для профиля аккаунта. Аккаунт должен выглядеть как реальный человек из России.

Стиль: {style}

Требования:
1. Имя (first_name) — реалистичное русское имя, 1-2 слова
2. Фамилия (last_name) — реалистичная русская фамилия или пусто
3. Описание (bio) — до 70 символов, должно содержать призыв к действию и намёк на VPN/технологии. НЕ добавляй ссылки — они будут добавлены отдельно.

Стили:
- "expert" — Техноблогер, эксперт по цифровой безопасности
- "casual" — Обычный пользователь, делится лайфхаками
- "business" — Предприниматель, оптимизатор процессов
- "student" — Студент/молодой специалист в IT

Ответь СТРОГО в формате (каждый на новой строке):
FIRST_NAME: ...
LAST_NAME: ...
BIO: ...
"""


class AccountPackager:
    """AI-упаковка профилей аккаунтов."""

    def __init__(self, session_manager: SessionManager):
        self.session_mgr = session_manager
        self._client: Optional[genai.Client] = None
        self._init_client()

    def _init_client(self):
        if settings.GEMINI_API_KEY:
            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)

    async def generate_profile(self, style: str = "casual") -> dict:
        """
        Сгенерировать данные профиля через AI.
        Возвращает {"first_name": str, "last_name": str, "bio": str}.
        """
        if not self._client:
            return self._get_fallback_profile(style)

        prompt = PACKAGING_PROMPT.format(style=style)

        try:
            response = await asyncio.to_thread(
                self._client.models.generate_content,
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.9,
                    max_output_tokens=200,
                ),
            )

            if not response or not response.text:
                return self._get_fallback_profile(style)

            return self._parse_profile(response.text, style)

        except Exception as exc:
            log.warning(f"Ошибка генерации профиля: {exc}")
            return self._get_fallback_profile(style)

    def _parse_profile(self, text: str, style: str) -> dict:
        """Парсинг ответа AI."""
        result = {"first_name": "", "last_name": "", "bio": ""}

        for line in text.strip().split("\n"):
            line = line.strip()
            if line.upper().startswith("FIRST_NAME:"):
                result["first_name"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("LAST_NAME:"):
                result["last_name"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("BIO:"):
                result["bio"] = line.split(":", 1)[1].strip()

        # Валидация
        if not result["first_name"]:
            fallback = self._get_fallback_profile(style)
            result["first_name"] = fallback["first_name"]

        # Обрезать bio до 70 символов
        if len(result["bio"]) > 70:
            result["bio"] = result["bio"][:67] + "..."

        return result

    @staticmethod
    def _get_fallback_profile(style: str) -> dict:
        """Фоллбэк-профили."""
        profiles = {
            "expert": [
                {"first_name": "Алексей", "last_name": "Медведев", "bio": "Цифровая безопасность | Обзоры VPN и приватность"},
                {"first_name": "Дмитрий", "last_name": "Волков", "bio": "IT-безопасность | Защита данных и свободный интернет"},
                {"first_name": "Максим", "last_name": "Козлов", "bio": "Техноблогер | Обход блокировок и цифровая свобода"},
            ],
            "casual": [
                {"first_name": "Андрей", "last_name": "", "bio": "Делюсь полезными лайфхаками для интернета"},
                {"first_name": "Михаил", "last_name": "К.", "bio": "Технологии, VPN, полезности"},
                {"first_name": "Сергей", "last_name": "", "bio": "Интернет без границ | Советы и лайфхаки"},
            ],
            "business": [
                {"first_name": "Артём", "last_name": "Соколов", "bio": "Предприниматель | Оптимизация бизнес-процессов"},
                {"first_name": "Никита", "last_name": "Павлов", "bio": "Digital-маркетинг | Инструменты для бизнеса"},
                {"first_name": "Роман", "last_name": "Новиков", "bio": "Бизнес и технологии | Цифровые решения"},
            ],
            "student": [
                {"first_name": "Кирилл", "last_name": "", "bio": "Студент IT | Нейросети и технологии"},
                {"first_name": "Даниил", "last_name": "М.", "bio": "Начинающий разработчик | AI и VPN"},
                {"first_name": "Илья", "last_name": "", "bio": "Учусь на программиста, люблю технологии"},
            ],
        }

        pool = profiles.get(style, profiles["casual"])
        return random.choice(pool)

    async def apply_profile(
        self,
        phone: str,
        profile: dict,
        channel_link: str = "",
    ) -> bool:
        """
        Применить профиль к аккаунту через Telethon.
        """
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            log.warning(f"{phone}: не подключён для упаковки")
            return False

        bio = profile.get("bio", "")
        # Добавить ссылку на канал в bio если есть
        if channel_link and len(bio) + len(channel_link) + 3 < 70:
            bio = f"{bio}\n{channel_link}"

        try:
            await client(UpdateProfileRequest(
                first_name=profile.get("first_name", ""),
                last_name=profile.get("last_name", ""),
                about=bio[:70],
            ))
            log.info(f"{phone}: профиль обновлён — {profile['first_name']} {profile.get('last_name', '')}")
            return True

        except Exception as exc:
            log.error(f"{phone}: ошибка обновления профиля: {exc}")
            return False

    async def package_account(
        self,
        phone: str,
        style: str = "casual",
    ) -> dict:
        """Полная упаковка аккаунта: генерация + применение."""
        profile = await self.generate_profile(style)
        channel_link = settings.DARTVPN_CHANNEL_LINK or ""
        success = await self.apply_profile(phone, profile, channel_link)

        return {
            "phone": phone,
            "profile": profile,
            "applied": success,
            "style": style,
        }

    async def package_all_accounts(self) -> list[dict]:
        """Упаковать все подключённые аккаунты."""
        connected = self.session_mgr.get_connected_phones()
        styles = ["casual", "expert", "business", "student"]
        results = []

        for i, phone in enumerate(connected):
            style = styles[i % len(styles)]
            result = await self.package_account(phone, style)
            results.append(result)
            await asyncio.sleep(random.uniform(3.0, 8.0))

        return results
