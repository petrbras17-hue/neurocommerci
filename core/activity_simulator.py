"""
ActivitySimulator — keep-alive для Telegram сессий.

Периодически выполняет лёгкие операции (get_me, read messages, reactions)
чтобы сессии не истекали по таймауту (180 дней мобильные, 365 десктоп).

НЕ отправляет комментарии — только пассивные действия с нулевым риском бана.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from config import settings
from utils.logger import log

if TYPE_CHECKING:
    from core.session_manager import SessionManager


class ActivitySimulator:
    """Фоновая симуляция активности для предотвращения истечения сессий."""

    def __init__(self, session_manager: SessionManager):
        self._session_mgr = session_manager
        self._task: asyncio.Task | None = None
        self._stats = {"keepalive_calls": 0, "reads": 0, "errors": 0}

    async def start(self) -> None:
        """Запустить фоновую симуляцию."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._simulate_loop(), name="activity_simulator")
        log.info("ActivitySimulator запущен")

    async def stop(self) -> None:
        """Остановить симуляцию."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("ActivitySimulator остановлен")

    def _is_sleep_window(self) -> bool:
        """Проверить, сейчас ли время 'сна' (без активности)."""
        now = datetime.now(timezone.utc)
        hour = now.hour
        start = settings.ACCOUNT_SLEEP_START_HOUR
        end = settings.ACCOUNT_SLEEP_END_HOUR

        if start > end:
            # Ночное окно (например 23:00-07:00)
            return hour >= start or hour < end
        else:
            return start <= hour < end

    async def _keepalive_one(self, phone: str) -> bool:
        """Выполнить keep-alive для одного аккаунта."""
        client = self._session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return False

        try:
            # Самый дешёвый вызов — get_me() (~1 API call)
            await client.get_me()
            self._stats["keepalive_calls"] += 1

            # С 30% вероятностью — прочитать пару сообщений из диалогов
            if random.random() < 0.3:
                try:
                    dialogs = await client.get_dialogs(limit=3)
                    for dialog in dialogs[:2]:
                        if dialog.unread_count > 0:
                            await client.send_read_acknowledge(
                                dialog.entity,
                                max_id=dialog.message.id if dialog.message else 0,
                            )
                            self._stats["reads"] += 1
                            await asyncio.sleep(random.uniform(1, 3))
                except Exception:
                    pass  # Не критично

            return True

        except Exception as exc:
            self._stats["errors"] += 1
            log.debug(f"ActivitySimulator ошибка {phone}: {exc}")
            return False

    async def _simulate_loop(self) -> None:
        """Фоновый цикл: keep-alive для всех аккаунтов."""
        interval = settings.KEEP_ALIVE_INTERVAL_HOURS * 3600

        # Первый цикл через 10 минут после старта
        await asyncio.sleep(600)

        while True:
            try:
                if self._is_sleep_window():
                    log.debug("ActivitySimulator: ночное время, пропуск")
                    await asyncio.sleep(3600)
                    continue

                phones = self._session_mgr.get_connected_phones()
                if not phones:
                    await asyncio.sleep(interval)
                    continue

                log.debug(f"ActivitySimulator: keep-alive для {len(phones)} аккаунтов")

                # Перемешать порядок (не одинаковая последовательность каждый раз)
                shuffled = list(phones)
                random.shuffle(shuffled)

                for phone in shuffled:
                    if self._is_sleep_window():
                        break
                    await self._keepalive_one(phone)
                    # Gaussian задержка между аккаунтами
                    delay = random.gauss(15, 5)
                    await asyncio.sleep(max(5, min(delay, 30)))

                # Ждать до следующего цикла (с рандомизацией ±20%)
                jitter = interval * random.uniform(0.8, 1.2)
                await asyncio.sleep(jitter)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(f"Ошибка в activity_simulator: {exc}")
                await asyncio.sleep(600)

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()
