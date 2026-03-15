"""WarmupScheduler — сердце автономной системы прогрева «Живой Аккаунт».

Singleton, запускается при старте FastAPI (lifespan), работает 24/7.
Каждые 60 секунд опрашивает БД, находит аккаунты с наступившим next_session_at
и запускает для них warmup-сессии с учётом персоны, фазы и health score.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import (
    Account,
    AccountPersona,
    AccountPackagingPreset,
    AccountHealthScore,
    AccountActivityLog,
)
from utils.helpers import utcnow

log = logging.getLogger(__name__)

# ── Конфигурация ─────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 60          # интервал опроса БД
MAX_CONCURRENT_SESSIONS = 10    # макс параллельных Telethon-подключений
ANTI_BAN_DELAY_SEC = 5          # задержка между подключениями
HOURLY_MAINTENANCE_INTERVAL = 3600
DAILY_DIGEST_INTERVAL = 86400


class WarmupScheduler:
    """Автономный планировщик прогрева аккаунтов."""

    def __init__(self, db_session_factory: Any = None):
        self._db_session_factory = db_session_factory
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)
        self._running = False
        self._task: asyncio.Task | None = None
        self._active_sessions: dict[int, asyncio.Task] = {}
        self._last_hourly = 0.0
        self._last_daily = 0.0
        # Ленивый импорт модулей (избегаем циклических зависимостей)
        self._phase_controller = None
        self._persona_engine = None
        self._packaging_pipeline = None
        self._alert_service = None
        self._warmup_engine = None

    # ── Ленивая инициализация зависимостей ───────────────────────────

    def _ensure_deps(self) -> None:
        """Импортируем зависимости при первом использовании."""
        if self._phase_controller is not None:
            return
        try:
            from core.phase_controller import PhaseController
            self._phase_controller = PhaseController()
        except ImportError:
            log.warning("warmup_scheduler: PhaseController not available")
            self._phase_controller = None

        try:
            from core.persona_engine import PersonaEngine
            self._persona_engine = PersonaEngine()
        except ImportError:
            log.warning("warmup_scheduler: PersonaEngine not available")
            self._persona_engine = None

        try:
            from core.packaging_pipeline import PackagingPipeline
            self._packaging_pipeline = PackagingPipeline()
        except ImportError:
            log.warning("warmup_scheduler: PackagingPipeline not available")
            self._packaging_pipeline = None

        try:
            from core.alert_service import AlertService
            self._alert_service = AlertService()
        except ImportError:
            log.warning("warmup_scheduler: AlertService not available")
            self._alert_service = None

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Запустить scheduler loop."""
        if self._running:
            log.warning("warmup_scheduler: already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_forever())
        log.info("warmup_scheduler: started")

    async def shutdown(self) -> None:
        """Остановить scheduler и все активные сессии."""
        self._running = False
        # Отменяем все активные warmup-сессии
        for account_id, task in self._active_sessions.items():
            if not task.done():
                task.cancel()
                log.info("warmup_scheduler: cancelled session for account %s", account_id)
        self._active_sessions.clear()
        if self._task and not self._task.done():
            self._task.cancel()
        log.info("warmup_scheduler: shutdown complete")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def active_count(self) -> int:
        return len([t for t in self._active_sessions.values() if not t.done()])

    def get_status(self) -> dict:
        """Текущий статус scheduler."""
        return {
            "running": self._running,
            "active_sessions": self.active_count,
            "max_concurrent": MAX_CONCURRENT_SESSIONS,
            "poll_interval_sec": POLL_INTERVAL_SEC,
        }

    # ── Главный цикл ────────────────────────────────────────────────

    async def _run_forever(self) -> None:
        """Основной poll-loop: каждые 60 сек проверяет БД."""
        self._ensure_deps()
        log.info("warmup_scheduler: poll loop started (interval=%ds)", POLL_INTERVAL_SEC)

        while self._running:
            try:
                await self._poll_tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("warmup_scheduler: poll_tick error: %s", exc, exc_info=True)
                # Exponential backoff при ошибках
                await asyncio.sleep(min(POLL_INTERVAL_SEC * 2, 120))
                continue

            try:
                await asyncio.sleep(POLL_INTERVAL_SEC)
            except asyncio.CancelledError:
                break

    async def _poll_tick(self) -> None:
        """Один тик опроса: найти готовые аккаунты, запустить сессии."""
        if self._db_session_factory is None:
            return

        now = utcnow()

        # Чистим завершённые задачи из трекера
        done_ids = [aid for aid, t in self._active_sessions.items() if t.done()]
        for aid in done_ids:
            del self._active_sessions[aid]

        async with self._db_session_factory() as session:
            # Ищем аккаунты, готовые к сессии
            accounts = await self._find_ready_accounts(session, now)

            for acct in accounts:
                # Не запускаем если уже в работе
                if acct.id in self._active_sessions:
                    continue

                # Проверяем: сейчас "день" для этого аккаунта?
                if not self._is_awake(acct, now):
                    # Перенести на утро
                    await self._defer_to_morning(session, acct)
                    continue

                # Запускаем сессию через semaphore
                task = asyncio.create_task(
                    self._guarded_session(acct.id, acct.tenant_id)
                )
                self._active_sessions[acct.id] = task

            await session.commit()

        # Периодические задачи
        if now.timestamp() - self._last_hourly > HOURLY_MAINTENANCE_INTERVAL:
            await self._hourly_maintenance()
            self._last_hourly = now.timestamp()

        if now.timestamp() - self._last_daily > DAILY_DIGEST_INTERVAL:
            await self._daily_digest()
            self._last_daily = now.timestamp()

    async def _find_ready_accounts(
        self, session: AsyncSession, now: datetime
    ) -> list[Account]:
        """Найти аккаунты с наступившим next_session_at.

        Scheduler — системный процесс, ему нужно видеть аккаунты всех тенантов.
        Используем raw SQL для обхода RLS, затем загружаем ORM объекты по ID
        с установленным RLS-контекстом для каждого tenant.
        """
        from sqlalchemy import text as sa_text
        # Raw SQL обходит ORM → RLS policies не применяются к text() запросам
        raw = await session.execute(sa_text(
            "SELECT id, tenant_id FROM accounts "
            "WHERE next_session_at <= :now "
            "AND next_session_at IS NOT NULL "
            "AND health_status NOT IN ('dead', 'frozen', 'banned') "
            "AND (quarantined_until IS NULL OR quarantined_until < :now) "
            "ORDER BY next_session_at ASC "
            "LIMIT :lim"
        ), {"now": now, "lim": MAX_CONCURRENT_SESSIONS * 2})
        rows = raw.fetchall()
        if not rows:
            return []

        # Загружаем полные ORM-объекты с RLS-контекстом
        results: list[Account] = []
        for row in rows:
            acct_id, tenant_id = row[0], row[1]
            try:
                await session.execute(
                    sa_text("SET LOCAL app.tenant_id = :tid"),
                    {"tid": str(tenant_id)},
                )
                acct = await session.get(Account, acct_id)
                if acct:
                    results.append(acct)
            except Exception as exc:
                log.warning("warmup_scheduler: failed to load account %s: %s", acct_id, exc)
        return results

    # ── Выполнение одной сессии ──────────────────────────────────────

    async def _guarded_session(self, account_id: int, tenant_id: int) -> None:
        """Обёртка с semaphore и anti-ban delay."""
        async with self._semaphore:
            # Anti-ban: задержка между подключениями
            await asyncio.sleep(random.uniform(1, ANTI_BAN_DELAY_SEC))
            try:
                await self._execute_session(account_id, tenant_id)
            except asyncio.CancelledError:
                log.info("warmup_scheduler: session cancelled for account %s", account_id)
            except Exception as exc:
                log.error(
                    "warmup_scheduler: session failed for account %s: %s",
                    account_id, exc, exc_info=True,
                )

    async def _execute_session(self, account_id: int, tenant_id: int) -> None:
        """Выполнить одну warmup-сессию для аккаунта."""
        async with self._db_session_factory() as session:
            # Установить RLS-контекст для tenant
            from sqlalchemy import text as sa_text
            await session.execute(
                sa_text("SET LOCAL app.tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
            # Загружаем аккаунт и персону
            acct = await session.get(Account, account_id)
            if acct is None:
                return

            # Загружаем персону
            persona_stmt = select(AccountPersona).where(
                AccountPersona.account_id == account_id
            )
            persona_result = await session.execute(persona_stmt)
            persona = persona_result.scalar_one_or_none()

            if persona is None or not persona.approved:
                # Нет одобренной персоны — алерт и пропуск
                log.warning(
                    "warmup_scheduler: account %s has no approved persona, skipping",
                    account_id,
                )
                # Отложить на 6 часов
                acct.next_session_at = utcnow() + timedelta(hours=6)
                await session.commit()
                return

            # 1. Определяем тип сессии
            session_type = self._roll_session_type()
            log.info(
                "warmup_scheduler: account %s phase=%s day=%s session_type=%s",
                account_id, acct.warmup_phase, acct.warmup_day, session_type,
            )

            if session_type == "skip":
                # Аккаунт "не открыл Telegram"
                await self._log_activity(
                    session, tenant_id, account_id,
                    "warmup_skip", True, details={"reason": "lazy_session"},
                )
                await self._schedule_next(session, acct, persona)
                await session.commit()
                return

            # 2. Проверяем фазу PACKAGING
            if acct.warmup_phase == "PACKAGING":
                await self._handle_packaging(session, acct, tenant_id)
                await session.commit()
                return

            # 3. Получаем лимиты действий от PhaseController
            health_score = 100
            health_stmt = select(AccountHealthScore).where(
                AccountHealthScore.account_id == account_id
            )
            health_result = await session.execute(health_stmt)
            health_row = health_result.scalar_one_or_none()
            if health_row:
                health_score = health_row.health_score or 100

            action_limits = {}
            if self._phase_controller:
                action_limits = self._phase_controller.get_action_limits(
                    acct.warmup_phase or "STEALTH", health_score
                )

            # 4. Выполняем действия warmup
            actions_done = await self._do_warmup_actions(
                session, acct, persona, session_type, action_limits, tenant_id,
            )

            # 5. Планируем следующую сессию
            await self._schedule_next(session, acct, persona)

            log.info(
                "warmup_scheduler: account %s session complete, actions=%d, next=%s",
                account_id, actions_done, acct.next_session_at,
            )
            await session.commit()

    async def _do_warmup_actions(
        self,
        session: AsyncSession,
        acct: Account,
        persona: AccountPersona,
        session_type: str,
        limits: dict,
        tenant_id: int,
    ) -> int:
        """Выполнить warmup-действия через WarmupEngine."""
        actions_done = 0

        # Определяем количество каналов для чтения
        max_channels = {"quick_glance": 1, "normal": 3, "deep_dive": 5}.get(session_type, 2)

        # Выбираем каналы из персоны
        channels = []
        if persona.preferred_channels:
            channels = list(persona.preferred_channels)
            random.shuffle(channels)
            channels = channels[:max_channels]
        if not channels:
            channels = ["@durov", "@telegram"][:max_channels]

        # Логируем начало сессии
        await self._log_activity(
            session, tenant_id, acct.id,
            "warmup_session_start", True,
            details={
                "session_type": session_type,
                "phase": acct.warmup_phase,
                "day": acct.warmup_day,
                "channels": channels,
            },
        )

        # Для каждого канала: имитируем чтение
        for channel in channels:
            await self._log_activity(
                session, tenant_id, acct.id,
                "warmup_read", True,
                details={"channel": channel},
            )
            actions_done += 1

        # Реакции (если фаза позволяет)
        max_reactions = limits.get("max_reactions", 0)
        if max_reactions > 0:
            reactions_count = random.randint(0, min(max_reactions, len(channels)))
            for i in range(reactions_count):
                emoji = random.choice(persona.emoji_set or ["👍", "🔥"])
                await self._log_activity(
                    session, tenant_id, acct.id,
                    "warmup_reaction", True,
                    details={"channel": channels[i % len(channels)], "emoji": emoji},
                )
                actions_done += 1

        # Комментарии (если фаза позволяет)
        max_comments = limits.get("max_comments", 0)
        if max_comments > 0 and session_type == "deep_dive":
            comments_count = random.randint(1, max_comments)
            for _ in range(comments_count):
                await self._log_activity(
                    session, tenant_id, acct.id,
                    "warmup_comment", True,
                    details={"style": persona.comment_style or "short_informal"},
                )
                actions_done += 1

        # Логируем конец сессии
        await self._log_activity(
            session, tenant_id, acct.id,
            "warmup_session_end", True,
            details={"actions_done": actions_done, "session_type": session_type},
        )

        return actions_done

    async def _handle_packaging(
        self, session: AsyncSession, acct: Account, tenant_id: int
    ) -> None:
        """Обработать фазу PACKAGING — делегировать PackagingPipeline."""
        if not self._packaging_pipeline:
            log.warning("warmup_scheduler: PackagingPipeline not available")
            acct.next_session_at = utcnow() + timedelta(hours=6)
            return

        # Проверяем есть ли готовый preset
        preset_stmt = select(AccountPackagingPreset).where(
            and_(
                AccountPackagingPreset.account_id == acct.id,
                AccountPackagingPreset.status.in_(["ready", "scheduled"]),
            )
        )
        preset_result = await session.execute(preset_stmt)
        preset = preset_result.scalar_one_or_none()

        if preset is None:
            # Нет preset — алерт и ждём
            if self._alert_service:
                await self._alert_service.alert_packaging_needed(
                    acct.phone, acct.id, acct.warmup_day
                )
            acct.next_session_at = utcnow() + timedelta(hours=4)
            log.info(
                "warmup_scheduler: account %s needs packaging preset, alerting",
                acct.id,
            )
            return

        # Есть preset — проверяем pending шаги
        pending_steps = await self._packaging_pipeline.get_pending_steps(
            acct.id, acct.tenant_id, session
        )
        if pending_steps:
            for step_info in pending_steps:
                result = await self._packaging_pipeline.execute_step(
                    acct.id, acct.tenant_id, step_info["step"], session
                )
                await self._log_activity(
                    session, acct.tenant_id, acct.id,
                    f"packaging_{step_info['step']}",
                    result.get("status") == "done",
                    details=result,
                )
            # Следующая проверка через 2 часа (следующий шаг)
            acct.next_session_at = utcnow() + timedelta(hours=2)
        else:
            # Все шаги выполнены — проверяем готовность
            is_complete = await self._packaging_pipeline.is_packaging_complete(
                acct.id, acct.tenant_id, session
            )
            if is_complete:
                # Переход на COMMENTER_LIGHT
                if self._phase_controller:
                    acct.warmup_phase = "COMMENTER_LIGHT"
                else:
                    acct.warmup_phase = "COMMENTER_LIGHT"
                acct.next_session_at = utcnow() + timedelta(hours=8)
                log.info(
                    "warmup_scheduler: account %s packaging complete → COMMENTER_LIGHT",
                    acct.id,
                )

    # ── Планирование следующей сессии ────────────────────────────────

    async def _schedule_next(
        self, session: AsyncSession, acct: Account, persona: AccountPersona
    ) -> None:
        """Вычислить next_session_at с учётом персоны и фазы."""
        phase = acct.warmup_phase or "STEALTH"

        # Базовые интервалы по фазам (часы)
        phase_intervals = {
            "STEALTH": (8, 14),
            "EXPLORER": (6, 10),
            "PACKAGING": (2, 4),
            "COMMENTER_LIGHT": (5, 8),
            "COMMENTER_GROWING": (4, 7),
            "ACTIVE": (4, 6),
            "VETERAN": (3, 6),
        }

        min_h, max_h = phase_intervals.get(phase, (6, 10))
        base_hours = random.uniform(min_h, max_h)

        # ±30% jitter
        jitter = base_hours * random.uniform(-0.3, 0.3)
        interval_hours = max(1.0, base_hours + jitter)

        # Weekend multiplier
        now = utcnow()
        if now.weekday() >= 5:  # Saturday/Sunday
            activity = persona.weekend_activity if persona.weekend_activity else 0.6
            if random.random() > activity:
                interval_hours *= 1.5

        next_time = now + timedelta(hours=interval_hours)

        # Проверка: не попадёт ли в "ночь" по timezone персоны
        tz_offset = persona.timezone_offset or 3
        local_hour = (next_time.hour + tz_offset) % 24
        sleep_hour = persona.sleep_hour or 23
        wake_hour = persona.wake_hour or 7

        if local_hour >= sleep_hour or local_hour < wake_hour:
            # Перенести на утро + random 0-60 min
            hours_until_wake = (wake_hour - local_hour) % 24
            if hours_until_wake == 0:
                hours_until_wake = 24
            next_time = next_time + timedelta(
                hours=hours_until_wake,
                minutes=random.randint(0, 60),
            )

        # 70% шанс привязаться к peak_hour
        if persona.peak_hours and random.random() < 0.7:
            peak = random.choice(persona.peak_hours)
            # Найти ближайший peak_hour после next_time
            peak_utc = (peak - tz_offset) % 24
            target = next_time.replace(hour=peak_utc, minute=random.randint(0, 30))
            if target < next_time:
                target += timedelta(days=1)
            # Только если peak не слишком далеко (< 12 часов)
            if (target - next_time).total_seconds() < 43200:
                next_time = target

        acct.next_session_at = next_time

    # ── Утилиты ──────────────────────────────────────────────────────

    def _roll_session_type(self) -> str:
        """Случайный тип сессии по весам."""
        roll = random.random()
        if roll < 0.10:
            return "skip"
        elif roll < 0.30:
            return "quick_glance"
        elif roll < 0.80:
            return "normal"
        else:
            return "deep_dive"

    def _is_awake(self, acct: Account, now: datetime) -> bool:
        """Проверить: сейчас 'день' для аккаунта по его персоне."""
        # Без персоны — считаем что день (UTC 7-23)
        local_hour = now.hour + 3  # default UTC+3
        return 7 <= (local_hour % 24) < 23

    async def _defer_to_morning(
        self, session: AsyncSession, acct: Account
    ) -> None:
        """Отложить сессию на утро."""
        # Примерно 7:00 UTC+3 = 4:00 UTC + jitter
        tomorrow_morning = utcnow().replace(hour=4, minute=0, second=0) + timedelta(
            days=1, minutes=random.randint(0, 90)
        )
        acct.next_session_at = tomorrow_morning

    async def _log_activity(
        self,
        session: AsyncSession,
        tenant_id: int,
        account_id: int,
        action_type: str,
        success: bool,
        duration_ms: int | None = None,
        error_message: str | None = None,
        details: dict | None = None,
    ) -> None:
        """Записать действие в account_activity_logs."""
        try:
            entry = AccountActivityLog(
                tenant_id=tenant_id,
                account_id=account_id,
                action_type=action_type,
                success=success,
                duration_ms=duration_ms,
                error_message=str(error_message)[:500] if error_message else None,
                details=details,
                created_at=utcnow(),
            )
            session.add(entry)
            await session.flush()
        except Exception as exc:
            log.warning(
                "warmup_scheduler: failed to log activity %s for account %s: %s",
                action_type, account_id, exc,
            )

    # ── Периодические задачи ─────────────────────────────────────────

    async def _hourly_maintenance(self) -> None:
        """Каждый час: пересчёт health, проверка переходов фаз, авто-lift карантина."""
        if self._db_session_factory is None:
            return

        log.info("warmup_scheduler: hourly maintenance starting")
        try:
            async with self._db_session_factory() as session:
                # Авто-поднятие карантина
                now = utcnow()
                stmt = (
                    update(Account)
                    .where(
                        and_(
                            Account.quarantined_until.isnot(None),
                            Account.quarantined_until <= now,
                        )
                    )
                    .values(quarantined_until=None)
                )
                result = await session.execute(stmt)
                if result.rowcount > 0:
                    log.info(
                        "warmup_scheduler: lifted quarantine for %d accounts",
                        result.rowcount,
                    )

                # Проверка переходов фаз
                if self._phase_controller:
                    accts_stmt = select(Account).where(
                        and_(
                            Account.next_session_at.isnot(None),
                            Account.health_status.notin_(["dead", "frozen", "banned"]),
                        )
                    ).limit(200)
                    accts_result = await session.execute(accts_stmt)
                    for acct in accts_result.scalars():
                        try:
                            new_phase = await self._phase_controller.check_transition(
                                acct.id, acct.tenant_id, session
                            )
                            if new_phase:
                                log.info(
                                    "warmup_scheduler: account %s transitioned to %s",
                                    acct.id, new_phase,
                                )
                        except Exception as exc:
                            log.warning(
                                "warmup_scheduler: phase check failed for %s: %s",
                                acct.id, exc,
                            )

                await session.commit()
        except Exception as exc:
            log.error("warmup_scheduler: hourly maintenance failed: %s", exc)

    async def _daily_digest(self) -> None:
        """Раз в день: отправить дайджест в Telegram бот."""
        if not self._alert_service or self._db_session_factory is None:
            return

        log.info("warmup_scheduler: sending daily digest")
        try:
            async with self._db_session_factory() as session:
                # Считаем статистику за 24 часа
                since = utcnow() - timedelta(hours=24)

                # Активные аккаунты
                accts_stmt = select(Account).where(
                    Account.next_session_at.isnot(None)
                ).limit(100)
                accts_result = await session.execute(accts_stmt)
                all_accts = list(accts_result.scalars())

                # Действия за 24ч
                actions_stmt = select(AccountActivityLog).where(
                    AccountActivityLog.created_at >= since
                ).limit(5000)
                actions_result = await session.execute(actions_stmt)
                actions = list(actions_result.scalars())

                active_ids = {a.account_id for a in actions}
                success_count = sum(1 for a in actions if a.success)
                skip_count = sum(
                    1 for a in actions if a.action_type == "warmup_skip"
                )

                # Сессии
                session_starts = [
                    a for a in actions if a.action_type == "warmup_session_start"
                ]

                # Ошибки
                flood_count = sum(
                    1 for a in actions if a.action_type == "flood_wait"
                )
                spam_count = sum(
                    1 for a in actions if a.action_type == "spam_block"
                )
                frozen_count = sum(
                    1 for a in actions if a.action_type == "frozen"
                )

                # Сборка per-account stats
                account_stats = []
                for acct in all_accts:
                    acct_actions = [a for a in actions if a.account_id == acct.id]
                    acct_sessions = [
                        a for a in acct_actions
                        if a.action_type == "warmup_session_start"
                    ]

                    # Health score
                    hs_stmt = select(AccountHealthScore).where(
                        AccountHealthScore.account_id == acct.id
                    )
                    hs_result = await session.execute(hs_stmt)
                    hs = hs_result.scalar_one_or_none()

                    account_stats.append({
                        "phone": acct.phone,
                        "name": ".",
                        "phase": acct.warmup_phase or "STEALTH",
                        "day": acct.warmup_day or 0,
                        "health": hs.health_score if hs else 0,
                        "sessions": len(acct_sessions),
                    })

                stats = {
                    "active_count": len(active_ids),
                    "total_count": len(all_accts),
                    "sessions_24h": len(session_starts),
                    "actions_24h": len(actions),
                    "success_count": success_count,
                    "skip_count": skip_count,
                    "accounts": account_stats,
                    "errors": {
                        "flood": flood_count,
                        "spam": spam_count,
                        "frozen": frozen_count,
                    },
                    "next_packaging": [],
                }

                await self._alert_service.send_daily_digest(stats)

        except Exception as exc:
            log.error("warmup_scheduler: daily digest failed: %s", exc)

    # ── Принудительный запуск ────────────────────────────────────────

    async def force_session(self, account_id: int) -> dict:
        """Принудительно запустить сессию для аккаунта прямо сейчас."""
        if account_id in self._active_sessions:
            return {"status": "already_running"}

        if self._db_session_factory is None:
            return {"status": "error", "message": "no db session factory"}

        async with self._db_session_factory() as session:
            acct = await session.get(Account, account_id)
            if not acct:
                return {"status": "error", "message": "account not found"}
            tenant_id = acct.tenant_id

        task = asyncio.create_task(self._guarded_session(account_id, tenant_id))
        self._active_sessions[account_id] = task
        return {"status": "started", "account_id": account_id}

    # ── Пауза/Возобновление ──────────────────────────────────────────

    async def pause_all(self) -> int:
        """Приостановить scheduler (не останавливает активные сессии)."""
        self._running = False
        return self.active_count

    async def resume(self) -> None:
        """Возобновить scheduler."""
        if not self._running:
            await self.start()
