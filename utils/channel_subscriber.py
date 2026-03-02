"""
Автоподписка аккаунтов на каналы и группы обсуждений.
Подписка необходима для возможности комментирования.
"""

from __future__ import annotations

import asyncio
import random
from typing import Optional

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
    UserAlreadyParticipantError,
    InviteHashExpiredError,
    ChatWriteForbiddenError,
)
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.types import Channel

from config import settings
from core.session_manager import SessionManager
from core.account_manager import AccountManager
from core.proxy_manager import ProxyManager
from channels.channel_db import ChannelDB
from storage.sqlite_db import async_session
from storage.models import Channel as DbChannel
from utils.anti_ban import AntibanManager
from utils.logger import log


class ChannelSubscriber:
    """Автоподписка аккаунтов на каналы из БД."""

    def __init__(
        self,
        session_manager: SessionManager,
        account_manager: AccountManager,
    ):
        self.session_mgr = session_manager
        self.account_mgr = account_manager
        self.channel_db = ChannelDB()
        self.antiban = AntibanManager()
        self._stats = {"subscribed": 0, "already": 0, "failed": 0, "unsubscribed": 0}

    async def subscribe_account_to_channels(
        self,
        phone: str,
        max_channels: int = 160,
    ) -> dict:
        """
        Подписать один аккаунт на все активные каналы из БД.
        Возвращает статистику {subscribed, already, failed}.
        """
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            log.warning(f"{phone}: клиент не подключён для подписки")
            return {"subscribed": 0, "already": 0, "failed": 0}

        channels = await self.channel_db.get_all_active()
        channels = channels[:max_channels]

        result = {"subscribed": 0, "already": 0, "failed": 0}

        for channel in channels:
            try:
                # Задержка между подписками (2-8 сек)
                delay = random.uniform(2.0, 8.0)
                await asyncio.sleep(delay)

                entity = await client.get_entity(channel.telegram_id)
                await client(JoinChannelRequest(entity))
                result["subscribed"] += 1
                self._stats["subscribed"] += 1
                log.debug(f"{phone}: подписан на {channel.title}")

                # Если есть группа обсуждений — подписаться и на неё
                if channel.discussion_group_id:
                    await asyncio.sleep(random.uniform(1.0, 3.0))
                    try:
                        discussion = await client.get_entity(channel.discussion_group_id)
                        await client(JoinChannelRequest(discussion))
                        log.debug(f"{phone}: вступил в группу обсуждений {channel.title}")
                    except UserAlreadyParticipantError:
                        pass
                    except Exception as exc:
                        log.debug(f"{phone}: не удалось вступить в группу обсуждений: {exc}")

            except UserAlreadyParticipantError:
                result["already"] += 1
                self._stats["already"] += 1

            except FloodWaitError as e:
                log.warning(f"{phone}: FloodWait {e.seconds}с при подписке")
                await asyncio.sleep(min(e.seconds, 60))
                result["failed"] += 1
                self._stats["failed"] += 1
                break  # Прекращаем подписку для этого аккаунта

            except ChannelPrivateError:
                log.debug(f"{phone}: канал {channel.title} приватный")
                result["failed"] += 1
                self._stats["failed"] += 1

            except Exception as exc:
                log.debug(f"{phone}: ошибка подписки на {channel.title}: {exc}")
                result["failed"] += 1
                self._stats["failed"] += 1

        log.info(
            f"{phone}: подписка завершена — "
            f"новых {result['subscribed']}, уже были {result['already']}, ошибок {result['failed']}"
        )
        return result

    async def subscribe_all_accounts(self, max_channels: int = 160) -> dict:
        """Подписать все подключённые аккаунты на каналы."""
        connected = self.session_mgr.get_connected_phones()
        total = {"subscribed": 0, "already": 0, "failed": 0, "accounts": 0}

        for phone in connected:
            result = await self.subscribe_account_to_channels(phone, max_channels)
            total["subscribed"] += result["subscribed"]
            total["already"] += result["already"]
            total["failed"] += result["failed"]
            total["accounts"] += 1

            # Пауза между аккаунтами (10-30 сек)
            if phone != connected[-1]:
                await asyncio.sleep(random.uniform(10, 30))

        return total

    async def unsubscribe_from_channel(
        self,
        phone: str,
        channel_telegram_id: int,
    ) -> bool:
        """Отписать аккаунт от канала."""
        client = self.session_mgr.get_client(phone)
        if not client or not client.is_connected():
            return False

        try:
            entity = await client.get_entity(channel_telegram_id)
            await client(LeaveChannelRequest(entity))
            self._stats["unsubscribed"] += 1
            log.info(f"{phone}: отписан от канала {channel_telegram_id}")
            return True
        except Exception as exc:
            log.warning(f"{phone}: ошибка отписки от {channel_telegram_id}: {exc}")
            return False

    async def auto_unsubscribe_banned(self) -> int:
        """Автоотписка от каналов, где комментарии выключены или забанены."""
        channels = await self.channel_db.get_all_active()
        disabled = [ch for ch in channels if not ch.comments_enabled]
        count = 0

        connected = self.session_mgr.get_connected_phones()
        for channel in disabled:
            for phone in connected:
                success = await self.unsubscribe_from_channel(phone, channel.telegram_id)
                if success:
                    count += 1
                await asyncio.sleep(random.uniform(1.0, 3.0))

        if count:
            log.info(f"Автоотписка: {count} отписок от каналов без комментариев")
        return count

    def get_stats(self) -> dict:
        return dict(self._stats)
