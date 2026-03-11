"""
AI генерация комментариев через Google Gemini API (google-genai SDK).
"""

from __future__ import annotations

import asyncio
import random
import re
from collections import deque
from typing import Optional

from google import genai
from google.genai import types

from config import settings
from core.gemini_models import get_text_model_candidates
from comments.templates import (
    SCENARIO_A_PROMPT,
    PERSONA_STYLES,
    FALLBACK_COMMENTS_A,
    get_system_prompt,
    get_scenario_b_prompt,
    get_fallback_comments_b,
)
from comments.scenarios import Scenario, ScenarioSelector
from utils.logger import log


class CommentGenerator:
    """Генерация уникальных комментариев с помощью Gemini."""

    def __init__(self):
        self.scenario_selector = ScenarioSelector()
        self._client: Optional[genai.Client] = None
        self._initialized = False
        self._recent_comments: deque[str] = deque(maxlen=100)  # Дедупликация
        self._last_model_used: str = ""

    @staticmethod
    def _model_candidates() -> list[str]:
        """Primary + Flash fallback (deduplicated, preserving order)."""
        return get_text_model_candidates(
            settings.GEMINI_MODEL,
            settings.GEMINI_FLASH_MODEL,
        )

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
        log.info(
            "Gemini клиент инициализирован: "
            f"primary={settings.GEMINI_MODEL}, fallback={settings.GEMINI_FLASH_MODEL}"
        )

    async def generate(
        self,
        post_text: str,
        scenario: Optional[Scenario] = None,
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
        text = self.get_fallback(scenario)
        return {
            "text": text,
            "scenario": scenario,
            "persona": persona_style,
            "source": "fallback",
        }

    async def _generate_ai(
        self,
        post_text: str,
        scenario: Scenario,
        style_description: str,
    ) -> Optional[str]:
        """Генерация через Gemini API."""
        # Обрезаем пост если слишком длинный
        truncated_post = post_text[:1500] if len(post_text) > 1500 else post_text

        if scenario == Scenario.A:
            prompt = SCENARIO_A_PROMPT.format(
                post_text=truncated_post,
                persona_style=style_description,
            )
        else:
            # Сценарий B: промпт строится динамически с актуальным bot mention
            prompt = get_scenario_b_prompt(truncated_post, style_description)

        models = self._model_candidates()
        if not models:
            return None

        for model_name in models:
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._client.models.generate_content,
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=get_system_prompt(),
                            temperature=0.9,
                            top_p=0.95,
                            max_output_tokens=300,
                        ),
                    ),
                    timeout=30.0,  # 30 секунд макс на попытку
                )

                if not response or not response.text:
                    log.warning(f"Gemini вернул пустой ответ (model={model_name})")
                    continue

                text = self._clean_comment(response.text)

                # Валидация
                if not self._validate(text, scenario):
                    log.warning(f"Комментарий не прошёл валидацию (model={model_name}): {text[:100]}")
                    continue

                # Дедупликация
                if self._is_duplicate(text):
                    log.debug(f"Дубль комментария (model={model_name}), повтор генерации")
                    continue

                self._recent_comments.append(text.lower())
                self._last_model_used = model_name
                return text

            except asyncio.TimeoutError:
                log.warning(f"Gemini API: таймаут (model={model_name})")
            except Exception as exc:
                log.warning(f"Ошибка Gemini API (model={model_name}): {exc}")

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

        # Для сценария A не должно быть ссылок на продукт
        bot_user_lower = settings.PRODUCT_BOT_USERNAME.lower()
        product_lower = settings.PRODUCT_NAME.lower()
        lower = text.lower()

        if scenario == Scenario.A:
            if f"@{bot_user_lower}" in lower or product_lower in lower:
                return False

        # Для сценария B должна быть ссылка
        if scenario == Scenario.B:
            if f"@{bot_user_lower}" not in lower:
                return False

        # Запрещённые паттерны
        forbidden = [
            "как бот", "я бот", "сгенерирован", "artificial",
            "нейросет", "prompt", "промпт", "gpt",
        ]
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
    def get_fallback(scenario: str) -> str:
        """Случайный фоллбэк-комментарий."""
        pool = FALLBACK_COMMENTS_A if scenario == Scenario.A else get_fallback_comments_b()
        return random.choice(pool)

    def get_stats(self) -> dict:
        """Статистика генератора."""
        return {
            "model": settings.GEMINI_MODEL,
            "fallback_model": settings.GEMINI_FLASH_MODEL,
            "last_model_used": self._last_model_used or "n/a",
            "ai_available": self._client is not None,
            "recent_comments": len(self._recent_comments),
            "scenarios": self.scenario_selector.get_stats(),
        }
