"""
Warmup Launcher — safely connect accounts with unique proxies and check status.

Usage:
    python scripts/warmup_launcher.py check     # Check all accounts (frozen/alive)
    python scripts/warmup_launcher.py warmup    # Start warmup for alive accounts

Rules:
    - 1 unique proxy per account (NEVER share IPs)
    - Never call send_code_request
    - Never change profile immediately
    - Wait 12-24h after proxy change before any actions
    - Conservative warmup: read channels, set reactions, inter-account dialogs
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon import TelegramClient
from telethon.tl.functions.account import GetAuthorizationsRequest
from telethon.tl.functions.help import GetAppConfigRequest
from telethon.errors import (
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
    SessionRevokedError,
    PhoneNumberBannedError,
)

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"
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
            data["_phone"] = jf.stem
            accounts.append(data)
    return accounts


def load_proxies(count: int) -> list[dict]:
    """Load `count` unique proxies, pre-testing connectivity."""
    import subprocess

    with open(PROXIES_FILE) as f:
        lines = [l.strip() for l in f if l.strip()]
    if len(lines) < count:
        raise RuntimeError(f"Need {count} proxies, only {len(lines)} available")

    # Test proxies from evenly spaced positions, skip dead ones
    step = max(1, len(lines) // (count * 4))  # oversample 4x
    selected = []
    tested = set()

    for start in range(0, len(lines), step):
        if len(selected) >= count:
            break
        if start in tested:
            continue
        tested.add(start)
        line = lines[start]
        parts = line.split(":")
        if len(parts) != 4:
            continue

        host, port, username, password = parts[0], parts[1], parts[2], parts[3]
        proxy_url = f"http://{username}:{password}@{host}:{port}"

        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "--proxy", proxy_url, "--connect-timeout", "6", "--max-time", "10",
                 "https://api.ipify.org"],
                capture_output=True, text=True, timeout=15,
            )
            if result.stdout.strip() == "200":
                selected.append({
                    "host": host,
                    "port": int(port),
                    "username": username,
                    "password": password,
                    "raw": line,
                })
                print(f"  ✅ Прокси #{start} жив")
            else:
                print(f"  ❌ Прокси #{start} мёртв ({result.stdout.strip()})")
        except Exception:
            print(f"  ❌ Прокси #{start} таймаут")

    if len(selected) < count:
        raise RuntimeError(f"Only {len(selected)} alive proxies found, need {count}")

    return selected


async def check_account(account: dict, proxy: dict) -> dict:
    """Connect to account via proxy and check if frozen/alive/banned."""
    phone = account["_phone"]
    session_path = account["_session_path"].replace(".session", "")
    app_id = account.get("app_id", 4)
    app_hash = account.get("app_hash", "014b35b6184100b085b0d0572f9b5103")
    two_fa = account.get("twoFA", "")

    proxy_tuple = (
        2,  # SOCKS5... actually Proxyverse is HTTP
        proxy["host"],
        proxy["port"],
        True,  # use auth
        proxy["username"],
        proxy["password"],
    )

    # Proxyverse uses HTTP proxy, Telethon type 3
    proxy_tuple = (
        3,  # HTTP
        proxy["host"],
        proxy["port"],
        True,
        proxy["username"],
        proxy["password"],
    )

    device = account.get("device", "Samsung Galaxy S21")
    sdk = account.get("sdk", "SDK 31")
    app_version = account.get("app_version", "10.0.0")
    lang_pack = account.get("lang_pack", "ru")
    system_lang = account.get("system_lang_pack", "ru-ru")

    result = {
        "phone": phone,
        "name": f"{account.get('first_name', '')} {account.get('last_name', '')}".strip(),
        "proxy_session": proxy["username"].split("-")[-1][:8],
        "status": "unknown",
        "details": "",
    }

    client = TelegramClient(
        session_path,
        app_id,
        app_hash,
        proxy=proxy_tuple,
        device_model=device,
        system_version=sdk,
        app_version=app_version,
        lang_code=lang_pack,
        system_lang_code=system_lang,
        timeout=30,
        connection_retries=2,
    )

    try:
        await client.connect()

        if not await client.is_user_authorized():
            result["status"] = "NOT_AUTHORIZED"
            result["details"] = "Session expired or revoked"
            return result

        # Get self info — this is the safest check
        me = await client.get_me()
        result["name"] = f"{me.first_name or ''} {me.last_name or ''}".strip()

        # Check for restrictions
        if getattr(me, "restricted", False):
            result["status"] = "RESTRICTED"
            result["details"] = str(getattr(me, "restriction_reason", ""))
            return result

        # Try to get active sessions (read-only, safe)
        try:
            auths = await client(GetAuthorizationsRequest())
            session_count = len(auths.authorizations)
            result["details"] = f"{session_count} active sessions"
        except Exception:
            result["details"] = "could not get sessions"

        # Check app config for freeze info
        try:
            app_config = await client(GetAppConfigRequest())
            config_data = getattr(app_config, "config", None)
            if config_data:
                # Look for freeze indicators in config
                pass
        except Exception:
            pass

        result["status"] = "ALIVE"

    except AuthKeyUnregisteredError:
        result["status"] = "NOT_AUTHORIZED"
        result["details"] = "Auth key unregistered"
    except UserDeactivatedBanError:
        result["status"] = "BANNED"
        result["details"] = "Account permanently banned"
    except SessionRevokedError:
        result["status"] = "SESSION_REVOKED"
        result["details"] = "Session was revoked"
    except PhoneNumberBannedError:
        result["status"] = "PHONE_BANNED"
        result["details"] = "Phone number banned"
    except ConnectionError as exc:
        result["status"] = "CONNECTION_ERROR"
        result["details"] = str(exc)[:100]
    except Exception as exc:
        error_msg = str(exc)
        if "420" in error_msg or "FLOOD" in error_msg.upper():
            result["status"] = "FLOOD_WAIT"
            result["details"] = error_msg[:100]
        elif "frozen" in error_msg.lower():
            result["status"] = "FROZEN"
            result["details"] = error_msg[:100]
        else:
            result["status"] = "ERROR"
            result["details"] = f"{type(exc).__name__}: {error_msg[:100]}"
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    return result


async def check_all():
    """Check all accounts sequentially with unique proxies."""
    accounts = load_accounts()
    proxies = load_proxies(len(accounts))

    print(f"\n{'='*60}")
    print(f"  ПРОВЕРКА АККАУНТОВ ({len(accounts)} шт)")
    print(f"  Каждый через уникальный RU прокси")
    print(f"{'='*60}\n")

    results = []
    for i, (acc, proxy) in enumerate(zip(accounts, proxies)):
        proxy_id = proxy["username"].split("-")[-1][:8]
        print(f"[{i+1}/{len(accounts)}] Проверяю {acc['_phone']} через прокси {proxy_id}...")

        result = await check_account(acc, proxy)
        results.append(result)

        status_icon = {
            "ALIVE": "✅",
            "FROZEN": "🧊",
            "RESTRICTED": "⚠️",
            "BANNED": "💀",
            "NOT_AUTHORIZED": "🔒",
            "SESSION_REVOKED": "🔒",
            "PHONE_BANNED": "💀",
            "CONNECTION_ERROR": "🌐",
            "FLOOD_WAIT": "⏳",
            "ERROR": "❌",
        }.get(result["status"], "❓")

        print(f"  {status_icon} {result['status']} | {result['name']} | {result['details']}")

        # Random delay between checks (5-15s) to avoid pattern
        if i < len(accounts) - 1:
            delay = random.uniform(5, 15)
            print(f"  ⏱ Ждём {delay:.0f}с перед следующим...")
            await asyncio.sleep(delay)

    print(f"\n{'='*60}")
    print("  ИТОГО:")
    for r in results:
        icon = "✅" if r["status"] == "ALIVE" else "❌"
        print(f"  {icon} {r['phone']} | {r['name']} | {r['status']} | {r['details']}")
    print(f"{'='*60}")

    alive = [r for r in results if r["status"] == "ALIVE"]
    print(f"\n  Живые: {len(alive)}/{len(results)}")

    if alive:
        print("\n  Для запуска прогрева выполни:")
        print("  python scripts/warmup_launcher.py warmup")

    return results


async def warmup_single_account(account: dict, proxy: dict, account_index: int, total: int):
    """Run conservative warmup session for one account."""
    phone = account["_phone"]
    session_path = account["_session_path"].replace(".session", "")
    app_id = account.get("app_id", 4)
    app_hash = account.get("app_hash", "014b35b6184100b085b0d0572f9b5103")

    proxy_tuple = (
        3,  # HTTP
        proxy["host"],
        proxy["port"],
        True,
        proxy["username"],
        proxy["password"],
    )

    device = account.get("device", "Samsung Galaxy S21")
    sdk = account.get("sdk", "SDK 31")
    app_version = account.get("app_version", "10.0.0")
    lang_pack = account.get("lang_pack", "ru")
    system_lang = account.get("system_lang_pack", "ru-ru")

    client = TelegramClient(
        session_path,
        app_id,
        app_hash,
        proxy=proxy_tuple,
        device_model=device,
        system_version=sdk,
        app_version=app_version,
        lang_code=lang_pack,
        system_lang_code=system_lang,
        timeout=30,
        connection_retries=3,
    )

    # Safe read-only channels for warmup reading
    WARMUP_CHANNELS = [
        "durov", "telegram", "bbcrussian", "rt_russian",
        "rbc_news", "fontanka_spb", "nsmag",
    ]

    REACTION_EMOJIS = ["👍", "❤️", "🔥", "👏", "😮", "🤔", "😁", "💯", "👌", "😍"]

    name = f"{account.get('first_name', '')} {account.get('last_name', '')}".strip()
    tag = f"[{account_index}/{total}] {phone} ({name})"

    actions_done = 0
    actions_log = []

    try:
        await client.connect()

        if not await client.is_user_authorized():
            print(f"  {tag}: ❌ Не авторизован, пропускаю")
            return {"phone": phone, "status": "NOT_AUTHORIZED", "actions": 0}

        me = await client.get_me()
        name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        tag = f"[{account_index}/{total}] {phone} ({name})"
        print(f"  {tag}: ✅ Подключён")

        # --- Action 1: Read dialogs (safest) ---
        print(f"  {tag}: 📖 Читаю диалоги...")
        try:
            dialogs = await client.get_dialogs(limit=15)
            if dialogs:
                picked = random.choice(dialogs[:10])
                msgs = await client.get_messages(picked.entity, limit=random.randint(3, 7))
                if msgs:
                    # Simulate reading each message
                    for msg in msgs:
                        text = getattr(msg, "text", "") or ""
                        read_time = max(0.5, len(text) / 8.0)  # ~8 chars/sec reading
                        await asyncio.sleep(min(read_time * random.uniform(0.7, 1.3), 12.0))
                actions_done += 1
                actions_log.append(f"read_dialogs({picked.name or 'dialog'})")
                print(f"  {tag}: ✅ Прочитал диалог '{picked.name}' ({len(msgs)} сообщений)")
        except Exception as e:
            print(f"  {tag}: ⚠️ Диалоги: {type(e).__name__}")

        # Conservative delay between actions: 20-60s
        delay = random.uniform(20, 60)
        print(f"  {tag}: ⏱ Пауза {delay:.0f}с...")
        await asyncio.sleep(delay)

        # --- Action 2: Read 2-3 public channels ---
        channels_to_read = random.sample(WARMUP_CHANNELS, min(3, len(WARMUP_CHANNELS)))
        for ch_name in channels_to_read:
            print(f"  {tag}: 📖 Читаю канал @{ch_name}...")
            try:
                entity = await client.get_entity(ch_name)
                msgs = await client.get_messages(entity, limit=random.randint(5, 12))
                if msgs:
                    for msg in msgs[:random.randint(3, 6)]:
                        text = getattr(msg, "text", "") or ""
                        read_time = max(0.5, len(text) / 8.0)
                        await asyncio.sleep(min(read_time * random.uniform(0.7, 1.3), 12.0))
                actions_done += 1
                actions_log.append(f"read_channel(@{ch_name})")
                print(f"  {tag}: ✅ Прочитал @{ch_name} ({len(msgs)} постов)")
            except Exception as e:
                print(f"  {tag}: ⚠️ @{ch_name}: {type(e).__name__}")

            # Delay between channels: 15-45s
            delay = random.uniform(15, 45)
            print(f"  {tag}: ⏱ Пауза {delay:.0f}с...")
            await asyncio.sleep(delay)

        # --- Action 3: Set 1-2 reactions on random posts ---
        react_channel = random.choice(WARMUP_CHANNELS[:4])  # safer big channels
        print(f"  {tag}: 👍 Ставлю реакции в @{react_channel}...")
        try:
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji

            entity = await client.get_entity(react_channel)
            msgs = await client.get_messages(entity, limit=10)
            if msgs:
                react_msgs = random.sample(list(msgs), min(2, len(msgs)))
                for msg in react_msgs:
                    emoji = random.choice(REACTION_EMOJIS)
                    try:
                        await client(SendReactionRequest(
                            peer=entity,
                            msg_id=msg.id,
                            reaction=[ReactionEmoji(emoticon=emoji)],
                        ))
                        actions_done += 1
                        actions_log.append(f"reaction({emoji}@{react_channel}:{msg.id})")
                        print(f"  {tag}: ✅ Реакция {emoji} на пост #{msg.id}")
                    except Exception as e:
                        err = str(e)
                        if "FLOOD" in err.upper():
                            print(f"  {tag}: ⏳ FloodWait, останавливаю реакции")
                            break
                        print(f"  {tag}: ⚠️ Реакция: {type(e).__name__}")

                    # Delay between reactions: 10-25s
                    await asyncio.sleep(random.uniform(10, 25))
        except Exception as e:
            print(f"  {tag}: ⚠️ Реакции: {type(e).__name__}")

        # Conservative delay: 30-90s
        delay = random.uniform(30, 90)
        print(f"  {tag}: ⏱ Пауза {delay:.0f}с...")
        await asyncio.sleep(delay)

        # --- Action 4: Read more dialogs for natural activity ---
        print(f"  {tag}: 📖 Ещё немного читаю диалоги...")
        try:
            dialogs = await client.get_dialogs(limit=20)
            if len(dialogs) > 5:
                picked = random.choice(dialogs[3:10])
                msgs = await client.get_messages(picked.entity, limit=random.randint(2, 5))
                if msgs:
                    for msg in msgs:
                        text = getattr(msg, "text", "") or ""
                        read_time = max(0.5, len(text) / 8.0)
                        await asyncio.sleep(min(read_time * random.uniform(0.7, 1.3), 10.0))
                actions_done += 1
                actions_log.append(f"read_dialogs_2({picked.name or 'dialog'})")
                print(f"  {tag}: ✅ Прочитал ещё диалог '{picked.name}'")
        except Exception as e:
            print(f"  {tag}: ⚠️ Диалоги 2: {type(e).__name__}")

        print(f"  {tag}: 🎉 Прогрев завершён! Действий: {actions_done}")
        return {"phone": phone, "name": name, "status": "WARMED", "actions": actions_done, "log": actions_log}

    except Exception as exc:
        error_msg = str(exc)
        if "FLOOD" in error_msg.upper() or "420" in error_msg:
            print(f"  {tag}: ⏳ FloodWait — останавливаю, аккаунт жив но нужна пауза")
            return {"phone": phone, "status": "FLOOD_WAIT", "actions": actions_done, "log": actions_log}
        elif "frozen" in error_msg.lower():
            print(f"  {tag}: 🧊 Заморожен")
            return {"phone": phone, "status": "FROZEN", "actions": actions_done}
        else:
            print(f"  {tag}: ❌ {type(exc).__name__}: {error_msg[:100]}")
            return {"phone": phone, "status": "ERROR", "actions": actions_done, "error": error_msg[:200]}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def warmup_accounts():
    """Start conservative warmup for all alive accounts with staggered timing."""
    accounts = load_accounts()
    print(f"\n  🔍 Ищу рабочие прокси для {len(accounts)} аккаунтов...")
    proxies = load_proxies(len(accounts))

    print(f"\n{'='*60}")
    print(f"  ПРОГРЕВ АККАУНТОВ ({len(accounts)} шт)")
    print(f"  Режим: КОНСЕРВАТИВНЫЙ")
    print(f"  Действия: чтение каналов, диалогов, реакции")
    print(f"  НЕ делаем: отправка сообщений, смена профиля, вступление в каналы")
    print(f"{'='*60}\n")

    results = []
    for i, (acc, proxy) in enumerate(zip(accounts, proxies)):
        # Stagger start: random 30-120s between accounts
        if i > 0:
            stagger = random.uniform(30, 120)
            print(f"\n  ⏱ Ждём {stagger:.0f}с перед следующим аккаунтом (разные тайминги)...\n")
            await asyncio.sleep(stagger)

        proxy_id = proxy["username"].split("-")[-1][:8]
        print(f"\n{'─'*50}")
        print(f"  Аккаунт {i+1}/{len(accounts)}: {acc['_phone']} через прокси {proxy_id}")
        print(f"{'─'*50}")

        result = await warmup_single_account(acc, proxy, i + 1, len(accounts))
        results.append(result)

    # Summary
    print(f"\n{'='*60}")
    print("  ИТОГО ПРОГРЕВА:")
    total_actions = 0
    for r in results:
        icon = {"WARMED": "✅", "FLOOD_WAIT": "⏳", "FROZEN": "🧊"}.get(r["status"], "❌")
        actions = r.get("actions", 0)
        total_actions += actions
        print(f"  {icon} {r['phone']} | {r['status']} | {actions} действий")
    print(f"{'='*60}")
    print(f"  Всего действий: {total_actions}")
    print(f"\n  📋 Следующий прогрев рекомендуется через 6-12 часов")
    print(f"  ⚠️ НЕ меняй профили минимум 24ч после первого подключения!")

    return results


async def main():
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python scripts/warmup_launcher.py check    # Проверить аккаунты")
        print("  python scripts/warmup_launcher.py warmup   # Запустить прогрев")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "check":
        await check_all()
    elif cmd == "warmup":
        await warmup_accounts()
    else:
        print(f"Неизвестная команда: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
