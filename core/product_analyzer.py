"""
Sprint 9 — Product Analyzer Service

Takes a product URL and uses the AI router to extract a ProductBrief.
Saves the brief to the product_briefs table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import ProductBrief
from utils.helpers import utcnow

log = logging.getLogger("uvicorn.error")


@dataclass
class ProductBriefData:
    """Structured result from product analysis."""

    url: str
    product_name: str = ""
    target_audience: str = ""
    brand_tone: str = ""
    usp: str = ""
    keywords: list[str] = field(default_factory=list)
    suggested_styles: list[str] = field(default_factory=list)
    daily_volume: int = 30
    analysis_raw: dict[str, Any] | None = None


class ProductAnalyzerError(Exception):
    pass


async def analyze_product_url(
    session: AsyncSession,
    *,
    url: str,
    tenant_id: int,
    workspace_id: int | None,
    user_id: int | None,
) -> ProductBrief:
    """
    Analyse a product URL via AI and persist a ProductBrief row.

    The AI call uses route_ai_task("product_analysis", ...) following the
    existing pattern from core/assistant_service.py.
    Returns the saved ORM row (flushed but not committed — the caller owns
    the transaction).
    """
    from core.ai_router import route_ai_task

    prompt = f"""
Проанализируй следующий продукт по URL и верни структурированный бриф в формате JSON.

URL продукта: {url}

Верни ТОЛЬКО валидный JSON-объект (без markdown-блоков) со следующими полями:
{{
  "product_name": "<название продукта>",
  "target_audience": "<целевая аудитория — 2-3 предложения>",
  "brand_tone": "<тональность бренда: casual/formal/expert/friendly/bold>",
  "usp": "<уникальное торговое предложение — 1-2 предложения>",
  "keywords": ["<ключевое слово 1>", "<ключевое слово 2>", ...],
  "suggested_styles": ["question", "agree", "expert"],
  "daily_volume": <рекомендуемое количество комментариев в день, целое число от 10 до 200>
}}

Ключевые слова (keywords) — это слова для поиска Telegram-каналов с целевой аудиторией.
suggested_styles — из набора: question, agree, expert, casual, hater, flirt, native.
""".strip()

    result = await route_ai_task(
        session=session,
        task_type="product_analysis",
        surface="product_analyzer",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        prompt=prompt,
        context={},
    )

    parsed: dict[str, Any] = {}
    if result.ok and result.parsed:
        parsed = result.parsed
    elif not result.ok:
        log.warning("product_analysis AI call failed: %s", result.reason_code)

    brief = ProductBrief(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        url=url,
        product_name=str(parsed.get("product_name") or ""),
        target_audience=str(parsed.get("target_audience") or ""),
        brand_tone=str(parsed.get("brand_tone") or ""),
        usp=str(parsed.get("usp") or ""),
        keywords=list(parsed.get("keywords") or []),
        suggested_styles=list(parsed.get("suggested_styles") or []),
        daily_volume=int(parsed.get("daily_volume") or 30),
        analysis_raw=parsed or None,
        created_at=utcnow(),
    )
    session.add(brief)
    await session.flush()
    return brief
