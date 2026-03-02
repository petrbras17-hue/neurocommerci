"""
Менеджер Telethon сессий.
Создание клиентов с прокси, управление подключениями.
Читает JSON-метаданные от поставщика для device fingerprint каждого аккаунта.
"""

import json
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from config import settings
from core.proxy_manager import ProxyConfig
from utils.logger import log

# Дефолтные параметры устройства (если JSON отсутствует)
DEFAULT_DEVICE = {
    "device": "Samsung Galaxy S23",
    "sdk": "Android 14",
    "app_version": "10.8.3",
    "lang_pack": "ru",
    "system_lang_pack": "ru",
}


class SessionManager:
    """Фабрика Telethon клиентов с поддержкой per-account device fingerprint."""

    def __init__(self):
        self._clients: dict[str, TelegramClient] = {}  # phone -> client
        self._device_cache: dict[str, dict] = {}  # phone -> device params

    def _load_device_params(self, session_name: str) -> dict:
        """Загрузить параметры устройства из JSON-файла поставщика."""
        if session_name in self._device_cache:
            return self._device_cache[session_name]

        json_path = settings.sessions_path / f"{session_name}.json"
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                params = {
                    "device": data.get("device", DEFAULT_DEVICE["device"]),
                    "sdk": data.get("sdk", DEFAULT_DEVICE["sdk"]),
                    "app_version": data.get("app_version", DEFAULT_DEVICE["app_version"]),
                    "lang_pack": data.get("lang_pack", DEFAULT_DEVICE["lang_pack"]),
                    "system_lang_pack": data.get("system_lang_pack", DEFAULT_DEVICE["system_lang_pack"]),
                    "app_id": data.get("app_id"),
                    "app_hash": data.get("app_hash"),
                    "first_name": data.get("first_name"),
                    "twoFA": data.get("twoFA"),
                }
                self._device_cache[session_name] = params
                log.debug(f"Device params для {session_name}: {params['device']}")
                return params
            except Exception as exc:
                log.warning(f"Ошибка чтения {json_path}: {exc}")

        self._device_cache[session_name] = DEFAULT_DEVICE
        return DEFAULT_DEVICE

    def create_client(
        self,
        session_name: str,
        proxy: Optional[ProxyConfig] = None,
    ) -> TelegramClient:
        """Создать Telethon клиент с device fingerprint из JSON метаданных."""
        session_path = str(settings.sessions_path / session_name)
        proxy_tuple = proxy.to_telethon_proxy() if proxy else None

        # Загрузить per-account device params
        device = self._load_device_params(session_name)

        # Использовать api_id/api_hash из JSON если есть (важно для купленных аккаунтов!)
        api_id = device.get("app_id") or settings.TELEGRAM_API_ID
        api_hash = device.get("app_hash") or settings.TELEGRAM_API_HASH

        client = TelegramClient(
            session_path,
            api_id=api_id,
            api_hash=api_hash,
            proxy=proxy_tuple,
            device_model=device.get("device", DEFAULT_DEVICE["device"]),
            system_version=device.get("sdk", DEFAULT_DEVICE["sdk"]),
            app_version=device.get("app_version", DEFAULT_DEVICE["app_version"]),
            lang_code=device.get("lang_pack", "ru"),
            system_lang_code=device.get("system_lang_pack", "ru"),
        )

        return client

    async def connect_client(
        self,
        phone: str,
        proxy: Optional[ProxyConfig] = None,
    ) -> Optional[TelegramClient]:
        """Подключить клиент и проверить авторизацию."""
        if phone in self._clients:
            client = self._clients[phone]
            if client.is_connected():
                return client
            # Старый клиент отключён — освободить ресурсы перед созданием нового
            try:
                await client.disconnect()
            except Exception:
                pass
            del self._clients[phone]

        # Имя сессии = номер телефона (без +)
        session_name = phone.lstrip("+").replace(" ", "")
        client = self.create_client(session_name, proxy)

        try:
            await client.connect()
            if not await client.is_user_authorized():
                log.warning(f"Аккаунт {phone} не авторизован. Нужна авторизация.")
                await client.disconnect()
                return None

            me = await client.get_me()
            log.info(f"Подключен аккаунт: {me.first_name} ({phone})")
            self._clients[phone] = client
            return client

        except FloodWaitError as e:
            wait = int(e.seconds * 1.5)
            log.warning(f"FloodWait при подключении {phone}: ждать {e.seconds}с (cooldown {wait}с)")
            # Не ретраим сразу — вызывающий код обработает через account_mgr.handle_error
            try:
                await client.disconnect()
            except Exception:
                pass
            raise  # Пробрасываем наверх чтобы account_manager мог выставить cooldown

        except Exception as e:
            log.error(f"Ошибка подключения {phone}: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            return None

    async def disconnect_client(self, phone: str):
        """Отключить клиент."""
        client = self._clients.pop(phone, None)
        if client:
            try:
                await client.disconnect()
                log.info(f"Отключен: {phone}")
            except Exception as e:
                log.error(f"Ошибка отключения {phone}: {e}")

    async def disconnect_all(self):
        """Отключить все клиенты."""
        phones = list(self._clients.keys())
        for phone in phones:
            await self.disconnect_client(phone)

    def get_client(self, phone: str) -> Optional[TelegramClient]:
        """Получить подключённый клиент по номеру."""
        return self._clients.get(phone)

    def get_connected_phones(self) -> list[str]:
        """Список подключённых номеров."""
        return [p for p, c in self._clients.items() if c.is_connected()]

    def get_device_info(self, phone: str) -> dict:
        """Получить device params для аккаунта (для отображения в боте)."""
        session_name = phone.lstrip("+").replace(" ", "")
        return self._load_device_params(session_name)

    def list_session_files(self) -> list[str]:
        """Список .session файлов в директории сессий."""
        sessions_dir = settings.sessions_path
        return [
            f.stem for f in sessions_dir.glob("*.session")
        ]
