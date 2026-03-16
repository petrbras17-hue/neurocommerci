# Multi-Database Architecture — NEURO COMMENTING

## Цель

Разгрузить PostgreSQL, убрать лаги, исключить галлюцинации AI через точный retrieval.

## Текущее состояние

78 ORM моделей в одном PostgreSQL: OLTP + OLAP + Search + AI context + Queue.
При 50 активных аккаунтах: ~15M строк append-only логов за 2 недели.

## Целевая архитектура

```
┌─────────────────────────────────────────────────────────┐
│                    NEURO COMMENTING                      │
├──────────┬──────────┬──────────┬──────────┬──────────────┤
│ PostgreSQL│  Redis   │ Pinecone │ClickHouse│  Meilisearch │
│  (OLTP)  │ (Cache+Q)│ (Vector) │ (OLAP)   │  (Search)    │
└──────────┴──────────┴──────────┴──────────┴──────────────┘
```

## 1. PostgreSQL — OLTP (оптимизация)

### Composite Indexes (Alembic миграция)

```sql
-- Channel Map: geo + filter queries
CREATE INDEX ix_cme_cat_lang_members ON channel_map_entries(category, language, member_count DESC);
CREATE INDEX ix_cme_lat_lng ON channel_map_entries(lat, lng);
CREATE INDEX ix_cme_tenant_created ON channel_map_entries(tenant_id, created_at DESC);
CREATE INDEX ix_cme_topic_tags ON channel_map_entries USING GIN(topic_tags);

-- Activity logs: timeline queries
CREATE INDEX ix_aal_account_created ON account_activity_logs(account_id, created_at DESC);
CREATE INDEX ix_aal_tenant_type_created ON account_activity_logs(tenant_id, action_type, created_at DESC);

-- Farm events: monitoring dashboard
CREATE INDEX ix_fe_farm_created ON farm_events(farm_id, created_at DESC);
CREATE INDEX ix_fe_account_action ON farm_events(account_id, action_type);

-- AI telemetry: cost aggregation
CREATE INDEX ix_air_tenant_task_created ON ai_requests(tenant_id, task_type, created_at DESC);

-- Jobs queue: worker dequeue
CREATE INDEX ix_aj_status_created ON app_jobs(status, created_at) WHERE status = 'queued';

-- Accounts: farm polling
CREATE INDEX ix_acc_tenant_status ON accounts(tenant_id, status);
CREATE INDEX ix_acc_lifecycle ON accounts(lifecycle_stage, health_status);
```

### Clusters endpoint — SQL-side GROUP BY

Заменить Python dict clustering на SQL:
```sql
SELECT
  floor(lat / :cell_size) AS cell_lat,
  floor(lng / :cell_size) AS cell_lng,
  COUNT(*) AS count,
  AVG(member_count) AS avg_members,
  mode() WITHIN GROUP (ORDER BY category) AS top_category
FROM channel_map_entries
WHERE tenant_id IS NULL OR tenant_id = :tid
GROUP BY cell_lat, cell_lng;
```

## 2. Redis — расширенное кэширование

### Новые ключи

| Ключ | Данные | TTL | Инвалидация |
|------|--------|-----|-------------|
| `brief:{workspace_id}` | BusinessBrief JSON | 5 мин | На UPDATE brief |
| `health:{account_id}` | HealthScore JSON | 30 сек | На UPDATE score |
| `clusters:{zoom}:{cat}` | Pre-computed clusters | 60 сек | На INSERT channel |
| `channel_geo` | Compact lat/lng array | 5 мин | На bulk import |
| `farm_stats:{farm_id}` | Live farm counters | 10 сек | На FarmEvent |

### Реализация

Файл: `core/cache_service.py`
- `CacheService` class с get/set/invalidate
- Decorator `@cached(key_pattern, ttl)` для endpoint handlers
- Pub/Sub инвалидация при write operations

## 3. Pinecone — векторная БД (anti-hallucination)

### Индексы

| Index | Модель | Dimensions | Metric | Содержимое |
|-------|--------|-----------|--------|-----------|
| `nc-business-context` | multilingual-e5-large | 1024 | cosine | BusinessBrief + Assets chunks |
| `nc-channel-knowledge` | multilingual-e5-large | 1024 | cosine | Channel title + description + topics |
| `nc-comment-patterns` | multilingual-e5-large | 1024 | cosine | Успешные комментарии (score > 0.8) |
| `nc-competitor-intel` | multilingual-e5-large | 1024 | cosine | GramGPT транскрипты + research |

### Anti-hallucination pipeline

```python
async def get_ai_context(query: str, workspace_id: int) -> dict:
    # 1. Semantic search в Pinecone
    results = await pinecone.query(
        index="nc-business-context",
        vector=embed(query),
        filter={"workspace_id": workspace_id},
        top_k=5
    )
    # 2. Inject facts в prompt
    context = "\n".join([r.metadata["text"] for r in results])
    # 3. LLM generates with FACTS, not imagination
    return {"context": context, "sources": results}
```

### Embedding pipeline

Файл: `core/embedding_service.py`
- Embed на INSERT/UPDATE BusinessBrief → upsert в Pinecone
- Embed на channel import → upsert в nc-channel-knowledge
- Embed на comment approve (score > 0.8) → upsert в nc-comment-patterns
- Batch embed для competitor intel transcripts

## 4. Meilisearch — мгновенный поиск каналов

### Docker Compose

```yaml
meilisearch:
  image: getmeili/meilisearch:v1.6
  ports:
    - "7700:7700"
  environment:
    MEILI_MASTER_KEY: ${MEILI_MASTER_KEY}
  volumes:
    - meili_data:/meili_data
```

### Индекс channels

```python
index_config = {
    "primaryKey": "id",
    "searchableAttributes": ["title", "username", "description", "category", "language"],
    "filterableAttributes": ["category", "language", "region", "member_count", "_geo"],
    "sortableAttributes": ["member_count", "engagement_rate", "created_at"],
    "rankingRules": ["words", "typo", "proximity", "attribute", "sort", "exactness"],
    "typoTolerance": {"enabled": True, "minWordSizeForTypos": {"oneTypo": 3, "twoTypos": 6}}
}
```

### Sync pipeline

Файл: `core/search_service.py`
- On channel INSERT/UPDATE → push to Meilisearch
- Bulk sync script: `scripts/sync_channels_to_meili.py`
- `/v1/channel-map/search` → Meilisearch query (<5ms) вместо PostgreSQL LIKE

## 5. ClickHouse — аналитика и телеметрия

### Docker Compose

```yaml
clickhouse:
  image: clickhouse/clickhouse-server:24.1
  ports:
    - "8123:8123"
    - "9000:9000"
  volumes:
    - clickhouse_data:/var/lib/clickhouse
  ulimits:
    nofile:
      soft: 262144
      hard: 262144
```

### Таблицы

```sql
-- Activity logs (partition by day, TTL 90 days)
CREATE TABLE activity_log (
    id UInt64,
    tenant_id UInt32,
    account_id UInt32,
    action_type LowCardinality(String),
    details String,
    created_at DateTime
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(created_at)
ORDER BY (tenant_id, account_id, created_at)
TTL created_at + INTERVAL 90 DAY;

-- Farm events (partition by day, TTL 30 days)
CREATE TABLE farm_events (
    id UInt64,
    tenant_id UInt32,
    farm_id UInt32,
    account_id UInt32,
    action_type LowCardinality(String),
    details String,
    created_at DateTime
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(created_at)
ORDER BY (tenant_id, farm_id, created_at)
TTL created_at + INTERVAL 30 DAY;

-- AI telemetry (partition by month)
CREATE TABLE ai_telemetry (
    id UInt64,
    tenant_id UInt32,
    task_type LowCardinality(String),
    provider LowCardinality(String),
    model String,
    status LowCardinality(String),
    estimated_cost_usd Float64,
    latency_ms UInt32,
    tokens_in UInt32,
    tokens_out UInt32,
    created_at DateTime
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(created_at)
ORDER BY (tenant_id, task_type, created_at);

-- Pre-aggregated analytics (5-min buckets)
CREATE TABLE analytics_5m (
    tenant_id UInt32,
    metric LowCardinality(String),
    bucket DateTime,
    value Float64,
    count UInt64
) ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(bucket)
ORDER BY (tenant_id, metric, bucket);
```

### Dual-write pipeline

Файл: `core/telemetry_service.py`
- Write to PostgreSQL (source of truth) + async push to ClickHouse
- Background job: `sync_logs_to_clickhouse` (batch 1000 rows, 5s interval)
- Read path: analytics dashboards → ClickHouse, audit → PostgreSQL
- Retention: ClickHouse TTL auto-drops old partitions

## Порядок реализации

| Фаза | Что | Файлы | Время |
|------|-----|-------|-------|
| 1 | PostgreSQL indexes | Alembic migration | 30 мин |
| 2 | Redis cache layer | core/cache_service.py, ops_api.py | 2-3 часа |
| 3 | Pinecone setup | core/embedding_service.py, ops_api.py | 3-4 часа |
| 4 | Meilisearch | docker-compose.yml, core/search_service.py | 2-3 часа |
| 5 | ClickHouse | docker-compose.yml, core/telemetry_service.py | 4-5 часов |

## Env vars

```env
# Pinecone
PINECONE_API_KEY=
PINECONE_ENVIRONMENT=

# Meilisearch
MEILI_MASTER_KEY=
MEILI_URL=http://meilisearch:7700

# ClickHouse
CLICKHOUSE_HOST=clickhouse
CLICKHOUSE_PORT=9000
CLICKHOUSE_DB=neuro_analytics
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=
```
