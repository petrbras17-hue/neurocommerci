#!/usr/bin/env python3
"""
Mass TGStat Parser — scrape 500K+ real Telegram channels from TGStat.ru.

TGStat indexes 2.7M+ channels. We scrape their public category pages
and search results to build a real channel database.

Sources:
1. TGStat API (if token available) — fastest, cleanest
2. TGStat category pages scraping — no token needed, 50 channels/page
3. TGStat search scraping — keyword-based discovery

Usage:
    # With TGStat API token (fastest):
    TGSTAT_TOKEN=xxx python scripts/mass_tgstat_parser.py --method api --target 500000

    # Without token (scraping):
    python scripts/mass_tgstat_parser.py --method scrape --target 500000

    # Hybrid: API + scraping + Telethon snowball:
    python scripts/mass_tgstat_parser.py --method hybrid --target 500000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from storage.sqlite_db import async_session
from storage.models import ChannelMapEntry
from utils.logger import log

# ── TGStat categories ──────────────────────────────────────────────
# These are TGStat's internal category slugs
TGSTAT_CATEGORIES = {
    "news": "News",
    "politics": "Politics",
    "economics": "Finance",
    "business": "Business",
    "marketing": "Marketing",
    "technologies": "Tech",
    "crypto": "Crypto",
    "education": "EdTech",
    "entertainment": "Entertainment",
    "humor": "Humor",
    "music": "Music",
    "films": "Movies",
    "games": "Gaming",
    "sport": "Sports",
    "travel": "Travel",
    "food": "Food",
    "health": "Health",
    "fitness": "Health",
    "fashion": "Fashion",
    "beauty": "Lifestyle",
    "auto": "Auto",
    "realestate": "Real Estate",
    "job": "HR",
    "law": "Legal",
    "science": "Science",
    "nature": "Science",
    "animals": "Pets",
    "family": "Parenting",
    "handmade": "DIY",
    "photography": "Photography",
    "design": "Design",
    "art": "Design",
    "books": "Books",
    "psychology": "Motivation",
    "motivation": "Motivation",
    "religion": "Lifestyle",
    "other": "Lifestyle",
    "18plus": "18+",
}

# TGStat country codes → our region codes
TGSTAT_COUNTRIES = {
    "russia": "ru",
    "ukraine": "ua",
    "belarus": "by",
    "kazakhstan": "kz",
    "uzbekistan": "uz",
    "worldwide": "other",
    "usa": "us",
    "india": "in",
    "brazil": "br",
    "turkey": "tr",
    "iran": "ir",
    "united_kingdom": "gb",
    "germany": "de",
    "france": "fr",
    "spain": "es",
    "italy": "it",
    "indonesia": "id",
}

# Geo coordinates for regions
REGION_COORDS = {
    "ru": (55.75, 37.62), "ua": (50.45, 30.52), "kz": (51.17, 71.43),
    "by": (53.90, 27.57), "uz": (41.31, 69.28), "us": (38.90, -77.04),
    "gb": (51.51, -0.13), "de": (52.52, 13.41), "fr": (48.86, 2.35),
    "es": (40.42, -3.70), "it": (41.90, 12.50), "br": (-15.79, -47.88),
    "in": (28.61, 77.21), "tr": (41.01, 28.98), "ir": (35.69, 51.39),
    "id": (-6.21, 106.85), "other": (30.0, 20.0),
}

# ── Keywords for search-based discovery ─────────────────────────────
SEARCH_KEYWORDS_RU = [
    # News & politics
    "новости", "политика", "экономика", "аналитика", "сводки",
    # Tech
    "технологии", "программирование", "разработка", "Python", "JavaScript",
    "AI", "нейросети", "ChatGPT", "стартап", "инновации",
    # Crypto
    "криптовалюта", "биткоин", "эфириум", "DeFi", "NFT", "трейдинг",
    # Business
    "бизнес", "предпринимательство", "инвестиции", "маркетинг", "продажи",
    "SMM", "реклама", "контент", "PR", "брендинг",
    # E-commerce
    "Wildberries", "Ozon", "маркетплейс", "товары", "dropshipping",
    # Education
    "образование", "курсы", "университет", "учёба", "наука",
    # Entertainment
    "мемы", "юмор", "приколы", "кино", "сериалы", "музыка",
    # Gaming
    "игры", "CS2", "Dota", "Minecraft", "стримы", "киберспорт",
    # Lifestyle
    "здоровье", "фитнес", "рецепты", "кулинария", "мода",
    "путешествия", "туризм", "авто", "недвижимость",
    # Professional
    "вакансии", "работа", "карьера", "фриланс", "удалёнка",
    "дизайн", "UI/UX", "фотография", "видео",
    # Society
    "психология", "саморазвитие", "мотивация", "книги",
]

SEARCH_KEYWORDS_EN = [
    "news", "politics", "tech", "programming", "AI", "crypto",
    "bitcoin", "startup", "business", "marketing", "ecommerce",
    "education", "science", "entertainment", "gaming", "music",
    "sports", "travel", "food", "health", "fitness", "fashion",
]

# User agents for scraping
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
]


def _jitter_coord(lat: float, lng: float, spread: float = 5.0) -> tuple[float, float]:
    return (
        max(-85, min(85, lat + random.uniform(-spread, spread))),
        max(-180, min(180, lng + random.uniform(-spread, spread))),
    )


class MassTGStatParser:
    """Mass channel parser using TGStat as primary source."""

    def __init__(self, tgstat_token: str | None = None):
        self.token = tgstat_token
        self.channels: dict[str, dict] = {}  # username -> data
        self.stats = {"api": 0, "scrape": 0, "search": 0, "total": 0}

    async def run(self, target: int = 500_000, method: str = "hybrid"):
        """Run the mass parsing pipeline."""
        log.info(f"Starting mass parse: target={target:,}, method={method}")

        if method in ("api", "hybrid") and self.token:
            await self._parse_via_api(target)

        if method in ("scrape", "hybrid"):
            remaining = target - len(self.channels)
            if remaining > 0:
                await self._parse_via_scrape(remaining)

        if method == "hybrid":
            remaining = target - len(self.channels)
            if remaining > 0:
                await self._parse_via_search(remaining)

        self.stats["total"] = len(self.channels)
        log.info(f"Parse complete: {len(self.channels):,} channels")
        log.info(f"Stats: {self.stats}")
        return list(self.channels.values())

    # ── Method 1: TGStat API ──────────────────────────────────────

    async def _parse_via_api(self, target: int):
        """Use TGStat Search API to find channels."""
        if not self.token:
            return

        log.info("[API] Starting TGStat API parsing...")
        base_url = "https://api.tgstat.ru/channels/search"

        async with aiohttp.ClientSession() as http:
            for country_slug, region in TGSTAT_COUNTRIES.items():
                if len(self.channels) >= target:
                    break

                for cat_slug, our_cat in TGSTAT_CATEGORIES.items():
                    if len(self.channels) >= target:
                        break

                    offset = 0
                    while offset < 1000:  # TGStat paginates up to ~1000
                        params = {
                            "token": self.token,
                            "category": cat_slug,
                            "country": country_slug,
                            "participants_count": "10000;",
                            "limit": 50,
                            "offset": offset,
                            "sort": "members",
                        }

                        try:
                            async with http.get(base_url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                                if resp.status == 429:
                                    log.warning("[API] Rate limited, waiting 30s...")
                                    await asyncio.sleep(30)
                                    continue
                                if resp.status != 200:
                                    break
                                data = await resp.json()

                            items = data.get("response", {}).get("items", [])
                            if not items:
                                break

                            for item in items:
                                self._add_channel_from_api(item, our_cat, region)

                            offset += 50
                            self.stats["api"] += len(items)

                            if len(self.channels) % 5000 == 0:
                                log.info(f"[API] {len(self.channels):,} channels so far...")

                        except Exception as e:
                            log.error(f"[API] Error: {e}")
                            break

                        await asyncio.sleep(1.0)  # TGStat rate limit

        log.info(f"[API] Done: {self.stats['api']} channels from API")

    def _add_channel_from_api(self, item: dict, category: str, region: str):
        """Add a channel from TGStat API response."""
        username = (item.get("username") or "").lstrip("@").lower()
        if not username or username in self.channels:
            return

        coords = REGION_COORDS.get(region, (30.0, 20.0))
        spread = 8.0 if region in ("ru", "us", "in", "br") else 4.0
        lat, lng = _jitter_coord(*coords, spread=spread)

        title = item.get("title", "")
        desc = item.get("description", "")

        self.channels[username] = {
            "username": username,
            "title": title[:500],
            "description": desc[:2000],
            "category": category,
            "language": self._detect_lang(title, desc),
            "region": region,
            "member_count": item.get("participants_count", 0),
            "has_comments": True,
            "comments_enabled": True,
            "avg_post_reach": item.get("avg_post_reach"),
            "engagement_rate": item.get("er"),
            "verified": bool(item.get("verified")),
            "source": "tgstat_api",
            "lat": lat,
            "lng": lng,
        }

    # ── Method 2: TGStat web scraping ─────────────────────────────

    async def _parse_via_scrape(self, target: int):
        """Scrape TGStat.ru category pages."""
        log.info(f"[Scrape] Starting TGStat web scraping (target: {target:,})...")

        # TGStat rating pages: https://tgstat.ru/ratings/channels
        # Category pages: https://tgstat.ru/news, https://tgstat.ru/crypto, etc.
        base_urls = []

        for cat_slug in TGSTAT_CATEGORIES:
            for country_slug in ["", "/russia", "/ukraine", "/belarus", "/kazakhstan"]:
                base_urls.append(
                    (f"https://tgstat.ru/{cat_slug}{country_slug}", cat_slug, country_slug)
                )

        async with aiohttp.ClientSession(headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }) as http:
            for url, cat_slug, country_slug in base_urls:
                if len(self.channels) >= self.stats.get("api", 0) + target:
                    break

                page = 1
                max_pages = 200  # 50 channels per page × 200 = 10K per category

                while page <= max_pages:
                    page_url = f"{url}?page={page}" if page > 1 else url

                    try:
                        async with http.get(page_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                            if resp.status == 429:
                                log.warning("[Scrape] Rate limited, waiting 60s...")
                                await asyncio.sleep(60)
                                continue
                            if resp.status != 200:
                                break
                            html = await resp.text()

                        count = self._parse_tgstat_html(html, cat_slug, country_slug)
                        if count == 0:
                            break  # No more channels on this page

                        self.stats["scrape"] += count
                        page += 1

                        if len(self.channels) % 10000 == 0:
                            log.info(f"[Scrape] {len(self.channels):,} channels so far...")

                    except Exception as e:
                        log.error(f"[Scrape] Error on {page_url}: {e}")
                        break

                    # Polite scraping: 2-5s between pages
                    await asyncio.sleep(2.0 + random.uniform(0, 3))

        log.info(f"[Scrape] Done: {self.stats['scrape']} channels from scraping")

    def _parse_tgstat_html(self, html: str, cat_slug: str, country_slug: str) -> int:
        """Extract channels from TGStat HTML page.

        TGStat channel cards have patterns like:
        - <a href="/channel/@username">
        - data-participants="12345"
        - Channel title in card header
        """
        count = 0
        our_cat = TGSTAT_CATEGORIES.get(cat_slug, "Other")
        region = "ru"  # default
        for cs, r in [("/russia", "ru"), ("/ukraine", "ua"), ("/belarus", "by"), ("/kazakhstan", "kz")]:
            if cs in country_slug:
                region = r
                break

        # Pattern 1: channel card links
        # <a href="/channel/@username" class="...">
        username_pattern = re.compile(
            r'href="/channel/@([a-zA-Z_][a-zA-Z0-9_]{3,31})"', re.IGNORECASE
        )
        # Pattern 2: participant counts
        member_pattern = re.compile(
            r'data-participants="(\d+)"'
        )
        # Pattern 3: channel titles
        title_pattern = re.compile(
            r'class="[^"]*channel-card[^"]*"[^>]*>.*?<h[2-5][^>]*>([^<]+)</h',
            re.DOTALL
        )

        usernames = username_pattern.findall(html)
        members_list = member_pattern.findall(html)

        for i, username in enumerate(usernames):
            username_lower = username.lower()
            if username_lower in self.channels:
                continue

            member_count = int(members_list[i]) if i < len(members_list) else 0
            if member_count < 1000:
                continue

            coords = REGION_COORDS.get(region, (30.0, 20.0))
            lat, lng = _jitter_coord(*coords, spread=5.0)

            self.channels[username_lower] = {
                "username": username_lower,
                "title": username,  # Will be enriched later
                "description": "",
                "category": our_cat,
                "language": "ru" if region in ("ru", "by", "kz", "uz") else "en",
                "region": region,
                "member_count": member_count,
                "has_comments": True,
                "comments_enabled": True,
                "source": "tgstat_scrape",
                "lat": lat,
                "lng": lng,
            }
            count += 1

        return count

    # ── Method 3: Search-based discovery ──────────────────────────

    async def _parse_via_search(self, target: int):
        """Discover channels via TGStat search."""
        log.info(f"[Search] Starting keyword search (target: {target:,})...")

        search_url = "https://tgstat.ru/search"
        keywords = SEARCH_KEYWORDS_RU + SEARCH_KEYWORDS_EN

        async with aiohttp.ClientSession(headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml",
        }) as http:
            for keyword in keywords:
                if self.stats["search"] >= target:
                    break

                try:
                    params = {"q": keyword, "type": "channels"}
                    async with http.get(search_url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            continue
                        html = await resp.text()

                    count = self._parse_tgstat_html(html, "", "")
                    self.stats["search"] += count

                    if self.stats["search"] % 5000 == 0:
                        log.info(f"[Search] {self.stats['search']:,} from search...")

                except Exception as e:
                    log.error(f"[Search] Error for '{keyword}': {e}")

                await asyncio.sleep(3.0 + random.uniform(0, 2))

        log.info(f"[Search] Done: {self.stats['search']} channels from search")

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _detect_lang(title: str, desc: str) -> str:
        text = f"{title} {desc}"
        if re.search(r'[А-Яа-яЁё]', text):
            return "ru"
        if re.search(r'[\u0600-\u06FF]', text):  # Arabic
            return "ar"
        if re.search(r'[\u0900-\u097F]', text):  # Hindi
            return "hi"
        return "en"

    # ── DB operations ─────────────────────────────────────────────

    async def save_to_db(self, batch_size: int = 5000):
        """Save all parsed channels to PostgreSQL."""
        channels = list(self.channels.values())
        total = len(channels)
        inserted = 0

        for i in range(0, total, batch_size):
            batch = channels[i:i + batch_size]
            async with async_session() as session:
                await session.execute(text("SET LOCAL app.bootstrap = '1'"))

                for ch in batch:
                    # Check for existing
                    existing = await session.execute(
                        select(ChannelMapEntry.id).where(
                            func.lower(ChannelMapEntry.username) == ch["username"].lower()
                        ).where(ChannelMapEntry.tenant_id.is_(None))
                    )
                    if existing.first():
                        continue

                    entry = ChannelMapEntry(
                        tenant_id=None,
                        username=ch["username"][:200],
                        title=ch.get("title", "")[:500],
                        description=ch.get("description", "")[:2000],
                        category=ch.get("category")[:100] if ch.get("category") else None,
                        language=ch.get("language", "ru")[:10],
                        region=ch.get("region", "ru")[:10],
                        member_count=ch.get("member_count", 0),
                        has_comments=ch.get("has_comments", True),
                        comments_enabled=ch.get("comments_enabled", True),
                        avg_post_reach=ch.get("avg_post_reach"),
                        engagement_rate=ch.get("engagement_rate"),
                        verified=ch.get("verified", False),
                        source=ch.get("source", "tgstat"),
                        lat=ch.get("lat"),
                        lng=ch.get("lng"),
                    )
                    session.add(entry)
                    inserted += 1

                await session.commit()
                log.info(f"Saved {inserted:,} / {total:,}")

        log.info(f"Total saved: {inserted:,} channels")
        return inserted

    def save_to_json(self, path: str = "data/parsed_channels.json"):
        """Save to JSON for backup/transfer."""
        channels = list(self.channels.values())
        with open(path, "w") as f:
            json.dump(channels, f, ensure_ascii=False, indent=None)
        log.info(f"Saved {len(channels):,} channels to {path}")


async def main():
    parser = argparse.ArgumentParser(description="Mass TGStat Parser")
    parser.add_argument("--target", type=int, default=500_000)
    parser.add_argument("--method", choices=["api", "scrape", "hybrid"], default="hybrid")
    parser.add_argument("--save-json", action="store_true")
    parser.add_argument("--save-db", action="store_true", default=True)
    args = parser.parse_args()

    token = os.getenv("TGSTAT_TOKEN")
    if token:
        log.info(f"TGStat API token found")
    else:
        log.info("No TGSTAT_TOKEN — will use scraping only")

    mass_parser = MassTGStatParser(tgstat_token=token)
    channels = await mass_parser.run(target=args.target, method=args.method)

    if args.save_json:
        mass_parser.save_to_json()

    if args.save_db:
        await mass_parser.save_to_db()

    # Print stats
    by_region = {}
    by_category = {}
    for ch in channels:
        by_region[ch["region"]] = by_region.get(ch["region"], 0) + 1
        by_category[ch.get("category", "?")] = by_category.get(ch.get("category", "?"), 0) + 1

    log.info(f"\n=== PARSE RESULTS ===")
    log.info(f"Total: {len(channels):,}")
    log.info(f"Regions: {len(by_region)}")
    for r, c in sorted(by_region.items(), key=lambda x: -x[1])[:15]:
        log.info(f"  {r}: {c:,}")
    log.info(f"Categories: {len(by_category)}")
    for cat, c in sorted(by_category.items(), key=lambda x: -x[1])[:15]:
        log.info(f"  {cat}: {c:,}")


if __name__ == "__main__":
    asyncio.run(main())
