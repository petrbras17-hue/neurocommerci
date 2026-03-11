#!/usr/bin/env python3
"""Reconcile accounts table with canonical session layout.

Usage examples:
  python3 scripts/reconcile_accounts_with_sessions.py
  python3 scripts/reconcile_accounts_with_sessions.py --user-id 1 --migrate-layout
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from storage.models import Account, User
from storage.sqlite_db import async_session, init_db, dispose_engine
from utils.helpers import utcnow
from utils.session_topology import (
    SessionAsset,
    canonical_session_dir,
    canonical_session_paths,
    discover_session_assets,
)


async def _load_user_ids() -> list[int]:
    async with async_session() as session:
        result = await session.execute(select(User.id).order_by(User.id))
        return [int(row[0]) for row in result.fetchall()]


async def _resolve_default_target_user(user_id: int | None) -> int | None:
    if user_id is not None:
        return int(user_id)
    user_ids = await _load_user_ids()
    if len(user_ids) == 1:
        return user_ids[0]
    return None


def _maybe_move_asset(
    asset: SessionAsset,
    *,
    target_user_id: int,
    dry_run: bool,
) -> SessionAsset:
    target_dir = canonical_session_dir(settings.sessions_path, target_user_id, create=not dry_run)
    target_session, target_json = canonical_session_paths(settings.sessions_path, target_user_id, asset.phone)
    if asset.session_path == target_session and asset.metadata_path == (target_json if target_json.exists() else asset.metadata_path):
        return SessionAsset(
            phone=asset.phone,
            session_path=target_session,
            metadata_path=target_json if target_json.exists() else asset.metadata_path,
            user_id=target_user_id,
            source_kind="canonical",
            source_dir=target_dir.name,
        )

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        if asset.session_path != target_session:
            shutil.move(str(asset.session_path), str(target_session))
        if asset.metadata_path and asset.metadata_path.exists() and asset.metadata_path != target_json:
            shutil.move(str(asset.metadata_path), str(target_json))

    return SessionAsset(
        phone=asset.phone,
        session_path=target_session,
        metadata_path=target_json if (dry_run or (asset.metadata_path and asset.metadata_path.exists())) else None,
        user_id=target_user_id,
        source_kind="canonical",
        source_dir=target_dir.name,
    )


async def reconcile(
    *,
    user_id: int | None = None,
    dry_run: bool = False,
    migrate_layout: bool = False,
) -> dict:
    known_user_ids = await _load_user_ids()
    default_user_id = await _resolve_default_target_user(user_id)
    discovery = discover_session_assets(settings.sessions_path, known_user_ids=known_user_ids)

    assets_by_phone: dict[str, SessionAsset] = {}
    unresolved: list[dict[str, str]] = []
    moved = 0
    metadata_ok = 0

    for phone, asset in discovery.assets.items():
        target_user_id = asset.user_id or default_user_id
        if user_id is not None and target_user_id is not None and int(target_user_id) != int(user_id):
            continue
        if asset.source_kind != "canonical" and not migrate_layout:
            unresolved.append(
                {
                    "phone": phone,
                    "source_kind": asset.source_kind,
                    "source_dir": asset.source_dir,
                    "session_path": str(asset.session_path),
                    "reason": "needs_migrate_layout",
                }
            )
            continue
        if target_user_id is None:
            unresolved.append(
                {
                    "phone": phone,
                    "source_kind": asset.source_kind,
                    "source_dir": asset.source_dir,
                    "session_path": str(asset.session_path),
                }
            )
            continue
        if migrate_layout:
            moved_asset = _maybe_move_asset(
                asset,
                target_user_id=int(target_user_id),
                dry_run=dry_run,
            )
            if moved_asset.session_path != asset.session_path:
                moved += 1
            asset = moved_asset
        else:
            target_session, target_json = canonical_session_paths(
                settings.sessions_path,
                int(target_user_id),
                phone,
            )
            asset = SessionAsset(
                phone=phone,
                session_path=target_session if asset.user_id == target_user_id else asset.session_path,
                metadata_path=target_json if asset.user_id == target_user_id and target_json.exists() else asset.metadata_path,
                user_id=int(target_user_id),
                source_kind=asset.source_kind if asset.user_id != target_user_id else "canonical",
                source_dir=asset.source_dir,
            )
        if asset.metadata_path is not None:
            metadata_ok += 1
        assets_by_phone[phone] = asset

    added = 0
    updated = 0
    orphaned = 0

    async with async_session() as session:
        query = select(Account)
        if user_id is not None:
            query = query.where((Account.user_id == user_id) | (Account.user_id.is_(None)))
        result = await session.execute(query)
        existing_accounts = list(result.scalars().all())
        existing_by_phone = {acc.phone: acc for acc in existing_accounts}

        for phone, asset in assets_by_phone.items():
            existing = existing_by_phone.get(phone)
            session_file = asset.session_path.name
            if existing is None:
                session.add(
                    Account(
                        phone=phone,
                        session_file=session_file,
                        user_id=asset.user_id,
                        status="active",
                        lifecycle_stage="uploaded",
                        created_at=utcnow(),
                    )
                )
                added += 1
                continue

            changed = False
            if existing.session_file != session_file:
                existing.session_file = session_file
                changed = True
            if asset.user_id is not None and existing.user_id != asset.user_id:
                existing.user_id = asset.user_id
                changed = True
            if existing.lifecycle_stage in {"orphaned", "", None, "packaging_error"}:
                existing.lifecycle_stage = "uploaded"
                changed = True
            if changed:
                updated += 1

        discovered_phones = set(assets_by_phone.keys())
        for account in existing_accounts:
            if account.phone not in discovered_phones and account.lifecycle_stage != "orphaned":
                account.lifecycle_stage = "orphaned"
                orphaned += 1

        if not dry_run:
            await session.commit()
        else:
            await session.rollback()

    return {
        "sessions_found": len(assets_by_phone),
        "metadata_ok": metadata_ok,
        "duplicates": len(discovery.duplicates),
        "duplicate_phones": sorted(discovery.duplicates.keys()),
        "added": added,
        "updated": updated,
        "orphaned": orphaned,
        "unresolved": unresolved,
        "unresolved_count": len(unresolved),
        "moved_to_canonical": moved,
        "dry_run": dry_run,
        "migrate_layout": migrate_layout,
        "user_id": user_id,
        "default_target_user_id": default_user_id,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile DB accounts with session files")
    parser.add_argument("--user-id", type=int, default=None, help="Optional user_id scope")
    parser.add_argument("--dry-run", action="store_true", help="Print report only without DB/file writes")
    parser.add_argument("--migrate-layout", action="store_true", help="Move files into canonical data/sessions/<user_id>/ layout")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    await init_db()
    try:
        report = await reconcile(
            user_id=args.user_id,
            dry_run=args.dry_run,
            migrate_layout=args.migrate_layout,
        )
    finally:
        await dispose_engine()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Reconcile accounts ↔ sessions")
        print(f"  user_id: {report['user_id']}")
        print(f"  default_target_user_id: {report['default_target_user_id']}")
        print(f"  sessions_found: {report['sessions_found']}")
        print(f"  metadata_ok: {report['metadata_ok']}")
        print(f"  duplicates: {report['duplicates']}")
        print(f"  added: {report['added']}")
        print(f"  updated: {report['updated']}")
        print(f"  orphaned: {report['orphaned']}")
        print(f"  unresolved_count: {report['unresolved_count']}")
        print(f"  moved_to_canonical: {report['moved_to_canonical']}")
        print(f"  dry_run: {report['dry_run']}")
        print(f"  migrate_layout: {report['migrate_layout']}")
        if report["duplicate_phones"]:
            print(f"  duplicate_phones: {report['duplicate_phones']}")
        if report["unresolved"]:
            print("  unresolved:")
            for item in report["unresolved"][:20]:
                print(f"    - {item['phone']} ({item['source_kind']}:{item['source_dir']})")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
