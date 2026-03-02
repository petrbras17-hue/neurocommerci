"""
Анализатор релевантности постов.
Определяет, стоит ли комментировать пост (тематика, ключевые слова, язык).
"""

from __future__ import annotations

import re
from typing import Optional

from utils.logger import log


# Ключевые слова по тематикам (для быстрого скоринга без AI)
RELEVANCE_KEYWORDS: dict[str, list[str]] = {
    "vpn": [
        "vpn", "впн", "блокировк", "обход", "роскомнадзор", "замедлен",
        "недоступ", "заблокирован", "proxy", "прокси", "tor", "тор",
        "цензур", "свобод", "доступ", "ограничен", "запрет",
    ],
    "ai": [
        "chatgpt", "gpt", "нейросет", "искусственн", "ai", "midjourney",
        "claude", "gemini", "генерац", "бот", "автоматиз",
        "машинн", "deepseek", "llm", "промпт",
    ],
    "services": [
        "instagram", "инстаграм", "youtube", "ютуб", "spotify", "спотифай",
        "netflix", "нетфликс", "tiktok", "тикток", "facebook", "фейсбук",
        "twitter", "твиттер", "discord", "дискорд", "linkedin",
    ],
    "crypto": [
        "крипт", "биткоин", "bitcoin", "ethereum", "эфир", "binance",
        "блокчейн", "blockchain", "defi", "nft", "web3",
    ],
    "tech": [
        "технолог", "программ", "разработ", "it ", "devops",
        "сервер", "хостинг", "облак", "cloud",
    ],
}

# Слова-стоп: если встречаются, пост лучше не комментировать
STOP_WORDS = [
    "реклам", "партнёр", "спонсор", "paid", "промокод",
    "розыгрыш", "конкурс", "giveaway",
    "18+", "порно", "казино", "ставк", "букмекер",
]

_RU_TEXT_RE = re.compile(r"[А-Яа-яЁё]")


class PostAnalyzer:
    """Анализ релевантности постов для комментирования."""

    def __init__(self, min_score: float = 0.3):
        self.min_score = min_score

    def analyze(self, text: str, channel_topic: Optional[str] = None) -> dict:
        """
        Оценить пост. Возвращает:
        {
            "score": 0.0..1.0,
            "should_comment": bool,
            "matched_topics": ["vpn", "ai"],
            "reason": str,
        }
        """
        if not text or not text.strip():
            return self._result(0.0, False, [], "Пустой текст")

        text_lower = text.lower()

        # Проверка стоп-слов
        for stop in STOP_WORDS:
            if stop in text_lower:
                return self._result(0.0, False, [], f"Стоп-слово: {stop}")

        # Проверка на русский язык
        is_russian = bool(_RU_TEXT_RE.search(text))

        # Скоринг по ключевым словам
        matched_topics = []
        total_matches = 0

        for topic, keywords in RELEVANCE_KEYWORDS.items():
            topic_matches = sum(1 for kw in keywords if kw in text_lower)
            if topic_matches > 0:
                matched_topics.append(topic)
                total_matches += topic_matches

        # Базовый скор
        if total_matches == 0:
            score = 0.1 if is_russian else 0.0
        elif total_matches <= 2:
            score = 0.4
        elif total_matches <= 5:
            score = 0.6
        else:
            score = 0.8

        # Бонус за русский язык
        if is_russian:
            score = min(1.0, score + 0.1)

        # Бонус если тема канала совпадает с найденными ключевыми словами
        if channel_topic and channel_topic.lower() in matched_topics:
            score = min(1.0, score + 0.15)

        # VPN-тема — особо релевантна для DartVPN
        if "vpn" in matched_topics:
            score = min(1.0, score + 0.15)

        # Слишком короткий текст — менее полезен
        if len(text.strip()) < 50:
            score *= 0.7

        should_comment = score >= self.min_score
        reason = f"Совпадения: {total_matches}, темы: {matched_topics}" if matched_topics else "Нет совпадений"

        return self._result(score, should_comment, matched_topics, reason)

    def filter_queue(self, posts: list[dict]) -> list[dict]:
        """Отфильтровать и отсортировать очередь постов по релевантности."""
        scored = []
        for post in posts:
            analysis = self.analyze(
                post.get("text", ""),
                post.get("channel_topic"),
            )
            if analysis["should_comment"]:
                post["relevance_score"] = analysis["score"]
                post["matched_topics"] = analysis["matched_topics"]
                scored.append(post)

        # Сортировка: высокий скор → новее
        scored.sort(key=lambda p: (p["relevance_score"], p.get("posted_at", 0)), reverse=True)
        return scored

    @staticmethod
    def _result(score: float, should_comment: bool, topics: list, reason: str) -> dict:
        return {
            "score": round(score, 2),
            "should_comment": should_comment,
            "matched_topics": topics,
            "reason": reason,
        }
