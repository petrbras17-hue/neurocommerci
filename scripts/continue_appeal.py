#!/usr/bin/env python3
"""
Continue SpamBot appeal from current state (without sending /start).
After CAPTCHA — opens Chrome with proxy directly.

Usage:
  python scripts/continue_appeal.py --phone 79637428613
  python scripts/continue_appeal.py --phone 79637429150
  python scripts/continue_appeal.py --phone 79637429437
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from telethon.tl.types import KeyboardButtonUrl, KeyboardButtonCallback, ReplyInlineMarkup
from utils.standalone_helpers import build_client, load_account_json, load_proxy_for_phone, _test_proxy


def find_captcha_url(msg) -> str | None:
    if msg.reply_markup and isinstance(msg.reply_markup, ReplyInlineMarkup):
        for row in msg.reply_markup.rows:
            for button in row.buttons:
                if isinstance(button, KeyboardButtonUrl) and "captcha" in button.url.lower():
                    return button.url
    text = msg.text or ""
    m = re.search(r'https?://telegram\.org/captcha[^\s\)]+', text)
    if m:
        return m.group(0)
    return None


def detect_question_type(text: str) -> str:
    low = text.lower()
    if any(k in low for k in ("full legal name", "your name", "full name")):
        return "full_name"
    if any(k in low for k in ("contact email", "email address", "your email")):
        return "email"
    if any(k in low for k in ("what year", "sign up", "registered")):
        return "reg_year"
    if any(k in low for k in ("how you discovered", "who invited", "how did you")):
        return "discovery"
    if any(k in low for k in ("daily use", "average daily", "what you like doing", "features you prefer")):
        return "daily_use"
    if any(k in low for k in ("more details", "why do you think", "what went wrong")):
        return "reason"
    if any(k in low for k in ("acknowledge", "truthful", "confirm")):
        return "confirm"
    return "reason"


def build_answer_bank(full_name: str, email: str, reg_year: str) -> dict:
    return {
        "full_name": [full_name],
        "email": [email],
        "reg_year": [reg_year],
        "reason": [
            "I use Telegram only for normal personal communication and I request a manual review of this restriction.",
            "I only chat with friends and subscribe to public channels. I don't spam or violate any rules.",
        ],
        "discovery": [
            "A friend recommended Telegram to me several years ago. I've been using it since to stay in touch with friends and family.",
        ],
        "daily_use": [
            "I use Telegram daily to chat with friends and family, read channels, and participate in hobby group discussions.",
        ],
        "confirm": ["Confirm"],
    }


def pick_button(msg):
    if not msg.buttons:
        return None
    for row in msg.buttons:
        for btn in row:
            text_lower = (btn.text or "").lower()
            if any(k in text_lower for k in ("this is a mistake", "mistake", "yes", "submit")):
                return btn
    return None


async def wait_new_incoming(client, entity, min_msg_id: int, timeout: int = 60):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        msgs = await client.get_messages(entity, limit=5, min_id=min_msg_id)
        incoming = [m for m in msgs if not m.out]
        if incoming:
            return incoming[0]
        await asyncio.sleep(2)
    return None


def get_proxy_tuple(phone: str):
    """Get raw proxy tuple for the phone."""
    return load_proxy_for_phone(phone)


def open_chrome_with_proxy(captcha_url: str, proxy_tuple):
    """Open Chrome with proxy settings to solve CAPTCHA on real telegram.org domain."""
    _, host, port, _, username, password = proxy_tuple

    # Create MV2 Chrome extension for proxy auth
    ext_dir = Path("/tmp/proxy_ext_appeal")
    ext_dir.mkdir(exist_ok=True)

    (ext_dir / "manifest.json").write_text(json.dumps({
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Proxy Auth",
        "permissions": ["webRequest", "webRequestBlocking", "<all_urls>"],
        "background": {"scripts": ["background.js"]},
    }))

    (ext_dir / "background.js").write_text(f"""
chrome.webRequest.onAuthRequired.addListener(
  function(details, callback) {{
    callback({{
      authCredentials: {{
        username: "{username}",
        password: "{password}"
      }}
    }});
  }},
  {{urls: ["<all_urls>"]}},
  ["asyncBlocking"]
);
""")

    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    user_data = f"/tmp/chrome_proxy_{phone}"

    cmd = [
        chrome_path,
        f"--proxy-server=http://{host}:{port}",
        f"--load-extension={ext_dir}",
        f"--user-data-dir={user_data}",
        "--no-first-run",
        "--disable-default-apps",
        captcha_url,
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  🌐 Chrome запущен (PID {proc.pid}) с прокси {host}:{port}")
    print(f"  👉 Реши CAPTCHA в браузере, потом вернись сюда")
    return proc


async def continue_appeal(phone: str, email: str, reg_year: str):
    print(f"\n{'='*60}")
    print(f"  ПРОДОЛЖЕНИЕ АПЕЛЛЯЦИИ: +{phone}")
    print(f"{'='*60}")

    data = load_account_json(phone)
    proxy = load_proxy_for_phone(phone)
    client = build_client(phone, data, proxy)

    try:
        await client.connect()
    except Exception as e:
        print(f"  ❌ Не удалось подключиться: {e}")
        return "connection_failed"

    if not await client.is_user_authorized():
        print(f"  ❌ Не авторизован")
        await client.disconnect()
        return "unauthorized"

    try:
        me = await client.get_me()
        full_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Telegram User"
        entity = await client.get_entity("SpamBot")
        answers = build_answer_bank(full_name, email, reg_year)
        used = {k: 0 for k in answers}

        print(f"  ✅ Подключён как {full_name}")

        # Check last bot message to see current state
        msgs = await client.get_messages(entity, limit=5)
        last_bot_msg = None
        for m in msgs:
            if not m.out:
                last_bot_msg = m
                break

        if not last_bot_msg:
            print("  ❌ Нет сообщений от SpamBot")
            return "no_messages"

        text = (last_bot_msg.text or "").strip()
        low = text.lower()
        print(f"  📩 Последнее от бота: {text[:100]}...")

        # Check if already has CAPTCHA
        captcha_url = find_captcha_url(last_bot_msg)
        if captcha_url:
            print(f"  🧩 CAPTCHA уже есть!")
            open_chrome_with_proxy(captcha_url, proxy)
            print(f"\n  ⏳ Жду решения CAPTCHA (макс 5 мин)...")
            # Wait for user to solve, then check for new messages
            for i in range(60):  # 5 min
                await asyncio.sleep(5)
                new_msgs = await client.get_messages(entity, limit=3)
                for nm in new_msgs:
                    if nm.out or nm.id <= last_bot_msg.id:
                        continue
                    rtext = (nm.text or "").lower()
                    print(f"  📩 SpamBot: {nm.text[:300]}")
                    if any(k in rtext for k in ("successfully submitted", "on review", "will check")):
                        print(f"\n  🎉 АПЕЛЛЯЦИЯ ПОДАНА!")
                        return "appeal_submitted"
                    if any(k in rtext for k in ("no limits", "good news", "your account is free")):
                        print(f"\n  🎉 АККАУНТ РАЗМОРОЖЕН!")
                        return "account_free"
                    if any(k in rtext for k in ("captcha", "try again")):
                        print(f"  ⚠️ CAPTCHA не пройдена, попробуй снова")
                        return "captcha_failed"
            return "captcha_timeout"

        # Check if appeal already submitted
        if any(k in low for k in ("successfully submitted", "on review", "will check")):
            print(f"\n  🎉 АПЕЛЛЯЦИЯ УЖЕ ПОДАНА!")
            return "appeal_submitted"

        if any(k in low for k in ("no limits", "good news", "your account is free")):
            print(f"\n  🎉 АККАУНТ УЖЕ РАЗМОРОЖЕН!")
            return "account_free"

        # Need to answer current question and continue
        min_id = last_bot_msg.id

        # Answer the current question first
        if last_bot_msg.buttons:
            button = pick_button(last_bot_msg)
            if button:
                print(f"  [0] Нажимаю: {button.text}")
                await button.click()
                await asyncio.sleep(3)
        else:
            qtype = detect_question_type(text)
            pool = answers[qtype]
            answer = pool[0]
            print(f"  [0] {qtype}: {answer[:50]}...")
            await client.send_message(entity, answer)
            await asyncio.sleep(3)

        # Continue answering
        for step in range(1, 25):
            msg = await wait_new_incoming(client, entity, min_msg_id=min_id, timeout=60)
            if not msg:
                print(f"  ⏱ Нет ответа от SpamBot")
                return "timeout"
            min_id = max(min_id, msg.id)

            text = (msg.text or "").strip()
            low = text.lower()
            print(f"  📩 [{step}] {text[:100]}...")

            if any(k in low for k in ("successfully submitted", "on review", "will check")):
                print(f"\n  🎉 АПЕЛЛЯЦИЯ ПОДАНА!")
                return "appeal_submitted"

            if any(k in low for k in ("no limits", "good news", "your account is free")):
                print(f"\n  🎉 АККАУНТ РАЗМОРОЖЕН!")
                return "account_free"

            # CAPTCHA detected — open Chrome with proxy
            captcha_url = find_captcha_url(msg)
            if captcha_url:
                print(f"\n  🧩 CAPTCHA!")
                open_chrome_with_proxy(captcha_url, proxy)
                print(f"\n  ⏳ Жду решения CAPTCHA (макс 5 мин)...")

                for i in range(60):
                    await asyncio.sleep(5)
                    new_msgs = await client.get_messages(entity, limit=3)
                    for nm in new_msgs:
                        if nm.out or nm.id <= msg.id:
                            continue
                        rtext = (nm.text or "").lower()
                        print(f"  📩 SpamBot: {nm.text[:300]}")
                        if any(k in rtext for k in ("successfully submitted", "on review", "will check")):
                            print(f"\n  🎉 АПЕЛЛЯЦИЯ ПОДАНА!")
                            return "appeal_submitted"
                        if any(k in rtext for k in ("no limits", "good news", "your account is free")):
                            print(f"\n  🎉 АККАУНТ РАЗМОРОЖЕН!")
                            return "account_free"
                return "captcha_waiting"

            if msg.buttons:
                button = pick_button(msg)
                if button:
                    print(f"  [{step}] Нажимаю: {button.text}")
                    await button.click()
                    await asyncio.sleep(3)
                    continue

            qtype = detect_question_type(text)
            pool = answers[qtype]
            idx = min(used[qtype], len(pool) - 1)
            answer = pool[idx]
            used[qtype] += 1
            print(f"  [{step}] {qtype}: {answer[:50]}...")
            await client.send_message(entity, answer)
            await asyncio.sleep(3)

        return "max_steps"
    finally:
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", required=True)
    parser.add_argument("--email", default="braslavskii1717@gmail.com")
    parser.add_argument("--reg-year", default="2024")
    args = parser.parse_args()

    phone = args.phone.replace("+", "")
    result = asyncio.run(continue_appeal(phone, args.email, args.reg_year))
    print(f"\n  ➡️  Результат: {result}")
