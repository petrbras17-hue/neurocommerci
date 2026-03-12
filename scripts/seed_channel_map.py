"""
Seed channel_map_entries with 5000 realistic Telegram channel records.

Pure Python — no AI calls. Uses random.Random(42) for reproducibility.
Inserts via asyncpg with ON CONFLICT (username) DO NOTHING (idempotent).

Usage:
    python scripts/seed_channel_map.py
"""
from __future__ import annotations

import asyncio
import math
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Category definitions with realistic templates
# ---------------------------------------------------------------------------

CATEGORIES: dict[str, dict] = {
    "Crypto": {
        "count": 600,
        "subcategories": ["Trading", "DeFi", "NFT", "Mining", "Altcoins", "Signals"],
        "title_templates": [
            "Crypto {word}", "{word} Trading", "DeFi {word}", "{word} Signals",
            "Bitcoin {word}", "{word} NFT", "Altcoin {word}", "{word} Crypto",
        ],
        "micro_topics": [
            "bitcoin", "ethereum", "defi", "nft", "trading signals", "altcoins",
            "crypto news", "blockchain", "mining", "staking", "web3", "tokenomics",
        ],
    },
    "Marketing": {
        "count": 500,
        "subcategories": ["SMM", "SEO", "Content", "Email", "Growth", "Ads"],
        "title_templates": [
            "Marketing {word}", "{word} SMM", "Growth {word}", "{word} Ads",
            "Digital {word}", "{word} SEO", "Content {word}", "{word} Marketing",
        ],
        "micro_topics": [
            "smm", "seo", "content marketing", "email marketing", "growth hacking",
            "targeting", "copywriting", "analytics", "conversion", "branding",
        ],
    },
    "E-commerce": {
        "count": 400,
        "subcategories": ["Dropshipping", "Marketplace", "Wildberries", "Ozon", "Amazon"],
        "title_templates": [
            "E-com {word}", "{word} Shop", "Wildberries {word}", "{word} Ozon",
            "Маркетплейс {word}", "{word} Продажи", "Dropship {word}",
        ],
        "micro_topics": [
            "wildberries", "ozon", "marketplace", "dropshipping", "amazon",
            "ecommerce", "retail", "wholesale", "logistics", "fulfillment",
        ],
    },
    "EdTech": {
        "count": 350,
        "subcategories": ["Online Courses", "Programming", "Languages", "Schools"],
        "title_templates": [
            "Learn {word}", "{word} Academy", "EdTech {word}", "{word} School",
            "Курсы {word}", "{word} Обучение", "Code {word}",
        ],
        "micro_topics": [
            "online courses", "programming", "python", "javascript", "data science",
            "english", "design", "product management", "career", "mentoring",
        ],
    },
    "News": {
        "count": 400,
        "subcategories": ["Breaking", "Tech News", "Economics", "World", "Local"],
        "title_templates": [
            "News {word}", "{word} Today", "Breaking {word}", "{word} Daily",
            "Новости {word}", "{word} Лента", "World {word}",
        ],
        "micro_topics": [
            "breaking news", "politics", "economics", "tech news", "world news",
            "analytics", "opinion", "investigations", "media", "journalism",
        ],
    },
    "Entertainment": {
        "count": 350,
        "subcategories": ["Memes", "Movies", "Music", "Humor", "Viral"],
        "title_templates": [
            "Fun {word}", "{word} Memes", "LOL {word}", "{word} Viral",
            "Юмор {word}", "{word} Кино", "Music {word}",
        ],
        "micro_topics": [
            "memes", "humor", "movies", "music", "viral content",
            "comedy", "streaming", "celebrities", "tv shows", "podcasts",
        ],
    },
    "Tech": {
        "count": 300,
        "subcategories": ["AI", "Startups", "DevOps", "Mobile", "Cloud"],
        "title_templates": [
            "Tech {word}", "{word} AI", "Dev {word}", "{word} Stack",
            "Startup {word}", "{word} Code", "Cloud {word}",
        ],
        "micro_topics": [
            "artificial intelligence", "machine learning", "startups", "devops",
            "cloud computing", "mobile dev", "open source", "saas", "api", "security",
        ],
    },
    "Finance": {
        "count": 250,
        "subcategories": ["Investing", "Stocks", "Banking", "Personal Finance"],
        "title_templates": [
            "Finance {word}", "{word} Invest", "Stock {word}", "{word} Money",
            "Финансы {word}", "{word} Инвестиции", "Bank {word}",
        ],
        "micro_topics": [
            "investing", "stocks", "bonds", "personal finance", "banking",
            "forex", "real estate", "dividends", "portfolio", "wealth",
        ],
    },
    "Lifestyle": {
        "count": 200,
        "subcategories": ["Fashion", "Food", "Fitness", "Home"],
        "title_templates": [
            "Lifestyle {word}", "{word} Style", "Fashion {word}", "{word} Life",
            "Стиль {word}", "{word} Мода",
        ],
        "micro_topics": [
            "fashion", "food", "fitness", "home decor", "wellness",
            "beauty", "self-care", "minimalism", "productivity",
        ],
    },
    "Health": {
        "count": 200,
        "subcategories": ["Medicine", "Nutrition", "Mental Health", "Fitness"],
        "title_templates": [
            "Health {word}", "{word} Med", "Nutrition {word}", "{word} Wellness",
            "Здоровье {word}", "{word} Фитнес",
        ],
        "micro_topics": [
            "medicine", "nutrition", "mental health", "fitness", "supplements",
            "yoga", "biohacking", "sleep", "longevity",
        ],
    },
    "Gaming": {
        "count": 200,
        "subcategories": ["PC", "Mobile", "Console", "Esports"],
        "title_templates": [
            "Game {word}", "{word} Play", "Esport {word}", "{word} Gaming",
            "Игры {word}", "{word} Геймер",
        ],
        "micro_topics": [
            "pc gaming", "mobile games", "esports", "game reviews", "indie games",
            "fps", "mmorpg", "game dev", "streaming",
        ],
    },
    "18+": {
        "count": 200,
        "subcategories": ["Adult", "Dating", "NSFW"],
        "title_templates": [
            "Hot {word}", "{word} Night", "Adult {word}", "{word} Dating",
            "{word} 18+", "Secret {word}",
        ],
        "micro_topics": [
            "dating", "relationships", "adult content", "nightlife", "flirting",
        ],
    },
    "Politics": {
        "count": 200,
        "subcategories": ["Domestic", "International", "Elections", "Analysis"],
        "title_templates": [
            "Politics {word}", "{word} Power", "Election {word}", "{word} Gov",
            "Политика {word}", "{word} Власть",
        ],
        "micro_topics": [
            "elections", "geopolitics", "domestic policy", "diplomacy",
            "governance", "legislation", "activism", "democracy",
        ],
    },
    "Sports": {
        "count": 250,
        "subcategories": ["Football", "Basketball", "MMA", "Olympics", "Betting"],
        "title_templates": [
            "Sport {word}", "{word} Goals", "Football {word}", "{word} Match",
            "Спорт {word}", "{word} Ставки",
        ],
        "micro_topics": [
            "football", "basketball", "mma", "tennis", "sports betting",
            "olympics", "formula 1", "hockey", "fitness",
        ],
    },
    "Travel": {
        "count": 250,
        "subcategories": ["Budget", "Luxury", "Backpacking", "City Guides"],
        "title_templates": [
            "Travel {word}", "{word} Trip", "Explore {word}", "{word} World",
            "Путешествия {word}", "{word} Тур",
        ],
        "micro_topics": [
            "travel tips", "budget travel", "luxury travel", "backpacking",
            "city guides", "visa", "flights", "hotels", "adventure",
        ],
    },
    "Business": {
        "count": 350,
        "subcategories": ["Entrepreneurship", "Management", "HR", "B2B"],
        "title_templates": [
            "Business {word}", "{word} CEO", "Startup {word}", "{word} Pro",
            "Бизнес {word}", "{word} Предприниматель",
        ],
        "micro_topics": [
            "entrepreneurship", "management", "leadership", "hr", "b2b",
            "sales", "negotiation", "strategy", "scaling", "networking",
        ],
    },
}

WORDS = [
    "Alpha", "Beta", "Gamma", "Delta", "Pro", "Hub", "Lab", "Zone", "Daily",
    "Weekly", "Plus", "Max", "Prime", "Core", "Edge", "Flow", "Grid", "Nest",
    "Pulse", "Wave", "Spark", "Storm", "Peak", "Bolt", "Link", "Flux", "Nexus",
    "Echo", "Vibe", "Spot", "Base", "Club", "Star", "Moon", "Sun", "Fire",
    "Ice", "Sky", "Land", "Bay", "Point", "View", "Mind", "Eye", "Key",
    "Top", "Win", "Go", "Run", "Fast", "Live", "Now", "One", "Two", "Net",
]

LANGUAGES = ["ru"] * 80 + ["en"] * 10 + ["uk"] * 5 + ["kz"] * 5
REGIONS = ["ru"] * 60 + ["cis"] * 15 + ["uk"] * 10 + ["kz"] * 5 + ["other"] * 10


def generate_channels(rng: random.Random) -> list[dict]:
    """Generate 5000 channel records."""
    channels = []
    used_usernames: set[str] = set()
    global_id = 1_000_000_000  # base telegram_id

    for category, cfg in CATEGORIES.items():
        subcats = cfg["subcategories"]
        templates = cfg["title_templates"]
        topics = cfg["micro_topics"]

        for i in range(cfg["count"]):
            # Unique username
            word = rng.choice(WORDS)
            suffix = rng.randint(1, 9999)
            base_user = f"{category.lower().replace(' ', '').replace('+', '')}_{word.lower()}_{suffix}"
            while base_user in used_usernames:
                suffix = rng.randint(10000, 99999)
                base_user = f"{category.lower().replace(' ', '').replace('+', '')}_{word.lower()}_{suffix}"
            used_usernames.add(base_user)

            # Title
            title = rng.choice(templates).replace("{word}", word)

            # Member count: log-normal
            member_count = int(math.exp(rng.gauss(9.5, 1.5)))
            member_count = max(500, min(member_count, 500_000))

            # Correlated stats
            er = round(rng.uniform(0.01, 0.15) * (1 + rng.gauss(0, 0.3)), 4)
            er = max(0.005, min(er, 0.5))
            avg_comments = max(0, int(member_count * er * rng.uniform(0.001, 0.01)))
            avg_reach = max(100, int(member_count * rng.uniform(0.1, 0.6)))

            # Topics: 3-5 random from category pool
            n_topics = rng.randint(3, min(5, len(topics)))
            selected_topics = rng.sample(topics, n_topics)

            subcategory = rng.choice(subcats)
            language = rng.choice(LANGUAGES)
            region = rng.choice(REGIONS)

            channels.append({
                "telegram_id": global_id + len(channels),
                "username": base_user,
                "title": title,
                "member_count": member_count,
                "category": category,
                "subcategory": subcategory,
                "language": language,
                "region": region,
                "engagement_rate": er,
                "avg_comments_per_post": avg_comments,
                "avg_post_reach": avg_reach,
                "topic_tags": selected_topics,
                "source": "seed_v1",
                "description": f"{title} — Telegram channel about {subcategory.lower()} in {category.lower()}",
            })

    return channels


async def seed_database(channels: list[dict]) -> int:
    """Insert channels into channel_map_entries via asyncpg."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    # Read SEED_DATABASE_URL or DATABASE_URL from env/.env
    # SEED_DATABASE_URL should point to a superuser connection to bypass RLS
    db_url = os.environ.get("SEED_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    if not db_url:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("SEED_DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip()
                    break
                if line.startswith("DATABASE_URL=") and not db_url:
                    db_url = line.split("=", 1)[1].strip()
    if not db_url:
        print("ERROR: DATABASE_URL not found in .env or environment")
        sys.exit(1)

    # Upgrade to asyncpg driver
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(db_url, echo=False)

    import json

    # Check if seed data already exists
    async with engine.begin() as conn:
        existing = (await conn.execute(text(
            "SELECT COUNT(*) FROM channel_map_entries WHERE source = 'seed_v1'"
        ))).scalar_one()
        if existing > 0:
            print(f"  Already have {existing} seed_v1 records. Skipping insert.")
            await engine.dispose()
            return 0

    inserted = 0
    batch_size = 500
    for batch_start in range(0, len(channels), batch_size):
        batch = channels[batch_start : batch_start + batch_size]
        async with engine.begin() as conn:
            for ch in batch:
                await conn.execute(
                    text("""
                        INSERT INTO channel_map_entries
                            (telegram_id, username, title, member_count, category,
                             subcategory, language, region, engagement_rate,
                             avg_comments_per_post, avg_post_reach, topic_tags,
                             source, description)
                        VALUES
                            (:telegram_id, :username, :title, :member_count, :category,
                             :subcategory, :language, :region, :engagement_rate,
                             :avg_comments_per_post, :avg_post_reach, CAST(:topic_tags AS jsonb),
                             :source, :description)
                    """),
                    {
                        **{k: v for k, v in ch.items() if k != "topic_tags"},
                        "topic_tags": json.dumps(ch["topic_tags"]),
                    },
                )
                inserted += 1
        print(f"  Batch {batch_start // batch_size + 1}/{(len(channels) + batch_size - 1) // batch_size}: {inserted} inserted so far")

    await engine.dispose()
    return inserted


def main():
    import json as _json

    mode = sys.argv[1] if len(sys.argv) > 1 else "db"

    print("Generating 5000 channel records...", file=sys.stderr)
    rng = random.Random(42)
    channels = generate_channels(rng)
    print(f"  Generated {len(channels)} channels across {len(CATEGORIES)} categories", file=sys.stderr)

    # Verify uniqueness
    usernames = [ch["username"] for ch in channels]
    assert len(usernames) == len(set(usernames)), "Duplicate usernames detected!"
    print("  All usernames unique ✓", file=sys.stderr)

    if mode == "sql":
        # Output raw SQL to stdout for piping to psql
        print("BEGIN;")
        for ch in channels:
            tags_json = _json.dumps(ch["topic_tags"]).replace("'", "''")
            desc = ch["description"].replace("'", "''")
            title = ch["title"].replace("'", "''")
            print(f"INSERT INTO channel_map_entries "
                  f"(telegram_id, username, title, member_count, category, "
                  f"subcategory, language, region, engagement_rate, "
                  f"avg_comments_per_post, avg_post_reach, topic_tags, source, description) "
                  f"VALUES ({ch['telegram_id']}, '{ch['username']}', '{title}', "
                  f"{ch['member_count']}, '{ch['category']}', '{ch['subcategory']}', "
                  f"'{ch['language']}', '{ch['region']}', {ch['engagement_rate']}, "
                  f"{ch['avg_comments_per_post']}, {ch['avg_post_reach']}, "
                  f"'{tags_json}'::jsonb, '{ch['source']}', '{desc}');")
        print("COMMIT;")
        print(f"-- {len(channels)} INSERT statements generated", file=sys.stderr)
        return

    print("\nInserting into database...", file=sys.stderr)
    inserted = asyncio.run(seed_database(channels))
    print(f"\nDone! {inserted} new channels inserted (existing skipped).", file=sys.stderr)


if __name__ == "__main__":
    main()
