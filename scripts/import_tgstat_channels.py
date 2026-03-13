#!/usr/bin/env python3
"""
Import real Telegram channels from TGStat API into channel_map_entries.

Usage:
    TGSTAT_TOKEN=xxx python scripts/import_tgstat_channels.py [--dry-run] [--limit 5000]

Env vars:
    TGSTAT_TOKEN  — API token from tgstat.ru/my/profile
    DATABASE_URL  — Postgres connection (reads from .env if not set)

Strategy:
    1. Fetch category list from TGStat
    2. For each category × country, search channels with 5K+ subscribers
    3. Upsert into channel_map_entries with source='tgstat'
    4. Assign geo coordinates based on country code
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone

import httpx

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker as async_sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tgstat_import")

# ── TGStat API ────────────────────────────────────────────────────────────────

TGSTAT_BASE = "https://api.tgstat.ru"
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN", "")

# Country code → approximate capital coordinates for globe placement
COUNTRY_GEO = {
    "ru": (55.75, 37.62),    # Moscow
    "ua": (50.45, 30.52),    # Kyiv
    "by": (53.90, 27.57),    # Minsk
    "uz": (41.31, 69.28),    # Tashkent
    "kz": (51.17, 71.43),    # Astana
    "ir": (35.69, 51.39),    # Tehran
    "kg": (42.87, 74.59),    # Bishkek
    # Extended geo for channels without country
    "us": (38.90, -77.04),   # Washington DC
    "gb": (51.51, -0.13),    # London
    "de": (52.52, 13.41),    # Berlin
    "fr": (48.86, 2.35),     # Paris
    "in": (28.61, 77.21),    # New Delhi
    "br": (-15.79, -47.88),  # Brasilia
    "tr": (39.93, 32.86),    # Ankara
    "ae": (25.20, 55.27),    # Dubai
    "il": (31.77, 35.22),    # Jerusalem
    "cn": (39.90, 116.40),   # Beijing
    "jp": (35.68, 139.69),   # Tokyo
    "kr": (37.57, 126.98),   # Seoul
    "pl": (52.23, 21.01),    # Warsaw
    "ge": (41.69, 44.80),    # Tbilisi
    "am": (40.18, 44.51),    # Yerevan
    "az": (40.41, 49.87),    # Baku
    "tj": (38.56, 68.77),    # Dushanbe
    "tm": (37.95, 58.38),    # Ashgabat
    "md": (47.01, 28.86),    # Chisinau
}

# Add jitter to coordinates so channels from same country don't stack
import random
def jittered_geo(country_code: str) -> tuple[float, float] | None:
    base = COUNTRY_GEO.get(country_code)
    if not base:
        return None
    lat = base[0] + random.uniform(-2.5, 2.5)
    lng = base[1] + random.uniform(-2.5, 2.5)
    return (round(lat, 4), round(lng, 4))


# Map TGStat category codes to our category names
CATEGORY_MAP: dict[str, str] = {}  # populated dynamically from API


async def tgstat_get(client: httpx.AsyncClient, endpoint: str, params: dict | None = None) -> dict:
    """Make a TGStat API request with rate-limit handling."""
    p = {"token": TGSTAT_TOKEN}
    if params:
        p.update(params)

    for attempt in range(3):
        resp = await client.get(f"{TGSTAT_BASE}/{endpoint}", params=p, timeout=30)
        if resp.status_code == 429:
            wait = 2 ** attempt + 1
            log.warning("Rate limited, waiting %ds...", wait)
            await asyncio.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "error":
            log.error("TGStat error: %s", data.get("error", data))
            return {"response": None}
        return data

    log.error("Failed after 3 retries: %s", endpoint)
    return {"response": None}


async def fetch_categories(client: httpx.AsyncClient) -> list[dict]:
    """Fetch TGStat category list."""
    data = await tgstat_get(client, "database/categories", {"lang": "en"})
    cats = data.get("response", [])
    if cats:
        for c in cats:
            CATEGORY_MAP[c["code"]] = c["name"]
        log.info("Loaded %d categories: %s", len(cats), [c["code"] for c in cats])
    return cats


async def search_channels(
    client: httpx.AsyncClient,
    category: str | None = None,
    country: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Search TGStat for channels."""
    params: dict = {"limit": min(limit, 100)}
    if category:
        params["category"] = category
    if country:
        params["country"] = country
    if query:
        params["q"] = query

    data = await tgstat_get(client, "channels/search", params)
    resp = data.get("response")
    if not resp:
        return []

    items = resp.get("items", [])
    # Filter to 5K+ subscribers
    return [ch for ch in items if (ch.get("participants_count") or 0) >= 5000]


async def import_channels(dry_run: bool = False, max_total: int = 50000):
    """Main import loop."""
    if not TGSTAT_TOKEN:
        log.error("TGSTAT_TOKEN not set. Get yours at https://tgstat.ru/my/profile")
        sys.exit(1)

    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    # Convert sync URL to async
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(db_url, echo=False)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with httpx.AsyncClient() as client:
        # 1. Fetch categories
        categories = await fetch_categories(client)
        if not categories:
            log.error("No categories returned from TGStat")
            return

        # 2. Countries to crawl (CIS first, then extended)
        countries_priority = ["ru", "ua", "kz", "by", "uz", "ir", "kg"]

        total_imported = 0
        seen_tg_ids: set[int] = set()

        # 3. Iterate category × country
        for country in countries_priority:
            if total_imported >= max_total:
                break

            for cat in categories:
                if total_imported >= max_total:
                    break

                cat_code = cat["code"]
                log.info("Searching: country=%s category=%s", country, cat_code)

                channels = await search_channels(
                    client,
                    category=cat_code,
                    country=country,
                    limit=100,
                )

                if not channels:
                    continue

                batch = []
                for ch in channels:
                    tg_id = ch.get("tg_id") or ch.get("id")
                    if not tg_id or tg_id in seen_tg_ids:
                        continue
                    seen_tg_ids.add(tg_id)

                    geo = jittered_geo(country)

                    entry = {
                        "telegram_id": tg_id,
                        "username": (ch.get("username") or "").lstrip("@") or None,
                        "title": ch.get("title"),
                        "description": (ch.get("about") or "")[:2000] or None,
                        "category": CATEGORY_MAP.get(cat_code, cat_code),
                        "language": None,  # TGStat search doesn't return language per-channel
                        "region": country,
                        "member_count": ch.get("participants_count", 0),
                        "has_comments": False,
                        "comments_enabled": False,
                        "verified": False,
                        "source": "tgstat",
                        "last_indexed_at": datetime.now(timezone.utc),
                        "lat": geo[0] if geo else None,
                        "lng": geo[1] if geo else None,
                        "tenant_id": None,  # platform catalog
                    }
                    batch.append(entry)

                if batch and not dry_run:
                    async with SessionLocal() as session:
                        async with session.begin():
                            # Enable bootstrap mode to bypass RLS for platform catalog inserts
                            await session.execute(text("SET LOCAL app.bootstrap = '1'"))
                            # Use raw INSERT ON CONFLICT for upsert by telegram_id
                            for entry in batch:
                                await session.execute(
                                    text("""
                                        INSERT INTO channel_map_entries
                                            (telegram_id, username, title, description, category,
                                             language, region, member_count, has_comments, comments_enabled,
                                             verified, source, last_indexed_at, lat, lng, tenant_id, created_at)
                                        VALUES
                                            (:telegram_id, :username, :title, :description, :category,
                                             :language, :region, :member_count, :has_comments, :comments_enabled,
                                             :verified, :source, :last_indexed_at, :lat, :lng, :tenant_id, NOW())
                                        ON CONFLICT (telegram_id) WHERE telegram_id IS NOT NULL
                                        DO UPDATE SET
                                            title = EXCLUDED.title,
                                            description = EXCLUDED.description,
                                            member_count = EXCLUDED.member_count,
                                            category = EXCLUDED.category,
                                            source = 'tgstat',
                                            last_indexed_at = NOW()
                                    """),
                                    entry,
                                )

                total_imported += len(batch)
                log.info(
                    "  -> %d channels (total: %d, country=%s, cat=%s)",
                    len(batch), total_imported, country, cat_code,
                )

                # Respect rate limits — 0.5s between requests
                await asyncio.sleep(0.5)

        log.info("Import complete: %d channels imported", total_imported)

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import channels from TGStat API")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--limit", type=int, default=50000, help="Max channels to import")
    args = parser.parse_args()

    asyncio.run(import_channels(dry_run=args.dry_run, max_total=args.limit))
