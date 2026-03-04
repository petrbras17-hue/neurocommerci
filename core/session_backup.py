"""
SessionBackupManager — экспорт и восстановление сессий через StringSession.

Защищает от потери .session файлов (коррупция, миграция, удаление).
НЕ защищает от отзыва auth key сервером — если Telegram отозвал ключ, бэкап бесполезен.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from config import settings, BASE_DIR
from utils.helpers import utcnow
from utils.logger import log

if TYPE_CHECKING:
    from telethon import TelegramClient

BACKUP_DIR = BASE_DIR / "data" / "session_backups"


class SessionBackupManager:
    """Экспорт/восстановление StringSession с опциональным шифрованием."""

    def __init__(self):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        self._fernet = None
        self._init_encryption()

    def _init_encryption(self) -> None:
        """Инициализировать Fernet если ключ задан."""
        key = settings.SESSION_BACKUP_KEY
        if not key:
            return
        try:
            from cryptography.fernet import Fernet
            # Ключ должен быть 32 url-safe base64 bytes
            if len(key) < 32:
                # Генерируем ключ из строки (padding)
                padded = base64.urlsafe_b64encode(key.ljust(32)[:32].encode())
                self._fernet = Fernet(padded)
            else:
                self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
            log.info("SessionBackup: шифрование включено")
        except ImportError:
            log.warning("SessionBackup: cryptography не установлен, бэкапы без шифрования")
        except Exception as exc:
            log.warning(f"SessionBackup: ошибка инициализации шифрования: {exc}")

    async def export(self, phone: str, client: TelegramClient) -> bool:
        """Экспортировать StringSession для аккаунта."""
        try:
            from telethon.sessions import StringSession
            string_session = StringSession.save(client.session)

            data = string_session.encode("utf-8")
            if self._fernet:
                data = self._fernet.encrypt(data)

            backup_path = BACKUP_DIR / f"{phone}.backup"
            backup_path.write_bytes(data)

            # Обновить timestamp в БД
            await self._update_backup_time(phone)

            log.debug(f"SessionBackup: экспортирован {phone}")
            return True

        except Exception as exc:
            log.error(f"SessionBackup: ошибка экспорта {phone}: {exc}")
            return False

    def restore(self, phone: str) -> Optional[str]:
        """Восстановить StringSession из бэкапа. Возвращает строку сессии."""
        backup_path = BACKUP_DIR / f"{phone}.backup"
        try:
            data = backup_path.read_bytes()
            if self._fernet:
                data = self._fernet.decrypt(data)

            return data.decode("utf-8")

        except FileNotFoundError:
            log.warning(f"SessionBackup: бэкап {phone} не найден")
            return None
        except Exception as exc:
            log.error(f"SessionBackup: ошибка восстановления {phone}: {exc}")
            return None

    async def export_all(self, session_manager) -> dict:
        """Экспортировать все подключённые аккаунты. Для scheduled job."""
        phones = session_manager.get_connected_phones()
        results = {"exported": 0, "failed": 0}

        for phone in phones:
            client = session_manager.get_client(phone)
            if client and client.is_connected():
                ok = await self.export(phone, client)
                if ok:
                    results["exported"] += 1
                else:
                    results["failed"] += 1

        if results["exported"] > 0:
            log.info(
                f"SessionBackup: экспортировано {results['exported']}, "
                f"ошибок {results['failed']}"
            )

        return results

    def list_backups(self) -> list[str]:
        """Список телефонов, для которых есть бэкапы."""
        return [f.stem for f in BACKUP_DIR.glob("*.backup")]

    async def _update_backup_time(self, phone: str) -> None:
        """Обновить session_backup_at в БД."""
        try:
            from storage.sqlite_db import async_session
            from storage.models import Account
            from sqlalchemy import update

            async with async_session() as session:
                await session.execute(
                    update(Account)
                    .where(Account.phone == phone)
                    .values(session_backup_at=utcnow())
                )
                await session.commit()
        except Exception as exc:
            log.debug(f"SessionBackup: ошибка обновления timestamp {phone}: {exc}")
