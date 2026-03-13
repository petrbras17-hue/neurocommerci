"""
Smart Channel Discovery — multi-layer channel finding engine.

Combines 4 discovery methods in priority order:
1. TGStat API (bulk, no Telegram risk, real data)
2. Telethon SearchRequest (real-time keyword search)
3. Snowball crawl (extract t.me links from known channels)
4. Global message search (find channels by their posts)

Usage:
    discovery = SmartChannelDiscovery(session_manager=sm)
    results = await discovery.discover(
        keywords=["крипто", "бизнес"],
        methods=["tgstat", "telethon", "snowball"],
        max_results=500,
        min_members=1000,
    )
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import ChannelMapEntry
from utils.logger import log

# Regex to find t.me links in text
_TG_LINK_RE = re.compile(r"(?:https?://)?t\.me/([a-zA-Z_][a-zA-Z0-9_]{3,31})", re.IGNORECASE)
_RU_TEXT_RE = re.compile(r"[А-Яа-яЁё]")


@dataclass
class DiscoveredChannel:
    """A channel found by any discovery method."""
    username: str
    title: str = ""
    description: str = ""
    member_count: int = 0
    has_comments: bool = False
    language: str = "ru"
    region: str = "ru"
    category: str = ""
    source_method: str = ""  # tgstat, telethon, snowball, global_search
    raw_data: dict = field(default_factory=dict)


class SmartChannelDiscovery:
    """Multi-layer channel discovery engine."""

    def __init__(
        self,
        session_manager=None,
        tgstat_token: str | None = None,
    ):
        self._sm = session_manager
        self._tgstat_token = tgstat_token
        self._seen_usernames: set[str] = set()

    async def discover(
        self,
        keywords: list[str],
        methods: list[str] | None = None,
        max_results: int = 500,
        min_members: int = 1000,
        require_comments: bool = True,
        language_filter: str | None = "ru",
        db_session: AsyncSession | None = None,
    ) -> list[DiscoveredChannel]:
        """Run multi-layer discovery pipeline.

        Args:
            keywords: Search keywords
            methods: Which methods to use (default: all available)
            max_results: Maximum total results
            min_members: Minimum subscriber count
            require_comments: Only channels with open comments
            language_filter: Filter by language (None = any)
            db_session: Optional DB session for dedup against existing catalog
        """
        if methods is None:
            methods = self._available_methods()

        results: list[DiscoveredChannel] = []

        # Load existing usernames for dedup
        if db_session:
            await self._load_existing_usernames(db_session)

        for method in methods:
            if len(results) >= max_results:
                break

            remaining = max_results - len(results)
            log.info(f"[SmartDiscovery] Running {method} (have {len(results)}, need {remaining} more)")

            try:
                if method == "tgstat":
                    batch = await self._discover_tgstat(keywords, remaining, min_members)
                elif method == "telethon":
                    batch = await self._discover_telethon(keywords, remaining, min_members)
                elif method == "snowball":
                    batch = await self._discover_snowball(remaining, min_members)
                elif method == "global_search":
                    batch = await self._discover_global_search(keywords, remaining, min_members)
                else:
                    log.warning(f"[SmartDiscovery] Unknown method: {method}")
                    continue
            except Exception as e:
                log.error(f"[SmartDiscovery] {method} failed: {e}")
                continue

            # Filter
            for ch in batch:
                if ch.username.lower() in self._seen_usernames:
                    continue
                if ch.member_count < min_members:
                    continue
                if require_comments and not ch.has_comments:
                    continue
                if language_filter and ch.language != language_filter:
                    continue

                self._seen_usernames.add(ch.username.lower())
                results.append(ch)

                if len(results) >= max_results:
                    break

            log.info(f"[SmartDiscovery] {method} added {len(batch)} candidates, {len(results)} total after filter")

        return results

    def _available_methods(self) -> list[str]:
        """Return available methods based on configured services."""
        methods = []
        if self._tgstat_token:
            methods.append("tgstat")
        if self._sm:
            methods.append("telethon")
            methods.append("snowball")
            methods.append("global_search")
        return methods or ["tgstat"]  # fallback

    async def _load_existing_usernames(self, session: AsyncSession):
        """Load existing channel usernames from DB for deduplication.

        Limited to 100k to prevent unbounded memory usage on large catalogs.
        """
        result = await session.execute(
            select(ChannelMapEntry.username).where(
                ChannelMapEntry.username.isnot(None)
            ).limit(100_000)
        )
        for row in result.fetchall():
            if row[0]:
                self._seen_usernames.add(row[0].lower())
        log.info(f"[SmartDiscovery] Loaded {len(self._seen_usernames)} existing usernames for dedup")

    # ── Method 1: TGStat API ──────────────────────────────────────

    async def _discover_tgstat(
        self, keywords: list[str], limit: int, min_members: int
    ) -> list[DiscoveredChannel]:
        """Search channels via TGStat API (no Telegram risk).

        TGStat indexes 2.7M+ channels. Free tier: limited requests.
        Paid Search API: full access.

        API docs: https://api.tgstat.ru/docs/ru/channels/search.html
        """
        if not self._tgstat_token:
            log.warning("[SmartDiscovery] TGStat token not configured, skipping")
            return []

        import aiohttp

        results = []
        base_url = "https://api.tgstat.ru/channels/search"

        async with aiohttp.ClientSession() as http:
            for keyword in keywords:
                if len(results) >= limit:
                    break

                params = {
                    "token": self._tgstat_token,
                    "q": keyword,
                    "country": "ru",  # RU/CIS focus
                    "participants_count": f"{min_members};",  # min;max
                    "limit": min(50, limit - len(results)),
                }

                try:
                    async with http.get(base_url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            log.warning(f"[TGStat] HTTP {resp.status} for '{keyword}'")
                            continue
                        data = await resp.json()

                    if data.get("status") != "ok":
                        log.warning(f"[TGStat] Error for '{keyword}': {data.get('error')}")
                        continue

                    for item in data.get("response", {}).get("items", []):
                        username = (item.get("username") or "").lstrip("@")
                        if not username or username.lower() in self._seen_usernames:
                            continue

                        ch = DiscoveredChannel(
                            username=username,
                            title=item.get("title", ""),
                            description=item.get("description", ""),
                            member_count=item.get("participants_count", 0),
                            has_comments=True,  # TGStat doesn't always expose this
                            language=self._detect_language(
                                item.get("title", ""), item.get("description", "")
                            ),
                            region="ru",
                            category=item.get("category", ""),
                            source_method="tgstat",
                            raw_data=item,
                        )
                        results.append(ch)

                except Exception as e:
                    log.error(f"[TGStat] Request failed for '{keyword}': {e}")

                # Rate limit: 1 req/sec for TGStat
                await asyncio.sleep(1.0)

        return results

    # ── Method 2: Telethon keyword search ─────────────────────────

    async def _discover_telethon(
        self, keywords: list[str], limit: int, min_members: int
    ) -> list[DiscoveredChannel]:
        """Search Telegram via contacts.SearchRequest."""
        if not self._sm:
            return []

        client = await self._get_telethon_client()
        if not client:
            return []

        from telethon.tl.functions.contacts import SearchRequest

        results = []
        for keyword in keywords:
            if len(results) >= limit:
                break

            try:
                search_result = await client(SearchRequest(q=keyword, limit=100))

                for chat in search_result.chats:
                    if not hasattr(chat, "username") or not chat.username:
                        continue
                    if not hasattr(chat, "broadcast") or not chat.broadcast:
                        continue  # Only channels, not groups
                    if hasattr(chat, "participants_count") and (chat.participants_count or 0) < min_members:
                        continue

                    username = chat.username
                    if username.lower() in self._seen_usernames:
                        continue

                    # Get full channel info for comments check
                    has_comments = False
                    try:
                        from telethon.tl.functions.channels import GetFullChannelRequest
                        full = await client(GetFullChannelRequest(chat))
                        has_comments = bool(full.full_chat.linked_chat_id)
                    except Exception:
                        pass

                    ch = DiscoveredChannel(
                        username=username,
                        title=getattr(chat, "title", ""),
                        member_count=getattr(chat, "participants_count", 0) or 0,
                        has_comments=has_comments,
                        language=self._detect_language(
                            getattr(chat, "title", ""), ""
                        ),
                        region="ru",
                        source_method="telethon",
                    )
                    results.append(ch)

            except Exception as e:
                log.error(f"[Telethon] Search failed for '{keyword}': {e}")

            # Rate limit: 3-5s between searches
            await asyncio.sleep(3.0 + (hash(keyword) % 3))

        return results

    # ── Method 3: Snowball crawl ──────────────────────────────────

    async def _discover_snowball(
        self, limit: int, min_members: int
    ) -> list[DiscoveredChannel]:
        """Discover new channels by crawling existing ones for t.me links.

        Extracts t.me/ links from:
        - Channel descriptions
        - Forwarded message sources
        - Pinned messages
        """
        if not self._sm:
            return []

        client = await self._get_telethon_client()
        if not client:
            return []

        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.functions.messages import GetHistoryRequest
        from telethon.tl.types import InputPeerChannel

        results = []
        discovered_usernames: set[str] = set()

        # Get seed channels from our existing catalog (top 50 by member count)
        # For now, use the seen_usernames as seeds
        seed_usernames = list(self._seen_usernames)[:50]

        for seed_username in seed_usernames:
            if len(results) >= limit:
                break

            try:
                entity = await client.get_entity(seed_username)
                if not hasattr(entity, "broadcast") or not entity.broadcast:
                    continue

                # Check description for t.me links
                try:
                    full = await client(GetFullChannelRequest(entity))
                    about = full.full_chat.about or ""
                    for match in _TG_LINK_RE.finditer(about):
                        discovered_usernames.add(match.group(1).lower())
                except Exception:
                    pass

                # Check recent messages for forwards
                try:
                    history = await client(GetHistoryRequest(
                        peer=entity, limit=20, offset_date=None,
                        offset_id=0, max_id=0, min_id=0, add_offset=0, hash=0,
                    ))
                    for msg in history.messages:
                        # Forward sources
                        if hasattr(msg, "fwd_from") and msg.fwd_from:
                            fwd = msg.fwd_from
                            if hasattr(fwd, "from_id") and fwd.from_id:
                                try:
                                    fwd_entity = await client.get_entity(fwd.from_id)
                                    if hasattr(fwd_entity, "username") and fwd_entity.username:
                                        discovered_usernames.add(fwd_entity.username.lower())
                                except Exception:
                                    pass

                        # t.me links in message text
                        text = getattr(msg, "message", "") or ""
                        for match in _TG_LINK_RE.finditer(text):
                            discovered_usernames.add(match.group(1).lower())
                except Exception:
                    pass

            except Exception as e:
                log.debug(f"[Snowball] Failed on {seed_username}: {e}")

            await asyncio.sleep(2.0)

        # Validate discovered channels
        for username in discovered_usernames:
            if len(results) >= limit:
                break
            if username in self._seen_usernames:
                continue

            try:
                entity = await client.get_entity(username)
                if not hasattr(entity, "broadcast") or not entity.broadcast:
                    continue
                if (getattr(entity, "participants_count", 0) or 0) < min_members:
                    continue

                has_comments = False
                try:
                    full = await client(GetFullChannelRequest(entity))
                    has_comments = bool(full.full_chat.linked_chat_id)
                except Exception:
                    pass

                ch = DiscoveredChannel(
                    username=entity.username,
                    title=getattr(entity, "title", ""),
                    member_count=getattr(entity, "participants_count", 0) or 0,
                    has_comments=has_comments,
                    language=self._detect_language(
                        getattr(entity, "title", ""), ""
                    ),
                    region="ru",
                    source_method="snowball",
                )
                results.append(ch)
            except Exception:
                pass

            await asyncio.sleep(1.5)

        return results

    # ── Method 4: Global message search ───────────────────────────

    async def _discover_global_search(
        self, keywords: list[str], limit: int, min_members: int
    ) -> list[DiscoveredChannel]:
        """Search globally for messages, extract unique channel sources.

        Uses messages.SearchGlobalRequest — finds channels by their content.
        """
        if not self._sm:
            return []

        client = await self._get_telethon_client()
        if not client:
            return []

        from telethon.tl.functions.messages import SearchGlobalRequest
        from telethon.tl.types import InputMessagesFilterEmpty, InputPeerEmpty

        results = []
        found_channel_ids: set[int] = set()

        for keyword in keywords:
            if len(results) >= limit:
                break

            try:
                search_result = await client(SearchGlobalRequest(
                    q=keyword,
                    filter=InputMessagesFilterEmpty(),
                    min_date=None,
                    max_date=None,
                    offset_rate=0,
                    offset_peer=InputPeerEmpty(),
                    offset_id=0,
                    limit=50,
                ))

                # Extract unique channels from messages
                chats_map = {getattr(c, "id", 0): c for c in search_result.chats}

                for msg in search_result.messages:
                    peer_id = getattr(msg, "peer_id", None)
                    if not peer_id:
                        continue
                    channel_id = getattr(peer_id, "channel_id", None)
                    if not channel_id or channel_id in found_channel_ids:
                        continue

                    chat = chats_map.get(channel_id)
                    if not chat:
                        continue
                    if not hasattr(chat, "broadcast") or not chat.broadcast:
                        continue
                    if not hasattr(chat, "username") or not chat.username:
                        continue

                    username = chat.username
                    if username.lower() in self._seen_usernames:
                        continue
                    if (getattr(chat, "participants_count", 0) or 0) < min_members:
                        continue

                    found_channel_ids.add(channel_id)
                    ch = DiscoveredChannel(
                        username=username,
                        title=getattr(chat, "title", ""),
                        member_count=getattr(chat, "participants_count", 0) or 0,
                        has_comments=True,  # Will validate later
                        language=self._detect_language(
                            getattr(chat, "title", ""), ""
                        ),
                        region="ru",
                        source_method="global_search",
                    )
                    results.append(ch)

            except Exception as e:
                log.error(f"[GlobalSearch] Failed for '{keyword}': {e}")

            await asyncio.sleep(5.0)

        return results

    # ── Helpers ────────────────────────────────────────────────────

    async def _get_telethon_client(self):
        """Get a connected Telethon client from session manager."""
        if not self._sm:
            return None
        try:
            client = await self._sm.get_any_connected_client()
            return client
        except Exception as e:
            log.warning(f"[SmartDiscovery] No Telethon client available: {e}")
            return None

    @staticmethod
    def _detect_language(title: str, about: str) -> str:
        """Detect language from Cyrillic presence."""
        if _RU_TEXT_RE.search(f"{title}\n{about}"):
            return "ru"
        return "en"

    async def save_to_catalog(
        self,
        channels: list[DiscoveredChannel],
        session: AsyncSession,
        tenant_id: int | None = None,
    ) -> int:
        """Save discovered channels to channel_map_entries.

        Args:
            channels: List of discovered channels
            session: DB session
            tenant_id: None for platform catalog, int for tenant-specific

        Returns:
            Number of channels inserted
        """
        inserted = 0
        now = datetime.now(timezone.utc)

        for ch in channels:
            # Check if already exists
            existing = await session.execute(
                select(ChannelMapEntry.id).where(
                    and_(
                        func.lower(ChannelMapEntry.username) == ch.username.lower(),
                        ChannelMapEntry.tenant_id.is_(tenant_id)
                        if tenant_id is None
                        else ChannelMapEntry.tenant_id == tenant_id,
                    )
                )
            )
            if existing.first():
                continue

            entry = ChannelMapEntry(
                tenant_id=tenant_id,
                username=ch.username,
                title=ch.title,
                description=ch.description,
                category=ch.category or None,
                language=ch.language,
                region=ch.region,
                member_count=ch.member_count,
                has_comments=ch.has_comments,
                comments_enabled=ch.has_comments,
                source=ch.source_method,
                verified=False,
                last_indexed_at=now,
            )
            session.add(entry)
            inserted += 1

        if inserted:
            await session.flush()

        log.info(f"[SmartDiscovery] Saved {inserted} channels to catalog (tenant_id={tenant_id})")
        return inserted
