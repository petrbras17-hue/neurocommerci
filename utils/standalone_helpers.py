"""
Shared utilities for standalone scripts (appeal_spambot, check_frozen, create_channel, etc.).

Centralizes proxy loading, account JSON loading, and TelegramClient construction
to avoid copy-pasting across scripts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from telethon import TelegramClient

from config import settings

SESSIONS_DIR = settings.sessions_path
PROXIES_FILE = Path("data/proxies.txt")


def load_proxy_for_phone(phone: str) -> tuple:
    """
    Загружает уникальный прокси для аккаунта (1 IP = 1 аккаунт).
    Индекс определяется по позиции phone в отсортированном списке сессий.
    Возвращает кортеж для Telethon: (type, host, port, rdns, user, pass).
    """
    lines = PROXIES_FILE.read_text().strip().split("\n")
    sessions = sorted(SESSIONS_DIR.glob("*.session"))
    phone_list = [f.stem for f in sessions if f.stem.isdigit()]
    try:
        index = phone_list.index(phone)
    except ValueError:
        index = 0
    if index >= len(lines):
        index = index % len(lines)
    parts = lines[index].strip().split(":")
    # Тип 3 = HTTP в Telethon/PySocks (корректно для Proxyverse)
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
