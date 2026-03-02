"""
Rate limiter — ограничение частоты и количества комментариев.
"""

import random
import time
from datetime import datetime, timedelta

from config import settings
from utils.logger import log


class RateLimiter:
    """Контроль лимитов на аккаунт."""

    def __init__(self):
        # phone -> {comments_today, last_comment_time, cooldown_until, day_start}
        self._state: dict[str, dict] = {}

    def _get_state(self, phone: str) -> dict:
        """Получить или создать состояние для аккаунта."""
        today = datetime.utcnow().date()
        if phone not in self._state:
            self._state[phone] = {
                "comments_today": 0,
                "last_comment_time": 0.0,
                "cooldown_until": 0.0,
                "day_start": today,
                "session_comments": 0,  # для пауз каждые 8-10 коммент.
            }

        state = self._state[phone]
        # Сброс дневного счётчика
        if state["day_start"] != today:
            state["comments_today"] = 0
            state["session_comments"] = 0
            state["day_start"] = today

        return state

    def get_daily_limit(self, days_active: int) -> int:
        """Дневной лимит с учётом прогрева."""
        if days_active <= 1:
            return settings.WARMUP_DAY_1_LIMIT
        elif days_active == 2:
            return settings.WARMUP_DAY_2_LIMIT
        elif days_active == 3:
            return settings.WARMUP_DAY_3_LIMIT
        return settings.MAX_COMMENTS_PER_ACCOUNT_PER_DAY

    def can_comment(self, phone: str, days_active: int = 99) -> bool:
        """Можно ли аккаунту сейчас писать комментарий."""
        state = self._get_state(phone)
        now = time.time()

        # Cooldown после ошибки
        if now < state["cooldown_until"]:
            remaining = int(state["cooldown_until"] - now)
            log.debug(f"{phone}: в cooldown ещё {remaining}с")
            return False

        # Дневной лимит
        limit = self.get_daily_limit(days_active)
        if state["comments_today"] >= limit:
            log.debug(f"{phone}: дневной лимит ({limit}) исчерпан")
            return False

        # Минимальная задержка между комментариями
        elapsed = now - state["last_comment_time"]
        if elapsed < settings.MIN_DELAY_BETWEEN_COMMENTS_SEC:
            log.debug(f"{phone}: слишком рано, прошло {int(elapsed)}с")
            return False

        return True

    def get_next_delay(self) -> float:
        """Случайная задержка (нормальное распределение) между комментариями."""
        mean = (settings.MIN_DELAY_BETWEEN_COMMENTS_SEC + settings.MAX_DELAY_BETWEEN_COMMENTS_SEC) / 2
        std = (settings.MAX_DELAY_BETWEEN_COMMENTS_SEC - settings.MIN_DELAY_BETWEEN_COMMENTS_SEC) / 4
        delay = random.gauss(mean, std)
        return max(settings.MIN_DELAY_BETWEEN_COMMENTS_SEC, min(delay, settings.MAX_DELAY_BETWEEN_COMMENTS_SEC * 1.5))

    def record_comment(self, phone: str):
        """Зафиксировать отправку комментария."""
        state = self._get_state(phone)
        state["comments_today"] += 1
        state["session_comments"] += 1
        state["last_comment_time"] = time.time()
        log.debug(f"{phone}: комментарий #{state['comments_today']} за сегодня")

    def set_cooldown(self, phone: str, seconds: int):
        """Установить cooldown для аккаунта."""
        state = self._get_state(phone)
        state["cooldown_until"] = time.time() + seconds
        log.info(f"{phone}: cooldown на {seconds}с")

    def set_flood_wait(self, phone: str, seconds: int):
        """FloodWaitError — cooldown с запасом ×1.5."""
        actual = int(seconds * 1.5)
        self.set_cooldown(phone, actual)
        log.warning(f"{phone}: FloodWait {seconds}с → cooldown {actual}с")

    def needs_rest(self, phone: str) -> bool:
        """Нужна ли длинная пауза (после 8-10 комментариев подряд)."""
        state = self._get_state(phone)
        threshold = random.randint(8, 10)
        return state["session_comments"] >= threshold

    def reset_session(self, phone: str):
        """Сбросить счётчик сессии после отдыха."""
        state = self._get_state(phone)
        state["session_comments"] = 0

    def get_stats(self, phone: str) -> dict:
        """Статистика по аккаунту."""
        state = self._get_state(phone)
        now = time.time()
        return {
            "comments_today": state["comments_today"],
            "in_cooldown": now < state["cooldown_until"],
            "cooldown_remaining": max(0, int(state["cooldown_until"] - now)),
            "session_comments": state["session_comments"],
        }
