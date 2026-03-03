"""
Реавторизация Telegram-сессий.

Режимы:
    python reauth_sessions.py                     — интерактивный (SMS-коды через input)
    python reauth_sessions.py send <phone|all>    — отправить SMS-код (без ввода)
    python reauth_sessions.py code <phone> <code> — ввести полученный код
    python reauth_sessions.py status              — проверить статус всех аккаунтов
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

# API ID для реавторизации (2040 = Telegram Desktop, не заблокирован)
# API ID 4 (Android) заблокирован Telegram для SendCodeRequest
REAUTH_API_ID = 2040
REAUTH_API_HASH = "b18441a1ff607e10a989891a5462e627"

proxy_mgr = ProxyManager()

# Файл для хранения phone_code_hash между вызовами send и code
HASHES_FILE = BASE_DIR / "data" / ".reauth_hashes.json"


def _load_hashes() -> dict:
    if HASHES_FILE.exists():
        try:
            return json.loads(HASHES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_hashes(hashes: dict):
    HASHES_FILE.parent.mkdir(parents=True, exist_ok=True)
    HASHES_FILE.write_text(json.dumps(hashes), encoding="utf-8")


def _init_proxy():
    """Загрузить прокси из файла."""
    count = proxy_mgr.load_from_file()
    if count:
        mode = "rotating" if proxy_mgr.is_rotating else "static"
        print(f"  Прокси: {count} шт. (режим: {mode})")
    else:
        print("  ⚠️  Прокси не найдены — подключение напрямую")


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
    """Создать Telethon-клиент с REAUTH API ID (2040, не заблокирован)."""
    session_path = str(settings.sessions_path / phone)
    params = load_device_params(phone)
    proxy_tuple = proxy.to_telethon_proxy() if proxy else None

    return TelegramClient(
        session_path,
        api_id=REAUTH_API_ID,
        api_hash=REAUTH_API_HASH,
        proxy=proxy_tuple,
        device_model=params.get("device", "Samsung Galaxy S23"),
        system_version=params.get("sdk", "Android 14"),
        app_version=params.get("app_version", "10.8.3"),
    )


# ─── Команда: status ───────────────────────────────────────────────

async def cmd_status():
    """Проверить статус авторизации всех аккаунтов."""
    _init_proxy()
    phones = get_session_phones()
    if not phones:
        print("  ❌ Нет .session файлов")
        return

    print(f"\n  Проверяю {len(phones)} аккаунтов...\n")
    authorized = 0
    for phone in phones:
        proxy = proxy_mgr.assign_to_account(phone)
        client = create_client(phone, proxy)
        try:
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                name = f"{me.first_name or ''} {me.last_name or ''}".strip()
                uname = f"@{me.username}" if me.username else "no username"
                print(f"    ✅ +{phone} — {name} ({uname})")
                authorized += 1
            else:
                print(f"    ❌ +{phone} — НЕ авторизован")
            await client.disconnect()
        except Exception as e:
            print(f"    ❌ +{phone} — ошибка: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass

    print(f"\n  Итого: {authorized}/{len(phones)} авторизовано\n")


# ─── Команда: send ─────────────────────────────────────────────────

async def cmd_send(target: str):
    """Отправить запрос SMS-кода для аккаунта(ов)."""
    _init_proxy()
    phones = get_session_phones()

    if target == "all":
        targets = phones
    else:
        target = target.lstrip("+")
        if target not in phones:
            print(f"  ❌ Телефон {target} не найден в сессиях")
            return
        targets = [target]

    hashes = _load_hashes()
    print(f"\n  Отправляю SMS-запросы для {len(targets)} аккаунтов...\n")

    for phone in targets:
        proxy = proxy_mgr.assign_to_account(phone)
        client = create_client(phone, proxy)
        try:
            await client.connect()

            if await client.is_user_authorized():
                me = await client.get_me()
                print(f"    ✅ +{phone} — уже авторизован ({me.first_name})")
                await client.disconnect()
                continue

            phone_formatted = f"+{phone}"
            try:
                sent = await client.send_code_request(phone_formatted)
                hashes[phone] = sent.phone_code_hash
                _save_hashes(hashes)
                print(f"    📱 +{phone} — SMS отправлен! Жду код.")
            except FloodWaitError as e:
                print(f"    ❌ +{phone} — FloodWait: подождите {e.seconds}с")
            except Exception as e:
                print(f"    ❌ +{phone} — ошибка отправки: {e}")

            await client.disconnect()
        except Exception as e:
            print(f"    ❌ +{phone} — ошибка: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass

        # Антибан задержка между аккаунтами
        if phone != targets[-1]:
            await asyncio.sleep(3)

    print()
    print("  Теперь введите коды:")
    print("  python reauth_sessions.py code <phone> <code>")
    print()


# ─── Команда: code ─────────────────────────────────────────────────

async def cmd_code(phone: str, code: str):
    """Ввести SMS-код для аккаунта."""
    _init_proxy()
    phone = phone.lstrip("+")

    hashes = _load_hashes()
    phone_code_hash = hashes.get(phone)

    params = load_device_params(phone)
    proxy = proxy_mgr.assign_to_account(phone)
    client = create_client(phone, proxy)

    try:
        await client.connect()

        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"    ✅ +{phone} — уже авторизован ({me.first_name})")
            await client.disconnect()
            return True

        phone_formatted = f"+{phone}"

        # Если нет сохранённого hash — отправим код заново
        if not phone_code_hash:
            print(f"    📱 Нет сохранённого hash, отправляю SMS заново...")
            try:
                sent = await client.send_code_request(phone_formatted)
                phone_code_hash = sent.phone_code_hash
                hashes[phone] = phone_code_hash
                _save_hashes(hashes)
                print(f"    📱 SMS отправлен повторно, используйте НОВЫЙ код")
                await client.disconnect()
                return False
            except FloodWaitError as e:
                print(f"    ❌ FloodWait: подождите {e.seconds}с")
                await client.disconnect()
                return False

        # Ввести код
        try:
            await client.sign_in(phone_formatted, code, phone_code_hash=phone_code_hash)
        except PhoneCodeInvalidError:
            print(f"    ❌ +{phone} — неверный код")
            await client.disconnect()
            return False
        except PhoneCodeExpiredError:
            print(f"    ❌ +{phone} — код истёк, отправьте заново: python reauth_sessions.py send {phone}")
            # Удалить старый hash
            hashes.pop(phone, None)
            _save_hashes(hashes)
            await client.disconnect()
            return False
        except SessionPasswordNeededError:
            # 2FA пароль из JSON
            twofa = params.get("twoFA") or ""
            if twofa:
                print(f"    🔐 2FA пароль из JSON, применяю...")
                try:
                    await client.sign_in(password=twofa)
                except Exception as e:
                    print(f"    ❌ Ошибка 2FA: {e}")
                    await client.disconnect()
                    return False
            else:
                print(f"    ❌ Требуется 2FA пароль, но его нет в JSON!")
                await client.disconnect()
                return False

        if await client.is_user_authorized():
            me = await client.get_me()
            name = f"{me.first_name or ''} {me.last_name or ''}".strip()
            print(f"    ✅ +{phone} — АВТОРИЗОВАН! ({name})")
            # Убрать hash после успеха
            hashes.pop(phone, None)
            _save_hashes(hashes)
            await client.disconnect()
            return True
        else:
            print(f"    ❌ +{phone} — не удалось авторизовать")
            await client.disconnect()
            return False

    except Exception as exc:
        print(f"    ❌ +{phone} — ошибка: {exc}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return False


# ─── Интерактивный режим (оригинальный) ─────────────────────────────

async def reauth_phone(phone: str) -> bool:
    """Реавторизовать один аккаунт (интерактивно)."""
    proxy = proxy_mgr.assign_to_account(phone)
    params = load_device_params(phone)
    client = create_client(phone, proxy)

    try:
        await client.connect()

        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"    ✅ {phone} — уже авторизован ({me.first_name})")
            await client.disconnect()
            return True

        print(f"    📱 {phone} — отправляю запрос SMS...")

        phone_formatted = f"+{phone}"
        try:
            sent = await client.send_code_request(phone_formatted)
        except FloodWaitError as e:
            print(f"    ❌ FloodWait: подождите {e.seconds}с")
            await client.disconnect()
            return False

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


async def main_interactive():
    """Интерактивный режим с input()."""
    _init_proxy()
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


# ─── Точка входа ────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args:
        # Интерактивный режим
        asyncio.run(main_interactive())

    elif args[0] == "status":
        asyncio.run(cmd_status())

    elif args[0] == "send":
        target = args[1] if len(args) > 1 else "all"
        asyncio.run(cmd_send(target))

    elif args[0] == "code":
        if len(args) < 3:
            print("  Использование: python reauth_sessions.py code <phone> <code>")
            print("  Пример: python reauth_sessions.py code 79327259395 12345")
            sys.exit(1)
        phone = args[1]
        code = args[2]
        asyncio.run(cmd_code(phone, code))

    else:
        print("  Использование:")
        print("    python reauth_sessions.py                     — интерактивный режим")
        print("    python reauth_sessions.py status              — статус аккаунтов")
        print("    python reauth_sessions.py send <phone|all>    — отправить SMS")
        print("    python reauth_sessions.py code <phone> <code> — ввести код")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Прервано (Ctrl+C)")
        sys.exit(0)
