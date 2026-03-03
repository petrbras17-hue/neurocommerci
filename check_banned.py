"""
Проверка 10 забаненных аккаунтов из _banned/.
Каждый через отдельный прокси.
Запуск: python check_banned.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from telethon import TelegramClient
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.errors import (
    UserDeactivatedBanError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    PhoneNumberBannedError,
)

from config import settings

BANNED_DIR = settings.sessions_path / "_banned"
PROXIES_FILE = Path("data/proxies.txt")


def load_proxies():
    """Загружает все рабочие прокси."""
    lines = PROXIES_FILE.read_text().strip().split("\n")
    proxies = []
    for line in lines:
        parts = line.strip().split(":")
        if len(parts) == 4:
            proxies.append((3, parts[0], int(parts[1]), True, parts[2], parts[3]))
    return proxies


async def check_account(phone: str, data: dict, proxy: tuple, proxy_idx: int):
    """Проверяет один аккаунт."""
    name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()

    client = TelegramClient(
        str(BANNED_DIR / phone),
        api_id=data.get("app_id") or settings.TELEGRAM_API_ID,
        api_hash=data.get("app_hash") or settings.TELEGRAM_API_HASH,
        proxy=proxy,
        device_model=data.get("device", "Samsung Galaxy S23"),
        system_version=data.get("sdk", "SDK 29"),
        app_version=data.get("app_version", "12.4.3"),
        lang_code=data.get("lang_pack", "ru"),
        system_lang_code=data.get("system_lang_pack", "ru-ru"),
        timeout=30,
        connection_retries=3,
        retry_delay=5,
    )

    result = {"phone": phone, "name": name, "status": "unknown", "detail": ""}

    try:
        await client.connect()

        if not await client.is_user_authorized():
            result["status"] = "not_authorized"
            result["detail"] = "Сессия невалидна"
            await client.disconnect()
            return result

        me = await client.get_me()
        current_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        result["name"] = current_name

        # Пробуем write-операцию с тем же именем
        try:
            await client(UpdateProfileRequest(
                first_name=me.first_name or "",
                last_name=me.last_name or "",
            ))
            result["status"] = "active"
            result["detail"] = "Аккаунт рабочий!"
        except Exception as e:
            err = str(e).upper()
            if "FROZEN" in err:
                result["status"] = "frozen"
                result["detail"] = "Заморожен — можно подать апелляцию!"
            else:
                result["status"] = "error_write"
                result["detail"] = str(e)[:80]

        await client.disconnect()

    except (UserDeactivatedBanError, UserDeactivatedError):
        result["status"] = "banned"
        result["detail"] = "Аккаунт удалён/забанен навсегда"
    except AuthKeyUnregisteredError:
        result["status"] = "auth_key_invalid"
        result["detail"] = "Сессия уничтожена (AuthKey)"
    except PhoneNumberBannedError:
        result["status"] = "phone_banned"
        result["detail"] = "Номер забанен"
    except Exception as e:
        err_str = str(e)
        if "deactivated" in err_str.lower() or "banned" in err_str.lower():
            result["status"] = "banned"
            result["detail"] = err_str[:80]
        elif "504" in err_str or "timeout" in err_str.lower():
            result["status"] = "proxy_error"
            result["detail"] = "Прокси не работает"
        else:
            result["status"] = "error"
            result["detail"] = err_str[:80]

        try:
            await client.disconnect()
        except Exception:
            pass

    return result


async def main():
    # Загрузить аккаунты
    session_files = sorted(BANNED_DIR.glob("*.session"))
    phones = [f.stem for f in session_files if f.stem.isdigit()]

    if not phones:
        print("  Нет .session файлов в _banned/")
        return

    proxies = load_proxies()
    print(f"\n  === Проверка {len(phones)} забаненных аккаунтов ===")
    print(f"  Доступно прокси: {len(proxies)}")
    print(f"  Каждый аккаунт через свой прокси\n")

    # Проверяем последовательно (чтобы не перегрузить)
    results = []
    for i, phone in enumerate(phones):
        json_path = BANNED_DIR / f"{phone}.json"
        data = json.loads(json_path.read_text()) if json_path.exists() else {}

        # Берём прокси с отступом (первые 2 заняты нашими аккаунтами)
        proxy_idx = i + 2
        if proxy_idx >= len(proxies):
            proxy_idx = i % len(proxies)
        proxy = proxies[proxy_idx]

        print(f"  [{i+1}/{len(phones)}] +{phone} (прокси #{proxy_idx+1})...", end=" ", flush=True)
        result = await check_account(phone, data, proxy, proxy_idx)
        results.append(result)

        icon = {
            "active": "✅",
            "frozen": "❄️ ",
            "banned": "💀",
            "not_authorized": "🔑",
            "auth_key_invalid": "🔑",
            "phone_banned": "💀",
            "proxy_error": "🌐",
            "error": "⚠️",
            "error_write": "⚠️",
        }.get(result["status"], "❓")

        print(f"{icon} {result['status']} — {result['detail']}")

        # Пауза между аккаунтами (не спамим)
        if i < len(phones) - 1:
            await asyncio.sleep(3)

    # Итог
    print(f"\n  === Итог ===")
    frozen = [r for r in results if r["status"] == "frozen"]
    active = [r for r in results if r["status"] == "active"]
    banned = [r for r in results if r["status"] in ("banned", "phone_banned")]
    invalid = [r for r in results if r["status"] in ("not_authorized", "auth_key_invalid")]
    errors = [r for r in results if r["status"] in ("error", "error_write", "proxy_error")]

    if active:
        print(f"  ✅ Рабочих: {len(active)}")
        for r in active:
            print(f"     +{r['phone']} ({r['name']})")

    if frozen:
        print(f"  ❄️  Замороженных (можно апелляцию): {len(frozen)}")
        for r in frozen:
            print(f"     +{r['phone']} ({r['name']})")

    if banned:
        print(f"  💀 Забаненных навсегда: {len(banned)}")
        for r in banned:
            print(f"     +{r['phone']} ({r['name']})")

    if invalid:
        print(f"  🔑 Невалидная сессия: {len(invalid)}")
        for r in invalid:
            print(f"     +{r['phone']} ({r['name']})")

    if errors:
        print(f"  ⚠️  Ошибки: {len(errors)}")
        for r in errors:
            print(f"     +{r['phone']} — {r['detail']}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
