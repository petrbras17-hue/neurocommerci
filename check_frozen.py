"""
Проверка: разморожен ли аккаунт.
Запуск: python check_frozen.py
"""

import asyncio
import json
from pathlib import Path
from telethon import TelegramClient
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.errors import FrozenMethodInvalidError

from config import settings


def load_proxy():
    lines = Path("data/proxies.txt").read_text().strip().split("\n")
    parts = lines[0].strip().split(":")
    return (3, parts[0], int(parts[1]), True, parts[2], parts[3])


async def check():
    sessions_dir = settings.sessions_path
    session_files = sorted(sessions_dir.glob("*.session"))
    phones = [f.stem for f in session_files if f.stem.isdigit()]

    if not phones:
        print("  Нет .session файлов")
        return

    proxy = load_proxy()

    for phone in phones:
        json_path = sessions_dir / f"{phone}.json"
        data = json.loads(json_path.read_text()) if json_path.exists() else {}

        client = TelegramClient(
            str(sessions_dir / phone),
            api_id=data.get("app_id") or settings.TELEGRAM_API_ID,
            api_hash=data.get("app_hash") or settings.TELEGRAM_API_HASH,
            proxy=proxy,
            device_model=data.get("device", "Samsung Galaxy S23"),
            system_version=data.get("sdk", "SDK 29"),
            app_version=data.get("app_version", "12.4.3"),
            lang_code=data.get("lang_pack", "ru"),
            system_lang_code=data.get("system_lang_pack", "ru-ru"),
            timeout=30,
            connection_retries=5,
            retry_delay=5,
        )

        try:
            await client.connect()
            if not await client.is_user_authorized():
                print(f"  +{phone} — NOT AUTHORIZED (сессия невалидна)")
                await client.disconnect()
                continue

            me = await client.get_me()
            name = f"{me.first_name or ''} {me.last_name or ''}".strip()

            # Тест: попробовать обновить профиль (без изменения данных)
            try:
                await client(UpdateProfileRequest(
                    first_name=me.first_name or "",
                    last_name=me.last_name or "",
                ))
                print(f"  ✅ +{phone} — РАЗМОРОЖЕН! ({name})")
            except FrozenMethodInvalidError:
                print(f"  ❄️  +{phone} — ещё заморожен ({name})")
            except Exception as e:
                if "FROZEN" in str(e).upper():
                    print(f"  ❄️  +{phone} — ещё заморожен ({name})")
                else:
                    print(f"  ⚠️  +{phone} — ошибка: {e}")

            await client.disconnect()

        except Exception as e:
            print(f"  ❌ +{phone} — не подключился: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    print()
    print("  === Проверка заморозки аккаунтов ===")
    print()
    asyncio.run(check())
    print()
