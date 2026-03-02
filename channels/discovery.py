"""
Поиск и базовая фильтрация Telegram-каналов через Telethon.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from telethon import functions
from telethon.tl.types import Channel as TLChannel

from core.account_manager import AccountManager
from core.proxy_manager import ProxyManager
from core.session_manager import SessionManager
from utils.logger import log


_RU_TEXT_RE = re.compile(r"[А-Яа-яЁё]")


@dataclass
class ChannelInfo:
    """Нормализованное представление канала для сохранения и отображения."""

    telegram_id: int
    username: Optional[str]
    title: str
    subscribers: int
    topic: Optional[str] = None
    comments_enabled: bool = False
    discussion_group_id: Optional[int] = None
    is_russian: bool = False
    about: str = ""


class ChannelDiscovery:
    """Парсер каналов через Telethon."""

    PRESET_TOPIC_KEYWORDS: dict[str, list[str]] = {
        "vpn": ["vpn", "впн", "разблокировка", "обход блокировок", "proxy", "прокси"],
        "ai": ["нейросети", "chatgpt", "midjourney", "claude", "ai", "искусственный интеллект", "генерация"],
        "social": ["instagram", "инстаграм", "facebook", "тикток", "youtube"],
        "it": ["it", "программирование", "devops", "технологии", "стартап"],
        "streaming": ["netflix", "spotify", "подписки", "стриминг"],
    }

    def __init__(
        self,
        session_manager: SessionManager,
        account_manager: AccountManager,
        proxy_manager: Optional[ProxyManager] = None,
        search_limit: int = 30,
    ):
        self.session_mgr = session_manager
        self.account_mgr = account_manager
        self.proxy_mgr = proxy_manager
        self.search_limit = search_limit

    async def search_by_keywords(
        self,
        keywords: list[str],
        min_subscribers: int = 500,
    ) -> list[ChannelInfo]:
        """Найти каналы по набору ключевых слов."""
        cleaned_keywords = [kw.strip() for kw in keywords if kw and kw.strip()]
        if not cleaned_keywords:
            return []

        client = await self._get_working_client()
        discovered: dict[int, ChannelInfo] = {}

        for keyword in cleaned_keywords:
            found = await self._search_one_keyword(client, keyword, min_subscribers=min_subscribers)
            for item in found:
                if item.telegram_id not in discovered:
                    discovered[item.telegram_id] = item

        channels = sorted(discovered.values(), key=lambda c: c.subscribers, reverse=True)
        log.info(f"Поиск по ключам {cleaned_keywords}: найдено {len(channels)} каналов")
        return channels

    async def check_comments_enabled(self, channel: Any) -> bool:
        """Проверить, что у канала включены комментарии (через discussion group)."""
        info = await self.get_channel_info(channel)
        return info.comments_enabled

    async def get_channel_info(self, username_or_id: Any) -> ChannelInfo:
        """Получить детальную информацию по одному каналу."""
        client = await self._get_working_client()
        entity = await client.get_entity(username_or_id)

        if not isinstance(entity, TLChannel) or not getattr(entity, "broadcast", False):
            raise ValueError("Указанный объект не является публичным Telegram-каналом.")

        info = await self._build_channel_info(client, entity)
        if not info:
            raise ValueError("Не удалось получить информацию о канале.")
        return info

    async def bulk_discover(self, topic_sets: dict[str, list[str]]) -> list[ChannelInfo]:
        """Поиск каналов сразу по нескольким тематикам."""
        topics = topic_sets or self.PRESET_TOPIC_KEYWORDS
        discovered: dict[int, ChannelInfo] = {}

        for topic, keywords in topics.items():
            channels = await self.search_by_keywords(keywords=keywords)
            for channel in channels:
                if channel.telegram_id not in discovered:
                    channel.topic = topic
                    discovered[channel.telegram_id] = channel
                elif not discovered[channel.telegram_id].topic:
                    discovered[channel.telegram_id].topic = topic

        return sorted(discovered.values(), key=lambda c: c.subscribers, reverse=True)

    async def _search_one_keyword(
        self,
        client: Any,
        keyword: str,
        min_subscribers: int,
    ) -> list[ChannelInfo]:
        try:
            result = await client(functions.contacts.SearchRequest(q=keyword, limit=self.search_limit))
        except Exception as exc:
            log.warning(f"Ошибка поиска по ключу '{keyword}': {exc}")
            return []

        channels: list[ChannelInfo] = []
        for chat in getattr(result, "chats", []):
            if not isinstance(chat, TLChannel):
                continue
            if not getattr(chat, "broadcast", False):
                continue

            info = await self._build_channel_info(client, chat, keyword=keyword)
            if not info:
                continue

            if info.subscribers < min_subscribers:
                continue
            if not info.comments_enabled:
                continue
            if not info.is_russian:
                continue

            channels.append(info)

        return channels

    async def _build_channel_info(
        self,
        client: Any,
        channel: TLChannel,
        keyword: str = "",
    ) -> Optional[ChannelInfo]:
        try:
            full = await client(functions.channels.GetFullChannelRequest(channel=channel))
        except Exception as exc:
            log.debug(f"Пропуск канала {getattr(channel, 'id', 'unknown')}: {exc}")
            return None

        full_chat = full.full_chat
        about = (getattr(full_chat, "about", "") or "").strip()
        discussion_group_id = getattr(full_chat, "linked_chat_id", None)
        subscribers = self._extract_subscribers(channel, full_chat)

        title = (getattr(channel, "title", "") or "").strip()
        username = getattr(channel, "username", None)
        is_russian = self._is_russian_channel(title=title, about=about, username=username, keyword=keyword)

        return ChannelInfo(
            telegram_id=int(channel.id),
            username=username,
            title=title or f"channel_{channel.id}",
            subscribers=subscribers,
            comments_enabled=bool(discussion_group_id),
            discussion_group_id=discussion_group_id,
            is_russian=is_russian,
            about=about,
        )

    @staticmethod
    def _extract_subscribers(channel: TLChannel, full_chat: Any) -> int:
        participants_count = getattr(full_chat, "participants_count", None)
        if isinstance(participants_count, int):
            return participants_count

        fallback_count = getattr(channel, "participants_count", None)
        if isinstance(fallback_count, int):
            return fallback_count
        return 0

    @staticmethod
    def _is_russian_channel(
        title: str,
        about: str,
        username: Optional[str],
        keyword: str,
    ) -> bool:
        text = f"{title}\n{about}"
        if _RU_TEXT_RE.search(text):
            return True
        if _RU_TEXT_RE.search(keyword):
            return True

        username_lc = (username or "").lower()
        ru_username_hints = ("ru", "russia", "moscow", "spb", "russ", "vpn")
        return any(hint in username_lc for hint in ru_username_hints)

    async def find_similar_channels(
        self,
        source_usernames: list[str],
        min_subscribers: int = 500,
    ) -> list[ChannelInfo]:
        """
        Найти похожие каналы через поиск по названиям существующих.
        Для каждого исходного канала берём слова из названия и ищем.
        """
        client = await self._get_working_client()
        discovered: dict[int, ChannelInfo] = {}
        source_ids: set[int] = set()

        for username in source_usernames[:20]:
            try:
                entity = await client.get_entity(username)
                source_ids.add(entity.id)

                title = getattr(entity, "title", "")
                if title:
                    words = title.split()[:3]
                    search_query = " ".join(words)
                    found = await self._search_one_keyword(
                        client, search_query, min_subscribers=min_subscribers,
                    )
                    for ch in found:
                        if ch.telegram_id not in discovered and ch.telegram_id not in source_ids:
                            discovered[ch.telegram_id] = ch

            except Exception as exc:
                log.debug(f"Пропуск {username} при поиске похожих: {exc}")
                continue

        channels = sorted(discovered.values(), key=lambda c: c.subscribers, reverse=True)
        log.info(f"Похожие каналы: найдено {len(channels)} новых")
        return channels

    async def _get_working_client(self) -> Any:
        connected = self.session_mgr.get_connected_phones()
        for phone in connected:
            client = self.session_mgr.get_client(phone)
            if client and client.is_connected():
                return client

        accounts = await self.account_mgr.load_accounts()
        for account in accounts:
            proxy = None
            if self.proxy_mgr:
                proxy = self.proxy_mgr.get_for_account(account.phone) or self.proxy_mgr.assign_to_account(account.phone)

            client = await self.session_mgr.connect_client(account.phone, proxy)
            if client:
                return client

        if not accounts:
            for session_name in self.session_mgr.list_session_files():
                proxy = self.proxy_mgr.assign_to_account(session_name) if self.proxy_mgr else None
                client = await self.session_mgr.connect_client(session_name, proxy)
                if client:
                    return client

        raise RuntimeError(
            "Нет подключённого аккаунта для парсинга. Добавьте .session файл и убедитесь, что API ID/HASH настроены."
        )
