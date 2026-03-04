"""
Проверка: разморожен ли аккаунт.
Безопасный метод: help.getAppConfig (read-only, без write-операций).
Запуск: python check_frozen.py
"""

import asyncio
import json
from telethon.tl.functions.help import GetAppConfigRequest

from config import settings
from utils.standalone_helpers import load_proxy_for_phone, build_client


async def check():
    sessions_dir = settings.sessions_path
    session_files = sorted(sessions_dir.glob("*.session"))
    phones = [f.stem for f in session_files if f.stem.isdigit()]

    if not phones:
        print("  Нет .session файлов")
        return

    for i, phone in enumerate(phones):
        # Антибан: 5с задержка между аккаунтами
        if i > 0:
            print("  Антибан задержка 5с...")
            await asyncio.sleep(5)

        # 1 IP = 1 аккаунт: уникальный прокси для каждого
        proxy = load_proxy_for_phone(phone)

        json_path = sessions_dir / f"{phone}.json"
        data = json.loads(json_path.read_text()) if json_path.exists() else {}

        client = build_client(phone, data, proxy)

        try:
            await client.connect()
            if not await client.is_user_authorized():
                print(f"  +{phone} — NOT AUTHORIZED (сессия невалидна)")
                await client.disconnect()
                continue

            me = await client.get_me()
            name = f"{me.first_name or ''} {me.last_name or ''}".strip()
            print(f"  +{phone} ({name}) — прокси: {proxy[1]}:{proxy[2]}")

            # Безопасная проверка через help.getAppConfig (READ-ONLY)
            try:
                result = await client(GetAppConfigRequest(hash=0))
                # getAppConfig возвращает JSONValue; проверяем freeze-поля
                # Если аккаунт заморожен, Telegram добавляет freeze_since_date
                config_text = str(result)
                if "freeze" in config_text.lower():
                    print(f"  ❄️  +{phone} — ещё заморожен ({name})")
                else:
                    print(f"  ✅ +{phone} — РАЗМОРОЖЕН! ({name})")
            except Exception as e:
                err_str = str(e).upper()
                if "FROZEN" in err_str:
                    print(f"  ❄️  +{phone} — ещё заморожен ({name})")
                else:
                    print(f"  ⚠️  +{phone} — ошибка getAppConfig: {e}")

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
