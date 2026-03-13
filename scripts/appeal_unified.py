#!/usr/bin/env python3
"""
Unified appeal: finds ONE proxy that works for both Telethon AND HTTPS,
runs appeal, opens Chrome with that same proxy for CAPTCHA, then clicks Done.

Usage:
  python scripts/appeal_unified.py --phone 79637428613
  python scripts/appeal_unified.py --phone 79637428613 --done   # just click Done
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from telethon.tl.types import KeyboardButtonUrl, ReplyInlineMarkup
from utils.standalone_helpers import build_client, load_account_json, _test_proxy

PROXIES_FILE = PROJECT_ROOT / "data" / "proxies.txt"
SESSIONS_DIR = Path("data/sessions")


def _test_proxy_https(host, port, user, password, timeout=10):
    import requests as req
    proxy_url = f"http://{user}:{password}@{host}:{port}"
    try:
        r = req.get("https://telegram.org/", proxies={"https": proxy_url},
                     timeout=timeout, allow_redirects=False)
        return r.status_code in (200, 301, 302)
    except Exception:
        return False


def find_dual_proxy(phone: str, max_search: int = 60) -> tuple | None:
    """Find a proxy that works for BOTH HTTP (Telethon) and HTTPS (Chrome CAPTCHA)."""
    lines = PROXIES_FILE.read_text().strip().split("\n")
    sessions = sorted(Path(SESSIONS_DIR).glob("*.session"))
    phone_list = [f.stem for f in sessions if f.stem.isdigit()]
    try:
        start_index = phone_list.index(phone)
    except ValueError:
        start_index = 0

    for offset in range(max_search * 3):
        idx = (start_index * 60 + offset) % len(lines)
        parts = lines[idx].strip().split(":")
        if len(parts) < 4:
            continue
        host, port_str, user, password = parts[0], parts[1], parts[2], parts[3]

        if not _test_proxy(host, int(port_str), user, password, timeout=8):
            continue

        print(f"  #{idx} HTTP OK, testing HTTPS...")
        if _test_proxy_https(host, int(port_str), user, password, timeout=10):
            print(f"  ✅ DUAL proxy #{idx}: {user[:30]}...")
            return (3, host, int(port_str), True, user, password)
        else:
            print(f"  ⚠️ #{idx} HTTP only, no HTTPS")

    return None


def create_chrome_extension(phone: str, user: str, password: str) -> str:
    """Create Chrome proxy auth extension, return path."""
    ext_dir = f"/tmp/proxy_ext_{phone}"
    os.makedirs(ext_dir, exist_ok=True)

    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": f"Proxy Auth {phone}",
        "permissions": ["webRequest", "webRequestBlocking", "<all_urls>"],
        "background": {"scripts": ["background.js"]},
    }
    Path(f"{ext_dir}/manifest.json").write_text(json.dumps(manifest))

    bg_js = f"""chrome.webRequest.onAuthRequired.addListener(
  function(details, callback) {{
    callback({{
      authCredentials: {{
        username: "{user}",
        password: "{password}"
      }}
    }});
  }},
  {{urls: ["<all_urls>"]}},
  ["asyncBlocking"]
);"""
    Path(f"{ext_dir}/background.js").write_text(bg_js)
    return ext_dir


def open_chrome_captcha(phone: str, proxy: tuple, captcha_url: str):
    """Launch Chrome with proxy pointing to real telegram.org CAPTCHA URL."""
    _, host, port, _, user, password = proxy
    ext_dir = create_chrome_extension(phone, user, password)
    user_data = f"/tmp/chrome_appeal_{phone}"

    cmd = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        f"--proxy-server=http://{host}:{port}",
        f"--load-extension={ext_dir}",
        f"--user-data-dir={user_data}",
        "--no-first-run",
        "--disable-default-apps",
        captcha_url,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  🌐 Chrome открыт (PID {proc.pid})")
    print(f"  🔗 {captcha_url}")
    print(f"  🔑 Proxy: {user[:40]}... / {password}")
    return proc


def find_captcha_url(msg) -> str | None:
    if msg.reply_markup and isinstance(msg.reply_markup, ReplyInlineMarkup):
        for row in msg.reply_markup.rows:
            for button in row.buttons:
                if isinstance(button, KeyboardButtonUrl) and "captcha" in button.url.lower():
                    return button.url
    text = msg.text or ""
    m = re.search(r'https?://telegram\.org/captcha[^\s\)]+', text)
    return m.group(0) if m else None


def detect_question_type(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ("email", "e-mail", "почт", "mail address")):
        return "email"
    if ("name" in t and any(k in t for k in ("full", "first", "last", "surname"))) or ("имя" in t) or ("фамил" in t):
        return "full_name"
    if any(k in t for k in ("valid year", "send me a valid year")) or \
       ("year" in t and any(k in t for k in ("register", "joined", "created", "started", "sign"))) or \
       ("в каком году" in t):
        return "reg_year"
    if ("where" in t and any(k in t for k in ("hear", "heard", "learn", "find"))) or ("откуда" in t):
        return "source"
    if any(k in t for k in ("daily use", "how do you use", "what do you use", "briefly describe", "опишите")):
        return "usage"
    if any(k in t for k in ("blocked by mistake", "why should", "appeal", "ошибк", "why did")):
        return "reason"
    return "fallback"


def pick_button(msg):
    if not msg.buttons:
        return None
    buttons = [b for row in msg.buttons for b in row]
    for key in ("this is a mistake", "ошибк", "yes", "да", "confirm", "подтвер"):
        for b in buttons:
            if key in (b.text or "").lower():
                return b
    return None


async def wait_reply(client, entity, min_id, timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        msgs = await client.get_messages(entity, limit=10)
        incoming = [m for m in msgs if not m.out and m.id > min_id]
        if incoming:
            incoming.sort(key=lambda m: m.id)
            return incoming[0]
        await asyncio.sleep(2)
    return None


async def run_appeal(phone: str, proxy: tuple):
    """Run full appeal through a specific proxy."""
    data = load_account_json(phone)
    client = build_client(phone, data, proxy)

    print(f"\n{'='*50}")
    print(f"  АПЕЛЛЯЦИЯ: +{phone}")
    print(f"  Proxy: {proxy[4][:30]}...")
    print(f"{'='*50}")

    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("  ❌ Не авторизован")
            return None
    except (ConnectionError, OSError) as e:
        print(f"  ❌ Подключение не удалось: {str(e)[:80]}")
        return None

    try:
        me = await client.get_me()
        full_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "User"
        entity = await client.get_entity("SpamBot")

        answers = {
            "reason": "I believe this restriction is a mistake. I use Telegram for normal personal communication. I do not send spam.",
            "usage": "I use Telegram daily to chat with friends and family, read channels, and participate in group discussions.",
            "full_name": full_name,
            "email": "braslavskii1717@gmail.com",
            "reg_year": "2026",
            "source": "I heard about Telegram from friends.",
            "fallback": "I use Telegram only for normal personal communication and request a manual review.",
        }

        print(f"  ✅ Подключён как {full_name}")
        print(f"  📤 /start → @SpamBot...")
        await client.send_message(entity, "/start")
        await asyncio.sleep(3)

        latest = await client.get_messages(entity, limit=1)
        min_id = latest[0].id - 1 if latest else 0

        for step in range(1, 30):
            msg = await wait_reply(client, entity, min_id)
            if not msg:
                print("  ⏱ Таймаут")
                return None
            min_id = max(min_id, msg.id)
            text = (msg.text or "").strip()
            low = text.lower()

            if any(k in low for k in ("successfully submitted", "on review", "will check")):
                print(f"\n  🎉 АПЕЛЛЯЦИЯ ПОДАНА!")
                return "appeal_submitted"

            if any(k in low for k in ("no limits", "good news", "your account is free")):
                print(f"\n  🎉 АККАУНТ СВОБОДЕН!")
                return "account_free"

            captcha_url = find_captcha_url(msg)
            if captcha_url:
                print(f"\n  🧩 CAPTCHA обнаружена!")
                print(f"  Открываю Chrome с ТЕМ ЖЕ прокси...")
                open_chrome_captcha(phone, proxy, captcha_url)
                print(f"\n  ⏳ Реши CAPTCHA в Chrome, затем запусти:")
                print(f"  python scripts/appeal_unified.py --phone {phone} --done")

                # Save state
                state = {"phone": phone, "captcha_url": captcha_url,
                         "proxy": list(proxy), "name": full_name}
                (PROJECT_ROOT / "data" / f"captcha_state_{phone}.json").write_text(
                    json.dumps(state, ensure_ascii=False))
                return "captcha_waiting"

            button = pick_button(msg)
            if button:
                print(f"  [{step}] Кнопка: {button.text}")
                await button.click()
                await asyncio.sleep(3)
                continue

            qtype = detect_question_type(text)
            answer = answers[qtype]
            print(f"  [{step}] {qtype}: {answer[:50]}...")
            await client.send_message(entity, answer)
            await asyncio.sleep(3)

        return "max_steps"
    finally:
        await client.disconnect()


async def click_done(phone: str, proxy: tuple):
    """Click Done button in SpamBot using the SAME proxy as CAPTCHA."""
    data = load_account_json(phone)
    client = build_client(phone, data, proxy)

    print(f"\n  Подключаюсь к +{phone} для нажатия Done...")

    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("  ❌ Не авторизован")
            return
    except (ConnectionError, OSError) as e:
        print(f"  ❌ Подключение: {str(e)[:80]}")
        return

    try:
        entity = await client.get_entity("SpamBot")
        msgs = await client.get_messages(entity, limit=5)

        for m in msgs:
            if not m.buttons:
                continue
            for row in m.buttons:
                for b in row:
                    txt = (b.text or "").lower()
                    if "done" in txt or "готово" in txt or "go back" in txt:
                        print(f"  Нажимаю: {b.text}")
                        await b.click()
                        await asyncio.sleep(5)
                        resp = await client.get_messages(entity, limit=3)
                        for r in resp:
                            if not r.out:
                                print(f"  SpamBot: {r.text[:300]}")
                        return

        print("  Кнопка Done не найдена. Последние сообщения:")
        for m in msgs[:3]:
            if not m.out:
                print(f"    {m.text[:150]}")
    finally:
        await client.disconnect()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", required=True)
    parser.add_argument("--done", action="store_true", help="Just click Done (after CAPTCHA)")
    args = parser.parse_args()

    phone = args.phone

    if args.done:
        # Load saved proxy from state
        state_file = PROJECT_ROOT / "data" / f"captcha_state_{phone}.json"
        if state_file.exists():
            state = json.loads(state_file.read_text())
            proxy = tuple(state["proxy"])
            print(f"  Используем сохранённый прокси: {proxy[4][:30]}...")
        else:
            print("  ⚠️ Нет сохранённого состояния, ищу DUAL прокси...")
            proxy = find_dual_proxy(phone)
            if not proxy:
                print("  ❌ Нет DUAL прокси")
                return

        await click_done(phone, proxy)
        return

    # Full appeal flow
    print(f"🔍 Ищу DUAL прокси (HTTP+HTTPS) для +{phone}...")
    proxy = find_dual_proxy(phone)
    if not proxy:
        print("❌ Не нашёл прокси с HTTP+HTTPS. Попробуй позже.")
        return

    result = await run_appeal(phone, proxy)
    print(f"\nРезультат: {result}")


if __name__ == "__main__":
    asyncio.run(main())
