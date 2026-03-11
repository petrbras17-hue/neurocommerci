"""
AI-упаковка профилей аккаунтов через Gemini.
Женские профили: имена, username, аватарки (Gemini Imagen).
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types
from telethon import TelegramClient
from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest
from telethon.tl.functions.account import CheckUsernameRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest

from config import settings
from core.gemini_models import get_text_model_candidates
from core.session_manager import SessionManager
from utils.logger import log


# ─── Промпт для генерации женского профиля ──────────────────────────

PACKAGING_PROMPT = """Ты — маркетолог, специализирующийся на упаковке Telegram-аккаунтов для продвижения VPN-сервиса.

Сгенерируй данные для профиля ДЕВУШКИ из России.

Стиль: {style}

Требования:
1. Имя (first_name) — реалистичное русское ЖЕНСКОЕ имя, 1 слово
2. Фамилия (last_name) — реалистичная русская ЖЕНСКАЯ фамилия или пусто
3. Username — ЛАТИНИЦЕЙ, женское (напр. marina_tech, ksenya.life, alina_rf)
4. Описание (bio) — до 70 символов, намёк на VPN/технологии/интернет. НЕ добавляй ссылки.

Стили:
- "beauty" — Бьюти-блогер, следит за трендами
- "casual" — Обычная девушка, делится лайфхаками
- "student" — Студентка/молодой специалист в IT
- "tech" — Тех-блогер, разбирается в технологиях
- "lifestyle" — Лайфстайл, путешествия, жизнь
- "blogger" — Блогер, контент-мейкер
- "fitness" — ЗОЖ, спорт, энергия
- "business" — Предпринимательница, бизнес
- "creative" — Дизайнер/художник/креатив
- "friendly" — Открытая, общительная, позитивная

Ответь СТРОГО в формате (каждый на новой строке):
FIRST_NAME: ...
LAST_NAME: ...
USERNAME: ...
BIO: ...
"""


# ─── 25 фоллбэк-профилей (женские) ─────────────────────────────────

FALLBACK_PROFILES: dict[str, list[dict]] = {
    "beauty": [
        {"first_name": "Алина", "last_name": "Романова", "bio": "Бьюти и технологии | Лайфхаки для интернета", "username_base": "alina_rom"},
        {"first_name": "Виктория", "last_name": "Белова", "bio": "Тренды и цифровой стиль | Свободный интернет", "username_base": "vika_belova"},
        {"first_name": "Полина", "last_name": "Кузнецова", "bio": "Бьюти-блог | Цифровые лайфхаки", "username_base": "polina_kuz"},
    ],
    "casual": [
        {"first_name": "Кристина", "last_name": "М.", "bio": "Делюсь полезностями | Интернет без границ", "username_base": "kristina_m"},
        {"first_name": "Настя", "last_name": "", "bio": "Лайфхаки, технологии, всё интересное", "username_base": "nastya_life"},
        {"first_name": "Юля", "last_name": "Соколова", "bio": "Полезности каждый день | Свободный доступ", "username_base": "yulya_sok"},
    ],
    "student": [
        {"first_name": "Дарья", "last_name": "", "bio": "Студентка IT | Нейросети и технологии", "username_base": "darya_it"},
        {"first_name": "Софья", "last_name": "Волкова", "bio": "Учусь на программиста | AI и VPN", "username_base": "sofya_dev"},
        {"first_name": "Мария", "last_name": "К.", "bio": "IT-студентка | Цифровая свобода", "username_base": "masha_tech"},
    ],
    "tech": [
        {"first_name": "Екатерина", "last_name": "Новикова", "bio": "Тех-блогер | Обзоры VPN и приватность", "username_base": "kate_tech"},
        {"first_name": "Анна", "last_name": "Козлова", "bio": "Цифровая безопасность | Обход блокировок", "username_base": "anna_sec"},
    ],
    "lifestyle": [
        {"first_name": "Ксения", "last_name": "Морозова", "bio": "Путешествия и технологии | Интернет везде", "username_base": "ksenya_travel"},
        {"first_name": "Елена", "last_name": "Павлова", "bio": "Лайфстайл | Свободный интернет в путешествиях", "username_base": "lena_life"},
    ],
    "blogger": [
        {"first_name": "Диана", "last_name": "Смирнова", "bio": "Блогер | Контент и цифровые инструменты", "username_base": "diana_blog"},
        {"first_name": "Карина", "last_name": "Ильина", "bio": "Контент-мейкер | Технологии для блогеров", "username_base": "karina_content"},
    ],
    "fitness": [
        {"first_name": "Александра", "last_name": "Орлова", "bio": "ЗОЖ и технологии | Энергия и свобода", "username_base": "sasha_fit"},
        {"first_name": "Валерия", "last_name": "Попова", "bio": "Фитнес и лайфхаки | Интернет без ограничений", "username_base": "lera_sport"},
    ],
    "business": [
        {"first_name": "Ольга", "last_name": "Соколова", "bio": "Предприниматель | Цифровые решения для бизнеса", "username_base": "olga_biz"},
        {"first_name": "Наталья", "last_name": "Лебедева", "bio": "Бизнес и технологии | Оптимизация процессов", "username_base": "natasha_biz"},
    ],
    "creative": [
        {"first_name": "Милана", "last_name": "Фёдорова", "bio": "Дизайнер | Креатив и цифровые инструменты", "username_base": "milana_art"},
        {"first_name": "Арина", "last_name": "Петрова", "bio": "Художник | Вдохновение и технологии", "username_base": "arina_create"},
    ],
    "friendly": [
        {"first_name": "Алёна", "last_name": "Иванова", "bio": "Позитив и полезности | Свободный интернет", "username_base": "alyona_happy"},
        {"first_name": "Вероника", "last_name": "Тихонова", "bio": "Общаюсь и делюсь | Интернет без границ", "username_base": "nika_joy"},
        {"first_name": "Таисия", "last_name": "Михайлова", "bio": "Открытая ко всему новому | Технологии", "username_base": "taya_open"},
    ],
}

ALL_STYLES = list(FALLBACK_PROFILES.keys())


# ─── 25 промптов для аватарок (Gemini Imagen) ───────────────────────

AVATAR_PROMPTS = [
    "A young brunette woman with bright brown eyes, wearing a light summer dress, taking a selfie in a sunny park with green trees, natural daylight, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a white blouse, taking a selfie near a large window with city view, soft natural light, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a cozy knit sweater, taking a selfie in a modern cafe, warm ambient light, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing an elegant black dress, taking a selfie on a rooftop terrace, golden hour sunlight, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a casual denim jacket, taking a selfie on a city street with flowers, natural daylight, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a pastel pink top, taking a selfie in a blooming garden, soft morning light, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a striped t-shirt, taking a selfie on a balcony overlooking the city, sunset light, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a light blue dress, taking a selfie near a fountain in a park, bright daylight, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a beige trench coat, taking a selfie on a tree-lined avenue in autumn, golden light, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a white summer dress with floral pattern, taking a selfie in a sunlit field, natural daylight, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a lavender blouse, taking a selfie in a cozy library corner, warm lamp light, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a red dress, taking a selfie near an old building with ivy, afternoon sunlight, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a mint green top, taking a selfie at a seaside promenade, bright daylight, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing an oversized hoodie, taking a selfie in a modern apartment with plants, soft natural light, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a silk blouse, taking a selfie in an art gallery, soft museum lighting, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a cream cardigan, taking a selfie in a botanical garden, dappled sunlight through leaves, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a coral dress, taking a selfie on stone steps of a historical building, golden hour, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a grey turtleneck, taking a selfie near a bookshelf in a home office, warm ambient light, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a yellow sundress, taking a selfie in a sunflower field, bright daylight, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a navy blazer, taking a selfie in a stylish co-working space, natural window light, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a floral maxi dress, taking a selfie on a wooden bridge in a park, morning light, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a white linen shirt, taking a selfie at an outdoor cafe table, soft afternoon light, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a peach colored top, taking a selfie near blooming cherry trees, spring daylight, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a classic little black dress, taking a selfie on a garden terrace, evening golden light, warm tones, realistic portrait photo, beautiful face",
    "A young brunette woman with bright brown eyes, wearing a sage green dress, taking a selfie in a vineyard, late afternoon sunlight, warm tones, realistic portrait photo, beautiful face",
]


# ─── Транслитерация кириллица → латиница ─────────────────────────────

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

USERNAME_SUFFIXES = [
    "_23", "_life", "_vpn", "_tech", "_rf", "_01", "_me", "_msk",
    "_24", "_go", "_top", "_pro", "_ok", "_hey", "_sun", "_joy",
    "_25", "_now", "_yes", "_up", "_26", "_new", "_wow", "_sky",
]


class AccountPackager:
    """AI-упаковка женских профилей аккаунтов."""

    def __init__(self, session_manager: SessionManager):
        self.session_mgr = session_manager
        self._client: Optional[genai.Client] = None
        self._init_client()

    def _init_client(self):
        if settings.GEMINI_API_KEY:
            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)

    @staticmethod
    def _model_candidates() -> list[str]:
        return get_text_model_candidates(
            settings.GEMINI_MODEL,
            settings.GEMINI_FLASH_MODEL,
        )

    # ── Генерация профиля ────────────────────────────────────────────

    async def generate_profile(self, style: str = "casual") -> dict:
        """
        Сгенерировать данные профиля через AI.
        Возвращает {"first_name", "last_name", "bio", "username_base"}.
        """
        if not self._client:
            return self._get_fallback_profile(style)

        prompt = PACKAGING_PROMPT.format(style=style)

        for model_name in self._model_candidates():
            try:
                response = await asyncio.to_thread(
                    self._client.models.generate_content,
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.9,
                        max_output_tokens=300,
                    ),
                )
                if response and response.text:
                    return self._parse_profile(response.text, style)
            except Exception as exc:
                log.warning(f"Ошибка генерации профиля (model={model_name}): {exc}")

        return self._get_fallback_profile(style)

    def _parse_profile(self, text: str, style: str) -> dict:
        """Парсинг ответа AI."""
        result = {"first_name": "", "last_name": "", "bio": "", "username_base": ""}

        for line in text.strip().split("\n"):
            line = line.strip()
            upper = line.upper()
            if upper.startswith("FIRST_NAME:"):
                result["first_name"] = line.split(":", 1)[1].strip()
            elif upper.startswith("LAST_NAME:"):
                result["last_name"] = line.split(":", 1)[1].strip()
            elif upper.startswith("USERNAME:"):
                result["username_base"] = line.split(":", 1)[1].strip().lstrip("@")
            elif upper.startswith("BIO:"):
                result["bio"] = line.split(":", 1)[1].strip()

        # Валидация
        if not result["first_name"]:
            fallback = self._get_fallback_profile(style)
            result["first_name"] = fallback["first_name"]
            if not result["username_base"]:
                result["username_base"] = fallback["username_base"]

        if not result["username_base"]:
            result["username_base"] = self._name_to_username_base(result["first_name"])

        if len(result["bio"]) > 70:
            result["bio"] = result["bio"][:67] + "..."

        return result

    @staticmethod
    def _get_fallback_profile(style: str) -> dict:
        """Фоллбэк-профили (женские)."""
        pool = FALLBACK_PROFILES.get(style, FALLBACK_PROFILES["casual"])
        return random.choice(pool).copy()

    # ── Транслитерация и username ─────────────────────────────────────

    @staticmethod
    def _name_to_username_base(name: str) -> str:
        """Транслитерация русского имени в латиницу для username."""
        result = []
        for char in name.lower():
            if char in _TRANSLIT:
                result.append(_TRANSLIT[char])
            elif char.isalnum() or char in ("_", "."):
                result.append(char)
        return "".join(result) or "user"

    async def generate_username(self, phone: str, profile: dict) -> Optional[str]:
        """Сгенерировать и проверить доступный username."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return None

        base = profile.get("username_base", "")
        if not base:
            base = self._name_to_username_base(profile.get("first_name", "user"))

        # Убрать недопустимые символы
        base = "".join(c for c in base if c.isalnum() or c in ("_", "."))
        if len(base) < 3:
            base = "girl_" + base

        # Кандидаты: базовый + варианты с суффиксами
        candidates = [base]
        shuffled_suffixes = random.sample(USERNAME_SUFFIXES, len(USERNAME_SUFFIXES))
        for suffix in shuffled_suffixes:
            candidates.append(base + suffix)

        for candidate in candidates:
            # Telegram username: 5-32 символа, a-z0-9_
            if len(candidate) < 5 or len(candidate) > 32:
                continue

            try:
                available = await client(CheckUsernameRequest(username=candidate))
                if available:
                    log.debug(f"{phone}: username '{candidate}' доступен")
                    return candidate
                await asyncio.sleep(random.uniform(1.0, 2.0))
            except Exception as exc:
                log.debug(f"{phone}: ошибка проверки username '{candidate}': {exc}")
                await asyncio.sleep(2.0)

        log.warning(f"{phone}: не удалось найти свободный username")
        return None

    async def apply_username(self, phone: str, username: str) -> bool:
        """Установить username на аккаунт."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return False

        try:
            await client(UpdateUsernameRequest(username=username))
            log.info(f"{phone}: username установлен — @{username}")
            return True
        except Exception as exc:
            log.error(f"{phone}: ошибка установки username: {exc}")
            return False

    # ── Аватарки (Gemini Imagen) ─────────────────────────────────────

    async def generate_avatar(self, prompt_index: int = 0) -> Optional[Path]:
        """Сгенерировать аватарку через Gemini Imagen."""
        if not self._client:
            log.warning("Gemini API не настроен, пропуск генерации аватарки")
            return None

        prompt = AVATAR_PROMPTS[prompt_index % len(AVATAR_PROMPTS)]
        save_dir = settings.profile_avatars_path
        save_path = save_dir / f"avatar_{prompt_index}_{random.randint(1000, 9999)}.png"

        try:
            response = await asyncio.to_thread(
                self._client.models.generate_images,
                model="imagen-4.0-generate-001",
                prompt=prompt,
                config=types.GenerateImagesConfig(number_of_images=1),
            )

            if not response or not response.generated_images:
                log.warning(f"Imagen: пустой ответ для промпта #{prompt_index}")
                return None

            image = response.generated_images[0].image
            image.save(str(save_path))
            log.info(f"Аватарка сохранена: {save_path}")
            return save_path

        except Exception as exc:
            log.error(f"Ошибка генерации аватарки: {exc}")
            return None

    async def apply_avatar(self, phone: str, avatar_path: Path) -> bool:
        """Установить аватарку на аккаунт через Telethon."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return False

        try:
            file = await client.upload_file(str(avatar_path))
            await client(UploadProfilePhotoRequest(file=file))
            log.info(f"{phone}: аватарка установлена — {avatar_path.name}")
            return True
        except Exception as exc:
            log.error(f"{phone}: ошибка установки аватарки: {exc}")
            return False

    # ── Применение профиля (имя, фамилия, bio) ──────────────────────

    async def apply_profile(
        self,
        phone: str,
        profile: dict,
        channel_link: str = "",
    ) -> bool:
        """Применить имя/фамилию/bio к аккаунту через Telethon."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            log.warning(f"{phone}: не подключён для упаковки")
            return False

        bio = profile.get("bio", "")
        if channel_link and len(bio) + len(channel_link) + 1 < 70:
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

    # ── Полная упаковка одного аккаунта ──────────────────────────────

    async def package_account(
        self,
        phone: str,
        style: str = "casual",
        avatar_prompt_index: int = 0,
    ) -> dict:
        """Полная упаковка: профиль + username + аватарка."""
        result = {
            "phone": phone,
            "profile": {},
            "applied": False,
            "username": None,
            "username_applied": False,
            "avatar_applied": False,
            "style": style,
        }

        # 1. Генерация профиля
        profile = await self.generate_profile(style)
        result["profile"] = profile

        # 2. Применение имени/фамилии/bio
        channel_link = settings.PRODUCT_CHANNEL_LINK or ""
        result["applied"] = await self.apply_profile(phone, profile, channel_link)
        await asyncio.sleep(random.uniform(2.0, 5.0))

        # 3. Генерация и установка username
        username = await self.generate_username(phone, profile)
        if username:
            result["username"] = username
            result["username_applied"] = await self.apply_username(phone, username)
            await asyncio.sleep(random.uniform(2.0, 5.0))

        # 4. Генерация и установка аватарки
        avatar_path = await self.generate_avatar(avatar_prompt_index)
        if avatar_path:
            result["avatar_applied"] = await self.apply_avatar(phone, avatar_path)

        return result

    async def package_all_accounts(self) -> list[dict]:
        """Упаковать все подключённые аккаунты (женские профили)."""
        connected = self.session_mgr.get_connected_phones()
        results = []

        for i, phone in enumerate(connected):
            style = ALL_STYLES[i % len(ALL_STYLES)]
            avatar_idx = i % len(AVATAR_PROMPTS)

            log.info(f"Упаковка аккаунта {i + 1}/{len(connected)}: {phone} (стиль: {style})")
            result = await self.package_account(phone, style, avatar_idx)
            results.append(result)

            # Антибан: задержка между аккаунтами
            if i < len(connected) - 1:
                delay = random.uniform(15.0, 30.0)
                log.debug(f"Задержка перед следующим аккаунтом: {delay:.0f}с")
                await asyncio.sleep(delay)

        return results
