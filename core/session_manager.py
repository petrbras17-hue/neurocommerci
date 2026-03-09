from __future__ import annotations

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
from telethon.errors import FloodWaitError, AuthKeyUnregisteredError

from config import settings
from core.proxy_manager import ProxyConfig
from core.policy_engine import policy_engine
from core.account_capabilities import (
    probe_account_capabilities,
    persist_probe_result,
)
from utils.logger import log
from utils.proxy_bindings import get_live_proxy_config
from utils.session_topology import canonical_session_dir, canonical_session_paths, session_stem

# Дефолтные параметры устройства (если JSON отсутствует)
DEFAULT_DEVICE = {
    "device": "Samsung Galaxy S23",
    "sdk": "Android 14",
    "app_version": "10.8.3",
    "lang_pack": "ru",
    "system_lang_pack": "ru",
}

class SessionManager:
    """Фабрика Telethon клиентов с LRU pool и per-account device fingerprint."""

    def __init__(self):
        self._clients: dict[str, TelegramClient] = {}  # phone -> client
        self._device_cache: dict[str, dict] = {}  # phone -> device params
        self._last_used: dict[str, float] = {}  # phone -> timestamp последнего использования
        self._phone_user: dict[str, int] = {}  # phone -> user_id (for per-user isolation)
        self._known_user_dirs: set[int] = set()  # user_ids whose dirs already exist
        self._session_owner: dict[str, str] = {}  # session_name -> phone

    def _remember_user_id(self, phone: str, user_id: int | None) -> int | None:
        if user_id is None:
            return self._phone_user.get(phone)
        resolved = int(user_id)
        self._phone_user[phone] = resolved
        return resolved

    def get_known_user_id(self, phone: str) -> int | None:
        return self._phone_user.get(phone)

    def get_session_paths(self, phone: str, user_id: int | None = None) -> tuple[Path, Path]:
        """Return canonical runtime session/json paths for the account."""
        if user_id is None:
            raise ValueError("user_id_required_for_runtime_session_path")
        stem = session_stem(phone)
        return canonical_session_paths(settings.sessions_path, int(user_id), stem)

    def _load_device_params(self, session_name: str, user_id: int = None) -> dict:
        """Загрузить параметры устройства из JSON-файла поставщика."""
        cache_key = f"{user_id or 0}:{session_name}"
        if cache_key in self._device_cache:
            return self._device_cache[cache_key]

        _, json_path = self.get_session_paths(session_name, user_id=user_id)

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
        session_file_path, _ = self.get_session_paths(session_name, user_id=user_id)
        if not session_file_path.exists():
            raise FileNotFoundError(
                f"Session file not found for {session_name} (user_id={user_id}): {session_file_path}"
            )
        session_path = str(session_file_path.with_suffix(""))

        proxy_tuple = proxy.to_telethon_proxy() if proxy else None

        # Загрузить per-account device params
        device = self._load_device_params(session_name, user_id)

        # Использовать api_id/api_hash из JSON если есть (важно для купленных аккаунтов!)
        api_id = device.get("app_id")
        api_hash = device.get("app_hash")
        if api_id in (None, "") or not str(api_hash or "").strip():
            raise ValueError(
                f"Account {session_name} requires app_id/app_hash from its own JSON metadata"
            )

        if api_id == 4:
            log.warning(
                f"ВНИМАНИЕ: аккаунт {session_name} использует API ID 4 (помечен Telegram)! "
                "Сессия не может быть перенесена — используйте осторожно."
            )

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
        max_concurrent = max(1, settings.MAX_CONNECTED_CLIENTS_PER_WORKER)
        if len(self._clients) < max_concurrent:
            return
        if not self._last_used:
            return
        oldest_phone = min(self._last_used, key=self._last_used.get)
        log.debug(f"LRU eviction: отключаем {oldest_phone} (лимит {max_concurrent})")
        await self.disconnect_client(oldest_phone)

    def _touch(self, phone: str):
        """Обновить timestamp последнего использования."""
        self._last_used[phone] = time.time()

    async def _resolve_live_proxy(
        self,
        phone: str,
        *,
        user_id: int | None,
        proxy: Optional[ProxyConfig],
    ) -> tuple[Optional[ProxyConfig], dict]:
        try:
            live_proxy, proxy_report = await get_live_proxy_config(phone, user_id=user_id)
        except Exception as exc:
            live_proxy = None
            proxy_report = {"ok": False, "reason": f"proxy_preflight_error:{exc.__class__.__name__}"}
        if live_proxy is not None:
            return live_proxy, proxy_report
        return proxy, proxy_report

    async def _connect_managed_client(
        self,
        phone: str,
        proxy: Optional[ProxyConfig] = None,
        user_id: int = None,
        *,
        run_connect_probe: bool,
    ) -> Optional[TelegramClient]:
        user_id = self._remember_user_id(phone, user_id)
        if user_id is None:
            log.warning(f"{phone}: connect blocked, user_id is required for canonical session runtime")
            return None
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
            try:
                await client.disconnect()
            except Exception:
                pass
            del self._clients[phone]
            self._last_used.pop(phone, None)

        await self._evict_lru()

        proxy, proxy_report = await self._resolve_live_proxy(phone, user_id=user_id, proxy=proxy)
        if proxy is None and settings.STRICT_PROXY_PER_ACCOUNT:
            log.warning(
                f"{phone}: connect blocked, no live unique proxy "
                f"({proxy_report.get('reason') or 'unknown'})"
            )
            return None

        session_name = session_stem(phone)
        current_owner = self._session_owner.get(session_name)
        if current_owner and current_owner != phone:
            await policy_engine.check(
                "session_duplicate_detected",
                {"duplicate": True, "session_name": session_name, "owner": current_owner, "phone": phone},
                phone=phone,
            )
            log.warning(
                f"Сессия {session_name} уже занята аккаунтом {current_owner}; "
                f"подключение {phone} отклонено"
            )
            return None
        client = self.create_client(session_name, proxy, user_id=user_id)

        try:
            await client.connect()
            if not await client.is_user_authorized():
                log.warning(f"Аккаунт {phone} не авторизован. Нужна авторизация.")
                await client.disconnect()
                return None

            me = await client.get_me()
            log.info(f"Подключен аккаунт: {me.first_name} ({phone})")

            if run_connect_probe and settings.FROZEN_PROBE_ON_CONNECT:
                probe = await probe_account_capabilities(client, run_search_probe=True)
                reason = str(probe.get("reason", "ok"))
                mark_restricted = reason in {"frozen", "restricted"}
                await persist_probe_result(
                    phone,
                    probe,
                    mark_restricted_on_failure=mark_restricted,
                    restriction_reason=reason if mark_restricted else None,
                )
                if mark_restricted:
                    await policy_engine.check(
                        "frozen_probe_failed",
                        {
                            "phone": phone,
                            "reason": reason,
                            "capabilities": probe,
                            "source": "connect_client",
                        },
                        phone=phone,
                    )
                    log.warning(
                        f"{phone}: capability probe blocked account on connect (reason={reason})"
                    )
                    await client.disconnect()
                    return None

            self._clients[phone] = client
            self._session_owner[session_name] = phone
            self._touch(phone)
            return client

        except FloodWaitError as e:
            wait = int(e.seconds * 1.5)
            log.warning(f"FloodWait при подключении {phone}: ждать {e.seconds}с (cooldown {wait}с)")
            try:
                await client.disconnect()
            except Exception:
                pass
            raise

        except AuthKeyUnregisteredError:
            log.critical(
                f"СЕССИЯ МЕРТВА: {phone} — auth key отозван Telegram. "
                "Восстановление невозможно без повторной авторизации (SMS)."
            )
            try:
                await client.disconnect()
            except Exception:
                pass
            return None

        except Exception as e:
            log.error(f"Ошибка подключения {phone}: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            return None

    async def connect_client(
        self,
        phone: str,
        proxy: Optional[ProxyConfig] = None,
        user_id: int = None,
    ) -> Optional[TelegramClient]:
        """Подключить клиент и проверить авторизацию."""
        return await self._connect_managed_client(
            phone,
            proxy=proxy,
            user_id=user_id,
            run_connect_probe=True,
        )

    async def connect_client_for_action(
        self,
        phone: str,
        proxy: Optional[ProxyConfig] = None,
        user_id: int = None,
    ) -> Optional[TelegramClient]:
        """Подключить клиент для ручного apply-шага без hidden capability/search probe."""
        return await self._connect_managed_client(
            phone,
            proxy=proxy,
            user_id=user_id,
            run_connect_probe=False,
        )

    async def probe_authorization(
        self,
        phone: str,
        proxy: Optional[ProxyConfig] = None,
        user_id: int = None,
    ) -> tuple[bool, str]:
        """Проверить авторизацию сессии без side effects policy/runtime.

        Используется в админ-проверках: не трогает lifecycle, не пишет policy events,
        не удерживает постоянное соединение в пуле.
        """
        user_id = self._remember_user_id(phone, user_id)
        if user_id is None:
            return False, "user_id_required"
        proxy, proxy_report = await self._resolve_live_proxy(phone, user_id=user_id, proxy=proxy)
        if proxy is None and settings.STRICT_PROXY_PER_ACCOUNT:
            return False, str(proxy_report.get("reason") or "proxy_unavailable")

        session_name = session_stem(phone)
        client = self.create_client(session_name, proxy, user_id=user_id)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return False, "unauthorized"
            return True, "authorized"
        except FloodWaitError as exc:
            return False, f"flood_wait_{exc.seconds}s"
        except AuthKeyUnregisteredError:
            return False, "auth_key_unregistered"
        except Exception as exc:
            return False, f"error:{exc.__class__.__name__}"
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def disconnect_client(self, phone: str):
        """Отключить клиент."""
        client = self._clients.pop(phone, None)
        self._last_used.pop(phone, None)
        self._phone_user.pop(phone, None)
        session_name = session_stem(phone)
        owner = self._session_owner.get(session_name)
        if owner == phone:
            self._session_owner.pop(session_name, None)
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
        session_name = session_stem(phone)
        return self._load_device_params(session_name, user_id)

    def list_session_files(self, user_id: int = None) -> list[str]:
        """Список .session файлов в директории сессий."""
        sessions_dir = self._get_sessions_dir(user_id)
        return [f.stem for f in sessions_dir.glob("*.session")]

    @property
    def pool_stats(self) -> dict:
        """Статистика пула соединений."""
        max_concurrent = max(1, settings.MAX_CONNECTED_CLIENTS_PER_WORKER)
        return {
            "connected": len(self._clients),
            "max_concurrent": max_concurrent,
            "cached_devices": len(self._device_cache),
        }
