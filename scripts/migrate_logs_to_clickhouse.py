#!/usr/bin/env python3
"""
NEURO COMMENTING — Скрипт одноразовой миграции логов из PostgreSQL в ClickHouse.

Использование:
    python scripts/migrate_logs_to_clickhouse.py --table activity_log --since 2026-01-01
    python scripts/migrate_logs_to_clickhouse.py --table all --since 2026-01-01 --batch-size 5000
    python scripts/migrate_logs_to_clickhouse.py --table ai_telemetry --since 2026-03-01 --dry-run

Поддерживаемые таблицы:
    activity_log    — account_activity_logs из PostgreSQL
    farm_events     — farm_events из PostgreSQL
    ai_telemetry    — ai_requests из PostgreSQL
    all             — все три таблицы последовательно

Прогресс сохраняется в Redis (ключ ch_sync:{table}:last_id) для resume-поддержки.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Добавляем корень проекта в sys.path чтобы импортировать config и storage
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("migrate_logs")


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_json(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)


def _parse_date(s: str) -> datetime:
    """Парсит дату в форматах YYYY-MM-DD или YYYY-MM-DDTHH:MM:SS."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Неверный формат даты: {s!r}. Используйте YYYY-MM-DD или YYYY-MM-DDTHH:MM:SS")


# ---------------------------------------------------------------------------
# ProgressBar — простой текстовый прогресс-бар без внешних зависимостей
# ---------------------------------------------------------------------------

class ProgressBar:
    """Простой прогресс-бар в stdout с отображением ETA."""

    def __init__(self, desc: str, total: Optional[int] = None, width: int = 40):
        self.desc = desc
        self.total = total
        self.width = width
        self._count = 0
        self._start = time.monotonic()

    def update(self, n: int = 1) -> None:
        self._count += n
        self._render()

    def _render(self) -> None:
        elapsed = time.monotonic() - self._start
        rate = self._count / elapsed if elapsed > 0 else 0
        eta_str = ""
        bar_str = ""
        if self.total and self.total > 0:
            pct = min(self._count / self.total, 1.0)
            filled = int(self.width * pct)
            bar_str = "[" + "#" * filled + "-" * (self.width - filled) + "] "
            remaining = self.total - self._count
            eta_sec = remaining / rate if rate > 0 else 0
            eta_str = f" ETA {int(eta_sec)}s"
        rate_str = f"{rate:.0f} rows/s" if rate > 0 else ""
        sys.stdout.write(
            f"\r{self.desc}: {bar_str}{self._count} rows  {rate_str}{eta_str}   "
        )
        sys.stdout.flush()

    def finish(self) -> None:
        elapsed = time.monotonic() - self._start
        sys.stdout.write(f"\n{self.desc}: готово, {self._count} строк за {elapsed:.1f}с\n")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Конфигурация миграции для каждой таблицы
# ---------------------------------------------------------------------------

def _activity_log_config(since_date: datetime):
    """Возвращает (count_sql, select_sql, transformer, ch_table)."""
    count_sql = """
        SELECT count(*) FROM account_activity_logs
        WHERE created_at >= :since_date
    """
    select_sql = """
        SELECT id, tenant_id, account_id, action_type,
               success, duration_ms, error_message, details, created_at
        FROM account_activity_logs
        WHERE id > :last_id
          AND created_at >= :since_date
        ORDER BY id ASC
        LIMIT :batch_size
    """
    def transformer(r) -> Tuple:
        return (
            r[0],                                    # id (UInt64)
            int(r[1] or 0),                          # tenant_id
            int(r[2] or 0),                          # account_id
            str(r[3] or ""),                         # action_type
            int(r[4]) if r[4] is not None else 1,    # success
            int(r[5] or 0),                          # duration_ms
            str(r[6] or ""),                         # error_msg
            _safe_json(r[7]),                        # details
            r[8] if r[8] else _utcnow(),             # created_at
        )
    return count_sql, select_sql, transformer, "activity_log"


def _farm_events_config(since_date: datetime):
    count_sql = """
        SELECT count(*) FROM farm_events
        WHERE created_at >= :since_date
    """
    select_sql = """
        SELECT id, tenant_id, farm_id, thread_id, event_type,
               severity, message, metadata, created_at
        FROM farm_events
        WHERE id > :last_id
          AND created_at >= :since_date
        ORDER BY id ASC
        LIMIT :batch_size
    """
    def transformer(r) -> Tuple:
        return (
            int(r[0] or 0),
            int(r[1] or 0),
            int(r[2] or 0),
            int(r[3] or 0),
            str(r[4] or ""),
            str(r[5] or "info"),
            str(r[6] or ""),
            _safe_json(r[7]),
            r[8] if r[8] else _utcnow(),
        )
    return count_sql, select_sql, transformer, "farm_events"


def _ai_telemetry_config(since_date: datetime):
    count_sql = """
        SELECT count(*) FROM ai_requests
        WHERE created_at >= :since_date
    """
    select_sql = """
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
    def transformer(r) -> Tuple:
        return (
            int(r[0] or 0),
            int(r[1] or 0),
            int(r[2] or 0),
            str(r[3] or ""),
            str(r[4] or ""),
            str(r[5] or ""),
            str(r[6] or ""),
            str(r[7] or "executed_as_requested"),
            int(r[8] or 0),
            int(r[9] or 0),
            int(r[10] or 0),
            float(r[11] or 0.0),
            int(r[12]) if r[12] is not None else 0,
            int(r[13]) if r[13] is not None else 0,
            float(r[14] or 0.0),
            r[15] if r[15] else _utcnow(),
        )
    return count_sql, select_sql, transformer, "ai_telemetry"


_TABLE_CONFIGS = {
    "activity_log": _activity_log_config,
    "farm_events": _farm_events_config,
    "ai_telemetry": _ai_telemetry_config,
}


# ---------------------------------------------------------------------------
# Класс мигратора
# ---------------------------------------------------------------------------

class ClickHouseMigrator:
    """
    Мигрирует строки из PostgreSQL в ClickHouse пачками.
    Поддерживает dry-run режим (только подсчёт строк, без записи).
    """

    def __init__(
        self,
        pg_database_url: str,
        ch_host: str,
        ch_port: int,
        ch_db: str,
        ch_user: str,
        ch_password: str,
        redis_url: str,
        batch_size: int = 10_000,
        dry_run: bool = False,
    ):
        self.pg_url = pg_database_url
        self.ch_host = ch_host
        self.ch_port = ch_port
        self.ch_db = ch_db
        self.ch_user = ch_user
        self.ch_password = ch_password
        self.redis_url = redis_url
        self.batch_size = batch_size
        self.dry_run = dry_run

        self._pg_engine = None
        self._redis = None
        self._ch_client = None
        self._aiohttp_session = None
        self._use_http_fallback = False

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Инициализирует соединения с PostgreSQL, Redis и ClickHouse."""
        # PostgreSQL
        from sqlalchemy.ext.asyncio import create_async_engine  # noqa: WPS433
        self._pg_engine = create_async_engine(self.pg_url, echo=False, pool_size=2)
        logger.info("PostgreSQL: подключено к %s", self.pg_url.split("@")[-1])

        # Redis
        try:
            import redis.asyncio as redis_async  # type: ignore  # noqa: WPS433
            self._redis = await redis_async.from_url(self.redis_url, decode_responses=True)
            await self._redis.ping()
            logger.info("Redis: подключено")
        except Exception as exc:
            logger.warning("Redis недоступен (%s) — resume-поддержка отключена", exc)
            self._redis = None

        # ClickHouse
        if not self.dry_run:
            await self._connect_clickhouse()

    async def teardown(self) -> None:
        """Закрывает все соединения."""
        if self._pg_engine:
            await self._pg_engine.dispose()
        if self._redis:
            await self._redis.aclose()
        if self._ch_client:
            try:
                await self._ch_client.close()
            except Exception:
                pass
        if self._aiohttp_session and not self._aiohttp_session.closed:
            await self._aiohttp_session.close()

    async def _connect_clickhouse(self) -> None:
        try:
            import clickhouse_connect  # type: ignore  # noqa: WPS433
            self._ch_client = await clickhouse_connect.get_async_client(
                host=self.ch_host,
                port=self.ch_port,
                database=self.ch_db,
                username=self.ch_user,
                password=self.ch_password,
            )
            logger.info("ClickHouse: подключено через clickhouse-connect")
        except (ImportError, Exception) as exc:
            logger.warning("clickhouse-connect недоступен (%s) — переключаемся на HTTP API", exc)
            self._use_http_fallback = True
            try:
                import aiohttp  # type: ignore  # noqa: WPS433
                self._aiohttp_session = aiohttp.ClientSession()
                logger.info("ClickHouse: используется HTTP API (aiohttp)")
            except ImportError:
                raise RuntimeError(
                    "Нет ни clickhouse-connect, ни aiohttp. "
                    "Установите хотя бы одну из зависимостей: pip install clickhouse-connect"
                )

    # ------------------------------------------------------------------
    # Основной метод миграции
    # ------------------------------------------------------------------

    async def migrate_table(
        self,
        table: str,
        since_date: datetime,
    ) -> int:
        """
        Мигрирует одну таблицу. Возвращает количество перенесённых строк.
        В dry-run режиме — только подсчитывает строки в PostgreSQL.
        """
        if table not in _TABLE_CONFIGS:
            logger.error("Таблица %r не поддерживается", table)
            return 0

        config_fn = _TABLE_CONFIGS[table]
        count_sql, select_sql, transformer, ch_table = config_fn(since_date)

        # Подсчёт общего числа строк для прогресс-бара
        total_rows = await self._count_pg_rows(count_sql, since_date)
        logger.info("Таблица %s: %d строк для миграции (с %s)", table, total_rows, since_date.date())

        if self.dry_run:
            logger.info("[DRY RUN] Таблица %s: найдено %d строк — реальная запись пропущена", table, total_rows)
            return total_rows

        # Получаем last_id из Redis (resume-поддержка)
        redis_key = f"ch_sync:{table}:last_id"
        last_id = 0
        if self._redis:
            val = await self._redis.get(redis_key)
            if val:
                last_id = int(val)
                logger.info("Resume: начинаем с id=%d (Redis key=%s)", last_id, redis_key)

        progress = ProgressBar(desc=f"Migrate {table}", total=total_rows)
        total_synced = 0

        from sqlalchemy import text as sa_text  # noqa: WPS433
        from sqlalchemy.ext.asyncio import AsyncSession  # noqa: WPS433

        async with AsyncSession(self._pg_engine) as session:
            while True:
                result = await session.execute(
                    sa_text(select_sql),
                    {
                        "last_id": last_id,
                        "since_date": since_date,
                        "batch_size": self.batch_size,
                    },
                )
                pg_rows = result.fetchall()
                if not pg_rows:
                    break

                ch_rows = [transformer(r) for r in pg_rows]
                await self._insert_to_clickhouse(ch_table, ch_rows)

                total_synced += len(ch_rows)
                last_id = pg_rows[-1][0]  # id всегда первая колонка

                # Сохраняем прогресс в Redis
                if self._redis:
                    await self._redis.set(redis_key, last_id)

                progress.update(len(ch_rows))

                if len(pg_rows) < self.batch_size:
                    break

        progress.finish()
        logger.info("Таблица %s: перенесено %d строк", table, total_synced)
        return total_synced

    # ------------------------------------------------------------------
    # PostgreSQL helpers
    # ------------------------------------------------------------------

    async def _count_pg_rows(self, count_sql: str, since_date: datetime) -> int:
        """Считает строки в PostgreSQL для отображения в прогресс-баре."""
        from sqlalchemy import text as sa_text  # noqa: WPS433
        from sqlalchemy.ext.asyncio import AsyncSession  # noqa: WPS433
        try:
            async with AsyncSession(self._pg_engine) as session:
                result = await session.execute(
                    sa_text(count_sql),
                    {"since_date": since_date},
                )
                row = result.fetchone()
                return int(row[0]) if row else 0
        except Exception as exc:
            logger.warning("Не удалось подсчитать строки: %s", exc)
            return 0

    # ------------------------------------------------------------------
    # ClickHouse insert helpers
    # ------------------------------------------------------------------

    _TABLE_COLUMNS: Dict[str, List[str]] = {
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
    }

    async def _insert_to_clickhouse(self, table: str, rows: List[Tuple]) -> None:
        columns = self._TABLE_COLUMNS.get(table, [])
        if not columns:
            logger.error("Неизвестные колонки для таблицы %s", table)
            return

        if not self._use_http_fallback and self._ch_client:
            await self._ch_client.insert(table, data=rows, column_names=columns)
        else:
            await self._http_insert(table, columns, rows)

    async def _http_insert(
        self,
        table: str,
        columns: List[str],
        rows: List[Tuple],
    ) -> None:
        """Вставляет строки через ClickHouse HTTP API в формате TabSeparated."""
        import aiohttp  # type: ignore  # noqa: WPS433

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
        url = f"http://{self.ch_host}:{self.ch_port}/"

        async with self._aiohttp_session.post(
            url,
            data=body.encode("utf-8"),
            params={
                "database": self.ch_db,
                "user": self.ch_user,
                "password": self.ch_password,
            },
        ) as resp:
            if resp.status not in (200, 204):
                text = await resp.text()
                raise RuntimeError(f"ClickHouse HTTP insert error {resp.status}: {text[:400]}")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Одноразовая миграция логов из PostgreSQL в ClickHouse",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--table",
        required=True,
        choices=["activity_log", "farm_events", "ai_telemetry", "all"],
        help="Таблица для миграции (all — все три)",
    )
    p.add_argument(
        "--since",
        required=True,
        help="Дата начала миграции (YYYY-MM-DD или YYYY-MM-DDTHH:MM:SS)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=10_000,
        dest="batch_size",
        help="Размер пачки (строк за одну SELECT+INSERT итерацию, default=10000)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Только подсчёт строк в PostgreSQL без записи в ClickHouse",
    )
    return p


async def main(args: argparse.Namespace) -> int:
    since_date = _parse_date(args.since)
    logger.info(
        "Запуск миграции: table=%s, since=%s, batch_size=%d, dry_run=%s",
        args.table,
        since_date.date(),
        args.batch_size,
        args.dry_run,
    )

    # Загружаем настройки проекта
    from config import settings  # noqa: WPS433

    pg_url = settings.DATABASE_URL
    if not pg_url:
        logger.error("DATABASE_URL не задан в .env — прервано")
        return 1

    migrator = ClickHouseMigrator(
        pg_database_url=pg_url,
        ch_host=getattr(settings, "CLICKHOUSE_HOST", "localhost"),
        ch_port=int(getattr(settings, "CLICKHOUSE_PORT", 8123)),
        ch_db=getattr(settings, "CLICKHOUSE_DB", "default"),
        ch_user=getattr(settings, "CLICKHOUSE_USER", "default"),
        ch_password=getattr(settings, "CLICKHOUSE_PASSWORD", ""),
        redis_url=settings.REDIS_URL,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )

    await migrator.setup()

    tables_to_migrate = (
        ["activity_log", "farm_events", "ai_telemetry"]
        if args.table == "all"
        else [args.table]
    )

    total_all = 0
    start_all = time.monotonic()
    for table in tables_to_migrate:
        t_start = time.monotonic()
        count = await migrator.migrate_table(table, since_date)
        elapsed = time.monotonic() - t_start
        total_all += count
        logger.info(
            "Таблица %s: %d строк за %.1fс (%.0f rows/s)",
            table,
            count,
            elapsed,
            count / elapsed if elapsed > 0 else 0,
        )

    elapsed_all = time.monotonic() - start_all
    logger.info(
        "Миграция завершена: итого %d строк за %.1fс",
        total_all,
        elapsed_all,
    )

    await migrator.teardown()
    return 0


if __name__ == "__main__":
    _parser = build_parser()
    _args = _parser.parse_args()
    sys.exit(asyncio.run(main(_args)))
