"""
AI Оркестратор — Claude как дирижёр системы нейрокомментирования.

Claude Sonnet анализирует посты, принимает стратегические решения,
проверяет качество комментариев перед отправкой.
Gemini остаётся исполнителем — генерирует текст.

Если ANTHROPIC_API_KEY не задан → система работает как раньше (fallback на keywords).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

from config import settings
from comments.scenarios import Scenario
from utils.logger import log


class AIOrchestrator:
    """
    Claude-дирижёр: анализ постов, выбор стратегии, контроль качества.
    Graceful degradation — если API недоступен, возвращает None.
    """

    def __init__(self):
        self._client = None
        self._initialized = False
        self._stats = {
            "analyses": 0,
            "reviews": 0,
            "approved": 0,
            "rejected": 0,
            "improved": 0,
            "errors": 0,
        }

    def _init_client(self):
        """Ленивая инициализация Anthropic клиента."""
        if self._initialized:
            return

        if not settings.ANTHROPIC_API_KEY:
            log.warning("ANTHROPIC_API_KEY не задан — Claude-дирижёр отключён")
            self._initialized = True
            return

        try:
            import anthropic
            self._client = anthropic.AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY,
            )
            self._initialized = True
            log.info(f"Claude-дирижёр инициализирован: {settings.CLAUDE_MODEL}")
        except ImportError:
            log.error("Пакет anthropic не установлен: pip install anthropic")
            self._initialized = True
        except Exception as exc:
            log.error(f"Ошибка инициализации Claude: {exc}")
            self._initialized = True

    @property
    def is_available(self) -> bool:
        """Доступен ли Claude для работы."""
        self._init_client()
        return self._client is not None

    async def _call_claude(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """Вызвать Claude API. Возвращает текст ответа или None."""
        self._init_client()
        if not self._client:
            return None

        try:
            response = await asyncio.wait_for(
                self._client.messages.create(
                    model=settings.CLAUDE_MODEL,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=30.0,
            )
            return response.content[0].text
        except asyncio.TimeoutError:
            log.warning("Claude API: таймаут (30с)")
            self._stats["errors"] += 1
            return None
        except Exception as exc:
            log.error(f"Ошибка Claude API: {exc}")
            self._stats["errors"] += 1
            return None

    def _parse_json(self, text: str) -> Optional[dict]:
        """Извлечь JSON из ответа Claude (может быть обёрнут в ```json)."""
        if not text:
            return None

        # Убрать markdown code block
        cleaned = re.sub(r"```json\s*", "", text)
        cleaned = re.sub(r"```\s*$", "", cleaned)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            log.debug(f"Claude вернул невалидный JSON: {text[:200]}")
            return None

    # ─────────────────────────────────────────────
    # Анализ постов
    # ─────────────────────────────────────────────

    async def analyze_post(
        self,
        post_text: str,
        channel_title: str = "",
        channel_topic: str = "",
    ) -> Optional[dict]:
        """
        Claude анализирует пост: стоит ли комментировать, какой сценарий, стиль.

        Возвращает:
        {
            "should_comment": bool,
            "score": float,
            "scenario": "A" | "B",
            "persona_style": str,
            "angle": str,
            "reason": str,
        }
        Или None если Claude недоступен.
        """
        truncated = post_text[:1500] if len(post_text) > 1500 else post_text

        prompt = f"""Ты — стратег системы комментирования для продвижения {settings.PRODUCT_NAME} в Telegram.

Пост из канала "{channel_title}" (тема: {channel_topic}):
---
{truncated}
---

Ответь ТОЛЬКО JSON (без пояснений):
{{"should_comment": true, "score": 0.7, "scenario": "A", "persona_style": "casual", "angle": "описание подхода", "reason": "почему"}}

Правила:
- scenario "B" (с {settings.product_bot_mention}) ТОЛЬКО если пост по теме {settings.PRODUCT_CATEGORY}
- scenario "A" (без ссылки) для остальных тем (AI, tech, крипто и т.д.)
- score 0.0-1.0, комментировать если > 0.4
- persona_style: "casual"/"formal"/"slang"/"tech"/"skeptic"
- НЕ комментировать: рекламу, розыгрыши, казино, 18+, спонсорские посты
- angle: 1 предложение — КАК комментировать (какой подход)"""

        raw = await self._call_claude(prompt, max_tokens=300)
        result = self._parse_json(raw)

        if result:
            self._stats["analyses"] += 1
            log.debug(
                f"Claude анализ: should={result.get('should_comment')}, "
                f"scenario={result.get('scenario')}, score={result.get('score')}"
            )

        return result

    # ─────────────────────────────────────────────
    # Проверка качества комментариев
    # ─────────────────────────────────────────────

    async def review_comment(
        self,
        comment: str,
        post_text: str,
        scenario: Scenario,
    ) -> Optional[dict]:
        """
        Claude проверяет комментарий перед отправкой.

        Возвращает:
        {
            "approved": bool,
            "natural_score": float,
            "issues": list[str],
            "improved": str | None,
        }
        Или None если Claude недоступен.
        """
        truncated_post = post_text[:500] if len(post_text) > 500 else post_text
        scenario_desc = "Без ссылки (обычный комментарий)" if scenario == Scenario.A else f"С упоминанием {settings.product_bot_mention}"

        prompt = f"""Оцени комментарий для Telegram-канала.

Пост:
---
{truncated_post}
---

Комментарий: "{comment}"
Сценарий: {scenario_desc}

Ответь ТОЛЬКО JSON:
{{"approved": true, "natural_score": 0.8, "issues": [], "improved": null}}

Проверяй:
- Естественность (не похоже на бота? не шаблонно?)
- Релевантность посту (по теме?)
- Длина до 30 слов
- Для сценария B: есть ли {settings.product_bot_mention}? не выглядит ли как реклама?
- improved: если approved=false, предложи исправленную версию (или null)"""

        raw = await self._call_claude(prompt, max_tokens=300)
        result = self._parse_json(raw)

        if result:
            self._stats["reviews"] += 1
            if result.get("approved"):
                self._stats["approved"] += 1
            else:
                self._stats["rejected"] += 1
                if result.get("improved"):
                    self._stats["improved"] += 1
            log.debug(
                f"Claude review: approved={result.get('approved')}, "
                f"score={result.get('natural_score')}"
            )

        return result

    # ─────────────────────────────────────────────
    # Статистика
    # ─────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Статистика дирижёра."""
        return {
            "available": self.is_available,
            "model": settings.CLAUDE_MODEL,
            **self._stats,
        }
