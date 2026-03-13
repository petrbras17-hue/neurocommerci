#!/usr/bin/env python3
"""
Account Status Monitor — следит за замороженными аккаунтами и уведомляет
в бот (ADMIN_TELEGRAM_ID) и чат нейросводки (DIGEST_CHAT_ID) о любых
изменениях статуса.

Режимы:
  --once       Однократная проверка всех аккаунтов
  --daemon     Цикл с интервалом (по умолчанию 30 минут)
  --interval   Интервал между проверками в минутах (default: 30)

Логика:
  1. Подключается к каждому аккаунту через Telethon (через прокси)
  2. Проверяет SpamBot — разморожен или нет
  3. При смене статуса — уведомляет в бот и дайджест-чат
  4. Сохраняет состояние в data/account_status.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from utils.standalone_helpers import build_client, load_account_json, load_proxy_for_phone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("account_monitor")

STATUS_FILE = PROJECT_ROOT / "data" / "account_status.json"
SESSIONS_DIR = settings.sessions_path


# ---------------------------------------------------------------------------
# Status persistence
# ---------------------------------------------------------------------------

def load_status() -> dict:
    if STATUS_FILE.exists():
        return json.loads(STATUS_FILE.read_text())
    return {}


def save_status(status: dict) -> None:
    STATUS_FILE.write_text(json.dumps(status, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Telegram notifications
# ---------------------------------------------------------------------------

async def send_admin_message(text: str) -> None:
    """Send message to admin via ADMIN_BOT."""
    token = str(settings.ADMIN_BOT_TOKEN or "").strip()
    admin_id = settings.ADMIN_TELEGRAM_ID
    if not token or not admin_id:
        log.warning("ADMIN_BOT_TOKEN or ADMIN_TELEGRAM_ID not set, skip admin notification")
        return
    try:
        from aiogram import Bot
        bot = Bot(token=token)
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            log.info("Admin notification sent")
        finally:
            await bot.session.close()
    except Exception as e:
        log.error(f"Failed to send admin message: {e}")


async def send_digest_message(text: str) -> None:
    """Send message to digest chat."""
    token = str(getattr(settings, "DIGEST_BOT_TOKEN", "") or "").strip()
    chat_id = str(getattr(settings, "DIGEST_CHAT_ID", "") or "").strip()
    if not token or not chat_id:
        log.warning("DIGEST_BOT_TOKEN or DIGEST_CHAT_ID not set, skip digest notification")
        return
    try:
        from aiogram import Bot
        bot = Bot(token=token)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            log.info("Digest notification sent")
        finally:
            await bot.session.close()
    except Exception as e:
        log.error(f"Failed to send digest message: {e}")


async def notify(text: str) -> None:
    """Send to both admin bot and digest chat."""
    await asyncio.gather(
        send_admin_message(text),
        send_digest_message(text),
        return_exceptions=True,
    )


# ---------------------------------------------------------------------------
# Account check via SpamBot
# ---------------------------------------------------------------------------

async def check_account_status(phone: str) -> dict:
    """
    Connect to account, check SpamBot, return status dict.

    Possible statuses:
      - "free"        — no restrictions
      - "frozen"      — restricted, appeal possible
      - "banned"      — permanently banned
      - "unauthorized" — session dead
      - "error"       — connection failed
    """
    result = {
        "phone": phone,
        "status": "error",
        "name": "",
        "spambot_text": "",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        data = load_account_json(phone)
    except (SystemExit, Exception) as e:
        result["error"] = f"No account JSON: {e}"
        return result

    # Try to connect with proxy
    proxy = load_proxy_for_phone(phone)
    client = build_client(phone, data, proxy)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            result["status"] = "unauthorized"
            return result

        me = await client.get_me()
        result["name"] = f"{me.first_name or ''} {me.last_name or ''}".strip()

        # Check SpamBot — DON'T send /start, just read last messages
        try:
            entity = await client.get_entity("SpamBot")
            msgs = await client.get_messages(entity, limit=5)

            for msg in msgs:
                if msg.out:
                    continue
                text = (msg.text or "").lower()
                result["spambot_text"] = (msg.text or "")[:200]

                if any(k in text for k in (
                    "no limits are currently applied",
                    "good news",
                    "your account is free",
                    "ваш аккаунт свободен",
                    "ограничений нет",
                )):
                    result["status"] = "free"
                    return result

                if any(k in text for k in (
                    "limited", "restricted", "frozen",
                    "ограничен", "заморожен",
                )):
                    result["status"] = "frozen"
                    return result

                if any(k in text for k in (
                    "appeal has been successfully submitted",
                    "on review", "will check it",
                    "апелляция подана", "на рассмотрении",
                )):
                    result["status"] = "appeal_submitted"
                    return result

                if any(k in text for k in (
                    "banned", "deleted", "забанен", "удалён",
                )):
                    result["status"] = "banned"
                    return result

            # If no clear status from SpamBot, try sending a message to test
            try:
                await client.send_message("me", ".")
                await asyncio.sleep(1)
                # If we can send, account is likely free
                msgs_to_self = await client.get_messages("me", limit=1)
                if msgs_to_self and msgs_to_self[0].text == ".":
                    await msgs_to_self[0].delete()
                result["status"] = "free"
            except Exception:
                result["status"] = "frozen"

        except Exception as e:
            # Can't access SpamBot but authorized — try basic write test
            try:
                await client.send_message("me", ".")
                await asyncio.sleep(1)
                msgs_to_self = await client.get_messages("me", limit=1)
                if msgs_to_self and msgs_to_self[0].text == ".":
                    await msgs_to_self[0].delete()
                result["status"] = "free"
            except Exception:
                result["status"] = "frozen"
                result["error"] = str(e)[:100]

    except (ConnectionError, OSError) as e:
        result["status"] = "error"
        result["error"] = str(e)[:100]
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# Monitor loop
# ---------------------------------------------------------------------------

def get_all_phones() -> list[str]:
    """Get all phone numbers from sessions directory."""
    phones = []
    for f in sorted(SESSIONS_DIR.glob("*.session")):
        if f.stem.isdigit():
            phones.append(f.stem)
    return phones


def format_status_change(phone: str, name: str, old: str, new: str) -> str:
    """Format a notification message about status change."""
    emoji_map = {
        "free": "\u2705",        # green check
        "frozen": "\u2744\ufe0f",     # snowflake
        "appeal_submitted": "\U0001f4e8",  # envelope
        "banned": "\u274c",       # red X
        "unauthorized": "\U0001f480",  # skull
        "error": "\u26a0\ufe0f",      # warning
    }

    old_emoji = emoji_map.get(old, "\u2753")
    new_emoji = emoji_map.get(new, "\u2753")

    status_labels = {
        "free": "РАЗМОРОЖЕН",
        "frozen": "ЗАМОРОЖЕН",
        "appeal_submitted": "АПЕЛЛЯЦИЯ НА РАССМОТРЕНИИ",
        "banned": "ЗАБАНЕН",
        "unauthorized": "СЕССИЯ МЕРТВА",
        "error": "ОШИБКА ПРОВЕРКИ",
    }

    old_label = status_labels.get(old, old.upper())
    new_label = status_labels.get(new, new.upper())

    lines = [
        f"\U0001f514 <b>СМЕНА СТАТУСА АККАУНТА</b>",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"Телефон: <code>+{phone}</code>",
        f"Имя: <b>{name or 'Unknown'}</b>",
        f"",
        f"Было: {old_emoji} {old_label}",
        f"Стало: {new_emoji} <b>{new_label}</b>",
        f"",
        f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]

    if new == "free":
        lines.extend([
            f"",
            f"\U0001f389 <b>Аккаунт можно использовать!</b>",
            f"Рекомендация: начни с warmup на 24-48 часов перед комментированием.",
        ])

    return "\n".join(lines)


def format_full_report(results: list[dict]) -> str:
    """Format a summary report of all accounts."""
    emoji_map = {
        "free": "\u2705",
        "frozen": "\u2744\ufe0f",
        "appeal_submitted": "\U0001f4e8",
        "banned": "\u274c",
        "unauthorized": "\U0001f480",
        "error": "\u26a0\ufe0f",
    }

    lines = [
        f"\U0001f4ca <b>СТАТУС ВСЕХ АККАУНТОВ</b>",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"",
    ]

    for r in results:
        emoji = emoji_map.get(r["status"], "\u2753")
        name = r.get("name") or "?"
        lines.append(f"{emoji} <code>+{r['phone']}</code> {name} — <b>{r['status'].upper()}</b>")

    free_count = sum(1 for r in results if r["status"] == "free")
    frozen_count = sum(1 for r in results if r["status"] in ("frozen", "appeal_submitted"))
    dead_count = sum(1 for r in results if r["status"] in ("banned", "unauthorized"))

    lines.extend([
        f"",
        f"Активных: <b>{free_count}</b>",
        f"Замороженных: <b>{frozen_count}</b>",
        f"Мёртвых: <b>{dead_count}</b>",
    ])

    return "\n".join(lines)


async def run_check(phones: list[str] | None = None, send_report: bool = True) -> list[dict]:
    """Run a single check cycle for all accounts."""
    if phones is None:
        phones = get_all_phones()

    if not phones:
        log.warning("No accounts found in sessions directory")
        return []

    log.info(f"Checking {len(phones)} accounts...")
    old_status = load_status()
    results = []
    changes = []

    for phone in phones:
        log.info(f"Checking +{phone}...")
        result = await check_account_status(phone)
        results.append(result)

        old = old_status.get(phone, {}).get("status", "unknown")
        new = result["status"]

        if old != new and old != "unknown":
            log.info(f"  STATUS CHANGE: {old} -> {new}")
            changes.append((phone, result.get("name", ""), old, new))
        else:
            log.info(f"  Status: {new}")

        # Save after each check
        old_status[phone] = result
        save_status(old_status)

        # Small delay between accounts
        await asyncio.sleep(2)

    # Send notifications for changes
    for phone, name, old, new in changes:
        text = format_status_change(phone, name, old, new)
        await notify(text)

    # Send full report
    if send_report:
        report = format_full_report(results)
        await notify(report)

    return results


async def daemon(interval_minutes: int = 30) -> None:
    """Run check loop forever."""
    log.info(f"Starting account monitor daemon (interval: {interval_minutes}m)")

    # First check immediately
    await run_check()

    while True:
        log.info(f"Next check in {interval_minutes} minutes...")
        await asyncio.sleep(interval_minutes * 60)
        await run_check()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Account Status Monitor")
    parser.add_argument("--once", action="store_true", help="Single check and exit")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument("--interval", type=int, default=30, help="Check interval in minutes (default: 30)")
    parser.add_argument("--phone", type=str, help="Check specific phone only")
    parser.add_argument("--no-report", action="store_true", help="Skip full report, only notify on changes")
    args = parser.parse_args()

    phones = [args.phone] if args.phone else None

    if args.daemon:
        asyncio.run(daemon(args.interval))
    else:
        results = asyncio.run(run_check(phones=phones, send_report=not args.no_report))
        for r in results:
            status = r["status"].upper()
            name = r.get("name") or "?"
            print(f"  {r['phone']}: {status} ({name})")


if __name__ == "__main__":
    main()
