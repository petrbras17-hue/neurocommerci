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

    @staticmethod
    def _scope_filter(user_id: int | None):
        if user_id is None:
            return Channel.user_id.is_(None)
        return Channel.user_id == user_id

    async def add_channel(self, channel_info: Any, user_id: int = None) -> Channel:
        """Добавить канал в БД или обновить существующий по telegram_id."""
        payload = self._to_payload(channel_info)

        async with async_session() as session:
            query = select(Channel).where(
                Channel.telegram_id == payload["telegram_id"],
                self._scope_filter(user_id),
            )
            result = await session.execute(query)
            existing = result.scalar_one_or_none()

            if existing:
                existing.username = payload["username"]
                existing.title = payload["title"]
                existing.subscribers = payload["subscribers"]
                existing.topic = payload["topic"]
                existing.comments_enabled = payload["comments_enabled"]
                existing.discussion_group_id = payload["discussion_group_id"]
                existing.review_state = payload["review_state"]
                existing.publish_mode = payload["publish_mode"]
                existing.permission_basis = payload["permission_basis"]
                existing.review_note = payload["review_note"]
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
                    review_state=payload["review_state"],
                    publish_mode=payload["publish_mode"],
                    permission_basis=payload["permission_basis"],
                    review_note=payload["review_note"],
                    user_id=user_id,
                    is_active=True,
                    is_blacklisted=False,
                    last_checked_at=utcnow(),
                )
                session.add(channel)

            await session.commit()
            await session.refresh(channel)
            return channel

    async def get_all_active(self, user_id: int = None) -> list[Channel]:
        """Получить активные каналы (не в чёрном списке)."""
        async with async_session() as session:
            query = (
                select(Channel)
                .where(Channel.is_active.is_(True), Channel.is_blacklisted.is_(False))
                .order_by(Channel.subscribers.desc())
            )
            if user_id is not None:
                query = query.where(Channel.user_id == user_id)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_publishable(self, user_id: int = None) -> list[Channel]:
        """Каналы, разрешённые для автоматического execution path."""
        async with async_session() as session:
            query = (
                select(Channel)
                .where(
                    Channel.is_active.is_(True),
                    Channel.is_blacklisted.is_(False),
                    Channel.review_state == "approved",
                    Channel.publish_mode == "auto_allowed",
                )
                .order_by(Channel.subscribers.desc())
            )
            if user_id is not None:
                query = query.where(Channel.user_id == user_id)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_all(self, user_id: int = None) -> list[Channel]:
        """Получить все каналы."""
        async with async_session() as session:
            query = select(Channel).order_by(Channel.created_at.desc())
            if user_id is not None:
                query = query.where(Channel.user_id == user_id)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_by_topic(self, topic: str, user_id: int = None) -> list[Channel]:
        """Получить активные каналы по тематике."""
        async with async_session() as session:
            query = (
                select(Channel)
                .where(
                    Channel.is_active.is_(True),
                    Channel.is_blacklisted.is_(False),
                    func.lower(Channel.topic) == topic.lower(),
                )
                .order_by(Channel.subscribers.desc())
            )
            if user_id is not None:
                query = query.where(Channel.user_id == user_id)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def blacklist_channel(self, channel_id: int):
        """Добавить канал в чёрный список по локальному DB id или scoped telegram_id."""
        await self.blacklist_channel_ref(channel_id)

    async def blacklist_channel_ref(self, channel_ref: int, user_id: int | None = None) -> bool:
        """Add channel to blacklist by DB id first, then by tenant-scoped telegram_id."""
        async with async_session() as session:
            result = await session.execute(
                select(Channel.id).where(Channel.id == channel_ref)
            )
            channel_db_id = result.scalar_one_or_none()
            if channel_db_id is None:
                result = await session.execute(
                    select(Channel.id).where(
                        Channel.telegram_id == channel_ref,
                        self._scope_filter(user_id),
                    )
                )
                channel_db_id = result.scalar_one_or_none()
            if channel_db_id is None:
                return False
            await session.execute(
                update(Channel)
                .where(Channel.id == channel_db_id)
                .values(is_blacklisted=True, is_active=False, last_checked_at=utcnow())
            )
            await session.commit()
            return True

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

    async def set_review_state(
        self,
        channel_id: int,
        *,
        review_state: str,
        publish_mode: str | None = None,
        permission_basis: str | None = None,
        review_note: str | None = None,
        user_id: int | None = None,
    ):
        """Update channel review/publish policy by telegram_id."""
        values: dict[str, Any] = {"review_state": review_state, "last_checked_at": utcnow()}
        if publish_mode is not None:
            values["publish_mode"] = publish_mode
        if permission_basis is not None:
            values["permission_basis"] = permission_basis
        if review_note is not None:
            values["review_note"] = review_note
        async with async_session() as session:
            await session.execute(
                update(Channel)
                .where(
                    Channel.telegram_id == channel_id,
                    self._scope_filter(user_id),
                )
                .values(**values)
            )
            await session.commit()

    async def set_review_state_by_db_id(
        self,
        channel_db_id: int,
        *,
        review_state: str,
        publish_mode: str | None = None,
        permission_basis: str | None = None,
        review_note: str | None = None,
        user_id: int | None = None,
    ) -> bool:
        """Update channel review/publish policy by local DB id."""
        values: dict[str, Any] = {"review_state": review_state, "last_checked_at": utcnow()}
        if publish_mode is not None:
            values["publish_mode"] = publish_mode
        if permission_basis is not None:
            values["permission_basis"] = permission_basis
        if review_note is not None:
            values["review_note"] = review_note
        async with async_session() as session:
            query = select(Channel.id).where(Channel.id == channel_db_id)
            if user_id is not None:
                query = query.where(Channel.user_id == user_id)
            result = await session.execute(query)
            found = result.scalar_one_or_none()
            if found is None:
                return False
            await session.execute(
                update(Channel).where(Channel.id == channel_db_id).values(**values)
            )
            await session.commit()
            return True

    async def get_by_db_id(self, channel_db_id: int, user_id: int | None = None) -> Channel | None:
        """Fetch channel by local DB id."""
        async with async_session() as session:
            query = select(Channel).where(Channel.id == channel_db_id)
            if user_id is not None:
                query = query.where(Channel.user_id == user_id)
            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def get_review_queue(
        self,
        *,
        user_id: int | None = None,
        states: tuple[str, ...] = ("discovered", "candidate"),
        limit: int = 10,
    ) -> list[Channel]:
        """Channels waiting for manual review."""
        async with async_session() as session:
            query = (
                select(Channel)
                .where(
                    Channel.is_active.is_(True),
                    Channel.is_blacklisted.is_(False),
                    Channel.review_state.in_(states),
                )
                .order_by(Channel.subscribers.desc(), Channel.created_at.asc())
                .limit(max(1, limit))
            )
            if user_id is not None:
                query = query.where(Channel.user_id == user_id)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_stats(self, user_id: int = None) -> dict:
        """Сводная статистика каналов."""
        async with async_session() as session:
            base_filter = []
            if user_id is not None:
                base_filter.append(Channel.user_id == user_id)

            total = await session.scalar(
                select(func.count(Channel.id)).where(*base_filter)
            )
            active_filters = [Channel.is_active.is_(True), Channel.is_blacklisted.is_(False)] + base_filter
            active = await session.scalar(
                select(func.count(Channel.id)).where(*active_filters)
            )
            comments_filters = [Channel.comments_enabled.is_(True)] + base_filter
            with_comments = await session.scalar(
                select(func.count(Channel.id)).where(*comments_filters)
            )
            bl_filters = [Channel.is_blacklisted.is_(True)] + base_filter
            blacklisted = await session.scalar(
                select(func.count(Channel.id)).where(*bl_filters)
            )

            topic_query = (
                select(Channel.topic, func.count(Channel.id))
                .where(Channel.topic.is_not(None), *base_filter)
                .group_by(Channel.topic)
                .order_by(func.count(Channel.id).desc())
            )
            rows = await session.execute(topic_query)
            by_topic = {topic: count for topic, count in rows.all() if topic}

            review_query = (
                select(Channel.review_state, func.count(Channel.id))
                .where(*base_filter)
                .group_by(Channel.review_state)
            )
            review_rows = await session.execute(review_query)
            by_review = {str(state or "unknown"): int(count) for state, count in review_rows.all()}

            publish_query = (
                select(Channel.publish_mode, func.count(Channel.id))
                .where(*base_filter)
                .group_by(Channel.publish_mode)
            )
            publish_rows = await session.execute(publish_query)
            by_publish_mode = {
                str(mode or "unknown"): int(count) for mode, count in publish_rows.all()
            }

            publishable_filters = [
                Channel.is_active.is_(True),
                Channel.is_blacklisted.is_(False),
                Channel.review_state == "approved",
                Channel.publish_mode == "auto_allowed",
            ] + base_filter
            publishable = await session.scalar(
                select(func.count(Channel.id)).where(*publishable_filters)
            )

        return {
            "total": int(total or 0),
            "active": int(active or 0),
            "with_comments": int(with_comments or 0),
            "blacklisted": int(blacklisted or 0),
            "by_topic": by_topic,
            "by_review": by_review,
            "by_publish_mode": by_publish_mode,
            "publishable": int(publishable or 0),
        }

    async def export_to_txt(self, filepath: str = "data/channels_export.txt", user_id: int = None) -> int:
        """Экспорт активных каналов в TXT файл. Возвращает количество."""
        channels = await self.get_all_active(user_id=user_id)
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

    async def get_usernames(self, user_id: int = None) -> list[str]:
        """Получить список username для поиска похожих каналов."""
        channels = await self.get_all_active(user_id=user_id)
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
            "review_state": getter("review_state", "discovered") or "discovered",
            "publish_mode": getter("publish_mode", "research_only") or "research_only",
            "permission_basis": getter("permission_basis", "") or "",
            "review_note": getter("review_note"),
        }
