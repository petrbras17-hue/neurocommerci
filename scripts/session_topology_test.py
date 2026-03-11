#!/usr/bin/env python3
"""Smoke checks for canonical session topology helpers."""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.session_topology import (
    audit_session_topology,
    canonical_session_paths,
    discover_session_assets,
    quarantine_noncanonical_assets,
)


def _touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        canonical_session, canonical_json = canonical_session_paths(base, 1, "+79990001122")
        _touch(canonical_session)
        _touch(canonical_json)

        _touch(base / "79990002233.session")
        _touch(base / "legacy-folder" / "79990003344.session")
        _touch(base / "79990001122.session")
        _touch(base / "79990001122.json")
        _touch(base / "legacy-folder" / "79990001122.session")

        discovery = discover_session_assets(base, known_user_ids=[1])
        assert "+79990001122" in discovery.assets
        assert discovery.assets["+79990001122"].source_kind == "canonical"
        assert discovery.assets["+79990002233"].source_kind == "flat"
        assert discovery.assets["+79990003344"].source_kind == "legacy_nested"

        topology = audit_session_topology(base, known_user_ids=[1])
        duplicate = next(item for item in topology["items"] if item["phone"] == "+79990001122")
        assert duplicate["status_kind"] == "duplicate"
        assert duplicate["safe_to_quarantine"] is True

        quarantine = quarantine_noncanonical_assets(base, known_user_ids=[1], dry_run=False)
        assert quarantine["moved_files"] >= 3
        assert canonical_session.exists()
        assert canonical_json.exists()
        assert not (base / "79990001122.session").exists()
        assert not (base / "79990001122.json").exists()

    print("session_topology_test_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
