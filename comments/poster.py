"""
Отправка комментариев в Telegram через Telethon.
Комментарии идут в группу обсуждений канала (discussion group).

Поддерживает:
- Прямую отправку комментариев
- Emoji→Link Swap: отправка эмодзи, замена на текст через 60 сек
- Пассивные действия (просмотры, реакции) для естественности
- Уведомления в Telegram о событиях
"""

from __future__ import annotations

import asyncio
import html
import os
import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from utils.cache import SettingsCache
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    ChannelPrivateError,
    MsgIdInvalidError,
    MessageNotModifiedError,
)

from config import settings
from channels.analyzer import PostAnalyzer
from channels.monitor import ChannelMonitor
from comments.generator import CommentGenerator
from comments.scenarios import Scenario
from core.account_manager import AccountManager
from core.ai_orchestrator import AIOrchestrator
from core.session_manager import SessionManager
from core.rate_limiter import RateLimiter
from core.policy_engine import policy_engine
from storage.models import Comment, Post, Channel
from storage.sqlite_db import async_session
from utils.anti_ban import AntibanManager
from utils.helpers import utcnow
from utils.passive_actions import PassiveActionsManager
from utils.notifier import notifier
from utils.logger import log


@dataclass
class ProcessOutcome:
    code: str
    reason: str = ""


# Эмодзи для первичной отправки (emoji→link swap)
SWAP_EMOJIS = ["👀", "🔥", "💯", "👍", "😄", "✨", "🤔", "📌", "⚡", "💪"]

# Задержка перед заменой эмодзи на текст (секунды)
EMOJI_SWAP_DELAY_SEC = 60

# Скрытая ссылка: @BotUsername → синий кликабельный текст в Telegram
# Слова-замены по категории продукта
_HIDDEN_LINK_WORDS = {
    "VPN": ["один VPN-бот", "один сервис", "один VPN", "один бот в тг", "один ВПН", "этот сервис", "этим ботом"],
    "AI": ["один AI-бот", "один сервис", "одна нейросеть", "этот бот", "этим сервисом", "один инструмент"],
    "Bot": ["один бот", "один сервис", "один инструмент", "этот бот", "этим ботом", "одну штуку"],
    "Service": ["один сервис", "одну штуку", "один инструмент", "этот сервис", "этим ботом", "одну находку"],
}


# ── Кэш product-зависимых значений (пересчитывается при изменении settings) ──


class _ProductCacheData:
    """Все product-зависимые значения в одном объекте."""
    __slots__ = ("hidden_link_words", "mention_re", "bot_mention_lower")

    def __init__(self):
        self.hidden_link_words = _HIDDEN_LINK_WORDS.get(
            settings.PRODUCT_CATEGORY, _HIDDEN_LINK_WORDS["Service"]
        )
        self.mention_re = re.compile(
            re.escape(settings.product_bot_mention), re.IGNORECASE
        )
        self.bot_mention_lower = settings.product_bot_mention.lower()


_product_cache = SettingsCache(
    key_fn=lambda: f"{settings.PRODUCT_CATEGORY}|{settings.product_bot_mention}|{settings.PRODUCT_BOT_LINK}",
    build_fn=_ProductCacheData,
)


# Public API
def get_active_hidden_link_words() -> list[str]:
    return _product_cache.get().hidden_link_words

def get_mention_re() -> re.Pattern:
    return _product_cache.get().mention_re

def get_bot_mention_lower() -> str:
    return _product_cache.get().bot_mention_lower


def _apply_hidden_link(text: str) -> str:
    """
    Заменить @BotUsername на скрытую HTML-ссылку (синий текст в Telegram).
    Сначала экранируем HTML-символы в тексте, затем вставляем <a href>.
    """
    safe_text = html.escape(text)
    word = random.choice(get_active_hidden_link_words())
    link_html = f'<a href="{settings.PRODUCT_BOT_LINK}">{word}</a>'
    return get_mention_re().sub(link_html, safe_text)


class CommentPoster:
    """Отправка AI-сгенерированных комментариев."""

    def __init__(
        self,
        account_manager: AccountManager,
        session_manager: SessionManager,
        rate_limiter: RateLimiter,
        generator: CommentGenerator,
        monitor: ChannelMonitor,
    ):
        self.account_mgr = account_manager
        self.session_mgr = session_manager
        self.rate_limiter = rate_limiter
        self.generator = generator
        self.monitor = monitor
        self.analyzer = PostAnalyzer()
        self.orchestrator = AIOrchestrator()  # Gemini decision/review layer
        self.antiban = AntibanManager()
        self.passive = PassiveActionsManager(session_manager)
        self._running = False
        self._emoji_swap_enabled = bool(settings.ENABLE_EMOJI_SWAP and not settings.HUMAN_GATED_COMMENTS)
        self._stats = {"sent": 0, "failed": 0, "skipped": 0, "swapped": 0}
        self._pending_swaps: list[asyncio.Task] = []

    @property
    def emoji_swap_enabled(self) -> bool:
        return self._emoji_swap_enabled

    @emoji_swap_enabled.setter
    def emoji_swap_enabled(self, value: bool):
        self._emoji_swap_enabled = value

    async def process_queue(self, account_subset: Optional[list[str]] = None) -> int:
        """
        Обработать один пост из очереди.
        account_subset: optional список телефонов, из которых разрешён выбор отправителя.
        Возвращает: 1 = отправлен, 0 = очередь пуста или пропущен, -1 = ошибка.
        """
        post_data = await self.monitor.queue.pop()
        if not post_data:
            return 0
        outcome = await self.process_post(post_data, account_subset=account_subset)
        if outcome.code == "retry":
            await self.monitor.queue.add(post_data)
            return 0
        if outcome.code == "sent":
            return 1
        if outcome.code == "failed":
            return -1
        return 0

    async def process_post(
        self,
        post_data: dict,
        account_subset: Optional[list[str]] = None,
    ) -> ProcessOutcome:
        """Stateless processing path for a single post payload."""
        if not isinstance(post_data, dict):
            return ProcessOutcome("failed", "invalid_post_data")

        scenario_override = None
        persona_override = None
        approved_comment_text = str(post_data.get("approved_comment_text") or "").strip()
        approved_scenario_raw = post_data.get("approved_comment_scenario") or Scenario.A
        approved_scenario = Scenario.B if str(getattr(approved_scenario_raw, "value", approved_scenario_raw)) == "B" else Scenario.A
        approved_persona = str(post_data.get("approved_comment_persona") or "casual")

        if settings.HUMAN_GATED_COMMENTS and not approved_comment_text:
            self._stats["skipped"] += 1
            log.debug("Human-gated commenting: пропуск без подтверждённого текста комментария")
            return ProcessOutcome("skip", "human_approval_required")

        if not settings.HUMAN_GATED_COMMENTS and self.orchestrator.is_available:
            ai_analysis = await self.orchestrator.analyze_post(
                post_text=post_data.get("text", ""),
                channel_title=post_data.get("channel_title", ""),
                channel_topic=post_data.get("channel_topic", ""),
            )
            if ai_analysis:
                if not ai_analysis.get("should_comment"):
                    self._stats["skipped"] += 1
                    log.debug(f"AI decision layer: пропустить пост ({ai_analysis.get('reason', '')})")
                    return ProcessOutcome("skip", "ai_should_not_comment")
                scenario_override = ai_analysis.get("scenario")
                persona_override = ai_analysis.get("persona_style")

        # Fallback: keyword-based анализ
        if not settings.HUMAN_GATED_COMMENTS and not scenario_override:
            analysis = self.analyzer.analyze(
                post_data.get("text", ""),
                post_data.get("channel_topic"),
            )
            if not analysis["should_comment"]:
                self._stats["skipped"] += 1
                log.debug(f"Пост пропущен (скор {analysis['score']}): {post_data.get('channel_title')}")
                return ProcessOutcome("skip", "keyword_should_not_comment")

        # Получить аккаунт
        account = await self.account_mgr.get_next_available(phones_subset=account_subset)
        if not account:
            log.warning("Нет доступных аккаунтов, пост возвращён в очередь")
            return ProcessOutcome("retry", "no_available_account")

        min_existing_comments = max(0, int(settings.MIN_EXISTING_COMMENTS_BEFORE_COMMENT))
        if min_existing_comments > 0:
            client = self.session_mgr.get_client(account.phone)
            current_comments = await self._get_current_comments_count(client, post_data)
            if current_comments < min_existing_comments:
                attempts = int(post_data.get("_min_comments_attempts", 0)) + 1
                max_attempts = max(1, int(settings.MIN_COMMENTS_RECHECK_MAX_ATTEMPTS))
                if attempts <= max_attempts:
                    post_data["_min_comments_attempts"] = attempts
                    log.debug(
                        f"Пост {post_data.get('telegram_post_id')} в {post_data.get('channel_title')}: "
                        f"комментариев {current_comments} < {min_existing_comments}, "
                        f"requeue {attempts}/{max_attempts}"
                    )
                else:
                    log.info(
                        f"Пост {post_data.get('telegram_post_id')} в {post_data.get('channel_title')} "
                        f"пропущен: за {max_attempts} проверок не набрал минимум "
                        f"{min_existing_comments} комментария(ев)"
                    )
                self._stats["skipped"] += 1
                if attempts <= max_attempts:
                    return ProcessOutcome("retry", "min_existing_comments")
                return ProcessOutcome("skip", "min_existing_comments_timeout")

        decision = await policy_engine.check(
            "comment_send_attempt",
            {
                "account": {
                    "phone": account.phone,
                    "lifecycle_stage": account.lifecycle_stage,
                    "status": account.status,
                    "risk_level": account.risk_level,
                },
            },
            phone=account.phone,
        )
        if decision.action in {"block", "quarantine"}:
            log.warning(
                f"{account.phone}: policy blocked comment send "
                f"({decision.rule_id}, action={decision.action})"
            )
            return ProcessOutcome("retry", f"policy_{decision.rule_id}")

        # Проверка rate limiter: можно ли отправлять комментарий
        if not self.rate_limiter.can_comment(account.phone, account.days_active or 0):
            log.debug(f"{account.phone}: rate limiter запретил комментарий, пост возвращён")
            return ProcessOutcome("retry", "rate_limiter")

        # Нужен ли отдых?
        if self.rate_limiter.needs_rest(account.phone):
            rest_time = self.antiban.get_rest_duration()
            log.info(f"{account.phone}: отдых {rest_time}с после серии комментариев")
            self.rate_limiter.set_cooldown(account.phone, rest_time)
            self.rate_limiter.reset_session(account.phone)
            return ProcessOutcome("retry", "rest_required")

        # Пассивное действие перед комментарием (25% шанс)
        if not settings.HUMAN_GATED_COMMENTS and self.antiban.should_do_passive_action():
            channel_tid = post_data.get("channel_telegram_id")
            post_tid = post_data.get("telegram_post_id")
            if channel_tid and post_tid:
                action = await self.passive.do_random_passive_action(
                    account.phone, channel_tid, post_tid,
                )
                if action != "none":
                    log.debug(f"{account.phone}: пассивное действие '{action}' перед комментарием")
                    await asyncio.sleep(random.uniform(2.0, 5.0))

        # Gemini генерирует комментарий, AI decision layer может подсказать сценарий/персону.
        if settings.HUMAN_GATED_COMMENTS:
            comment_data = {
                "text": approved_comment_text,
                "scenario": approved_scenario,
                "persona": approved_persona,
                "source": "approved",
            }
        else:
            comment_data = await self.generator.generate(
                post_text=post_data.get("text", ""),
                scenario=scenario_override,
                persona_style=persona_override or account.persona_style or "casual",
            )

        # Gemini review/rewrite layer
        if not settings.HUMAN_GATED_COMMENTS and self.orchestrator.is_available and comment_data["source"] == "ai":
            review = await self.orchestrator.review_comment(
                comment=comment_data["text"],
                post_text=post_data.get("text", ""),
                scenario=comment_data["scenario"],
            )
            if review:
                if not review.get("approved"):
                    if review.get("improved"):
                        log.info(f"AI layer улучшил комментарий: {review['improved'][:80]}")
                        comment_data["text"] = review["improved"]
                        comment_data["source"] = "ai_improved"
                    else:
                        log.debug("AI layer отклонил комментарий, используем fallback")
                        comment_data["text"] = self.generator.get_fallback(comment_data["scenario"])
                        comment_data["source"] = "fallback"

        # Решаем: emoji swap или прямая отправка
        use_swap = (
            self._emoji_swap_enabled
            and comment_data["scenario"] == Scenario.B
            and random.random() < 0.6  # 60% сценариев B используют swap
        )

        if use_swap:
            send_status = await self._send_emoji_swap(
                account_phone=account.phone,
                post_data=post_data,
                comment_text=comment_data["text"],
                scenario=comment_data["scenario"],
            )
        else:
            send_status = await self._send_comment(
                account_phone=account.phone,
                post_data=post_data,
                comment_text=comment_data["text"],
                scenario=comment_data["scenario"],
            )

        if send_status == "sent":
            self._stats["sent"] += 1
            await self.account_mgr.record_comment(account.phone)

            # Уведомление
            await notifier.comment_sent(
                account.phone,
                post_data.get("channel_title", ""),
                comment_data["text"],
                comment_data["scenario"],
            )

            next_delay = self.rate_limiter.get_next_delay()
            log.info(
                f"[{comment_data['scenario']}] {account.phone} → {post_data.get('channel_title')}: "
                f"{comment_data['text'][:80]}... | Следующий через {int(next_delay)}с"
            )
            return ProcessOutcome("sent")
        if send_status == "retry":
            return ProcessOutcome("retry", "send_retry")
        else:
            self._stats["failed"] += 1
            return ProcessOutcome("failed", "send_failed")

    async def _get_current_comments_count(self, client, post_data: dict) -> int:
        """Fetch current replies count for channel post thread."""
        cached = int(post_data.get("existing_comments_count") or 0)
        if not client or not client.is_connected():
            return cached

        channel_tid = post_data.get("channel_telegram_id")
        telegram_post_id = post_data.get("telegram_post_id")
        if not channel_tid or not telegram_post_id:
            return cached

        try:
            channel_entity = await client.get_entity(channel_tid)
            msg = await client.get_messages(channel_entity, ids=telegram_post_id)
            if not msg:
                return cached
            replies = getattr(msg, "replies", None)
            current = int(getattr(replies, "replies", 0) or 0)
            post_data["existing_comments_count"] = current
            return current
        except Exception as exc:
            log.debug(
                f"{post_data.get('channel_title')}: не удалось обновить счётчик комментариев: {exc}"
            )
            return cached

    async def _send_comment(
        self,
        account_phone: str,
        post_data: dict,
        comment_text: str,
        scenario: Scenario,
    ) -> str:
        """Отправить комментарий через Telethon (прямая отправка)."""
        # Dry-run: логируем, но не отправляем
        if os.environ.get("NEURO_DRY_RUN") == "1":
            log.info(f"[DRY-RUN] [{scenario}] {account_phone} → {post_data.get('channel_title')}: {comment_text[:80]}...")
            await self._save_comment(
                account_phone=account_phone,
                post_db_id=post_data.get("post_db_id"),
                text=comment_text,
                scenario=scenario,
                status="dry_run",
            )
            return "sent"

        client = self.session_mgr.get_client(account_phone)
        if not client or not client.is_connected():
            log.warning(f"{account_phone}: клиент не подключён")
            return "retry"

        discussion_group_id = post_data.get("discussion_group_id")
        if not discussion_group_id:
            log.debug(f"У канала {post_data.get('channel_title')} нет группы обсуждений")
            return "failed"

        try:
            discussion_group = await client.get_entity(discussion_group_id)
            telegram_post_id = post_data.get("telegram_post_id")

            # Сценарий B: скрытая ссылка (синий текст в Telegram)
            if (
                not settings.HUMAN_GATED_COMMENTS
                and scenario == Scenario.B
                and get_bot_mention_lower() in comment_text.lower()
            ):
                send_text = _apply_hidden_link(comment_text)
                parse_mode = "html"
            else:
                send_text = comment_text
                parse_mode = None

            # Имитация набора текста (SetTypingRequest)
            if not settings.HUMAN_GATED_COMMENTS:
                await self.antiban.send_typing(client, discussion_group, len(send_text))

            await client.send_message(
                discussion_group,
                send_text,
                comment_to=telegram_post_id,
                parse_mode=parse_mode,
            )

            await self._save_comment(
                account_phone=account_phone,
                post_db_id=post_data.get("post_db_id"),
                text=comment_text,
                scenario=scenario,
                status="sent",
            )
            return "sent"

        except FloodWaitError as e:
            log.warning(f"{account_phone}: FloodWait {e.seconds}с")
            await policy_engine.check(
                "floodwait_detected",
                {"seconds": int(e.seconds)},
                phone=account_phone,
            )
            await self.account_mgr.handle_error(account_phone, "flood_wait", str(e.seconds))
            await notifier.error_occurred(account_phone, "FloodWait", f"{e.seconds}с")
            return "retry"

        except UserBannedInChannelError:
            log.warning(f"{account_phone}: забанен в канале {post_data.get('channel_title')}")
            await notifier.error_occurred(account_phone, "BannedInChannel", post_data.get("channel_title", ""))
            await self._save_comment(
                account_phone=account_phone,
                post_db_id=post_data.get("post_db_id"),
                text=comment_text,
                scenario=scenario,
                status="failed",
                error="UserBannedInChannel",
            )
            return "failed"

        except ChatWriteForbiddenError:
            log.warning(f"Запись запрещена в {post_data.get('channel_title')}")
            await self._disable_channel_comments(post_data.get("channel_id"))
            return "failed"

        except ChannelPrivateError:
            log.warning(f"Канал {post_data.get('channel_title')} стал приватным")
            return "failed"

        except MsgIdInvalidError:
            log.debug(f"Невалидный ID поста в {post_data.get('channel_title')}")
            return "failed"

        except Exception as exc:
            log.error(f"Ошибка отправки комментария: {exc}")
            await self.account_mgr.handle_error(account_phone, "unknown", str(exc))
            return "retry"

    async def _send_emoji_swap(
        self,
        account_phone: str,
        post_data: dict,
        comment_text: str,
        scenario: Scenario,
    ) -> str:
        """
        Emoji→Link Swap: отправить эмодзи, через 60 сек заменить на текст.
        Обходит первичный спам-фильтр Telegram.
        """
        if os.environ.get("NEURO_DRY_RUN") == "1":
            log.info(f"[DRY-RUN] [SWAP] [{scenario}] {account_phone} → {post_data.get('channel_title')}: {comment_text[:80]}...")
            return "sent"

        client = self.session_mgr.get_client(account_phone)
        if not client or not client.is_connected():
            return "retry"

        discussion_group_id = post_data.get("discussion_group_id")
        if not discussion_group_id:
            return "failed"

        try:
            discussion_group = await client.get_entity(discussion_group_id)
            telegram_post_id = post_data.get("telegram_post_id")
            emoji = random.choice(SWAP_EMOJIS)

            # Имитация набора перед эмодзи
            await self.antiban.send_typing(client, discussion_group, len(emoji))

            # Шаг 1: отправляем эмодзи
            sent_message = await client.send_message(
                discussion_group,
                emoji,
                comment_to=telegram_post_id,
            )

            log.debug(f"{account_phone}: отправлен эмодзи {emoji}, swap через {EMOJI_SWAP_DELAY_SEC}с")

            # Шаг 2: планируем замену через 60 секунд
            swap_task = asyncio.create_task(
                self._do_swap(
                    client=client,
                    discussion_group=discussion_group,
                    message_id=sent_message.id,
                    new_text=comment_text,
                    account_phone=account_phone,
                    post_db_id=post_data.get("post_db_id"),
                    scenario=scenario,
                )
            )
            # Очистить завершённые swap-задачи перед добавлением новой
            self._pending_swaps = [t for t in self._pending_swaps if not t.done()]
            self._pending_swaps.append(swap_task)

            # Предварительно сохраняем как pending_swap (обновится на sent в _do_swap)
            await self._save_comment(
                account_phone=account_phone,
                post_db_id=post_data.get("post_db_id"),
                text=comment_text,
                scenario=scenario,
                status="pending_swap",
            )

            self._stats["swapped"] += 1
            return "sent"

        except FloodWaitError as e:
            log.warning(f"{account_phone}: FloodWait {e.seconds}с (swap)")
            await policy_engine.check(
                "floodwait_detected",
                {"seconds": int(e.seconds)},
                phone=account_phone,
            )
            await self.account_mgr.handle_error(account_phone, "flood_wait", str(e.seconds))
            return "retry"

        except Exception as exc:
            log.error(f"Ошибка emoji swap: {exc}")
            # Фоллбэк: прямая отправка
            return await self._send_comment(account_phone, post_data, comment_text, scenario)

    async def _do_swap(
        self,
        client: TelegramClient,
        discussion_group,
        message_id: int,
        new_text: str,
        account_phone: str,
        post_db_id: Optional[int],
        scenario: Scenario,
    ):
        """Фоновая задача: подождать и заменить эмодзи на текст."""
        try:
            await asyncio.sleep(EMOJI_SWAP_DELAY_SEC)

            # Получить свежий client (за 60с мог переподключиться)
            fresh_client = self.session_mgr.get_client(account_phone)
            if fresh_client and fresh_client.is_connected():
                client = fresh_client
            elif client.is_connected():
                pass  # Исходный client всё ещё подключён
            else:
                log.warning(f"{account_phone}: клиент отключился, swap отменён")
                await self._update_comment_status(post_db_id, "swap_failed")
                return

            # Сценарий B: скрытая ссылка (синий текст в Telegram)
            if (
                not settings.HUMAN_GATED_COMMENTS
                and scenario == Scenario.B
                and get_bot_mention_lower() in new_text.lower()
            ):
                send_text = _apply_hidden_link(new_text)
                parse_mode = "html"
            else:
                send_text = new_text
                parse_mode = None

            await client.edit_message(
                discussion_group,
                message_id,
                send_text,
                parse_mode=parse_mode,
            )
            log.info(f"{account_phone}: emoji→text swap выполнен (msg_id={message_id})")
            # Обновить статус на "sent" после успешного свопа
            await self._update_comment_status(post_db_id, "sent")

        except MessageNotModifiedError:
            log.debug(f"{account_phone}: сообщение уже изменено")
            await self._update_comment_status(post_db_id, "sent")

        except Exception as exc:
            log.warning(f"{account_phone}: ошибка swap: {exc}")
            await self._update_comment_status(post_db_id, "swap_failed")

    async def _save_comment(
        self,
        account_phone: str,
        post_db_id: Optional[int],
        text: str,
        scenario: Scenario,
        status: str,
        error: str = "",
    ):
        """Сохранить комментарий в БД."""
        try:
            async with async_session() as session:
                from storage.models import Account
                result = await session.execute(
                    select(Account.id).where(Account.phone == account_phone)
                )
                account_id = result.scalar_one_or_none()
                if not account_id:
                    return

                if not post_db_id:
                    log.debug("post_db_id отсутствует, комментарий не сохранён в БД")
                    return

                comment = Comment(
                    account_id=account_id,
                    post_id=post_db_id,
                    text=text,
                    scenario=scenario,
                    status=status,
                    error_message=error or None,
                    created_at=utcnow(),
                )
                session.add(comment)

                if post_db_id and status == "sent":
                    await session.execute(
                        update(Post)
                        .where(Post.id == post_db_id)
                        .values(is_commented=True)
                    )

                await session.commit()
        except Exception as exc:
            log.warning(f"Ошибка сохранения комментария в БД: {exc}")

    async def _update_comment_status(self, post_db_id: Optional[int], status: str):
        """Обновить статус комментария (для swap: pending_swap → sent/swap_failed)."""
        if not post_db_id:
            return
        try:
            async with async_session() as session:
                await session.execute(
                    update(Comment)
                    .where(Comment.post_id == post_db_id, Comment.status == "pending_swap")
                    .values(status=status)
                )
                await session.commit()
        except Exception as exc:
            log.warning(f"Ошибка обновления статуса комментария: {exc}")

    async def _disable_channel_comments(self, channel_id: Optional[int]):
        """Пометить канал как без комментариев."""
        if not channel_id:
            return
        try:
            async with async_session() as session:
                await session.execute(
                    update(Channel)
                    .where(Channel.id == channel_id)
                    .values(comments_enabled=False)
                )
                await session.commit()
        except Exception as exc:
            log.warning(f"Ошибка обновления канала: {exc}")

    async def shutdown(self):
        """Graceful shutdown: дождаться завершения всех pending swap задач."""
        self._running = False
        pending = [t for t in self._pending_swaps if not t.done()]
        if pending:
            log.info(f"Ожидание завершения {len(pending)} swap задач...")
            results = await asyncio.gather(*pending, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    log.warning(f"Swap задача {i} завершилась с ошибкой: {result}")
            log.info("Все swap задачи завершены")
        self._pending_swaps.clear()

    def get_stats(self) -> dict:
        queue_size = 0
        if getattr(self.monitor, "queue", None) is not None:
            queue_size = self.monitor.queue.size
        return {
            **self._stats,
            "emoji_swap_enabled": self._emoji_swap_enabled,
            "pending_swaps": len([t for t in self._pending_swaps if not t.done()]),
            "passive_actions": self.passive.get_stats() if not settings.HUMAN_GATED_COMMENTS else {"enabled": False},
            "generator": self.generator.get_stats(),
            "orchestrator": self.orchestrator.get_stats(),
            "queue_size": queue_size,
        }
