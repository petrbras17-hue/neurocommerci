---
name: telegram-parser
description: "Professional Telegram parser for mass channel/chat/community discovery and analysis. Use this skill whenever the user wants to parse Telegram channels, scrape channel data, build a channel database, discover new channels, analyze Telegram communities, find channels by keywords, do mass channel parsing, import channels from TGStat, scrape Telegram for market research, or populate the channel map. Also triggers when the user mentions 'parse channels', 'find channels', 'channel database', 'TGStat', 'channel discovery', 'scrape Telegram', 'mass parse', '1 million channels', 'channel map data', or any Telegram data collection task."
---

# Telegram Parser

Mass-scale Telegram channel, chat, and community parser. Discovers, validates, classifies, and stores channel data with geo-coordinates for the 3D globe visualization.

## Why this exists

NEURO COMMENTING needs a massive database of real Telegram channels to power the Channel Map globe, farm targeting, campaign planning, and competitive intelligence. This skill orchestrates all parsing methods from a single interface.

## Architecture

```
Keywords/Seeds → Discovery Engine → Validation → Classification → Geo-Detection → PostgreSQL
                      ↓                                                              ↓
              TGStat API (bulk)                                              channel_map_entries
              Telethon Search                                                    ↓
              Snowball Crawl                                              3D Globe / Channel Map
              Global Message Search
              TGStat Web Scraping
```

## Available parsing methods

### 1. TGStat API (fastest, safest, recommended first)

Requires `TGSTAT_TOKEN` env var. TGStat indexes 2.7M+ channels with real metrics.

```bash
cd "$PROJECT_DIR" && TGSTAT_TOKEN=xxx .venv/bin/python scripts/mass_tgstat_parser.py \
  --method api --target 500000 --save-db
```

Rate: ~50,000 channels/hour. Zero Telegram ban risk.

### 2. TGStat web scraping (no token needed)

Scrapes category pages from tgstat.ru. Slower but free.

```bash
cd "$PROJECT_DIR" && .venv/bin/python scripts/mass_tgstat_parser.py \
  --method scrape --target 100000 --save-db
```

Rate: ~5,000 channels/hour. Risk: IP rate limiting (use rotating proxies).

### 3. Hybrid (API + scraping + search)

Combines all TGStat methods for maximum coverage.

```bash
cd "$PROJECT_DIR" && .venv/bin/python scripts/mass_tgstat_parser.py \
  --method hybrid --target 500000 --save-db
```

### 4. SmartChannelDiscovery (Telethon-based, real-time)

Uses a live Telegram account for search, snowball crawl, and global message search.

```python
from core.smart_channel_discovery import SmartChannelDiscovery

discovery = SmartChannelDiscovery(session_manager=sm, tgstat_token=token)
results = await discovery.discover(
    keywords=["крипто", "бизнес", "маркетинг"],
    methods=["tgstat", "telethon", "snowball", "global_search"],
    max_results=10000,
    min_members=10000,
    require_comments=True,
    language_filter=None,  # None = all languages
    db_session=session,
)
await discovery.save_to_catalog(results, session, tenant_id=None)
```

Rate: ~200-500 channels/hour. Risk: account freeze if too aggressive.

### 5. Seed generator (procedural, for globe coverage)

Generates geo-tagged channel seeds to fill the globe visualization. Not real channels but realistic placeholders.

```bash
cd "$PROJECT_DIR" && .venv/bin/python scripts/mass_channel_seeder.py \
  --target 1000000 --batch 10000
```

Rate: 1,000,000 in ~30 seconds. Use only for visual coverage, not for actual targeting.

### 6. Channel Parser Service (keyword-based via Telethon)

Production parser integrated with the job queue system.

```python
from core.channel_parser_service import ChannelParserService

parser = ChannelParserService()
await parser.run_parsing_job(
    session=db_session,
    account=account,
    keywords=["новости Москва", "крипто трейдинг"],
    min_members=5000,
    max_results=500,
)
```

## Data model

All parsed channels go to `channel_map_entries` table:

| Column | Type | Description |
|--------|------|-------------|
| id | int | Primary key |
| tenant_id | int/null | NULL = platform catalog (visible to all) |
| telegram_id | bigint | Telegram channel ID |
| username | varchar(200) | @username |
| title | varchar(500) | Channel title |
| description | varchar(2000) | About text |
| category | varchar(100) | News, Tech, Crypto, etc. (35 categories) |
| subcategory | varchar(100) | Sub-classification |
| language | varchar(10) | ru, en, uk, kz, etc. |
| region | varchar(10) | Country code (ru, us, de, etc.) |
| member_count | int | Subscriber count |
| has_comments | bool | Comments discussion group linked |
| comments_enabled | bool | Same as above |
| engagement_rate | float | ER metric |
| avg_post_reach | int | Average post views |
| avg_comments_per_post | int | Comments per post average |
| post_frequency_daily | float | Posts per day |
| verified | bool | Verified channel |
| source | varchar(50) | tgstat_api, tgstat_scrape, telethon, snowball, seed_gen |
| lat | float | Latitude for globe |
| lng | float | Longitude for globe |
| spam_score | float | AI spam suitability 0-10 |
| topic_tags | jsonb | AI-generated topic tags |

## Geo-detection

Region is detected from:
1. TGStat country field (most reliable)
2. Language heuristic (Cyrillic → ru/ua/by, Arabic → ae/sa/eg, etc.)
3. Phone prefix of channel admin (if accessible)
4. Description keywords (city names, country references)

Lat/lng coordinates are assigned from region center + random jitter to spread points across the country.

## Categories (35 total)

News, Tech, Crypto, Finance, Marketing, E-commerce, EdTech, Entertainment, Gaming, Lifestyle, Health, Sports, Travel, Politics, Business, Science, Music, Food, Fashion, Auto, Real Estate, Legal, HR, Design, Photography, Startups, AI/ML, Cybersecurity, Parenting, Pets, DIY, Books, Movies, Humor, Motivation

## API endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /v1/channel-map | List channels (paginated) |
| GET | /v1/channel-map/geo | Compact geo data for globe (lat/lng + minimal fields) |
| POST | /v1/channel-map/search | Search by keyword |
| GET | /v1/channel-map/stats | Aggregate stats by category/language/region |
| GET | /v1/channel-map/categories | List categories |
| POST | /v1/channel-map/index | Trigger channel indexing job |
| POST | /v1/channel-map/refresh | Refresh channel data |
| POST | /v1/parser/channels | Start parsing job |
| GET | /v1/parser/jobs | List parsing jobs |

## Safety rules

- Never use the user's primary account for mass parsing. Use a dedicated parser account.
- Respect Telegram rate limits: 200-300 requests/hour per account.
- TGStat scraping: 2-5 second delays between pages, rotate user agents.
- Always save to DB incrementally (batch commits) to avoid losing progress on crash.
- Platform catalog channels use `tenant_id=NULL` so they're visible to all tenants via RLS.
- When inserting via SQL on VPS, use `postgres` superuser to bypass FORCE RLS, or set `app.bootstrap='1'`.

## Recommended parsing strategy for 500K+ real channels

1. Start with TGStat API if you have a token ($50/month) — fastest route to 500K
2. Supplement with TGStat scraping for categories the API missed
3. Use Telethon snowball crawl to find niche channels not on TGStat
4. Generate seed data to fill globe gaps in underrepresented regions
5. Run classification pass to assign categories to uncategorized channels

## Key files

- `core/smart_channel_discovery.py` — SmartChannelDiscovery engine
- `core/channel_parser_service.py` — Production parser service
- `core/channel_indexer.py` — Telethon channel metadata fetcher
- `scripts/mass_tgstat_parser.py` — Mass TGStat parser (API + scraping)
- `scripts/mass_channel_seeder.py` — Procedural seed generator
- `scripts/parse_cis_channels.py` — CIS channel bootstrap CLI
- `storage/models.py` — ChannelMapEntry ORM model
- `ops_api.py` — Channel map API endpoints
