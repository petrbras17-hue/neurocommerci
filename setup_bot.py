"""
Настройка профиля Telegram-бота: аватарка (Gemini Imagen), описание, шапка.
Запуск: python setup_bot.py
"""

from __future__ import annotations

import requests
import sys
from pathlib import Path
from typing import Optional

from config import settings, BASE_DIR
from utils.logger import log


TOKEN = settings.ADMIN_BOT_TOKEN
API = f"https://api.telegram.org/bot{TOKEN}"

BOT_NAME = "NEURO COMMENTING"

BOT_SHORT_DESCRIPTION = (
    f"Система автокомментирования в Telegram для продвижения {settings.PRODUCT_NAME}. "
    "AI-генерация, 10 стилей, антибан."
)

BOT_DESCRIPTION = (
    f"🚀 NEURO COMMENTING — система автоматического комментирования "
    f"в Telegram-каналах для продвижения {settings.PRODUCT_NAME}.\n\n"
    "Возможности:\n"
    "  🤖 AI-генерация комментариев (Google Gemini)\n"
    "  👩 Упаковка профилей: женские имена, аватарки, username\n"
    f"  📺 Каналы-переходники с закреплённым постом {settings.PRODUCT_NAME}\n"
    "  🔍 Парсинг и мониторинг каналов по тематикам\n"
    "  📊 Статистика и аналитика в реальном времени\n"
    "  🛡 Антибан: прогрев, задержки, пассивные действия\n\n"
    "Нажми /start чтобы начать управление."
)

AVATAR_PROMPT = (
    "A futuristic glowing neon brain made of blue and purple circuits and neural networks, "
    "floating in a dark space with Telegram-style blue gradient background, "
    "digital art, clean minimalistic style, centered composition, "
    "vibrant cyan and electric blue tones, professional icon design, "
    "suitable for a Telegram bot profile picture"
)


def api_call(method: str, **kwargs) -> dict:
    """Вызов Telegram Bot API."""
    r = requests.post(f"{API}/{method}", **kwargs)
    result = r.json()
    if not result.get("ok"):
        log.error(f"Bot API {method}: {result}")
    return result


def set_bot_description():
    """Установить описание бота."""
    print("  [1] Устанавливаю описание бота...")

    r1 = api_call("setMyDescription", json={"description": BOT_DESCRIPTION})
    print(f"      Description: {'✅' if r1.get('ok') else '❌'}")

    r2 = api_call("setMyShortDescription", json={"short_description": BOT_SHORT_DESCRIPTION})
    print(f"      Short desc:  {'✅' if r2.get('ok') else '❌'}")

    r3 = api_call("setMyName", json={"name": BOT_NAME})
    print(f"      Name:        {'✅' if r3.get('ok') else '❌'}")


def set_bot_avatar_from_file(path: Path):
    """Установить аватарку бота из файла."""
    print(f"  [2] Устанавливаю аватарку из {path.name}...")
    with open(path, "rb") as f:
        r = api_call("setMyProfilePhoto", files={"photo": f})
    print(f"      Avatar: {'✅' if r.get('ok') else '❌ ' + str(r)}")


def generate_avatar_gemini() -> Optional[Path]:
    """Сгенерировать аватарку через Gemini Imagen."""
    if not settings.GEMINI_API_KEY:
        print("      ⚠️ GEMINI_API_KEY не задан, пропуск генерации")
        return None

    print("  [2] Генерирую аватарку через Gemini Imagen...")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    save_path = BASE_DIR / "data" / "avatars" / "bot_avatar.png"

    try:
        response = client.models.generate_images(
            model="imagen-4.0-generate-001",
            prompt=AVATAR_PROMPT,
            config=types.GenerateImagesConfig(number_of_images=1),
        )

        if not response or not response.generated_images:
            print("      ⚠️ Пустой ответ от Imagen")
            return None

        image = response.generated_images[0].image
        image.save(str(save_path))
        print(f"      Аватарка сохранена: {save_path}")
        return save_path

    except Exception as exc:
        print(f"      ❌ Ошибка Imagen: {exc}")
        return None


def send_welcome_post():
    """Отправить приветственный пост в бот (самому себе = admin)."""
    admin_id = settings.ADMIN_TELEGRAM_ID
    if not admin_id:
        print("  [3] ⚠️ ADMIN_TELEGRAM_ID не задан, пропуск поста")
        return

    print(f"  [3] Отправляю приветственный пост админу ({admin_id})...")

    # Сначала отправляю баннер продукта как фото
    banner_path = BASE_DIR / settings.PRODUCT_AVATAR_PATH
    post_text = (
        "<b>🚀 NEURO COMMENTING</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Система автоматического комментирования в Telegram\n"
        f"для продвижения <b>{settings.PRODUCT_NAME}</b>.\n\n"
        "📋 <b>Что умеет:</b>\n"
        "  🤖 AI-комментарии (Google Gemini)\n"
        "  👩 Женские профили + аватарки\n"
        f"  📺 Каналы-переходники с рекламой {settings.PRODUCT_NAME}\n"
        "  🔍 Парсинг каналов по тематикам\n"
        "  📊 Статистика в реальном времени\n"
        "  🛡 Антибан: прогрев, задержки, ротация\n\n"
        "📌 <b>Быстрый старт:</b>\n"
        "  1. Аккаунты → Подключить все\n"
        "  2. Аккаунты → Упаковка профилей\n"
        "  3. Аккаунты → Каналы-переходники\n"
        "  4. Каналы → Парсинг → Мониторинг\n"
        "  5. Комментирование → Запуск\n\n"
        f'🎯 <a href="{settings.PRODUCT_BOT_LINK}">{settings.PRODUCT_NAME} — летай без ограничений</a>'
    )

    if banner_path.exists():
        with open(banner_path, "rb") as f:
            r = api_call(
                "sendPhoto",
                data={
                    "chat_id": admin_id,
                    "caption": post_text,
                    "parse_mode": "HTML",
                },
                files={"photo": f},
            )
    else:
        r = api_call(
            "sendMessage",
            json={
                "chat_id": admin_id,
                "text": post_text,
                "parse_mode": "HTML",
            },
        )

    print(f"      Post: {'✅' if r.get('ok') else '❌ ' + str(r)}")


def main():
    print()
    print("  === NEURO COMMENTING — Настройка бота ===")
    print()

    if not TOKEN:
        print("  ❌ ADMIN_BOT_TOKEN не задан в .env")
        return

    # 1. Описание и шапка
    set_bot_description()

    # 2. Аватарка (Gemini Imagen)
    avatar_path = generate_avatar_gemini()
    if avatar_path and avatar_path.exists():
        set_bot_avatar_from_file(avatar_path)
    else:
        # Фоллбэк — баннер продукта
        banner = BASE_DIR / settings.PRODUCT_AVATAR_PATH
        if banner.exists():
            print(f"      Используем {settings.PRODUCT_NAME} баннер как фоллбэк...")
            set_bot_avatar_from_file(banner)

    # 3. Приветственный пост
    send_welcome_post()

    print()
    print("  ✅ Настройка бота завершена!")
    print()


if __name__ == "__main__":
    main()
