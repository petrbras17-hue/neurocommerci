"""
Мониторинг новых постов в каналах.
Поллинг каналов из БД, детект новых постов, постановка в очередь.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select, update, or_
from telethon import TelegramClient
from telethon.tl.types import Message

from config import settings
from channels.channel_db import ChannelDB
from core.session_manager import SessionManager
from core.account_manager import AccountManager
from core.proxy_manager import ProxyManager
from core.task_queue import task_queue
from storage.models import Account, Channel, Post
from storage.sqlite_db import async_session
from utils.helpers import utcnow
from utils.logger import log
from utils.proxy_bindings import get_bound_proxy_config


def _normalize_phone(raw_phone: str | None) -> str:
    digits = "".join(ch for ch in str(raw_phone or "") if ch.isdigit())
    return f"+{digits}" if digits else ""


class PostQueue:
    """Очередь постов для комментирования с приоритетом (новые выше).

    Все операции защищены asyncio.Lock для безопасного доступа
    из нескольких корутин (мониторинг add + постер pop).
    """

    MAX_SEEN = 10_000  # Лимит для предотвращения утечки памяти

    def __init__(self):
        self._queue: list[dict] = []
        self._seen: dict[str, bool] = {}  # OrderedDict-like LRU через dict (Python 3.7+)
        self._lock = asyncio.Lock()

    async def add(self, post_data: dict) -> bool:
        """Добавить пост в очередь. Возвращает True если добавлен (не дубль)."""
        async with self._lock:
            key = f"{post_data['channel_id']}:{post_data['telegram_post_id']}"
            if key in self._seen:
                return False
            # LRU eviction: удаляем самые старые записи, сохраняя текущую очередь
            if len(self._seen) >= self.MAX_SEEN:
                active_keys = {
                    f"{p['channel_id']}:{p['telegram_post_id']}" for p in self._queue
                }
                # Сохраняем только ключи из активной очереди
                self._seen = {k: True for k in active_keys}
            self._seen[key] = True
            self._queue.append(post_data)
            self._queue.sort(key=lambda p: p.get("posted_at", datetime.min), reverse=True)
            return True

    async def pop(self) -> Optional[dict]:
        """Забрать следующий пост из очереди (удаляет из _seen для возможного re-add)."""
        async with self._lock:
            if self._queue:
                item = self._queue.pop(0)
                key = f"{item['channel_id']}:{item['telegram_post_id']}"
                self._seen.pop(key, None)
                return item
            return None

    def peek(self) -> Optional[dict]:
        """Посмотреть следующий пост без удаления."""
        if self._queue:
            return self._queue[0]
        return None

    @property
    def size(self) -> int:
        return len(self._queue)

    @property
    def total_seen(self) -> int:
        return len(self._seen)

    def clear(self):
        self._queue.clear()
        self._seen.clear()


class ChannelMonitor:
    """Мониторинг каналов — поллинг новых постов."""

    def __init__(
        self,
        session_manager: SessionManager,
        account_manager: AccountManager,
        proxy_manager: Optional[ProxyManager] = None,
    ):
        self.session_mgr = session_manager
        self.account_mgr = account_manager
        self.proxy_mgr = proxy_manager
        self.channel_db = ChannelDB()
        self.queue = PostQueue()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Запустить фоновый мониторинг."""
        if self._running:
            log.warning("Мониторинг уже запущен")
            return
        if settings.DISTRIBUTED_QUEUE_MODE:
            await task_queue.connect()
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        log.info("Мониторинг каналов запущен")

    async def stop(self):
        """Остановить мониторинг с гарантированной очисткой."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._task = None
        log.info("Мониторинг каналов остановлен")

    @property
    def is_running(self) -> bool:
        return self._running

    async def check_channels_once(self, user_id: int = None) -> int:
        """Проверить все каналы один раз. Возвращает кол-во новых постов."""
        channels = await self.channel_db.get_publishable(user_id=user_id)
        if not channels:
            log.debug("Нет publishable каналов для мониторинга")
            return 0

        client = await self._get_working_client(user_id=user_id)
        if not client:
            log.warning("Нет подключённого клиента для мониторинга")
            return 0

        new_posts_total = 0
        for channel in channels:
            try:
                new_posts = await self._check_channel(client, channel)
                new_posts_total += new_posts
            except Exception as exc:
                log.warning(f"Ошибка мониторинга канала {channel.title}: {exc}")

        if new_posts_total > 0:
            log.info(f"Найдено {new_posts_total} новых постов в {len(channels)} каналах")

        return new_posts_total

    async def _monitor_loop(self):
        """Основной цикл мониторинга с exponential backoff при ошибках."""
        log.info(f"Цикл мониторинга: интервал {settings.MONITOR_POLL_INTERVAL_SEC}с")
        consecutive_errors = 0
        max_backoff = 600  # 10 мин макс

        while self._running:
            try:
                new_count = await self.check_channels_once()
                if new_count:
                    if settings.DISTRIBUTED_QUEUE_MODE:
                        log.info(f"Поставлено задач комментариев: {new_count}")
                    else:
                        log.info(f"Очередь: {self.queue.size} постов ожидают комментария")
                consecutive_errors = 0  # Сброс при успехе
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                consecutive_errors += 1
                backoff = min(settings.MONITOR_POLL_INTERVAL_SEC * (2 ** consecutive_errors), max_backoff)
                log.error(f"Ошибка в цикле мониторинга ({consecutive_errors}x): {exc}. Backoff: {backoff}с")
                await asyncio.sleep(backoff)
                continue

            await asyncio.sleep(settings.MONITOR_POLL_INTERVAL_SEC)

    async def _check_channel(self, client: TelegramClient, channel: Channel) -> int:
        """Проверить канал на новые посты. Возвращает кол-во новых."""
        max_age = utcnow() - timedelta(hours=settings.POST_MAX_AGE_HOURS)
        new_count = 0

        try:
            entity = await client.get_entity(channel.telegram_id)
        except Exception as exc:
            log.debug(f"Не удалось получить entity канала {channel.title}: {exc}")
            return 0

        # Читаем последние сообщения (до 20)
        async for message in client.iter_messages(entity, limit=20):
            if not isinstance(message, Message):
                continue
            if not message.text and not message.media:
                continue

            # Фильтр по возрасту
            if message.date and message.date.replace(tzinfo=None) < max_age:
                break  # Посты идут в хронологическом порядке (от новых), значит дальше ещё старее

            # Фильтр: уже проверяли этот пост?
            if channel.last_post_checked and message.id <= channel.last_post_checked:
                break

            # Проверяем, нет ли поста в БД
            is_new = await self._is_new_post(channel.id, message.id)
            if not is_new:
                continue

            # Сохраняем пост в БД
            post_data = await self._save_post(channel, message)
            if post_data:
                added = await self._stage_post(post_data, user_id=channel.user_id)
                if added:
                    new_count += 1

        # Обновить last_post_checked
        if new_count > 0:
            await self._update_last_checked(channel)

        return new_count

    async def _is_new_post(self, channel_id: int, telegram_post_id: int) -> bool:
        """Проверить, что пост ещё не в БД."""
        async with async_session() as session:
            result = await session.execute(
                select(Post.id).where(
                    Post.channel_id == channel_id,
                    Post.telegram_post_id == telegram_post_id,
                )
            )
            return result.scalar_one_or_none() is None

    async def _save_post(self, channel: Channel, message: Message) -> Optional[dict]:
        """Сохранить пост в БД и вернуть данные для очереди."""
        text = message.text or ""
        posted_at = message.date.replace(tzinfo=None) if message.date else utcnow()
        existing_comments_count = self._extract_reply_count(message)

        try:
            async with async_session() as session:
                post = Post(
                    channel_id=channel.id,
                    telegram_post_id=message.id,
                    text=text[:4000],  # Ограничиваем длину
                    posted_at=posted_at,
                    discovered_at=utcnow(),
                )
                session.add(post)
                await session.commit()
                await session.refresh(post)

                return {
                    "post_db_id": post.id,
                    "channel_id": channel.id,
                    "channel_telegram_id": channel.telegram_id,
                    "channel_title": channel.title,
                    "channel_username": channel.username,
                    "channel_topic": channel.topic,
                    "discussion_group_id": channel.discussion_group_id,
                    "telegram_post_id": message.id,
                    "text": text[:4000],
                    "posted_at": posted_at,
                    "existing_comments_count": existing_comments_count,
                }
        except Exception as exc:
            log.warning(f"Ошибка сохранения поста {message.id} из {channel.title}: {exc}")
            return None

    @staticmethod
    def _extract_reply_count(message: Message) -> int:
        replies = getattr(message, "replies", None)
        count = getattr(replies, "replies", 0) if replies is not None else 0
        try:
            return int(count or 0)
        except Exception:
            return 0

    async def _update_last_checked(self, channel: Channel):
        """Обновить last_post_checked и last_checked_at для канала."""
        async with async_session() as session:
            # Берём самый свежий post_id из БД для канала
            result = await session.execute(
                select(Post.telegram_post_id)
                .where(Post.channel_id == channel.id)
                .order_by(Post.telegram_post_id.desc())
                .limit(1)
            )
            latest_post_id = result.scalar_one_or_none()

            if latest_post_id:
                await session.execute(
                    update(Channel)
                    .where(Channel.id == channel.id)
                    .values(
                        last_post_checked=latest_post_id,
                        last_checked_at=utcnow(),
                    )
                )
                await session.commit()

    async def _stage_post(self, post_data: dict, *, user_id: int | None = None) -> bool:
        """Stage discovered post either in local queue or distributed task queue."""
        if settings.DISTRIBUTED_QUEUE_MODE:
            payload = {
                "post_data": post_data,
                "user_id": user_id,
            }
            try:
                await task_queue.connect()
                await task_queue.enqueue("comment_tasks", payload)
                return True
            except Exception as exc:
                log.warning(
                    f"Не удалось поставить post_data в distributed queue "
                    f"(channel={post_data.get('channel_title')} post={post_data.get('telegram_post_id')}): {exc}"
                )
                return False
        return await self.queue.add(post_data)

    async def _get_working_client(self, user_id: int = None) -> Optional[TelegramClient]:
        """Получить любой подключённый Telethon клиент."""
        parser_phone = _normalize_phone(settings.PARSER_ONLY_PHONE)
        if settings.STRICT_PARSER_ONLY and parser_phone:
            client = self.session_mgr.get_client(parser_phone)
            if client and client.is_connected():
                return client
            parser_accounts = await self._monitor_candidate_accounts(phones_subset=[parser_phone])
            if not parser_accounts:
                log.warning(
                    f"Parser account {parser_phone} is not eligible for monitor client "
                    "(status/health/lifecycle/proxy/session blocker)"
                )
                return None
            return await self._connect_monitor_account(parser_accounts[0])

        connected = self.session_mgr.get_connected_phones(user_id=user_id)
        connected_accounts = await self._monitor_candidate_accounts(
            user_id=user_id,
            phones_subset=connected,
        )
        for account in connected_accounts:
            client = self.session_mgr.get_client(account.phone)
            if client and client.is_connected():
                return client

        accounts = await self._monitor_candidate_accounts(user_id=user_id)
        for account in accounts:
            client = await self._connect_monitor_account(account)
            if client:
                return client

        return None

    async def _monitor_candidate_accounts(
        self,
        *,
        user_id: int | None = None,
        phones_subset: list[str] | None = None,
    ) -> list[Account]:
        if phones_subset is not None and not phones_subset:
            return []
        async with async_session() as session:
            query = select(Account).where(
                Account.status == "active",
                Account.lifecycle_stage.in_(("warming_up", "gate_review", "active_commenting", "execution_ready")),
                or_(
                    Account.health_status.is_(None),
                    Account.health_status.notin_(list(AccountManager.BLOCKED_HEALTH_STATUSES)),
                ),
            )
            if settings.STRICT_PROXY_PER_ACCOUNT:
                query = query.where(Account.proxy_id.is_not(None))
            if user_id is not None:
                query = query.where(Account.user_id == user_id)
            if phones_subset is not None:
                query = query.where(Account.phone.in_(phones_subset))
            result = await session.execute(query.order_by(Account.last_active_at.desc(), Account.id.asc()))
            return list(result.scalars().all())

    async def _connect_monitor_account(self, account: Account) -> Optional[TelegramClient]:
        proxy = await get_bound_proxy_config(account.phone)
        if proxy is None and self.proxy_mgr and not settings.STRICT_PROXY_PER_ACCOUNT:
            proxy = self.proxy_mgr.get_for_account(account.phone) or self.proxy_mgr.assign_to_account(account.phone)
        if settings.STRICT_PROXY_PER_ACCOUNT and proxy is None:
            log.warning(f"Monitor client skipped {account.phone}: no proxy binding in strict mode")
            return None
        return await self.session_mgr.connect_client(account.phone, proxy, user_id=account.user_id)

    async def scan_old_posts(self, max_posts_per_channel: int = 50, user_id: int = None) -> int:
        """
        Режим 'старые посты': сканирует непрокомментированные посты.
        Возвращает количество добавленных постов в очередь.
        """
        channels = await self.channel_db.get_all_active(user_id=user_id)
        if not channels:
            return 0

        client = await self._get_working_client(user_id=user_id)
        if not client:
            log.warning("Нет клиента для сканирования старых постов")
            return 0

        added = 0
        for channel in channels:
            try:
                entity = await client.get_entity(channel.telegram_id)
            except Exception:
                continue

            try:
                async for message in client.iter_messages(entity, limit=max_posts_per_channel):
                    if not isinstance(message, Message):
                        continue
                    if not message.text:
                        continue

                    is_new = await self._is_new_post(channel.id, message.id)
                    if not is_new:
                        # Проверяем: сохранён но не прокомментирован?
                        async with async_session() as session:
                            result = await session.execute(
                                select(Post).where(
                                    Post.channel_id == channel.id,
                                    Post.telegram_post_id == message.id,
                                    Post.is_commented == False,
                                )
                            )
                            uncommented = result.scalar_one_or_none()
                            if not uncommented:
                                continue

                            post_data = {
                                "post_db_id": uncommented.id,
                                "channel_id": channel.id,
                                "channel_telegram_id": channel.telegram_id,
                                "channel_title": channel.title,
                                "channel_username": channel.username,
                                "channel_topic": channel.topic,
                                "discussion_group_id": channel.discussion_group_id,
                                "telegram_post_id": message.id,
                                "text": message.text[:4000],
                                "posted_at": message.date.replace(tzinfo=None) if message.date else utcnow(),
                                "existing_comments_count": self._extract_reply_count(message),
                            }
                    else:
                        post_data = await self._save_post(channel, message)
                        if not post_data:
                            continue

                    if await self._stage_post(post_data, user_id=channel.user_id):
                        added += 1

            except Exception as exc:
                log.debug(f"Ошибка сканирования старых постов {channel.title}: {exc}")

        if added > 0:
            log.info(f"Режим старых постов: {added} постов добавлено в очередь")
        return added

    def get_stats(self) -> dict:
        """Статистика мониторинга."""
        return {
            "running": self._running,
            "queue_size": 0 if settings.DISTRIBUTED_QUEUE_MODE else self.queue.size,
            "total_seen": self.queue.total_seen,
            "distributed_mode": settings.DISTRIBUTED_QUEUE_MODE,
        }
