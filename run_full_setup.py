"""
ПОЛНЫЙ SETUP: восстановление сессий, регистрация в БД, подключение, упаковка, каналы.

Этот скрипт:
1. Восстанавливает оригинальные .session файлы из папки продавца
2. Регистрирует все аккаунты в БД (если ещё не зарегистрированы)
3. Подключает все аккаунты через HTTP прокси
4. Упаковывает профили (женские имена, аватарки, username)
5. Создаёт каналы-переходники с рекламой DartVPN

Запуск: python run_full_setup.py
"""

import asyncio
import json
import random
import shutil
import sys
from pathlib import Path

from config import settings, BASE_DIR
from storage.sqlite_db import init_db, dispose_engine, async_session
from storage.models import Account
from core.session_manager import SessionManager
from core.proxy_manager import ProxyManager
from core.rate_limiter import RateLimiter
from core.account_manager import AccountManager
from utils.account_packager import AccountPackager
from utils.channel_setup import ChannelSetup
from utils.helpers import utcnow
from utils.logger import log
from sqlalchemy import select


# ─── КОНФИГУРАЦИЯ ───────────────────────────────────────────────────

# Папка с оригинальными сессиями от продавца
SELLER_FOLDER = settings.sessions_path / "3306544857"

# HTTP прокси (прямой tuple для Telethon, без ProxyManager)
PROXY_LINE = None  # Будет заполнен из proxies.txt


def load_proxy_tuple():
    """Загрузить прокси из файла и вернуть tuple для Telethon."""
    proxy_path = settings.proxy_list_path
    if not proxy_path.exists():
        print("  ⚠️  Файл прокси не найден — подключение без прокси")
        return None

    line = proxy_path.read_text(encoding="utf-8").strip().split("\n")[0].strip()
    if not line or line.startswith("#"):
        print("  ⚠️  Файл прокси пуст")
        return None

    # Формат: host:port:user:pass
    parts = line.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        # HTTP прокси для Telethon: (тип=3, хост, порт, rdns, юзер, пароль)
        proxy_tuple = (3, host, int(port), True, user, password)
        print(f"  Прокси: HTTP {host}:{port} (user: {user[:12]}...)")
        return proxy_tuple
    else:
        print(f"  ⚠️  Не удалось распарсить прокси: {line[:40]}...")
        return None


# ─── ШАГ 1: Восстановить оригинальные сессии ───────────────────────

def restore_original_sessions() -> list[str]:
    """Скопировать оригинальные .session и .json из папки продавца."""
    if not SELLER_FOLDER.exists():
        print("  ℹ️  Папка продавца не найдена, используем текущие сессии")
        # Собираем из текущей папки
        phones = []
        for f in sorted(settings.sessions_path.glob("*.session")):
            if f.stem.isdigit():
                phones.append(f.stem)
        return phones

    phones = []
    session_files = sorted(SELLER_FOLDER.glob("*.session"))

    for src_session in session_files:
        phone = src_session.stem
        if not phone.isdigit():
            continue

        dst_session = settings.sessions_path / src_session.name
        src_json = SELLER_FOLDER / f"{phone}.json"
        dst_json = settings.sessions_path / f"{phone}.json"

        # Восстановить .session (оригинал от продавца)
        shutil.copy2(src_session, dst_session)

        # Восстановить .json (если есть)
        if src_json.exists():
            shutil.copy2(src_json, dst_json)

        phones.append(phone)

    return phones


# ─── ШАГ 2: Зарегистрировать аккаунты в БД ─────────────────────────

async def register_accounts_in_db(phones: list[str]) -> int:
    """Зарегистрировать аккаунты в SQLite, если ещё не зарегистрированы."""
    registered = 0
    async with async_session() as session:
        for phone in phones:
            phone_formatted = f"+{phone}"
            # Проверить, есть ли уже
            result = await session.execute(
                select(Account).where(Account.phone == phone_formatted)
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Всегда сбрасываем статус на active (свежие сессии)
                if existing.status != "active":
                    existing.status = "active"
                    existing.comments_today = 0
                    existing.days_active = 0
                    await session.commit()
                    print(f"    ♻️  +{phone} — сброшен на active")
                    registered += 1
                else:
                    print(f"    ✓  +{phone} — уже в БД (active)")
            else:
                session_file = f"{phone}.session"
                account = Account(
                    phone=phone_formatted,
                    session_file=session_file,
                    status="active",
                    created_at=utcnow(),
                )
                session.add(account)
                await session.commit()
                print(f"    ✅ +{phone} — зарегистрирован в БД")
                registered += 1

    return registered


# ─── ШАГ 3: Подключить аккаунты через прокси ───────────────────────

async def connect_accounts(phones: list[str], proxy_tuple) -> list[str]:
    """Подключить аккаунты напрямую через Telethon с HTTP прокси."""
    from telethon import TelegramClient

    connected = []

    for i, phone in enumerate(phones):
        # Загрузить device params из JSON
        json_path = settings.sessions_path / f"{phone}.json"
        app_id = settings.TELEGRAM_API_ID
        app_hash = settings.TELEGRAM_API_HASH
        device = "Samsung Galaxy S23"
        sdk = "Android 14"
        app_version = "10.8.3"
        lang_pack = "ru"
        system_lang_pack = "ru"

        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                app_id = data.get("app_id") or app_id
                app_hash = data.get("app_hash") or app_hash
                device = data.get("device", device)
                sdk = data.get("sdk", sdk)
                app_version = data.get("app_version", app_version)
                lang_pack = data.get("lang_pack", lang_pack)
                system_lang_pack = data.get("system_lang_pack", system_lang_pack)
            except Exception:
                pass

        session_path = str(settings.sessions_path / phone)

        client = TelegramClient(
            session_path,
            api_id=app_id,
            api_hash=app_hash,
            proxy=proxy_tuple,
            device_model=device,
            system_version=sdk,
            app_version=app_version,
            lang_code=lang_pack,
            system_lang_code=system_lang_pack,
        )

        try:
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                name = f"{me.first_name or ''} {me.last_name or ''}".strip()
                uname = f"@{me.username}" if me.username else "no username"
                print(f"    ✅ +{phone} — {name} ({uname})")
                connected.append(phone)
            else:
                print(f"    ❌ +{phone} — сессия невалидна (нужна реавторизация)")
            await client.disconnect()
        except Exception as e:
            print(f"    ❌ +{phone} — ошибка: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass

        # Задержка между подключениями
        if i < len(phones) - 1:
            await asyncio.sleep(2)

    return connected


# ─── ШАГ 4+5: Упаковка и каналы через AccountManager ───────────────

async def run_packaging_and_channels(connected_phones: list[str], proxy_tuple):
    """Упаковать профили и создать каналы."""
    proxy_mgr = ProxyManager()
    proxy_mgr.load_from_file()
    session_mgr = SessionManager()
    rate_limiter = RateLimiter()
    account_mgr = AccountManager(session_mgr, proxy_mgr, rate_limiter)

    # Подключить через AccountManager
    print("\n  Подключаю через AccountManager...")
    results = await account_mgr.connect_all()
    connected = [p for p, s in results.items() if s == "connected"]
    failed = [p for p, s in results.items() if s != "connected"]
    print(f"  Подключено: {len(connected)}/{len(results)}")
    for phone in failed:
        print(f"    ❌ {phone} — {results[phone]}")

    if not connected:
        print("\n  ❌ Нет подключённых аккаунтов для упаковки.")
        await session_mgr.disconnect_all()
        return

    # Упаковка профилей
    print(f"\n  [4] Упаковка профилей ({len(connected)} аккаунтов)...")
    packager = AccountPackager(session_mgr)

    all_styles = [
        "beauty", "casual", "student", "tech", "lifestyle",
        "blogger", "fitness", "business", "creative", "friendly",
    ]
    applied = 0
    usernames_set = 0
    avatars_set = 0

    for i, phone in enumerate(connected):
        # Проверить подключение
        client = session_mgr.get_client(phone)
        if not client or not client.is_connected():
            print(f"    ⚡ {phone} — переподключаю...")
            proxy = proxy_mgr.assign_to_account(phone)
            client = await session_mgr.connect_client(phone, proxy)
            if not client:
                print(f"    ❌ {phone} — не удалось переподключить")
                continue

        style = all_styles[i % len(all_styles)]
        avatar_idx = i % 25
        print(f"    [{i+1}/{len(connected)}] {phone} (стиль: {style})...")

        r = await packager.package_account(phone, style, avatar_idx)
        profile = r["profile"]
        name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
        username_str = f" (@{r['username']})" if r.get("username") else ""
        avatar_str = " 📷" if r.get("avatar_applied") else ""
        status = "✅" if r["applied"] else "❌"

        print(f"    {status} {name}{username_str}{avatar_str}")

        if r["applied"]:
            applied += 1
        if r.get("username_applied"):
            usernames_set += 1
        if r.get("avatar_applied"):
            avatars_set += 1

        # Антибан-задержка
        if i < len(connected) - 1:
            delay = random.uniform(15.0, 30.0)
            print(f"       ⏳ Пауза {delay:.0f}с...")
            await asyncio.sleep(delay)

    print(f"\n  Профили: {applied}/{len(connected)}")
    print(f"  Username: {usernames_set}/{len(connected)}")
    print(f"  Аватарки: {avatars_set}/{len(connected)}")

    # Переподключение перед каналами
    print("\n  Переподключаю для каналов...")
    results = await account_mgr.connect_all()
    connected = [p for p, s in results.items() if s == "connected"]

    if connected:
        print(f"\n  [5] Создание каналов-переходников ({len(connected)})...")
        ch_setup = ChannelSetup(session_mgr, account_mgr)
        ch_results = await ch_setup.setup_all_accounts()

        for r in ch_results["results"]:
            if r["success"]:
                print(f"    ✅ {r['phone']} — «{r['channel_title']}» — {r['channel_link']}")
            else:
                print(f"    ❌ {r['phone']} — {r.get('error', '?')}")

        print(f"\n  Каналы: {ch_results['success']}/{ch_results['total']}")
    else:
        print("  ❌ Нет подключённых аккаунтов для каналов.")

    # Итоги
    print("\n  ═══════════════════════════════════")
    print("  📊 ИТОГОВАЯ СВОДКА:")
    print(f"     Профили:  {applied}")
    print(f"     Username: {usernames_set}")
    print(f"     Аватарки: {avatars_set}")
    print("  ═══════════════════════════════════")

    await session_mgr.disconnect_all()


# ─── MAIN ───────────────────────────────────────────────────────────

async def main():
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║   NEURO COMMENTING — ПОЛНЫЙ SETUP           ║")
    print("  ╚══════════════════════════════════════════════╝")
    print()

    # 0. Загрузить прокси
    proxy_tuple = load_proxy_tuple()

    # 1. Восстановить оригинальные сессии
    print("  [1/5] Восстанавливаю оригинальные сессии...")
    phones = restore_original_sessions()
    print(f"  Найдено {len(phones)} аккаунтов: {', '.join(phones)}")

    # 2. Инициализация БД и регистрация
    print("\n  [2/5] Регистрация аккаунтов в БД...")
    await init_db()
    registered = await register_accounts_in_db(phones)
    print(f"  Зарегистрировано/обновлено: {registered}")

    # 3. Подключение через прокси — проверка авторизации
    print(f"\n  [3/5] Подключение {len(phones)} аккаунтов через прокси...")
    connected = await connect_accounts(phones, proxy_tuple)
    print(f"\n  Авторизовано: {len(connected)}/{len(phones)}")

    if not connected:
        print("\n  ❌ Ни один аккаунт не авторизован.")
        print("  Сессии были отозваны Telegram.")
        print("  Для реавторизации нужны SMS-коды:")
        print("    python reauth_sessions.py send all")
        print("    python reauth_sessions.py code <phone> <code>")
        await dispose_engine()
        return

    # 4-5. Упаковка и каналы
    try:
        await run_packaging_and_channels(connected, proxy_tuple)
    finally:
        await dispose_engine()

    print("\n  ✅ SETUP ЗАВЕРШЁН!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n  Прервано (Ctrl+C)")
        sys.exit(0)
