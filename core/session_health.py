"""
SessionHealthMonitor — фоновый мониторинг здоровья Telegram сессий.

Периодически проверяет все подключённые аккаунты через get_me().
Обнаруживает AuthKeyUnregisteredError (сессия отозвана) в течение часов, не дней.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

from telethon.errors import (
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
    UserDeactivatedError,
)

from config import settings
from utils.helpers import utcnow
from utils.logger import log

if TYPE_CHECKING:
    from core.session_manager import SessionManager
    from utils.notifier import TelegramNotifier


_MAX_ERROR_STREAK_ENTRIES = 5000


class SessionHealthMonitor:
    """Фоновый мониторинг здоровья сессий."""

    def __init__(
        self,
        session_manager: SessionManager,
        notifier: TelegramNotifier,
    ):
        self._session_mgr = session_manager
        self._notifier = notifier
        self._task: asyncio.Task | None = None
        self._last_cycle = {"alive": 0, "dead": 0, "total": 0}
        self._error_streaks: dict[str, int] = {}

    async def start(self) -> None:
        """Запустить фоновый мониторинг."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._monitor_loop(), name="session_health_monitor")
        log.info("SessionHealthMonitor запущен")

    async def stop(self) -> None:
        """Остановить мониторинг."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("SessionHealthMonitor остановлен")

    async def check_one(self, phone: str) -> str:
        """
        Проверить одну сессию. Возвращает статус: 'alive', 'dead', 'banned', 'disconnected'.
        """
        client = self._session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return "disconnected"

        try:
            await client.get_me()
            return "alive"

        except AuthKeyUnregisteredError:
            log.critical(f"СЕССИЯ МЕРТВА: {phone} — AuthKeyUnregisteredError")
            await self._handle_dead_session(phone, "AuthKeyUnregistered")
            return "dead"

        except UserDeactivatedBanError:
            log.critical(f"АККАУНТ ЗАБАНЕН: {phone} — UserDeactivatedBanError")
            await self._handle_dead_session(phone, "UserDeactivatedBan")
            return "banned"

        except UserDeactivatedError:
            log.critical(f"АККАУНТ УДАЛЁН: {phone} — UserDeactivatedError")
            await self._handle_dead_session(phone, "UserDeactivated")
            return "dead"

        except Exception as exc:
            log.warning(f"Health check ошибка {phone}: {exc}")
            return "error"

    async def check_all(self, user_id: int = None) -> dict[str, str]:
        """Проверить все подключённые аккаунты. Возвращает {phone: status}."""
        phones = self._session_mgr.get_connected_phones(user_id=user_id)
        results: dict[str, str] = {}

        for phone in phones:
            status = await self.check_one(phone)
            results[phone] = status
            # Задержка между проверками (антифлуд)
            await asyncio.sleep(random.uniform(2, 5))

        # Отправить сводку если есть мёртвые
        alive = sum(1 for s in results.values() if s == "alive")
        dead = sum(1 for s in results.values() if s in ("dead", "banned"))
        unknown = sum(1 for s in results.values() if s not in ("alive", "dead", "banned"))

        if dead > 0:
            await self._notifier.health_report(alive, dead, unknown)

        return results

    async def _handle_dead_session(self, phone: str, error_type: str) -> None:
        """Обработать мёртвую сессию: обновить БД + уведомить."""
        # Обновить статус в БД
        try:
            from storage.sqlite_db import async_session
            from storage.models import Account
            from sqlalchemy import update

            async with async_session() as session:
                await session.execute(
                    update(Account)
                    .where(Account.phone == phone)
                    .values(
                        health_status="dead",
                        status="dead",
                        last_health_check=utcnow(),
                    )
                )
                await session.commit()
        except Exception as exc:
            log.error(f"Ошибка обновления статуса {phone}: {exc}")

        # Отключить клиент
        await self._session_mgr.disconnect_client(phone)

        # Уведомить админа
        await self._notifier.session_dead(phone, error_type)

    async def _update_health_status(self, phone: str, status: str) -> None:
        """Обновить last_health_check и health_status в БД."""
        try:
            from storage.sqlite_db import async_session
            from storage.models import Account
            from sqlalchemy import update

            async with async_session() as session:
                await session.execute(
                    update(Account)
                    .where(Account.phone == phone)
                    .values(
                        health_status=status,
                        last_health_check=utcnow(),
                    )
                )
                await session.commit()
        except Exception as exc:
            log.debug(f"Ошибка обновления health_status для {phone}: {exc}")

    async def _escalate_health_issue(self, phone: str) -> None:
        """Escalate repeated health issues: active -> cooldown -> restricted."""
        # Cap dict size to prevent unbounded memory growth
        if len(self._error_streaks) >= _MAX_ERROR_STREAK_ENTRIES and phone not in self._error_streaks:
            # Evict zero-streak entries first
            zeros = [p for p, v in self._error_streaks.items() if v == 0]
            for p in zeros:
                del self._error_streaks[p]
            # If still over limit, evict oldest entries
            if len(self._error_streaks) >= _MAX_ERROR_STREAK_ENTRIES:
                to_remove = list(self._error_streaks.keys())[: len(self._error_streaks) // 4]
                for p in to_remove:
                    del self._error_streaks[p]
        streak = self._error_streaks.get(phone, 0) + 1
        self._error_streaks[phone] = streak
        try:
            from storage.sqlite_db import async_session
            from storage.models import Account, AccountStageEvent
            from sqlalchemy import update, select

            async with async_session() as session:
                result = await session.execute(select(Account).where(Account.phone == phone))
                account = result.scalar_one_or_none()
                if account is None:
                    return
                old_stage = account.lifecycle_stage

                if streak >= 3:
                    await session.execute(
                        update(Account)
                        .where(Account.phone == phone)
                        .values(
                            status="error",
                            health_status="restricted",
                            lifecycle_stage="restricted",
                            restriction_reason="health_error_streak",
                            last_health_check=utcnow(),
                        )
                    )
                    if old_stage != "restricted":
                        session.add(
                            AccountStageEvent(
                                account_id=account.id,
                                phone=phone,
                                from_stage=old_stage,
                                to_stage="restricted",
                                actor="session_health",
                                reason=f"error_streak={streak}",
                            )
                        )
                else:
                    await session.execute(
                        update(Account)
                        .where(Account.phone == phone)
                        .values(
                            status="cooldown",
                            health_status="unknown",
                            last_health_check=utcnow(),
                        )
                    )
                await session.commit()
        except Exception as exc:
            log.debug(f"Ошибка health escalation для {phone}: {exc}")

    async def _monitor_loop(self) -> None:
        """Фоновый цикл: проверять все аккаунты каждые N часов."""
        interval = settings.SESSION_HEALTH_CHECK_HOURS * 3600

        # Первая проверка через 5 минут после старта
        await asyncio.sleep(300)

        while True:
            try:
                phones = self._session_mgr.get_connected_phones()
                if not phones:
                    await asyncio.sleep(interval)
                    continue

                log.info(f"SessionHealth: проверка {len(phones)} аккаунтов...")

                cycle_alive = 0
                cycle_dead = 0
                for phone in phones:
                    status = await self.check_one(phone)
                    if status == "alive":
                        self._error_streaks[phone] = 0
                        cycle_alive += 1
                        await self._update_health_status(phone, "alive")
                    elif status in ("dead", "banned"):
                        cycle_dead += 1
                    elif status in ("error", "disconnected"):
                        await self._escalate_health_issue(phone)
                    # Задержка между проверками
                    await asyncio.sleep(random.uniform(3, 8))

                self._last_cycle = {"alive": cycle_alive, "dead": cycle_dead, "total": len(phones)}
                log.info(
                    f"SessionHealth: проверено {len(phones)} — "
                    f"alive={cycle_alive}, dead={cycle_dead}"
                )

                # Ждать до следующего цикла
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(f"Ошибка в session_health_monitor: {exc}")
                await asyncio.sleep(600)

    @property
    def stats(self) -> dict:
        return dict(self._last_cycle)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()
