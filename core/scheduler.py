"""
Планировщик задач — оркестрация мониторинга, комментирования, синхронизации.
Использует APScheduler для фоновых задач.

Поддерживает:
- Отложенный запуск (delayed start) — старт по таймеру
- Фоновые задачи с интервалом
- Cron-задачи (ежедневный сброс)
- Авто-восстановление аккаунтов
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable

from utils.helpers import utcnow

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from config import settings
from utils.logger import log


class TaskScheduler:
    """Центральный планировщик фоновых задач."""

    def __init__(self):
        self._scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,  # Объединить пропущенные запуски
                "max_instances": 1,  # Не запускать задачу параллельно с собой
                "misfire_grace_time": 60,
            }
        )
        self._started = False
        self._delayed_start_time: Optional[datetime] = None

    def add_monitoring_job(self, func: Callable[[], Awaitable], interval_sec: Optional[int] = None):
        """Добавить задачу мониторинга каналов."""
        interval = interval_sec or settings.MONITOR_POLL_INTERVAL_SEC
        self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=interval),
            id="channel_monitor",
            name="Мониторинг каналов",
            replace_existing=True,
        )
        log.info(f"Задача мониторинга: каждые {interval}с")

    def add_commenting_job(self, func: Callable[[], Awaitable], interval_sec: int = 30):
        """Добавить задачу обработки очереди комментариев."""
        self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=interval_sec),
            id="comment_processor",
            name="Обработка очереди комментариев",
            replace_existing=True,
        )
        log.info(f"Задача комментирования: каждые {interval_sec}с")

    def add_sheets_sync_job(self, func: Callable[[], Awaitable], interval_sec: Optional[int] = None):
        """Добавить задачу синхронизации с Google Sheets."""
        interval = interval_sec or settings.SHEETS_SYNC_INTERVAL_SEC
        self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=interval),
            id="sheets_sync",
            name="Синхронизация Google Sheets",
            replace_existing=True,
        )
        log.info(f"Задача синхронизации Sheets: каждые {interval}с")

    def add_daily_reset_job(self, func: Callable[[], Awaitable]):
        """Добавить задачу сброса дневных счётчиков (в полночь)."""
        self._scheduler.add_job(
            func,
            trigger=CronTrigger(hour=0, minute=0, timezone="UTC"),
            id="daily_reset",
            name="Сброс дневных счётчиков",
            replace_existing=True,
        )
        log.info("Задача сброса счётчиков: ежедневно в 00:00")

    def add_auto_recovery_job(self, func: Callable[[], Awaitable], interval_sec: int = 600):
        """Добавить задачу авто-восстановления аккаунтов (каждые 10 мин)."""
        self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=interval_sec),
            id="auto_recovery",
            name="Авто-восстановление аккаунтов",
            replace_existing=True,
        )
        log.info(f"Задача авто-восстановления: каждые {interval_sec}с")

    def add_custom_job(
        self,
        job_id: str,
        func: Callable[[], Awaitable],
        interval_sec: int,
        name: str = "",
    ):
        """Добавить произвольную задачу."""
        self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=interval_sec),
            id=job_id,
            name=name or job_id,
            replace_existing=True,
        )

    def schedule_delayed_start(
        self,
        func: Callable[[], Awaitable],
        delay_minutes: int,
    ) -> datetime:
        """
        Отложенный запуск комментирования через N минут.
        Возвращает datetime когда запустится.
        """
        run_at = utcnow() + timedelta(minutes=delay_minutes)
        self._delayed_start_time = run_at

        self._scheduler.add_job(
            func,
            trigger=DateTrigger(run_date=run_at),
            id="delayed_start",
            name=f"Отложенный запуск (через {delay_minutes} мин)",
            replace_existing=True,
        )
        log.info(f"Отложенный запуск запланирован на {run_at.strftime('%H:%M:%S')}")
        return run_at

    def cancel_delayed_start(self) -> bool:
        """Отменить отложенный запуск."""
        try:
            self._scheduler.remove_job("delayed_start")
            self._delayed_start_time = None
            log.info("Отложенный запуск отменён")
            return True
        except Exception:
            return False

    @property
    def delayed_start_time(self) -> Optional[datetime]:
        return self._delayed_start_time

    def remove_job(self, job_id: str):
        """Удалить задачу."""
        try:
            self._scheduler.remove_job(job_id)
            log.info(f"Задача '{job_id}' удалена")
        except Exception:
            pass

    def start(self):
        """Запустить планировщик."""
        if not self._started:
            self._scheduler.start()
            self._started = True
            log.info("Планировщик задач запущен")

    def stop(self):
        """Остановить планировщик. Создаёт новый экземпляр для возможного перезапуска."""
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            # Пересоздать scheduler чтобы можно было запустить снова
            self._scheduler = AsyncIOScheduler(
                job_defaults={
                    "coalesce": True,
                    "max_instances": 1,
                    "misfire_grace_time": 60,
                }
            )
            self._delayed_start_time = None
            log.info("Планировщик задач остановлен")

    @property
    def is_running(self) -> bool:
        return self._started

    def get_jobs_info(self) -> list[dict]:
        """Информация о текущих задачах."""
        jobs = []
        for job in self._scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": next_run.strftime("%H:%M:%S") if next_run else "—",
            })
        return jobs
