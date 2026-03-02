"""
AI генерация комментариев через Google Gemini API (google-genai SDK).
"""

from __future__ import annotations

import asyncio
import random
import re
from typing import Optional

from google import genai
from google.genai import types

from config import settings
from comments.templates import (
    SYSTEM_PROMPT,
    SCENARIO_A_PROMPT,
    SCENARIO_B_PROMPT,
    PERSONA_STYLES,
    FALLBACK_COMMENTS_A,
    FALLBACK_COMMENTS_B,
)
from comments.scenarios import ScenarioSelector
from utils.logger import log


class CommentGenerator:
    """Генерация уникальных комментариев с помощью Gemini."""

    def __init__(self):
        self.scenario_selector = ScenarioSelector()
        self._client: Optional[genai.Client] = None
        self._initialized = False
        self._recent_comments: list[str] = []  # Дедупликация

    def _init_client(self):
        """Инициализация Gemini клиента (ленивая)."""
        if self._initialized:
            return

        if not settings.GEMINI_API_KEY:
            log.warning("GEMINI_API_KEY не задан — генерация через фоллбэки")
            self._initialized = True
            return

        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._initialized = True
        log.info(f"Gemini клиент инициализирован: {settings.GEMINI_MODEL}")

    async def generate(
        self,
        post_text: str,
        scenario: Optional[str] = None,
        persona_style: str = "casual",
    ) -> dict:
        """
        Сгенерировать комментарий к посту.

        Возвращает:
        {
            "text": str,
            "scenario": "A" | "B",
            "persona": str,
            "source": "ai" | "fallback",
        }
        """
        self._init_client()

        if scenario is None:
            scenario = self.scenario_selector.choose()

        style_description = PERSONA_STYLES.get(persona_style, PERSONA_STYLES["casual"])

        # Генерация через AI
        if self._client:
            text = await self._generate_ai(post_text, scenario, style_description)
            if text:
                return {
                    "text": text,
                    "scenario": scenario,
                    "persona": persona_style,
                    "source": "ai",
                }

        # Фоллбэк
        text = self._get_fallback(scenario)
        return {
            "text": text,
            "scenario": scenario,
            "persona": persona_style,
            "source": "fallback",
        }

    async def _generate_ai(
        self,
        post_text: str,
        scenario: str,
        style_description: str,
    ) -> Optional[str]:
        """Генерация через Gemini API."""
        template = SCENARIO_A_PROMPT if scenario == "A" else SCENARIO_B_PROMPT

        # Обрезаем пост если слишком длинный
        truncated_post = post_text[:1500] if len(post_text) > 1500 else post_text

        prompt = template.format(
            post_text=truncated_post,
            persona_style=style_description,
        )

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self._client.models.generate_content,
                    model=settings.GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.9,
                        top_p=0.95,
                        max_output_tokens=300,
                    ),
                ),
                timeout=30.0,  # 30 секунд макс на Gemini API
            )

            if not response or not response.text:
                log.warning("Gemini вернул пустой ответ")
                return None

            text = self._clean_comment(response.text)

            # Валидация
            if not self._validate(text, scenario):
                log.warning(f"Комментарий не прошёл валидацию: {text[:100]}")
                return None

            # Дедупликация
            if self._is_duplicate(text):
                log.debug("Дубль комментария, повтор генерации")
                return None

            self._recent_comments.append(text.lower())
            if len(self._recent_comments) > 100:
                self._recent_comments = self._recent_comments[-100:]

            return text

        except asyncio.TimeoutError:
            log.warning("Gemini API: таймаут (30с)")
            return None
        except Exception as exc:
            log.error(f"Ошибка Gemini API: {exc}")
            return None

    def _clean_comment(self, text: str) -> str:
        """Очистить текст комментария от артефактов."""
        text = text.strip()

        # Убрать кавычки по краям
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1]
        if text.startswith("\u00ab") and text.endswith("\u00bb"):
            text = text[1:-1]

        # Убрать префиксы типа "Комментарий:" или "Ответ:"
        text = re.sub(r"^(комментарий|ответ|comment)\s*:\s*", "", text, flags=re.IGNORECASE)

        # Убрать множественные пробелы
        text = re.sub(r"\s+", " ", text).strip()

        return text

    def _validate(self, text: str, scenario: str) -> bool:
        """Валидация комментария."""
        if not text:
            return False

        # Длина символов
        if len(text) < 5 or len(text) > 350:
            return False

        # Лимит 30 слов (как в NeuroCom)
        word_count = len(text.split())
        if word_count > 35:  # Даём небольшой запас
            return False

        # Для сценария A не должно быть ссылок на DartVPN
        if scenario == "A":
            lower = text.lower()
            if "@dartvpnbot" in lower or "dartvpn" in lower or "dart vpn" in lower:
                return False

        # Для сценария B должна быть ссылка
        if scenario == "B":
            if "@dartvpnbot" not in text.lower():
                return False

        # Запрещённые паттерны
        forbidden = [
            "как бот", "я бот", "сгенерирован", "artificial",
            "нейросет", "prompt", "промпт", "gpt",
        ]
        lower = text.lower()
        for f in forbidden:
            if f in lower:
                return False

        return True

    def _is_duplicate(self, text: str) -> bool:
        """Проверка на дубль среди недавних комментариев."""
        text_lower = text.lower().strip()
        for recent in self._recent_comments:
            if text_lower == recent:
                return True
            if self._similarity(text_lower, recent) > 0.8:
                return True
        return False

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """Простая оценка похожести по пересечению слов."""
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)

    @staticmethod
    def _get_fallback(scenario: str) -> str:
        """Случайный фоллбэк-комментарий."""
        pool = FALLBACK_COMMENTS_A if scenario == "A" else FALLBACK_COMMENTS_B
        return random.choice(pool)

    def get_stats(self) -> dict:
        """Статистика генератора."""
        return {
            "model": settings.GEMINI_MODEL,
            "ai_available": self._client is not None,
            "recent_comments": len(self._recent_comments),
            "scenarios": self.scenario_selector.get_stats(),
        }
