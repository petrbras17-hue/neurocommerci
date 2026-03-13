"""
CommentingEngine — главный оркестратор системы нейрокомментирования.

Управляет параллельными процессами:
- Мониторинг каналов (поиск новых постов)
- Обработка очереди комментариев
- Прогрев новых аккаунтов (14-дневный цикл)
- Проверка здоровья аккаунтов

Поддерживает per-user task spawning для multi-tenant SaaS.
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
from core.session_health import SessionHealthMonitor
from core.session_backup import SessionBackupManager
from core.activity_simulator import ActivitySimulator
from channels.monitor import ChannelMonitor
from comments.poster import CommentPoster
from core.task_queue import task_queue
from core.policy_engine import policy_engine
from utils.anti_ban import AntibanManager
from utils.channel_subscriber import ChannelSubscriber
from utils.passive_actions import PassiveActionsManager
from utils.notifier import notifier
from utils.logger import log


class CommentingEngine:
    """
    Центральный движок: запускает и координирует все подсистемы.
    Управляется через Telegram-бот (start/stop).
    Поддерживает per-user задачи для multi-tenant режима.
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

        # Session survival modules
        self.health_monitor = SessionHealthMonitor(session_manager, notifier)
        self.backup_manager = SessionBackupManager()
        self.activity_sim = ActivitySimulator(session_manager)

        self._running = False
        self._tasks: list[asyncio.Task] = []
        # Per-user tasks for multi-tenant mode
        self._user_tasks: dict[int, dict[str, asyncio.Task]] = {}
        self._stats = {
            "comments_sent": 0,
            "queued_tasks": 0,
            "warmup_actions": 0,
            "health_checks": 0,
            "errors": 0,
        }

    @property
    def is_running(self) -> bool:
        return self._running

    def is_running_for_user(self, user_id: int) -> bool:
        """Check if engine is running for specific user."""
        tasks = self._user_tasks.get(user_id, {})
        return any(not t.done() for t in tasks.values())

    def _should_run(self, user_id: int = None) -> bool:
        """Should loop continue? Works for both global and per-user modes."""
        return self._running or bool(user_id and self.is_running_for_user(user_id))

    async def start(self) -> None:
        """Запустить все процессы параллельно (legacy global mode)."""
        if self._running:
            log.warning("Движок уже запущен")
            return

        log.info("=== ЗАПУСК ДВИЖКА НЕЙРОКОММЕНТИРОВАНИЯ ===")

        try:
            if settings.DISTRIBUTED_QUEUE_MODE and settings.MAX_ACCOUNTS_PER_WORKER <= 0:
                raise RuntimeError(
                    "Distributed mode включён, но MAX_ACCOUNTS_PER_WORKER<=0. "
                    "Увеличьте лимит worker claim перед запуском."
                )
            # Загрузить счётчики из БД
            await self.rate_limiter.load_from_db()

            # Запустить мониторинг каналов
            await self.monitor.start()

            if settings.DISTRIBUTED_QUEUE_MODE:
                await task_queue.connect()
                log.info("Distributed queue mode: enabled")

            if settings.DISTRIBUTED_QUEUE_MODE:
                self._tasks = [
                    asyncio.create_task(self._distributed_idle_loop(), name="distributed_idle"),
                ]
            else:
                # Локальный режим оставляем только в щадящем варианте без
                # старых цикличных warmup/activity/SpamBot сценариев.
                self._tasks = [
                    asyncio.create_task(self._comment_loop(), name="comment_loop"),
                ]
                if not settings.HUMAN_GATED_COMMENTS:
                    self._tasks.extend([
                        asyncio.create_task(self._warmup_loop(), name="warmup_loop"),
                        asyncio.create_task(self._health_check_loop(), name="health_check"),
                        asyncio.create_task(self._auto_recover_loop(), name="auto_recover"),
                    ])
                    await self.health_monitor.start()
                    await self.activity_sim.start()

            # Флаг только после успешного создания задач
            self._running = True
            log.info("=== ДВИЖОК НЕЙРОКОММЕНТИРОВАНИЯ ЗАПУЩЕН ===")
            await notifier.notify("Движок нейрокомментирования запущен")

        except Exception as e:
            self._running = False
            self._tasks.clear()
            log.error(f"Ошибка запуска движка: {e}")
            await notifier.notify(f"ОШИБКА запуска движка: {e}")
            raise

    async def start_for_user(self, user_id: int) -> None:
        """Запустить движок для конкретного пользователя."""
        if self.is_running_for_user(user_id):
            log.warning(f"Движок для user_id={user_id} уже запущен")
            return

        log.info(f"=== ЗАПУСК ДВИЖКА ДЛЯ USER {user_id} ===")
        if settings.DISTRIBUTED_QUEUE_MODE and settings.MAX_ACCOUNTS_PER_WORKER <= 0:
            raise RuntimeError(
                "Distributed mode включён, но MAX_ACCOUNTS_PER_WORKER<=0. "
                "Увеличьте лимит worker claim перед запуском."
            )
        await self.rate_limiter.load_from_db(user_id=user_id)
        if settings.DISTRIBUTED_QUEUE_MODE:
            await task_queue.connect()
            self._user_tasks[user_id] = {
                "idle": asyncio.create_task(
                    self._distributed_idle_loop(user_id=user_id),
                    name=f"distributed_idle_u{user_id}",
                ),
            }
        else:
            self._user_tasks[user_id] = {
                "comment": asyncio.create_task(
                    self._comment_loop(user_id=user_id),
                    name=f"comment_loop_u{user_id}",
                ),
            }
            if not settings.HUMAN_GATED_COMMENTS:
                self._user_tasks[user_id].update({
                    "warmup": asyncio.create_task(
                        self._warmup_loop(user_id=user_id),
                        name=f"warmup_loop_u{user_id}",
                    ),
                    "health": asyncio.create_task(
                        self._health_check_loop(user_id=user_id),
                        name=f"health_check_u{user_id}",
                    ),
                    "recover": asyncio.create_task(
                        self._auto_recover_loop(user_id=user_id),
                        name=f"auto_recover_u{user_id}",
                    ),
                })

                # Session survival modules (start once, shared across users)
                if not self.health_monitor.is_running:
                    await self.health_monitor.start()
                if not self.activity_sim.is_running:
                    await self.activity_sim.start()

        # Set global running flag if any user is running
        self._running = True
        log.info(f"=== ДВИЖОК ДЛЯ USER {user_id} ЗАПУЩЕН ===")

    async def stop_for_user(self, user_id: int) -> None:
        """Остановить движок для конкретного пользователя."""
        tasks = self._user_tasks.pop(user_id, {})
        for task in tasks.values():
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        log.info(f"Движок для user_id={user_id} остановлен")

        # Update global running flag
        if not self._user_tasks and not self._tasks:
            self._running = False

    async def stop(self) -> None:
        """Остановить движок gracefully (все пользователи)."""
        if not self._running:
            return

        self._running = False
        log.info("=== ОСТАНОВКА ДВИЖКА ===")

        # Остановить session survival модули
        if self.health_monitor.is_running:
            await self.health_monitor.stop()
        if self.activity_sim.is_running:
            await self.activity_sim.stop()

        # Остановить мониторинг
        await self.monitor.stop()

        # Остановить глобальные задачи
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # Остановить per-user задачи
        for uid in list(self._user_tasks.keys()):
            await self.stop_for_user(uid)

        # Дождаться завершения pending swap задач
        await self.poster.shutdown()

        if settings.DISTRIBUTED_QUEUE_MODE:
            await task_queue.close()

        await notifier.notify("Движок остановлен")
        log.info("Движок остановлен")

    # ─────────────────────────────────────────────
    # Цикл комментирования
    # ─────────────────────────────────────────────

    async def _comment_loop(self, user_id: int = None) -> None:
        """Основной цикл: берём пост из очереди → генерируем коммент → отправляем."""
        log.info(f"Comment loop запущен (user_id={user_id})")

        if settings.DISTRIBUTED_QUEUE_MODE:
            while self._should_run(user_id):
                await asyncio.sleep(300)
            return

        while self._should_run(user_id):
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

                # Обработать один пост (локально) или отправить задачу в Redis-очередь.
                if settings.DISTRIBUTED_QUEUE_MODE:
                    result = await self._enqueue_comment_task(user_id=user_id)
                    if result == -1:
                        self._stats["errors"] += 1
                else:
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

    async def _distributed_idle_loop(self, user_id: int = None) -> None:
        """Control-plane loop for distributed mode; workers consume Redis tasks directly."""
        log.info(f"Distributed idle loop запущен (user_id={user_id})")
        while self._should_run(user_id):
            await asyncio.sleep(300)

    async def _enqueue_comment_task(self, user_id: int = None) -> int:
        """Move one discovered post from in-memory queue to Redis queue for workers."""
        post_data = await self.monitor.queue.pop()
        if not post_data:
            return 0

        decision = await policy_engine.check(
            "comment_enqueue_attempt",
            {
                "queue_size": self.monitor.queue.size,
                "post_channel": post_data.get("channel_title"),
            },
        )
        if decision.action in {"block", "quarantine"}:
            await self.monitor.queue.add(post_data)
            log.warning(f"Policy blocked enqueue ({decision.rule_id})")
            return -1

        payload = {
            "post_data": post_data,
            "user_id": user_id,
        }
        try:
            await task_queue.enqueue("comments", payload)
            self._stats["queued_tasks"] += 1
            log.debug(
                f"Queued comment task: channel={post_data.get('channel_title')} "
                f"post={post_data.get('telegram_post_id')}"
            )
            return 1
        except Exception as exc:
            # Do not lose task on transient Redis issues.
            await self.monitor.queue.add(post_data)
            log.warning(f"Не удалось поставить задачу в Redis, пост возвращён в очередь: {exc}")
            return -1

    # ─────────────────────────────────────────────
    # Цикл прогрева (14-дневный)
    # ─────────────────────────────────────────────

    async def _warmup_loop(self, user_id: int = None) -> None:
        """
        Прогрев новых аккаунтов:
        - readonly: get_me, скролл каналов
        - reactions: реакции, send_read_acknowledge
        - light/moderate: обрабатывается через poster с лимитами rate_limiter
        """
        log.info(f"Warmup loop запущен (user_id={user_id})")

        while self._should_run(user_id):
            try:
                if not self.antiban.is_active_hours():
                    await asyncio.sleep(600)
                    continue

                accounts = await self._get_warmup_accounts(user_id=user_id)

                # Загрузить каналы один раз для всех аккаунтов
                from channels.channel_db import ChannelDB
                channels = await ChannelDB().get_all_active(user_id=user_id)

                for phone, days_active in accounts:
                    if not self._should_run(user_id):
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

    async def _get_warmup_accounts(self, user_id: int = None) -> list[tuple[str, int]]:
        """Получить аккаунты в фазе прогрева (days_active < 15)."""
        from storage.models import Account
        from storage.sqlite_db import async_session

        try:
            async with async_session() as session:
                query = select(Account.phone, Account.days_active).where(
                    Account.status == "active",
                    Account.days_active < 15,
                )
                if user_id is not None:
                    query = query.where(Account.user_id == user_id)
                result = await session.execute(query.order_by(Account.last_active_at.asc()).limit(500))
                return [(phone, days if days is not None else 0) for phone, days in result.all()]
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

    async def _health_check_loop(self, user_id: int = None) -> None:
        """Проверка здоровья аккаунтов через @SpamBot (раз в 3 дня)."""
        log.info(f"Health check loop запущен (user_id={user_id})")

        while self._should_run(user_id):
            try:
                # Проверка раз в 6 часов
                await asyncio.sleep(6 * 3600)

                if not self._should_run(user_id):
                    break

                connected = self.session_mgr.get_connected_phones(user_id=user_id)
                for phone in connected[:5]:  # Не больше 5 за раз
                    if not self._should_run(user_id):
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

    async def _auto_recover_loop(self, user_id: int = None) -> None:
        """Автоматическое восстановление аккаунтов из cooldown/error."""
        log.info(f"Auto-recover loop запущен (user_id={user_id})")

        while self._should_run(user_id):
            try:
                await asyncio.sleep(600)  # Каждые 10 мин

                if not self._should_run(user_id):
                    break

                result = await self.account_mgr.auto_recover(user_id=user_id)
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
        queue_size = 0 if settings.DISTRIBUTED_QUEUE_MODE else self.monitor.queue.size
        return {
            "running": self._running,
            "distributed_mode": settings.DISTRIBUTED_QUEUE_MODE,
            "engine": self._stats,
            "poster": self.poster.get_stats(),
            "queue_size": queue_size,
            "monitor_running": self.monitor.is_running,
            "pool": self.session_mgr.pool_stats,
            "active_users": len(self._user_tasks),
        }

    def get_active_user_ids(self) -> list[int]:
        """Get list of user_ids with active tasks."""
        return [uid for uid, tasks in self._user_tasks.items()
                if any(not t.done() for t in tasks.values())]
