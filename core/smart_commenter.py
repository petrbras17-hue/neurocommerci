"""
Smart Commenting Engine — интеллектуальная система генерации комментариев.

Основные компоненты:
  PostAnalyzer       — анализ поста и существующих комментариев
  CommentGenerator   — генерация вирусных комментариев через AI router
  CommentStrategy    — стратегия: когда и как комментировать
  CommentOrchestrator — склеивает все вместе, интегрируется с FarmThread

Ключевые принципы безопасности:
  - НИКОГДА не комментируем первыми — ждём 2-10 мин и минимум N чужих комментариев
  - Emoji-first трюк: сначала шлём эмодзи, потом редактируем на реальный текст
  - Все AI-вызовы идут через route_ai_task(), не напрямую к провайдеру
  - Все действия логируются в FarmEvent
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from core.ai_router import route_ai_task
from storage.models import FarmEvent
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log


# ---------------------------------------------------------------------------
# Публичные константы — тональности
# ---------------------------------------------------------------------------

TONE_POSITIVE = "positive"       # одобрение, поддержка
TONE_HATER = "hater"             # скептицизм, оппозиция
TONE_EMOTIONAL = "emotional"     # эмоциональный отклик
TONE_EXPERT = "expert"           # экспертная оценка
TONE_WITTY = "witty"             # ирония и юмор

VALID_TONES = {TONE_POSITIVE, TONE_HATER, TONE_EMOTIONAL, TONE_EXPERT, TONE_WITTY}

# Стратегии частоты комментирования
FREQ_ALL = "all"                 # комментировать все посты
FREQ_30PCT = "30pct"             # 30% постов
FREQ_BY_KEYWORDS = "by_keywords" # только посты с нужными ключевыми словами

VALID_FREQUENCIES = {FREQ_ALL, FREQ_30PCT, FREQ_BY_KEYWORDS}

# Emoji-пул для первого «безопасного» комментария перед редактированием
_SAFE_EMOJIS = ["👍", "🔥", "💯", "👏", "💪", "🎯", "✅", "⚡", "🚀", "😎"]

# Минимальная задержка перед первым комментарием (секунды) — антидетект
_MIN_WAIT_BEFORE_FIRST_COMMENT_SEC = 120   # 2 мин
_MAX_WAIT_BEFORE_FIRST_COMMENT_SEC = 600   # 10 мин

# Задержка между emoji-постом и редактированием (секунды)
_EMOJI_TRICK_EDIT_DELAY_MIN = 40
_EMOJI_TRICK_EDIT_DELAY_MAX = 55


# ---------------------------------------------------------------------------
# Dataclass-контракты
# ---------------------------------------------------------------------------

@dataclass
class PostAnalysis:
    """Результат анализа поста."""
    topic: str                       # краткая тема
    sentiment: str                   # positive / negative / neutral / mixed
    key_points: list[str]            # 2-4 ключевых тезиса
    suggested_angle: str             # рекомендованный угол для комментария
    language: str = "ru"             # определённый язык поста
    is_promotional: bool = False     # рекламный пост?
    has_questions: bool = False      # пост содержит вопросы?


@dataclass
class CommentContext:
    """Анализ существующих комментариев под постом."""
    top_themes: list[str]            # доминирующие темы чужих комментариев
    gaps: list[str]                  # чего не хватает в диалоге
    opportunity_score: float         # 0.0–1.0: насколько выгодно комментировать
    comments_count: int = 0          # количество проанализированных комментариев
    dominant_sentiment: str = "neutral"


@dataclass
class CommentDecision:
    """Решение стратегии — комментировать или нет."""
    should_comment: bool
    delay_seconds: float             # сколько ждать перед постингом
    reason: str                      # человекочитаемое пояснение
    tone_override: Optional[str] = None   # принудительная тональность
    use_emoji_trick: bool = False    # использовать emoji-first трюк


@dataclass
class CommentingConfig:
    """
    Настройки стратегии комментирования для одной фермы/потока.
    Аналог FarmConfig, но специфичен для smart commenter.
    """
    tone: str = TONE_POSITIVE
    language: str = "auto"
    frequency: str = FREQ_ALL
    keywords: list[str] = field(default_factory=list)
    max_comments_per_hour: int = 10
    max_comments_per_day: int = 50
    min_existing_comments: int = 1   # не комментировать, если чужих < N
    account_rotate_every_n: int = 5  # ротация аккаунта каждые N комментариев
    use_emoji_trick: bool = True
    custom_prompt: str = ""


# ---------------------------------------------------------------------------
# PostAnalyzer
# ---------------------------------------------------------------------------

class PostAnalyzer:
    """
    Анализирует пост и существующие комментарии через AI-роутер.
    Возвращает PostAnalysis и CommentContext без побочных эффектов в БД.
    """

    async def analyze_post(
        self,
        post_text: str,
        channel_info: dict,
        tenant_id: int,
    ) -> PostAnalysis:
        """
        Отправляет пост в AI и получает структурированный анализ.

        Args:
            post_text:    текст поста (до 2000 символов)
            channel_info: {'title': ..., 'username': ..., 'category': ...}
            tenant_id:    для RLS-контекста

        Returns:
            PostAnalysis с темой, тональностью, ключевыми тезисами и углом.
        """
        channel_hint = channel_info.get("title", "") or channel_info.get("username", "")
        category_hint = channel_info.get("category", "")

        prompt = (
            f"Channel: {channel_hint}"
            + (f" [{category_hint}]" if category_hint else "")
            + f"\n\nPost:\n{post_text[:2000]}\n\n"
            "Analyze this Telegram post. "
            "Return JSON with keys: "
            '"topic" (string, max 10 words), '
            '"sentiment" (positive|negative|neutral|mixed), '
            '"key_points" (array of 2-4 strings), '
            '"suggested_angle" (string, 1 sentence: what angle a commenter should take), '
            '"language" (2-letter ISO code), '
            '"is_promotional" (bool), '
            '"has_questions" (bool). '
            "Be concise."
        )
        system_instruction = (
            "You are a Telegram content analyst. "
            "Analyze the post and return a strict JSON object with no extra text."
        )

        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    result = await route_ai_task(
                        sess,
                        task_type="farm_comment",
                        prompt=prompt,
                        system_instruction=system_instruction,
                        tenant_id=tenant_id,
                        max_output_tokens=300,
                        temperature=0.3,
                        surface="smart_commenter_analysis",
                    )

            if result.ok and result.parsed:
                d = result.parsed
                return PostAnalysis(
                    topic=str(d.get("topic", "general")),
                    sentiment=str(d.get("sentiment", "neutral")),
                    key_points=list(d.get("key_points") or []),
                    suggested_angle=str(d.get("suggested_angle", "")),
                    language=str(d.get("language", "ru")),
                    is_promotional=bool(d.get("is_promotional", False)),
                    has_questions=bool(d.get("has_questions", False)),
                )
        except Exception as exc:
            log.warning(f"PostAnalyzer.analyze_post: AI failed: {exc}")

        # Fallback: минимальный анализ по эвристике
        return _heuristic_post_analysis(post_text)

    async def analyze_existing_comments(
        self,
        comments: list[str],
        tenant_id: int,
    ) -> CommentContext:
        """
        Анализирует список существующих комментариев.
        Ищет доминирующие темы и пробелы в диалоге.

        Args:
            comments:  список текстов комментариев (до 30 штук)
            tenant_id: для RLS

        Returns:
            CommentContext с темами, пробелами и opportunity_score.
        """
        if not comments:
            return CommentContext(
                top_themes=[],
                gaps=["no comments yet"],
                opportunity_score=0.5,
                comments_count=0,
            )

        # Берём не более 20 комментариев, чтобы не раздувать промпт
        sample = comments[:20]
        joined = "\n".join(f"- {c[:150]}" for c in sample)

        prompt = (
            f"Here are {len(sample)} comments on a Telegram post:\n{joined}\n\n"
            "Analyze them. Return JSON with keys: "
            '"top_themes" (array of 2-3 strings), '
            '"gaps" (array of 1-3 strings: perspectives missing from the discussion), '
            '"opportunity_score" (float 0.0-1.0: how valuable it is to add a new comment), '
            '"dominant_sentiment" (positive|negative|neutral|mixed).'
        )
        system_instruction = (
            "You are a Telegram comment analyst. "
            "Return a strict JSON object with no extra text."
        )

        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    result = await route_ai_task(
                        sess,
                        task_type="farm_comment",
                        prompt=prompt,
                        system_instruction=system_instruction,
                        tenant_id=tenant_id,
                        max_output_tokens=250,
                        temperature=0.3,
                        surface="smart_commenter_ctx",
                    )

            if result.ok and result.parsed:
                d = result.parsed
                return CommentContext(
                    top_themes=list(d.get("top_themes") or []),
                    gaps=list(d.get("gaps") or []),
                    opportunity_score=float(d.get("opportunity_score", 0.5)),
                    comments_count=len(comments),
                    dominant_sentiment=str(d.get("dominant_sentiment", "neutral")),
                )
        except Exception as exc:
            log.warning(f"PostAnalyzer.analyze_existing_comments: AI failed: {exc}")

        return CommentContext(
            top_themes=[],
            gaps=["analysis unavailable"],
            opportunity_score=0.4,
            comments_count=len(comments),
        )


# ---------------------------------------------------------------------------
# CommentGenerator
# ---------------------------------------------------------------------------

class CommentGenerator:
    """
    Генерирует вирусные комментарии для Telegram через AI-роутер.

    Гарантии:
    - всегда использует route_ai_task с task_type='farm_comment'
    - учитывает существующий контекст комментариев
    - никогда не добавляет ссылки или хэштеги
    - адаптирует язык под язык поста
    """

    # Системный промпт является фиксированным — не позволяем менять снаружи
    _BASE_SYSTEM_PROMPT = (
        "Write a comment that people will like. "
        "Be authentic, no links, no hashtags. "
        "Match the language of the post. "
        "Never be the first commenter — analyze what others said and add value. "
        "Do NOT mention AI, bots, or promotion. "
        "Return JSON: {\"text\": \"<comment text here>\"}."
    )

    _TONE_INSTRUCTIONS = {
        TONE_POSITIVE: "Be supportive and encouraging. Express genuine enthusiasm.",
        TONE_HATER: (
            "Be a sceptic. Express mild disagreement or critique constructively. "
            "Do not be offensive or use profanity."
        ),
        TONE_EMOTIONAL: (
            "React emotionally and personally. Use first-person feelings. "
            "Emojis are allowed (1-2 max)."
        ),
        TONE_EXPERT: (
            "Sound like a domain expert. Add a specific fact or insight. "
            "Be concise and confident."
        ),
        TONE_WITTY: (
            "Be clever and slightly ironic. Witty observation welcome. "
            "Keep it light, never mean."
        ),
    }

    async def generate_viral_comment(
        self,
        post_analysis: PostAnalysis,
        comment_context: CommentContext,
        tone: str,
        language: str,
        tenant_id: int,
        custom_prompt: str = "",
    ) -> Optional[str]:
        """
        Генерирует один комментарий.

        Args:
            post_analysis:    результат анализа поста
            comment_context:  анализ уже существующих комментариев
            tone:             одна из VALID_TONES
            language:         ISO-код языка или 'auto'
            tenant_id:        для RLS
            custom_prompt:    дополнительные инструкции оператора

        Returns:
            Текст комментария или None при ошибке.
        """
        if tone not in VALID_TONES:
            tone = TONE_POSITIVE

        tone_instruction = self._TONE_INSTRUCTIONS.get(tone, "")
        lang_instruction = (
            f"Write exclusively in {language}."
            if language and language != "auto"
            else f"Write in the language detected from the post: {post_analysis.language}."
        )

        gaps_hint = ""
        if comment_context.gaps:
            gaps_hint = (
                f"The discussion is missing: {', '.join(comment_context.gaps[:2])}. "
                "Your comment should fill one of these gaps."
            )

        system_instruction = (
            f"{self._BASE_SYSTEM_PROMPT}\n"
            f"Tone: {tone_instruction}\n"
            f"{lang_instruction}\n"
            f"{gaps_hint}"
            + (f"\nAdditional operator instructions: {custom_prompt}" if custom_prompt else "")
        ).strip()

        # Построение полного промпта
        key_points_text = "; ".join(post_analysis.key_points) if post_analysis.key_points else ""
        existing_themes = ", ".join(comment_context.top_themes) if comment_context.top_themes else "none"
        full_prompt = (
            f"Post topic: {post_analysis.topic}\n"
            f"Post sentiment: {post_analysis.sentiment}\n"
            + (f"Key points: {key_points_text}\n" if key_points_text else "")
            + f"Suggested angle: {post_analysis.suggested_angle}\n"
            f"Existing comment themes: {existing_themes}\n"
            f"Number of existing comments: {comment_context.comments_count}\n\n"
            "Write one short natural comment (max 25 words) that adds value to this discussion."
        )

        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    result = await route_ai_task(
                        sess,
                        task_type="farm_comment",
                        prompt=full_prompt,
                        system_instruction=system_instruction,
                        tenant_id=tenant_id,
                        max_output_tokens=150,
                        temperature=0.85,
                        surface="smart_commenter_gen",
                    )

            if result.ok and result.parsed:
                text = (result.parsed or {}).get("text", "")
                text = text.strip()
                if text:
                    return _sanitize_comment(text)

            log.debug(
                f"CommentGenerator: AI returned no text "
                f"(ok={result.ok}, parsed={result.parsed})"
            )
        except Exception as exc:
            log.warning(f"CommentGenerator.generate_viral_comment: {exc}")

        return None


# ---------------------------------------------------------------------------
# CommentStrategy
# ---------------------------------------------------------------------------

class CommentStrategy:
    """
    Стратегия: когда и как публиковать комментарий.

    Правила:
    - НИКОГДА не комментируем первыми: ждём от 2 до 10 минут после публикации
    - Частота: all / 30pct / by_keywords
    - Ротация аккаунтов каждые N комментариев
    - Лимиты: max_comments_per_hour и max_comments_per_day на аккаунт
    - Emoji-first трюк опционален
    """

    def should_comment(
        self,
        post: dict,
        config: CommentingConfig,
        account_comments_this_hour: int = 0,
        account_comments_today: int = 0,
        existing_comments_count: int = 0,
    ) -> CommentDecision:
        """
        Определяет, стоит ли комментировать пост.

        Args:
            post:                       словарь с полями поста (text, posted_at, ...)
            config:                     CommentingConfig с настройками стратегии
            account_comments_this_hour: счётчик комментариев аккаунта за этот час
            account_comments_today:     счётчик за сегодня
            existing_comments_count:    сколько чужих комментариев уже есть

        Returns:
            CommentDecision с решением и задержкой.
        """
        # Лимит в час
        if account_comments_this_hour >= config.max_comments_per_hour:
            return CommentDecision(
                should_comment=False,
                delay_seconds=0,
                reason=f"hourly limit reached ({config.max_comments_per_hour})",
            )

        # Лимит в день
        if account_comments_today >= config.max_comments_per_day:
            return CommentDecision(
                should_comment=False,
                delay_seconds=0,
                reason=f"daily limit reached ({config.max_comments_per_day})",
            )

        # Никогда не быть первым — требуем минимум N чужих комментариев
        if existing_comments_count < config.min_existing_comments:
            return CommentDecision(
                should_comment=False,
                delay_seconds=0,
                reason=(
                    f"too few existing comments: {existing_comments_count} "
                    f"(min={config.min_existing_comments})"
                ),
            )

        # Фильтр по частоте
        if config.frequency == FREQ_30PCT:
            if random.randint(1, 100) > 30:
                return CommentDecision(
                    should_comment=False,
                    delay_seconds=0,
                    reason="30pct filter: skipped this post",
                )

        elif config.frequency == FREQ_BY_KEYWORDS:
            post_text = (post.get("text") or "").lower()
            matched = any(kw.lower() in post_text for kw in config.keywords)
            if not matched:
                return CommentDecision(
                    should_comment=False,
                    delay_seconds=0,
                    reason="by_keywords filter: no matching keywords",
                )

        # Вычисляем задержку: случайная пауза перед комментарием
        delay = random.uniform(
            _MIN_WAIT_BEFORE_FIRST_COMMENT_SEC,
            _MAX_WAIT_BEFORE_FIRST_COMMENT_SEC,
        )

        return CommentDecision(
            should_comment=True,
            delay_seconds=delay,
            reason="all checks passed",
            use_emoji_trick=config.use_emoji_trick,
        )

    def next_account_rotation(
        self,
        thread_comment_counter: int,
        rotate_every: int,
    ) -> bool:
        """
        Возвращает True, если пора ротировать аккаунт.

        Args:
            thread_comment_counter: сколько всего комментариев сделал поток
            rotate_every:           каждые N комментариев — ротация

        Returns:
            True если rotate_every > 0 и счётчик кратен rotate_every.
        """
        if rotate_every <= 0:
            return False
        return thread_comment_counter > 0 and (thread_comment_counter % rotate_every) == 0


# ---------------------------------------------------------------------------
# CommentOrchestrator
# ---------------------------------------------------------------------------

class CommentOrchestrator:
    """
    Интегрирует PostAnalyzer + CommentGenerator + CommentStrategy в единый
    пайплайн: мониторинг → анализ → решение → генерация → публикация.

    Предназначен для использования из FarmThread (передаётся как зависимость).
    Все действия логируются в FarmEvent через _log_event().

    Пример использования в FarmThread:
        orchestrator = CommentOrchestrator(
            tenant_id=self.tenant_id,
            farm_id=self.farm_id,
            thread_id=self.thread_id,
            commenting_config=CommentingConfig(tone="expert"),
        )
        comment_text, decision = await orchestrator.process_post(
            post=post_dict,
            existing_comments=["...", "..."],
            channel_info={"title": "Tech Channel"},
        )
    """

    def __init__(
        self,
        tenant_id: int,
        farm_id: int,
        thread_id: Optional[int],
        commenting_config: CommentingConfig,
    ) -> None:
        self.tenant_id = tenant_id
        self.farm_id = farm_id
        self.thread_id = thread_id
        self.config = commenting_config

        self._analyzer = PostAnalyzer()
        self._generator = CommentGenerator()
        self._strategy = CommentStrategy()

        # Внутренние счётчики для rate-limiting
        self._hour_bucket: tuple[int, int] = (0, 0)   # (utc_hour, count)
        self._day_counter: int = 0
        self._comment_counter: int = 0                 # суммарный счётчик

    # ------------------------------------------------------------------
    # Основной пайплайн
    # ------------------------------------------------------------------

    async def process_post(
        self,
        post: dict,
        existing_comments: list[str],
        channel_info: dict,
    ) -> tuple[Optional[str], CommentDecision]:
        """
        Полный цикл обработки одного поста.

        Args:
            post:              {'text': ..., 'message_id': ..., ...}
            existing_comments: список текстов уже опубликованных комментариев
            channel_info:      {'title': ..., 'username': ..., 'category': ...}

        Returns:
            (comment_text, decision)
            comment_text=None означает: не публиковать.
        """
        post_text = post.get("text", "")

        # 1. Обновить hourly bucket
        self._refresh_hourly_bucket()

        # 2. Проверка стратегии — без AI-вызовов
        decision = self._strategy.should_comment(
            post=post,
            config=self.config,
            account_comments_this_hour=self._hour_bucket[1],
            account_comments_today=self._day_counter,
            existing_comments_count=len(existing_comments),
        )

        if not decision.should_comment:
            await self._log_event(
                event_type="comment_skipped",
                message=f"Post skipped: {decision.reason}",
                severity="info",
                metadata={"reason": decision.reason},
            )
            return None, decision

        # 3. Анализ поста через AI
        post_analysis = await self._analyzer.analyze_post(
            post_text=post_text,
            channel_info=channel_info,
            tenant_id=self.tenant_id,
        )

        # 4. Анализ существующих комментариев
        comment_context = await self._analyzer.analyze_existing_comments(
            comments=existing_comments,
            tenant_id=self.tenant_id,
        )

        # 5. Если opportunity_score слишком низкий — не тратим деньги на генерацию
        if comment_context.opportunity_score < 0.2:
            await self._log_event(
                event_type="comment_skipped",
                message=(
                    f"Low opportunity score ({comment_context.opportunity_score:.2f}), "
                    "skipping comment generation"
                ),
                severity="info",
                metadata={"opportunity_score": comment_context.opportunity_score},
            )
            return None, CommentDecision(
                should_comment=False,
                delay_seconds=0,
                reason=f"low opportunity_score={comment_context.opportunity_score:.2f}",
            )

        # 6. Генерация комментария
        tone = decision.tone_override or self.config.tone
        comment_text = await self._generator.generate_viral_comment(
            post_analysis=post_analysis,
            comment_context=comment_context,
            tone=tone,
            language=self.config.language,
            tenant_id=self.tenant_id,
            custom_prompt=self.config.custom_prompt,
        )

        if not comment_text:
            await self._log_event(
                event_type="comment_failed",
                message="AI returned no comment text",
                severity="warn",
            )
            return None, decision

        # 7. Обновить счётчики
        self._comment_counter += 1
        hour, count = self._hour_bucket
        self._hour_bucket = (hour, count + 1)
        self._day_counter += 1

        await self._log_event(
            event_type="comment_generated",
            message=f"Generated comment ({len(comment_text)} chars, tone={tone})",
            severity="info",
            metadata={
                "tone": tone,
                "opportunity_score": comment_context.opportunity_score,
                "comment_preview": comment_text[:80],
                "post_topic": post_analysis.topic,
                "post_language": post_analysis.language,
                "existing_count": len(existing_comments),
            },
        )

        return comment_text, decision

    async def apply_emoji_trick(
        self,
        client,
        channel_entity,
        post_message_id: int,
        real_comment: str,
        stop_event: Optional[asyncio.Event] = None,
    ) -> bool:
        """
        Emoji-first трюк:
          1. Отправляем случайный эмодзи в ответ на пост
          2. Ждём 40-55 секунд
          3. Редактируем сообщение на реальный комментарий

        Args:
            client:          Telethon-клиент
            channel_entity:  сущность канала
            post_message_id: ID поста для reply
            real_comment:    итоговый текст комментария
            stop_event:      asyncio.Event для прерывания ожидания

        Returns:
            True при успешной публикации, False при ошибке.
        """
        emoji = random.choice(_SAFE_EMOJIS)
        try:
            from telethon.errors import FloodWaitError, MsgIdInvalidError
        except ImportError:
            log.warning("CommentOrchestrator.apply_emoji_trick: Telethon not available")
            return False

        try:
            # Шаг 1: отправить эмодзи
            sent_msg = await client.send_message(
                channel_entity,
                emoji,
                comment_to=post_message_id,
            )
            log.debug(
                f"CommentOrchestrator: emoji '{emoji}' sent, "
                f"msg_id={sent_msg.id}"
            )

            # Шаг 2: ждём перед редактированием
            edit_delay = random.uniform(
                _EMOJI_TRICK_EDIT_DELAY_MIN,
                _EMOJI_TRICK_EDIT_DELAY_MAX,
            )
            if stop_event is not None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=edit_delay)
                    if stop_event.is_set():
                        # Стоп-сигнал — сохраняем эмодзи как есть
                        return True
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(edit_delay)

            # Шаг 3: редактировать на реальный текст
            await client.edit_message(channel_entity, sent_msg.id, real_comment)
            log.debug(
                f"CommentOrchestrator: edited msg {sent_msg.id} to real comment"
            )

            await self._log_event(
                event_type="comment_sent",
                message=f"Comment posted via emoji trick (post #{post_message_id})",
                severity="info",
                metadata={
                    "method": "emoji_trick",
                    "post_message_id": post_message_id,
                    "comment_preview": real_comment[:80],
                },
            )
            return True

        except FloodWaitError as exc:
            log.warning(
                f"CommentOrchestrator.apply_emoji_trick: FloodWait {exc.seconds}s"
            )
            raise  # пробрасываем — пусть FarmThread обрабатывает

        except MsgIdInvalidError:
            log.debug(
                "CommentOrchestrator.apply_emoji_trick: post disappeared before edit"
            )
            return False

        except Exception as exc:
            log.warning(f"CommentOrchestrator.apply_emoji_trick: {exc}")
            return False

    # ------------------------------------------------------------------
    # Утилиты для предпросмотра (используются API-эндпоинтом)
    # ------------------------------------------------------------------

    async def preview_comment(
        self,
        post_text: str,
        channel_info: dict,
        existing_comments: list[str],
    ) -> dict:
        """
        Генерирует предпросмотр без реальной публикации.
        Возвращает полный анализ + сгенерированный комментарий.

        Используется эндпоинтом POST /v1/commenting/preview.
        """
        post = {"text": post_text}

        post_analysis = await self._analyzer.analyze_post(
            post_text=post_text,
            channel_info=channel_info,
            tenant_id=self.tenant_id,
        )
        comment_context = await self._analyzer.analyze_existing_comments(
            comments=existing_comments,
            tenant_id=self.tenant_id,
        )
        comment_text = await self._generator.generate_viral_comment(
            post_analysis=post_analysis,
            comment_context=comment_context,
            tone=self.config.tone,
            language=self.config.language,
            tenant_id=self.tenant_id,
            custom_prompt=self.config.custom_prompt,
        )

        decision = self._strategy.should_comment(
            post=post,
            config=self.config,
            account_comments_this_hour=0,
            account_comments_today=0,
            existing_comments_count=len(existing_comments),
        )

        return {
            "post_analysis": {
                "topic": post_analysis.topic,
                "sentiment": post_analysis.sentiment,
                "key_points": post_analysis.key_points,
                "suggested_angle": post_analysis.suggested_angle,
                "language": post_analysis.language,
                "is_promotional": post_analysis.is_promotional,
                "has_questions": post_analysis.has_questions,
            },
            "comment_context": {
                "top_themes": comment_context.top_themes,
                "gaps": comment_context.gaps,
                "opportunity_score": comment_context.opportunity_score,
                "comments_count": comment_context.comments_count,
                "dominant_sentiment": comment_context.dominant_sentiment,
            },
            "generated_comment": comment_text,
            "would_comment": decision.should_comment,
            "decision_reason": decision.reason,
            "strategy_delay_seconds": decision.delay_seconds,
            "tone": self.config.tone,
            "language": self.config.language,
        }

    # ------------------------------------------------------------------
    # Внутренние хелперы
    # ------------------------------------------------------------------

    def _refresh_hourly_bucket(self) -> None:
        """Сбрасывает часовой счётчик при смене часа."""
        from datetime import timezone
        current_hour = datetime.now(timezone.utc).hour
        if self._hour_bucket[0] != current_hour:
            self._hour_bucket = (current_hour, 0)

    async def _log_event(
        self,
        event_type: str,
        message: str,
        severity: str = "info",
        metadata: Optional[dict] = None,
    ) -> None:
        """Записывает событие в таблицу farm_events."""
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=self.tenant_id)
                    event = FarmEvent(
                        tenant_id=self.tenant_id,
                        farm_id=self.farm_id,
                        thread_id=self.thread_id,
                        event_type=event_type,
                        severity=severity,
                        message=message,
                        event_metadata=metadata,
                        created_at=utcnow(),
                    )
                    sess.add(event)
        except Exception as exc:
            log.debug(f"CommentOrchestrator._log_event: {exc}")


# ---------------------------------------------------------------------------
# Хелперы модуля
# ---------------------------------------------------------------------------

def _heuristic_post_analysis(post_text: str) -> PostAnalysis:
    """Эвристический fallback-анализ без AI."""
    text_lower = post_text.lower()
    # Определение языка по эвристике
    cyrillic_chars = sum(1 for c in post_text if "\u0400" <= c <= "\u04FF")
    language = "ru" if cyrillic_chars > len(post_text) * 0.3 else "en"

    # Определение тональности
    positive_words = {"отлично", "круто", "супер", "great", "amazing", "awesome", "хорошо"}
    negative_words = {"плохо", "ужасно", "terrible", "bad", "проблема", "issue"}
    has_positive = any(w in text_lower for w in positive_words)
    has_negative = any(w in text_lower for w in negative_words)
    if has_positive and has_negative:
        sentiment = "mixed"
    elif has_positive:
        sentiment = "positive"
    elif has_negative:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    return PostAnalysis(
        topic="general discussion",
        sentiment=sentiment,
        key_points=[post_text[:100]] if post_text else [],
        suggested_angle="Share your perspective on the topic.",
        language=language,
        is_promotional="реклама" in text_lower or "промо" in text_lower or "promo" in text_lower,
        has_questions="?" in post_text,
    )


def _sanitize_comment(text: str) -> str:
    """
    Очищает комментарий от потенциально опасного контента:
    - убирает ссылки (http/https)
    - убирает хэштеги
    - обрезает до разумной длины
    """
    import re
    # Убираем URL
    text = re.sub(r"https?://\S+", "", text)
    # Убираем хэштеги
    text = re.sub(r"#\w+", "", text)
    # Схлопываем лишние пробелы
    text = re.sub(r"\s+", " ", text).strip()
    # Обрезаем до 500 символов (Telegram limit гораздо выше, но для безопасности)
    return text[:500]


# ---------------------------------------------------------------------------
# Публичные фабричные функции
# ---------------------------------------------------------------------------

def build_orchestrator(
    tenant_id: int,
    farm_id: int,
    thread_id: Optional[int],
    *,
    tone: str = TONE_POSITIVE,
    language: str = "auto",
    frequency: str = FREQ_ALL,
    keywords: Optional[list[str]] = None,
    max_comments_per_hour: int = 10,
    max_comments_per_day: int = 50,
    min_existing_comments: int = 1,
    account_rotate_every_n: int = 5,
    use_emoji_trick: bool = True,
    custom_prompt: str = "",
) -> CommentOrchestrator:
    """
    Создаёт CommentOrchestrator с заданными параметрами.
    Предпочтительная точка входа для FarmThread и API-эндпоинтов.
    """
    config = CommentingConfig(
        tone=tone if tone in VALID_TONES else TONE_POSITIVE,
        language=language,
        frequency=frequency if frequency in VALID_FREQUENCIES else FREQ_ALL,
        keywords=keywords or [],
        max_comments_per_hour=max_comments_per_hour,
        max_comments_per_day=max_comments_per_day,
        min_existing_comments=min_existing_comments,
        account_rotate_every_n=account_rotate_every_n,
        use_emoji_trick=use_emoji_trick,
        custom_prompt=custom_prompt,
    )
    return CommentOrchestrator(
        tenant_id=tenant_id,
        farm_id=farm_id,
        thread_id=thread_id,
        commenting_config=config,
    )


def list_strategies() -> list[dict]:
    """
    Возвращает список доступных стратегий для GET /v1/commenting/strategies.
    """
    return [
        {
            "id": "all",
            "name": "Комментировать все посты",
            "description": "Комментирует каждый новый пост в канале.",
            "frequency": FREQ_ALL,
        },
        {
            "id": "30pct",
            "name": "Каждый третий пост (30%)",
            "description": "Комментирует примерно 30% постов, случайная выборка.",
            "frequency": FREQ_30PCT,
        },
        {
            "id": "by_keywords",
            "name": "По ключевым словам",
            "description": "Комментирует только посты, содержащие заданные ключевые слова.",
            "frequency": FREQ_BY_KEYWORDS,
        },
    ]


def list_tones() -> list[dict]:
    """
    Возвращает список доступных тональностей.
    """
    return [
        {
            "id": TONE_POSITIVE,
            "name": "Позитивный",
            "description": "Одобрение, поддержка, энтузиазм.",
        },
        {
            "id": TONE_HATER,
            "name": "Скептик",
            "description": "Мягкий скептицизм или конструктивная критика.",
        },
        {
            "id": TONE_EMOTIONAL,
            "name": "Эмоциональный",
            "description": "Личная эмоциональная реакция от первого лица.",
        },
        {
            "id": TONE_EXPERT,
            "name": "Эксперт",
            "description": "Профессиональная оценка с конкретным фактом или инсайтом.",
        },
        {
            "id": TONE_WITTY,
            "name": "Остроумный",
            "description": "Лёгкая ирония или умное наблюдение.",
        },
    ]
