#!/usr/bin/env python3
"""Smoke test for human-gated Gemini draft/apply workflow."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, select

from comments.scenarios import Scenario
from config import settings
from core.onboarding_service import get_onboarding_status, start_onboarding_run
import core.human_gated_workflow as workflow
from storage.models import (
    Account,
    AccountDraftArtifact,
    AccountOnboardingRun,
    AccountOnboardingStep,
    User,
)
from storage.sqlite_db import async_session, dispose_engine, init_db


TEST_PHONE = "+79990000002"
TEST_TELEGRAM_ID = 999000222


async def _cleanup() -> None:
    async with async_session() as session:
        account = (
            await session.execute(select(Account).where(Account.phone == TEST_PHONE))
        ).scalar_one_or_none()
        if account is not None:
            await session.execute(delete(AccountDraftArtifact).where(AccountDraftArtifact.account_id == account.id))
            await session.execute(delete(AccountOnboardingStep).where(AccountOnboardingStep.account_id == account.id))
            await session.execute(delete(AccountOnboardingRun).where(AccountOnboardingRun.account_id == account.id))
            await session.execute(delete(Account).where(Account.id == account.id))
        await session.execute(delete(User).where(User.telegram_id == TEST_TELEGRAM_ID))
        await session.commit()


async def main() -> int:
    await init_db()
    old_memory_path = settings.ONBOARDING_MEMORY_PATH
    settings.ONBOARDING_MEMORY_PATH = "data/test_human_gated_memory.md"
    memory_path = settings.onboarding_memory_path
    avatar_path = PROJECT_ROOT / "data" / "test_human_gated_avatar.png"

    originals = {
        "connect_client_for_action": workflow.session_mgr.connect_client_for_action,
        "disconnect_client": workflow.session_mgr.disconnect_client,
        "generate_profile": workflow.packager.generate_profile,
        "apply_profile": workflow.packager.apply_profile,
        "generate_username": workflow.packager.generate_username,
        "apply_username": workflow.packager.apply_username,
        "generate_avatar": workflow.packager.generate_avatar,
        "apply_avatar": workflow.packager.apply_avatar,
        "generate_channel_content": workflow.channel_setup.generate_channel_content,
        "create_channel_shell": workflow.channel_setup.create_channel_shell,
        "publish_content_to_channel": workflow.channel_setup.publish_content_to_channel,
        "comment_generate": workflow.comment_generator.generate,
        "comment_review": workflow.comment_reviewer.review_comment,
    }

    class DummyClient:
        pass

    try:
        await _cleanup()
        avatar_path.parent.mkdir(parents=True, exist_ok=True)
        avatar_path.write_bytes(b"fake-avatar")

        async with async_session() as session:
            user = User(telegram_id=TEST_TELEGRAM_ID, username="test", first_name="Test", is_active=True, is_admin=False)
            session.add(user)
            await session.flush()
            account = Account(
                phone=TEST_PHONE,
                session_file="79990000002.session",
                user_id=user.id,
                status="active",
                health_status="alive",
                lifecycle_stage="auth_verified",
                account_role="comment_candidate",
            )
            session.add(account)
            await session.commit()

        async def _connect_client_for_action(phone: str, user_id: int | None = None):
            return DummyClient()

        async def _disconnect_client(phone: str):
            return None

        async def _generate_profile(style: str = "casual"):
            return {
                "first_name": "Ирина",
                "last_name": "Тестова",
                "bio": "Технологии и полезные находки",
                "username_base": "irina_test",
            }

        async def _apply_profile(phone: str, profile: dict, channel_link: str = ""):
            return True

        async def _generate_username(phone: str, profile: dict):
            return "irina_test_01"

        async def _apply_username(phone: str, username: str):
            return True

        async def _generate_avatar(prompt_index: int = 0):
            return avatar_path

        async def _apply_avatar(phone: str, path: Path):
            return True

        async def _generate_channel_content(style: str = "casual"):
            return {
                "name": "Test Channel",
                "username": "test_channel_gate",
                "about": "Описание канала",
                "post": "Первый закреплённый пост",
            }

        async def _create_channel_shell(phone: str, content: dict, style: str = "casual"):
            return {
                "ok": True,
                "channel_id": 12345,
                "channel_title": content.get("name"),
                "channel_link": "https://t.me/test_channel_gate",
                "personal_channel_set": True,
            }

        async def _publish_content_to_channel(
            phone: str,
            *,
            channel_id: int,
            post_text: str,
            pin_post: bool = True,
            attach_personal_channel: bool = True,
        ):
            return {
                "ok": True,
                "channel_id": channel_id,
                "message_id": 777,
                "pin_applied": bool(pin_post),
                "post_text": post_text,
            }

        async def _comment_generate(post_text: str, persona_style: str = "casual"):
            return {
                "text": "Согласен, полезный материал.",
                "scenario": Scenario.A,
                "persona": persona_style,
                "source": "ai",
            }

        async def _comment_review(*, comment: str, post_text: str, scenario: Scenario):
            return {"approved": True, "summary": "ok"}

        workflow.session_mgr.connect_client_for_action = _connect_client_for_action
        workflow.session_mgr.disconnect_client = _disconnect_client
        workflow.packager.generate_profile = _generate_profile
        workflow.packager.apply_profile = _apply_profile
        workflow.packager.generate_username = _generate_username
        workflow.packager.apply_username = _apply_username
        workflow.packager.generate_avatar = _generate_avatar
        workflow.packager.apply_avatar = _apply_avatar
        workflow.channel_setup.generate_channel_content = _generate_channel_content
        workflow.channel_setup.create_channel_shell = _create_channel_shell
        workflow.channel_setup.publish_content_to_channel = _publish_content_to_channel
        workflow.comment_generator.generate = _comment_generate
        workflow.comment_reviewer.review_comment = _comment_review

        await start_onboarding_run(TEST_PHONE, mode="cli", channel="cli", actor="test:start")
        await workflow.generate_profile_draft(TEST_PHONE, actor="test:profile_draft", source="cli", channel="cli")
        await workflow.apply_profile_draft(TEST_PHONE, actor="test:profile_apply", source="cli", channel="cli")
        await workflow.generate_channel_draft(TEST_PHONE, actor="test:channel_draft", source="cli", channel="cli")
        await workflow.apply_channel_draft(TEST_PHONE, actor="test:channel_apply", source="cli", channel="cli")
        await workflow.generate_content_draft(TEST_PHONE, actor="test:content_draft", source="cli", channel="cli")
        await workflow.apply_content_draft(TEST_PHONE, actor="test:content_apply", source="cli", channel="cli")
        comment = await workflow.create_comment_draft(
            phone=TEST_PHONE,
            post_text="Тестовый пост",
            actor="test:comment_draft",
            source="cli",
            channel="cli",
        )
        await workflow.review_comment_draft(
            draft_id=int(comment["artifact_id"]),
            actor="test:comment_review",
            source="cli",
            channel="cli",
        )
        await workflow.approve_comment_draft(
            draft_id=int(comment["artifact_id"]),
            actor="test:comment_approve",
            source="cli",
            channel="cli",
        )
        await workflow.assign_account_role(
            TEST_PHONE,
            role="execution_ready",
            actor="test:assign_role",
            source="cli",
            channel="cli",
        )

        async with async_session() as session:
            account = (
                await session.execute(select(Account).where(Account.phone == TEST_PHONE))
            ).scalar_one()
            artifacts = list(
                (
                    await session.execute(
                        select(AccountDraftArtifact).where(AccountDraftArtifact.phone == TEST_PHONE)
                    )
                ).scalars().all()
            )

        snapshot = await get_onboarding_status(TEST_PHONE, limit_steps=20)
        assert account.lifecycle_stage == "execution_ready"
        assert account.account_role == "execution_ready"
        assert len(artifacts) >= 4
        assert snapshot["run"] is not None
        assert memory_path.exists()
        print("human_gated_workflow_test_ok")
        return 0
    finally:
        workflow.session_mgr.connect_client_for_action = originals["connect_client_for_action"]
        workflow.session_mgr.disconnect_client = originals["disconnect_client"]
        workflow.packager.generate_profile = originals["generate_profile"]
        workflow.packager.apply_profile = originals["apply_profile"]
        workflow.packager.generate_username = originals["generate_username"]
        workflow.packager.apply_username = originals["apply_username"]
        workflow.packager.generate_avatar = originals["generate_avatar"]
        workflow.packager.apply_avatar = originals["apply_avatar"]
        workflow.channel_setup.generate_channel_content = originals["generate_channel_content"]
        workflow.channel_setup.create_channel_shell = originals["create_channel_shell"]
        workflow.channel_setup.publish_content_to_channel = originals["publish_content_to_channel"]
        workflow.comment_generator.generate = originals["comment_generate"]
        workflow.comment_reviewer.review_comment = originals["comment_review"]
        await _cleanup()
        avatar_path.unlink(missing_ok=True)
        memory_path.unlink(missing_ok=True)
        settings.ONBOARDING_MEMORY_PATH = old_memory_path
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
