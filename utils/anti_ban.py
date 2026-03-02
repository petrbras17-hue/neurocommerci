"""
Антибан утилиты — human-like поведение, задержки, прогрев.
"""

from __future__ import annotations

import random


class AntibanManager:
    """Менеджер антидетекта для имитации человеческого поведения."""

    def get_typing_delay(self, text_length: int) -> float:
        """
        Задержка перед отправкой — имитация набора текста.
        Среднее 3-4 символа в секунду + случайный разброс.
        """
        base_time = text_length / random.uniform(3.0, 5.0)
        noise = random.gauss(0, base_time * 0.2)  # 20% разброс
        delay = max(2.0, base_time + noise)
        return min(delay, 30.0)  # Макс 30 секунд

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

    @staticmethod
    def get_warmup_schedule() -> dict[int, int]:
        """
        Расписание прогрева новых аккаунтов.
        День -> максимум комментариев (из config).
        """
        from config import settings
        return {
            1: settings.WARMUP_DAY_1_LIMIT,
            2: settings.WARMUP_DAY_2_LIMIT,
            3: settings.WARMUP_DAY_3_LIMIT,
            4: settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY,
        }

    def jitter(self, base_seconds: float, spread: float = 0.3) -> float:
        """Добавить случайный разброс к задержке."""
        noise = random.gauss(0, base_seconds * spread)
        return max(1.0, base_seconds + noise)
