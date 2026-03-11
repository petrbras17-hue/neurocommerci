#!/usr/bin/env python3
"""Dynamic @SpamBot appeal runner (question order agnostic).

Usage:
  python scripts/appeal_spambot_dynamic.py --phone 79637411890 --email you@example.com
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

from telethon.tl.types import KeyboardButtonUrl, ReplyInlineMarkup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if not (PROJECT_ROOT / "utils").exists():
    PROJECT_ROOT = Path.cwd()
sys.path.insert(0, str(PROJECT_ROOT))

from utils.standalone_helpers import build_client, load_account_json, load_proxy_for_phone

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def detect_question_type(text: str) -> str:
    t = text.lower()

    if any(k in t for k in ("email", "e-mail", "почт", "mail address")):
        return "email"
    if (
        ("name" in t and any(k in t for k in ("full", "first", "last", "surname")))
        or ("имя" in t)
        or ("фамил" in t)
    ):
        return "full_name"
    if (
        (
            "year" in t
            and any(
                k in t
                for k in ("register", "joined", "created", "started", "sign up", "signed up")
            )
        )
        or ("valid year" in t)
        or ("в каком году" in t)
        or ("когда вы зарегистр" in t)
    ):
        return "reg_year"
    if (
        ("where" in t and any(k in t for k in ("hear", "heard", "learn", "find")))
        or ("откуда" in t)
        or ("узнали" in t)
    ):
        return "source"
    if any(
        k in t
        for k in (
            "average daily use",
            "how do you use telegram",
            "what do you use telegram for",
            "briefly describe your average daily use",
            "как вы используете telegram",
            "опишите",
            "для чего используете",
        )
    ):
        return "usage"
    if any(
        k in t
        for k in (
            "blocked by mistake",
            "why should",
            "why did this happen",
            "appeal",
            "наруш",
            "ошибк",
            "почему",
        )
    ):
        return "reason"
    return "fallback"


def find_captcha_url(msg) -> str | None:
    if not msg.reply_markup:
        return None
    if isinstance(msg.reply_markup, ReplyInlineMarkup):
        for row in msg.reply_markup.rows:
            for button in row.buttons:
                if isinstance(button, KeyboardButtonUrl) and "captcha" in button.url.lower():
                    return button.url
    return None


def pick_button(msg):
    if not msg.buttons:
        return None
    buttons = [b for row in msg.buttons for b in row]
    lower = [(b, (b.text or "").lower()) for b in buttons]

    def pick(keyword: str):
        for b, txt in lower:
            if keyword in txt:
                return b
        return None

    for key in ("this is a mistake", "ошибк", "yes", "да", "confirm", "подтвер", "done", "готов"):
        b = pick(key)
        if b is not None:
            return b
    return None


async def wait_new_incoming(client, entity, min_msg_id: int, timeout: int = 60):
    start = time.time()
    while time.time() - start < timeout:
        msgs = await client.get_messages(entity, limit=10)
        incoming = [m for m in msgs if not m.out and m.id > min_msg_id]
        if incoming:
            incoming.sort(key=lambda m: m.id)
            return incoming[0]
        await asyncio.sleep(2)
    return None


async def find_recent_email(client, entity) -> str | None:
    msgs = await client.get_messages(entity, limit=80)
    for m in msgs:
        if not m.out or not m.text:
            continue
        found = EMAIL_RE.search(m.text)
        if found:
            return found.group(0)
    return None


def build_answer_bank(full_name: str, email: str, reg_year: str):
    return {
        "reason": [
            "I believe this restriction is a mistake. I use Telegram for normal personal communication, reading channels, and chats. I do not send spam and I ask for a review.",
            "I think my account was flagged in error. I only use Telegram for personal messaging and regular channel reading. Please review and remove the restriction.",
        ],
        "usage": [
            "I use Telegram daily to chat with friends and family, read channels, and participate in hobby group discussions.",
            "My normal use is personal chats, reading news channels, and occasional group discussions with people I know.",
        ],
        "full_name": [full_name],
        "email": [email],
        "reg_year": [reg_year],
        "source": [
            "I heard about Telegram from friends.",
            "A friend recommended Telegram to me.",
        ],
        "fallback": [
            "I use Telegram only for normal personal communication and I request a manual review of this restriction.",
        ],
    }


async def run(phone: str, email: str | None, reg_year: str, max_steps: int) -> int:
    data = load_account_json(phone)
    proxy = load_proxy_for_phone(phone)
    client = build_client(phone, data, proxy)

    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("ERROR: session is not authorized")
            return 2

        me = await client.get_me()
        full_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Telegram User"
        entity = await client.get_entity("SpamBot")
        if not email:
            email = await find_recent_email(client, entity)
        if not email:
            print("ERROR: no email provided/found. Pass --email.")
            return 2

        answers = build_answer_bank(full_name=full_name, email=email, reg_year=reg_year)
        used = {k: 0 for k in answers}

        print(f"Connected as: {full_name} (+{phone})")
        print(f"Using email: {email}")
        print("Sending /start to @SpamBot...")

        ts = time.time()
        await client.send_message(entity, "/start")
        await asyncio.sleep(3)

        latest = await client.get_messages(entity, limit=1)
        min_id = latest[0].id - 1 if latest else 0

        for step in range(1, max_steps + 1):
            msg = await wait_new_incoming(client, entity, min_msg_id=min_id, timeout=60)
            if not msg:
                print(f"[{step}] No new incoming message in timeout window.")
                return 1
            min_id = max(min_id, msg.id)

            text = (msg.text or "").strip()
            low = text.lower()
            print(f"[{step}] SpamBot: {text[:500]}")

            if any(
                k in low
                for k in (
                    "appeal has been successfully submitted",
                    "on review",
                    "will check it as soon as possible",
                    "рассмотр",
                    "апелляц",
                )
            ):
                print("RESULT: appeal_submitted_or_under_review")
                return 0

            if any(
                k in low
                for k in (
                    "no limits are currently applied",
                    "good news",
                    "your account is free",
                    "ограничений нет",
                )
            ):
                print("RESULT: account_not_restricted")
                return 0

            captcha_url = find_captcha_url(msg)
            if captcha_url:
                print(f"CAPTCHA_URL: {captcha_url}")
                print("RESULT: captcha_required_manual_step")
                return 1

            if msg.buttons:
                btns = [b.text for row in msg.buttons for b in row]
                print(f"Buttons: {btns}")
                button = pick_button(msg)
                if button is not None:
                    print(f"Click: {button.text}")
                    await button.click()
                    await asyncio.sleep(3)
                    continue

            qtype = detect_question_type(text)
            pool = answers[qtype]
            idx = min(used[qtype], len(pool) - 1)
            answer = pool[idx]
            used[qtype] += 1
            print(f"Answer({qtype}): {answer}")
            await client.send_message(entity, answer)
            await asyncio.sleep(3)

        print("RESULT: max_steps_reached")
        return 1
    finally:
        await client.disconnect()


def normalize_phone(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""
    return digits


def main() -> int:
    parser = argparse.ArgumentParser(description="Dynamic @SpamBot appeal runner")
    parser.add_argument("--phone", required=True, help="Phone with or without leading +")
    parser.add_argument("--email", default=None, help="Appeal contact email")
    parser.add_argument("--reg-year", default="2024", help="Registration year answer")
    parser.add_argument("--max-steps", type=int, default=20, help="Safety loop limit")
    args = parser.parse_args()

    phone = normalize_phone(args.phone)
    if not phone:
        print("ERROR: invalid --phone")
        return 2
    return asyncio.run(run(phone, args.email, args.reg_year, args.max_steps))


if __name__ == "__main__":
    raise SystemExit(main())
