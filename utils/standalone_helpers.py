"""
Shared utilities for standalone scripts (appeal_spambot, check_frozen, create_channel, etc.).

Centralizes proxy loading, account JSON loading, and TelegramClient construction
to avoid copy-pasting across scripts.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from telethon import TelegramClient

from config import settings

_log = logging.getLogger(__name__)

SESSIONS_DIR = settings.sessions_path
PROXIES_FILE = Path("data/proxies.txt")


def _test_proxy(host: str, port: int, user: str, password: str, timeout: int = 10) -> bool:
    """Quick connectivity check via httpx (no subprocess, no command injection risk)."""
    try:
        import httpx
    except ImportError:
        import requests
        try:
            resp = requests.get(
                "http://api.ipify.org",
                proxies={"http": f"http://{user}:{password}@{host}:{port}"},
                timeout=timeout,
            )
            return resp.status_code == 200
        except Exception:
            return False

    try:
        with httpx.Client(
            proxy=f"http://{user}:{password}@{host}:{port}",
            timeout=timeout,
        ) as client:
            resp = client.get("http://api.ipify.org")
            return resp.status_code == 200
    except Exception:
        return False


def load_proxy_for_phone(phone: str) -> tuple:
    """
    Загружает рабочий прокси для аккаунта (1 IP = 1 аккаунт).
    Сначала пробует прокси по индексу, если мёртв — ищет следующий рабочий.
    Возвращает кортеж для Telethon: (type, host, port, rdns, user, pass).
    """
    lines = PROXIES_FILE.read_text().strip().split("\n")
    sessions = sorted(SESSIONS_DIR.glob("*.session"))
    phone_list = [f.stem for f in sessions if f.stem.isdigit()]
    try:
        start_index = phone_list.index(phone)
    except ValueError:
        start_index = 0

    # Spread accounts across proxy pool to maintain 1:1 isolation
    pool_size = len(lines)
    stride = max(1, pool_size // max(1, len(phone_list)))

    max_tests = 30
    tested = 0
    for offset in range(pool_size):
        idx = (start_index * stride + offset) % pool_size
        parts = lines[idx].strip().split(":")
        if len(parts) < 4:
            continue
        host, port_str, user, password = parts[0], parts[1], parts[2], parts[3]
        tested += 1
        if _test_proxy(host, int(port_str), user, password):
            _log.info("Proxy #%d alive for %s", idx, phone)
            return (3, host, int(port_str), True, user, password)
        else:
            _log.warning("Proxy #%d dead for %s", idx, phone)
        if tested >= max_tests:
            break

    # Fallback: use untested proxy from far end of pool
    fallback_idx = (start_index * stride + pool_size // 2) % pool_size
    parts = lines[fallback_idx].strip().split(":")
    if len(parts) < 4:
        _log.error("All %d proxies dead and fallback #%d malformed for %s", tested, fallback_idx, phone)
        parts = lines[0].strip().split(":")
    _log.warning("All %d proxies dead, fallback #%d for %s", tested, fallback_idx, phone)
    return (3, parts[0], int(parts[1]), True, parts[2], parts[3])


def load_account_json(phone: str) -> dict:
    """Загружает JSON-метаданные аккаунта из data/sessions/{phone}.json."""
    json_path = SESSIONS_DIR / f"{phone}.json"
    if not json_path.exists():
        print(f"  Файл {json_path} не найден!")
        sys.exit(1)
    return json.loads(json_path.read_text())


def build_client(phone: str, data: dict, proxy: tuple) -> TelegramClient:
    """
    Создаёт TelegramClient с полным device fingerprint из JSON-метаданных.
    Параметры берутся из data (JSON) с фоллбэком на settings / дефолты.
    """
    return TelegramClient(
        str(SESSIONS_DIR / phone),
        api_id=data.get("app_id") or settings.TELEGRAM_API_ID,
        api_hash=data.get("app_hash") or settings.TELEGRAM_API_HASH,
        proxy=proxy,
        device_model=data.get("device", "Samsung Galaxy S23"),
        system_version=data.get("sdk", "SDK 29"),
        app_version=data.get("app_version", "12.4.3"),
        lang_code=data.get("lang_pack", "ru"),
        system_lang_code=data.get("system_lang_pack", "ru-ru"),
        timeout=30,
        connection_retries=5,
        retry_delay=5,
    )
