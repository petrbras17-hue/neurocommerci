"""
Реавторизация Telegram-сессий.
Интерактивный скрипт: запрашивает SMS-код для каждого аккаунта.

Запуск: python reauth_sessions.py
"""

import asyncio
import json
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from config import settings, BASE_DIR
from core.proxy_manager import ProxyManager, ProxyConfig
from utils.logger import log


proxy_mgr = ProxyManager()


def get_session_phones() -> list[str]:
    """Получить список телефонов из .session файлов."""
    sessions_dir = settings.sessions_path
    phones = []
    for f in sorted(sessions_dir.glob("*.session")):
        phone = f.stem
        if phone.isdigit():
            phones.append(phone)
    return phones


def load_device_params(phone: str) -> dict:
    """Загрузить параметры устройства из JSON."""
    json_path = settings.sessions_path / f"{phone}.json"
    defaults = {
        "device": "Samsung Galaxy S23",
        "sdk": "Android 14",
        "app_version": "10.8.3",
        "app_id": settings.TELEGRAM_API_ID,
        "app_hash": settings.TELEGRAM_API_HASH,
    }
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return {
                "app_id": data.get("app_id") or defaults["app_id"],
                "app_hash": data.get("app_hash") or defaults["app_hash"],
                "device": data.get("device", defaults["device"]),
                "sdk": data.get("sdk", defaults["sdk"]),
                "app_version": data.get("app_version", defaults["app_version"]),
                "twoFA": data.get("twoFA"),
            }
        except Exception:
            pass
    return defaults


def create_client(phone: str, proxy: ProxyConfig = None) -> TelegramClient:
    """Создать Telethon-клиент."""
    session_path = str(settings.sessions_path / phone)
    params = load_device_params(phone)
    proxy_tuple = proxy.to_telethon_proxy() if proxy else None

    return TelegramClient(
        session_path,
        api_id=params["app_id"],
        api_hash=params["app_hash"],
        proxy=proxy_tuple,
        device_model=params.get("device", "Samsung Galaxy S23"),
        system_version=params.get("sdk", "Android 14"),
        app_version=params.get("app_version", "10.8.3"),
    )


async def reauth_phone(phone: str) -> bool:
    """Реавторизовать один аккаунт."""
    proxy = proxy_mgr.assign_to_account(phone)
    params = load_device_params(phone)
    client = create_client(phone, proxy)

    try:
        await client.connect()

        # Проверить текущую авторизацию
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"    ✅ {phone} — уже авторизован ({me.first_name})")
            await client.disconnect()
            return True

        print(f"    📱 {phone} — отправляю запрос SMS...")

        # Отправить код
        phone_formatted = f"+{phone}"
        try:
            sent = await client.send_code_request(phone_formatted)
        except FloodWaitError as e:
            print(f"    ❌ FloodWait: подождите {e.seconds}с")
            await client.disconnect()
            return False

        # Запросить код у пользователя
        code = input(f"    ✏️  Введите код из SMS для +{phone}: ").strip()
        if not code:
            print("    ⏩ Пропускаю")
            await client.disconnect()
            return False

        try:
            await client.sign_in(phone_formatted, code, phone_code_hash=sent.phone_code_hash)
        except PhoneCodeInvalidError:
            print("    ❌ Неверный код")
            await client.disconnect()
            return False
        except PhoneCodeExpiredError:
            print("    ❌ Код истёк, попробуйте заново")
            await client.disconnect()
            return False
        except SessionPasswordNeededError:
            # 2FA пароль
            twofa = params.get("twoFA") or ""
            if twofa:
                print(f"    🔐 2FA пароль найден в JSON, пробую...")
            else:
                twofa = input("    🔐 Введите 2FA пароль: ").strip()
            try:
                await client.sign_in(password=twofa)
            except Exception as e:
                print(f"    ❌ Ошибка 2FA: {e}")
                await client.disconnect()
                return False

        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"    ✅ {phone} — авторизован! ({me.first_name})")
            await client.disconnect()
            return True
        else:
            print(f"    ❌ {phone} — не удалось авторизовать")
            await client.disconnect()
            return False

    except Exception as exc:
        print(f"    ❌ {phone} — ошибка: {exc}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return False


async def main():
    print()
    print("  === NEURO COMMENTING — Реавторизация сессий ===")
    print()

    phones = get_session_phones()
    if not phones:
        print("  ❌ Нет .session файлов в data/sessions/")
        return

    print(f"  Найдено {len(phones)} аккаунтов:")
    for p in phones:
        print(f"    • +{p}")

    print()
    choice = input("  Реавторизовать все? (y/n/номер телефона): ").strip().lower()

    if choice == "n":
        print("  Отмена.")
        return

    if choice.isdigit() and len(choice) > 5:
        # Один конкретный номер
        target = choice.lstrip("+")
        if target in phones:
            phones = [target]
        else:
            print(f"  ❌ Телефон {target} не найден")
            return

    print()
    success = 0
    for phone in phones:
        ok = await reauth_phone(phone)
        if ok:
            success += 1

    print()
    print(f"  Итого: {success}/{len(phones)} авторизовано")
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n  Прервано (Ctrl+C)")
        sys.exit(0)
