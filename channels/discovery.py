"""
Поиск и базовая фильтрация Telegram-каналов через Telethon.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Optional

from telethon import functions
from telethon.tl.types import Channel as TLChannel
from sqlalchemy import select

from config import settings
from core.account_manager import AccountManager
from core.policy_engine import policy_engine
from core.account_capabilities import (
    probe_account_capabilities,
    persist_probe_result,
    is_frozen_error,
)
from core.proxy_manager import ProxyManager
from core.session_manager import SessionManager
from storage.models import Account
from storage.sqlite_db import async_session
from utils.logger import log
from utils.proxy_bindings import get_bound_proxy_config


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
        self.last_filter_stats: dict[str, int] = {}
        self._last_client_phone: str = ""

    async def search_by_keywords(
        self,
        keywords: list[str],
        min_subscribers: int = 500,
        require_comments: bool = True,
        require_russian: bool = True,
        stage1_limit: Optional[int] = None,
    ) -> list[ChannelInfo]:
        """Найти каналы по набору ключевых слов."""
        cleaned_keywords = [kw.strip() for kw in keywords if kw and kw.strip()]
        if not cleaned_keywords:
            return []

        client = await self._get_working_client()
        if not client:
            raise RuntimeError("Нет доступного parser-аккаунта")
        parser_phone = self._last_client_phone
        if settings.FROZEN_PROBE_BEFORE_PARSER:
            probe = await probe_account_capabilities(client, run_search_probe=True)
            reason = str(probe.get("reason", "ok"))
            mark_restricted = reason in {"frozen", "restricted"}
            if parser_phone:
                await persist_probe_result(
                    parser_phone,
                    probe,
                    mark_restricted_on_failure=mark_restricted,
                    restriction_reason=reason if mark_restricted else None,
                )
            if mark_restricted or not probe.get("can_search", False):
                if parser_phone:
                    await policy_engine.check(
                        "frozen_probe_failed",
                        {
                            "phone": parser_phone,
                            "reason": reason,
                            "capabilities": probe,
                            "source": "parser_search",
                        },
                        phone=parser_phone,
                    )
                raise RuntimeError(f"frozen_probe_failed: parser account blocked ({reason})")

        discovered: dict[int, ChannelInfo] = {}

        for i, keyword in enumerate(cleaned_keywords):
            if i > 0:
                await asyncio.sleep(2.0)  # Задержка между запросами (FloodWait)
            try:
                found = await self._search_one_keyword(client, keyword, limit=stage1_limit or self.search_limit)
            except RuntimeError as exc:
                if str(exc).startswith("search_blocked_by_telegram:"):
                    if parser_phone:
                        await policy_engine.check(
                            "parser_search_blocked",
                            {
                                "blocked": True,
                                "keyword": keyword,
                                "error": str(exc),
                            },
                            phone=parser_phone,
                            worker_id="parser",
                        )
                raise
            for item in found:
                if item.telegram_id not in discovered:
                    discovered[item.telegram_id] = item

        stage1 = sorted(discovered.values(), key=lambda c: c.subscribers, reverse=True)
        stage2, stats = self.apply_stage2_filters(
            stage1,
            min_subscribers=min_subscribers,
            require_comments=require_comments,
            require_russian=require_russian,
        )
        self.last_filter_stats = stats
        log.info(
            f"Поиск по ключам {cleaned_keywords}: stage1={stats['stage1_total']} "
            f"stage2={stats['stage2_total']} (dropped={stats['dropped_total']})"
        )
        return stage2

    def apply_stage2_filters(
        self,
        candidates: list[ChannelInfo],
        *,
        min_subscribers: int,
        require_comments: bool,
        require_russian: bool,
    ) -> tuple[list[ChannelInfo], dict[str, int]]:
        """Apply strict filters as stage-2 with transparent drop statistics."""
        stats = {
            "stage1_total": len(candidates),
            "dropped_subscribers": 0,
            "dropped_comments": 0,
            "dropped_language": 0,
            "dropped_total": 0,
            "stage2_total": 0,
        }
        result: list[ChannelInfo] = []
        for info in candidates:
            if info.subscribers < min_subscribers:
                stats["dropped_subscribers"] += 1
                continue
            if require_comments and not info.comments_enabled:
                stats["dropped_comments"] += 1
                continue
            if require_russian and not info.is_russian:
                stats["dropped_language"] += 1
                continue
            result.append(info)
        stats["stage2_total"] = len(result)
        stats["dropped_total"] = (
            stats["dropped_subscribers"] + stats["dropped_comments"] + stats["dropped_language"]
        )
        return result, stats

    async def check_comments_enabled(self, channel: Any) -> bool:
        """Проверить, что у канала включены комментарии (через discussion group)."""
        info = await self.get_channel_info(channel)
        return info.comments_enabled

    async def get_channel_info(self, username_or_id: Any) -> ChannelInfo:
        """Получить детальную информацию по одному каналу."""
        client = await self._get_working_client()
        if not client:
            raise RuntimeError("Нет подключённого аккаунта для парсинга")
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
        limit: int,
    ) -> list[ChannelInfo]:
        try:
            result = await client(functions.contacts.SearchRequest(q=keyword, limit=limit))
        except Exception as exc:
            if is_frozen_error(exc):
                raise RuntimeError(f"search_blocked_by_telegram: {exc}") from exc
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
        if not client:
            return []
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
                        client,
                        search_query,
                        limit=self.search_limit,
                    )
                    if min_subscribers > 0:
                        found = [ch for ch in found if ch.subscribers >= min_subscribers]
                    for ch in found:
                        if ch.telegram_id not in discovered and ch.telegram_id not in source_ids:
                            discovered[ch.telegram_id] = ch

            except Exception as exc:
                log.debug(f"Пропуск {username} при поиске похожих: {exc}")
                continue

        channels = sorted(discovered.values(), key=lambda c: c.subscribers, reverse=True)
        log.info(f"Похожие каналы: найдено {len(channels)} новых")
        return channels

    async def _get_working_client(self) -> Optional[Any]:
        parser_phone = self._normalize_phone(settings.PARSER_ONLY_PHONE)
        strict_mode = (settings.COMPLIANCE_MODE or "").strip().lower() == "strict"
        if strict_mode and settings.STRICT_PARSER_ONLY and not parser_phone:
            await policy_engine.check(
                "parser_without_parser_phone",
                {
                    "strict_parser_only": True,
                    "parser_phone_configured": False,
                },
                worker_id="parser",
            )
            raise RuntimeError("parser_not_configured: set PARSER_ONLY_PHONE in strict mode")

        if parser_phone:
            self._last_client_phone = parser_phone
            return await self._get_parser_only_client(parser_phone)

        if strict_mode and settings.STRICT_PARSER_ONLY:
            raise RuntimeError("parser_not_configured: strict parser-only mode is enabled")

        connected = self.session_mgr.get_connected_phones()
        for phone in connected:
            client = self.session_mgr.get_client(phone)
            if client and client.is_connected():
                account = await self._load_account(phone)
                decision = await policy_engine.check(
                    "parser_client_candidate",
                    {"account": self._account_ctx(account, phone)},
                    phone=phone,
                )
                if decision.action in {"block", "quarantine"}:
                    continue
                self._last_client_phone = phone
                return client

        accounts = await self.account_mgr.load_accounts()
        for account in accounts:
            proxy = await get_bound_proxy_config(account.phone)
            if proxy is None and self.proxy_mgr:
                proxy = self.proxy_mgr.get_for_account(account.phone) or self.proxy_mgr.assign_to_account(account.phone)

            decision = await policy_engine.check(
                "parser_client_candidate",
                {"account": self._account_ctx(account, account.phone)},
                phone=account.phone,
            )
            if decision.action in {"block", "quarantine"}:
                continue

            client = await self.session_mgr.connect_client(account.phone, proxy, user_id=account.user_id)
            if client:
                self._last_client_phone = account.phone
                return client

        log.error("Нет подключённого аккаунта для парсинга. Добавьте .session файл и убедитесь, что API ID/HASH настроены.")
        return None

    @staticmethod
    def _normalize_phone(raw_phone: str) -> str:
        digits = "".join(ch for ch in str(raw_phone) if ch.isdigit())
        return f"+{digits}" if digits else ""

    async def _load_account(self, phone: str) -> Optional[Account]:
        async with async_session() as session:
            result = await session.execute(select(Account).where(Account.phone == phone))
            return result.scalar_one_or_none()

    @staticmethod
    def _account_ctx(account: Optional[Account], phone: str) -> dict[str, Any]:
        if account is None:
            return {
                "phone": phone,
                "health_status": "unknown",
                "lifecycle_stage": "unknown",
                "status": "unknown",
            }
        return {
            "phone": account.phone,
            "health_status": account.health_status,
            "lifecycle_stage": account.lifecycle_stage,
            "status": account.status,
        }

    async def _get_parser_only_client(self, parser_phone: str) -> Optional[Any]:
        account = await self._load_account(parser_phone)
        if account is None:
            raise RuntimeError(f"PARSER_ONLY_PHONE={parser_phone}: аккаунт не найден в БД")

        decision = await policy_engine.check(
            "parser_client_candidate",
            {"account": self._account_ctx(account, parser_phone)},
            phone=parser_phone,
        )
        if decision.action in {"block", "quarantine"}:
            raise RuntimeError(
                f"PARSER_ONLY_PHONE={parser_phone} недоступен для парсинга: {decision.rule_id}"
            )

        existing = self.session_mgr.get_client(parser_phone)
        if existing and existing.is_connected():
            return existing

        proxy = None
        proxy = await get_bound_proxy_config(parser_phone)
        if proxy is None and self.proxy_mgr:
            proxy = self.proxy_mgr.get_for_account(parser_phone) or self.proxy_mgr.assign_to_account(parser_phone)
        client = await self.session_mgr.connect_client(parser_phone, proxy, user_id=account.user_id)
        if not client:
            raise RuntimeError(f"PARSER_ONLY_PHONE={parser_phone}: не удалось подключить клиент")
        return client
