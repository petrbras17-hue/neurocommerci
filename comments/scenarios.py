"""
Выбор сценария комментирования: A (без ссылки) или B (со ссылкой).
"""

from __future__ import annotations

import random

from config import settings
from utils.logger import log


class ScenarioSelector:
    """Взвешенный выбор сценария A/B."""

    def __init__(self):
        self._history: list[str] = []  # Последние N сценариев для контроля баланса

    def choose(self) -> str:
        """
        Выбрать сценарий:
        - "A" — комментарий без ссылки (аватарка как лид-магнит)
        - "B" — комментарий со ссылкой на @DartVPNBot

        Соотношение задаётся в settings.SCENARIO_B_RATIO (0.3 = 30% B).
        """
        b_ratio = settings.SCENARIO_B_RATIO

        # Анти-паттерн: не давать подряд больше 2 сценариев B
        recent_b = sum(1 for s in self._history[-5:] if s == "B")
        if recent_b >= 2:
            scenario = "A"
        else:
            scenario = "B" if random.random() < b_ratio else "A"

        self._history.append(scenario)
        # Храним только последние 50 записей
        if len(self._history) > 50:
            self._history = self._history[-50:]

        return scenario

    def get_stats(self) -> dict:
        """Статистика по сценариям."""
        total = len(self._history)
        if total == 0:
            return {"total": 0, "a_count": 0, "b_count": 0, "b_ratio_actual": 0.0}

        b_count = sum(1 for s in self._history if s == "B")
        a_count = total - b_count
        return {
            "total": total,
            "a_count": a_count,
            "b_count": b_count,
            "b_ratio_actual": round(b_count / total, 2),
            "b_ratio_target": settings.SCENARIO_B_RATIO,
        }
