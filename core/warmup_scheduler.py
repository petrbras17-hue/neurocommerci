"""WarmupScheduler — сердце автономной системы прогрева «Живой Аккаунт».

Singleton, запускается при старте FastAPI (lifespan), работает 24/7.
Каждые 60 секунд опрашивает БД, находит аккаунты с наступившим next_session_at
и запускает для них warmup-сессии с учётом персоны, фазы и health score.

v2: реальные Telethon-действия через SessionPool + AntiDetection.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, update, and_, or_, text
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
log.setLevel(logging.INFO)  # всегда видим warmup-логи, независимо от root level
# Гарантируем вывод в stdout даже если root logger не сконфигурирован (uvicorn)
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setLevel(logging.INFO)
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    log.addHandler(_handler)

# ── Конфигурация ─────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 60          # интервал опроса БД
MAX_CONCURRENT_SESSIONS = 10    # макс параллельных Telethon-подключений
ANTI_BAN_DELAY_SEC = 5          # задержка между подключениями
HOURLY_MAINTENANCE_INTERVAL = 3600
DAILY_DIGEST_INTERVAL = 86400
MAX_TENANT_SCAN = 100           # макс tenant_id для brute-force итерации

# Фатальные ошибки Telethon — при этих ошибках аккаунт МЁРТВ, прогрев бесполезен
FATAL_AUTH_ERRORS = {
    "AuthKeyUnregisteredError",
    "SessionRevokedError",
    "AuthKeyDuplicatedError",
    "UserDeactivatedBanError",
    "UserDeactivatedError",
    "PhoneNumberBannedError",
}
# Максимум неудачных подряд сессий до auto-dead (на случай неизвестных ошибок)
MAX_CONSECUTIVE_FAILURES = 5

# Каналы для чтения, если у персоны нет preferred_channels
DEFAULT_CHANNELS = [
    "@durov", "@telegram", "@tginfo",
    "@vpngen", "@ntc_party", "@roskomsvoboda",
    "@digitalresistance", "@zatelecom",
    "@ai_machinelearning_big_data", "@neurohive",
    "@deep_learning", "@futureinsider",
    "@techinsider", "@habr_com",
    "@itsecfeed", "@kod_ru",
    "@astana_life", "@digitalkz", "@techkz",
]


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
        # Трекер последнего дня инкремента warmup_day (account_id -> date)
        self._last_day_increment: dict[int, str] = {}
        # Трекер подряд идущих неудачных сессий (account_id -> count)
        self._consecutive_failures: dict[int, int] = {}
        # Ленивый импорт модулей (избегаем циклических зависимостей)
        self._phase_controller = None
        self._persona_engine = None
        self._packaging_pipeline = None
        self._alert_service = None
        self._session_pool = None
        self._anti_detection_cls = None
        self._packaging_executor = None

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

        try:
            from core.session_pool import SessionPool
            from config import settings
            self._session_pool = SessionPool(sessions_dir=settings.sessions_path)
            log.info("warmup_scheduler: SessionPool initialized (dir=%s)", settings.sessions_path)
        except (ImportError, Exception) as exc:
            log.warning("warmup_scheduler: SessionPool not available: %s", exc)
            self._session_pool = None

        try:
            from core.anti_detection import AntiDetection
            self._anti_detection_cls = AntiDetection
        except ImportError:
            log.warning("warmup_scheduler: AntiDetection not available")
            self._anti_detection_cls = None

        # PackagingExecutor для фазы PACKAGING
        if self._session_pool and self._db_session_factory:
            try:
                from core.packaging_executor import PackagingExecutor
                self._packaging_executor = PackagingExecutor(
                    self._session_pool, self._db_session_factory,
                )
                log.info("warmup_scheduler: PackagingExecutor initialized")
            except (ImportError, Exception) as exc:
                log.warning("warmup_scheduler: PackagingExecutor not available: %s", exc)
                self._packaging_executor = None

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Запустить scheduler loop."""
        if self._running:
            log.warning("warmup_scheduler: already running, skipping duplicate start")
            return
        log.info(
            "warmup_scheduler: starting (max_slots=%d, poll_interval=%ds, anti_ban_delay=%ds)",
            MAX_CONCURRENT_SESSIONS,
            POLL_INTERVAL_SEC,
            ANTI_BAN_DELAY_SEC,
        )
        self._running = True
        self._task = asyncio.create_task(self._run_forever())
        self._task.add_done_callback(self._on_task_done)
        log.info("warmup_scheduler: background task created, scheduler is active")

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Callback: логирует завершение background task (в т.ч. аварийное)."""
        self._running = False  # сбрасываем флаг, чтобы restart был возможен
        if task.cancelled():
            log.info("warmup_scheduler: background task cancelled (normal shutdown)")
        elif task.exception():
            exc = task.exception()
            log.error(
                "warmup_scheduler: background task crashed unexpectedly: %s",
                exc,
                exc_info=exc,
            )
        else:
            log.info("warmup_scheduler: background task finished normally")

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

        # Проверяем, «день» ли сейчас по МСК
        MSK_OFFSET = 3
        local_hour = (now.hour + MSK_OFFSET) % 24
        is_daytime = 8 <= local_hour < 23

        # Ищем аккаунты, готовые к сессии (отдельная read-only транзакция)
        ready_accounts = await self._find_ready_accounts(now)

        launch_list: list[tuple[int, int]] = []
        defer_list: list[tuple[int, int]] = []
        for acct_id, tenant_id in ready_accounts:
            if acct_id in self._active_sessions:
                continue
            if not is_daytime:
                defer_list.append((acct_id, tenant_id))
            else:
                launch_list.append((acct_id, tenant_id))

        # Ночные аккаунты — откладываем до утра
        for acct_id, tenant_id in defer_list:
            try:
                async with self._db_session_factory() as session:
                    async with session.begin():
                        await session.execute(
                            text(f"SET LOCAL app.tenant_id = '{int(tenant_id)}'"),
                        )
                        acct = await session.get(Account, acct_id)
                        if acct:
                            await self._defer_to_morning(session, acct)
            except Exception as exc:
                log.warning("warmup_scheduler: defer_to_morning failed for %s: %s", acct_id, exc)

        # СРАЗУ ставим next_session_at в будущее чтобы следующий poll НЕ подхватил
        for acct_id, tenant_id in launch_list:
            try:
                async with self._db_session_factory() as session:
                    async with session.begin():
                        await session.execute(
                            text(f"SET LOCAL app.tenant_id = '{int(tenant_id)}'"),
                        )
                        acct = await session.get(Account, acct_id)
                        if acct:
                            # Временно ставим +4 часа, финальное время пересчитается в _finalize_session
                            acct.next_session_at = utcnow() + timedelta(hours=4)
            except Exception as exc:
                log.warning("warmup_scheduler: pre-lock next_session_at failed for %s: %s", acct_id, exc)

        # Запускаем дневные сессии
        for account_id, tenant_id in launch_list:
            task = asyncio.create_task(
                self._guarded_session(account_id, tenant_id)
            )
            self._active_sessions[account_id] = task

        # Периодические задачи
        if now.timestamp() - self._last_hourly > HOURLY_MAINTENANCE_INTERVAL:
            await self._hourly_maintenance()
            self._last_hourly = now.timestamp()

        if now.timestamp() - self._last_daily > DAILY_DIGEST_INTERVAL:
            await self._daily_digest()
            self._last_daily = now.timestamp()

    async def _find_ready_accounts(
        self, now: datetime
    ) -> list[tuple[int, int]]:
        """Найти аккаунты с наступившим next_session_at.

        Возвращает список (account_id, tenant_id).
        Перебираем tenant_id от 1 до MAX_TENANT_SCAN.
        """
        results: list[tuple[int, int]] = []

        for tid in range(1, MAX_TENANT_SCAN + 1):
            try:
                async with self._db_session_factory() as session:
                    async with session.begin():
                        await session.execute(
                            text(f"SET LOCAL app.tenant_id = '{int(tid)}'"),
                        )
                        stmt = (
                            select(Account.id, Account.tenant_id, Account.next_session_at)
                            .where(
                                and_(
                                    Account.next_session_at <= now,
                                    Account.next_session_at.isnot(None),
                                    Account.health_status.notin_(["dead", "frozen", "banned"]),
                                    or_(
                                        Account.quarantined_until.is_(None),
                                        Account.quarantined_until < now,
                                    ),
                                )
                            )
                            .order_by(Account.next_session_at.asc())
                            .limit(MAX_CONCURRENT_SESSIONS)
                        )
                        result = await session.execute(stmt)
                        rows = result.all()
                        for row in rows:
                            results.append((row[0], row[1]))
                        if rows:
                            log.info(
                                "warmup_scheduler: found %d ready accounts in tenant %d",
                                len(rows), tid,
                            )
            except Exception as exc:
                # Логируем ошибку только для tenant_id < 5 (реальные тенанты)
                if tid <= 3:
                    log.warning(
                        "warmup_scheduler: query failed for tenant %d: %s", tid, exc,
                    )

        results.sort(key=lambda r: r[0])
        return results[:MAX_CONCURRENT_SESSIONS * 2]

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
        """Выполнить одну warmup-сессию для аккаунта.

        Архитектура:
        1. Короткая DB-транзакция: загрузить аккаунт, персону, определить тип сессии
        2. Долгая Telethon-фаза: реальные действия с human-like задержками (БЕЗ открытой транзакции)
        3. Короткая DB-транзакция: записать результаты, запланировать следующую сессию
        """
        # ── Phase 1: загрузка данных ──
        session_plan = await self._plan_session(account_id, tenant_id)
        if session_plan is None:
            return

        session_type = session_plan["session_type"]
        channels = session_plan["channels"]
        action_limits = session_plan["action_limits"]
        warmup_phase = session_plan["warmup_phase"]
        warmup_day = session_plan["warmup_day"]
        persona_emoji_set = session_plan["emoji_set"]

        log.info(
            "warmup_scheduler: account %s phase=%s day=%s session_type=%s channels=%d",
            account_id, warmup_phase, warmup_day, session_type, len(channels),
        )

        if session_type == "skip":
            await self._finalize_session(
                account_id, tenant_id, 0, session_type, warmup_phase, warmup_day,
                details={"reason": "lazy_session"},
            )
            return

        # ── Phase 2: реальные Telethon-действия ──
        actions_done = 0
        telethon_ok = False
        action_details: list[dict] = []

        if self._session_pool is not None and self._db_session_factory is not None:
            try:
                actions_done, action_details, telethon_ok = await self._do_real_warmup(
                    account_id, tenant_id, session_type, channels,
                    action_limits, warmup_phase, persona_emoji_set,
                )
            except Exception as exc:
                log.error(
                    "warmup_scheduler: Telethon warmup failed for account %s: %s",
                    account_id, exc,
                )
                action_details.append({"action": "telethon_error", "error": str(exc)[:200]})
        else:
            log.warning(
                "warmup_scheduler: SessionPool unavailable, logging simulated actions for account %s",
                account_id,
            )
            # Fallback: log-only (без Telethon)
            for ch in channels:
                action_details.append({"action": "warmup_read_simulated", "channel": ch})
                actions_done += 1

        # ── Phase 3: записать результаты и запланировать следующую ──
        await self._finalize_session(
            account_id, tenant_id, actions_done, session_type,
            warmup_phase, warmup_day,
            details={
                "actions": action_details,
                "telethon": telethon_ok,
            },
        )

        # ── Phase 4: инкремент warmup_day (раз в календарные сутки) ──
        # ТОЛЬКО если были реальные Telethon-действия — иначе фейковый прогресс
        telethon_ok = (details or {}).get("telethon", False)
        if actions_done > 0 and telethon_ok:
            await self._maybe_increment_day(account_id, tenant_id)
        elif actions_done == 0:
            log.info(
                "warmup_scheduler: account %s — skipping day increment (0 actions, telethon=%s)",
                account_id, telethon_ok,
            )

    async def _plan_session(
        self, account_id: int, tenant_id: int
    ) -> dict | None:
        """Загрузить данные для сессии из БД (короткая транзакция)."""
        async with self._db_session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(f"SET LOCAL app.tenant_id = '{int(tenant_id)}'"),
                )
                acct = await session.get(Account, account_id)
                if acct is None:
                    return None

                # ── 48h post-purchase quarantine ──────────────────────────
                # Newly purchased accounts must sit idle for 48 hours before
                # ANY Telethon activity.  This prevents the early-activity
                # ban pattern that killed several accounts.
                if (acct.warmup_day or 0) == 0 and acct.created_at is not None:
                    now_check = utcnow()
                    age_hours = (now_check - acct.created_at).total_seconds() / 3600
                    if age_hours < 48:
                        quarantine_end = acct.created_at + timedelta(
                            hours=48 + random.uniform(0, 2)
                        )
                        acct.next_session_at = quarantine_end
                        log.info(
                            "warmup_scheduler: account %s in 48h post-purchase quarantine"
                            " (age=%.1fh), skipping — next attempt at %s",
                            account_id, age_hours, quarantine_end.strftime("%Y-%m-%d %H:%M"),
                        )
                        return None

                # Проверяем фазу PACKAGING
                if acct.warmup_phase == "PACKAGING":
                    await self._handle_packaging(session, acct, tenant_id)
                    return None

                # Загружаем персону
                persona_result = await session.execute(
                    select(AccountPersona).where(
                        AccountPersona.account_id == account_id
                    )
                )
                persona = persona_result.scalar_one_or_none()

                if persona is None or not persona.approved:
                    log.warning(
                        "warmup_scheduler: account %s has no approved persona, skipping",
                        account_id,
                    )
                    acct.next_session_at = utcnow() + timedelta(hours=6)
                    return None

                # Определяем тип сессии
                session_type = self._roll_session_type()

                # Получаем health score
                health_score = 100
                health_result = await session.execute(
                    select(AccountHealthScore).where(
                        AccountHealthScore.account_id == account_id
                    )
                )
                health_row = health_result.scalar_one_or_none()
                if health_row:
                    health_score = health_row.health_score or 100

                # Лимиты от PhaseController
                action_limits = {}
                if self._phase_controller:
                    action_limits = self._phase_controller.get_action_limits(
                        acct.warmup_phase or "STEALTH", health_score
                    )

                # Определяем каналы
                max_channels = {"quick_glance": 1, "normal": 3, "deep_dive": 5}.get(session_type, 2)
                channels = []
                if persona.preferred_channels:
                    channels = list(persona.preferred_channels)
                if not channels:
                    channels = list(DEFAULT_CHANNELS)
                random.shuffle(channels)
                channels = channels[:max_channels]

                return {
                    "session_type": session_type,
                    "channels": channels,
                    "action_limits": action_limits,
                    "warmup_phase": acct.warmup_phase or "STEALTH",
                    "warmup_day": acct.warmup_day or 0,
                    "emoji_set": persona.emoji_set or ["👍", "🔥", "❤️"],
                }

    async def _do_real_warmup(
        self,
        account_id: int,
        tenant_id: int,
        session_type: str,
        channels: list[str],
        action_limits: dict,
        warmup_phase: str,
        emoji_set: list[str],
    ) -> tuple[int, list[dict], bool]:
        """Выполнить реальные Telethon-действия с human-like задержками.

        Возвращает (actions_done, action_details, telethon_ok).
        """
        try:
            from telethon.errors import (
                FloodWaitError,
                ChannelPrivateError,
            )
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
        except ImportError:
            log.warning("warmup_scheduler: Telethon not installed")
            return 0, [{"action": "error", "error": "telethon_not_installed"}], False

        # Определяем anti-detection режим по фазе
        ad_mode = "conservative"
        if warmup_phase in ("COMMENTER_LIGHT", "COMMENTER_GROWING"):
            ad_mode = "moderate"
        elif warmup_phase in ("ACTIVE", "VETERAN"):
            ad_mode = "aggressive"

        anti_det = self._anti_detection_cls(mode=ad_mode) if self._anti_detection_cls else None

        actions_done = 0
        action_details: list[dict] = []
        client = None

        try:
            # SessionPool.get_client нужна DB-сессия для загрузки Account/Proxy на pool miss
            async with self._db_session_factory() as db_sess:
                async with db_sess.begin():
                    await db_sess.execute(
                        text(f"SET LOCAL app.tenant_id = '{int(tenant_id)}'"),
                    )
                    client = await self._session_pool.get_client(
                        account_id, db_session=db_sess, tenant_id=tenant_id,
                    )
        except Exception as exc:
            error_name = type(exc).__name__
            log.warning(
                "warmup_scheduler: cannot get Telethon client for account %s: %s",
                account_id, exc,
            )
            # ── АВТОМАТИЧЕСКАЯ ПОМЕТКА DEAD при фатальных ошибках ──
            if error_name in FATAL_AUTH_ERRORS or any(
                marker in str(exc).lower()
                for marker in ("deactivated", "not logged in", "auth key", "session revoked", "banned")
            ):
                log.error(
                    "warmup_scheduler: FATAL auth error for account %s — marking DEAD: %s",
                    account_id, exc,
                )
                await self._mark_dead(account_id, tenant_id, reason=f"{error_name}: {exc}")
            return 0, [{"action": "client_error", "error": f"{error_name}: {exc}"}], False

        try:
            # ── Читаем каналы с человеческими паузами ──
            for i, channel in enumerate(channels):
                # Пауза ПЕРЕД каждым каналом (кроме первого): 15-45 сек
                if i > 0 and anti_det:
                    delay = random.uniform(15, 45)
                    log.info(
                        "warmup_scheduler: account %s inter-channel pause %.0fs",
                        account_id, delay,
                    )
                    await asyncio.sleep(delay)

                try:
                    entity = await client.get_entity(channel)

                    # Пауза после get_entity, как будто UI загружается: 2-5 сек
                    await asyncio.sleep(random.uniform(2, 5))

                    # Читаем 3-8 последних сообщений
                    msg_count = random.randint(3, 8)
                    messages = await client.get_messages(entity, limit=msg_count)

                    if messages and anti_det:
                        # simulate_reading: 3-12 сек на сообщение, скроллинг
                        await anti_det.simulate_reading(client, list(messages))

                    action_details.append({
                        "action": "read_channel",
                        "channel": channel,
                        "messages_read": len(messages) if messages else 0,
                    })
                    actions_done += 1

                except ChannelPrivateError:
                    log.info("warmup_scheduler: channel %s private, skipping", channel)
                    action_details.append({
                        "action": "channel_private",
                        "channel": channel,
                    })
                except FloodWaitError as exc:
                    log.warning(
                        "warmup_scheduler: FloodWait %ds on account %s reading %s",
                        exc.seconds, account_id, channel,
                    )
                    action_details.append({
                        "action": "flood_wait",
                        "channel": channel,
                        "seconds": exc.seconds,
                    })
                    # Ставим карантин, дальше не идём
                    await self._quarantine_on_flood(account_id, tenant_id, exc.seconds)
                    return actions_done, action_details, True
                except Exception as exc:
                    error_name = type(exc).__name__
                    # Фатальные ошибки — аккаунт мёртв
                    if error_name in FATAL_AUTH_ERRORS or any(
                        marker in str(exc).lower()
                        for marker in ("deactivated", "not logged in", "auth key", "session revoked", "banned")
                    ):
                        log.error(
                            "warmup_scheduler: FATAL error for account %s during channel read — marking DEAD: %s",
                            account_id, exc,
                        )
                        await self._mark_dead(account_id, tenant_id, reason=f"{error_name}: {exc}")
                        return actions_done, action_details, False
                    if "FrozenMethodInvalid" in error_name:
                        log.warning(
                            "warmup_scheduler: account %s FROZEN during warmup",
                            account_id,
                        )
                        await self._mark_frozen(account_id, tenant_id)
                        return actions_done, action_details, True
                    log.info(
                        "warmup_scheduler: read %s error for account %s: %s",
                        channel, account_id, exc,
                    )
                    action_details.append({
                        "action": "read_error",
                        "channel": channel,
                        "error": str(exc)[:100],
                    })

            # ── Реакции (если фаза позволяет) ──
            max_reactions = action_limits.get("max_reactions", 0)
            if max_reactions > 0 and channels:
                reactions_to_do = random.randint(0, min(max_reactions, len(channels)))
                for r_idx in range(reactions_to_do):
                    # AntiDetection skip check (30% вероятность пропуска)
                    if anti_det and anti_det.should_skip_action():
                        continue

                    # Пауза перед реакцией: 20-60 сек (как будто перечитываешь пост)
                    await asyncio.sleep(random.uniform(20, 60))

                    ch = channels[r_idx % len(channels)]
                    emoji = random.choice(emoji_set)
                    try:
                        entity = await client.get_entity(ch)
                        msgs = await client.get_messages(entity, limit=5)
                        if msgs:
                            msg = random.choice(msgs)
                            await client(
                                SendReactionRequest(
                                    peer=entity,
                                    msg_id=msg.id,
                                    reaction=[ReactionEmoji(emoticon=emoji)],
                                )
                            )
                            action_details.append({
                                "action": "reaction",
                                "channel": ch,
                                "emoji": emoji,
                                "msg_id": msg.id,
                            })
                            actions_done += 1
                    except FloodWaitError as exc:
                        log.warning(
                            "warmup_scheduler: FloodWait %ds on reaction, account %s",
                            exc.seconds, account_id,
                        )
                        await self._quarantine_on_flood(account_id, tenant_id, exc.seconds)
                        return actions_done, action_details, True
                    except Exception as exc:
                        if "FrozenMethodInvalid" in type(exc).__name__:
                            await self._mark_frozen(account_id, tenant_id)
                            return actions_done, action_details, True
                        log.info(
                            "warmup_scheduler: reaction error on %s: %s", ch, exc,
                        )

            # ── Чтение диалогов (50% шанс, только normal+deep_dive) ──
            if session_type in ("normal", "deep_dive") and random.random() < 0.5:
                # Пауза перед переключением на диалоги: 10-30 сек
                await asyncio.sleep(random.uniform(10, 30))
                try:
                    dialogs = await client.get_dialogs(limit=random.randint(3, 8))
                    if dialogs:
                        dialog = random.choice(dialogs)
                        msgs = await client.get_messages(dialog.entity, limit=3)
                        if msgs and anti_det:
                            await anti_det.simulate_reading(client, list(msgs))
                        action_details.append({
                            "action": "dialog_read",
                            "dialog_count": len(dialogs),
                        })
                        actions_done += 1
                except FloodWaitError as exc:
                    await self._quarantine_on_flood(account_id, tenant_id, exc.seconds)
                    return actions_done, action_details, True
                except Exception as exc:
                    if "FrozenMethodInvalid" in type(exc).__name__:
                        await self._mark_frozen(account_id, tenant_id)
                        return actions_done, action_details, True
                    log.info("warmup_scheduler: dialog_read error: %s", exc)

            return actions_done, action_details, True

        finally:
            # ВСЕГДА освобождаем клиент
            if client is not None:
                try:
                    await self._session_pool.release_client(account_id)
                except Exception as exc:
                    log.warning(
                        "warmup_scheduler: release_client failed for %s: %s",
                        account_id, exc,
                    )

    async def _finalize_session(
        self,
        account_id: int,
        tenant_id: int,
        actions_done: int,
        session_type: str,
        warmup_phase: str,
        warmup_day: int,
        details: dict | None = None,
    ) -> None:
        """Записать результаты сессии и запланировать следующую (короткая транзакция)."""
        async with self._db_session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(f"SET LOCAL app.tenant_id = '{int(tenant_id)}'"),
                )
                acct = await session.get(Account, account_id)
                if acct is None:
                    return

                # Загружаем персону для _schedule_next
                persona_result = await session.execute(
                    select(AccountPersona).where(
                        AccountPersona.account_id == account_id
                    )
                )
                persona = persona_result.scalar_one_or_none()

                # Логируем session start
                await self._log_activity(
                    session, tenant_id, account_id,
                    "warmup_session_start", True,
                    details={
                        "session_type": session_type,
                        "phase": warmup_phase,
                        "day": warmup_day,
                    },
                )

                # Логируем session end
                await self._log_activity(
                    session, tenant_id, account_id,
                    "warmup_session_end", True,
                    details={
                        "actions_done": actions_done,
                        "session_type": session_type,
                        **(details or {}),
                    },
                )

                # ── Consecutive failure tracking ──────────────────────────
                # A "failure" is any session where Telethon reported False
                # (auth/network error) AND zero actions were completed.
                # Pure skips (lazy_session) do not count as failures.
                telethon_flag = (details or {}).get("telethon", None)
                is_lazy_skip = (details or {}).get("reason") == "lazy_session"

                if not is_lazy_skip:
                    if telethon_flag is False and actions_done == 0:
                        # Increment failure counter
                        self._consecutive_failures[account_id] = (
                            self._consecutive_failures.get(account_id, 0) + 1
                        )
                        fail_count = self._consecutive_failures[account_id]
                        log.warning(
                            "warmup_scheduler: account %s consecutive_failures=%d/%d",
                            account_id, fail_count, MAX_CONSECUTIVE_FAILURES,
                        )
                        if fail_count >= MAX_CONSECUTIVE_FAILURES:
                            log.error(
                                "warmup_scheduler: account %s reached %d consecutive failures"
                                " — marking DEAD (unknown error pattern)",
                                account_id, fail_count,
                            )
                            # Mark inside current transaction
                            acct.health_status = "dead"
                            acct.status = "dead"
                            acct.warmup_phase = "DEAD"
                            acct.next_session_at = None
                            await self._log_activity(
                                session, tenant_id, account_id,
                                "account_dead", False,
                                details={
                                    "source": "consecutive_failures",
                                    "count": fail_count,
                                },
                            )
                            # Fire alert + auto-rotation outside this transaction
                            async def _post_dead_hooks(
                                _acct_id: int = account_id,
                                _tenant_id: int = tenant_id,
                                _count: int = fail_count,
                            ) -> None:
                                if self._alert_service:
                                    try:
                                        await self._alert_service.send_alert(
                                            level="critical",
                                            title=f"АККАУНТ МЁРТВ (consecutive_failures): {_acct_id}",
                                            message=(
                                                f"Аккаунт ID={_acct_id} помечен как DEAD.\n"
                                                f"Причина: {_count} подряд неудачных сессий (Telethon=False, actions=0).\n\n"
                                                "Прогрев остановлен. Запускается авто-ротация."
                                            ),
                                        )
                                    except Exception as exc:
                                        log.warning("warmup_scheduler: dead alert failed: %s", exc)
                                try:
                                    from core.account_rotation import AccountRotation
                                    rotation = AccountRotation()
                                    await rotation.check_and_rotate(self._db_session_factory)
                                except Exception as exc:
                                    log.warning(
                                        "warmup_scheduler: auto-rotation after consecutive failures failed: %s",
                                        exc,
                                    )

                            import asyncio as _asyncio
                            _asyncio.create_task(_post_dead_hooks())
                            return  # stop further processing for this account
                    elif telethon_flag is True and actions_done > 0:
                        # Successful session — reset counter
                        if account_id in self._consecutive_failures:
                            log.info(
                                "warmup_scheduler: account %s consecutive_failures reset (successful session)",
                                account_id,
                            )
                            del self._consecutive_failures[account_id]

                # Планируем следующую сессию
                if persona:
                    await self._schedule_next(session, acct, persona)
                else:
                    acct.next_session_at = utcnow() + timedelta(hours=4)

                log.info(
                    "warmup_scheduler: account %s session complete, actions=%d, telethon=%s, next=%s",
                    account_id, actions_done,
                    details.get("telethon", "?") if details else "?",
                    acct.next_session_at,
                )

    async def _maybe_increment_day(self, account_id: int, tenant_id: int) -> None:
        """Инкрементировать warmup_day раз в календарные сутки (UTC)."""
        today = utcnow().strftime("%Y-%m-%d")
        last = self._last_day_increment.get(account_id)
        if last == today:
            return  # Уже инкрементировали сегодня

        async with self._db_session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(f"SET LOCAL app.tenant_id = '{int(tenant_id)}'"),
                )
                acct = await session.get(Account, account_id)
                if acct is None:
                    return

                old_day = acct.warmup_day or 0
                acct.warmup_day = old_day + 1
                log.info(
                    "warmup_scheduler: account %s warmup_day %d -> %d",
                    account_id, old_day, acct.warmup_day,
                )

                # Проверяем переход фазы
                if self._phase_controller:
                    try:
                        new_phase = await self._phase_controller.check_transition(
                            account_id, tenant_id, session,
                        )
                        if new_phase:
                            log.info(
                                "warmup_scheduler: account %s phase transition -> %s",
                                account_id, new_phase,
                            )
                    except Exception as exc:
                        log.warning(
                            "warmup_scheduler: phase check after day increment failed: %s",
                            exc,
                        )

        self._last_day_increment[account_id] = today

    # ── Вспомогательные Telethon-методы ──────────────────────────────

    async def _quarantine_on_flood(
        self, account_id: int, tenant_id: int, seconds: int
    ) -> None:
        """Поставить аккаунт в карантин после FloodWait."""
        # Минимум 2 часа карантина, максимум 48 часов
        quarantine_hours = max(2, min(48, seconds / 3600 * 3))
        async with self._db_session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(f"SET LOCAL app.tenant_id = '{int(tenant_id)}'"),
                )
                acct = await session.get(Account, account_id)
                if acct:
                    acct.quarantined_until = utcnow() + timedelta(hours=quarantine_hours)
                    acct.next_session_at = acct.quarantined_until + timedelta(
                        minutes=random.randint(10, 60),
                    )
                    log.warning(
                        "warmup_scheduler: account %s quarantined for %.1f hours (flood_wait=%ds)",
                        account_id, quarantine_hours, seconds,
                    )

                    # Лог в activity
                    await self._log_activity(
                        session, tenant_id, account_id,
                        "flood_wait", False,
                        details={"seconds": seconds, "quarantine_hours": quarantine_hours},
                    )

    async def _mark_frozen(self, account_id: int, tenant_id: int) -> None:
        """Пометить аккаунт как frozen."""
        async with self._db_session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(f"SET LOCAL app.tenant_id = '{int(tenant_id)}'"),
                )
                acct = await session.get(Account, account_id)
                if acct:
                    acct.health_status = "frozen"
                    acct.next_session_at = None  # Не планируем сессии
                    log.warning(
                        "warmup_scheduler: account %s marked FROZEN",
                        account_id,
                    )
                    await self._log_activity(
                        session, tenant_id, account_id,
                        "frozen", False,
                        details={"source": "warmup_scheduler"},
                    )

    async def _mark_dead(
        self, account_id: int, tenant_id: int, reason: str = ""
    ) -> None:
        """Пометить аккаунт как dead и отправить КРИТИЧЕСКИЙ алерт оператору."""
        async with self._db_session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(f"SET LOCAL app.tenant_id = '{int(tenant_id)}'"),
                )
                acct = await session.get(Account, account_id)
                if acct:
                    acct.health_status = "dead"
                    acct.status = "dead"
                    acct.warmup_phase = "DEAD"
                    acct.next_session_at = None  # Навсегда остановить прогрев
                    log.error(
                        "warmup_scheduler: account %s (phone=%s) marked DEAD — %s",
                        account_id, acct.phone, reason[:200],
                    )
                    await self._log_activity(
                        session, tenant_id, account_id,
                        "account_dead", False,
                        details={"source": "warmup_scheduler", "reason": reason[:500]},
                    )

        # Отправляем алерт оператору
        if self._alert_service:
            try:
                await self._alert_service.send_alert(
                    level="critical",
                    title=f"АККАУНТ МЁРТВ: {account_id}",
                    message=(
                        f"Аккаунт ID={account_id} помечен как DEAD.\n"
                        f"Причина: {reason[:300]}\n\n"
                        "Прогрев остановлен. Аккаунт НЕ восстановим без SMS-кода."
                    ),
                )
            except Exception as exc:
                log.warning("warmup_scheduler: alert send failed: %s", exc)

        # Trigger auto-rotation check
        try:
            from core.account_rotation import AccountRotation
            rotation = AccountRotation()
            await rotation.check_and_rotate(self._db_session_factory)
        except Exception as exc:
            log.warning("warmup_scheduler: auto-rotation check failed: %s", exc)

    # ── Обработка фазы PACKAGING ──────────────────────────────────────

    async def _handle_packaging(
        self, session: AsyncSession, acct: Account, tenant_id: int
    ) -> None:
        """Обработать фазу PACKAGING — выполнить следующие шаги упаковки.

        Использует PackagingExecutor для реальных Telethon-действий.
        Максимум 2 шага за сессию, между сессиями 2-4 часа.
        """
        # Проверяем есть ли готовый preset
        preset_stmt = select(AccountPackagingPreset).where(
            and_(
                AccountPackagingPreset.account_id == acct.id,
                AccountPackagingPreset.status.in_(["ready", "scheduled", "applied"]),
            )
        )
        preset_result = await session.execute(preset_stmt)
        preset = preset_result.scalar_one_or_none()

        if preset is None:
            # Нет preset — автоматически создаём из шаблона
            preset = await self._auto_create_preset(session, acct, tenant_id)
            if preset is None:
                acct.next_session_at = utcnow() + timedelta(hours=4)
                log.info(
                    "warmup_scheduler: account %s needs packaging preset but template not found",
                    acct.id,
                )
                return

        if not self._packaging_executor:
            log.warning("warmup_scheduler: PackagingExecutor not available")
            acct.next_session_at = utcnow() + timedelta(hours=6)
            return

        # Выполняем следующие шаги (вне текущей DB-транзакции)
        # Сначала завершаем текущую транзакцию, потом executor сам откроет свои
        acct.next_session_at = utcnow() + timedelta(hours=random.randint(2, 4))

        # Запускаем executor после выхода из транзакции (через _plan_session)
        # Но _handle_packaging вызывается внутри session.begin() в _plan_session,
        # поэтому планируем выполнение как отдельную задачу
        asyncio.create_task(
            self._run_packaging_steps(acct.id, tenant_id, preset.id)
        )
        log.info(
            "warmup_scheduler: account %s packaging steps scheduled, next check in 2-4h",
            acct.id,
        )

    async def _run_packaging_steps(
        self, account_id: int, tenant_id: int, preset_id: int
    ) -> None:
        """Выполнить packaging шаги вне DB-транзакции _plan_session."""
        # Маленькая пауза перед началом (чтобы _plan_session завершился)
        await asyncio.sleep(random.uniform(5, 15))

        try:
            # Загружаем preset
            async with self._db_session_factory() as session:
                async with session.begin():
                    await session.execute(
                        text(f"SET LOCAL app.tenant_id = '{int(tenant_id)}'"),
                    )
                    preset = await session.get(AccountPackagingPreset, preset_id)
                    if not preset:
                        log.warning("packaging: preset %d not found", preset_id)
                        return

                    result = await self._packaging_executor.execute_next_steps(
                        account_id, tenant_id, preset,
                    )

            log.info(
                "warmup_scheduler: packaging result for account %s: %s",
                account_id, result,
            )

            # Если все шаги готовы — переводим в COMMENTER_LIGHT
            if result.get("status") == "all_done":
                async with self._db_session_factory() as session:
                    async with session.begin():
                        await session.execute(
                            text(f"SET LOCAL app.tenant_id = '{int(tenant_id)}'"),
                        )
                        acct = await session.get(Account, account_id)
                        if acct:
                            acct.warmup_phase = "COMMENTER_LIGHT"
                            acct.next_session_at = utcnow() + timedelta(
                                hours=random.randint(6, 10),
                            )
                            log.info(
                                "warmup_scheduler: account %s PACKAGING COMPLETE -> COMMENTER_LIGHT",
                                account_id,
                            )

                        # Обновляем статус preset
                        preset_obj = await session.get(AccountPackagingPreset, preset_id)
                        if preset_obj:
                            preset_obj.status = "applied"
                            preset_obj.applied_at = utcnow()

        except Exception as exc:
            log.error(
                "warmup_scheduler: packaging steps failed for account %s: %s",
                account_id, exc, exc_info=True,
            )

    async def _auto_create_preset(
        self, session: AsyncSession, acct: Account, tenant_id: int,
    ) -> AccountPackagingPreset:
        """Автоматически создать preset из шаблона dartvpn.json."""
        import json as _json
        template_path = Path(__file__).parent.parent / "data" / "packaging_templates" / "dartvpn.json"
        if not template_path.exists():
            return None

        try:
            with open(template_path, "r", encoding="utf-8") as f:
                template = _json.load(f)
        except Exception:
            return None

        # Загружаем персону для определения пола
        persona_result = await session.execute(
            select(AccountPersona).where(AccountPersona.account_id == acct.id)
        )
        persona = persona_result.scalar_one_or_none()

        # Определяем имя
        names_cfg = template.get("name_generation", {})
        gender = "male"
        if persona and persona.persona_data:
            gender = persona.persona_data.get("gender", "male")

        if gender == "female":
            first_names = names_cfg.get("kz_female_names", ["Айгерим"])
            last_names = names_cfg.get("kz_last_names_female", ["Касымова"])
        else:
            first_names = names_cfg.get("kz_male_names", ["Дамир"])
            last_names = names_cfg.get("kz_last_names", ["Касымов"])

        display_name = f"{random.choice(first_names)} {random.choice(last_names)}"

        # Bio
        bios = template.get("profile", {}).get("bio_templates", [""])
        bio = random.choice(bios) if bios else ""

        # Канал (рандомный из списка)
        ch_names = template.get("channel", {}).get("names", [])
        if ch_names:
            ch = random.choice(ch_names)
            ch_name = ch["name"]
            ch_desc = ch["desc"]
        else:
            ch_name = "Digital лайфхаки"
            ch_desc = "Полезные сервисы"

        # Пост
        post_template = template.get("post", {}).get("template", "")
        bot_link = template.get("post", {}).get("bot_link", "")
        post_text = post_template.replace("{LINK}", bot_link)

        preset = AccountPackagingPreset(
            tenant_id=tenant_id,
            account_id=acct.id,
            display_name=display_name,
            bio=bio,
            channel_name=ch_name,
            channel_description=ch_desc,
            channel_pin_text=post_text,
            source="template",
            status="ready",
            generation_params={"template": "dartvpn", "gender": gender},
        )
        session.add(preset)
        await session.flush()

        log.info(
            "warmup_scheduler: auto-created preset %d for account %s: name='%s', channel='%s'",
            preset.id, acct.id, display_name, ch_name,
        )
        return preset

    # ── Планирование следующей сессии ────────────────────────────────

    async def _schedule_next(
        self, session: AsyncSession, acct: Account, persona: AccountPersona
    ) -> None:
        """Вычислить next_session_at с учётом персоны и фазы."""
        phase = acct.warmup_phase or "STEALTH"

        # Базовые интервалы по фазам (часы)
        phase_intervals = {
            "STEALTH": (3, 5),
            "EXPLORER": (2.5, 4),
            "PACKAGING": (2, 4),
            "COMMENTER_LIGHT": (2, 4),
            "COMMENTER_GROWING": (1.5, 3),
            "ACTIVE": (1, 2.5),
            "VETERAN": (1, 2),
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
            peak_utc = (peak - tz_offset) % 24
            target = next_time.replace(hour=peak_utc, minute=random.randint(0, 30))
            if target < next_time:
                target += timedelta(days=1)
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
        """Проверить: сейчас 'день' для аккаунта по GMT+3 (Москва)."""
        MSK_OFFSET = 3
        local_hour = (now.hour + MSK_OFFSET) % 24
        return 8 <= local_hour < 23

    async def _defer_to_morning(
        self, session: AsyncSession, acct: Account
    ) -> None:
        """Отложить сессию на утро по МСК (GMT+3)."""
        now = utcnow()
        tomorrow_morning = now.replace(hour=5, minute=0, second=0, microsecond=0)
        if tomorrow_morning <= now:
            tomorrow_morning += timedelta(days=1)
        tomorrow_morning += timedelta(
            minutes=random.randint(0, 90),
            seconds=random.randint(0, 59),
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
            for tenant_id in range(1, MAX_TENANT_SCAN + 1):
                async with self._db_session_factory() as session:
                    async with session.begin():
                        await session.execute(
                            text(f"SET LOCAL app.tenant_id = '{tenant_id}'")
                        )
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
                                "warmup_scheduler: lifted quarantine for %d accounts (tenant %d)",
                                result.rowcount, tenant_id,
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
                                        acct.id, tenant_id, session
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
        except Exception as exc:
            log.error("warmup_scheduler: hourly maintenance failed: %s", exc)

    async def _daily_digest(self) -> None:
        """Раз в день: отправить дайджест в Telegram бот."""
        if not self._alert_service or self._db_session_factory is None:
            return

        log.info("warmup_scheduler: sending daily digest")
        try:
            all_accts: list = []
            actions: list = []
            since = utcnow() - timedelta(hours=24)

            for tenant_id in range(1, MAX_TENANT_SCAN + 1):
                async with self._db_session_factory() as session:
                    async with session.begin():
                        await session.execute(
                            text(f"SET LOCAL app.tenant_id = '{tenant_id}'")
                        )
                        accts_stmt = select(Account).where(
                            Account.next_session_at.isnot(None)
                        ).limit(100)
                        accts_result = await session.execute(accts_stmt)
                        tenant_accts = list(accts_result.scalars())
                        all_accts.extend(tenant_accts)

                        actions_stmt = select(AccountActivityLog).where(
                            AccountActivityLog.created_at >= since
                        ).limit(5000)
                        actions_result = await session.execute(actions_stmt)
                        actions.extend(list(actions_result.scalars()))

            active_ids = {a.account_id for a in actions}
            success_count = sum(1 for a in actions if a.success)
            skip_count = sum(
                1 for a in actions if a.action_type == "warmup_skip"
            )
            session_starts = [
                a for a in actions if a.action_type == "warmup_session_start"
            ]
            flood_count = sum(1 for a in actions if a.action_type == "flood_wait")
            spam_count = sum(1 for a in actions if a.action_type == "spam_block")
            frozen_count = sum(1 for a in actions if a.action_type == "frozen")

            account_stats = []
            for acct in all_accts:
                acct_actions = [a for a in actions if a.account_id == acct.id]
                acct_sessions = [
                    a for a in acct_actions
                    if a.action_type == "warmup_session_start"
                ]
                account_stats.append({
                    "phone": acct.phone,
                    "name": ".",
                    "phase": acct.warmup_phase or "STEALTH",
                    "day": acct.warmup_day or 0,
                    "health": 0,
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

        # Find account's tenant by iterating RLS contexts
        tenant_id = None
        for tid in range(1, MAX_TENANT_SCAN + 1):
            async with self._db_session_factory() as session:
                async with session.begin():
                    await session.execute(text(f"SET LOCAL app.tenant_id = '{tid}'"))
                    acct = await session.get(Account, account_id)
                    if acct:
                        tenant_id = tid
                        break
        if tenant_id is None:
            return {"status": "error", "message": "account not found"}

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
