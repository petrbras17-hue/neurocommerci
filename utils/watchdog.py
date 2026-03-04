"""
Watchdog — фоновый мониторинг здоровья системы.

Проверяет:
- Живы ли per-user engine tasks (перезапуск при падении)
- Подключены ли клиенты (reconnect при отключении)
- Уведомляет админа о проблемах
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from utils.logger import log
from utils.notifier import notifier

if TYPE_CHECKING:
    from core.engine import CommentingEngine
    from core.session_manager import SessionManager


class Watchdog:
    """Фоновый мониторинг здоровья per-user задач."""

    CHECK_INTERVAL = 300  # 5 минут

    def __init__(
        self,
        engine: CommentingEngine,
        session_manager: SessionManager,
    ):
        self.engine = engine
        self.session_mgr = session_manager
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("Watchdog запущен")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._task = None
        log.info("Watchdog остановлен")

    async def _loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.CHECK_INTERVAL)
                await self._check_user_tasks()
                await self._check_connections()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error(f"Watchdog ошибка: {exc}")

    async def _check_user_tasks(self):
        """Проверить per-user engine tasks, перезапустить упавшие."""
        user_tasks = getattr(self.engine, "_user_tasks", {})
        for user_id, tasks in list(user_tasks.items()):
            for name, task in list(tasks.items()):
                if task.done():
                    exc = task.exception() if not task.cancelled() else None
                    log.warning(
                        f"Watchdog: задача {name} пользователя {user_id} упала: {exc}"
                    )
                    try:
                        await self.engine.start_for_user(user_id)
                        await notifier.send(
                            f"🔄 Watchdog перезапустил {name} для user {user_id}"
                        )
                        log.info(f"Watchdog: перезапущены задачи user {user_id}")
                    except Exception as restart_exc:
                        log.error(
                            f"Watchdog: не удалось перезапустить user {user_id}: {restart_exc}"
                        )
                    break  # start_for_user перезапускает все задачи

    async def _check_connections(self):
        """Проверить подключения клиентов и валидность auth key."""
        from telethon.errors import AuthKeyUnregisteredError, UserDeactivatedBanError

        phones = self.session_mgr.get_connected_phones()
        disconnected = []
        for phone in phones:
            client = self.session_mgr.get_client(phone)
            if client and not client.is_connected():
                disconnected.append(phone)

        if disconnected:
            log.warning(f"Watchdog: {len(disconnected)} клиентов отключены: {disconnected}")
            for phone in disconnected:
                try:
                    await self.session_mgr.connect_client(phone)
                    log.info(f"Watchdog: reconnect {phone} успешен")
                except AuthKeyUnregisteredError:
                    log.critical(f"Watchdog: {phone} — сессия мертва (AuthKeyUnregistered)")
                    await self._mark_dead(phone, "AuthKeyUnregistered")
                except UserDeactivatedBanError:
                    log.critical(f"Watchdog: {phone} — аккаунт забанен")
                    await self._mark_dead(phone, "UserDeactivatedBan")
                except Exception as exc:
                    log.warning(f"Watchdog: reconnect {phone} не удался: {exc}")

    async def _mark_dead(self, phone: str, error_type: str):
        """Пометить аккаунт как мёртвый в БД и уведомить."""
        try:
            from storage.sqlite_db import async_session
            from storage.models import Account
            from sqlalchemy import update
            from utils.helpers import utcnow

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
            log.error(f"Watchdog: ошибка обновления статуса {phone}: {exc}")

        await notifier.session_dead(phone, error_type)
