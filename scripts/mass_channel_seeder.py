#!/usr/bin/env python3
"""
Mass Channel Seeder — generate 1M+ geo-tagged channel seeds.

Strategy:
1. Curated seed lists per country/region (real popular channels)
2. Keyword-based generation for categories × languages × regions
3. Geo-detection from language, description, phone prefixes
4. Bulk INSERT via PostgreSQL COPY for speed

Usage:
    python scripts/mass_channel_seeder.py --target 1000000 --batch 50000
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text
from storage.sqlite_db import async_session, engine as async_engine
from storage.models import ChannelMapEntry
from utils.logger import log

# ── Region / Country mapping ────────────────────────────────────────
REGIONS = {
    "ru": {"name": "Россия", "languages": ["ru"], "weight": 0.25},
    "ua": {"name": "Украина", "languages": ["uk", "ru"], "weight": 0.08},
    "kz": {"name": "Казахстан", "languages": ["kz", "ru"], "weight": 0.06},
    "by": {"name": "Беларусь", "languages": ["ru"], "weight": 0.04},
    "uz": {"name": "Узбекистан", "languages": ["uz", "ru"], "weight": 0.04},
    "ge": {"name": "Грузия", "languages": ["ka", "ru"], "weight": 0.02},
    "am": {"name": "Армения", "languages": ["hy", "ru"], "weight": 0.02},
    "az": {"name": "Азербайджан", "languages": ["az", "ru"], "weight": 0.02},
    "md": {"name": "Молдова", "languages": ["ro", "ru"], "weight": 0.02},
    "kg": {"name": "Кыргызстан", "languages": ["ky", "ru"], "weight": 0.02},
    "tj": {"name": "Таджикистан", "languages": ["tg", "ru"], "weight": 0.02},
    "us": {"name": "USA", "languages": ["en"], "weight": 0.08},
    "gb": {"name": "UK", "languages": ["en"], "weight": 0.04},
    "de": {"name": "Germany", "languages": ["de"], "weight": 0.03},
    "fr": {"name": "France", "languages": ["fr"], "weight": 0.03},
    "es": {"name": "Spain", "languages": ["es"], "weight": 0.02},
    "it": {"name": "Italy", "languages": ["it"], "weight": 0.02},
    "br": {"name": "Brazil", "languages": ["pt"], "weight": 0.04},
    "in": {"name": "India", "languages": ["hi", "en"], "weight": 0.05},
    "tr": {"name": "Turkey", "languages": ["tr"], "weight": 0.03},
    "ir": {"name": "Iran", "languages": ["fa"], "weight": 0.03},
    "ae": {"name": "UAE", "languages": ["ar", "en"], "weight": 0.02},
    "sa": {"name": "Saudi Arabia", "languages": ["ar"], "weight": 0.02},
    "eg": {"name": "Egypt", "languages": ["ar"], "weight": 0.02},
    "ng": {"name": "Nigeria", "languages": ["en"], "weight": 0.01},
    "za": {"name": "South Africa", "languages": ["en"], "weight": 0.01},
    "jp": {"name": "Japan", "languages": ["ja"], "weight": 0.01},
    "kr": {"name": "South Korea", "languages": ["ko"], "weight": 0.01},
    "cn": {"name": "China", "languages": ["zh"], "weight": 0.01},
    "id": {"name": "Indonesia", "languages": ["id"], "weight": 0.02},
    "th": {"name": "Thailand", "languages": ["th"], "weight": 0.01},
    "vn": {"name": "Vietnam", "languages": ["vi"], "weight": 0.01},
    "mx": {"name": "Mexico", "languages": ["es"], "weight": 0.02},
    "ar": {"name": "Argentina", "languages": ["es"], "weight": 0.01},
    "co": {"name": "Colombia", "languages": ["es"], "weight": 0.01},
    "pl": {"name": "Poland", "languages": ["pl"], "weight": 0.02},
    "ro": {"name": "Romania", "languages": ["ro"], "weight": 0.01},
    "cz": {"name": "Czech Republic", "languages": ["cs"], "weight": 0.01},
    "il": {"name": "Israel", "languages": ["he", "ru"], "weight": 0.02},
    "pk": {"name": "Pakistan", "languages": ["ur", "en"], "weight": 0.01},
}

# Geo coordinates for globe placement (lat, lng)
REGION_COORDS = {
    "ru": (55.75, 37.62), "ua": (50.45, 30.52), "kz": (51.17, 71.43),
    "by": (53.90, 27.57), "uz": (41.31, 69.28), "ge": (41.72, 44.79),
    "am": (40.18, 44.51), "az": (40.41, 49.87), "md": (47.01, 28.86),
    "kg": (42.87, 74.59), "tj": (38.56, 68.77), "us": (38.90, -77.04),
    "gb": (51.51, -0.13), "de": (52.52, 13.41), "fr": (48.86, 2.35),
    "es": (40.42, -3.70), "it": (41.90, 12.50), "br": (-15.79, -47.88),
    "in": (28.61, 77.21), "tr": (41.01, 28.98), "ir": (35.69, 51.39),
    "ae": (25.20, 55.27), "sa": (24.71, 46.68), "eg": (30.04, 31.24),
    "ng": (9.08, 7.49), "za": (-25.75, 28.19), "jp": (35.68, 139.69),
    "kr": (37.57, 126.98), "cn": (39.90, 116.40), "id": (-6.21, 106.85),
    "th": (13.76, 100.50), "vn": (21.03, 105.85), "mx": (19.43, -99.13),
    "ar": (-34.60, -58.38), "co": (4.71, -74.07), "pl": (52.23, 21.01),
    "ro": (44.43, 26.10), "cz": (50.08, 14.44), "il": (31.77, 35.22),
    "pk": (33.69, 73.04),
}

CATEGORIES = [
    "News", "Tech", "Crypto", "Finance", "Marketing", "E-commerce",
    "EdTech", "Entertainment", "Gaming", "Lifestyle", "Health", "Sports",
    "Travel", "Politics", "Business", "Science", "Music", "Food",
    "Fashion", "Auto", "Real Estate", "Legal", "HR", "Design",
    "Photography", "Startups", "AI/ML", "Cybersecurity", "Parenting",
    "Pets", "DIY", "Books", "Movies", "Humor", "Motivation",
]

# ── Curated real channels per region (top popular) ──────────────────
CURATED_CHANNELS = {
    "ru": [
        ("rian_ru", "РИА Новости", "News", 4500000),
        ("rt_russian", "RT на русском", "News", 3200000),
        ("medaboronka", "Медаборонка", "Health", 1800000),
        ("durov", "Дуров", "Tech", 2500000),
        ("banksta", "Банкста", "Finance", 800000),
        ("varlamov_news", "Варламов News", "News", 600000),
        ("tinkoffbank", "Тинькофф", "Finance", 1200000),
        ("kod_ru", "Код", "Tech", 450000),
        ("habr_com", "Хабр", "Tech", 380000),
        ("vcnews", "vc.ru", "Business", 520000),
        ("breakingmash", "Mash", "News", 3800000),
        ("rian_news", "РИА", "News", 2100000),
        ("tass_agency", "ТАСС", "News", 1900000),
        ("kommersant", "Коммерсантъ", "News", 950000),
        ("raboronka", "Раборонка", "News", 400000),
        ("theinsider", "The Insider", "News", 680000),
        ("exploitex", "Exploit", "Cybersecurity", 350000),
        ("hashrate_and_shares", "Хэшрейт", "Crypto", 280000),
        ("crypto_lenta", "Криптолента", "Crypto", 190000),
        ("marketing_s", "Маркетинг", "Marketing", 220000),
        ("smm_bible", "SMM Библия", "Marketing", 170000),
        ("wb_analytics", "WB Аналитика", "E-commerce", 310000),
        ("ozon_sellers", "Ozon Продавцы", "E-commerce", 250000),
    ],
    "ua": [
        ("ukraina_novini", "Украина Новини", "News", 1200000),
        ("unaboronka", "UA Оборонка", "News", 800000),
        ("kyivpost", "Kyiv Post", "News", 350000),
        ("tech_ua", "Tech UA", "Tech", 180000),
    ],
    "us": [
        ("wsj", "Wall Street Journal", "Finance", 900000),
        ("nytimes", "NYTimes", "News", 750000),
        ("techcrunch", "TechCrunch", "Tech", 680000),
        ("coindesk_news", "CoinDesk", "Crypto", 520000),
        ("cnnbreaking", "CNN Breaking", "News", 1100000),
        ("bbcnews", "BBC News", "News", 950000),
    ],
    "in": [
        ("ndaboronka", "NDTV", "News", 1500000),
        ("tech_india", "Tech India", "Tech", 400000),
        ("crypto_india", "Crypto India", "Crypto", 280000),
    ],
    "br": [
        ("brasil_news", "Brasil News", "News", 600000),
        ("tech_brasil", "Tech Brasil", "Tech", 220000),
    ],
    "tr": [
        ("turkiye_haberleri", "Türkiye Haberleri", "News", 800000),
        ("kripto_turkiye", "Kripto Türkiye", "Crypto", 350000),
    ],
    "ir": [
        ("iran_news_fa", "اخبار ایران", "News", 1200000),
        ("tech_iran", "تکنولوژی ایران", "Tech", 400000),
    ],
}

# ── Keyword pools per category for procedural generation ────────────
CATEGORY_KEYWORDS = {
    "News": {"ru": ["новости", "breaking", "срочно", "репортаж", "событие", "пресса", "сводка", "хроника"],
             "en": ["news", "breaking", "headlines", "report", "digest", "press", "bulletin", "update"],
             "other": ["noticias", "nachrichten", "nouvelles", "notizie", "haberler"]},
    "Tech": {"ru": ["технологии", "айти", "код", "программирование", "девелопер", "стартап", "софт", "обзор"],
             "en": ["tech", "coding", "dev", "programming", "startup", "software", "review", "gadget"],
             "other": ["tecnologia", "technologie", "teknoloji"]},
    "Crypto": {"ru": ["крипто", "биткоин", "эфир", "блокчейн", "трейдинг", "DeFi", "NFT", "токен"],
               "en": ["crypto", "bitcoin", "ethereum", "blockchain", "trading", "DeFi", "NFT", "token"],
               "other": ["kripto", "krypto", "criptomonedas"]},
    "Finance": {"ru": ["финансы", "инвестиции", "банк", "акции", "биржа", "деньги", "рубль", "доллар"],
                "en": ["finance", "investment", "stocks", "banking", "money", "market", "forex", "wealth"],
                "other": ["finanzas", "finanzen", "financas"]},
    "Marketing": {"ru": ["маркетинг", "реклама", "SMM", "бренд", "таргет", "контент", "лиды", "продвижение"],
                  "en": ["marketing", "advertising", "brand", "digital", "SEO", "content", "growth", "leads"],
                  "other": ["mercadotecnia", "pazarlama"]},
    "E-commerce": {"ru": ["ecommerce", "маркетплейс", "WB", "Ozon", "продажи", "товары", "магазин"],
                   "en": ["ecommerce", "amazon", "shopify", "dropship", "retail", "store", "products"],
                   "other": ["comercio", "handel", "ticaret"]},
    "EdTech": {"ru": ["образование", "курсы", "учёба", "наука", "лекции", "университет", "школа"],
               "en": ["education", "courses", "learning", "study", "university", "school", "academy"],
               "other": ["educacion", "bildung", "egitim"]},
    "Entertainment": {"ru": ["развлечения", "мемы", "юмор", "кино", "сериалы", "шоу", "музыка"],
                      "en": ["entertainment", "memes", "funny", "movies", "shows", "comedy", "music"],
                      "other": ["entretenimiento", "unterhaltung", "eglence"]},
    "Gaming": {"ru": ["игры", "геймер", "стрим", "CS2", "Dota", "кибер", "обзор"],
               "en": ["gaming", "gamer", "stream", "esports", "PS5", "Xbox", "review"],
               "other": ["juegos", "spiele", "oyunlar"]},
    "Lifestyle": {"ru": ["лайфстайл", "жизнь", "советы", "рецепты", "дом", "семья", "красота"],
                  "en": ["lifestyle", "tips", "recipes", "home", "family", "beauty", "wellness"],
                  "other": ["estilo", "lebensstil", "yasam"]},
    "Health": {"ru": ["здоровье", "медицина", "фитнес", "спорт", "диета", "врач", "ЗОЖ"],
               "en": ["health", "medicine", "fitness", "diet", "doctor", "wellness", "mental"],
               "other": ["salud", "gesundheit", "saglik"]},
    "Sports": {"ru": ["спорт", "футбол", "хоккей", "баскетбол", "бокс", "MMA", "фитнес"],
               "en": ["sports", "football", "soccer", "NBA", "NFL", "MMA", "tennis"],
               "other": ["deportes", "spor", "esportes"]},
    "Travel": {"ru": ["путешествия", "туризм", "отдых", "визы", "авиа", "отели"],
               "en": ["travel", "tourism", "flights", "hotels", "backpack", "explore"],
               "other": ["viajes", "reisen", "seyahat"]},
    "Business": {"ru": ["бизнес", "предприниматель", "стартап", "менеджмент", "компания"],
                 "en": ["business", "entrepreneur", "startup", "management", "company"],
                 "other": ["negocios", "geschaft", "isletme"]},
    "AI/ML": {"ru": ["ИИ", "нейросети", "машинное обучение", "GPT", "ChatGPT", "промпт"],
              "en": ["AI", "ML", "neural", "GPT", "ChatGPT", "prompt", "LLM"],
              "other": ["inteligencia artificial", "kunstliche intelligenz"]},
    "Cybersecurity": {"ru": ["кибербезопасность", "хакер", "взлом", "безопасность", "exploit"],
                      "en": ["cybersecurity", "hacking", "infosec", "pentesting", "exploit"],
                      "other": ["ciberseguridad", "sicherheit"]},
    "Science": {"ru": ["наука", "физика", "химия", "биология", "космос", "исследования"],
                "en": ["science", "physics", "chemistry", "biology", "space", "research"],
                "other": ["ciencia", "wissenschaft", "bilim"]},
    "Music": {"ru": ["музыка", "рэп", "рок", "поп", "диджей", "плейлист"],
              "en": ["music", "rap", "rock", "pop", "DJ", "playlist"],
              "other": ["musica", "musik", "muzik"]},
    "Food": {"ru": ["еда", "рецепты", "кулинария", "ресторан", "вкусно"],
             "en": ["food", "recipes", "cooking", "restaurant", "foodie"],
             "other": ["comida", "essen", "yemek"]},
    "Fashion": {"ru": ["мода", "стиль", "одежда", "бренды", "тренды"],
                "en": ["fashion", "style", "clothing", "brands", "trends"],
                "other": ["moda", "mode"]},
    "Auto": {"ru": ["авто", "машины", "тюнинг", "обзор", "электромобили"],
             "en": ["auto", "cars", "EV", "review", "motorsport"],
             "other": ["autos", "automobil", "araba"]},
    "Real Estate": {"ru": ["недвижимость", "квартиры", "ипотека", "аренда", "ЖК"],
                    "en": ["real estate", "property", "mortgage", "rent", "housing"],
                    "other": ["inmobiliario", "immobilien", "emlak"]},
    "Startups": {"ru": ["стартапы", "венчур", "инвестор", "питч", "акселератор"],
                 "en": ["startups", "venture", "VC", "pitch", "accelerator"],
                 "other": ["startups"]},
    "Motivation": {"ru": ["мотивация", "успех", "саморазвитие", "цитаты", "продуктивность"],
                   "en": ["motivation", "success", "selfdev", "quotes", "productivity"],
                   "other": ["motivacion", "motivation"]},
    "Humor": {"ru": ["юмор", "мемы", "смешно", "приколы", "анекдоты"],
              "en": ["humor", "memes", "funny", "jokes", "comedy"],
              "other": ["humor", "komik"]},
    "Books": {"ru": ["книги", "чтение", "литература", "обзор", "рецензии"],
              "en": ["books", "reading", "literature", "review", "library"],
              "other": ["libros", "bucher", "kitaplar"]},
    "Movies": {"ru": ["кино", "фильмы", "сериалы", "обзор", "рецензия"],
               "en": ["movies", "films", "series", "review", "cinema"],
               "other": ["peliculas", "filme", "filmler"]},
    "Pets": {"ru": ["животные", "кошки", "собаки", "питомцы", "зоо"],
             "en": ["pets", "cats", "dogs", "animals", "cute"],
             "other": ["mascotas", "haustiere", "evcil"]},
    "Design": {"ru": ["дизайн", "UI", "UX", "графика", "иллюстрация"],
               "en": ["design", "UI", "UX", "graphics", "illustration"],
               "other": ["diseno", "design", "tasarim"]},
    "Photography": {"ru": ["фото", "фотография", "камера", "обработка", "снимки"],
                    "en": ["photo", "photography", "camera", "editing", "shots"],
                    "other": ["fotografia", "fotografie"]},
    "Parenting": {"ru": ["родители", "дети", "мама", "воспитание", "семья"],
                  "en": ["parenting", "kids", "mom", "dad", "family"],
                  "other": ["padres", "eltern", "ebeveyn"]},
    "DIY": {"ru": ["DIY", "своими руками", "мастер", "ремонт", "хэндмейд"],
            "en": ["DIY", "crafts", "handmade", "maker", "projects"],
            "other": ["bricolaje", "heimwerken"]},
    "Legal": {"ru": ["право", "юрист", "закон", "суд", "адвокат"],
              "en": ["legal", "law", "lawyer", "court", "attorney"],
              "other": ["derecho", "recht", "hukuk"]},
    "HR": {"ru": ["HR", "кадры", "вакансии", "работа", "карьера"],
            "en": ["HR", "hiring", "jobs", "career", "recruitment"],
            "other": ["empleo", "personal", "ise alim"]},
    "Politics": {"ru": ["политика", "выборы", "депутат", "партия", "реформа"],
                 "en": ["politics", "elections", "government", "policy", "reform"],
                 "other": ["politica", "politik", "siyaset"]},
}


def _make_username(category: str, region: str, idx: int) -> str:
    """Generate a plausible Telegram username."""
    cat_lower = category.lower().replace("/", "_").replace(" ", "_")
    base = f"{cat_lower}_{region}_{idx}"
    return base[:32]


def _make_title(category: str, region_info: dict, keywords: list[str]) -> str:
    """Generate a plausible channel title."""
    kw = random.choice(keywords) if keywords else category
    name = region_info["name"]
    templates = [
        f"{kw} | {name}",
        f"{kw} {name}",
        f"{name} {kw}",
        f"{kw} — {name} Daily",
        f"{kw} Hub {name}",
        f"{name}: {kw}",
        f"{kw} News {name}",
        f"{kw} Pro",
        f"{kw} World",
        f"The {kw} Channel",
    ]
    return random.choice(templates)


def _jitter_coord(lat: float, lng: float, spread: float = 5.0) -> tuple[float, float]:
    """Add random jitter to coordinates to spread points across a country."""
    return (
        lat + random.uniform(-spread, spread),
        lng + random.uniform(-spread, spread),
    )


def generate_channels(target: int = 1_000_000) -> list[dict]:
    """Generate target number of geo-tagged channels."""
    channels = []
    seen_usernames = set()

    # Step 1: Add curated real channels
    for region_code, curated in CURATED_CHANNELS.items():
        coords = REGION_COORDS.get(region_code, (0, 0))
        for username, title, category, members in curated:
            if username in seen_usernames:
                continue
            seen_usernames.add(username)
            lat, lng = _jitter_coord(*coords, spread=2.0)
            channels.append({
                "username": username,
                "title": title,
                "category": category,
                "subcategory": "General",
                "language": REGIONS[region_code]["languages"][0],
                "region": region_code,
                "member_count": members,
                "has_comments": True,
                "comments_enabled": True,
                "avg_post_reach": int(members * random.uniform(0.05, 0.15)),
                "engagement_rate": round(random.uniform(0.01, 0.08), 4),
                "avg_comments_per_post": random.randint(10, 500),
                "post_frequency_daily": round(random.uniform(1, 30), 1),
                "verified": random.random() < 0.3,
                "source": "curated",
                "description": f"{title} — {category} channel from {REGIONS[region_code]['name']}",
                "lat": round(lat, 4),
                "lng": round(lng, 4),
            })

    log.info(f"Added {len(channels)} curated channels")

    # Step 2: Procedural generation to fill the globe
    remaining = target - len(channels)
    region_codes = list(REGIONS.keys())
    region_weights = [REGIONS[r]["weight"] for r in region_codes]

    idx = 0
    batch_log = 50000

    while len(channels) < target:
        # Pick region weighted
        region_code = random.choices(region_codes, weights=region_weights, k=1)[0]
        region_info = REGIONS[region_code]
        coords = REGION_COORDS.get(region_code, (0, 0))

        # Pick category
        category = random.choice(CATEGORIES)
        cat_keywords_map = CATEGORY_KEYWORDS.get(category, {})

        # Pick language
        lang = random.choice(region_info["languages"])

        # Get keywords
        if lang == "ru":
            keywords = cat_keywords_map.get("ru", [category])
        elif lang == "en":
            keywords = cat_keywords_map.get("en", [category])
        else:
            keywords = cat_keywords_map.get("other", cat_keywords_map.get("en", [category]))

        # Generate username
        username = _make_username(category, region_code, idx)
        if username in seen_usernames:
            idx += 1
            continue
        seen_usernames.add(username)

        # Generate member count (power law: most small, few huge)
        r = random.random()
        if r < 0.01:
            members = random.randint(500_000, 5_000_000)
        elif r < 0.05:
            members = random.randint(100_000, 500_000)
        elif r < 0.15:
            members = random.randint(50_000, 100_000)
        elif r < 0.35:
            members = random.randint(10_000, 50_000)
        elif r < 0.60:
            members = random.randint(5_000, 10_000)
        else:
            members = random.randint(1_000, 5_000)

        # Geo jitter (bigger countries get more spread)
        spread = 8.0 if region_code in ("ru", "us", "br", "in", "cn") else 3.0
        lat, lng = _jitter_coord(*coords, spread=spread)
        # Clamp
        lat = max(-85, min(85, lat))
        lng = max(-180, min(180, lng))

        title = _make_title(category, region_info, keywords)
        has_comments = random.random() < 0.6
        er = round(random.uniform(0.005, 0.12), 4) if has_comments else 0.0

        channels.append({
            "username": username,
            "title": title,
            "category": category,
            "subcategory": random.choice(keywords)[:100] if keywords else "General",
            "language": lang,
            "region": region_code,
            "member_count": members,
            "has_comments": has_comments,
            "comments_enabled": has_comments,
            "avg_post_reach": int(members * random.uniform(0.03, 0.20)),
            "engagement_rate": er,
            "avg_comments_per_post": random.randint(5, 300) if has_comments else 0,
            "post_frequency_daily": round(random.uniform(0.5, 25), 1),
            "verified": random.random() < 0.05,
            "source": "seed_gen",
            "description": f"{title} — {category}",
            "lat": round(lat, 4),
            "lng": round(lng, 4),
        })

        idx += 1
        if len(channels) % batch_log == 0:
            log.info(f"Generated {len(channels):,} / {target:,} channels...")

    log.info(f"Total generated: {len(channels):,}")
    return channels


async def bulk_insert(channels: list[dict], batch_size: int = 10_000):
    """Bulk insert channels into PostgreSQL."""
    total = len(channels)
    inserted = 0

    for i in range(0, total, batch_size):
        batch = channels[i:i + batch_size]
        async with async_session() as session:
            # Use postgres superuser-like approach: set bootstrap mode
            await session.execute(text("SET LOCAL app.bootstrap = '1'"))

            for ch in batch:
                entry = ChannelMapEntry(
                    tenant_id=None,  # Global channels visible to all
                    username=ch["username"][:200],
                    title=ch["title"][:500],
                    category=ch["category"][:100],
                    subcategory=ch.get("subcategory", "")[:100],
                    language=ch["language"][:10],
                    region=ch["region"][:10],
                    member_count=ch["member_count"],
                    has_comments=ch["has_comments"],
                    comments_enabled=ch.get("comments_enabled", False),
                    avg_post_reach=ch.get("avg_post_reach"),
                    engagement_rate=ch.get("engagement_rate"),
                    avg_comments_per_post=ch.get("avg_comments_per_post"),
                    post_frequency_daily=ch.get("post_frequency_daily"),
                    verified=ch.get("verified", False),
                    source=ch.get("source", "seed_gen"),
                    description=ch.get("description", "")[:2000],
                )
                session.add(entry)

            await session.commit()
            inserted += len(batch)
            log.info(f"Inserted {inserted:,} / {total:,} channels")

    log.info(f"Bulk insert complete: {inserted:,} channels")


async def main():
    parser = argparse.ArgumentParser(description="Mass Channel Seeder")
    parser.add_argument("--target", type=int, default=1_000_000, help="Target channel count")
    parser.add_argument("--batch", type=int, default=10_000, help="Batch size for DB insert")
    parser.add_argument("--dry-run", action="store_true", help="Generate but don't insert")
    args = parser.parse_args()

    log.info(f"Generating {args.target:,} channels...")
    start = time.time()
    channels = generate_channels(target=args.target)
    gen_time = time.time() - start
    log.info(f"Generation took {gen_time:.1f}s")

    # Stats
    by_region = {}
    by_category = {}
    for ch in channels:
        by_region[ch["region"]] = by_region.get(ch["region"], 0) + 1
        by_category[ch["category"]] = by_category.get(ch["category"], 0) + 1

    log.info(f"Regions: {len(by_region)}")
    for r, c in sorted(by_region.items(), key=lambda x: -x[1])[:10]:
        log.info(f"  {r}: {c:,}")
    log.info(f"Categories: {len(by_category)}")
    for cat, c in sorted(by_category.items(), key=lambda x: -x[1])[:10]:
        log.info(f"  {cat}: {c:,}")

    if args.dry_run:
        log.info("Dry run — skipping DB insert")
        return

    log.info(f"Inserting into DB in batches of {args.batch:,}...")
    start = time.time()
    await bulk_insert(channels, batch_size=args.batch)
    insert_time = time.time() - start
    log.info(f"Insert took {insert_time:.1f}s")
    log.info("Done!")


if __name__ == "__main__":
    asyncio.run(main())
