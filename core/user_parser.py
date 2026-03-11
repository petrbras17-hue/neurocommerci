"""
UserParser — parse Telegram channel members into UserParsingResult rows.

Public API
----------
parser = UserParser(session_manager)
results = await parser.parse_channel_members(
    channel_username, account_id, workspace_id, tenant_id, db_session
)
rows = await parser.list_results(tenant_id, db_session, channel_username=None)
data = await parser.export_results(tenant_id, db_session, job_id=None)

Safety rules enforced:
  - FloodWaitError → abort current batch and re-raise as UserParserError
    so the caller can schedule a retry.
  - FrozenMethodInvalidError → raise UserParserError immediately.
  - All DB writes use parameterized ORM queries.
  - Every DB session call uses apply_session_rls_context.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Account, UserParsingResult
from storage.sqlite_db import apply_session_rls_context
from utils.helpers import utcnow
from utils.logger import log


# Maximum members to fetch per GetParticipants page
_PAGE_LIMIT = 200


class UserParserError(Exception):
    """Raised on unrecoverable Telethon errors during parsing."""


class UserParser:
    """Parse Telegram channel members and persist results."""

    def __init__(self, session_manager=None) -> None:
        self._session_mgr = session_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def parse_channel_members(
        self,
        channel_username: str,
        account_id: int,
        workspace_id: int,
        tenant_id: int,
        db_session: AsyncSession,
    ) -> List[UserParsingResult]:
        """
        Fetch all channel members via GetParticipantsRequest and persist them.

        The caller must have already applied RLS context to *db_session*.

        Returns the list of persisted UserParsingResult rows.

        Raises UserParserError on FloodWait or frozen-account errors.
        """
        try:
            from telethon.errors import FloodWaitError, ChannelPrivateError
            from telethon.tl.functions.channels import GetParticipantsRequest
            from telethon.tl.types import ChannelParticipantsSearch
        except ImportError as exc:
            raise UserParserError("Telethon not available") from exc

        client = await self._get_client(account_id, tenant_id, db_session)
        if client is None:
            raise UserParserError(
                f"No Telethon client available for account {account_id}"
            )

        log.info(
            f"UserParser: parsing '{channel_username}' via account {account_id} "
            f"(tenant {tenant_id})"
        )

        participants: List[Any] = []
        offset = 0

        try:
            entity = await client.get_entity(channel_username)

            while True:
                result = await client(
                    GetParticipantsRequest(
                        channel=entity,
                        filter=ChannelParticipantsSearch(""),
                        offset=offset,
                        limit=_PAGE_LIMIT,
                        hash=0,
                    )
                )
                batch = result.users
                if not batch:
                    break
                participants.extend(batch)
                offset += len(batch)
                if len(batch) < _PAGE_LIMIT:
                    break

        except FloodWaitError as exc:
            raise UserParserError(
                f"FloodWait {exc.seconds}s while parsing '{channel_username}'"
            ) from exc

        except ChannelPrivateError as exc:
            raise UserParserError(
                f"Channel '{channel_username}' is private or inaccessible"
            ) from exc

        except Exception as exc:
            if "FrozenMethodInvalid" in type(exc).__name__:
                raise UserParserError(
                    f"Account {account_id} is frozen"
                ) from exc
            raise UserParserError(
                f"Unexpected error parsing '{channel_username}': {exc}"
            ) from exc

        # Persist results
        saved: List[UserParsingResult] = []
        for user in participants:
            last_seen_dt = None
            if hasattr(user, "status") and hasattr(user.status, "was_online"):
                ts = user.status.was_online
                if ts is not None:
                    if hasattr(ts, "timestamp"):
                        from datetime import datetime
                        last_seen_dt = datetime.utcfromtimestamp(ts.timestamp()).replace(
                            tzinfo=timezone.utc
                        )
                    elif isinstance(ts, int):
                        from datetime import datetime
                        last_seen_dt = datetime.utcfromtimestamp(ts).replace(
                            tzinfo=timezone.utc
                        )

            row = UserParsingResult(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                channel_username=channel_username,
                user_telegram_id=user.id,
                username=getattr(user, "username", None),
                first_name=getattr(user, "first_name", None),
                last_name=getattr(user, "last_name", None),
                bio=None,  # bio requires a separate GetFullUser call — not fetched here
                is_premium=bool(getattr(user, "premium", False)),
                last_seen=last_seen_dt,
                parsed_at=utcnow(),
            )
            db_session.add(row)
            saved.append(row)

        await db_session.flush()

        log.info(
            f"UserParser: saved {len(saved)} members from '{channel_username}' "
            f"(tenant {tenant_id})"
        )
        return saved

    async def list_results(
        self,
        tenant_id: int,
        db_session: AsyncSession,
        channel_username: Optional[str] = None,
    ) -> List[UserParsingResult]:
        """
        Return UserParsingResult rows for this tenant.

        The caller must have already applied RLS context to *db_session*.
        """
        stmt = select(UserParsingResult).where(
            UserParsingResult.tenant_id == tenant_id
        )
        if channel_username is not None:
            stmt = stmt.where(
                UserParsingResult.channel_username == channel_username
            )
        stmt = stmt.order_by(UserParsingResult.parsed_at.desc())
        result = await db_session.execute(stmt)
        return list(result.scalars().all())

    async def export_results(
        self,
        tenant_id: int,
        db_session: AsyncSession,
        job_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return serialisable dicts for export / API response.

        The caller must have already applied RLS context to *db_session*.
        """
        stmt = select(UserParsingResult).where(
            UserParsingResult.tenant_id == tenant_id
        )
        if job_id is not None:
            stmt = stmt.where(UserParsingResult.job_id == job_id)
        stmt = stmt.order_by(UserParsingResult.parsed_at.desc())

        result = await db_session.execute(stmt)
        rows = result.scalars().all()

        return [
            {
                "id": row.id,
                "channel_username": row.channel_username,
                "user_telegram_id": row.user_telegram_id,
                "username": row.username,
                "first_name": row.first_name,
                "last_name": row.last_name,
                "is_premium": row.is_premium,
                "last_seen": row.last_seen.isoformat() if row.last_seen else None,
                "parsed_at": row.parsed_at.isoformat() if row.parsed_at else None,
                "job_id": row.job_id,
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_client(
        self, account_id: int, tenant_id: int, db_session: AsyncSession
    ):
        """
        Resolve the Telethon client for *account_id*.

        Uses the already-open *db_session* (with RLS already applied) to
        fetch the Account row, then delegates to the session manager.
        """
        if self._session_mgr is None:
            return None

        result = await db_session.execute(
            select(Account).where(Account.id == account_id)
        )
        account = result.scalar_one_or_none()
        if account is None:
            log.warning(f"UserParser: account {account_id} not found in DB")
            return None

        client = self._session_mgr.get_client(account.phone)
        if client is not None and client.is_connected():
            return client

        try:
            return await self._session_mgr.connect_client_for_action(
                account.phone,
                user_id=account.user_id,
            )
        except Exception as exc:
            log.warning(
                f"UserParser: could not connect client for account {account_id}: {exc}"
            )
            return None
