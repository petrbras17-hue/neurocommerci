#!/usr/bin/env python3
"""Fetch and snapshot official compliance sources used by policy pack."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SOURCES = [
    "https://telegram.org/tos",
    "https://core.telegram.org/api/terms",
    "https://core.telegram.org/api/errors#error-handling",
    "https://docs.telethon.dev/en/stable/quick-references/faq.html#my-account-was-deleted-limited-when-using-the-library",
    "https://docs.telethon.dev/en/stable/concepts/sessions.html",
    "https://telegram.org/tos/content-licensing",
    "https://telegram.org/tos/bot-developers",
]


def fetch(url: str, timeout: int = 20) -> str:
    req = Request(url, headers={"User-Agent": "NEURO-COMPLIANCE-SYNC/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return body.decode("utf-8", "ignore")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync compliance source snapshots")
    parser.add_argument(
        "--out-dir",
        default="artifacts/compliance_snapshots",
        help="Output directory for HTML snapshots",
    )
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    out_dir = PROJECT_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    checked_at = datetime.now(timezone.utc).isoformat()
    manifest: list[dict] = []

    for idx, url in enumerate(SOURCES, start=1):
        name = f"{idx:02d}_{url.replace('https://', '').replace('/', '_').replace('#', '_')}.html"
        path = out_dir / name
        item = {
            "url": url,
            "snapshot": str(path.relative_to(PROJECT_ROOT)),
            "checked_at": checked_at,
            "ok": False,
            "error": "",
        }
        try:
            content = fetch(url, timeout=args.timeout)
            path.write_text(content, encoding="utf-8")
            item["ok"] = True
            item["bytes"] = len(content.encode("utf-8", "ignore"))
        except Exception as exc:
            item["error"] = str(exc)
        manifest.append(item)

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    ok_count = sum(1 for i in manifest if i["ok"])
    print(f"synced={ok_count}/{len(manifest)} checked_at={checked_at}")
    print(f"manifest={manifest_path.relative_to(PROJECT_ROOT)}")
    return 0 if ok_count == len(manifest) else 1


if __name__ == "__main__":
    raise SystemExit(main())
