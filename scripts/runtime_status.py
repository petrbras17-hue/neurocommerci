#!/usr/bin/env python3
"""Distributed runtime status CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.runtime_snapshot import collect_runtime_snapshot, print_runtime_snapshot


async def main() -> int:
    parser = argparse.ArgumentParser(description="Distributed runtime status")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    report = await collect_runtime_snapshot(
        initialize_db=True,
        close_backends=True,
        dispose_db=True,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_runtime_snapshot(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
