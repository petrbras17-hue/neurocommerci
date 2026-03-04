"""
Менеджер Telethon сессий.
Создание клиентов с прокси, управление подключениями.
Читает JSON-метаданные от поставщика для device fingerprint каждого аккаунта.
LRU connection pool для масштабирования до 1000 аккаунтов.
"""

import json
import time
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from config import settings, BASE_DIR
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

# Максимум одновременных подключений (для 1000 аккаунтов нельзя держать все)
MAX_CONCURRENT_CONNECTIONS = 50


class SessionManager:
    """Фабрика Telethon клиентов с LRU pool и per-account device fingerprint."""

    def __init__(self):
        self._clients: dict[str, TelegramClient] = {}  # phone -> client
        self._device_cache: dict[str, dict] = {}  # phone -> device params
        self._last_used: dict[str, float] = {}  # phone -> timestamp последнего использования
        self._phone_user: dict[str, int] = {}  # phone -> user_id (for per-user isolation)
        self._known_user_dirs: set[int] = set()  # user_ids whose dirs already exist

    def _get_sessions_dir(self, user_id: int = None) -> Path:
        """Get sessions directory, optionally per-user."""
        base = settings.sessions_path
        if user_id is not None:
            user_dir = base / str(user_id)
            if user_id not in self._known_user_dirs:
                user_dir.mkdir(parents=True, exist_ok=True)
                self._known_user_dirs.add(user_id)
            return user_dir
        return base

    def _load_device_params(self, session_name: str, user_id: int = None) -> dict:
        """Загрузить параметры устройства из JSON-файла поставщика."""
        cache_key = f"{user_id or 0}:{session_name}"
        if cache_key in self._device_cache:
            return self._device_cache[cache_key]

        sessions_dir = self._get_sessions_dir(user_id)
        json_path = sessions_dir / f"{session_name}.json"

        # Fallback to flat directory for backward compatibility
        if not json_path.exists() and user_id is not None:
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
                self._device_cache[cache_key] = params
                log.debug(f"Device params для {session_name}: {params['device']}")
                return params
            except Exception as exc:
                log.warning(f"Ошибка чтения {json_path}: {exc}")

        self._device_cache[cache_key] = DEFAULT_DEVICE
        return DEFAULT_DEVICE

    def create_client(
        self,
        session_name: str,
        proxy: Optional[ProxyConfig] = None,
        user_id: int = None,
    ) -> TelegramClient:
        """Создать Telethon клиент с device fingerprint из JSON метаданных."""
        sessions_dir = self._get_sessions_dir(user_id)
        session_path = str(sessions_dir / session_name)

        # Fallback: if session file doesn't exist in user dir, check flat dir
        if user_id is not None and not (sessions_dir / f"{session_name}.session").exists():
            flat_path = settings.sessions_path / f"{session_name}.session"
            if flat_path.exists():
                session_path = str(settings.sessions_path / session_name)

        proxy_tuple = proxy.to_telethon_proxy() if proxy else None

        # Загрузить per-account device params
        device = self._load_device_params(session_name, user_id)

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
            flood_sleep_threshold=10,
            receive_updates=True,
            timeout=30,
            connection_retries=5,
        )

        return client

    async def _evict_lru(self):
        """Отключить наименее используемый клиент если превышен лимит."""
        if len(self._clients) < MAX_CONCURRENT_CONNECTIONS:
            return
        if not self._last_used:
            return
        oldest_phone = min(self._last_used, key=self._last_used.get)
        log.debug(f"LRU eviction: отключаем {oldest_phone} (лимит {MAX_CONCURRENT_CONNECTIONS})")
        await self.disconnect_client(oldest_phone)

    def _touch(self, phone: str):
        """Обновить timestamp последнего использования."""
        self._last_used[phone] = time.time()

    async def connect_client(
        self,
        phone: str,
        proxy: Optional[ProxyConfig] = None,
        user_id: int = None,
    ) -> Optional[TelegramClient]:
        """Подключить клиент и проверить авторизацию."""
        # АНТИБАН: запрет подключения нескольких аккаунтов с одного IP (без прокси)
        if proxy is None and len(self.get_connected_phones()) > 0:
            log.warning(
                "АНТИБАН: подключение без прокси запрещено при наличии других аккаунтов"
            )
            return None

        if phone in self._clients:
            client = self._clients[phone]
            if client.is_connected():
                self._touch(phone)
                return client
            # Старый клиент отключён — освободить ресурсы перед созданием нового
            try:
                await client.disconnect()
            except Exception:
                pass
            del self._clients[phone]
            self._last_used.pop(phone, None)

        # LRU eviction: освободить место если нужно
        await self._evict_lru()

        # Имя сессии = номер телефона (без +)
        session_name = phone.lstrip("+").replace(" ", "")
        client = self.create_client(session_name, proxy, user_id=user_id)

        try:
            await client.connect()
            if not await client.is_user_authorized():
                log.warning(f"Аккаунт {phone} не авторизован. Нужна авторизация.")
                await client.disconnect()
                return None

            me = await client.get_me()
            log.info(f"Подключен аккаунт: {me.first_name} ({phone})")
            self._clients[phone] = client
            self._touch(phone)
            if user_id is not None:
                self._phone_user[phone] = user_id
            return client

        except FloodWaitError as e:
            wait = int(e.seconds * 1.5)
            log.warning(f"FloodWait при подключении {phone}: ждать {e.seconds}с (cooldown {wait}с)")
            try:
                await client.disconnect()
            except Exception:
                pass
            raise

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
        self._last_used.pop(phone, None)
        self._phone_user.pop(phone, None)
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
        client = self._clients.get(phone)
        if client:
            self._touch(phone)
        return client

    def get_connected_phones(self, user_id: int = None) -> list[str]:
        """Список подключённых номеров, опционально фильтр по user_id."""
        phones = [p for p, c in self._clients.items() if c.is_connected()]
        if user_id is not None:
            phones = [p for p in phones if self._phone_user.get(p) == user_id]
        return phones

    def get_device_info(self, phone: str, user_id: int = None) -> dict:
        """Получить device params для аккаунта (для отображения в боте)."""
        session_name = phone.lstrip("+").replace(" ", "")
        return self._load_device_params(session_name, user_id)

    def list_session_files(self, user_id: int = None) -> list[str]:
        """Список .session файлов в директории сессий."""
        sessions_dir = self._get_sessions_dir(user_id)
        result = [f.stem for f in sessions_dir.glob("*.session")]
        # Also include flat directory files for backward compatibility
        if user_id is not None:
            flat_sessions = [f.stem for f in settings.sessions_path.glob("*.session")]
            result = list(set(result + flat_sessions))
        return result

    @property
    def pool_stats(self) -> dict:
        """Статистика пула соединений."""
        return {
            "connected": len(self._clients),
            "max_concurrent": MAX_CONCURRENT_CONNECTIONS,
            "cached_devices": len(self._device_cache),
        }
