"""
Day 1 — Безопасная упаковка аккаунта (Шаг 1/4).

Операции Day 1:
  1. Подключение + проверка alive
  2. Скрытие номера телефона (SetPrivacyRequest)
  3. Генерация аватарки (парень с телефоном, VPN) через Gemini Imagen
  4. Установка аватарки на профиль
  5. Бэкап StringSession
  6. Отключение (disconnect, НЕ log_out!)

Между операциями — гауссовские задержки 15-45 минут.
Запуск: venv/bin/python scripts/day1_package.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from pathlib import Path

# Добавить корень проекта в sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.account import SetPrivacyRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.types import (
    InputPrivacyKeyPhoneNumber,
    InputPrivacyValueDisallowAll,
    InputPrivacyValueAllowContacts,
)

# ─── Конфигурация ────────────────────────────────────────
PHONE = "79637421804"
SESSION_DIR = PROJECT_ROOT / "data" / "sessions"
BACKUP_DIR = PROJECT_ROOT / "data" / "session_backups"
AVATAR_DIR = PROJECT_ROOT / "data" / "avatars" / "profiles"

# Гауссовские задержки (минуты)
DELAY_MIN_MINUTES = 15
DELAY_MAX_MINUTES = 45
DELAY_MEAN_MINUTES = 25
DELAY_STD_MINUTES = 5

# Промпт для аватарки — ПАРЕНЬ с телефоном, VPN
AVATAR_PROMPT = (
    "A handsome young man in his mid-20s with short dark hair and light stubble, "
    "wearing a casual grey hoodie, holding a modern smartphone in his hand. "
    "The smartphone screen clearly shows the text 'VPN' in large bold letters "
    "with a glowing shield icon. He is smiling confidently, looking at camera. "
    "Background is a soft blurred modern office with warm lighting. "
    "Realistic portrait photo, high quality, natural skin texture, "
    "professional photography style, warm color tones."
)


def gaussian_delay(min_m: float = DELAY_MIN_MINUTES,
                   max_m: float = DELAY_MAX_MINUTES) -> float:
    """Гауссовская задержка в секундах, clipped в [min, max]."""
    raw = random.gauss(DELAY_MEAN_MINUTES, DELAY_STD_MINUTES)
    clamped = max(min_m, min(max_m, raw))
    return clamped * 60


def short_delay() -> float:
    """Короткая пауза 3-8 секунд между мелкими операциями."""
    return random.gauss(5.0, 1.5)


async def step_connect(meta: dict) -> TelegramClient:
    """Шаг 0: Подключение и проверка alive."""
    print(f"\n{'='*60}")
    print(f"  STEP 0: Подключение к {meta['phone']}")
    print(f"{'='*60}")

    client = TelegramClient(
        str(SESSION_DIR / meta["session_file"]),
        meta["app_id"],
        meta["app_hash"],
        device_model=meta["device"],
        system_version=meta["sdk"],
        app_version=meta["app_version"],
        lang_code=meta.get("lang_pack", "ru"),
        system_lang_code=meta.get("system_lang_pack", "ru-ru"),
    )

    await client.connect()

    if not await client.is_user_authorized():
        raise RuntimeError("Аккаунт НЕ АВТОРИЗОВАН — сессия мёртвая!")

    me = await client.get_me()
    if me is None:
        raise RuntimeError("get_me() вернул None — сессия мёртвая!")

    # Проверка заморозки
    bv = getattr(me, "bot_verification", None)
    if bv:
        raise RuntimeError(f"АККАУНТ ЗАМОРОЖЕН: {bv}")

    print(f"  ✓ Alive: {me.first_name} {me.last_name or ''} (ID: {me.id})")
    print(f"  ✓ Restricted: {getattr(me, 'restricted', False)}")
    return client


async def step_hide_phone(client: TelegramClient) -> bool:
    """Шаг 1: Скрыть номер телефона (видим только контактам)."""
    print(f"\n{'='*60}")
    print(f"  STEP 1: Скрытие номера телефона")
    print(f"{'='*60}")

    try:
        await client(SetPrivacyRequest(
            key=InputPrivacyKeyPhoneNumber(),
            rules=[
                InputPrivacyValueAllowContacts(),  # контакты видят
                InputPrivacyValueDisallowAll(),     # остальные — нет
            ],
        ))
        print("  ✓ Номер скрыт (видим только контактам)")
        return True
    except Exception as exc:
        print(f"  ✗ Ошибка: {exc}")
        return False


async def step_generate_avatar() -> Path | None:
    """Шаг 2: Генерация аватарки через Gemini Imagen."""
    print(f"\n{'='*60}")
    print(f"  STEP 2: Генерация аватарки (Gemini Imagen)")
    print(f"{'='*60}")

    # Загрузить GEMINI_API_KEY из .env или config
    try:
        from config import settings
        api_key = settings.GEMINI_API_KEY
    except Exception:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
        api_key = os.getenv("GEMINI_API_KEY", "")

    if not api_key:
        print("  ✗ GEMINI_API_KEY не задан — пропуск генерации")
        return None

    try:
        from google import genai
        from google.genai import types

        ai_client = genai.Client(api_key=api_key)

        print(f"  Промпт: {AVATAR_PROMPT[:80]}...")
        print(f"  Генерирую...")

        response = await asyncio.to_thread(
            ai_client.models.generate_images,
            model="imagen-4.0-generate-001",
            prompt=AVATAR_PROMPT,
            config=types.GenerateImagesConfig(number_of_images=1),
        )

        if not response or not response.generated_images:
            print("  ✗ Imagen вернул пустой ответ")
            return None

        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        save_path = AVATAR_DIR / f"avatar_{PHONE}_{random.randint(1000, 9999)}.png"

        image = response.generated_images[0].image
        image.save(str(save_path))
        print(f"  ✓ Аватарка сохранена: {save_path}")
        return save_path

    except Exception as exc:
        print(f"  ✗ Ошибка генерации: {exc}")
        return None


async def step_set_avatar(client: TelegramClient, avatar_path: Path) -> bool:
    """Шаг 3: Установить аватарку на профиль."""
    print(f"\n{'='*60}")
    print(f"  STEP 3: Установка аватарки на профиль")
    print(f"{'='*60}")

    try:
        file = await client.upload_file(str(avatar_path))
        await client(UploadProfilePhotoRequest(file=file))
        print(f"  ✓ Аватарка установлена: {avatar_path.name}")
        return True
    except Exception as exc:
        print(f"  ✗ Ошибка: {exc}")
        return False


async def step_backup(client: TelegramClient, phone: str) -> bool:
    """Шаг 4: Экспорт StringSession бэкапа."""
    print(f"\n{'='*60}")
    print(f"  STEP 4: Бэкап StringSession")
    print(f"{'='*60}")

    try:
        string_session = StringSession.save(client.session)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = BACKUP_DIR / f"{phone}.backup"
        backup_path.write_text(string_session)
        print(f"  ✓ Бэкап сохранён: {backup_path}")
        return True
    except Exception as exc:
        print(f"  ✗ Ошибка: {exc}")
        return False


def wait_message(seconds: float, description: str) -> str:
    """Форматировать сообщение ожидания."""
    minutes = seconds / 60
    return f"  ⏳ Пауза {minutes:.1f} мин перед: {description}"


async def main():
    print("=" * 60)
    print("  DAY 1: Безопасная упаковка аккаунта")
    print(f"  Телефон: {PHONE}")
    print(f"  Операции: скрытие номера + аватарка")
    print("=" * 60)

    # Загрузить метаданные
    meta_path = SESSION_DIR / f"{PHONE}.json"
    with open(meta_path) as f:
        meta = json.load(f)

    # ─── STEP 0: Подключение ───
    client = await step_connect(meta)

    try:
        # ─── Пауза перед первой операцией ───
        delay = gaussian_delay(5, 15)  # 5-15 мин, первая пауза покороче
        print(wait_message(delay, "скрытие номера"))
        await asyncio.sleep(delay)

        # ─── STEP 1: Скрытие номера ───
        ok_privacy = await step_hide_phone(client)
        if not ok_privacy:
            print("\n  ⚠️  Скрытие номера не удалось. Продолжаю...")

        # ─── Пауза перед аватаркой ───
        delay = gaussian_delay()
        print(wait_message(delay, "генерация аватарки"))
        await asyncio.sleep(delay)

        # ─── STEP 2: Генерация аватарки ───
        avatar_path = await step_generate_avatar()

        if avatar_path:
            # Короткая пауза перед загрузкой
            await asyncio.sleep(short_delay())

            # ─── STEP 3: Установка аватарки ───
            ok_avatar = await step_set_avatar(client, avatar_path)
        else:
            ok_avatar = False
            print("\n  ⚠️  Аватарка не сгенерирована. Можно сгенерировать вручную.")

        # ─── Короткая пауза ───
        await asyncio.sleep(short_delay())

        # ─── STEP 4: Бэкап ───
        await step_backup(client, PHONE)

        # ─── Итог ───
        print(f"\n{'='*60}")
        print(f"  DAY 1 РЕЗУЛЬТАТ:")
        print(f"  • Номер скрыт: {'✓' if ok_privacy else '✗'}")
        print(f"  • Аватарка: {'✓' if ok_avatar else '✗'}")
        print(f"  • Бэкап: ✓")
        print(f"{'='*60}")
        print(f"\n  Следующий шаг (Day 3-7):")
        print(f"  → Установка био")
        print(f"  → Подписка на 3-5 каналов (только чтение)")
        print(f"\n  Следующий шаг (Day 22-30):")
        print(f"  → Создание канала-переходника")
        print(f"  → Пост с ссылкой + аватарка канала")
        print(f"  → Закрепление канала в профиле")

    finally:
        # НИКОГДА не вызываем log_out — только disconnect
        await client.disconnect()
        print("\n  ✓ Disconnect (сессия сохранена)")


if __name__ == "__main__":
    asyncio.run(main())
