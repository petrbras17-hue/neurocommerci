"""
AI Orchestrator backed by Gemini only.

The public interface remains stable for the rest of the project:
- analyze_post(...)
- review_comment(...)
- get_stats()
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

from google import genai
from google.genai import types

from comments.scenarios import Scenario
from config import settings
from core.gemini_models import get_text_model_candidates
from utils.logger import log


class AIOrchestrator:
    """Gemini-backed decision/review layer with graceful degradation."""

    def __init__(self):
        self._client: Optional[genai.Client] = None
        self._initialized = False
        self._stats = {
            "analyses": 0,
            "reviews": 0,
            "approved": 0,
            "rejected": 0,
            "improved": 0,
            "errors": 0,
        }
        self._last_model_used: str = ""

    def _init_client(self):
        if self._initialized:
            return
        if not settings.GEMINI_API_KEY:
            log.warning("GEMINI_API_KEY не задан — AI decision layer отключён")
            self._initialized = True
            return
        try:
            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        except Exception as exc:
            log.error(f"Ошибка инициализации Gemini decision layer: {exc}")
        finally:
            self._initialized = True

    @property
    def is_available(self) -> bool:
        self._init_client()
        return self._client is not None

    @staticmethod
    def _model_candidates() -> list[str]:
        return get_text_model_candidates(
            settings.GEMINI_MODEL,
            settings.GEMINI_FLASH_MODEL,
        )

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        if not text:
            return None
        cleaned = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            log.debug(f"AI layer returned invalid JSON: {text[:200]}")
            return None

    async def _call_json(self, prompt: str, *, system_instruction: str, max_tokens: int) -> Optional[dict]:
        self._init_client()
        if not self._client:
            return None

        for model_name in self._model_candidates():
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._client.models.generate_content,
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.3,
                            top_p=0.9,
                            max_output_tokens=max_tokens,
                        ),
                    ),
                    timeout=30.0,
                )
                text = getattr(response, "text", "") or ""
                parsed = self._parse_json(text)
                if parsed is None:
                    continue
                self._last_model_used = model_name
                return parsed
            except asyncio.TimeoutError:
                self._stats["errors"] += 1
                log.warning(f"Gemini decision layer timeout (model={model_name})")
            except Exception as exc:
                self._stats["errors"] += 1
                log.warning(f"Gemini decision layer error (model={model_name}): {exc}")
        return None

    async def analyze_post(
        self,
        post_text: str,
        channel_title: str = "",
        channel_topic: str = "",
    ) -> Optional[dict]:
        truncated = post_text[:1500] if len(post_text) > 1500 else post_text
        prompt = f"""Пост из Telegram-канала "{channel_title}" (тема: {channel_topic}).

Текст:
---
{truncated}
---

Ответь ТОЛЬКО JSON:
{{"should_comment": true, "score": 0.0, "scenario": "A", "persona_style": "casual", "angle": "", "reason": ""}}

Правила:
- scenario "B" допустим только если пост реально релевантен продукту {settings.PRODUCT_NAME}
- scenario "A" для нейтрального нативного ответа без упоминания продукта
- score от 0.0 до 1.0
- persona_style: casual|formal|slang|tech|skeptic
- should_comment=false для спама, оффтопа, казино, 18+, нерелевантных объявлений"""
        system_instruction = (
            "Ты — строгий классификатор Telegram-постов. "
            "Возвращай только компактный валидный JSON без markdown."
        )
        result = await self._call_json(prompt, system_instruction=system_instruction, max_tokens=220)
        if result:
            self._stats["analyses"] += 1
        return result

    async def review_comment(
        self,
        comment: str,
        post_text: str,
        scenario: Scenario,
    ) -> Optional[dict]:
        truncated_post = post_text[:500] if len(post_text) > 500 else post_text
        scenario_desc = (
            "Обычный нативный комментарий без упоминания продукта"
            if scenario == Scenario.A
            else f"Комментарий с упоминанием {settings.product_bot_mention}"
        )
        prompt = f"""Проверь Telegram-комментарий.

Пост:
---
{truncated_post}
---

Комментарий:
{comment}

Сценарий:
{scenario_desc}

Ответь ТОЛЬКО JSON:
{{"approved": true, "natural_score": 0.0, "issues": [], "improved": null}}

Требования:
- естественно и по теме
- до 35 слов
- без явных признаков бота
- improved заполняй только если комментарий нужно переписать"""
        system_instruction = (
            "Ты — редактор Telegram-комментариев. "
            "Возвращай только валидный JSON, без markdown и пояснений."
        )
        result = await self._call_json(prompt, system_instruction=system_instruction, max_tokens=220)
        if result:
            self._stats["reviews"] += 1
            if result.get("approved"):
                self._stats["approved"] += 1
            else:
                self._stats["rejected"] += 1
                if result.get("improved"):
                    self._stats["improved"] += 1
        return result

    def get_stats(self) -> dict:
        return {
            "available": self.is_available,
            "model": self._last_model_used or (self._model_candidates()[0] if self._model_candidates() else ""),
            **self._stats,
        }
