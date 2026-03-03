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
        Фаза 14-дневного прогрева:
        - readonly (0-2): только connect, get_me, чтение
        - reactions (3-4): реакции, send_read_acknowledge
        - light (5-7): 1-3 комментария
        - moderate (8-14): 5-8 комментариев
        - full (14+): полный режим
        """
        if days_active < 3:
            return "readonly"
        elif days_active < 5:
            return "reactions"
        elif days_active < 8:
            return "light"
        elif days_active < 15:
            return "moderate"
        return "full"

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
