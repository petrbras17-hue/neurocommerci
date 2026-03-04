"""
Выбор сценария комментирования: A (без ссылки) или B (со ссылкой).
"""

from __future__ import annotations

import random
from enum import Enum

from config import settings
from utils.logger import log


class Scenario(str, Enum):
    """Сценарий комментирования."""
    A = "A"  # Комментарий без ссылки (аватарка как лид-магнит)
    B = "B"  # Комментарий со ссылкой на продукт


class ScenarioSelector:
    """Взвешенный выбор сценария A/B."""

    def __init__(self):
        self._history: list[Scenario] = []  # Последние N сценариев для контроля баланса

    def choose(self) -> Scenario:
        """
        Выбрать сценарий:
        - Scenario.A — комментарий без ссылки (аватарка как лид-магнит)
        - Scenario.B — комментарий со ссылкой на продукт

        Соотношение задаётся в settings.SCENARIO_B_RATIO (0.3 = 30% B).
        """
        b_ratio = settings.SCENARIO_B_RATIO

        # Анти-паттерн: не давать подряд больше 2 сценариев B
        recent_b = sum(1 for s in self._history[-5:] if s == Scenario.B)
        if recent_b >= 2:
            scenario = Scenario.A
        else:
            scenario = Scenario.B if random.random() < b_ratio else Scenario.A

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

        b_count = sum(1 for s in self._history if s == Scenario.B)
        a_count = total - b_count
        return {
            "total": total,
            "a_count": a_count,
            "b_count": b_count,
            "b_ratio_actual": round(b_count / total, 2),
            "b_ratio_target": settings.SCENARIO_B_RATIO,
        }
