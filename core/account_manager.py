"""
Менеджер аккаунтов — пул, ротация, статусы.
"""

import asyncio
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.proxy_manager import ProxyManager
from core.session_manager import SessionManager
from core.rate_limiter import RateLimiter
from storage.models import Account
from storage.sqlite_db import async_session
from utils.logger import log


class AccountManager:
    """Управление пулом Telegram аккаунтов."""

    def __init__(
        self,
        session_manager: SessionManager,
        proxy_manager: ProxyManager,
        rate_limiter: RateLimiter,
    ):
        self.session_mgr = session_manager
        self.proxy_mgr = proxy_manager
        self.rate_limiter = rate_limiter
        self._rotation_index = 0
        self._lock = asyncio.Lock()  # Защита от race condition при выборе аккаунта

    async def load_accounts(self) -> list[Account]:
        """Загрузить все аккаунты из БД."""
        async with async_session() as session:
            result = await session.execute(
                select(Account).where(Account.status != "banned")
            )
            accounts = result.scalars().all()
            log.info(f"Загружено аккаунтов из БД: {len(accounts)}")
            return list(accounts)

    async def add_account(self, phone: str, session_file: str) -> Account:
        """Добавить новый аккаунт в БД."""
        async with async_session() as session:
            account = Account(
                phone=phone,
                session_file=session_file,
                status="active",
                created_at=datetime.utcnow(),
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            log.info(f"Добавлен аккаунт: {phone}")
            return account

    async def connect_all(self) -> dict[str, str]:
        """Подключить все активные аккаунты. Возвращает {phone: status}."""
        from telethon.errors import FloodWaitError

        accounts = await self.load_accounts()
        results = {}

        for account in accounts:
            if account.status == "banned":
                results[account.phone] = "banned"
                continue

            proxy = self.proxy_mgr.assign_to_account(account.phone)
            try:
                client = await self.session_mgr.connect_client(account.phone, proxy)
                if client:
                    results[account.phone] = "connected"
                else:
                    results[account.phone] = "failed"
                    await self._update_status(account.phone, "error")
            except FloodWaitError as e:
                results[account.phone] = f"flood_wait_{e.seconds}s"
                await self.handle_error(account.phone, "flood_wait", str(e.seconds))
            except Exception as e:
                results[account.phone] = "failed"
                await self._update_status(account.phone, "error")
                log.error(f"Ошибка подключения {account.phone}: {e}")

        return results

    async def get_next_available(self) -> Optional[Account]:
        """Получить следующий доступный аккаунт для комментирования (round-robin).

        Используем asyncio.Lock для защиты от race condition:
        без него два concurrent вызова могут выбрать один и тот же аккаунт.
        """
        async with self._lock:
            accounts = await self.load_accounts()
            active = [a for a in accounts if a.status == "active"]

            if not active:
                log.warning("Нет активных аккаунтов")
                return None

            # Round-robin с проверкой лимитов
            for _ in range(len(active)):
                idx = self._rotation_index % len(active)
                self._rotation_index += 1
                account = active[idx]

                if self.rate_limiter.can_comment(account.phone, account.days_active):
                    # Проверить что клиент подключён
                    client = self.session_mgr.get_client(account.phone)
                    if client and client.is_connected():
                        return account
                    else:
                        log.debug(f"{account.phone}: клиент не подключён")

            log.warning("Все аккаунты на cooldown или отключены")
            return None

    async def record_comment(self, phone: str):
        """Зафиксировать комментарий: rate limiter + БД."""
        self.rate_limiter.record_comment(phone)
        async with async_session() as session:
            await session.execute(
                update(Account)
                .where(Account.phone == phone)
                .values(
                    comments_today=Account.comments_today + 1,
                    total_comments=Account.total_comments + 1,
                    last_active_at=datetime.utcnow(),
                )
            )
            await session.commit()

    async def handle_error(self, phone: str, error_type: str, details: str = ""):
        """Обработать ошибку аккаунта."""
        if error_type == "flood_wait":
            seconds = int(details) if details.isdigit() else 300
            self.rate_limiter.set_flood_wait(phone, seconds)
            await self._update_status(phone, "flood_wait")

        elif error_type == "banned":
            await self._update_status(phone, "banned")
            await self.session_mgr.disconnect_client(phone)
            log.error(f"Аккаунт ЗАБАНЕН: {phone}")

        elif error_type == "forbidden":
            self.rate_limiter.set_cooldown(phone, settings.COMMENT_COOLDOWN_AFTER_ERROR_SEC)
            await self._update_status(phone, "cooldown")

        else:
            self.rate_limiter.set_cooldown(phone, settings.COMMENT_COOLDOWN_AFTER_ERROR_SEC)
            await self._update_status(phone, "cooldown")

    async def _update_status(self, phone: str, status: str):
        """Обновить статус аккаунта в БД."""
        async with async_session() as session:
            await session.execute(
                update(Account)
                .where(Account.phone == phone)
                .values(status=status)
            )
            await session.commit()

    async def reset_daily_counters(self):
        """Сбросить дневные счётчики (вызывается в полночь)."""
        async with async_session() as session:
            await session.execute(
                update(Account).values(comments_today=0)
            )
            # Восстановить cooldown аккаунты
            await session.execute(
                update(Account)
                .where(Account.status == "cooldown")
                .values(status="active")
            )
            await session.commit()
        log.info("Дневные счётчики сброшены")

    async def get_status_summary(self) -> dict:
        """Сводка по всем аккаунтам."""
        accounts = await self.load_accounts()
        summary = {
            "total": len(accounts),
            "active": 0,
            "cooldown": 0,
            "banned": 0,
            "flood_wait": 0,
            "connected": 0,
            "total_comments_today": 0,
        }

        for acc in accounts:
            summary[acc.status] = summary.get(acc.status, 0) + 1
            summary["total_comments_today"] += acc.comments_today
            if self.session_mgr.get_client(acc.phone):
                summary["connected"] += 1

        return summary

    async def auto_recover(self) -> dict:
        """
        Авто-восстановление аккаунтов из flood_wait/cooldown/error.
        Проверяет прошло ли достаточно времени и пытается переподключить.

        Возвращает {"recovered": int, "still_blocked": int, "reconnected": int}.
        """
        recovered = 0
        still_blocked = 0
        reconnected = 0

        async with async_session() as session:
            result = await session.execute(
                select(Account).where(
                    Account.status.in_(["flood_wait", "cooldown", "error"])
                )
            )
            blocked = list(result.scalars().all())

        for account in blocked:
            phone = account.phone

            # Проверить через rate_limiter не истёк ли cooldown
            if self.rate_limiter.can_comment(phone, account.days_active):
                client = self.session_mgr.get_client(phone)

                if client and client.is_connected():
                    await self._update_status(phone, "active")
                    recovered += 1
                    log.info(f"{phone}: авто-восстановлен из {account.status}")
                else:
                    proxy = self.proxy_mgr.get_for_account(phone)
                    client = await self.session_mgr.connect_client(phone, proxy)
                    if client:
                        await self._update_status(phone, "active")
                        recovered += 1
                        reconnected += 1
                        log.info(f"{phone}: переподключён и восстановлен")
                    else:
                        still_blocked += 1
            else:
                still_blocked += 1

        if recovered > 0:
            log.info(
                f"Авто-восстановление: {recovered} восстановлено, "
                f"{reconnected} переподключено, {still_blocked} ещё заблокировано"
            )

        return {
            "recovered": recovered,
            "still_blocked": still_blocked,
            "reconnected": reconnected,
        }

    async def disconnect_all(self):
        """Отключить все аккаунты."""
        await self.session_mgr.disconnect_all()
