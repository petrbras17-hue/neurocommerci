"""
Пассивные действия для естественности аккаунтов.
Просмотры, реакции, чтение каналов.
"""

from __future__ import annotations

import asyncio
import random
from typing import Optional

from telethon import TelegramClient
from telethon.tl.functions.messages import (
    GetMessagesViewsRequest,
    SendReactionRequest,
    ReadHistoryRequest,
)
from telethon.tl.types import ReactionEmoji

from core.session_manager import SessionManager
from utils.logger import log


# Популярные реакции Telegram
REACTION_EMOJIS = ["👍", "❤️", "🔥", "👏", "😂", "🤔", "👀", "💯"]


class PassiveActionsManager:
    """Пассивные действия: просмотры, реакции, чтение каналов."""

    def __init__(self, session_manager: SessionManager):
        self.session_mgr = session_manager
        self._stats = {"views": 0, "reactions": 0, "reads": 0}

    async def view_post(self, phone: str, channel_id: int, post_id: int) -> bool:
        """Просмотреть пост (увеличить счётчик просмотров)."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return False

        try:
            entity = await client.get_entity(channel_id)
            await client(GetMessagesViewsRequest(
                peer=entity,
                id=[post_id],
                increment=True,
            ))
            self._stats["views"] += 1
            log.debug(f"{phone}: просмотр поста {post_id} в канале {channel_id}")
            return True
        except Exception as exc:
            log.debug(f"{phone}: ошибка просмотра поста: {exc}")
            return False

    async def send_reaction(
        self,
        phone: str,
        channel_id: int,
        post_id: int,
        emoji: Optional[str] = None,
    ) -> bool:
        """Поставить реакцию на пост."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return False

        if emoji is None:
            emoji = random.choice(REACTION_EMOJIS)

        try:
            entity = await client.get_entity(channel_id)
            await client(SendReactionRequest(
                peer=entity,
                msg_id=post_id,
                reaction=[ReactionEmoji(emoticon=emoji)],
            ))
            self._stats["reactions"] += 1
            log.debug(f"{phone}: реакция {emoji} на пост {post_id}")
            return True
        except Exception as exc:
            log.debug(f"{phone}: ошибка реакции: {exc}")
            return False

    async def mark_as_read(self, phone: str, channel_id: int) -> bool:
        """Пометить канал как прочитанный."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return False

        try:
            entity = await client.get_entity(channel_id)
            await client(ReadHistoryRequest(
                peer=entity,
                max_id=0,  # Прочитать всё
            ))
            self._stats["reads"] += 1
            log.debug(f"{phone}: канал {channel_id} прочитан")
            return True
        except Exception as exc:
            log.debug(f"{phone}: ошибка чтения канала: {exc}")
            return False

    async def do_random_passive_action(
        self,
        phone: str,
        channel_id: int,
        post_id: int,
    ) -> str:
        """
        Выполнить случайное пассивное действие.
        Возвращает тип действия: "view", "reaction", "read", "none".
        """
        roll = random.random()

        if roll < 0.4:
            # Просмотр (40%)
            ok = await self.view_post(phone, channel_id, post_id)
            return "view" if ok else "none"
        elif roll < 0.65:
            # Реакция (25%)
            ok = await self.send_reaction(phone, channel_id, post_id)
            return "reaction" if ok else "none"
        elif roll < 0.85:
            # Чтение канала (20%)
            ok = await self.mark_as_read(phone, channel_id)
            return "read" if ok else "none"
        else:
            # Ничего (15%)
            return "none"

    def get_stats(self) -> dict:
        return dict(self._stats)
