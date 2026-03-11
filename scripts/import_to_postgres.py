#!/usr/bin/env python3
"""Import migration export bundle into PostgreSQL.

Expected input: directory from scripts/export_local_state.py with json/*.json files.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extras import execute_values
except ModuleNotFoundError:
    psycopg2 = None
    sql = None
    execute_values = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


IMPORT_ORDER = ["users", "proxies", "accounts", "channels", "posts", "comments"]


def normalize_pg_dsn(dsn: str) -> str:
    dsn = dsn.strip()
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql://" + dsn.split("postgresql+asyncpg://", 1)[1]
    return dsn


def load_rows(export_dir: Path, table: str) -> list[dict[str, Any]]:
    path = export_dir / "json" / f"{table}.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Invalid JSON payload for {table}: expected list")
    return payload


def get_table_columns(cur, table: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return [row[0] for row in cur.fetchall()]


def get_table_column_types(cur, table: str) -> dict[str, str]:
    cur.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return {name: dtype for name, dtype in cur.fetchall()}


def normalize_value(value: Any, data_type: str) -> Any:
    if value is None:
        return None

    dtype = data_type.lower()
    if dtype == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            raw = value.strip().lower()
            if raw in {"1", "true", "t", "yes", "y"}:
                return True
            if raw in {"0", "false", "f", "no", "n", ""}:
                return False
        return value

    if dtype in {"smallint", "integer", "bigint"}:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, str):
            raw = value.strip()
            if raw == "":
                return None
            return int(raw)
        if isinstance(value, (int, float)):
            return int(value)
        return value

    if dtype in {"numeric", "real", "double precision", "decimal"}:
        if isinstance(value, str):
            raw = value.strip()
            if raw == "":
                return None
            return float(raw)
        if isinstance(value, (int, float)):
            return float(value)
        return value

    if "timestamp" in dtype or dtype in {"date", "time without time zone", "time with time zone"}:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    return value


def upsert_rows(cur, table: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    db_columns = get_table_columns(cur, table)
    db_types = get_table_column_types(cur, table)
    if not db_columns:
        raise RuntimeError(f"Target table does not exist: {table}")

    # Keep only columns that exist in target DB.
    common = [col for col in db_columns if col in rows[0]]
    if not common:
        return 0

    values = []
    for row in rows:
        values.append(
            tuple(normalize_value(row.get(col), db_types.get(col, "text")) for col in common)
        )

    conflict_col = "id" if "id" in common else None
    update_cols = [col for col in common if col != conflict_col]

    insert_stmt = sql.SQL("INSERT INTO {table} ({fields}) VALUES %s").format(
        table=sql.Identifier(table),
        fields=sql.SQL(", ").join(sql.Identifier(col) for col in common),
    )

    if conflict_col:
        if update_cols:
            upsert_stmt = insert_stmt + sql.SQL(
                " ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
            ).format(
                conflict=sql.Identifier(conflict_col),
                updates=sql.SQL(", ").join(
                    sql.SQL("{col}=EXCLUDED.{col}").format(col=sql.Identifier(col))
                    for col in update_cols
                ),
            )
        else:
            upsert_stmt = insert_stmt + sql.SQL(" ON CONFLICT ({conflict}) DO NOTHING").format(
                conflict=sql.Identifier(conflict_col)
            )
    else:
        upsert_stmt = insert_stmt

    execute_values(cur, upsert_stmt.as_string(cur.connection), values, page_size=500)
    return len(values)


def fix_sequence(cur, table: str) -> None:
    cur.execute(
        sql.SQL(
            """
            SELECT setval(
                pg_get_serial_sequence(%s, 'id'),
                COALESCE((SELECT MAX(id) FROM {table}), 1),
                true
            )
            """
        ).format(table=sql.Identifier(table)),
        (table,),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Import local export bundle into PostgreSQL")
    parser.add_argument("--export-dir", required=True, help="Path to export directory")
    parser.add_argument(
        "--pg-dsn",
        default="",
        help="PostgreSQL DSN. If omitted, uses PG_DSN env or config.DATABASE_URL",
    )
    args = parser.parse_args()

    export_dir = Path(args.export_dir)
    if not export_dir.exists():
        print(f"ERROR: export dir does not exist: {export_dir}")
        return 1

    dsn = args.pg_dsn.strip() or os.environ.get("PG_DSN", "").strip()
    if not dsn:
        from config import settings

        dsn = settings.DATABASE_URL

    dsn = normalize_pg_dsn(dsn)
    if not dsn.startswith("postgresql://"):
        print("ERROR: provide a valid PostgreSQL DSN (postgresql://...)")
        return 1

    if psycopg2 is None:
        print("ERROR: psycopg2 is not installed. Install requirements.txt before import.")
        return 1

    conn = psycopg2.connect(dsn)
    imported: dict[str, int] = {}
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            for table in IMPORT_ORDER:
                rows = load_rows(export_dir, table)
                count = upsert_rows(cur, table, rows)
                imported[table] = count
                if count > 0:
                    fix_sequence(cur, table)

        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: import failed: {exc}")
        return 1
    finally:
        conn.close()

    print("Import completed.")
    for table in IMPORT_ORDER:
        print(f"  - {table}: {imported.get(table, 0)} rows")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
