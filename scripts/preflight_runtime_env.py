#!/usr/bin/env python3
"""Preflight checks for compliance runtime env before compose up."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings


def _read_env_pairs(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Compliance runtime preflight")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    env_path = Path(settings.model_config["env_file"])
    env_map = _read_env_pairs(env_path)

    errors: list[str] = []
    warnings: list[str] = []

    pinned_required = str(env_map.get("PINNED_PHONE_REQUIRED", str(settings.PINNED_PHONE_REQUIRED))).lower() == "true"
    if pinned_required:
        if not env_map.get("WORKER_A_PINNED_PHONE", "").strip():
            errors.append("WORKER_A_PINNED_PHONE is required when PINNED_PHONE_REQUIRED=true")
        if not env_map.get("WORKER_B_PINNED_PHONE", "").strip():
            errors.append("WORKER_B_PINNED_PHONE is required when PINNED_PHONE_REQUIRED=true")

    strict_mode = (env_map.get("COMPLIANCE_MODE") or settings.COMPLIANCE_MODE or "").strip().lower() == "strict"
    strict_parser_only = str(env_map.get("STRICT_PARSER_ONLY", str(settings.STRICT_PARSER_ONLY))).lower() == "true"
    if strict_mode and strict_parser_only and not (env_map.get("PARSER_ONLY_PHONE") or "").strip():
        errors.append("PARSER_ONLY_PHONE is required in strict parser-only mode")

    if strict_mode and str(env_map.get("ENABLE_EMOJI_SWAP", str(settings.ENABLE_EMOJI_SWAP))).lower() == "true":
        warnings.append("ENABLE_EMOJI_SWAP=true in strict mode (allowed only as emergency)")

    report = {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "checked_env": str(env_path),
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Preflight runtime env")
        print(f"  ok: {report['ok']}")
        for item in errors:
            print(f"  error: {item}")
        for item in warnings:
            print(f"  warning: {item}")

    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
