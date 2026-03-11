#!/usr/bin/env python3
"""Smoke test for shared account onboarding persistence."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

TEST_TELEGRAM_ID = 999000111
TEST_PHONE = "+79990000001"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, select

from config import settings
from core.onboarding_service import get_onboarding_status, record_onboarding_step, start_onboarding_run
from storage.models import Account, AccountOnboardingRun, AccountOnboardingStep, User
from storage.sqlite_db import async_session, dispose_engine, init_db


async def _cleanup() -> None:
    async with async_session() as session:
        account = (
            await session.execute(select(Account).where(Account.phone == TEST_PHONE))
        ).scalar_one_or_none()
        if account is not None:
            await session.execute(delete(AccountOnboardingStep).where(AccountOnboardingStep.account_id == account.id))
            await session.execute(delete(AccountOnboardingRun).where(AccountOnboardingRun.account_id == account.id))
            await session.execute(delete(Account).where(Account.id == account.id))
        await session.execute(delete(User).where(User.telegram_id == TEST_TELEGRAM_ID))
        await session.commit()


async def main() -> int:
    await init_db()
    old_memory_path = settings.ONBOARDING_MEMORY_PATH
    settings.ONBOARDING_MEMORY_PATH = "data/test_onboarding_memory.md"
    memory_path = settings.onboarding_memory_path
    try:
        await _cleanup()
        async with async_session() as session:
            user = User(telegram_id=TEST_TELEGRAM_ID, username="test", first_name="Test", is_active=True, is_admin=False)
            session.add(user)
            await session.flush()
            account = Account(
                phone=TEST_PHONE,
                session_file="79990000001.session",
                user_id=user.id,
                status="active",
                health_status="unknown",
                lifecycle_stage="uploaded",
            )
            session.add(account)
            await session.commit()

        payload = await start_onboarding_run(TEST_PHONE, user_id=None, mode="cli", channel="cli", actor="test:start")
        assert payload["ok"] is True
        step = await record_onboarding_step(
            TEST_PHONE,
            step_key="auth_check",
            actor="test:auth",
            source="cli",
            channel="cli",
            result="authorized",
            notes="smoke",
        )
        assert step["ok"] is True
        snapshot = await get_onboarding_status(TEST_PHONE, limit_steps=10)
        assert snapshot["run"] is not None
        assert snapshot["run"]["current_step"] == "auth_check"
        assert len(snapshot["steps"]) >= 2
        assert memory_path.exists()
        print("onboarding_flow_test_ok")
        return 0
    finally:
        await _cleanup()
        memory_path.unlink(missing_ok=True)
        settings.ONBOARDING_MEMORY_PATH = old_memory_path
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
