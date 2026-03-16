---
name: channel-ops
description: Channel map operations for NEURO COMMENTING — parsing, enriching, refreshing, exporting, importing channels. Use when the user asks about channel database management, channel parsing, channel enrichment, channel map data, TGStat import, or channel export.
disable-model-invocation: true
allowed-tools: Bash, Read, Grep, Glob
---

# Channel Map Operations Skill

Manage the NEURO COMMENTING channel database: parse, enrich, refresh, export, and import channels.

## Environment

- **Channel Map Table**: `channel_map_entries`
- **Channel DB Table**: `channel_databases` + `channel_db_entries`
- **VPS Path**: `/opt/neuro-commenting`
- **Local Repo**: `/Users/braslavskii/NEURO COMMENTING`
- **Import Scripts**: `scripts/import_channels_*.py`, `scripts/parse_cis_channels.py`
- **Channel Indexer**: `core/channel_indexer.py`
- **Channel Parser**: `core/channel_parser_service.py`

## Quick Queries

### Channel Map Overview

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
SELECT
  COUNT(*) as total_channels,
  COUNT(DISTINCT category) as categories,
  COUNT(DISTINCT language) as languages,
  AVG(subscribers_count)::int as avg_subs,
  MAX(subscribers_count) as max_subs,
  MIN(subscribers_count) as min_subs,
  COUNT(CASE WHEN subscribers_count >= 5000 THEN 1 END) as channels_5k_plus
FROM channel_map_entries;
\""
```

### Channels by Category

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
SELECT category, COUNT(*) as count, AVG(subscribers_count)::int as avg_subs
FROM channel_map_entries
GROUP BY category
ORDER BY count DESC;
\""
```

### Channels by Language

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
SELECT language, COUNT(*) as count
FROM channel_map_entries
WHERE language IS NOT NULL
GROUP BY language
ORDER BY count DESC
LIMIT 20;
\""
```

### Channels by Country

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
SELECT country, COUNT(*) as count, AVG(subscribers_count)::int as avg_subs
FROM channel_map_entries
WHERE country IS NOT NULL
GROUP BY country
ORDER BY count DESC
LIMIT 20;
\""
```

### Top Channels by Subscribers

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
SELECT id, username, title, category, subscribers_count, language, country
FROM channel_map_entries
ORDER BY subscribers_count DESC
LIMIT 30;
\""
```

### Recently Added Channels

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
SELECT id, username, title, category, subscribers_count, created_at
FROM channel_map_entries
ORDER BY created_at DESC
LIMIT 30;
\""
```

### Channels Missing Data (Need Enrichment)

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
SELECT
  COUNT(CASE WHEN category IS NULL THEN 1 END) as no_category,
  COUNT(CASE WHEN language IS NULL THEN 1 END) as no_language,
  COUNT(CASE WHEN country IS NULL THEN 1 END) as no_country,
  COUNT(CASE WHEN latitude IS NULL THEN 1 END) as no_coordinates,
  COUNT(CASE WHEN description IS NULL OR description = '' THEN 1 END) as no_description,
  COUNT(CASE WHEN topic_tags IS NULL THEN 1 END) as no_topics,
  COUNT(CASE WHEN spam_score IS NULL THEN 1 END) as no_spam_score
FROM channel_map_entries;
\""
```

### Search Channels

```bash
# Replace QUERY with search term
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
SELECT id, username, title, category, subscribers_count
FROM channel_map_entries
WHERE title ILIKE '%QUERY%' OR username ILIKE '%QUERY%' OR description ILIKE '%QUERY%'
ORDER BY subscribers_count DESC
LIMIT 20;
\""
```

### Spam Score Distribution

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
SELECT
  CASE
    WHEN spam_score < 0.2 THEN 'clean (0-0.2)'
    WHEN spam_score < 0.5 THEN 'moderate (0.2-0.5)'
    WHEN spam_score < 0.8 THEN 'suspicious (0.5-0.8)'
    ELSE 'likely spam (0.8-1.0)'
  END as spam_level,
  COUNT(*) as count
FROM channel_map_entries
WHERE spam_score IS NOT NULL
GROUP BY spam_level
ORDER BY spam_level;
\""
```

## Channel Database (Per-Tenant)

### List Channel Databases

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT id, name, description, channels_count, created_at FROM channel_databases ORDER BY id;\""
```

### Channels in a Database

```bash
# Replace DB_ID with the actual database ID
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT id, username, title, subscribers_count, is_blacklisted FROM channel_db_entries WHERE database_id = DB_ID ORDER BY subscribers_count DESC LIMIT 30;\""
```

## Parsing Operations

### List Active Parsing Jobs

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT id, keywords, status, progress, results_count, created_at FROM parsing_jobs ORDER BY created_at DESC LIMIT 10;\""
```

### Start Parsing via API

```bash
# Requires JWT token
TOKEN="<your-jwt>"
VPS="https://neurocommenting.com"
curl -sk -X POST "$VPS/v1/parser/channels" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"keywords": ["crypto", "трейдинг"], "min_subscribers": 1000, "max_results": 100}'
```

### Check Parsing Job Status

```bash
# Replace JOB_ID with actual job ID
TOKEN="<your-jwt>"
VPS="https://neurocommenting.com"
curl -sk "$VPS/v1/parser/jobs/JOB_ID" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

## Import Operations

### Import from File (Usernames List)

```bash
# Local: prepare a file with one username per line
# Then run the import script
cd "/Users/braslavskii/NEURO COMMENTING"
python scripts/import_channels_from_file.py data/tgstat_usernames.txt
```

### Import via SQL (Direct Insert)

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
INSERT INTO channel_map_entries (username, title, category, subscribers_count, language, country, latitude, longitude)
VALUES
  ('durov', 'Durov Channel', 'tech', 1000000, 'en', 'AE', 25.2048, 55.2708)
ON CONFLICT (username) DO UPDATE SET
  subscribers_count = EXCLUDED.subscribers_count,
  title = EXCLUDED.title;
\""
```

### Bulk Import via Seed SQL

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -f data/channel_map_seed.sql"
```

## Export Operations

### Export All Channels as CSV

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
COPY (
  SELECT id, username, title, category, subscribers_count, language, country, latitude, longitude, description
  FROM channel_map_entries
  ORDER BY subscribers_count DESC
) TO STDOUT WITH CSV HEADER;
\"" > channels_export.csv
```

### Export Channels for Category

```bash
# Replace CATEGORY with actual category name
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
COPY (
  SELECT username, title, subscribers_count, language, country
  FROM channel_map_entries
  WHERE category = 'CATEGORY'
  ORDER BY subscribers_count DESC
) TO STDOUT WITH CSV HEADER;
\""
```

### Export Usernames Only

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"
SELECT username FROM channel_map_entries WHERE username IS NOT NULL ORDER BY subscribers_count DESC;
\"" | tail -n +3 | head -n -2 > usernames.txt
```

## Enrichment Operations

### Classify Channels (AI Micro-Topic)

```bash
# Single channel classification
TOKEN="<your-jwt>"
VPS="https://neurocommenting.com"
curl -sk -X POST "$VPS/v1/channel-map/classify" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"channel_id": 1}' | python3 -m json.tool
```

### Batch Classify

```bash
TOKEN="<your-jwt>"
VPS="https://neurocommenting.com"
curl -sk -X POST "$VPS/v1/channel-map/classify/batch" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"channel_ids": [1, 2, 3, 4, 5]}' | python3 -m json.tool
```

### Refresh Channel Metadata (via Channel Indexer)

The `core/channel_indexer.py` module fetches live Telethon metadata:

```bash
# Run on VPS inside ops_api container
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T ops_api python -c \"
from core.channel_indexer import ChannelIndexer
import asyncio
indexer = ChannelIndexer()
asyncio.run(indexer.refresh_stale_channels(max_channels=50))
\""
```

## Channel Map API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/channel-map` | List channels (pagination, filters) |
| GET | `/v1/channel-map/{id}` | Channel detail |
| GET | `/v1/channel-map/clusters` | SQL-side clustered channels for globe |
| GET | `/v1/channel-map/viewport` | Channels within map viewport bounds |
| POST | `/v1/channel-map/classify` | AI micro-topic classification |
| POST | `/v1/channel-map/classify/batch` | Batch AI classification |
| POST | `/v1/channel-db` | Create channel database |
| GET | `/v1/channel-db` | List channel databases |
| GET | `/v1/channel-db/{id}` | Get database details |
| POST | `/v1/channel-db/{id}/import` | Import channels into database |
| GET | `/v1/channel-db/{id}/channels` | List channels in database |

## Data Quality Checklist

1. **Total channels** — target 5K+ channels with 5K+ subscribers.
2. **Category coverage** — every channel should have a category assigned.
3. **Coordinates** — channels need lat/lng for globe rendering.
4. **Language detection** — run classification on channels with NULL language.
5. **Spam filtering** — check spam_score distribution, remove channels with score > 0.8.
6. **Freshness** — channels should be refreshed at least monthly (check `last_refreshed_at`).
7. **Deduplication** — no duplicate usernames (enforced by UNIQUE constraint).

## Useful Scripts in Repo

| Script | Purpose |
|--------|---------|
| `scripts/parse_cis_channels.py` | Bootstrap CIS channels from keyword search |
| `scripts/import_channels_from_file.py` | Import usernames from text file |
| `scripts/import_channels_sqlite.py` | Import from SQLite source |
| `scripts/import_channels_botapi.py` | Import via Bot API |
| `core/channel_indexer.py` | Telethon metadata fetcher + bulk indexer |
| `core/channel_parser_service.py` | Keyword-driven channel discovery |
