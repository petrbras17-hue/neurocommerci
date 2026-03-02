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
import os
import random
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
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
from core.account_manager import AccountManager
from core.session_manager import SessionManager
from core.rate_limiter import RateLimiter
from storage.models import Comment, Post, Channel
from storage.sqlite_db import async_session
from utils.anti_ban import AntibanManager
from utils.passive_actions import PassiveActionsManager
from utils.notifier import notifier
from utils.logger import log


# Эмодзи для первичной отправки (emoji→link swap)
SWAP_EMOJIS = ["👀", "🔥", "💯", "👍", "😄", "✨", "🤔", "📌", "⚡", "💪"]

# Задержка перед заменой эмодзи на текст (секунды)
EMOJI_SWAP_DELAY_SEC = 60


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
        self.antiban = AntibanManager()
        self.passive = PassiveActionsManager(session_manager)
        self._running = False
        self._emoji_swap_enabled = True
        self._stats = {"sent": 0, "failed": 0, "skipped": 0, "swapped": 0}
        self._pending_swaps: list[asyncio.Task] = []

    @property
    def emoji_swap_enabled(self) -> bool:
        return self._emoji_swap_enabled

    @emoji_swap_enabled.setter
    def emoji_swap_enabled(self, value: bool):
        self._emoji_swap_enabled = value

    async def process_queue(self) -> int:
        """
        Обработать один пост из очереди.
        Возвращает: 1 = отправлен, 0 = очередь пуста или пропущен, -1 = ошибка.
        """
        post_data = self.monitor.queue.pop()
        if not post_data:
            return 0

        # Проверка релевантности
        analysis = self.analyzer.analyze(
            post_data.get("text", ""),
            post_data.get("channel_topic"),
        )
        if not analysis["should_comment"]:
            self._stats["skipped"] += 1
            log.debug(f"Пост пропущен (скор {analysis['score']}): {post_data.get('channel_title')}")
            return 0

        # Получить аккаунт
        account = await self.account_mgr.get_next_available()
        if not account:
            self.monitor.queue.add(post_data)
            log.warning("Нет доступных аккаунтов, пост возвращён в очередь")
            return 0

        # Нужен ли отдых?
        if self.rate_limiter.needs_rest(account.phone):
            rest_time = self.antiban.get_rest_duration()
            log.info(f"{account.phone}: отдых {rest_time}с после серии комментариев")
            self.rate_limiter.set_cooldown(account.phone, rest_time)
            self.rate_limiter.reset_session(account.phone)
            self.monitor.queue.add(post_data)
            return 0

        # Пассивное действие перед комментарием (25% шанс)
        if self.antiban.should_do_passive_action():
            channel_tid = post_data.get("channel_telegram_id")
            post_tid = post_data.get("telegram_post_id")
            if channel_tid and post_tid:
                action = await self.passive.do_random_passive_action(
                    account.phone, channel_tid, post_tid,
                )
                if action != "none":
                    log.debug(f"{account.phone}: пассивное действие '{action}' перед комментарием")
                    await asyncio.sleep(random.uniform(2.0, 5.0))

        # Сгенерировать комментарий
        comment_data = await self.generator.generate(
            post_text=post_data.get("text", ""),
            persona_style=account.persona_style or "casual",
        )

        # Human-like задержка перед отправкой
        delay = self.antiban.get_typing_delay(len(comment_data["text"]))
        log.debug(f"Задержка перед отправкой: {delay:.1f}с")
        await asyncio.sleep(delay)

        # Решаем: emoji swap или прямая отправка
        use_swap = (
            self._emoji_swap_enabled
            and comment_data["scenario"] == "B"
            and random.random() < 0.6  # 60% сценариев B используют swap
        )

        if use_swap:
            success = await self._send_emoji_swap(
                account_phone=account.phone,
                post_data=post_data,
                comment_text=comment_data["text"],
                scenario=comment_data["scenario"],
            )
        else:
            success = await self._send_comment(
                account_phone=account.phone,
                post_data=post_data,
                comment_text=comment_data["text"],
                scenario=comment_data["scenario"],
            )

        if success:
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
            return 1
        else:
            self._stats["failed"] += 1
            return -1

    async def _send_comment(
        self,
        account_phone: str,
        post_data: dict,
        comment_text: str,
        scenario: str,
    ) -> bool:
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
            return True

        client = self.session_mgr.get_client(account_phone)
        if not client or not client.is_connected():
            log.warning(f"{account_phone}: клиент не подключён")
            return False

        discussion_group_id = post_data.get("discussion_group_id")
        if not discussion_group_id:
            log.debug(f"У канала {post_data.get('channel_title')} нет группы обсуждений")
            return False

        try:
            discussion_group = await client.get_entity(discussion_group_id)
            telegram_post_id = post_data.get("telegram_post_id")

            await client.send_message(
                discussion_group,
                comment_text,
                comment_to=telegram_post_id,
            )

            await self._save_comment(
                account_phone=account_phone,
                post_db_id=post_data.get("post_db_id"),
                text=comment_text,
                scenario=scenario,
                status="sent",
            )
            return True

        except FloodWaitError as e:
            log.warning(f"{account_phone}: FloodWait {e.seconds}с")
            await self.account_mgr.handle_error(account_phone, "flood_wait", str(e.seconds))
            await notifier.error_occurred(account_phone, "FloodWait", f"{e.seconds}с")
            self.monitor.queue.add(post_data)
            return False

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
            return False

        except ChatWriteForbiddenError:
            log.warning(f"Запись запрещена в {post_data.get('channel_title')}")
            await self._disable_channel_comments(post_data.get("channel_id"))
            return False

        except ChannelPrivateError:
            log.warning(f"Канал {post_data.get('channel_title')} стал приватным")
            return False

        except MsgIdInvalidError:
            log.debug(f"Невалидный ID поста в {post_data.get('channel_title')}")
            return False

        except Exception as exc:
            log.error(f"Ошибка отправки комментария: {exc}")
            await self.account_mgr.handle_error(account_phone, "unknown", str(exc))
            return False

    async def _send_emoji_swap(
        self,
        account_phone: str,
        post_data: dict,
        comment_text: str,
        scenario: str,
    ) -> bool:
        """
        Emoji→Link Swap: отправить эмодзи, через 60 сек заменить на текст.
        Обходит первичный спам-фильтр Telegram.
        """
        client = self.session_mgr.get_client(account_phone)
        if not client or not client.is_connected():
            return False

        discussion_group_id = post_data.get("discussion_group_id")
        if not discussion_group_id:
            return False

        try:
            discussion_group = await client.get_entity(discussion_group_id)
            telegram_post_id = post_data.get("telegram_post_id")
            emoji = random.choice(SWAP_EMOJIS)

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

            # Предварительно сохраняем как "pending"
            await self._save_comment(
                account_phone=account_phone,
                post_db_id=post_data.get("post_db_id"),
                text=comment_text,
                scenario=scenario,
                status="sent",
            )

            self._stats["swapped"] += 1
            return True

        except FloodWaitError as e:
            log.warning(f"{account_phone}: FloodWait {e.seconds}с (swap)")
            await self.account_mgr.handle_error(account_phone, "flood_wait", str(e.seconds))
            self.monitor.queue.add(post_data)
            return False

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
        scenario: str,
    ):
        """Фоновая задача: подождать и заменить эмодзи на текст."""
        try:
            await asyncio.sleep(EMOJI_SWAP_DELAY_SEC)

            if not client.is_connected():
                log.warning(f"{account_phone}: клиент отключился, swap отменён")
                return

            await client.edit_message(
                discussion_group,
                message_id,
                new_text,
            )
            log.info(f"{account_phone}: emoji→text swap выполнен (msg_id={message_id})")

        except MessageNotModifiedError:
            log.debug(f"{account_phone}: сообщение уже изменено")

        except Exception as exc:
            log.warning(f"{account_phone}: ошибка swap: {exc}")

    async def _save_comment(
        self,
        account_phone: str,
        post_db_id: Optional[int],
        text: str,
        scenario: str,
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

                comment = Comment(
                    account_id=account_id,
                    post_id=post_db_id or 0,
                    text=text,
                    scenario=scenario,
                    status=status,
                    error_message=error or None,
                    created_at=datetime.utcnow(),
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

    def get_stats(self) -> dict:
        return {
            **self._stats,
            "emoji_swap_enabled": self._emoji_swap_enabled,
            "pending_swaps": len([t for t in self._pending_swaps if not t.done()]),
            "passive_actions": self.passive.get_stats(),
            "generator": self.generator.get_stats(),
            "queue_size": self.monitor.queue.size,
        }
