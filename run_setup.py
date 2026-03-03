"""
Прямой запуск упаковки профилей + создания каналов-переходников.
Без Telegram-бота — всё через CLI.

Запуск: python run_setup.py
"""

import asyncio
import sys

from config import settings
from storage.sqlite_db import init_db, dispose_engine
from core.session_manager import SessionManager
from core.proxy_manager import ProxyManager
from core.rate_limiter import RateLimiter
from core.account_manager import AccountManager
from utils.account_packager import AccountPackager
from utils.channel_setup import ChannelSetup
from utils.logger import log


async def reconnect_all(account_mgr: AccountManager) -> list[str]:
    """Переподключить все аккаунты, вернуть список подключённых."""
    results = await account_mgr.connect_all()
    connected = [p for p, s in results.items() if s == "connected"]
    failed = [p for p, s in results.items() if s != "connected"]
    print(f"  Подключено: {len(connected)}/{len(results)}")
    for phone in failed:
        print(f"    ❌ {phone} — {results[phone]}")
    return connected


async def main():
    print()
    print("  === NEURO COMMENTING — Прямой запуск упаковки ===")
    print()

    # 1. Инициализация БД
    log.info("Инициализация БД...")
    await init_db()

    # 2. Создание менеджеров
    proxy_mgr = ProxyManager()
    session_mgr = SessionManager()
    rate_limiter = RateLimiter()
    account_mgr = AccountManager(session_mgr, proxy_mgr, rate_limiter)

    try:
        # 3. Подключение всех аккаунтов
        print("  [1/5] Подключаю аккаунты...")
        connected = await reconnect_all(account_mgr)

        if not connected:
            print("\n  ❌ Нет подключённых аккаунтов. Выход.")
            return

        # 4. Упаковка профилей — по одному с переподключением
        print(f"\n  [2/5] Упаковка профилей ({len(connected)} аккаунтов)...")
        print("  Генерация AI-профилей, аватарок, проверка username...")
        print("  Задержки 15-30с между аккаунтами (антибан).\n")

        packager = AccountPackager(session_mgr)

        all_styles = [
            "beauty", "casual", "student", "tech", "lifestyle",
            "blogger", "fitness", "business", "creative", "friendly",
        ]
        pack_results = []
        applied = 0
        usernames_set = 0
        avatars_set = 0

        for i, phone in enumerate(connected):
            # Проверить подключение, переподключить если нужно
            client = session_mgr.get_client(phone)
            if not client or not client.is_connected():
                print(f"    ⚡ {phone} — переподключаю...")
                proxy = proxy_mgr.assign_to_account(phone)
                client = await session_mgr.connect_client(phone, proxy)
                if not client:
                    print(f"    ❌ {phone} — не удалось переподключить")
                    pack_results.append({
                        "phone": phone, "profile": {}, "applied": False,
                        "username": None, "username_applied": False, "avatar_applied": False,
                    })
                    continue

            style = all_styles[i % len(all_styles)]
            avatar_idx = i % 25

            print(f"    [{i+1}/{len(connected)}] {phone} (стиль: {style})...")

            r = await packager.package_account(phone, style, avatar_idx)
            pack_results.append(r)

            profile = r["profile"]
            name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
            username_str = f" (@{r['username']})" if r.get("username") else ""
            avatar_str = " 📷" if r.get("avatar_applied") else ""
            status = "✅" if r["applied"] else "❌"

            print(f"    {status} {name}{username_str}{avatar_str}")
            if profile.get("bio"):
                print(f"       {profile['bio'][:60]}")

            if r["applied"]:
                applied += 1
            if r.get("username_applied"):
                usernames_set += 1
            if r.get("avatar_applied"):
                avatars_set += 1

            # Антибан-задержка
            if i < len(connected) - 1:
                import random
                delay = random.uniform(15.0, 30.0)
                print(f"       ⏳ Пауза {delay:.0f}с...")
                await asyncio.sleep(delay)

        print(f"\n  Итого профили: {applied}/{len(pack_results)}")
        print(f"  Username: {usernames_set}/{len(pack_results)}")
        print(f"  Аватарки: {avatars_set}/{len(pack_results)}")

        # 5. Переподключение перед созданием каналов
        print("\n  [3/5] Переподключаю аккаунты для создания каналов...")
        connected = await reconnect_all(account_mgr)

        if not connected:
            print("  ❌ Нет подключённых аккаунтов для каналов.")
        else:
            # 6. Создание каналов-переходников
            print(f"\n  [4/5] Создание каналов-переходников ({len(connected)} акк.)...")
            print("  Канал + DartVPN аватарка + пост + закреп + bio\n")

            ch_setup = ChannelSetup(session_mgr, account_mgr)
            ch_results = await ch_setup.setup_all_accounts()

            for r in ch_results["results"]:
                if r["success"]:
                    print(f"    ✅ {r['phone']}")
                    print(f"       «{r['channel_title']}» — {r['channel_link']}")
                else:
                    print(f"    ❌ {r['phone']} — {r.get('error', '?')}")

            print(f"\n  Каналы: {ch_results['success']}/{ch_results['total']}")

        # 7. Финальная сводка
        print("\n  ═══════════════════════════════════")
        print("  📊 ИТОГОВАЯ СВОДКА:")
        print(f"     Профили установлены:  {applied}/{len(pack_results)}")
        print(f"     Username установлены: {usernames_set}/{len(pack_results)}")
        print(f"     Аватарки установлены: {avatars_set}/{len(pack_results)}")
        if connected:
            ch_ok = ch_results['success'] if 'ch_results' in dir() else 0
        else:
            ch_ok = 0
        print(f"     Каналы созданы:       {ch_ok}")
        print("  ═══════════════════════════════════")
        print("\n  [5/5] Готово!")

    finally:
        print("\n  Отключаю аккаунты...")
        await session_mgr.disconnect_all()
        await dispose_engine()
        print("  Завершено.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n  Прервано (Ctrl+C)")
        sys.exit(0)
