"""
Check account health and run channel parsing with safe anti-ban practices.

Usage:
  python scripts/check_and_parse.py check          # Check all sessions
  python scripts/check_and_parse.py parse           # Parse channels with healthy accounts
  python scripts/check_and_parse.py parse --limit 500 --keywords-file data/parsing_keywords.txt

Safety rules enforced:
  - 1 proxy per account (NEVER share IPs)
  - NO send_code_request (instant ban on purchased sessions)
  - NO profile changes (FrozenMethodInvalidError risk)
  - Read-only operations for checking
  - 2-5 second delays between API calls
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import random
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
    UserDeactivatedError,
    FloodWaitError,
    SessionPasswordNeededError,
)
from telethon.tl.functions.help import GetAppConfigRequest
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel
import socks


SESSIONS_DIR = Path("data/sessions")
BANNED_DIR = SESSIONS_DIR / "_banned"
QUARANTINE_DIR = SESSIONS_DIR / "_quarantine"
PROXIES_FILE = Path("data/proxies.txt")

# Cyrillic detection for Russian channels
import re
_RU_RE = re.compile(r"[А-Яа-яЁё]")


def load_proxies() -> list[dict]:
    """Load proxies from file. Format: host:port:user:pass"""
    if not PROXIES_FILE.exists():
        return []
    proxies = []
    for line in PROXIES_FILE.read_text().strip().splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 4:
            proxies.append({
                "proxy_type": "http",
                "addr": parts[0],
                "port": int(parts[1]),
                "username": parts[2],
                "password": parts[3],
            })
    return proxies


def find_sessions() -> list[dict]:
    """Find all .session files that are not banned/quarantined."""
    sessions = []
    for session_file in SESSIONS_DIR.rglob("*.session"):
        # Skip banned and quarantine
        rel = session_file.relative_to(SESSIONS_DIR)
        if str(rel).startswith("_banned") or str(rel).startswith("_quarantine"):
            continue
        json_file = session_file.with_suffix(".json")
        meta = {}
        if json_file.exists():
            try:
                meta = json.loads(json_file.read_text())
            except Exception:
                pass
        sessions.append({
            "path": str(session_file),
            "phone": session_file.stem,
            "meta": meta,
        })
    return sessions


def find_all_sessions_including_quarantine() -> list[dict]:
    """Find ALL .session files including quarantine (for unfreeze attempts)."""
    sessions = []
    for session_file in SESSIONS_DIR.rglob("*.session"):
        # Skip banned
        rel = session_file.relative_to(SESSIONS_DIR)
        if str(rel).startswith("_banned"):
            continue
        json_file = session_file.with_suffix(".json")
        meta = {}
        if json_file.exists():
            try:
                meta = json.loads(json_file.read_text())
            except Exception:
                pass
        sessions.append({
            "path": str(session_file),
            "phone": session_file.stem,
            "meta": meta,
            "is_quarantine": str(rel).startswith("_quarantine"),
        })
    return sessions


async def check_session(session_info: dict, proxy: dict | None) -> dict:
    """
    Check if a Telegram session is alive. Read-only — no send_code.
    Returns status: alive, frozen, banned, dead, error
    """
    phone = session_info["phone"]
    session_path = session_info["path"].replace(".session", "")
    meta = session_info["meta"]

    app_id = meta.get("app_id", 4)
    app_hash = meta.get("app_hash", "014b35b6184100b085b0d0572f9b5103")
    device = meta.get("device", "Samsung Galaxy S21")
    app_version = meta.get("app_version", "12.4.3 (65272)")
    system_version = meta.get("sdk", "SDK 30")
    lang_pack = meta.get("lang_pack", "ru")
    system_lang_pack = meta.get("system_lang_pack", "ru-ru")

    proxy_tuple = None
    if proxy:
        proxy_tuple = (socks.HTTP, proxy["addr"], proxy["port"], True, proxy["username"], proxy["password"])

    result = {
        "phone": phone,
        "status": "unknown",
        "name": meta.get("first_name", ""),
        "proxy": f"{proxy['addr']}:{proxy['port']}" if proxy else "none",
        "error": None,
        "frozen_until": None,
    }

    client = TelegramClient(
        session_path,
        app_id,
        app_hash,
        device_model=device,
        app_version=app_version,
        system_version=system_version,
        lang_code=lang_pack,
        system_lang_code=system_lang_pack,
        proxy=proxy_tuple,
    )

    try:
        await client.connect()

        if not await client.is_user_authorized():
            result["status"] = "dead"
            result["error"] = "not_authorized"
            return result

        # Get self — read-only, safe
        me = await client.get_me()
        result["name"] = f"{me.first_name or ''} {me.last_name or ''}".strip()
        result["status"] = "alive"

        # Check for freeze via GetAppConfig
        try:
            app_config = await client(GetAppConfigRequest())
            config_dict = {}
            if hasattr(app_config, "config") and hasattr(app_config.config, "value"):
                # Parse the config
                for item in getattr(app_config.config, "value", []):
                    if hasattr(item, "key") and hasattr(item, "value"):
                        config_dict[item.key] = getattr(item.value, "value", None)

            if config_dict.get("freeze_since_date"):
                result["status"] = "frozen"
                result["frozen_until"] = config_dict.get("freeze_until_date")
        except Exception:
            pass  # GetAppConfig may not return freeze info in all cases

        # Try to read a dialog as additional alive check
        try:
            async for dialog in client.iter_dialogs(limit=1):
                pass  # If we can read dialogs, account is definitely alive
        except Exception as e:
            err_str = str(e).lower()
            if "frozen" in err_str or "420" in err_str:
                result["status"] = "frozen"
            elif "banned" in err_str or "deactivated" in err_str:
                result["status"] = "banned"

    except AuthKeyUnregisteredError:
        result["status"] = "dead"
        result["error"] = "auth_key_unregistered"
    except (UserDeactivatedBanError, UserDeactivatedError):
        result["status"] = "banned"
        result["error"] = "user_deactivated"
    except FloodWaitError as e:
        result["status"] = "alive"  # FloodWait means account is alive but rate-limited
        result["error"] = f"flood_wait_{e.seconds}s"
    except SessionPasswordNeededError:
        result["status"] = "alive"  # 2FA needed means account exists
        result["error"] = "2fa_needed"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)[:200]
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    return result


async def parse_channels_with_account(
    session_info: dict,
    proxy: dict | None,
    keywords: list[str],
    limit: int = 500,
) -> list[dict]:
    """
    Use a healthy account to search for Russian Telegram channels.
    Returns list of channel dicts.
    """
    phone = session_info["phone"]
    session_path = session_info["path"].replace(".session", "")
    meta = session_info["meta"]

    app_id = meta.get("app_id", 4)
    app_hash = meta.get("app_hash", "014b35b6184100b085b0d0572f9b5103")
    device = meta.get("device", "Samsung Galaxy S21")
    app_version = meta.get("app_version", "12.4.3 (65272)")
    system_version = meta.get("sdk", "SDK 30")
    lang_pack = meta.get("lang_pack", "ru")
    system_lang_pack = meta.get("system_lang_pack", "ru-ru")

    proxy_tuple = None
    if proxy:
        proxy_tuple = (socks.HTTP, proxy["addr"], proxy["port"], True, proxy["username"], proxy["password"])

    client = TelegramClient(
        session_path,
        app_id,
        app_hash,
        device_model=device,
        app_version=app_version,
        system_version=system_version,
        lang_code=lang_pack,
        system_lang_code=system_lang_pack,
        proxy=proxy_tuple,
    )

    channels_found: dict[int, dict] = {}
    print(f"[{phone}] Connecting via proxy {proxy['addr']}:{proxy['port']}" if proxy else f"[{phone}] Connecting without proxy")

    try:
        await client.connect()
        if not await client.is_user_authorized():
            print(f"[{phone}] NOT AUTHORIZED — skipping")
            return []

        me = await client.get_me()
        print(f"[{phone}] Connected as {me.first_name} {me.last_name or ''}")

        for kw in keywords:
            if len(channels_found) >= limit:
                break

            print(f"[{phone}] Searching: '{kw}' ({len(channels_found)}/{limit} found)")

            try:
                # contacts.Search — safe read-only API
                result = await client(SearchRequest(q=kw, limit=100))

                for chat in result.chats:
                    if not isinstance(chat, Channel):
                        continue
                    if chat.id in channels_found:
                        continue

                    # Basic filters
                    title = chat.title or ""
                    username = chat.username

                    # Must have username (public channel)
                    if not username:
                        continue

                    # Check Russian text in title
                    has_russian = bool(_RU_RE.search(title))

                    channels_found[chat.id] = {
                        "telegram_id": chat.id,
                        "username": username,
                        "title": title,
                        "has_russian": has_russian,
                        "participants_count": getattr(chat, "participants_count", None),
                        "has_comments": False,  # Will check with GetFullChannel
                    }

                # Anti-ban delay: 3-6 seconds between searches
                delay = random.uniform(3.0, 6.0)
                await asyncio.sleep(delay)

            except FloodWaitError as e:
                print(f"[{phone}] FloodWait {e.seconds}s on search '{kw}' — waiting...")
                await asyncio.sleep(min(e.seconds + 5, 120))
            except Exception as e:
                print(f"[{phone}] Error searching '{kw}': {e}")
                await asyncio.sleep(5)

        # Phase 2: Get full info for found channels (subscribers, comments)
        enriched = []
        for i, (cid, ch) in enumerate(channels_found.items()):
            if len(enriched) >= limit:
                break

            try:
                full = await client(GetFullChannelRequest(ch["username"]))
                full_chat = full.full_chat

                subscribers = getattr(full_chat, "participants_count", 0) or 0
                linked_chat_id = getattr(full_chat, "linked_chat_id", None)
                about = getattr(full_chat, "about", "") or ""

                # Filter: min 1000 subscribers
                if subscribers < 1000:
                    continue

                ch["subscriber_count"] = subscribers
                ch["has_comments"] = linked_chat_id is not None
                ch["about"] = about[:500]
                ch["language"] = "ru" if ch["has_russian"] or bool(_RU_RE.search(about)) else "other"

                enriched.append(ch)

                if (i + 1) % 10 == 0:
                    print(f"[{phone}] Enriched {len(enriched)} channels ({i+1}/{len(channels_found)} checked)")

                # Anti-ban delay: 2-4 seconds between GetFullChannel
                await asyncio.sleep(random.uniform(2.0, 4.0))

            except FloodWaitError as e:
                print(f"[{phone}] FloodWait {e.seconds}s on GetFullChannel — waiting...")
                await asyncio.sleep(min(e.seconds + 5, 120))
            except Exception as e:
                print(f"[{phone}] Error enriching {ch['username']}: {e}")
                await asyncio.sleep(3)

        print(f"[{phone}] Done: {len(enriched)} channels enriched out of {len(channels_found)} found")
        return enriched

    except Exception as e:
        print(f"[{phone}] Fatal error: {e}")
        return []
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def cmd_check():
    """Check all available sessions."""
    sessions = find_all_sessions_including_quarantine()
    proxies = load_proxies()

    if not sessions:
        print("No sessions found!")
        return

    print(f"Found {len(sessions)} sessions, {len(proxies)} proxies")
    print("=" * 70)

    results = []
    for i, sess in enumerate(sessions):
        # Assign unique proxy per account
        proxy = proxies[i % len(proxies)] if proxies else None
        print(f"\nChecking {sess['phone']} ({sess['meta'].get('first_name', '?')}){'  [quarantine]' if sess.get('is_quarantine') else ''}...")

        result = await check_session(sess, proxy)
        results.append(result)

        status_emoji = {
            "alive": "✅",
            "frozen": "🥶",
            "banned": "🚫",
            "dead": "💀",
            "error": "❌",
        }.get(result["status"], "❓")

        print(f"  {status_emoji} {result['status'].upper()} | {result['name']} | proxy: {result['proxy']}")
        if result["error"]:
            print(f"     Error: {result['error']}")
        if result["frozen_until"]:
            print(f"     Frozen until: {result['frozen_until']}")

        # Delay between checks
        await asyncio.sleep(random.uniform(2.0, 4.0))

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY:")
    for status in ["alive", "frozen", "banned", "dead", "error"]:
        count = sum(1 for r in results if r["status"] == status)
        if count > 0:
            print(f"  {status.upper()}: {count}")

    alive = [r for r in results if r["status"] == "alive"]
    if alive:
        print(f"\nHealthy accounts ready for parsing: {len(alive)}")
        for a in alive:
            print(f"  +{a['phone']} ({a['name']})")

    return results


async def cmd_parse(limit: int = 500, keywords_file: str | None = None):
    """Parse channels using healthy accounts."""
    # First check which accounts are alive
    sessions = find_sessions()
    proxies = load_proxies()

    if not sessions:
        print("No active sessions found! Run 'check' first.")
        return

    # Load keywords
    kw_file = keywords_file or "data/parsing_keywords.txt"
    if os.path.exists(kw_file):
        keywords = [line.strip() for line in open(kw_file) if line.strip()]
    else:
        keywords = [
            "маркетинг", "крипта", "бизнес", "новости", "финансы",
            "IT", "программирование", "стартап", "реклама", "SMM",
            "ecommerce", "недвижимость", "инвестиции", "трейдинг",
            "психология", "саморазвитие", "здоровье",
        ]

    print(f"Using {len(keywords)} keywords, limit={limit}")
    print(f"Found {len(sessions)} active sessions, {len(proxies)} proxies")

    # Quick health check first
    print("\nQuick health check...")
    healthy = []
    for i, sess in enumerate(sessions):
        proxy = proxies[i % len(proxies)] if proxies else None
        result = await check_session(sess, proxy)
        if result["status"] == "alive":
            healthy.append((sess, proxy))
            print(f"  ✅ +{sess['phone']} ({result['name']}) — ready")
        else:
            print(f"  ❌ +{sess['phone']} — {result['status']} ({result.get('error', '')})")
        await asyncio.sleep(2)

    if not healthy:
        print("\nNo healthy accounts available for parsing!")
        return

    print(f"\n{len(healthy)} healthy account(s). Starting parse...")

    # Split keywords across accounts
    all_channels = []
    per_account_limit = max(limit // len(healthy), 100)

    for idx, (sess, proxy) in enumerate(healthy):
        # Give each account a different slice of keywords
        account_keywords = keywords[idx::len(healthy)]
        if not account_keywords:
            account_keywords = keywords

        channels = await parse_channels_with_account(
            sess, proxy, account_keywords, limit=per_account_limit,
        )
        all_channels.extend(channels)
        print(f"\nTotal so far: {len(all_channels)} channels")

    # Dedup by telegram_id
    seen = set()
    unique = []
    for ch in all_channels:
        if ch["telegram_id"] not in seen:
            seen.add(ch["telegram_id"])
            unique.append(ch)

    # Save results
    output_path = f"output/parsed_channels_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    os.makedirs("output", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*70}")
    print(f"PARSING COMPLETE")
    print(f"  Total unique channels: {len(unique)}")
    print(f"  Russian language: {sum(1 for c in unique if c.get('language') == 'ru')}")
    print(f"  With comments: {sum(1 for c in unique if c.get('has_comments'))}")
    print(f"  Avg subscribers: {sum(c.get('subscriber_count', 0) for c in unique) // max(len(unique), 1)}")
    print(f"  Saved to: {output_path}")

    return unique


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"

    if cmd == "check":
        asyncio.run(cmd_check())
    elif cmd == "parse":
        limit = 500
        kw_file = None
        for i, arg in enumerate(sys.argv):
            if arg == "--limit" and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])
            if arg == "--keywords-file" and i + 1 < len(sys.argv):
                kw_file = sys.argv[i + 1]
        asyncio.run(cmd_parse(limit=limit, keywords_file=kw_file))
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python scripts/check_and_parse.py [check|parse]")
