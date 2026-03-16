#!/usr/bin/env python3
"""Import 650K Telegram channels from Kaggle CSV dataset into channel_map_entries.

Source: kaggle.com/datasets/alexandervarlamov/public-telegram-channels
Format: 43 CSV files by category, TSV separated

Usage:
  python scripts/import_kaggle_channels.py --min-subscribers 5000
  python scripts/import_kaggle_channels.py --min-subscribers 1000 --limit 50000
  python scripts/import_kaggle_channels.py --dry-run --min-subscribers 10000
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
import time
import random
from datetime import datetime

# Координаты стран для глобуса (react-globe.gl)
COUNTRY_COORDS: dict[str, tuple[float, float]] = {
    "RU": (55.75, 37.62), "UA": (50.45, 30.52), "BY": (53.90, 27.57),
    "KZ": (51.17, 71.43), "UZ": (41.30, 69.28), "KG": (42.87, 74.59),
    "TJ": (38.56, 68.77), "AZ": (40.41, 49.87), "GE": (41.72, 44.79),
    "AM": (40.18, 44.51), "MD": (47.01, 28.86), "TM": (37.95, 58.38),
    "US": (39.83, -98.58), "GB": (51.51, -0.13), "DE": (52.52, 13.41),
    "FR": (48.86, 2.35), "IT": (41.90, 12.50), "ES": (40.42, -3.70),
    "PT": (38.72, -9.14), "NL": (52.37, 4.90), "BE": (50.85, 4.35),
    "CH": (46.95, 7.45), "AT": (48.21, 16.37), "PL": (52.23, 21.01),
    "CZ": (50.08, 14.44), "SE": (59.33, 18.07), "NO": (59.91, 10.75),
    "FI": (60.17, 24.94), "DK": (55.68, 12.57), "IE": (53.35, -6.26),
    "TR": (41.01, 28.98), "IR": (35.69, 51.39), "IQ": (33.31, 44.37),
    "SA": (24.71, 46.68), "AE": (25.20, 55.27), "IL": (31.77, 35.22),
    "EG": (30.04, 31.24), "MA": (33.97, -6.85), "NG": (9.08, 7.49),
    "ZA": (33.93, 18.42), "KE": (1.29, 36.82), "ET": (9.03, 38.75),
    "IN": (28.61, 77.21), "PK": (33.69, 73.04), "BD": (23.81, 90.41),
    "LK": (6.93, 79.85), "NP": (27.72, 85.32), "CN": (39.91, 116.40),
    "JP": (35.68, 139.69), "KR": (37.57, 126.98), "TW": (25.03, 121.57),
    "HK": (22.32, 114.17), "SG": (1.35, 103.82), "MY": (3.14, 101.69),
    "TH": (13.76, 100.50), "VN": (21.03, 105.85), "ID": (6.21, 106.85),
    "PH": (14.60, 120.98), "MM": (16.87, 96.20),
    "BR": (-15.79, -47.88), "MX": (19.43, -99.13), "AR": (-34.60, -58.38),
    "CO": (4.71, -74.07), "CL": (-33.45, -70.67), "PE": (-12.05, -77.04),
    "VE": (10.48, -66.90), "EC": (-0.18, -78.47),
    "AU": (-33.87, 151.21), "NZ": (-41.29, 174.78),
    "CA": (45.42, -75.70), "CU": (23.11, -82.37),
    # СНГ / постсовет
    "Global": (20.0, 0.0), "CIS": (55.0, 60.0),
    "EU": (50.0, 10.0), "LATAM": (-10.0, -55.0),
    "MENA": (28.0, 40.0), "SEA": (5.0, 110.0),
    "Africa": (5.0, 20.0),
}

# Маппинг категорий Kaggle → наши категории
CATEGORY_MAP: dict[str, str] = {
    "tech": "tech", "crypto": "crypto", "news": "news",
    "entertainment": "entertainment", "education": "education",
    "business": "business", "beauty": "lifestyle", "sport": "entertainment",
    "music": "entertainment", "games": "gaming", "science": "science",
    "politics": "politics", "art": "entertainment", "books": "education",
    "design": "tech", "career": "business", "food": "lifestyle",
    "health": "lifestyle", "psychology": "education", "humor": "entertainment",
    "travels": "lifestyle", "marketing": "business", "sales": "business",
    "economics": "business", "courses": "education", "blogs": "lifestyle",
    "construction": "business", "apps": "tech", "transport": "tech",
    "fashion": "lifestyle", "nature": "science", "religion": "politics",
    "babies": "lifestyle", "handmade": "lifestyle", "real_estate": "business",
    "erotic": "entertainment", "video": "entertainment", "telegram": "tech",
    "law": "business", "medicine": "science", "pets": "lifestyle",
    "languages": "education", "other": "other",
}


def get_coords(country: str) -> tuple[float, float]:
    """Получить координаты по стране с jitter ±2° для глобуса."""
    base = COUNTRY_COORDS.get(country, COUNTRY_COORDS.get("Global", (20.0, 0.0)))
    jitter_lat = random.uniform(-2.0, 2.0)
    jitter_lng = random.uniform(-2.0, 2.0)
    return (
        max(-85, min(85, base[0] + jitter_lat)),
        max(-180, min(180, base[1] + jitter_lng)),
    )


def parse_subscribers(val: str) -> int:
    """Парсинг числа подписчиков (может быть строкой с пробелами)."""
    try:
        return int(str(val).strip().replace(" ", "").replace(",", "").replace("\xa0", ""))
    except (ValueError, TypeError):
        return 0


def main():
    parser = argparse.ArgumentParser(description="Import Kaggle Telegram channels into DB")
    parser.add_argument("--csv-dir", default="data/kaggle_channels", help="Directory with CSV files")
    parser.add_argument("--min-subscribers", type=int, default=5000, help="Min subscribers filter")
    parser.add_argument("--limit", type=int, default=0, help="Max channels to import (0=all)")
    parser.add_argument("--dry-run", action="store_true", help="Count only, don't insert")
    parser.add_argument("--batch-size", type=int, default=500, help="DB batch commit size")
    args = parser.parse_args()

    csv_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), args.csv_dir)
    csv_files = sorted(glob.glob(os.path.join(csv_dir, "*_channels.csv")))

    if not csv_files:
        print(f"Нет CSV файлов в {csv_dir}")
        sys.exit(1)

    print(f"Найдено {len(csv_files)} CSV файлов в {csv_dir}")
    print(f"Фильтр: подписчиков >= {args.min_subscribers}")
    if args.limit:
        print(f"Лимит: {args.limit} каналов")
    print()

    # Фаза 1: подсчёт и сбор каналов
    channels = []
    seen_handles = set()
    stats = {"total_rows": 0, "passed_filter": 0, "duplicates": 0, "by_category": {}, "by_country": {}}

    for csv_file in csv_files:
        cat_name = os.path.basename(csv_file).replace("_channels.csv", "")
        try:
            with open(csv_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f, delimiter="\t")
                for row in reader:
                    stats["total_rows"] += 1
                    subs = parse_subscribers(row.get("subscribers", "0"))
                    if subs < args.min_subscribers:
                        continue
                    handle = (row.get("handle") or "").strip()
                    if not handle or handle in seen_handles:
                        stats["duplicates"] += 1
                        continue
                    seen_handles.add(handle)

                    country = (row.get("country") or "Global").strip()
                    our_category = CATEGORY_MAP.get(cat_name, "other")
                    lat, lng = get_coords(country)

                    channels.append({
                        "username": handle.lstrip("@"),
                        "title": (row.get("name") or handle).strip()[:500],
                        "description": (row.get("description") or "")[:2000],
                        "category": our_category,
                        "language": "",  # Kaggle не даёт язык, определим позже
                        "region": _country_to_region(country),
                        "member_count": subs,
                        "lat": lat,
                        "lng": lng,
                        "avatar_url": (row.get("image") or "").strip()[:500] or None,
                        "source": "kaggle",
                        "country": country,
                    })

                    stats["passed_filter"] += 1
                    stats["by_category"][our_category] = stats["by_category"].get(our_category, 0) + 1
                    stats["by_country"][country] = stats["by_country"].get(country, 0) + 1

                    if args.limit and len(channels) >= args.limit:
                        break
        except Exception as e:
            print(f"  Ошибка в {csv_file}: {e}")
            continue

        if args.limit and len(channels) >= args.limit:
            break

    # Статистика
    print(f"═══════════════════════════════════════")
    print(f"  Всего строк:     {stats['total_rows']:,}")
    print(f"  Прошли фильтр:   {stats['passed_filter']:,}")
    print(f"  Дубликаты:       {stats['duplicates']:,}")
    print(f"  К импорту:       {len(channels):,}")
    print(f"═══════════════════════════════════════")
    print(f"\n  Топ категорий:")
    for cat, cnt in sorted(stats["by_category"].items(), key=lambda x: -x[1])[:10]:
        print(f"    {cat:20s} {cnt:>8,}")
    print(f"\n  Топ стран:")
    for country, cnt in sorted(stats["by_country"].items(), key=lambda x: -x[1])[:15]:
        print(f"    {country:20s} {cnt:>8,}")

    if args.dry_run:
        print(f"\n  [DRY RUN] Импорт не выполнен.")
        return

    # Фаза 2: импорт в БД
    print(f"\n  Импортирую {len(channels):,} каналов в channel_map_entries...")
    _import_to_db(channels, args.batch_size)


def _country_to_region(country: str) -> str:
    """Маппинг страны в регион."""
    cis = {"RU", "UA", "BY", "KZ", "UZ", "KG", "TJ", "AZ", "GE", "AM", "MD", "TM"}
    eu = {"GB", "DE", "FR", "IT", "ES", "PT", "NL", "BE", "CH", "AT", "PL", "CZ", "SE", "NO", "FI", "DK", "IE", "GR", "RO", "HU", "BG", "HR", "SK", "SI", "LT", "LV", "EE"}
    mena = {"TR", "IR", "IQ", "SA", "AE", "IL", "EG", "MA", "TN", "LB", "JO", "KW", "QA", "BH", "OM", "YE", "SY", "PS"}
    sea = {"SG", "MY", "TH", "VN", "ID", "PH", "MM", "KH", "LA", "BN"}
    latam = {"BR", "MX", "AR", "CO", "CL", "PE", "VE", "EC", "UY", "PY", "BO", "CR", "PA", "DO", "GT", "HN", "SV", "NI", "CU"}

    if country in cis:
        return "cis"
    elif country in eu:
        return "eu"
    elif country in mena:
        return "mena"
    elif country in sea:
        return "sea"
    elif country in latam:
        return "latam"
    elif country in {"US", "CA"}:
        return "na"
    elif country in {"CN", "JP", "KR", "TW", "HK"}:
        return "asia"
    elif country in {"IN", "PK", "BD", "LK", "NP"}:
        return "south_asia"
    elif country in {"AU", "NZ"}:
        return "oceania"
    else:
        return "global"


def _import_to_db(channels: list[dict], batch_size: int):
    """Импорт каналов в PostgreSQL через SQLAlchemy."""
    import asyncio

    async def _do_import():
        # Используем тот же DB engine что и проект
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from storage.sqlite_db import async_session, init_db
        from storage.models import ChannelMapEntry
        from sqlalchemy import select

        await init_db()

        imported = 0
        skipped = 0
        t0 = time.time()

        for i in range(0, len(channels), batch_size):
            batch = channels[i:i + batch_size]
            async with async_session() as session:
                async with session.begin():
                    for ch in batch:
                        # Dedup по username
                        existing = await session.execute(
                            select(ChannelMapEntry.id).where(
                                ChannelMapEntry.username == ch["username"]
                            ).limit(1)
                        )
                        if existing.scalar_one_or_none() is not None:
                            skipped += 1
                            continue

                        entry = ChannelMapEntry(
                            tenant_id=None,  # platform-level catalog
                            username=ch["username"],
                            title=ch["title"],
                            description=ch["description"],
                            category=ch["category"],
                            language=ch["language"] or None,
                            region=ch["region"],
                            member_count=ch["member_count"],
                            lat=ch["lat"],
                            lng=ch["lng"],
                            avatar_url=ch.get("avatar_url"),
                            source="kaggle",
                            verified=False,
                            has_comments=True,
                            comments_enabled=True,
                        )
                        session.add(entry)
                        imported += 1

            elapsed = time.time() - t0
            rate = imported / elapsed if elapsed > 0 else 0
            print(f"  [{imported + skipped:,}/{len(channels):,}] imported={imported:,} skipped={skipped:,} ({rate:.0f}/s)")

        print(f"\n  ✅ Импорт завершён: {imported:,} добавлено, {skipped:,} пропущено (дубли)")
        print(f"  Время: {time.time() - t0:.1f}s")

    asyncio.run(_do_import())


if __name__ == "__main__":
    main()
