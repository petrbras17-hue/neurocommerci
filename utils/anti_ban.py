"""
Антибан утилиты — human-like поведение, задержки, прогрев.
SetTypingRequest, контроль времени активности, 14-дневный прогрев.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone, timedelta

from utils.logger import log

# Московское время (UTC+3)
MSK = timezone(timedelta(hours=3))


class AntibanManager:
    """Менеджер антидетекта для имитации человеческого поведения."""

    async def send_typing(self, client, peer, text_len: int) -> None:
        """
        Отправить SetTypingRequest и подождать — имитация набора.
        ОБЯЗАТЕЛЬНО перед каждым send_message.
        """
        from telethon.tl.functions.messages import SetTypingRequest
        from telethon.tl.types import SendMessageTypingAction

        try:
            await client(SetTypingRequest(peer=peer, action=SendMessageTypingAction()))
            delay = max(2.0, text_len * 0.08)  # 80мс на символ, мин 2с
            delay = min(delay, 25.0)  # Макс 25с
            await asyncio.sleep(delay)
        except Exception as exc:
            log.debug(f"SetTypingRequest ошибка (не критично): {exc}")

    @staticmethod
    def is_active_hours() -> bool:
        """
        Проверка: активное время 8:00-23:00 MSK.
        Ночью (0:00-7:59) аккаунты не должны действовать.
        """
        now_msk = datetime.now(MSK)
        return 8 <= now_msk.hour < 23

    @staticmethod
    def is_peak_hours() -> bool:
        """Пиковые часы: 12-14 и 19-22 MSK — больше активности."""
        now_msk = datetime.now(MSK)
        return now_msk.hour in range(12, 14) or now_msk.hour in range(19, 22)

    @staticmethod
    def get_warmup_phase(days_active: int) -> str:
        """
        Фаза прогрева:
        - readonly (0-2): только connect, get_me, чтение
        - reactions (3-4): реакции, send_read_acknowledge
        - light (5-7): 1-3 комментария
        - moderate (8-14): 5-8 комментариев
        - full (15-30): 20 комментариев
        - veteran (30+): полный режим (35)
        """
        if days_active < 3:
            return "readonly"
        elif days_active < 5:
            return "reactions"
        elif days_active < 8:
            return "light"
        elif days_active < 15:
            return "moderate"
        elif days_active < 30:
            return "full"
        return "veteran"

    @staticmethod
    def get_account_age_factor(account_age_days: int) -> float:
        """
        Множитель лимитов по возрасту аккаунта.
        Аккаунты младше 90 дней получают пропорционально меньшие лимиты.
        """
        if account_age_days <= 0:
            return 0.5  # Неизвестный возраст — консервативно
        return min(1.0, account_age_days / 90)

    def get_rest_duration(self) -> int:
        """
        Длительность отдыха после серии комментариев (8-10 подряд).
        15-45 минут.
        """
        return random.randint(900, 2700)

    def get_action_delay(self) -> float:
        """
        Случайная задержка между действиями (чтение каналов, листание).
        5-20 секунд.
        """
        return random.uniform(5.0, 20.0)

    def should_do_passive_action(self) -> bool:
        """
        Нужно ли выполнить пассивное действие (просмотр, чтение) для естественности.
        Примерно каждый 3-5 комментарий.
        """
        return random.random() < 0.25

    def jitter(self, base_seconds: float, spread: float = 0.3) -> float:
        """Добавить случайный разброс к задержке."""
        noise = random.gauss(0, base_seconds * spread)
        return max(1.0, base_seconds + noise)

    @staticmethod
    def get_account_active_window(phone: str) -> tuple[int, int]:
        """Per-account active hours window with random offset."""
        from config import settings as cfg
        # Use phone hash for deterministic but varied offsets
        offset = hash(phone) % 5 - 2  # -2 to +2 hours
        start = cfg.ACCOUNT_SLEEP_END_HOUR + offset
        end = cfg.ACCOUNT_SLEEP_START_HOUR + offset
        return (start % 24, end % 24)

    @staticmethod
    def is_lazy_day(phone: str) -> bool:
        """20% chance of a lazy day (50% fewer comments)."""
        from datetime import date
        day_seed = hash(f"{phone}:{date.today().isoformat()}")
        return (day_seed % 5) == 0  # 20% chance

    @staticmethod
    def get_strict_daily_limit(days_active: int, account_age_days: int = 0) -> int:
        """Stricter limits for API ID 4 accounts.

        Uses warmup phase logic from get_warmup_phase and account age factor
        to compute the normal limit, then applies a 0.6x multiplier in strict mode.
        """
        from config import settings as cfg

        # Compute normal daily limit based on warmup phase
        phase = AntibanManager.get_warmup_phase(days_active)
        phase_limits = {
            "readonly": 0,
            "reactions": 0,
            "light": cfg.WARMUP_LIGHT_LIMIT,
            "moderate": cfg.WARMUP_MODERATE_LIMIT,
            "full": 20,
            "veteran": cfg.MAX_COMMENTS_PER_ACCOUNT_PER_DAY,
        }
        base_limit = phase_limits.get(phase, 0)
        age_factor = AntibanManager.get_account_age_factor(account_age_days)
        normal = max(0, int(base_limit * age_factor))

        if not cfg.API_ID_4_STRICT_MODE:
            return normal

        # API ID 4 strict mode: 60% of normal limits
        return max(1, int(normal * 0.6)) if normal > 0 else 0
