"""
Account Security Manager — protect bought Telegram accounts.

Usage:
    python scripts/account_security.py sessions      # Show all sessions for all accounts
    python scripts/account_security.py terminate      # Terminate ALL foreign sessions (keep ours)
    python scripts/account_security.py set-2fa        # Set 2FA on all accounts
    python scripts/account_security.py privacy        # Configure privacy settings
    python scripts/account_security.py full-secure    # Full cycle: backup → terminate → 2FA → privacy
    python scripts/account_security.py status         # Security status report

CRITICAL RULES:
    - NEVER call send_code_request on bought accounts
    - ALWAYS backup sessions before any security operation
    - Store 2FA passwords securely in JSON configs
    - Wait 30-60s between operations on different accounts
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon import TelegramClient
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
    GetPrivacyRequest,
    SetPrivacyRequest,
)
from telethon.tl.types import (
    InputPrivacyKeyPhoneNumber,
    InputPrivacyKeyStatusTimestamp,
    InputPrivacyKeyProfilePhoto,
    InputPrivacyKeyForwards,
    InputPrivacyValueDisallowAll,
    InputPrivacyValueAllowContacts,
)
from telethon.errors import (
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
    SessionRevokedError,
    PhoneNumberBannedError,
)

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"
BACKUPS_DIR = Path(__file__).resolve().parent.parent / "data" / "session_backups"
PROXIES_FILE = Path(__file__).resolve().parent.parent / "data" / "proxies.txt"


def load_accounts() -> list[dict]:
    """Load all account JSON configs."""
    accounts = []
    for jf in sorted(SESSIONS_DIR.glob("*.json")):
        if jf.stem.startswith("_"):
            continue
        with open(jf) as f:
            data = json.load(f)
        session_path = SESSIONS_DIR / f"{jf.stem}.session"
        if session_path.exists():
            data["_session_path"] = str(session_path)
            data["_json_path"] = str(jf)
            data["_phone"] = jf.stem
            accounts.append(data)
    return accounts


def load_proxy_for_account(index: int) -> dict | None:
    """Load a tested proxy for a given account index."""
    lines = [l.strip() for l in open(PROXIES_FILE) if l.strip()]
    if not lines:
        return None

    # Test proxies starting from evenly spaced position
    step = max(1, len(lines) // 20)
    start = (index * step * 3) % len(lines)

    for offset in range(0, len(lines), step):
        idx = (start + offset) % len(lines)
        line = lines[idx]
        parts = line.split(":")
        if len(parts) != 4:
            continue

        host, port, username, password = parts
        proxy_url = f"http://{username}:{password}@{host}:{port}"
        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "--proxy", proxy_url, "--connect-timeout", "6", "--max-time", "10",
                 "https://api.ipify.org"],
                capture_output=True, text=True, timeout=15,
            )
            if result.stdout.strip() == "200":
                return {
                    "host": host, "port": int(port),
                    "username": username, "password": password,
                }
        except Exception:
            continue

    return None


def make_client(account: dict, proxy: dict) -> TelegramClient:
    """Create a Telethon client with proper device fingerprint."""
    session_path = account["_session_path"].replace(".session", "")
    proxy_tuple = (3, proxy["host"], proxy["port"], True, proxy["username"], proxy["password"])

    return TelegramClient(
        session_path,
        account.get("app_id", 4),
        account.get("app_hash", "014b35b6184100b085b0d0572f9b5103"),
        proxy=proxy_tuple,
        device_model=account.get("device", "Samsung Galaxy S21"),
        system_version=account.get("sdk", "SDK 31"),
        app_version=account.get("app_version", "10.0.0"),
        lang_code=account.get("lang_pack", "ru"),
        system_lang_code=account.get("system_lang_pack", "ru-ru"),
        timeout=30,
        connection_retries=3,
    )


def backup_sessions():
    """Backup all session files before security operations."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS_DIR / ts
    backup_dir.mkdir()

    count = 0
    for f in SESSIONS_DIR.glob("*.session"):
        shutil.copy2(f, backup_dir / f.name)
        count += 1
    for f in SESSIONS_DIR.glob("*.json"):
        if not f.stem.startswith("_"):
            shutil.copy2(f, backup_dir / f.name)
            count += 1

    print(f"  📦 Бэкап: {count} файлов → {backup_dir}")
    return backup_dir


async def show_sessions():
    """Show all active sessions for all accounts."""
    accounts = load_accounts()
    print(f"\n{'='*70}")
    print(f"  СЕССИИ АККАУНТОВ ({len(accounts)} шт)")
    print(f"{'='*70}\n")

    for i, acc in enumerate(accounts):
        phone = acc["_phone"]
        name = f"{acc.get('first_name', '')} {acc.get('last_name', '')}".strip()
        print(f"  [{i+1}/{len(accounts)}] {phone} ({name})")
        print(f"  🔍 Ищу рабочий прокси...")

        proxy = load_proxy_for_account(i)
        if not proxy:
            print(f"  ❌ Нет рабочего прокси, пропускаю\n")
            continue

        client = make_client(acc, proxy)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                print(f"  🔒 Не авторизован\n")
                continue

            auths = await client(GetAuthorizationsRequest())
            sessions = auths.authorizations

            our_session = None
            foreign_sessions = []
            for s in sessions:
                if getattr(s, "current", False):
                    our_session = s
                else:
                    foreign_sessions.append(s)

            print(f"  📊 Всего сессий: {len(sessions)} (наша: 1, чужих: {len(foreign_sessions)})")

            if our_session:
                print(f"    🟢 НАША: {our_session.device_model} | {our_session.app_name} {our_session.app_version}")
                print(f"       IP: {our_session.ip} | Страна: {our_session.country}")

            for s in foreign_sessions:
                print(f"    🔴 ЧУЖАЯ: {s.device_model} | {s.app_name} {s.app_version}")
                print(f"       IP: {s.ip} | Страна: {s.country} | hash: {s.hash}")

            # Check 2FA status
            has_2fa = acc.get("twoFA", "") not in ("", None)
            print(f"  🔐 2FA в конфиге: {'да (' + acc.get('twoFA', '') + ')' if has_2fa else 'НЕТ'}")
            print()

        except Exception as e:
            print(f"  ❌ Ошибка: {type(e).__name__}: {str(e)[:100]}\n")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

        if i < len(accounts) - 1:
            delay = random.uniform(10, 20)
            print(f"  ⏱ Пауза {delay:.0f}с...\n")
            await asyncio.sleep(delay)


async def terminate_foreign_sessions():
    """Terminate all foreign sessions, keep only ours."""
    accounts = load_accounts()
    print(f"\n{'='*70}")
    print(f"  ЗАКРЫТИЕ ЧУЖИХ СЕССИЙ ({len(accounts)} аккаунтов)")
    print(f"{'='*70}")
    print(f"  📦 Создаю бэкап...")
    backup_sessions()
    print()

    results = []
    for i, acc in enumerate(accounts):
        phone = acc["_phone"]
        name = f"{acc.get('first_name', '')} {acc.get('last_name', '')}".strip()
        print(f"  [{i+1}/{len(accounts)}] {phone} ({name})")

        proxy = load_proxy_for_account(i)
        if not proxy:
            print(f"  ❌ Нет прокси\n")
            results.append({"phone": phone, "terminated": 0, "error": "no_proxy"})
            continue

        client = make_client(acc, proxy)
        terminated = 0
        try:
            await client.connect()
            if not await client.is_user_authorized():
                print(f"  🔒 Не авторизован\n")
                results.append({"phone": phone, "terminated": 0, "error": "not_authorized"})
                continue

            auths = await client(GetAuthorizationsRequest())
            foreign = [s for s in auths.authorizations if not getattr(s, "current", False)]

            if not foreign:
                print(f"  ✅ Чужих сессий нет")
            else:
                print(f"  🔴 Найдено {len(foreign)} чужих сессий, закрываю...")
                for s in foreign:
                    try:
                        await client(ResetAuthorizationRequest(hash=s.hash))
                        terminated += 1
                        print(f"    ✅ Закрыта: {s.device_model} | {s.ip}")
                        await asyncio.sleep(random.uniform(2, 5))
                    except Exception as e:
                        print(f"    ⚠️ Не удалось: {type(e).__name__}")

            print(f"  📊 Закрыто: {terminated}/{len(foreign)}")
            results.append({"phone": phone, "terminated": terminated})

        except Exception as e:
            print(f"  ❌ {type(e).__name__}: {str(e)[:100]}")
            results.append({"phone": phone, "terminated": terminated, "error": str(e)[:100]})
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

        if i < len(accounts) - 1:
            delay = random.uniform(30, 60)
            print(f"  ⏱ Пауза {delay:.0f}с...\n")
            await asyncio.sleep(delay)

    print(f"\n{'='*70}")
    print(f"  ИТОГО:")
    total = sum(r.get("terminated", 0) for r in results)
    print(f"  Закрыто чужих сессий: {total}")
    print(f"{'='*70}")
    return results


async def set_2fa_all():
    """Set 2FA cloud password on all accounts that don't have it."""
    accounts = load_accounts()
    print(f"\n{'='*70}")
    print(f"  УСТАНОВКА 2FA ({len(accounts)} аккаунтов)")
    print(f"{'='*70}")
    print(f"  📦 Создаю бэкап...")
    backup_sessions()
    print()

    results = []
    for i, acc in enumerate(accounts):
        phone = acc["_phone"]
        name = f"{acc.get('first_name', '')} {acc.get('last_name', '')}".strip()
        current_2fa = acc.get("twoFA", "")
        print(f"  [{i+1}/{len(accounts)}] {phone} ({name})")

        proxy = load_proxy_for_account(i)
        if not proxy:
            print(f"  ❌ Нет прокси\n")
            results.append({"phone": phone, "status": "no_proxy"})
            continue

        client = make_client(acc, proxy)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                print(f"  🔒 Не авторизован\n")
                results.append({"phone": phone, "status": "not_authorized"})
                continue

            # Generate a strong 2FA password
            import secrets
            new_password = secrets.token_hex(8)  # 16-char hex password

            try:
                # edit_2fa handles the SRP protocol internally
                if current_2fa and current_2fa not in ("", "0"):
                    # Account already has 2FA — change it
                    await client.edit_2fa(
                        current_password=current_2fa,
                        new_password=new_password,
                        hint=f"nc-{phone[-4:]}",
                    )
                    print(f"  🔐 2FA ОБНОВЛЁН: {current_2fa} → {new_password}")
                else:
                    # No 2FA — set new
                    await client.edit_2fa(
                        new_password=new_password,
                        hint=f"nc-{phone[-4:]}",
                    )
                    print(f"  🔐 2FA УСТАНОВЛЕН: {new_password}")

                # Save new 2FA to JSON config
                acc["twoFA"] = new_password
                with open(acc["_json_path"], "w") as f:
                    # Remove internal keys before saving
                    save_data = {k: v for k, v in acc.items() if not k.startswith("_")}
                    json.dump(save_data, f, indent=2, ensure_ascii=False)

                print(f"  💾 Сохранён в {acc['_json_path']}")
                results.append({"phone": phone, "status": "set", "password": new_password})

            except Exception as e:
                error_msg = str(e)
                if "PASSWORD_HASH_INVALID" in error_msg:
                    print(f"  ⚠️ Неверный текущий 2FA пароль в конфиге")
                    results.append({"phone": phone, "status": "wrong_current_password"})
                elif "FLOOD" in error_msg.upper():
                    print(f"  ⏳ FloodWait — пропускаю")
                    results.append({"phone": phone, "status": "flood_wait"})
                else:
                    print(f"  ❌ {type(e).__name__}: {error_msg[:100]}")
                    results.append({"phone": phone, "status": "error", "error": error_msg[:100]})

        except Exception as e:
            print(f"  ❌ {type(e).__name__}: {str(e)[:100]}")
            results.append({"phone": phone, "status": "error"})
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

        if i < len(accounts) - 1:
            delay = random.uniform(30, 60)
            print(f"  ⏱ Пауза {delay:.0f}с...\n")
            await asyncio.sleep(delay)

    print(f"\n{'='*70}")
    print(f"  ИТОГО 2FA:")
    for r in results:
        icon = "✅" if r["status"] == "set" else "❌"
        pwd = r.get("password", "—")
        print(f"  {icon} {r['phone']} | {r['status']} | пароль: {pwd}")
    print(f"{'='*70}")
    print(f"  ⚠️ ПАРОЛИ СОХРАНЕНЫ В JSON КОНФИГАХ!")
    print(f"  ⚠️ НЕ ТЕРЯЙ ИХ — без пароля аккаунт потерян!")
    return results


async def set_privacy():
    """Configure privacy settings for all accounts."""
    accounts = load_accounts()
    print(f"\n{'='*70}")
    print(f"  НАСТРОЙКА ПРИВАТНОСТИ ({len(accounts)} аккаунтов)")
    print(f"{'='*70}\n")

    for i, acc in enumerate(accounts):
        phone = acc["_phone"]
        name = f"{acc.get('first_name', '')} {acc.get('last_name', '')}".strip()
        print(f"  [{i+1}/{len(accounts)}] {phone} ({name})")

        proxy = load_proxy_for_account(i)
        if not proxy:
            print(f"  ❌ Нет прокси\n")
            continue

        client = make_client(acc, proxy)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                print(f"  🔒 Не авторизован\n")
                continue

            # Hide phone number from everyone
            try:
                await client(SetPrivacyRequest(
                    key=InputPrivacyKeyPhoneNumber(),
                    rules=[InputPrivacyValueDisallowAll()],
                ))
                print(f"  ✅ Телефон скрыт от всех")
            except Exception as e:
                print(f"  ⚠️ Телефон: {type(e).__name__}")

            await asyncio.sleep(random.uniform(2, 5))

            # Last seen — contacts only
            try:
                await client(SetPrivacyRequest(
                    key=InputPrivacyKeyStatusTimestamp(),
                    rules=[InputPrivacyValueAllowContacts()],
                ))
                print(f"  ✅ Последний визит — только контакты")
            except Exception as e:
                print(f"  ⚠️ Последний визит: {type(e).__name__}")

            await asyncio.sleep(random.uniform(2, 5))

            # Profile photo — contacts only
            try:
                await client(SetPrivacyRequest(
                    key=InputPrivacyKeyProfilePhoto(),
                    rules=[InputPrivacyValueAllowContacts()],
                ))
                print(f"  ✅ Фото профиля — только контакты")
            except Exception as e:
                print(f"  ⚠️ Фото: {type(e).__name__}")

            await asyncio.sleep(random.uniform(2, 5))

            # Forwarded messages — nobody
            try:
                await client(SetPrivacyRequest(
                    key=InputPrivacyKeyForwards(),
                    rules=[InputPrivacyValueDisallowAll()],
                ))
                print(f"  ✅ Пересылки — запрещены для всех")
            except Exception as e:
                print(f"  ⚠️ Пересылки: {type(e).__name__}")

        except Exception as e:
            print(f"  ❌ {type(e).__name__}: {str(e)[:100]}")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

        if i < len(accounts) - 1:
            delay = random.uniform(20, 40)
            print(f"  ⏱ Пауза {delay:.0f}с...\n")
            await asyncio.sleep(delay)

    print(f"\n  ✅ Приватность настроена!")


async def full_secure():
    """Full security cycle: backup → terminate → 2FA → privacy."""
    print(f"\n{'='*70}")
    print(f"  ПОЛНАЯ ЗАЩИТА АККАУНТОВ")
    print(f"  Шаги: бэкап → закрытие сессий → 2FA → приватность")
    print(f"{'='*70}")

    print(f"\n  Шаг 1/4: Бэкап сессий")
    backup_sessions()

    print(f"\n  Шаг 2/4: Закрытие чужих сессий")
    await terminate_foreign_sessions()

    print(f"\n  Шаг 3/4: Установка 2FA")
    await set_2fa_all()

    print(f"\n  Шаг 4/4: Настройка приватности")
    await set_privacy()

    print(f"\n{'='*70}")
    print(f"  ПОЛНАЯ ЗАЩИТА ЗАВЕРШЕНА!")
    print(f"  📦 Бэкапы: {BACKUPS_DIR}")
    print(f"  🔐 2FA пароли сохранены в JSON конфигах")
    print(f"  ⚠️ Подожди 12-24 часа перед прогревом!")
    print(f"{'='*70}")


async def security_status():
    """Show security status report for all accounts."""
    accounts = load_accounts()
    print(f"\n{'='*70}")
    print(f"  СТАТУС БЕЗОПАСНОСТИ ({len(accounts)} аккаунтов)")
    print(f"{'='*70}\n")

    for i, acc in enumerate(accounts):
        phone = acc["_phone"]
        name = f"{acc.get('first_name', '')} {acc.get('last_name', '')}".strip()
        has_2fa = acc.get("twoFA", "") not in ("", None, "0")
        has_session = Path(acc["_session_path"]).exists()

        print(f"  {phone} ({name})")
        print(f"    📁 Сессия: {'✅' if has_session else '❌'}")
        print(f"    🔐 2FA в конфиге: {'✅ ' + acc.get('twoFA', '') if has_2fa else '❌ НЕТ'}")
        print(f"    📱 Устройство: {acc.get('device', '?')}")
        print(f"    🌐 Lang: {acc.get('lang_pack', '?')}")
        print()

    # Check backups
    if BACKUPS_DIR.exists():
        backups = sorted(BACKUPS_DIR.iterdir())
        if backups:
            print(f"  📦 Бэкапов: {len(backups)} (последний: {backups[-1].name})")
    else:
        print(f"  📦 Бэкапов: НЕТ")

    print(f"{'='*70}")


async def main():
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python scripts/account_security.py sessions      # Показать сессии")
        print("  python scripts/account_security.py terminate      # Закрыть чужие сессии")
        print("  python scripts/account_security.py set-2fa        # Установить 2FA")
        print("  python scripts/account_security.py privacy        # Настроить приватность")
        print("  python scripts/account_security.py full-secure    # Полный цикл защиты")
        print("  python scripts/account_security.py status         # Статус безопасности")
        sys.exit(1)

    cmd = sys.argv[1].lower().replace("-", "_").replace(" ", "_")

    if cmd == "sessions":
        await show_sessions()
    elif cmd == "terminate":
        await terminate_foreign_sessions()
    elif cmd in ("set_2fa", "2fa"):
        await set_2fa_all()
    elif cmd == "privacy":
        await set_privacy()
    elif cmd in ("full_secure", "fullsecure", "full"):
        await full_secure()
    elif cmd == "status":
        await security_status()
    else:
        print(f"Неизвестная команда: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
