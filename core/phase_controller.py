"""
PhaseController — управление фазами автономного прогрева Telegram-аккаунтов.

Определяет 7 фаз прогрева, регулирует лимиты действий с учётом health score,
обрабатывает инциденты (flood_wait, spam_block, frozen) и управляет переходами.
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Account, AccountActivityLog, AccountHealthScore, AccountPhaseHistory
from utils.helpers import utcnow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

PHASES: list[str] = [
    "STEALTH",
    "EXPLORER",
    "PACKAGING",
    "COMMENTER_LIGHT",
    "COMMENTER_GROWING",
    "ACTIVE",
    "VETERAN",
]

PHASE_CONFIG: dict[str, dict] = {
    "STEALTH": {
        "days": (0, 2),
        "sessions_per_day": (1, 2),
        "duration_min": (15, 25),
        "max_reactions": 0,
        "max_comments": 0,
        "max_joins": 0,
    },
    "EXPLORER": {
        "days": (3, 3),
        "sessions_per_day": (2, 3),
        "duration_min": (20, 35),
        "max_reactions": 3,
        "max_comments": 0,
        "max_joins": 2,
    },
    "PACKAGING": {
        "days": (4, 4),
        "sessions_per_day": (1, 1),
        "duration_min": (5, 10),
        "max_reactions": 0,
        "max_comments": 0,
        "max_joins": 0,
    },
    "COMMENTER_LIGHT": {
        "days": (5, 7),
        "sessions_per_day": (2, 4),
        "duration_min": (25, 40),
        "max_reactions": 5,
        "max_comments": 3,
        "max_joins": 1,
    },
    "COMMENTER_GROWING": {
        "days": (8, 14),
        "sessions_per_day": (2, 4),
        "duration_min": (25, 40),
        "max_reactions": 8,
        "max_comments": 6,
        "max_joins": 1,
    },
    "ACTIVE": {
        "days": (15, 29),
        "sessions_per_day": (3, 4),
        "duration_min": (30, 50),
        "max_reactions": 12,
        "max_comments": 10,
        "max_joins": 2,
    },
    "VETERAN": {
        "days": (30, 999),
        "sessions_per_day": (3, 5),
        "duration_min": (30, 60),
        "max_reactions": 20,
        "max_comments": 15,
        "max_joins": 3,
    },
}

# Сколько секунд в часе — для удобства расчётов квантина
_HOURS = 3600


class PhaseController:
    """
    Контроллер фаз автономного прогрева.

    Управляет переходами между фазами, вычисляет лимиты действий с поправкой
    на health score, обрабатывает инциденты и обновляет состояние аккаунта
    в базе данных через AsyncSession с применением RLS-контекста.
    """

    # ------------------------------------------------------------------
    # Утилиты
    # ------------------------------------------------------------------

    @staticmethod
    def get_phase_for_day(day: int) -> str:
        """
        Возвращает название фазы для указанного дня прогрева.

        :param day: День прогрева (0-based).
        :return: Название фазы из списка PHASES.
        """
        for phase_name in PHASES:
            low, high = PHASE_CONFIG[phase_name]["days"]
            if low <= day <= high:
                return phase_name
        # Если день вышел за пределы — VETERAN
        return "VETERAN"

    @staticmethod
    def get_action_limits(phase: str, health_score: int) -> dict:
        """
        Возвращает скорректированные лимиты действий для фазы с учётом health score.

        Правила корректировки:
        - health 80-100: 100% от потолка фазы
        - health 60-79: floor(потолок * 0.7)
        - health 40-59: floor(потолок * 0.4)
        - health < 40: лимиты фазы на один шаг ниже

        :param phase: Название текущей фазы.
        :param health_score: Текущий health score (0-100).
        :return: Словарь с ключами max_reactions, max_comments, max_joins.
        """
        if health_score < 40:
            # Откатываем лимиты к предыдущей фазе
            idx = PHASES.index(phase)
            effective_phase = PHASES[max(0, idx - 1)]
            cfg = PHASE_CONFIG[effective_phase]
        else:
            cfg = PHASE_CONFIG[phase]

        raw = {
            "max_reactions": cfg["max_reactions"],
            "max_comments": cfg["max_comments"],
            "max_joins": cfg["max_joins"],
        }

        if health_score < 40:
            # Уже применили пониженную фазу — используем как есть
            return raw

        if health_score >= 80:
            factor = 1.0
        elif health_score >= 60:
            factor = 0.7
        else:
            # 40-59
            factor = 0.4

        return {
            "max_reactions": math.floor(raw["max_reactions"] * factor),
            "max_comments": math.floor(raw["max_comments"] * factor),
            "max_joins": math.floor(raw["max_joins"] * factor),
        }

    @staticmethod
    def get_session_params(phase: str, health_score: int) -> dict:
        """
        Возвращает параметры сессий для фазы с поправкой на health score.

        Параметр sessions_per_day и duration_min уменьшаются пропорционально
        при низком health — та же логика коэффициентов, что у get_action_limits.

        :param phase: Название фазы.
        :param health_score: Текущий health score (0-100).
        :return: Словарь с ключами sessions_per_day (tuple), duration_min (tuple).
        """
        cfg = PHASE_CONFIG[phase]
        spd_lo, spd_hi = cfg["sessions_per_day"]
        dur_lo, dur_hi = cfg["duration_min"]

        if health_score >= 80:
            factor = 1.0
        elif health_score >= 60:
            factor = 0.7
        elif health_score >= 40:
            factor = 0.4
        else:
            factor = 0.25

        return {
            "sessions_per_day": (
                max(1, math.floor(spd_lo * factor)),
                max(1, math.floor(spd_hi * factor)),
            ),
            "duration_min": (
                max(5, math.floor(dur_lo * factor)),
                max(5, math.floor(dur_hi * factor)),
            ),
        }

    @staticmethod
    def get_anti_detection_mode(phase: str) -> str:
        """
        Возвращает режим антидетекта для указанной фазы.

        - STEALTH, EXPLORER, PACKAGING -> conservative
        - COMMENTER_LIGHT, COMMENTER_GROWING -> moderate
        - ACTIVE, VETERAN -> aggressive

        :param phase: Название фазы.
        :return: Строка режима: 'conservative', 'moderate' или 'aggressive'.
        """
        conservative = {"STEALTH", "EXPLORER", "PACKAGING"}
        moderate = {"COMMENTER_LIGHT", "COMMENTER_GROWING"}

        if phase in conservative:
            return "conservative"
        if phase in moderate:
            return "moderate"
        return "aggressive"

    # ------------------------------------------------------------------
    # Методы, работающие с БД
    # ------------------------------------------------------------------

    @staticmethod
    async def _set_rls(session: AsyncSession, tenant_id: int) -> None:
        """Устанавливает RLS-контекст для текущей транзакции."""
        await session.execute(
            text("SET LOCAL \"app.tenant_id\" = :tid"),
            {"tid": str(tenant_id)},
        )

    async def check_transition(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> Optional[str]:
        """
        Проверяет, нужно ли переводить аккаунт в другую фазу прогрева.

        Логика:
        1. Загружает аккаунт и его последний AccountHealthScore.
        2. Определяет ожидаемую фазу по warmup_day.
        3. Если ожидаемая фаза выше текущей И health >= 70 И нет инцидентов
           за последние 48 ч — переходит к новой фазе.
        4. Если health < 40 И текущая фаза не STEALTH — откатывает на одну фазу.
        5. Во всех случаях перехода создаёт запись AccountPhaseHistory.

        :param account_id: ID аккаунта.
        :param tenant_id: ID тенанта.
        :param session: Async SQLAlchemy сессия.
        :return: Название новой фазы или None, если переход не нужен.
        """
        await self._set_rls(session, tenant_id)

        # Загружаем аккаунт
        result = await session.execute(
            select(Account).where(Account.id == account_id)
        )
        account: Optional[Account] = result.scalar_one_or_none()
        if account is None:
            logger.warning("check_transition: аккаунт %d не найден", account_id)
            return None

        current_phase: str = account.warmup_phase or "STEALTH"
        warmup_day: int = account.warmup_day or 0

        # Загружаем последний health score
        hs_result = await session.execute(
            select(AccountHealthScore)
            .where(AccountHealthScore.account_id == account_id)
            .where(AccountHealthScore.tenant_id == tenant_id)
        )
        health_row: Optional[AccountHealthScore] = hs_result.scalar_one_or_none()
        health_score: int = health_row.health_score if health_row else 100

        expected_phase = self.get_phase_for_day(warmup_day)
        current_idx = PHASES.index(current_phase) if current_phase in PHASES else 0
        expected_idx = PHASES.index(expected_phase) if expected_phase in PHASES else 0

        new_phase: Optional[str] = None
        reason: Optional[str] = None

        # Условие повышения фазы
        if expected_idx > current_idx and health_score >= 70:
            # Проверяем отсутствие инцидентов за последние 48 ч
            cutoff = utcnow() - timedelta(hours=48)
            incident_result = await session.execute(
                select(AccountActivityLog.id)
                .where(AccountActivityLog.account_id == account_id)
                .where(AccountActivityLog.tenant_id == tenant_id)
                .where(AccountActivityLog.success == False)  # noqa: E712
                .where(AccountActivityLog.created_at >= cutoff)
                .limit(1)
            )
            has_incident = incident_result.scalar_one_or_none() is not None

            if not has_incident:
                new_phase = expected_phase
                reason = "scheduled_progression"
                logger.info(
                    "account %d: повышение фазы %s -> %s (health=%d, day=%d)",
                    account_id, current_phase, new_phase, health_score, warmup_day,
                )

        # Условие откатки по низкому health
        elif health_score < 40 and current_phase != "STEALTH":
            rollback_idx = max(0, current_idx - 1)
            new_phase = PHASES[rollback_idx]
            reason = "health_rollback"
            logger.warning(
                "account %d: откат фазы %s -> %s из-за низкого health=%d",
                account_id, current_phase, new_phase, health_score,
            )

        if new_phase is None or new_phase == current_phase:
            return None

        # Обновляем фазу аккаунта
        account.warmup_phase = new_phase
        session.add(account)

        # Записываем историю
        history = AccountPhaseHistory(
            tenant_id=tenant_id,
            account_id=account_id,
            phase_from=current_phase,
            phase_to=new_phase,
            reason=reason,
            health_at_transition=health_score,
            triggered_by="phase_controller",
        )
        session.add(history)
        await session.flush()

        return new_phase

    async def handle_incident(
        self,
        account_id: int,
        tenant_id: int,
        incident_type: str,
        flood_wait_seconds: Optional[int],
        session: AsyncSession,
    ) -> dict:
        """
        Обрабатывает инцидент на аккаунте и возвращает предписанное действие.

        Типы инцидентов и реакция:
        - flood_wait < 60s: оставить фазу, пауза = flood_wait * 2
        - flood_wait 60-300s: откат на 1 фазу, пауза 6 часов
        - flood_wait > 300s: откат до STEALTH, карантин 24 ч
        - spam_block: откат до STEALTH, карантин 48 ч
        - frozen: стоп, алерт

        Всегда создаёт запись AccountPhaseHistory.

        :param account_id: ID аккаунта.
        :param tenant_id: ID тенанта.
        :param incident_type: Тип инцидента: 'flood_wait' | 'spam_block' | 'frozen'.
        :param flood_wait_seconds: Длина flood wait в секундах (только для flood_wait).
        :param session: Async SQLAlchemy сессия.
        :return: Словарь с предписанным действием.
        """
        await self._set_rls(session, tenant_id)

        result = await session.execute(
            select(Account).where(Account.id == account_id)
        )
        account: Optional[Account] = result.scalar_one_or_none()
        if account is None:
            logger.warning("handle_incident: аккаунт %d не найден", account_id)
            return {"action": "stop", "alert": True, "reason": "account_not_found"}

        current_phase: str = account.warmup_phase or "STEALTH"
        current_idx = PHASES.index(current_phase) if current_phase in PHASES else 0

        action_result: dict
        new_phase: str = current_phase
        reason: str = incident_type

        if incident_type == "flood_wait":
            fw = flood_wait_seconds or 0
            if fw < 60:
                action_result = {
                    "action": "pause",
                    "pause_seconds": fw * 2,
                }
                # Фаза не меняется
            elif fw <= 300:
                rollback_idx = max(0, current_idx - 1)
                new_phase = PHASES[rollback_idx]
                reason = "flood_wait_medium"
                action_result = {
                    "action": "rollback",
                    "pause_hours": 6,
                    "new_phase": new_phase,
                }
            else:
                new_phase = "STEALTH"
                reason = "flood_wait_severe"
                action_result = {
                    "action": "quarantine",
                    "hours": 24,
                    "new_phase": new_phase,
                }

        elif incident_type == "spam_block":
            new_phase = "STEALTH"
            reason = "spam_block"
            action_result = {
                "action": "quarantine",
                "hours": 48,
                "new_phase": new_phase,
            }

        elif incident_type == "frozen":
            action_result = {
                "action": "stop",
                "alert": True,
            }
            # Фаза не меняется, аккаунт помечается снаружи

        else:
            logger.warning(
                "handle_incident: неизвестный тип инцидента '%s' для account %d",
                incident_type, account_id,
            )
            action_result = {"action": "pause", "pause_seconds": 60}

        # Обновляем фазу аккаунта, если изменилась
        if new_phase != current_phase:
            account.warmup_phase = new_phase
            session.add(account)
            logger.warning(
                "account %d: инцидент '%s' -> откат фазы %s -> %s",
                account_id, incident_type, current_phase, new_phase,
            )

        # Всегда записываем историю
        history = AccountPhaseHistory(
            tenant_id=tenant_id,
            account_id=account_id,
            phase_from=current_phase,
            phase_to=new_phase,
            reason=reason,
            triggered_by="incident_handler",
        )
        session.add(history)
        await session.flush()

        return action_result

    async def increment_day(
        self,
        account_id: int,
        tenant_id: int,
        session: AsyncSession,
    ) -> int:
        """
        Увеличивает счётчик дней прогрева (warmup_day) на 1.

        Вызывается планировщиком один раз в сутки для каждого активного аккаунта.

        :param account_id: ID аккаунта.
        :param tenant_id: ID тенанта.
        :param session: Async SQLAlchemy сессия.
        :return: Новое значение warmup_day.
        """
        await self._set_rls(session, tenant_id)

        result = await session.execute(
            select(Account).where(Account.id == account_id)
        )
        account: Optional[Account] = result.scalar_one_or_none()
        if account is None:
            logger.warning("increment_day: аккаунт %d не найден", account_id)
            return 0

        current_day: int = account.warmup_day or 0
        new_day = current_day + 1
        account.warmup_day = new_day
        session.add(account)
        await session.flush()

        logger.debug(
            "account %d: warmup_day %d -> %d (фаза: %s)",
            account_id, current_day, new_day,
            self.get_phase_for_day(new_day),
        )
        return new_day
