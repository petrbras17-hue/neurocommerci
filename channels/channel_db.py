"""
CRUD-операции для таблицы каналов.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from utils.helpers import utcnow

from storage.models import Channel
from storage.sqlite_db import async_session


class ChannelDB:
    """Работа с каналами в SQLite."""

    async def add_channel(self, channel_info: Any) -> Channel:
        """Добавить канал в БД или обновить существующий по telegram_id."""
        payload = self._to_payload(channel_info)

        async with async_session() as session:
            result = await session.execute(
                select(Channel).where(Channel.telegram_id == payload["telegram_id"])
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.username = payload["username"]
                existing.title = payload["title"]
                existing.subscribers = payload["subscribers"]
                existing.topic = payload["topic"]
                existing.comments_enabled = payload["comments_enabled"]
                existing.discussion_group_id = payload["discussion_group_id"]
                existing.is_active = True
                existing.last_checked_at = utcnow()
                channel = existing
            else:
                channel = Channel(
                    telegram_id=payload["telegram_id"],
                    username=payload["username"],
                    title=payload["title"],
                    subscribers=payload["subscribers"],
                    topic=payload["topic"],
                    comments_enabled=payload["comments_enabled"],
                    discussion_group_id=payload["discussion_group_id"],
                    is_active=True,
                    is_blacklisted=False,
                    last_checked_at=utcnow(),
                )
                session.add(channel)

            await session.commit()
            await session.refresh(channel)
            return channel

    async def get_all_active(self) -> list[Channel]:
        """Получить активные каналы (не в чёрном списке)."""
        async with async_session() as session:
            result = await session.execute(
                select(Channel)
                .where(Channel.is_active.is_(True), Channel.is_blacklisted.is_(False))
                .order_by(Channel.subscribers.desc())
            )
            return list(result.scalars().all())

    async def get_all(self) -> list[Channel]:
        """Получить все каналы."""
        async with async_session() as session:
            result = await session.execute(select(Channel).order_by(Channel.created_at.desc()))
            return list(result.scalars().all())

    async def get_by_topic(self, topic: str) -> list[Channel]:
        """Получить активные каналы по тематике."""
        async with async_session() as session:
            result = await session.execute(
                select(Channel)
                .where(
                    Channel.is_active.is_(True),
                    Channel.is_blacklisted.is_(False),
                    func.lower(Channel.topic) == topic.lower(),
                )
                .order_by(Channel.subscribers.desc())
            )
            return list(result.scalars().all())

    async def blacklist_channel(self, channel_id: int):
        """Добавить канал в чёрный список по telegram_id."""
        async with async_session() as session:
            await session.execute(
                update(Channel)
                .where(Channel.telegram_id == channel_id)
                .values(is_blacklisted=True, is_active=False, last_checked_at=utcnow())
            )
            await session.commit()

    async def blacklist_by_db_id(self, db_id: int):
        """Добавить канал в чёрный список по локальному DB id."""
        async with async_session() as session:
            await session.execute(
                update(Channel)
                .where(Channel.id == db_id)
                .values(is_blacklisted=True, is_active=False, last_checked_at=utcnow())
            )
            await session.commit()

    async def update_last_checked(self, channel_id: int):
        """Обновить время последней проверки канала по telegram_id."""
        async with async_session() as session:
            await session.execute(
                update(Channel)
                .where(Channel.telegram_id == channel_id)
                .values(last_checked_at=utcnow())
            )
            await session.commit()

    async def get_stats(self) -> dict:
        """Сводная статистика каналов."""
        async with async_session() as session:
            total = await session.scalar(select(func.count(Channel.id)))
            active = await session.scalar(
                select(func.count(Channel.id)).where(
                    Channel.is_active.is_(True),
                    Channel.is_blacklisted.is_(False),
                )
            )
            with_comments = await session.scalar(
                select(func.count(Channel.id)).where(Channel.comments_enabled.is_(True))
            )
            blacklisted = await session.scalar(
                select(func.count(Channel.id)).where(Channel.is_blacklisted.is_(True))
            )

            rows = await session.execute(
                select(Channel.topic, func.count(Channel.id))
                .where(Channel.topic.is_not(None))
                .group_by(Channel.topic)
                .order_by(func.count(Channel.id).desc())
            )
            by_topic = {topic: count for topic, count in rows.all() if topic}

        return {
            "total": int(total or 0),
            "active": int(active or 0),
            "with_comments": int(with_comments or 0),
            "blacklisted": int(blacklisted or 0),
            "by_topic": by_topic,
        }

    async def export_to_txt(self, filepath: str = "data/channels_export.txt") -> int:
        """Экспорт активных каналов в TXT файл. Возвращает количество."""
        channels = await self.get_all_active()
        lines = []
        for ch in channels:
            username = f"@{ch.username}" if ch.username else str(ch.telegram_id)
            topic = ch.topic or "—"
            lines.append(f"{username}\t{ch.title}\t{ch.subscribers}\t{topic}")

        import os
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("username\ttitle\tsubscribers\ttopic\n")
            f.write("\n".join(lines))

        return len(lines)

    async def get_usernames(self) -> list[str]:
        """Получить список username для поиска похожих каналов."""
        channels = await self.get_all_active()
        return [ch.username for ch in channels if ch.username]

    @staticmethod
    def _to_payload(channel_info: Any) -> dict:
        """Нормализовать входные данные канала в словарь."""
        if isinstance(channel_info, dict):
            source = channel_info
            getter = source.get
        else:
            source = channel_info
            getter = lambda key, default=None: getattr(source, key, default)

        telegram_id = getter("telegram_id")
        if telegram_id is None:
            raise ValueError("channel_info должен содержать telegram_id")

        return {
            "telegram_id": int(telegram_id),
            "username": getter("username"),
            "title": getter("title", "") or f"channel_{telegram_id}",
            "subscribers": int(getter("subscribers", 0) or 0),
            "topic": getter("topic"),
            "comments_enabled": bool(getter("comments_enabled", False)),
            "discussion_group_id": getter("discussion_group_id"),
        }
