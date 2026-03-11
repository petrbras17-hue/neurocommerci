#!/usr/bin/env python3
"""Export local NEURO COMMENTING state from SQLite and filesystem.

Creates a timestamped export bundle with:
- JSON and CSV dumps of key DB tables
- sessions/proxy inventory
- optional raw copies (DB/proxies/product posts)
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


TABLES = ["users", "proxies", "accounts", "channels", "posts", "comments"]


@dataclass
class TableDump:
    rows: list[dict[str, Any]]
    missing: bool = False
    error: str = ""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def serialize_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


def dump_table(conn: sqlite3.Connection, table: str) -> TableDump:
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
        columns = [d[0] for d in cur.description] if cur.description else []
        rows: list[dict[str, Any]] = []
        for raw in cur.fetchall():
            row = {col: serialize_value(val) for col, val in zip(columns, raw)}
            rows.append(row)
        return TableDump(rows=rows)
    except sqlite3.OperationalError as exc:
        return TableDump(rows=[], missing=True, error=str(exc))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def collect_sessions_inventory(sessions_dir: Path) -> list[dict[str, Any]]:
    sessions = []
    if not sessions_dir.exists():
        return sessions

    for session_path in sorted(sessions_dir.glob("*.session")):
        phone = session_path.stem
        json_path = sessions_dir / f"{phone}.json"
        sessions.append(
            {
                "phone": phone,
                "session_file": session_path.name,
                "session_size": session_path.stat().st_size,
                "has_metadata_json": json_path.exists(),
                "metadata_file": json_path.name if json_path.exists() else None,
            }
        )
    return sessions


def collect_proxy_inventory(proxies_path: Path) -> dict[str, Any]:
    if not proxies_path.exists():
        return {"exists": False, "count": 0, "sample": []}

    lines = [ln.strip() for ln in proxies_path.read_text(encoding="utf-8").splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    return {
        "exists": True,
        "count": len(lines),
        "sample": lines[:5],
    }


def main() -> int:
    from config import settings, BASE_DIR

    parser = argparse.ArgumentParser(description="Export local state for VPS migration")
    parser.add_argument("--db-path", default=str(BASE_DIR / settings.DB_PATH))
    parser.add_argument(
        "--out-dir",
        default=str(BASE_DIR / "artifacts" / f"local_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}"),
    )
    parser.add_argument("--include-raw", action="store_true", help="Copy raw DB/proxy/posts files")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    out_dir = Path(args.out_dir)
    json_dir = out_dir / "json"
    csv_dir = out_dir / "csv"
    inventory_dir = out_dir / "inventory"
    raw_dir = out_dir / "raw"

    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "generated_at_utc": utc_now_iso(),
        "db_path": str(db_path),
        "tables": {},
        "missing_tables": [],
        "inventory": {},
    }

    if not db_path.exists():
        write_json(out_dir / "manifest.json", {**manifest, "error": f"DB file not found: {db_path}"})
        print(f"ERROR: DB file not found: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for table in TABLES:
            dump = dump_table(conn, table)
            manifest["tables"][table] = {
                "count": len(dump.rows),
                "missing": dump.missing,
                "error": dump.error,
            }
            if dump.missing:
                manifest["missing_tables"].append(table)
                continue

            write_json(json_dir / f"{table}.json", dump.rows)
            write_csv(csv_dir / f"{table}.csv", dump.rows)
    finally:
        conn.close()

    sessions_dir = BASE_DIR / settings.SESSIONS_DIR
    proxies_path = BASE_DIR / settings.PROXY_LIST_FILE
    product_posts_path = BASE_DIR / "data" / "product_posts.json"

    sessions_inventory = collect_sessions_inventory(sessions_dir)
    proxy_inventory = collect_proxy_inventory(proxies_path)

    manifest["inventory"] = {
        "sessions": {
            "count": len(sessions_inventory),
            "path": str(sessions_dir),
        },
        "proxies": {
            "count": proxy_inventory["count"],
            "path": str(proxies_path),
            "exists": proxy_inventory["exists"],
        },
        "product_posts_exists": product_posts_path.exists(),
    }

    write_json(inventory_dir / "sessions_inventory.json", sessions_inventory)
    write_json(inventory_dir / "proxies_inventory.json", proxy_inventory)

    if args.include_raw:
        raw_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db_path, raw_dir / db_path.name)
        if proxies_path.exists():
            shutil.copy2(proxies_path, raw_dir / proxies_path.name)
        if product_posts_path.exists():
            shutil.copy2(product_posts_path, raw_dir / product_posts_path.name)

    write_json(out_dir / "manifest.json", manifest)

    print(f"Export completed: {out_dir}")
    for table, info in manifest["tables"].items():
        suffix = " (missing)" if info["missing"] else ""
        print(f"  - {table}: {info['count']}{suffix}")
    print(f"  - sessions: {len(sessions_inventory)}")
    print(f"  - proxies: {proxy_inventory['count']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
