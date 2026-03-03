"""
CommentingEngine — главный оркестратор системы нейрокомментирования.

Управляет параллельными процессами:
- Мониторинг каналов (поиск новых постов)
- Обработка очереди комментариев
- Прогрев новых аккаунтов (14-дневный цикл)
- Проверка здоровья аккаунтов
"""

from __future__ import annotations

import asyncio
import random
from typing import Optional

from sqlalchemy import select

from config import settings
from core.account_manager import AccountManager
from core.session_manager import SessionManager
from core.proxy_manager import ProxyManager
from core.rate_limiter import RateLimiter
from channels.monitor import ChannelMonitor
from comments.poster import CommentPoster
from utils.anti_ban import AntibanManager
from utils.channel_subscriber import ChannelSubscriber
from utils.passive_actions import PassiveActionsManager
from utils.notifier import notifier
from utils.logger import log


class CommentingEngine:
    """
    Центральный движок: запускает и координирует все подсистемы.
    Управляется через Telegram-бот (start/stop).
    """

    def __init__(
        self,
        account_manager: AccountManager,
        session_manager: SessionManager,
        proxy_manager: ProxyManager,
        rate_limiter: RateLimiter,
        poster: CommentPoster,
        monitor: ChannelMonitor,
        subscriber: ChannelSubscriber,
    ):
        self.account_mgr = account_manager
        self.session_mgr = session_manager
        self.proxy_mgr = proxy_manager
        self.rate_limiter = rate_limiter
        self.poster = poster
        self.monitor = monitor
        self.subscriber = subscriber
        self.antiban = AntibanManager()
        self.passive = PassiveActionsManager(session_manager)

        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._stats = {
            "comments_sent": 0,
            "warmup_actions": 0,
            "health_checks": 0,
            "errors": 0,
        }

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Запустить все процессы параллельно."""
        if self._running:
            log.warning("Движок уже запущен")
            return

        self._running = True
        log.info("=== ДВИЖОК НЕЙРОКОММЕНТИРОВАНИЯ ЗАПУЩЕН ===")

        # Загрузить счётчики из БД
        await self.rate_limiter.load_from_db()

        # Запустить мониторинг каналов
        await self.monitor.start()

        # Запустить параллельные циклы
        self._tasks = [
            asyncio.create_task(self._comment_loop(), name="comment_loop"),
            asyncio.create_task(self._warmup_loop(), name="warmup_loop"),
            asyncio.create_task(self._health_check_loop(), name="health_check"),
            asyncio.create_task(self._auto_recover_loop(), name="auto_recover"),
        ]

        await notifier.send("Движок нейрокомментирования запущен")

    async def stop(self) -> None:
        """Остановить движок gracefully."""
        if not self._running:
            return

        self._running = False
        log.info("=== ОСТАНОВКА ДВИЖКА ===")

        # Остановить мониторинг
        await self.monitor.stop()

        # Остановить все фоновые задачи
        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # Дождаться завершения pending swap задач
        await self.poster.shutdown()

        await notifier.send("Движок остановлен")
        log.info("Движок остановлен")

    # ─────────────────────────────────────────────
    # Цикл комментирования
    # ─────────────────────────────────────────────

    async def _comment_loop(self) -> None:
        """Основной цикл: берём пост из очереди → генерируем коммент → отправляем."""
        log.info("Comment loop запущен")

        while self._running:
            try:
                # Ночью не комментируем (8:00-23:00 MSK)
                if not self.antiban.is_active_hours():
                    log.debug("Ночное время — комментирование приостановлено")
                    await asyncio.sleep(300)  # Проверяем каждые 5 мин
                    continue

                # Проверить есть ли посты в очереди
                if self.monitor.queue.size == 0:
                    await asyncio.sleep(30)  # Ждём новых постов
                    continue

                # Обработать один пост
                result = await self.poster.process_queue()

                if result == 1:
                    self._stats["comments_sent"] += 1
                elif result == -1:
                    self._stats["errors"] += 1

                # Задержка между комментариями (Gaussian)
                delay = self.rate_limiter.get_next_delay()

                # В пиковые часы — чуть быстрее
                if self.antiban.is_peak_hours():
                    delay *= 0.7

                await asyncio.sleep(delay)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._stats["errors"] += 1
                log.error(f"Ошибка в comment_loop: {exc}")
                await asyncio.sleep(60)

    # ─────────────────────────────────────────────
    # Цикл прогрева (14-дневный)
    # ─────────────────────────────────────────────

    async def _warmup_loop(self) -> None:
        """
        Прогрев новых аккаунтов:
        - readonly: get_me, скролл каналов
        - reactions: реакции, send_read_acknowledge
        - light/moderate: обрабатывается через poster с лимитами rate_limiter
        """
        log.info("Warmup loop запущен")

        while self._running:
            try:
                if not self.antiban.is_active_hours():
                    await asyncio.sleep(600)
                    continue

                accounts = await self._get_warmup_accounts()

                # Загрузить каналы один раз для всех аккаунтов
                from channels.channel_db import ChannelDB
                channels = await ChannelDB().get_all_active()

                for phone, days_active in accounts:
                    if not self._running:
                        break

                    phase = self.antiban.get_warmup_phase(days_active)

                    if phase == "readonly":
                        await self._do_warmup_readonly(phone, channels)
                    elif phase == "reactions":
                        await self._do_warmup_reactions(phone, channels)
                    # light/moderate/full — управляются через poster + rate_limiter

                    # Задержка между аккаунтами
                    await asyncio.sleep(random.uniform(10, 30))

                # Прогрев раз в час
                await asyncio.sleep(3600)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(f"Ошибка в warmup_loop: {exc}")
                await asyncio.sleep(300)

    async def _get_warmup_accounts(self) -> list[tuple[str, int]]:
        """Получить аккаунты в фазе прогрева (days_active < 15)."""
        from storage.models import Account
        from storage.sqlite_db import async_session

        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Account.phone, Account.days_active).where(
                        Account.status == "active",
                        Account.days_active < 15,
                    )
                )
                return [(phone, days or 0) for phone, days in result.all()]
        except Exception as exc:
            log.warning(f"Ошибка загрузки warmup аккаунтов: {exc}")
            return []

    async def _do_warmup_readonly(self, phone: str, channels: list = None) -> None:
        """Фаза readonly: просто подключиться, get_me, скроллить."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return

        try:
            me = await client.get_me()
            log.debug(f"Warmup readonly {phone}: {me.first_name}")
            self._stats["warmup_actions"] += 1

            # Случайно пометить пару каналов прочитанными
            if channels:
                sample = random.sample(channels, min(3, len(channels)))
                for ch in sample:
                    await self.passive.mark_as_read(phone, ch.telegram_id)
                    await asyncio.sleep(random.uniform(2, 5))

        except Exception as exc:
            log.debug(f"Warmup readonly ошибка {phone}: {exc}")

    async def _do_warmup_reactions(self, phone: str, channels: list = None) -> None:
        """Фаза reactions: реакции на посты, чтение."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return

        try:
            if not channels:
                return

            # Выбрать 2-3 канала для реакций
            sample = random.sample(channels, min(3, len(channels)))
            for ch in sample:
                try:
                    entity = await client.get_entity(ch.telegram_id)
                    async for msg in client.iter_messages(entity, limit=3):
                        if msg.text:
                            # Реакция с 50% шансом
                            if random.random() < 0.5:
                                await self.passive.send_reaction(
                                    phone, ch.telegram_id, msg.id,
                                )
                                self._stats["warmup_actions"] += 1
                            # Просмотр
                            await self.passive.view_post(phone, ch.telegram_id, msg.id)
                            await asyncio.sleep(random.uniform(3, 8))
                            break
                except Exception:
                    pass

                await asyncio.sleep(random.uniform(5, 15))

        except Exception as exc:
            log.debug(f"Warmup reactions ошибка {phone}: {exc}")

    # ─────────────────────────────────────────────
    # Проверка здоровья
    # ─────────────────────────────────────────────

    async def _health_check_loop(self) -> None:
        """Проверка здоровья аккаунтов через @SpamBot (раз в 3 дня)."""
        log.info("Health check loop запущен")

        while self._running:
            try:
                # Проверка раз в 6 часов
                await asyncio.sleep(6 * 3600)

                if not self._running:
                    break

                connected = self.session_mgr.get_connected_phones()
                for phone in connected[:5]:  # Не больше 5 за раз
                    if not self._running:
                        break
                    await self._check_account_health(phone)
                    self._stats["health_checks"] += 1
                    await asyncio.sleep(random.uniform(30, 60))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(f"Ошибка в health_check_loop: {exc}")
                await asyncio.sleep(3600)

    async def _check_account_health(self, phone: str) -> None:
        """Проверить один аккаунт через @SpamBot."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return

        try:
            entity = await client.get_entity("SpamBot")
            await client.send_message(entity, "/start")
            await asyncio.sleep(5)

            # Прочитать ответ
            messages = await client.get_messages(entity, limit=1)
            if messages:
                text = (messages[0].text or "").lower()
                if "free" in text or "no limits" in text:
                    log.info(f"{phone}: SpamBot — аккаунт чист")
                elif "frozen" in text:
                    log.warning(f"{phone}: SpamBot — аккаунт ЗАМОРОЖЕН!")
                    await self.account_mgr.handle_error(phone, "banned", "frozen")
                    await notifier.error_occurred(phone, "Frozen", "Аккаунт заморожен!")
                elif "limited" in text:
                    log.warning(f"{phone}: SpamBot — есть ограничения")
                    await notifier.error_occurred(phone, "Limited", "Ограничения на аккаунте")

        except Exception as exc:
            log.debug(f"Health check ошибка {phone}: {exc}")

    # ─────────────────────────────────────────────
    # Авто-восстановление
    # ─────────────────────────────────────────────

    async def _auto_recover_loop(self) -> None:
        """Автоматическое восстановление аккаунтов из cooldown/error."""
        log.info("Auto-recover loop запущен")

        while self._running:
            try:
                await asyncio.sleep(600)  # Каждые 10 мин

                if not self._running:
                    break

                result = await self.account_mgr.auto_recover()
                if result["recovered"] > 0:
                    log.info(
                        f"Auto-recover: {result['recovered']} восстановлено, "
                        f"{result['reconnected']} переподключено"
                    )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(f"Ошибка в auto_recover_loop: {exc}")
                await asyncio.sleep(600)

    # ─────────────────────────────────────────────
    # Статистика
    # ─────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Полная статистика движка."""
        return {
            "running": self._running,
            "engine": self._stats,
            "poster": self.poster.get_stats(),
            "queue_size": self.monitor.queue.size,
            "monitor_running": self.monitor.is_running,
            "pool": self.session_mgr.pool_stats,
        }
