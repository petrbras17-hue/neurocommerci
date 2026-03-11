#!/usr/bin/env python3
"""Smoke test for digest summary formatting."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.digest_service import build_parser_task_digest


def main() -> int:
    task = {"kind": "keyword_search", "user_id": 7}
    report = {
        "kind": "keyword_search",
        "keywords": ["vpn", "telegram"],
        "found": 5,
        "saved": 3,
        "items": [
            {"title": "VPN News", "username": "vpnnews", "subscribers": 1500},
            {"title": "Telegram Tech", "username": "tgtech", "subscribers": 900},
        ],
    }
    text = build_parser_task_digest(task, report)
    assert "VPN News" in text
    assert "vpn, telegram" in text
    assert "Сохранено" in text
    print("digest_service_test_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
