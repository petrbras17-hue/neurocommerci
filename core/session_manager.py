"""
Менеджер Telethon сессий.
Создание клиентов с прокси, управление подключениями.
"""

from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import settings
from core.proxy_manager import ProxyConfig
from utils.logger import log


class SessionManager:
    """Фабрика Telethon клиентов."""

    def __init__(self):
        self._clients: dict[str, TelegramClient] = {}  # phone -> client

    def create_client(
        self,
        session_name: str,
        proxy: Optional[ProxyConfig] = None,
    ) -> TelegramClient:
        """Создать Telethon клиент с опциональным прокси."""
        session_path = str(settings.sessions_path / session_name)

        proxy_tuple = proxy.to_telethon_proxy() if proxy else None

        client = TelegramClient(
            session_path,
            api_id=settings.TELEGRAM_API_ID,
            api_hash=settings.TELEGRAM_API_HASH,
            proxy=proxy_tuple,
            device_model="Samsung Galaxy S23",
            system_version="Android 14",
            app_version="10.8.3",
            lang_code="ru",
            system_lang_code="ru",
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

    def list_session_files(self) -> list[str]:
        """Список .session файлов в директории сессий."""
        sessions_dir = settings.sessions_path
        return [
            f.stem for f in sessions_dir.glob("*.session")
        ]
