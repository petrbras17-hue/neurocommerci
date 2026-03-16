"""
Content Factory — трансформация одного источника в посты для 6 платформ.

Паттерн Велса: один источник → Telegram, Twitter/X, LinkedIn, YouTube, Reels/Shorts, Email.
Этот паттерн позволил набрать 670K подписчиков YouTube за 6 месяцев.

Каждая платформа имеет строго определённые требования:
  - длина текста
  - формат и структура
  - тональность
  - SEO-требования (для YouTube)
  - хук + CTA (для Reels/Shorts)

Ключевые принципы:
  - все AI-вызовы через route_ai_task(task_type="content_factory")
  - параллельная генерация всех 6 форматов через asyncio.gather
  - каждый промпт тщательно настроен под платформу
  - никаких прямых вызовов провайдеров
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.ai_router import route_ai_task
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log


# ---------------------------------------------------------------------------
# Голоса бренда
# ---------------------------------------------------------------------------

BRAND_VOICES: dict[str, str] = {
    "professional": (
        "Профессиональный и авторитетный тон. Экспертные формулировки, "
        "структурированная подача, без жаргона. Уважение к аудитории."
    ),
    "casual": (
        "Дружелюбный разговорный стиль. Простые слова, тёплый тон, "
        "как будто говоришь с другом. Допустимы эмодзи."
    ),
    "bold": (
        "Смелый и провокационный стиль. Сильные утверждения, "
        "прямые вызовы, нестандартные углы. Цепляет с первой строки."
    ),
    "educational": (
        "Обучающий и поясняющий тон. Объяснения «почему», структура «шаг за шагом», "
        "акцент на практической пользе для читателя."
    ),
    "storytelling": (
        "Нарративный стиль. История с началом, развитием и выводом. "
        "Личный опыт, эмоциональные детали, вовлекающий сторителлинг."
    ),
}

BRAND_VOICE_OPTIONS = list(BRAND_VOICES.keys())


# ---------------------------------------------------------------------------
# Датакласс результата
# ---------------------------------------------------------------------------

@dataclass
class ContentPack:
    """Полный пакет контента для 6 платформ из одного источника."""

    source_text: str
    topic: str
    telegram: str
    twitter_thread: list[str]
    linkedin: str
    youtube_description: str
    reels_ideas: list[dict]   # [{hook, script, cta, duration_sec}]
    email: dict               # {subject, body, cta}
    generated_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict:
        return {
            "source_text": self.source_text,
            "topic": self.topic,
            "telegram": self.telegram,
            "twitter_thread": self.twitter_thread,
            "linkedin": self.linkedin,
            "youtube_description": self.youtube_description,
            "reels_ideas": self.reels_ideas,
            "email": self.email,
            "generated_at": self.generated_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# ContentFactory
# ---------------------------------------------------------------------------

class ContentFactory:
    """Трансформирует один кусок контента в оптимизированные посты для 6 платформ.

    Паттерн Велса: Content Factory сгенерировала 670K подписчиков YouTube за 6 месяцев.
    Каждая платформа имеет специфические требования по формату, длине, тональности и SEO.
    """

    # task_type для AI-роутера (добавляется в DEFAULT_TASK_POLICIES)
    TASK_TYPE = "content_factory"

    async def generate_all(
        self,
        session: AsyncSession,
        *,
        source_text: str,
        topic: str,
        target_audience: str,
        brand_voice: str = "professional",
        tenant_id: int,
        workspace_id: int | None = None,
        user_id: int | None = None,
    ) -> ContentPack:
        """Генерирует контент для всех 6 платформ параллельно.

        Использует asyncio.gather для параллельных AI-запросов — каждая платформа
        получает свой промпт и запускается независимо.
        """
        voice_desc = BRAND_VOICES.get(brand_voice, BRAND_VOICES["professional"])
        common_kwargs = dict(
            source_text=source_text,
            topic=topic,
            target_audience=target_audience,
            voice_desc=voice_desc,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )

        log.info(
            "content_factory.generate_all started",
            topic=topic,
            brand_voice=brand_voice,
            tenant_id=tenant_id,
            source_len=len(source_text),
        )

        results = await asyncio.gather(
            self._run_telegram(session, **common_kwargs),
            self._run_twitter(session, **common_kwargs),
            self._run_linkedin(session, **common_kwargs),
            self._run_youtube(session, **common_kwargs),
            self._run_reels(session, **common_kwargs),
            self._run_email(session, **common_kwargs),
            return_exceptions=True,
        )

        telegram_out, twitter_out, linkedin_out, youtube_out, reels_out, email_out = results

        # Подменяем ошибки безопасными заглушками — не роняем весь пакет
        telegram_str = telegram_out if isinstance(telegram_out, str) else _fallback_telegram(topic)
        twitter_list = twitter_out if isinstance(twitter_out, list) else _fallback_twitter(topic)
        linkedin_str = linkedin_out if isinstance(linkedin_out, str) else _fallback_linkedin(topic)
        youtube_str = youtube_out if isinstance(youtube_out, str) else _fallback_youtube(topic)
        reels_list = reels_out if isinstance(reels_out, list) else _fallback_reels(topic)
        email_dict = email_out if isinstance(email_out, dict) else _fallback_email(topic)

        # Логируем ошибки отдельно
        for name, res in [
            ("telegram", telegram_out),
            ("twitter", twitter_out),
            ("linkedin", linkedin_out),
            ("youtube", youtube_out),
            ("reels", reels_out),
            ("email", email_out),
        ]:
            if isinstance(res, Exception):
                log.warning(
                    "content_factory.platform_failed",
                    platform=name,
                    error=str(res),
                    tenant_id=tenant_id,
                )

        pack = ContentPack(
            source_text=source_text,
            topic=topic,
            telegram=telegram_str,
            twitter_thread=twitter_list,
            linkedin=linkedin_str,
            youtube_description=youtube_str,
            reels_ideas=reels_list,
            email=email_dict,
        )

        log.info(
            "content_factory.generate_all done",
            topic=topic,
            tenant_id=tenant_id,
            twitter_tweets=len(twitter_list),
            reels_count=len(reels_list),
        )
        return pack

    # ------------------------------------------------------------------
    # Публичные методы генерации (одна платформа)
    # ------------------------------------------------------------------

    async def generate_telegram(
        self,
        session: AsyncSession,
        source: str,
        topic: str,
        audience: str,
        voice: str,
        tenant_id: int,
        workspace_id: int | None = None,
        user_id: int | None = None,
    ) -> str:
        voice_desc = BRAND_VOICES.get(voice, BRAND_VOICES["professional"])
        return await self._run_telegram(
            session,
            source_text=source,
            topic=topic,
            target_audience=audience,
            voice_desc=voice_desc,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )

    async def generate_twitter_thread(
        self,
        session: AsyncSession,
        source: str,
        topic: str,
        audience: str,
        voice: str,
        tenant_id: int,
        workspace_id: int | None = None,
        user_id: int | None = None,
    ) -> list[str]:
        voice_desc = BRAND_VOICES.get(voice, BRAND_VOICES["professional"])
        return await self._run_twitter(
            session,
            source_text=source,
            topic=topic,
            target_audience=audience,
            voice_desc=voice_desc,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )

    async def generate_linkedin(
        self,
        session: AsyncSession,
        source: str,
        topic: str,
        audience: str,
        voice: str,
        tenant_id: int,
        workspace_id: int | None = None,
        user_id: int | None = None,
    ) -> str:
        voice_desc = BRAND_VOICES.get(voice, BRAND_VOICES["professional"])
        return await self._run_linkedin(
            session,
            source_text=source,
            topic=topic,
            target_audience=audience,
            voice_desc=voice_desc,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )

    async def generate_youtube_description(
        self,
        session: AsyncSession,
        source: str,
        topic: str,
        audience: str,
        voice: str,
        tenant_id: int,
        workspace_id: int | None = None,
        user_id: int | None = None,
    ) -> str:
        voice_desc = BRAND_VOICES.get(voice, BRAND_VOICES["professional"])
        return await self._run_youtube(
            session,
            source_text=source,
            topic=topic,
            target_audience=audience,
            voice_desc=voice_desc,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )

    async def generate_reels_ideas(
        self,
        session: AsyncSession,
        source: str,
        topic: str,
        audience: str,
        voice: str,
        tenant_id: int,
        workspace_id: int | None = None,
        user_id: int | None = None,
    ) -> list[dict]:
        voice_desc = BRAND_VOICES.get(voice, BRAND_VOICES["professional"])
        return await self._run_reels(
            session,
            source_text=source,
            topic=topic,
            target_audience=audience,
            voice_desc=voice_desc,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )

    async def generate_email(
        self,
        session: AsyncSession,
        source: str,
        topic: str,
        audience: str,
        voice: str,
        tenant_id: int,
        workspace_id: int | None = None,
        user_id: int | None = None,
    ) -> dict:
        voice_desc = BRAND_VOICES.get(voice, BRAND_VOICES["professional"])
        return await self._run_email(
            session,
            source_text=source,
            topic=topic,
            target_audience=audience,
            voice_desc=voice_desc,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )

    # ------------------------------------------------------------------
    # Внутренние методы генерации (с промптами)
    # ------------------------------------------------------------------

    async def _run_telegram(
        self,
        session: AsyncSession,
        *,
        source_text: str,
        topic: str,
        target_audience: str,
        voice_desc: str,
        tenant_id: int,
        workspace_id: int | None,
        user_id: int | None,
    ) -> str:
        """Telegram: 500-1500 символов, Markdown, эмодзи-заголовки, прямой тон."""
        system = (
            "Ты — экспертный копирайтер для Telegram-каналов. "
            "Твоя задача — писать посты, которые получают высокое вовлечение. "
            "Используй Markdown-форматирование Telegram (жирный, курсив, ссылки). "
            "Обязательно: эмодзи в заголовках, прямой разговорный стиль, чёткий призыв к действию."
        )
        prompt = f"""Напиши Telegram-пост на основе следующего контента.

Тема: {topic}
Целевая аудитория: {target_audience}
Голос бренда: {voice_desc}

Исходный контент:
{source_text}

Требования:
- Длина: 500-1500 символов
- Формат: Telegram Markdown (** для жирного, __ для курсива)
- Начни с яркого эмодзи-заголовка (например: 🔥 **Заголовок**)
- Раздели текст на 2-3 смысловых блока
- Закончи призывом к действию (подписаться, поставить реакцию, написать в комменты)
- Тональность: {voice_desc}

Верни ТОЛЬКО текст поста, без пояснений и markdown-блоков вокруг него.
Ответ должен быть на русском языке."""

        result = await route_ai_task(
            session,
            task_type=self.TASK_TYPE,
            prompt=prompt,
            system_instruction=system,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            max_output_tokens=1000,
            temperature=0.7,
            surface="content_factory",
        )

        if not result.ok or not result.parsed:
            raise RuntimeError(f"Telegram generation failed: {result.reason_code}")

        return _extract_text(result.parsed)

    async def _run_twitter(
        self,
        session: AsyncSession,
        *,
        source_text: str,
        topic: str,
        target_audience: str,
        voice_desc: str,
        tenant_id: int,
        workspace_id: int | None,
        user_id: int | None,
    ) -> list[str]:
        """Twitter/X: тред из 5-10 твитов, короткие удары, провокационный тон."""
        system = (
            "Ты — мастер Twitter/X. Ты создаёшь вирусные треды, "
            "которые взрывают вовлечение. Каждый твит — самостоятельный удар. "
            "Первый твит — хук, последний — CTA + ссылка на подписку."
        )
        prompt = f"""Создай Twitter/X тред из 5-10 твитов на основе следующего контента.

Тема: {topic}
Целевая аудитория: {target_audience}
Голос бренда: {voice_desc}

Исходный контент:
{source_text}

Требования:
- 5-10 твитов в треде
- Каждый твит: максимум 280 символов
- Первый твит: максимально цепляющий хук, вопрос или провокационное утверждение
- Нумерация: начни каждый твит с цифры (1/, 2/, ... N/)
- Короткие ударные предложения, без лишних слов
- Используй числа и факты где возможно
- Последний твит: призыв к действию (подписаться, ретвитнуть, ответить)
- Тональность: {voice_desc}

Верни JSON-объект в строго следующем формате:
{{"tweets": ["текст твита 1", "текст твита 2", ...]}}

Все тексты на русском языке."""

        result = await route_ai_task(
            session,
            task_type=self.TASK_TYPE,
            prompt=prompt,
            system_instruction=system,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            max_output_tokens=1500,
            temperature=0.8,
            surface="content_factory",
        )

        if not result.ok or not result.parsed:
            raise RuntimeError(f"Twitter generation failed: {result.reason_code}")

        return _extract_tweet_list(result.parsed)

    async def _run_linkedin(
        self,
        session: AsyncSession,
        *,
        source_text: str,
        topic: str,
        target_audience: str,
        voice_desc: str,
        tenant_id: int,
        workspace_id: int | None,
        user_id: int | None,
    ) -> str:
        """LinkedIn: 1300-2000 символов, профессиональный тон, экспертиза."""
        system = (
            "Ты — эксперт по LinkedIn-контенту. "
            "Пишешь профессиональные посты, которые повышают авторитет и получают вирусный охват. "
            "LinkedIn ценит длинные рефлексивные посты с личной позицией и практическими выводами."
        )
        prompt = f"""Напиши LinkedIn-пост на основе следующего контента.

Тема: {topic}
Целевая аудитория: {target_audience}
Голос бренда: {voice_desc}

Исходный контент:
{source_text}

Требования:
- Длина: 1300-2000 символов
- Начни с сильного первого предложения (без подводки — сразу к сути)
- Структура: наблюдение/проблема → анализ → вывод/совет
- Используй короткие абзацы (2-3 предложения) для читабельности
- Добавь 3-5 профессиональных хэштегов в конце
- Избегай корпоративного штампованного языка
- Покажи личную позицию эксперта
- Тональность: {voice_desc}

Верни ТОЛЬКО текст поста с хэштегами. Без пояснений.
Текст на русском языке."""

        result = await route_ai_task(
            session,
            task_type=self.TASK_TYPE,
            prompt=prompt,
            system_instruction=system,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            max_output_tokens=1200,
            temperature=0.65,
            surface="content_factory",
        )

        if not result.ok or not result.parsed:
            raise RuntimeError(f"LinkedIn generation failed: {result.reason_code}")

        return _extract_text(result.parsed)

    async def _run_youtube(
        self,
        session: AsyncSession,
        *,
        source_text: str,
        topic: str,
        target_audience: str,
        voice_desc: str,
        tenant_id: int,
        workspace_id: int | None,
        user_id: int | None,
    ) -> str:
        """YouTube: 500-5000 символов, SEO-ключевые слова, таймкоды, CTA."""
        system = (
            "Ты — SEO-специалист по YouTube-контенту. "
            "Пишешь описания, которые ранжируются в поиске YouTube и Google, "
            "и конвертируют зрителей в подписчиков."
        )
        prompt = f"""Напиши описание для YouTube-видео на основе следующего контента.

Тема: {topic}
Целевая аудитория: {target_audience}
Голос бренда: {voice_desc}

Исходный контент:
{source_text}

Требования:
- Длина: 500-2000 символов
- Первые 2-3 строки — критически важны для превью (напиши их максимально compelling)
- Добавь раздел с таймкодами (даже примерными): "ТАЙМКОДЫ:" с временными метками
- Вставь 5-10 SEO-ключевых слов естественно в текст (не спамом)
- Добавь раздел "ЧТО БУДЕТ В ВИДЕО:" с буллет-пойнтами
- CTA: призыв подписаться, лайкнуть, включить уведомления
- Добавь 5-7 хэштегов в конце
- Тональность: {voice_desc}

Верни ТОЛЬКО текст описания. Текст на русском языке."""

        result = await route_ai_task(
            session,
            task_type=self.TASK_TYPE,
            prompt=prompt,
            system_instruction=system,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            max_output_tokens=1500,
            temperature=0.6,
            surface="content_factory",
        )

        if not result.ok or not result.parsed:
            raise RuntimeError(f"YouTube generation failed: {result.reason_code}")

        return _extract_text(result.parsed)

    async def _run_reels(
        self,
        session: AsyncSession,
        *,
        source_text: str,
        topic: str,
        target_audience: str,
        voice_desc: str,
        tenant_id: int,
        workspace_id: int | None,
        user_id: int | None,
    ) -> list[dict]:
        """Reels/Shorts: 5 идей, 15-60 сек, хук → ценность → CTA."""
        system = (
            "Ты — режиссёр короткого видеоконтента (Reels, TikTok, YouTube Shorts). "
            "Создаёшь вирусные форматы с чётким хуком в первые 3 секунды, "
            "мощной ценностью и убедительным призывом к действию."
        )
        prompt = f"""Создай 5 идей для Reels/Shorts на основе следующего контента.

Тема: {topic}
Целевая аудитория: {target_audience}
Голос бренда: {voice_desc}

Исходный контент:
{source_text}

Требования:
- Ровно 5 идей
- Каждая идея: длительность 15-60 секунд
- Структура каждого: Хук (первые 3 сек) → Ценность/Контент → CTA
- Хук должен мгновенно останавливать скролл (вопрос, провокация, цифра, шок-факт)
- Разные форматы: текст на экране, говорящая голова, демонстрация, сравнение, лайфхак
- CTA: подписаться, сохранить, поделиться

Верни JSON-объект в строго следующем формате:
{{
  "ideas": [
    {{
      "hook": "первые 3 секунды — что говорим/показываем",
      "script": "полный сценарий 15-60 сек (что говорим + что показываем)",
      "cta": "призыв к действию в конце",
      "duration_sec": 30
    }}
  ]
}}

Все тексты на русском языке."""

        result = await route_ai_task(
            session,
            task_type=self.TASK_TYPE,
            prompt=prompt,
            system_instruction=system,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            max_output_tokens=2000,
            temperature=0.85,
            surface="content_factory",
        )

        if not result.ok or not result.parsed:
            raise RuntimeError(f"Reels generation failed: {result.reason_code}")

        return _extract_reels_list(result.parsed)

    async def _run_email(
        self,
        session: AsyncSession,
        *,
        source_text: str,
        topic: str,
        target_audience: str,
        voice_desc: str,
        tenant_id: int,
        workspace_id: int | None,
        user_id: int | None,
    ) -> dict:
        """Email: 300-800 символов, тема + тело + CTA, личный тёплый тон."""
        system = (
            "Ты — email-маркетолог с высоким open rate. "
            "Пишешь письма, которые открывают и читают. "
            "Личный тон, конкретная польза, один чёткий призыв к действию."
        )
        prompt = f"""Напиши email-рассылку на основе следующего контента.

Тема письма должна быть написана как человек, не как компания.
Аудитория: {target_audience}
Голос бренда: {voice_desc}

Исходный контент:
{source_text}

Тема (topic): {topic}

Требования:
- Тема письма (subject): 40-60 символов, цепляющая, без spam-слов
- Тело письма (body): 300-800 символов, личный тон, одна главная идея
- Призыв к действию (cta): одна конкретная кнопка/ссылка, 3-7 слов
- Не используй: "Уважаемый", "Здравствуйте", "Дорогой подписчик"
- Начни как будто продолжаешь разговор: "Хотел поделиться...", "Только что...", "Вы просили..."
- Тональность: {voice_desc}

Верни JSON-объект в строго следующем формате:
{{
  "subject": "тема письма",
  "body": "тело письма",
  "cta": "текст кнопки"
}}

Все тексты на русском языке."""

        result = await route_ai_task(
            session,
            task_type=self.TASK_TYPE,
            prompt=prompt,
            system_instruction=system,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            max_output_tokens=700,
            temperature=0.7,
            surface="content_factory",
        )

        if not result.ok or not result.parsed:
            raise RuntimeError(f"Email generation failed: {result.reason_code}")

        return _extract_email_dict(result.parsed)


# ---------------------------------------------------------------------------
# Вспомогательные функции разбора ответов AI
# ---------------------------------------------------------------------------

def _extract_text(parsed: Any) -> str:
    """Извлекает строку из ответа AI (может быть dict с ключом text/content)."""
    if isinstance(parsed, str):
        return parsed.strip()
    if isinstance(parsed, dict):
        for key in ("text", "content", "post", "result", "output"):
            if key in parsed and isinstance(parsed[key], str):
                return parsed[key].strip()
        # Fallback: берём первое строковое значение
        for v in parsed.values():
            if isinstance(v, str) and len(v) > 20:
                return v.strip()
    return str(parsed).strip()


def _extract_tweet_list(parsed: Any) -> list[str]:
    """Извлекает список твитов из ответа AI."""
    if isinstance(parsed, list):
        return [str(t).strip() for t in parsed if str(t).strip()]
    if isinstance(parsed, dict):
        for key in ("tweets", "thread", "items", "list"):
            val = parsed.get(key)
            if isinstance(val, list):
                return [str(t).strip() for t in val if str(t).strip()]
    # Пробуем распарсить строку как JSON
    raw = _extract_text(parsed)
    try:
        obj = json.loads(raw)
        if isinstance(obj, list):
            return [str(t).strip() for t in obj if str(t).strip()]
        if isinstance(obj, dict):
            for key in ("tweets", "thread", "items"):
                val = obj.get(key)
                if isinstance(val, list):
                    return [str(t).strip() for t in val if str(t).strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    # Последний fallback: разбиваем по нумерации
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    tweets = [re.sub(r"^\d+[/\.]\s*", "", l) for l in lines if re.match(r"^\d+[/\.]", l)]
    return tweets if tweets else lines[:10]


def _extract_reels_list(parsed: Any) -> list[dict]:
    """Извлекает список идей для Reels из ответа AI."""
    _required_keys = {"hook", "script", "cta", "duration_sec"}

    def _normalize(item: Any) -> dict | None:
        if not isinstance(item, dict):
            return None
        return {
            "hook": str(item.get("hook", "")).strip(),
            "script": str(item.get("script", "")).strip(),
            "cta": str(item.get("cta", "")).strip(),
            "duration_sec": int(item.get("duration_sec", 30)),
        }

    if isinstance(parsed, list):
        ideas = [_normalize(i) for i in parsed]
        return [i for i in ideas if i]

    if isinstance(parsed, dict):
        for key in ("ideas", "reels", "shorts", "items"):
            val = parsed.get(key)
            if isinstance(val, list):
                ideas = [_normalize(i) for i in val]
                return [i for i in ideas if i]

    # Пробуем разобрать как JSON-строку
    raw = _extract_text(parsed)
    try:
        obj = json.loads(raw)
        if isinstance(obj, list):
            ideas = [_normalize(i) for i in obj]
            return [i for i in ideas if i]
        if isinstance(obj, dict):
            for key in ("ideas", "reels", "items"):
                val = obj.get(key)
                if isinstance(val, list):
                    ideas = [_normalize(i) for i in val]
                    return [i for i in ideas if i]
    except (json.JSONDecodeError, TypeError):
        pass

    return _fallback_reels("контент")


def _extract_email_dict(parsed: Any) -> dict:
    """Извлекает {subject, body, cta} из ответа AI."""
    _required = {"subject", "body", "cta"}

    if isinstance(parsed, dict) and _required.issubset(parsed.keys()):
        return {
            "subject": str(parsed["subject"]).strip(),
            "body": str(parsed["body"]).strip(),
            "cta": str(parsed["cta"]).strip(),
        }

    # Пробуем как JSON-строку
    raw = _extract_text(parsed)
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and _required.issubset(obj.keys()):
            return {
                "subject": str(obj["subject"]).strip(),
                "body": str(obj["body"]).strip(),
                "cta": str(obj["cta"]).strip(),
            }
    except (json.JSONDecodeError, TypeError):
        pass

    return _fallback_email("контент")


# ---------------------------------------------------------------------------
# Fallback-заглушки при ошибке генерации
# ---------------------------------------------------------------------------

def _fallback_telegram(topic: str) -> str:
    return f"📌 **{topic}**\n\nКонтент временно недоступен. Повторите попытку."


def _fallback_twitter(topic: str) -> list[str]:
    return [
        f"1/ {topic} — тред временно недоступен.",
        "2/ Повторите попытку позже.",
    ]


def _fallback_linkedin(topic: str) -> str:
    return f"{topic}\n\nКонтент временно недоступен. Повторите попытку позже."


def _fallback_youtube(topic: str) -> str:
    return f"{topic}\n\nОписание временно недоступно. Повторите попытку."


def _fallback_reels(topic: str) -> list[dict]:
    return [
        {
            "hook": f"Всё что нужно знать о {topic} за 30 секунд",
            "script": "Контент временно недоступен. Повторите попытку.",
            "cta": "Подписаться",
            "duration_sec": 30,
        }
    ]


def _fallback_email(topic: str) -> dict:
    return {
        "subject": f"О теме: {topic}",
        "body": "Контент временно недоступен. Повторите попытку.",
        "cta": "Читать далее",
    }
