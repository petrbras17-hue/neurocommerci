"""
NEURO COMMENTING — Точка входа.
Система автоматического комментирования в Telegram.

Запуск:
    python main.py          — Telegram-бот (основной режим)
    python main.py --cli    — CLI интерфейс в терминале
    python main.py --dry-run — Тестовый запуск без реальной отправки

Graceful shutdown: Ctrl+C для безопасного завершения (сохраняет состояние).
"""

import asyncio
import sys

from config import settings
from storage.sqlite_db import init_db, dispose_engine
from utils.bootstrap import bootstrap
from utils.logger import log


async def run_bot():
    """Запуск Telegram-бота (основной режим)."""
    from admin.bot_admin import start_bot

    log.info("Инициализация базы данных...")
    await init_db()

    try:
        log.info("Запуск NEURO COMMENTING в режиме Telegram-бота")
        await start_bot()
    finally:
        await dispose_engine()
        log.info("БД соединение закрыто")


async def run_cli():
    """Запуск CLI интерфейса."""
    from admin.cli_menu import main as cli_main

    log.info("Инициализация базы данных...")
    await init_db()

    try:
        log.info("Запуск NEURO COMMENTING в режиме CLI")
        await cli_main()
    finally:
        await dispose_engine()


def main():
    # Проверка критичных настроек
    warnings = settings.validate_critical()
    if warnings:
        print()
        print("  ⚠️  Предупреждения конфигурации:")
        for w in warnings:
            print(f"     • {w}")
        print()

    # Bootstrap: восстановить данные из env vars (Railway)
    bootstrap()

    if "--cli" in sys.argv:
        asyncio.run(run_cli())
    elif "--dry-run" in sys.argv:
        import os
        os.environ["NEURO_DRY_RUN"] = "1"
        print()
        print("  🧪 NEURO COMMENTING — DRY RUN")
        print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  Тестовый запуск без реальной отправки")
        print()
        asyncio.run(run_bot())
    else:
        print()
        print("  🚀 NEURO COMMENTING")
        print("  ━━━━━━━━━━━━━━━━━━")
        print("  Запуск Telegram-бота...")
        print(f"  AI: {settings.GEMINI_MODEL}")
        print(f"  Product: {settings.PRODUCT_NAME} ({settings.PRODUCT_BOT_LINK})")
        print()
        print("  Ctrl+C для graceful shutdown")
        print("  --cli для терминального интерфейса")
        print("  --dry-run для тестового режима")
        print()
        asyncio.run(run_bot())


if __name__ == "__main__":
    main()
