"""
NEURO COMMENTING — TelemetryService: буферизированная запись в ClickHouse.

Этот модуль отвечает за:
- создание таблиц в ClickHouse при старте (CREATE TABLE IF NOT EXISTS)
- dual-write методы (вызываются из бизнес-логики вместо прямой записи в PG)
- буферизацию строк и batched INSERT каждые 5 секунд или при накоплении 1000 строк
- query-методы для дашбордов (activity timeline, farm stats, AI cost summary, timeseries)
- одноразовую историческую синхронизацию из PostgreSQL → ClickHouse
  с resume-поддержкой через Redis ключ ch_sync:{table}:last_id

Зависимости: clickhouse-connect (pip install clickhouse-connect).
Если clickhouse-connect недоступен, используется fallback через aiohttp HTTP API
ClickHouse на порту 8123 (REST-совместимый интерфейс).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Попытка импорта clickhouse-connect; при неудаче — fallback на aiohttp
# ---------------------------------------------------------------------------

try:
    import clickhouse_connect  # type: ignore
    _CH_CONNECT_AVAILABLE = True
except ImportError:
    _CH_CONNECT_AVAILABLE = False
    logger.warning(
        "clickhouse-connect не установлен — используется aiohttp HTTP fallback. "
        "Установите: pip install clickhouse-connect"
    )

try:
    import aiohttp  # type: ignore
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False


# ---------------------------------------------------------------------------
# DDL для всех четырёх таблиц ClickHouse
# ---------------------------------------------------------------------------

_DDL_ACTIVITY_LOG = """
CREATE TABLE IF NOT EXISTS activity_log (
    id          UInt64,
    tenant_id   UInt32,
    account_id  UInt32,
    action_type LowCardinality(String),
    success     UInt8,
    duration_ms Int32,
    error_msg   String,
    details     String,
    created_at  DateTime
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(created_at)
ORDER BY (tenant_id, account_id, created_at)
TTL created_at + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;
"""

_DDL_FARM_EVENTS = """
CREATE TABLE IF NOT EXISTS farm_events (
    id         UInt64,
    tenant_id  UInt32,
    farm_id    UInt32,
    thread_id  UInt32,
    event_type LowCardinality(String),
    severity   LowCardinality(String),
    message    String,
    metadata   String,
    created_at DateTime
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(created_at)
ORDER BY (tenant_id, farm_id, created_at)
TTL created_at + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;
"""

_DDL_AI_TELEMETRY = """
CREATE TABLE IF NOT EXISTS ai_telemetry (
    id               UInt64,
    tenant_id        UInt32,
    workspace_id     UInt32,
    task_type        LowCardinality(String),
    provider         LowCardinality(String),
    model            String,
    status           LowCardinality(String),
    outcome          LowCardinality(String),
    latency_ms       Int32,
    tokens_in        Int32,
    tokens_out       Int32,
    cost_usd         Float64,
    fallback_used    UInt8,
    json_parse_fail  UInt8,
    quality_score    Float32,
    created_at       DateTime
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(created_at)
ORDER BY (tenant_id, task_type, created_at)
SETTINGS index_granularity = 8192;
"""

_DDL_ANALYTICS_5M = """
CREATE TABLE IF NOT EXISTS analytics_5m (
    tenant_id     UInt32,
    workspace_id  UInt32,
    metric        LowCardinality(String),
    bucket        DateTime,
    total         Int64,
    success_cnt   Int64,
    fail_cnt      Int64
) ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(bucket)
ORDER BY (tenant_id, workspace_id, metric, bucket)
SETTINGS index_granularity = 8192;
"""


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    """Возвращает текущее UTC время без tzinfo (ClickHouse DateTime не хранит tz)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_json(obj: Any) -> str:
    """Безопасная сериализация в JSON-строку для ClickHouse String-колонки."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)


def _round_to_5m(dt: datetime) -> datetime:
    """Округляет datetime вниз до ближайших 5 минут."""
    return dt.replace(second=0, microsecond=0, minute=(dt.minute // 5) * 5)


# ---------------------------------------------------------------------------
# TelemetryBuffer — накапливает строки, сбрасывает пачкой
# ---------------------------------------------------------------------------

class TelemetryBuffer:
    """
    Накапливает строки по таблицам.
    Автоматически flush при достижении max_size строк на таблицу.
    Периодический flush вызывается из TelemetryService._flush_loop().
    """

    def __init__(self, flush_interval: int = 5, max_size: int = 1000):
        self._buffer: Dict[str, List[Tuple]] = defaultdict(list)
        self._flush_interval = flush_interval
        self._max_size = max_size
        self._lock = asyncio.Lock()
        # Колбэк для реального INSERT; устанавливается после создания сервиса
        self._insert_cb = None  # async callable(table, rows)

    def set_insert_callback(self, cb) -> None:
        """Устанавливает async функцию вставки, вызываемую при flush."""
        self._insert_cb = cb

    async def add(self, table: str, row: Tuple) -> None:
        """Добавляет строку в буфер; при достижении max_size — сразу flush."""
        async with self._lock:
            self._buffer[table].append(row)
            if len(self._buffer[table]) >= self._max_size:
                rows = self._buffer.pop(table)
        # flush вне лока, чтобы не блокировать добавление
        if table not in self._buffer or len(self._buffer.get(table, [])) == 0:
            # Уже вытащили в rows выше — вставляем
            if self._insert_cb and rows:
                await self._safe_insert(table, rows)

    async def flush(self, table: Optional[str] = None) -> None:
        """Принудительный flush одной или всех таблиц."""
        async with self._lock:
            if table:
                tables_to_flush = {table: self._buffer.pop(table, [])}
            else:
                tables_to_flush = dict(self._buffer)
                self._buffer.clear()

        if self._insert_cb:
            for tbl, rows in tables_to_flush.items():
                if rows:
                    await self._safe_insert(tbl, rows)

    async def _safe_insert(self, table: str, rows: List[Tuple]) -> None:
        """Вызывает insert_cb; при ошибке логирует, не пробрасывает исключение."""
        try:
            await self._insert_cb(table, rows)
        except Exception as exc:
            logger.error("TelemetryBuffer: ошибка вставки в %s (%d строк): %s", table, len(rows), exc)


# ---------------------------------------------------------------------------
# TelemetryService — основной класс
# ---------------------------------------------------------------------------

class TelemetryService:
    """
    Сервис телеметрии: dual-write в ClickHouse + query-методы для дашбордов.

    Использует clickhouse-connect при наличии, иначе падбэк на aiohttp HTTP API.
    Буферизирует строки в TelemetryBuffer и сбрасывает каждые flush_interval секунд.

    Пример использования:
        svc = TelemetryService(redis_client=redis)
        await svc.start()
        await svc.log_activity(tenant_id=1, account_id=5, action_type="warmup_read", ...)
        # при завершении приложения:
        await svc.stop()
    """

    def __init__(
        self,
        redis_client=None,
        flush_interval: int = 5,
        buffer_max_size: int = 1000,
    ):
        from config import settings  # noqa: WPS433 (локальный импорт — избегаем циклических)

        self._host = getattr(settings, "CLICKHOUSE_HOST", "localhost")
        self._port = int(getattr(settings, "CLICKHOUSE_PORT", 8123))
        self._db = getattr(settings, "CLICKHOUSE_DB", "default")
        self._user = getattr(settings, "CLICKHOUSE_USER", "default")
        self._password = getattr(settings, "CLICKHOUSE_PASSWORD", "")

        self._redis = redis_client
        self._ch_client = None  # clickhouse_connect.AsyncClient
        self._aiohttp_session = None  # aiohttp.ClientSession для fallback
        self._use_http_fallback = not _CH_CONNECT_AVAILABLE

        self._buffer = TelemetryBuffer(
            flush_interval=flush_interval,
            max_size=buffer_max_size,
        )
        self._buffer.set_insert_callback(self._insert_batch)

        self._flush_task: Optional[asyncio.Task] = None
        self._running = False

    # ------------------------------------------------------------------
    # Жизненный цикл
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Инициализирует соединение, создаёт таблицы, запускает flush loop."""
        await self._connect()
        await self._create_tables()
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop(), name="telemetry-flush")
        logger.info("TelemetryService запущен (host=%s, db=%s)", self._host, self._db)

    async def stop(self) -> None:
        """Сбрасывает остаток буфера и закрывает соединение."""
        self._running = False
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        # Финальный flush
        await self._buffer.flush()
        await self._close()
        logger.info("TelemetryService остановлен")

    # ------------------------------------------------------------------
    # Соединение
    # ------------------------------------------------------------------

    async def _connect(self) -> None:
        """Устанавливает соединение с ClickHouse."""
        if not self._use_http_fallback:
            try:
                self._ch_client = await clickhouse_connect.get_async_client(
                    host=self._host,
                    port=self._port,
                    database=self._db,
                    username=self._user,
                    password=self._password,
                )
                logger.info("ClickHouse: подключено через clickhouse-connect")
                return
            except Exception as exc:
                logger.warning("clickhouse-connect: ошибка %s — переключаемся на HTTP fallback", exc)
                self._use_http_fallback = True

        # HTTP fallback через aiohttp
        if not _AIOHTTP_AVAILABLE:
            raise RuntimeError(
                "Нет ни clickhouse-connect, ни aiohttp. "
                "Установите хотя бы одну из зависимостей."
            )
        self._aiohttp_session = aiohttp.ClientSession()
        logger.info("ClickHouse: используется HTTP API fallback (aiohttp)")

    async def _close(self) -> None:
        """Закрывает соединение."""
        if self._ch_client:
            try:
                await self._ch_client.close()
            except Exception:
                pass
            self._ch_client = None
        if self._aiohttp_session and not self._aiohttp_session.closed:
            await self._aiohttp_session.close()
            self._aiohttp_session = None

    async def ping(self) -> bool:
        """Health check: возвращает True если ClickHouse доступен."""
        try:
            if not self._use_http_fallback and self._ch_client:
                result = await self._ch_client.query("SELECT 1")
                return bool(result)
            else:
                url = self._http_url("SELECT 1")
                async with self._aiohttp_session.get(url) as resp:
                    return resp.status == 200
        except Exception as exc:
            logger.warning("ClickHouse ping failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # DDL
    # ------------------------------------------------------------------

    async def _create_tables(self) -> None:
        """Создаёт все четыре таблицы, если они ещё не существуют."""
        for ddl_name, ddl in [
            ("activity_log", _DDL_ACTIVITY_LOG),
            ("farm_events", _DDL_FARM_EVENTS),
            ("ai_telemetry", _DDL_AI_TELEMETRY),
            ("analytics_5m", _DDL_ANALYTICS_5M),
        ]:
            try:
                await self._execute(ddl)
                logger.debug("ClickHouse: таблица %s готова", ddl_name)
            except Exception as exc:
                logger.error("ClickHouse: ошибка создания таблицы %s: %s", ddl_name, exc)

    # ------------------------------------------------------------------
    # Flush loop
    # ------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        """Периодически сбрасывает буфер каждые flush_interval секунд."""
        while self._running:
            await asyncio.sleep(self._buffer._flush_interval)
            try:
                await self._buffer.flush()
            except Exception as exc:
                logger.warning("TelemetryService flush error: %s", exc)

    async def flush_batch(self) -> None:
        """Публичный метод для принудительного flush (можно вызвать из тестов)."""
        await self._buffer.flush()

    # ------------------------------------------------------------------
    # Dual-write методы
    # ------------------------------------------------------------------

    async def log_activity(
        self,
        tenant_id: int,
        account_id: int,
        action_type: str,
        details: Any = None,
        *,
        success: bool = True,
        duration_ms: int = 0,
        error_msg: str = "",
        row_id: int = 0,
        created_at: Optional[datetime] = None,
    ) -> None:
        """
        Логирует одно действие аккаунта (AccountActivityLog) в ClickHouse.
        Соответствует колонкам таблицы activity_log.
        """
        row = (
            row_id,
            tenant_id,
            account_id,
            action_type,
            int(success),
            duration_ms or 0,
            error_msg or "",
            _safe_json(details),
            created_at or _utcnow(),
        )
        await self._buffer.add("activity_log", row)

    async def log_farm_event(
        self,
        tenant_id: int,
        farm_id: int,
        event_type: str,
        *,
        thread_id: int = 0,
        severity: str = "info",
        message: str = "",
        metadata: Any = None,
        row_id: int = 0,
        created_at: Optional[datetime] = None,
    ) -> None:
        """
        Логирует событие фермы (FarmEvent) в ClickHouse.
        Соответствует колонкам таблицы farm_events.
        """
        row = (
            row_id,
            tenant_id,
            farm_id,
            thread_id or 0,
            event_type,
            severity,
            message or "",
            _safe_json(metadata),
            created_at or _utcnow(),
        )
        await self._buffer.add("farm_events", row)

    async def log_ai_request(
        self,
        tenant_id: int,
        task_type: str,
        provider: str,
        model: str,
        status: str,
        *,
        workspace_id: int = 0,
        outcome: str = "executed_as_requested",
        cost: float = 0.0,
        latency_ms: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        fallback_used: bool = False,
        json_parse_fail: bool = False,
        quality_score: float = 0.0,
        row_id: int = 0,
        created_at: Optional[datetime] = None,
    ) -> None:
        """
        Логирует AI-запрос (AIRequest) в ClickHouse.
        Соответствует колонкам таблицы ai_telemetry.
        """
        row = (
            row_id,
            tenant_id,
            workspace_id or 0,
            task_type,
            provider,
            model,
            status,
            outcome,
            latency_ms or 0,
            tokens_in or 0,
            tokens_out or 0,
            float(cost or 0.0),
            int(fallback_used),
            int(json_parse_fail),
            float(quality_score or 0.0),
            created_at or _utcnow(),
        )
        await self._buffer.add("ai_telemetry", row)

    async def log_analytics_event(
        self,
        tenant_id: int,
        workspace_id: int,
        metric: str,
        *,
        success: bool = True,
        created_at: Optional[datetime] = None,
    ) -> None:
        """
        Добавляет инкремент в pre-aggregated таблицу analytics_5m.
        SummingMergeTree автоматически суммирует строки с одинаковым ключом ORDER BY.
        """
        bucket = _round_to_5m(created_at or _utcnow())
        success_cnt = 1 if success else 0
        fail_cnt = 0 if success else 1
        row = (
            tenant_id,
            workspace_id or 0,
            metric,
            bucket,
            1,           # total
            success_cnt,
            fail_cnt,
        )
        await self._buffer.add("analytics_5m", row)

    # ------------------------------------------------------------------
    # Query методы для дашбордов
    # ------------------------------------------------------------------

    async def get_activity_timeline(
        self,
        tenant_id: int,
        account_id: int,
        days: int = 7,
    ) -> List[Dict]:
        """
        Возвращает лог активности аккаунта за последние N дней.
        Сортировка по убыванию created_at, лимит 500 строк.
        """
        since = _utcnow() - timedelta(days=days)
        query = """
            SELECT
                id,
                action_type,
                success,
                duration_ms,
                error_msg,
                details,
                created_at
            FROM activity_log
            WHERE tenant_id = {tenant_id:UInt32}
              AND account_id = {account_id:UInt32}
              AND created_at >= {since:DateTime}
            ORDER BY created_at DESC
            LIMIT 500
        """
        rows = await self._query(
            query,
            params={
                "tenant_id": tenant_id,
                "account_id": account_id,
                "since": since,
            },
        )
        return [
            {
                "id": r[0],
                "action_type": r[1],
                "success": bool(r[2]),
                "duration_ms": r[3],
                "error_msg": r[4],
                "details": _try_parse_json(r[5]),
                "created_at": r[6].isoformat() if hasattr(r[6], "isoformat") else str(r[6]),
            }
            for r in rows
        ]

    async def get_farm_stats(
        self,
        tenant_id: int,
        farm_id: int,
        hours: int = 24,
    ) -> Dict:
        """
        Агрегированная статистика событий фермы за последние N часов.
        Возвращает словарь: итоги по типам событий и по severity.
        """
        since = _utcnow() - timedelta(hours=hours)
        query = """
            SELECT
                event_type,
                severity,
                count() AS cnt
            FROM farm_events
            WHERE tenant_id = {tenant_id:UInt32}
              AND farm_id    = {farm_id:UInt32}
              AND created_at >= {since:DateTime}
            GROUP BY event_type, severity
            ORDER BY cnt DESC
        """
        rows = await self._query(
            query,
            params={
                "tenant_id": tenant_id,
                "farm_id": farm_id,
                "since": since,
            },
        )
        by_type: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}
        for event_type, severity, cnt in rows:
            by_type[event_type] = by_type.get(event_type, 0) + cnt
            by_severity[severity] = by_severity.get(severity, 0) + cnt
        return {
            "farm_id": farm_id,
            "period_hours": hours,
            "by_event_type": by_type,
            "by_severity": by_severity,
            "total_events": sum(by_type.values()),
        }

    async def get_ai_cost_summary(
        self,
        tenant_id: int,
        days: int = 30,
    ) -> Dict:
        """
        Сводка расходов AI за последние N дней.
        Разбивка по provider, model, task_type.
        """
        since = _utcnow() - timedelta(days=days)
        query = """
            SELECT
                provider,
                model,
                task_type,
                count()              AS requests,
                sum(tokens_in)       AS total_tokens_in,
                sum(tokens_out)      AS total_tokens_out,
                sum(cost_usd)        AS total_cost_usd,
                avg(latency_ms)      AS avg_latency_ms,
                countIf(status = 'succeeded') AS successes,
                countIf(json_parse_fail = 1)  AS json_failures
            FROM ai_telemetry
            WHERE tenant_id = {tenant_id:UInt32}
              AND created_at >= {since:DateTime}
            GROUP BY provider, model, task_type
            ORDER BY total_cost_usd DESC
        """
        rows = await self._query(
            query,
            params={"tenant_id": tenant_id, "since": since},
        )
        items = []
        total_cost = 0.0
        for row in rows:
            provider, model, task_type, reqs, tin, tout, cost, latency, suc, jf = row
            cost_f = float(cost or 0.0)
            total_cost += cost_f
            items.append({
                "provider": provider,
                "model": model,
                "task_type": task_type,
                "requests": reqs,
                "tokens_in": tin,
                "tokens_out": tout,
                "cost_usd": round(cost_f, 6),
                "avg_latency_ms": round(float(latency or 0), 1),
                "successes": suc,
                "json_failures": jf,
            })
        return {
            "tenant_id": tenant_id,
            "period_days": days,
            "total_cost_usd": round(total_cost, 6),
            "breakdown": items,
        }

    async def get_analytics_timeseries(
        self,
        tenant_id: int,
        metric: str,
        from_dt: datetime,
        to_dt: datetime,
        bucket: str = "5m",
    ) -> List[Dict]:
        """
        Временной ряд из pre-aggregated таблицы analytics_5m.

        bucket: "5m" | "1h" | "1d"
        Возвращает список словарей {bucket, total, success_cnt, fail_cnt}.
        """
        # Определяем функцию округления
        bucket_fn_map = {
            "5m": "toStartOfFiveMinutes",
            "1h": "toStartOfHour",
            "1d": "toStartOfDay",
        }
        bucket_fn = bucket_fn_map.get(bucket, "toStartOfFiveMinutes")

        query = f"""
            SELECT
                {bucket_fn}(bucket)  AS ts,
                sum(total)           AS total,
                sum(success_cnt)     AS success_cnt,
                sum(fail_cnt)        AS fail_cnt
            FROM analytics_5m
            WHERE tenant_id  = {{tenant_id:UInt32}}
              AND metric      = {{metric:String}}
              AND bucket     >= {{from_dt:DateTime}}
              AND bucket     <= {{to_dt:DateTime}}
            GROUP BY ts
            ORDER BY ts ASC
        """
        rows = await self._query(
            query,
            params={
                "tenant_id": tenant_id,
                "metric": metric,
                "from_dt": from_dt,
                "to_dt": to_dt,
            },
        )
        return [
            {
                "bucket": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                "total": r[1],
                "success_cnt": r[2],
                "fail_cnt": r[3],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Историческая синхронизация из PostgreSQL
    # ------------------------------------------------------------------

    async def sync_historical(
        self,
        table: str,
        since_date: datetime,
        batch_size: int = 10_000,
        pg_session_factory=None,
    ) -> int:
        """
        Одноразовая миграция исторических данных из PostgreSQL → ClickHouse.

        Поддерживаемые значения table: "activity_log", "farm_events", "ai_telemetry".
        Прогресс сохраняется в Redis под ключом ch_sync:{table}:last_id
        для возможности возобновления после прерывания.

        pg_session_factory: async callable без аргументов, возвращает SQLAlchemy AsyncSession.
        Если не передан, метод завершится с предупреждением.

        Возвращает количество перенесённых строк.
        """
        if pg_session_factory is None:
            logger.warning("sync_historical: pg_session_factory не передан — пропуск")
            return 0

        redis_key = f"ch_sync:{table}:last_id"
        last_id = 0
        if self._redis:
            val = await self._redis.get(redis_key)
            if val:
                last_id = int(val)
                logger.info("sync_historical %s: resuming from id=%d", table, last_id)

        # Маппинг таблицы → SQL + трансформатор строки
        config = self._sync_config(table, since_date)
        if config is None:
            logger.error("sync_historical: таблица %r не поддерживается", table)
            return 0

        pg_table, query_tmpl, row_transformer, ch_table = config
        total_synced = 0

        async with pg_session_factory() as session:
            while True:
                # Параметризованный запрос — без raw SQL concatenation
                from sqlalchemy import text as sa_text  # noqa: WPS433
                result = await session.execute(
                    sa_text(query_tmpl),
                    {"last_id": last_id, "since_date": since_date, "batch_size": batch_size},
                )
                pg_rows = result.fetchall()
                if not pg_rows:
                    break

                ch_rows = [row_transformer(r) for r in pg_rows]
                await self._insert_batch(ch_table, ch_rows)
                total_synced += len(ch_rows)
                last_id = pg_rows[-1][0]  # первая колонка всегда id

                # Сохраняем прогресс в Redis
                if self._redis:
                    await self._redis.set(redis_key, last_id)

                logger.info(
                    "sync_historical %s: перенесено %d строк, last_id=%d",
                    table,
                    total_synced,
                    last_id,
                )
                if len(pg_rows) < batch_size:
                    break

        logger.info("sync_historical %s: итого %d строк", table, total_synced)
        return total_synced

    def _sync_config(
        self,
        table: str,
        since_date: datetime,
    ) -> Optional[Tuple[str, str, Any, str]]:
        """
        Возвращает (pg_table_name, sql_query, row_transformer, ch_table_name)
        для указанной логической таблицы.
        """
        if table == "activity_log":
            query = """
                SELECT id, tenant_id, account_id, action_type,
                       success, duration_ms, error_message, details, created_at
                FROM account_activity_logs
                WHERE id > :last_id
                  AND created_at >= :since_date
                ORDER BY id ASC
                LIMIT :batch_size
            """
            def _transform(r):
                return (
                    r[0],                    # id
                    r[1],                    # tenant_id
                    r[2],                    # account_id
                    r[3] or "",             # action_type
                    int(r[4]) if r[4] is not None else 1,  # success
                    r[5] or 0,              # duration_ms
                    r[6] or "",             # error_message
                    _safe_json(r[7]),       # details
                    r[8] if r[8] else _utcnow(),  # created_at
                )
            return ("account_activity_logs", query, _transform, "activity_log")

        if table == "farm_events":
            query = """
                SELECT id, tenant_id, farm_id, thread_id, event_type,
                       severity, message, metadata, created_at
                FROM farm_events
                WHERE id > :last_id
                  AND created_at >= :since_date
                ORDER BY id ASC
                LIMIT :batch_size
            """
            def _transform(r):
                return (
                    r[0],
                    r[1],
                    r[2],
                    r[3] or 0,
                    r[4] or "",
                    r[5] or "info",
                    r[6] or "",
                    _safe_json(r[7]),
                    r[8] if r[8] else _utcnow(),
                )
            return ("farm_events", query, _transform, "farm_events")

        if table == "ai_telemetry":
            query = """
                SELECT id, tenant_id, workspace_id, task_type, executed_provider,
                       executed_model, status, outcome, latency_ms,
                       prompt_tokens, completion_tokens, estimated_cost_usd,
                       fallback_used, json_parse_failed, quality_score, created_at
                FROM ai_requests
                WHERE id > :last_id
                  AND created_at >= :since_date
                ORDER BY id ASC
                LIMIT :batch_size
            """
            def _transform(r):
                return (
                    r[0],
                    r[1],
                    r[2] or 0,
                    r[3] or "",
                    r[4] or "",
                    r[5] or "",
                    r[6] or "",
                    r[7] or "executed_as_requested",
                    r[8] or 0,
                    r[9] or 0,
                    r[10] or 0,
                    float(r[11] or 0.0),
                    int(r[12]) if r[12] is not None else 0,
                    int(r[13]) if r[13] is not None else 0,
                    float(r[14] or 0.0),
                    r[15] if r[15] else _utcnow(),
                )
            return ("ai_requests", query, _transform, "ai_telemetry")

        return None

    # ------------------------------------------------------------------
    # Низкоуровневые методы выполнения SQL
    # ------------------------------------------------------------------

    async def _execute(self, query: str, params: Optional[Dict] = None) -> None:
        """Выполняет DDL или INSERT без возврата результата."""
        if not self._use_http_fallback and self._ch_client:
            await self._ch_client.command(query, parameters=params)
        else:
            await self._http_execute(query, params)

    async def _query(self, query: str, params: Optional[Dict] = None) -> List[Tuple]:
        """Выполняет SELECT и возвращает список кортежей."""
        if not self._use_http_fallback and self._ch_client:
            result = await self._ch_client.query(query, parameters=params)
            return result.result_rows
        else:
            return await self._http_query(query, params)

    async def _insert_batch(self, table: str, rows: List[Tuple]) -> None:
        """
        Вставляет пачку строк в указанную таблицу.
        Использует INSERT ... VALUES через clickhouse-connect или HTTP API.
        """
        if not rows:
            return

        _table_columns = {
            "activity_log": [
                "id", "tenant_id", "account_id", "action_type",
                "success", "duration_ms", "error_msg", "details", "created_at",
            ],
            "farm_events": [
                "id", "tenant_id", "farm_id", "thread_id",
                "event_type", "severity", "message", "metadata", "created_at",
            ],
            "ai_telemetry": [
                "id", "tenant_id", "workspace_id", "task_type", "provider", "model",
                "status", "outcome", "latency_ms", "tokens_in", "tokens_out", "cost_usd",
                "fallback_used", "json_parse_fail", "quality_score", "created_at",
            ],
            "analytics_5m": [
                "tenant_id", "workspace_id", "metric", "bucket",
                "total", "success_cnt", "fail_cnt",
            ],
        }

        columns = _table_columns.get(table)
        if not columns:
            logger.error("_insert_batch: неизвестная таблица %r", table)
            return

        if not self._use_http_fallback and self._ch_client:
            # clickhouse-connect принимает список кортежей
            await self._ch_client.insert(
                table,
                data=rows,
                column_names=columns,
            )
        else:
            await self._http_insert(table, columns, rows)

    # ------------------------------------------------------------------
    # HTTP API fallback (aiohttp)
    # ------------------------------------------------------------------

    def _http_url(self, query: str) -> str:
        """Формирует URL для ClickHouse HTTP API."""
        import urllib.parse
        base = f"http://{self._host}:{self._port}/"
        q = urllib.parse.quote_plus(query)
        return f"{base}?query={q}&database={self._db}&user={self._user}&password={self._password}"

    async def _http_execute(self, query: str, params: Optional[Dict] = None) -> None:
        """Выполняет DDL через HTTP API."""
        if not self._aiohttp_session:
            raise RuntimeError("aiohttp session не инициализирована")
        url = f"http://{self._host}:{self._port}/"
        async with self._aiohttp_session.post(
            url,
            data=query.encode("utf-8"),
            params={
                "database": self._db,
                "user": self._user,
                "password": self._password,
            },
        ) as resp:
            if resp.status not in (200, 204):
                body = await resp.text()
                raise RuntimeError(f"ClickHouse HTTP error {resp.status}: {body[:300]}")

    async def _http_query(self, query: str, params: Optional[Dict] = None) -> List[Tuple]:
        """Выполняет SELECT через HTTP API, возвращает list of tuples."""
        if not self._aiohttp_session:
            raise RuntimeError("aiohttp session не инициализирована")
        # ClickHouse FORMAT TabSeparated
        full_query = query.strip().rstrip(";") + " FORMAT TabSeparated"
        url = f"http://{self._host}:{self._port}/"
        async with self._aiohttp_session.post(
            url,
            data=full_query.encode("utf-8"),
            params={
                "database": self._db,
                "user": self._user,
                "password": self._password,
            },
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"ClickHouse HTTP query error {resp.status}: {body[:300]}")
            text = await resp.text()
        result = []
        for line in text.splitlines():
            if line.strip():
                result.append(tuple(line.split("\t")))
        return result

    async def _http_insert(
        self,
        table: str,
        columns: List[str],
        rows: List[Tuple],
    ) -> None:
        """Вставляет строки через HTTP API в формате TabSeparated."""
        if not self._aiohttp_session:
            raise RuntimeError("aiohttp session не инициализирована")
        cols_str = ", ".join(columns)
        header = f"INSERT INTO {table} ({cols_str}) FORMAT TabSeparated\n"
        lines = []
        for row in rows:
            parts = []
            for val in row:
                if isinstance(val, datetime):
                    parts.append(val.strftime("%Y-%m-%d %H:%M:%S"))
                elif val is None:
                    parts.append("")
                else:
                    parts.append(str(val).replace("\t", " ").replace("\n", " "))
            lines.append("\t".join(parts))
        body = header + "\n".join(lines)
        url = f"http://{self._host}:{self._port}/"
        async with self._aiohttp_session.post(
            url,
            data=body.encode("utf-8"),
            params={
                "database": self._db,
                "user": self._user,
                "password": self._password,
            },
        ) as resp:
            if resp.status not in (200, 204):
                text = await resp.text()
                raise RuntimeError(f"ClickHouse HTTP insert error {resp.status}: {text[:300]}")


# ---------------------------------------------------------------------------
# Вспомогательная функция для безопасного парсинга JSON из ClickHouse
# ---------------------------------------------------------------------------

def _try_parse_json(s: str) -> Any:
    """Пытается распарсить строку как JSON; при неудаче возвращает строку."""
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


# ---------------------------------------------------------------------------
# Глобальный singleton (инициализируется в lifespan ops_api)
# ---------------------------------------------------------------------------

_telemetry_service: Optional[TelemetryService] = None


def get_telemetry_service() -> Optional[TelemetryService]:
    """Возвращает глобальный экземпляр TelemetryService или None если не инициализирован."""
    return _telemetry_service


def set_telemetry_service(svc: TelemetryService) -> None:
    """Устанавливает глобальный экземпляр (вызывается из lifespan)."""
    global _telemetry_service
    _telemetry_service = svc
