"""
MassReactionService — send reactions from multiple accounts to posts in a channel.

Public API
----------
svc = MassReactionService(session_manager)
job = await svc.create_job(channel_username, reaction_type, account_ids,
                           workspace_id, tenant_id, db_session)
await svc.run_job(job_id, tenant_id)
jobs = await svc.list_jobs(tenant_id, db_session)
job  = await svc.get_job(job_id, tenant_id, db_session)

Safety rules enforced:
  - Random 5–30 s delay between accounts to avoid rate-limit clustering.
  - FloodWaitError on an account → skip that account, increment failed count.
  - FrozenMethodInvalidError → skip account immediately.
  - Each DB write uses its own short-lived session with RLS.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Account, ReactionJob
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log


# Status constants
_STATUS_PENDING = "pending"
_STATUS_RUNNING = "running"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"

# Delay range between each account reaction (seconds)
_INTER_ACCOUNT_DELAY_MIN = 5
_INTER_ACCOUNT_DELAY_MAX = 30

# How many recent posts to fetch when no specific post_id is given
_DEFAULT_POST_LIMIT = 10


class MassReactionService:
    """
    Manages mass-reaction jobs.

    One instance per process.  Background tasks are tracked in memory:
        _tasks[job_id] = asyncio.Task
    """

    def __init__(self, session_manager=None) -> None:
        self._session_mgr = session_manager
        self._tasks: dict[int, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_job(
        self,
        channel_username: str,
        reaction_type: str,
        account_ids: List[int],
        workspace_id: int,
        tenant_id: int,
        db_session: AsyncSession,
    ) -> ReactionJob:
        """
        Persist a new ReactionJob row and return it.

        The caller must have already applied RLS context to *db_session*.
        """
        job = ReactionJob(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            channel_username=channel_username,
            reaction_type=reaction_type or "random",
            account_ids=account_ids,
            status=_STATUS_PENDING,
            total_reactions=len(account_ids),
            successful_reactions=0,
            failed_reactions=0,
        )
        db_session.add(job)
        await db_session.flush()
        log.info(
            f"MassReactionService: created job {job.id} "
            f"for channel '{channel_username}' "
            f"({len(account_ids)} accounts, tenant {tenant_id})"
        )
        return job

    async def run_job(self, job_id: int, tenant_id: int) -> None:
        """
        Launch a background asyncio task to execute *job_id*.

        Idempotent: if a task for this job already exists and is still running,
        this call is a no-op.
        """
        existing = self._tasks.get(job_id)
        if existing and not existing.done():
            log.debug(
                f"MassReactionService: job {job_id} task already running, ignoring"
            )
            return

        task = asyncio.create_task(
            self._execute_job(job_id=job_id, tenant_id=tenant_id),
            name=f"mass-reactions-job{job_id}",
        )
        def _on_done(t: asyncio.Task, jid: int = job_id) -> None:
            exc = t.exception() if not t.cancelled() else None
            if exc:
                log.error("mass-reactions job %s failed: %s", jid, exc, exc_info=exc)
        task.add_done_callback(_on_done)
        self._tasks[job_id] = task

    async def list_jobs(
        self,
        tenant_id: int,
        db_session: AsyncSession,
        status: Optional[str] = None,
    ) -> List[ReactionJob]:
        """
        Return ReactionJob rows for this tenant.

        The caller must have already applied RLS context to *db_session*.
        """
        stmt = select(ReactionJob).where(ReactionJob.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(ReactionJob.status == status)
        stmt = stmt.order_by(ReactionJob.id.desc()).limit(500)
        result = await db_session.execute(stmt)
        return list(result.scalars().all())

    async def get_job(
        self,
        job_id: int,
        tenant_id: int,
        db_session: AsyncSession,
    ) -> Optional[ReactionJob]:
        """
        Return a single ReactionJob by id.

        The caller must have already applied RLS context to *db_session*.
        """
        result = await db_session.execute(
            select(ReactionJob).where(
                ReactionJob.id == job_id,
                ReactionJob.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Internal: background execution
    # ------------------------------------------------------------------

    async def _execute_job(self, job_id: int, tenant_id: int) -> None:
        """Drive the full reaction job from pending → running → completed/failed."""
        await self._set_job_status(job_id, tenant_id, _STATUS_RUNNING)

        job = await self._load_job(job_id, tenant_id)
        if job is None:
            log.warning(
                f"MassReactionService: job {job_id} not found, aborting"
            )
            return

        account_ids: List[int] = job.account_ids or []
        channel_username: str = job.channel_username
        reaction_type: str = job.reaction_type or "random"
        specific_post_id: Optional[int] = job.post_id

        successful = 0
        failed = 0

        for account_id in account_ids:
            ok = await self._react_with_account(
                account_id=account_id,
                tenant_id=tenant_id,
                channel_username=channel_username,
                reaction_type=reaction_type,
                specific_post_id=specific_post_id,
            )
            if ok:
                successful += 1
            else:
                failed += 1

            # Anti-detection delay between accounts
            delay = random.uniform(_INTER_ACCOUNT_DELAY_MIN, _INTER_ACCOUNT_DELAY_MAX)
            await asyncio.sleep(delay)

        await self._complete_job(
            job_id=job_id,
            tenant_id=tenant_id,
            successful=successful,
            failed=failed,
        )
        # Clean up in-memory task reference to prevent unbounded growth.
        self._tasks.pop(job_id, None)
        log.info(
            f"MassReactionService: job {job_id} completed "
            f"(success={successful}, failed={failed})"
        )

    async def _react_with_account(
        self,
        account_id: int,
        tenant_id: int,
        channel_username: str,
        reaction_type: str,
        specific_post_id: Optional[int],
    ) -> bool:
        """
        Obtain the Telethon client for *account_id* and send one reaction.

        Returns True on success, False on any error.
        """
        try:
            from telethon.errors import FloodWaitError
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
        except ImportError:
            log.warning(
                "MassReactionService: Telethon not available, skipping reaction"
            )
            return False

        client = await self._get_client(account_id, tenant_id)
        if client is None:
            log.debug(
                f"MassReactionService: no client for account {account_id}, skipping"
            )
            return False

        emoji = _pick_emoji(reaction_type)

        try:
            entity = await client.get_entity(channel_username)

            if specific_post_id is not None:
                target_msg_id = specific_post_id
            else:
                messages = await client.get_messages(
                    entity, limit=_DEFAULT_POST_LIMIT
                )
                if not messages:
                    return False
                target_msg_id = random.choice(messages).id

            await client(
                SendReactionRequest(
                    peer=entity,
                    msg_id=target_msg_id,
                    reaction=[ReactionEmoji(emoticon=emoji)],
                )
            )
            log.debug(
                f"MassReactionService: account {account_id} reacted to "
                f"{channel_username}#{target_msg_id} with {emoji}"
            )
            return True

        except FloodWaitError as exc:
            log.warning(
                f"MassReactionService: FloodWait {exc.seconds}s "
                f"on account {account_id}, skipping"
            )
            return False

        except Exception as exc:
            if "FrozenMethodInvalid" in type(exc).__name__:
                log.warning(
                    f"MassReactionService: account {account_id} is frozen, skipping"
                )
                return False
            log.debug(
                f"MassReactionService: account {account_id} reaction error: {exc}"
            )
            return False

    # ------------------------------------------------------------------
    # DB helpers — each opens its own session
    # ------------------------------------------------------------------

    async def _load_job(
        self, job_id: int, tenant_id: int
    ) -> Optional[ReactionJob]:
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    result = await sess.execute(
                        select(ReactionJob).where(ReactionJob.id == job_id)
                    )
                    return result.scalar_one_or_none()
        except Exception as exc:
            log.warning(f"MassReactionService: _load_job error: {exc}", exc_info=True)
            return None

    async def _set_job_status(
        self, job_id: int, tenant_id: int, status: str
    ) -> None:
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    await sess.execute(
                        update(ReactionJob)
                        .where(ReactionJob.id == job_id)
                        .values(status=status)
                    )
        except Exception as exc:
            log.warning(
                f"MassReactionService: _set_job_status [{job_id}]: {exc}"
            )

    async def _complete_job(
        self,
        job_id: int,
        tenant_id: int,
        successful: int,
        failed: int,
    ) -> None:
        status = _STATUS_COMPLETED if failed == 0 else _STATUS_FAILED
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    await sess.execute(
                        update(ReactionJob)
                        .where(ReactionJob.id == job_id)
                        .values(
                            status=status,
                            successful_reactions=successful,
                            failed_reactions=failed,
                            completed_at=utcnow(),
                        )
                    )
        except Exception as exc:
            log.warning(
                f"MassReactionService: _complete_job [{job_id}]: {exc}"
            )

    async def _get_client(self, account_id: int, tenant_id: int):
        """Resolve a connected Telethon client for *account_id*, or return None."""
        if self._session_mgr is None:
            return None
        try:
            async with async_session() as sess:
                async with sess.begin():
                    await apply_session_rls_context(sess, tenant_id=tenant_id)
                    result = await sess.execute(
                        select(Account).where(Account.id == account_id)
                    )
                    account = result.scalar_one_or_none()
                    if account is None:
                        return None
                    # Cache attributes before session closes to avoid detached instance
                    phone = account.phone
                    user_id = account.user_id

            client = self._session_mgr.get_client(phone)
            if client is not None and client.is_connected():
                return client

            return await self._session_mgr.connect_client_for_action(
                phone,
                user_id=user_id,
            )
        except Exception as exc:
            log.warning(
                f"MassReactionService: _get_client [account={account_id}]: {exc}"
            )
            return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_REACTION_EMOJIS = [
    "\U0001f44d",  # thumbs up
    "\u2764\ufe0f",  # red heart
    "\U0001f525",  # fire
    "\U0001f44f",  # clapping hands
    "\U0001f914",  # thinking face
    "\U0001f44c",  # ok hand
    "\U0001f60d",  # heart eyes
    "\U0001f4af",  # 100
]


def _pick_emoji(reaction_type: str) -> str:
    """Return the emoji for *reaction_type*, or a random one."""
    mapping = {
        "thumbs_up": "\U0001f44d",
        "heart": "\u2764\ufe0f",
        "fire": "\U0001f525",
        "clap": "\U0001f44f",
        "ok": "\U0001f44c",
        "eyes": "\U0001f60d",
        "100": "\U0001f4af",
        "think": "\U0001f914",
    }
    return mapping.get(reaction_type) or random.choice(_REACTION_EMOJIS)
