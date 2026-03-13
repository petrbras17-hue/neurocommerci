#!/usr/bin/env python3
"""
SpamBot appeal with reverse proxy tunnel for CAPTCHA.

For each frozen account:
1. Runs appeal through all questions
2. When CAPTCHA detected — starts local reverse proxy that tunnels telegram.org
   through the account's proxy
3. User opens http://localhost:PORT/captcha?... — traffic goes via account's proxy IP
4. User solves CAPTCHA, presses Enter in terminal
5. Script presses Done and checks result

Usage:
  python scripts/appeal_with_tunnel.py --phone 79637429150
  python scripts/appeal_with_tunnel.py --all
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import http.client
import json
import re
import socket
import sys
import threading
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from telethon.tl.types import KeyboardButtonUrl, ReplyInlineMarkup
from utils.standalone_helpers import build_client, load_account_json, load_proxy_for_phone, _test_proxy


# ---------------------------------------------------------------------------
# Reverse proxy: localhost:PORT → telegram.org via upstream Proxyverse proxy
# ---------------------------------------------------------------------------

class ReverseProxyHandler(BaseHTTPRequestHandler):
    """
    Reverse proxy that forwards requests to telegram.org through upstream HTTP proxy.
    Uses requests library (same as curl) instead of raw CONNECT.
    """

    def log_message(self, format, *args):
        pass

    def _forward_request(self, method):
        import requests as req
        srv = self.server
        path = self.path
        target_url = f"https://telegram.org{path}"

        # Read body if any
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        proxy_url = f"http://{srv.upstream_user}:{srv.upstream_pass}@{srv.upstream_host}:{srv.upstream_port}"
        proxies = {"http": proxy_url, "https": proxy_url}

        # Forward relevant headers
        fwd_headers = {}
        for key in ("User-Agent", "Accept", "Accept-Language", "Cookie",
                     "Referer", "Origin", "Content-Type"):
            val = self.headers.get(key)
            if val:
                fwd_headers[key] = val
        if "User-Agent" not in fwd_headers:
            fwd_headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

        try:
            resp = req.request(
                method, target_url,
                headers=fwd_headers,
                data=body,
                proxies=proxies,
                timeout=30,
                allow_redirects=False,
            )

            self.send_response(resp.status_code)

            # Forward response headers
            skip = {"transfer-encoding", "connection", "keep-alive", "content-encoding", "content-length"}
            for key, val in resp.headers.items():
                if key.lower() in skip:
                    continue
                # Rewrite Location redirects
                if key.lower() == "location" and "telegram.org" in val:
                    val = val.replace("https://telegram.org", f"http://127.0.0.1:{srv.server_port}")
                self.send_header(key, val)

            # Rewrite body: replace telegram.org URLs with local
            body_bytes = resp.content
            try:
                body_str = body_bytes.decode("utf-8")
                body_str = body_str.replace("https://telegram.org", f"http://127.0.0.1:{srv.server_port}")
                body_bytes = body_str.encode("utf-8")
            except (UnicodeDecodeError, Exception):
                pass

            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body_bytes)

        except Exception as e:
            self.send_error(502, f"Tunnel error: {e}")

    def do_GET(self):
        self._forward_request("GET")

    def do_POST(self):
        self._forward_request("POST")

    def do_HEAD(self):
        self._forward_request("HEAD")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()


class ReverseProxyServer(HTTPServer):
    def __init__(self, port, upstream_host, upstream_port, upstream_user, upstream_pass):
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.upstream_user = upstream_user
        self.upstream_pass = upstream_pass
        super().__init__(("127.0.0.1", port), ReverseProxyHandler)


def start_reverse_proxy(local_port: int, proxy_tuple: tuple) -> ReverseProxyServer:
    """Start reverse proxy tunnel in background."""
    _, host, port, _, user, password = proxy_tuple
    server = ReverseProxyServer(local_port, host, port, user, password)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# SpamBot appeal logic
# ---------------------------------------------------------------------------

def detect_question_type(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ("email", "e-mail", "почт", "mail address")):
        return "email"
    if ("name" in t and any(k in t for k in ("full", "first", "last", "surname"))) or ("имя" in t) or ("фамил" in t):
        return "full_name"
    if ("year" in t and any(k in t for k in ("register", "joined", "created", "started", "sign up", "signed up"))) or ("valid year" in t) or ("в каком году" in t):
        return "reg_year"
    if ("where" in t and any(k in t for k in ("hear", "heard", "learn", "find"))) or ("откуда" in t) or ("узнали" in t):
        return "source"
    if any(k in t for k in ("average daily use", "how do you use telegram", "what do you use telegram for", "briefly describe your average daily use", "как вы используете telegram", "опишите")):
        return "usage"
    if any(k in t for k in ("blocked by mistake", "why should", "why did this happen", "appeal", "наруш", "ошибк", "почему")):
        return "reason"
    return "fallback"


def find_captcha_url(msg) -> str | None:
    # Check inline URL buttons first
    if msg.reply_markup and isinstance(msg.reply_markup, ReplyInlineMarkup):
        for row in msg.reply_markup.rows:
            for button in row.buttons:
                if isinstance(button, KeyboardButtonUrl) and "captcha" in button.url.lower():
                    return button.url
    # Check message text for markdown links: [text](https://telegram.org/captcha?...)
    text = msg.text or ""
    m = re.search(r'https?://telegram\.org/captcha[^\s\)]+', text)
    if m:
        return m.group(0)
    return None


def pick_button(msg):
    if not msg.buttons:
        return None
    buttons = [b for row in msg.buttons for b in row]
    lower = [(b, (b.text or "").lower()) for b in buttons]
    def pick(keyword):
        for b, txt in lower:
            if keyword in txt:
                return b
        return None
    for key in ("this is a mistake", "ошибк", "yes", "да", "confirm", "подтвер"):
        b = pick(key)
        if b is not None:
            return b
    return None


async def wait_new_incoming(client, entity, min_msg_id, timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        msgs = await client.get_messages(entity, limit=10)
        incoming = [m for m in msgs if not m.out and m.id > min_msg_id]
        if incoming:
            incoming.sort(key=lambda m: m.id)
            return incoming[0]
        await asyncio.sleep(2)
    return None


def build_answer_bank(full_name, email, reg_year):
    return {
        "reason": ["I believe this restriction is a mistake. I use Telegram for normal personal communication, reading channels, and chats. I do not send spam and I ask for a review."],
        "usage": ["I use Telegram daily to chat with friends and family, read channels, and participate in hobby group discussions."],
        "full_name": [full_name],
        "email": [email],
        "reg_year": [reg_year],
        "source": ["I heard about Telegram from friends."],
        "fallback": ["I use Telegram only for normal personal communication and I request a manual review of this restriction."],
    }


def _test_proxy_https(host: str, port: int, user: str, password: str, timeout: int = 10) -> bool:
    """Test that proxy can tunnel HTTPS to telegram.org (needed for CAPTCHA tunnel)."""
    import requests as req
    proxy_url = f"http://{user}:{password}@{host}:{port}"
    try:
        r = req.get("https://telegram.org/", proxies={"https": proxy_url},
                     timeout=timeout, allow_redirects=False)
        return r.status_code in (200, 301, 302)
    except Exception:
        return False


def get_proxy_candidates(phone: str, max_candidates: int = 50, test_https: bool = False):
    """Get a list of live proxies for this phone.
    If test_https=True, also verifies HTTPS CONNECT to telegram.org works.
    """
    proxies_file = PROJECT_ROOT / "data" / "proxies.txt"
    lines = proxies_file.read_text().strip().split("\n")
    sessions_dir = PROJECT_ROOT / "data" / "sessions"
    sessions = sorted(sessions_dir.glob("*.session"))
    phone_list = [f.stem for f in sessions if f.stem.isdigit()]
    try:
        start_index = phone_list.index(phone)
    except ValueError:
        start_index = 0

    candidates = []
    for offset in range(len(lines)):
        if len(candidates) >= max_candidates:
            break
        idx = (start_index * 60 + offset) % len(lines)
        parts = lines[idx].strip().split(":")
        if len(parts) < 4:
            continue
        host, port_str, user, password = parts[0], parts[1], parts[2], parts[3]
        if not _test_proxy(host, int(port_str), user, password, timeout=5):
            print(f"  ❌ #{idx} мёртв")
            continue
        if test_https:
            if _test_proxy_https(host, int(port_str), user, password, timeout=10):
                print(f"  ✅ #{idx} жив (HTTP+HTTPS)")
                candidates.append((3, host, int(port_str), True, user, password))
            else:
                print(f"  ⚠️ #{idx} жив (HTTP) но HTTPS tunnel не работает")
        else:
            print(f"  ✅ #{idx} жив (curl)")
            candidates.append((3, host, int(port_str), True, user, password))
    return candidates


async def try_connect_with_retry(phone: str, data: dict, max_telethon_tries: int = 5):
    """Try multiple proxies until Telethon actually connects."""
    candidates = get_proxy_candidates(phone, max_candidates=8)
    if not candidates:
        print(f"  ❌ Нет живых прокси для {phone}")
        return None, None

    for i, proxy in enumerate(candidates):
        if i >= max_telethon_tries:
            break
        print(f"  🔌 Telethon попытка {i+1}/{min(len(candidates), max_telethon_tries)} через {proxy[1]}:{proxy[2]}...")
        client = build_client(phone, data, proxy)
        try:
            await client.connect()
            if await client.is_user_authorized():
                print(f"  ✅ Telethon подключён!")
                return client, proxy
            else:
                print(f"  ❌ Не авторизован")
                await client.disconnect()
                return None, None
        except (ConnectionError, OSError) as e:
            print(f"  ❌ Telethon: {str(e)[:80]}")
            try:
                await client.disconnect()
            except Exception:
                pass
            continue

    print(f"  ❌ Все прокси не подошли для Telethon")
    return None, None


async def run_appeal_with_tunnel(phone: str, email: str, reg_year: str, local_port: int):
    print(f"\n{'='*60}")
    print(f"  АПЕЛЛЯЦИЯ: +{phone}")
    print(f"{'='*60}")

    data = load_account_json(phone)
    client, proxy = await try_connect_with_retry(phone, data)
    if not client:
        return "connection_failed"

    try:
        if not await client.is_user_authorized():
            print(f"  ❌ Не авторизован")
            return "unauthorized"

        me = await client.get_me()
        full_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Telegram User"
        entity = await client.get_entity("SpamBot")
        answers = build_answer_bank(full_name, email, reg_year)
        used = {k: 0 for k in answers}

        print(f"  ✅ Подключён как {full_name}")
        print(f"  📤 Отправляю /start в @SpamBot...")

        await client.send_message(entity, "/start")
        await asyncio.sleep(3)

        latest = await client.get_messages(entity, limit=1)
        min_id = latest[0].id - 1 if latest else 0

        for step in range(1, 30):
            msg = await wait_new_incoming(client, entity, min_msg_id=min_id, timeout=60)
            if not msg:
                print(f"  ⏱ Нет ответа от SpamBot")
                return "timeout"
            min_id = max(min_id, msg.id)

            text = (msg.text or "").strip()
            low = text.lower()

            if any(k in low for k in ("appeal has been successfully submitted", "on review", "will check it as soon as possible")):
                print(f"\n  🎉 АПЕЛЛЯЦИЯ ПОДАНА!")
                return "appeal_submitted"

            if any(k in low for k in ("no limits are currently applied", "good news", "your account is free")):
                print(f"\n  🎉 АККАУНТ РАЗМОРОЖЕН!")
                return "account_free"

            # CAPTCHA detected
            captcha_url = find_captcha_url(msg)
            if captcha_url:
                # Extract path from captcha URL
                parsed = urllib.parse.urlparse(captcha_url)
                captcha_path = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path

                print(f"\n  🧩 CAPTCHA!")

                # Find HTTPS-capable proxy for tunnel (may differ from Telethon proxy)
                print(f"  🔍 Ищу прокси с HTTPS tunnel для CAPTCHA...")
                https_proxies = get_proxy_candidates(phone, max_candidates=3, test_https=True)
                if not https_proxies:
                    print(f"  ❌ Нет прокси с HTTPS tunnel! Используй прокси напрямую в браузере.")
                    print(f"  Original CAPTCHA URL: {captcha_url}")
                    return "no_https_proxy"
                tunnel_proxy = https_proxies[0]
                print(f"  ✅ HTTPS прокси найден: {tunnel_proxy[1]}:{tunnel_proxy[2]}")

                # Start reverse proxy tunnel
                tunnel = start_reverse_proxy(local_port, tunnel_proxy)
                local_url = f"http://127.0.0.1:{local_port}{captcha_path}"

                print(f"  🚀 Туннель запущен на порту {local_port}")
                print(f"\n  ╔══════════════════════════════════════════════════╗")
                print(f"  ║  ОТКРОЙ ЭТУ ССЫЛКУ В БРАУЗЕРЕ:                  ║")
                print(f"  ╚══════════════════════════════════════════════════╝")
                print(f"\n  👉 {local_url}\n")
                print(f"  IP трафика = IP аккаунта (через прокси)")
                print(f"  После прохождения CAPTCHA запусти: python scripts/appeal_with_tunnel.py --phone {phone} --phase done")

                # Save state for done phase
                state_file = PROJECT_ROOT / "data" / f"captcha_state_{phone}.json"
                state_file.write_text(json.dumps({
                    "phone": phone,
                    "local_port": local_port,
                    "captcha_url": captcha_url,
                    "local_url": local_url,
                    "proxy_idx": None,
                }))

                # Keep tunnel alive — this blocks until killed
                print(f"\n  🔄 Туннель работает. Ctrl+C чтобы остановить.")
                try:
                    while True:
                        await asyncio.sleep(1)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    pass
                finally:
                    tunnel.shutdown()
                    print(f"\n  🔌 Туннель остановлен")
                return "captcha_tunnel_running"

            if msg.buttons:
                button = pick_button(msg)
                if button is not None:
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


async def run_all(phones, email, reg_year, base_port):
    for i, phone in enumerate(phones):
        port = base_port + i
        try:
            result = await run_appeal_with_tunnel(phone, email, reg_year, port)
        except (ConnectionError, OSError) as e:
            print(f"  ❌ Ошибка подключения для {phone}: {e}")
            result = "connection_error"
        print(f"\n  ➡️  {phone}: {result}")
        if i < len(phones) - 1:
            print(f"\n  ⏱ Пауза 10с...")
            await asyncio.sleep(10)

    print(f"\n{'='*60}")
    print(f"  ГОТОВО")
    print(f"{'='*60}")


def get_frozen_phones():
    sessions_dir = PROJECT_ROOT / "data" / "sessions"
    phones = []
    for jf in sorted(sessions_dir.glob("*.json")):
        if jf.stem.startswith("_") or not jf.stem.isdigit():
            continue
        phones.append(jf.stem)
    dead = {"79637429684"}
    alive = {"79637430838"}
    return [p for p in phones if p not in dead and p not in alive]


async def click_done(phone: str):
    """Phase 2: connect, click Done, check result."""
    print(f"\n  📤 Подключаюсь к {phone} чтобы нажать Done...")
    data = load_account_json(phone)
    client, proxy = await try_connect_with_retry(phone, data)
    if not client:
        return "connection_failed"

    try:
        if not await client.is_user_authorized():
            print(f"  ❌ Не авторизован")
            return "unauthorized"

        entity = await client.get_entity("SpamBot")
        msgs = await client.get_messages(entity, limit=5)

        # Find the last message with Done button
        for msg in msgs:
            if not msg.buttons:
                continue
            for row in msg.buttons:
                for btn in row:
                    if "done" in (btn.text or "").lower():
                        print(f"  🖱 Нажимаю Done...")
                        await btn.click()
                        await asyncio.sleep(5)

                        # Check result
                        new_msgs = await client.get_messages(entity, limit=3)
                        for nm in new_msgs:
                            if nm.id <= msg.id or nm.out:
                                continue
                            rtext = (nm.text or "").lower()
                            print(f"  📩 SpamBot: {nm.text[:300]}")

                            if any(k in rtext for k in ("successfully submitted", "on review", "will check")):
                                print(f"\n  🎉 АПЕЛЛЯЦИЯ ПОДАНА УСПЕШНО!")
                                return "appeal_submitted"
                            elif any(k in rtext for k in ("no limits", "good news", "free")):
                                print(f"\n  🎉 АККАУНТ РАЗМОРОЖЕН!")
                                return "account_free"
                            elif "verify" in rtext:
                                print(f"\n  ⚠️ SpamBot просит верификацию снова")
                                return "captcha_retry"
                        return "done_pressed"

        print(f"  ❌ Кнопка Done не найдена")
        return "no_done_button"
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", help="Single phone")
    parser.add_argument("--all", action="store_true", help="All frozen")
    parser.add_argument("--phase", choices=["appeal", "done"], default="appeal")
    parser.add_argument("--email", default="braslavskii1717@gmail.com")
    parser.add_argument("--reg-year", default="2024")
    parser.add_argument("--base-port", type=int, default=18080)
    args = parser.parse_args()

    if args.phase == "done":
        if not args.phone:
            print("--phone обязателен для --phase done")
            return
        result = asyncio.run(click_done(args.phone.replace("+", "")))
        print(f"\n  Результат: {result}")
        return

    if args.all:
        phones = get_frozen_phones()
        if not phones:
            print("Нет замороженных аккаунтов")
            return
    elif args.phone:
        phones = [args.phone.replace("+", "")]
    else:
        parser.print_help()
        return

    asyncio.run(run_all(phones, args.email, args.reg_year, args.base_port))


if __name__ == "__main__":
    main()
