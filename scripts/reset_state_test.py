#!/usr/bin/env python3
"""Smoke test for clean-slate user-state reset."""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, select

from config import settings
from core.reset_service import reset_user_state
from storage.models import Account, Channel, User
from storage.sqlite_db import async_session, dispose_engine, init_db


TEST_TELEGRAM_ID = 999000222
TEST_PHONE = "+79990000991"


async def _cleanup() -> None:
    async with async_session() as session:
        account = (
            await session.execute(select(Account).where(Account.phone == TEST_PHONE))
        ).scalar_one_or_none()
        if account is not None:
            await session.execute(delete(Account).where(Account.id == account.id))
        await session.execute(delete(Channel).where(Channel.title == "Reset Test Channel"))
        await session.execute(delete(User).where(User.telegram_id == TEST_TELEGRAM_ID))
        await session.commit()


async def main() -> int:
    await init_db()
    old_sessions_dir = settings.SESSIONS_DIR
    settings.SESSIONS_DIR = "data/test_reset_sessions"
    sessions_root = settings.sessions_path
    try:
        await _cleanup()
        async with async_session() as session:
            user = User(telegram_id=TEST_TELEGRAM_ID, username="resettest", first_name="Reset", is_active=True, is_admin=False)
            session.add(user)
            await session.flush()
            account = Account(
                phone=TEST_PHONE,
                session_file="79990000991.session",
                user_id=user.id,
                status="active",
                health_status="alive",
                lifecycle_stage="uploaded",
            )
            channel = Channel(
                user_id=user.id,
                telegram_id=123456789,
                title="Reset Test Channel",
                username="reset_test_channel",
                subscribers=100,
                review_state="discovered",
                publish_mode="research_only",
            )
            session.add(account)
            session.add(channel)
            await session.commit()
            target_user_id = int(user.id)

        tenant_dir = sessions_root / str(target_user_id)
        tenant_dir.mkdir(parents=True, exist_ok=True)
        (tenant_dir / "79990000991.session").write_text("", encoding="utf-8")
        (tenant_dir / "79990000991.json").write_text(
            '{"phone":"+79990000991","session_file":"79990000991.session","app_id":2040,"app_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","device":"Pixel 8","sdk":"Android 14","app_version":"12.4.3"}',
            encoding="utf-8",
        )

        payload = await reset_user_state(user_id=target_user_id, actor="reset_state_test", dry_run=False)
        assert payload["ok"] is True
        assert int(payload["deleted"]["accounts"]) == 1
        assert int(payload["deleted"]["channels"]) == 1
        assert int(payload["session_archive"]["moved_files"]) == 2
        assert Path(payload["archive_path"]).exists()

        async with async_session() as session:
            account = (
                await session.execute(select(Account).where(Account.phone == TEST_PHONE))
            ).scalar_one_or_none()
            channel = (
                await session.execute(select(Channel).where(Channel.title == "Reset Test Channel"))
            ).scalar_one_or_none()
        assert account is None
        assert channel is None
        print("reset_state_test_ok")
        return 0
    finally:
        await _cleanup()
        shutil.rmtree(sessions_root, ignore_errors=True)
        settings.SESSIONS_DIR = old_sessions_dir
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
