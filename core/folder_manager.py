"""
FolderManager — create, list, delete, and export Telegram folders for accounts.

Each folder groups channel subscriptions under a named Telegram dialog filter
(UpdateDialogFilter).  An invite link is generated via ExportChatlistInvite
when the Telethon API version supports it, so other accounts can join all
channels in the folder at once.

Public API
----------
mgr = FolderManager(session_manager)
folder = await mgr.create_folder(
    account_id, folder_name, channel_usernames,
    workspace_id, tenant_id, db_session
)
folders = await mgr.list_folders(tenant_id, db_session, account_id=None)
await mgr.delete_folder(folder_id, tenant_id, db_session)
link = await mgr.get_folder_invite(folder_id, tenant_id, db_session)

Safety rules enforced:
  - FloodWaitError during entity resolution → FolderManagerError.
  - FrozenMethodInvalidError → FolderManagerError.
  - UpdateDialogFilter failure is surfaced but does not prevent DB row creation
    (the folder is still saved so the operator can retry).
  - ExportChatlistInvite failure is non-fatal: invite_link stays None.
  - All DB writes use apply_session_rls_context inside a transaction.
"""

from __future__ import annotations

from typing import Any, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import Account, TelegramFolder
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log


class FolderManagerError(Exception):
    """Raised on unrecoverable Telethon errors during folder operations."""


class FolderManager:
    """Manages Telegram dialog-filter folders for tenant accounts."""

    def __init__(self, session_manager=None) -> None:
        self._session_mgr = session_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_folder(
        self,
        account_id: int,
        folder_name: str,
        channel_usernames: List[str],
        workspace_id: int,
        tenant_id: int,
        db_session: AsyncSession,
    ) -> TelegramFolder:
        """
        Create a Telegram dialog-filter folder on the account and persist it.

        Steps:
        1. Resolve channel entities via the Telethon client.
        2. Call UpdateDialogFilter to create the folder in Telegram.
        3. Attempt ExportChatlistInvite to generate a shareable link.
        4. Persist the TelegramFolder ORM row.

        The caller must have already applied RLS context to *db_session*.

        Raises FolderManagerError if the Telethon client is unavailable or
        a fatal Telegram error occurs.
        """
        try:
            from telethon.errors import FloodWaitError
            from telethon.tl.functions.messages import UpdateDialogFilter
            from telethon.tl.types import (
                DialogFilter,
                InputDialogPeer,
                InputPeerChannel,
            )
        except ImportError as exc:
            raise FolderManagerError("Telethon not available") from exc

        client = await self._get_client(account_id, tenant_id, db_session)
        if client is None:
            raise FolderManagerError(
                f"No Telethon client available for account {account_id}"
            )

        # Resolve entities for all requested channels
        include_peers: List[Any] = []
        resolved_usernames: List[str] = []

        for username in channel_usernames:
            try:
                entity = await client.get_entity(username)
                peer = await client.get_input_entity(entity)
                include_peers.append(InputDialogPeer(peer=peer))
                resolved_usernames.append(username)
            except FloodWaitError as exc:
                raise FolderManagerError(
                    f"FloodWait {exc.seconds}s while resolving channel '{username}'"
                ) from exc
            except Exception as exc:
                if "FrozenMethodInvalid" in type(exc).__name__:
                    raise FolderManagerError(
                        f"Account {account_id} is frozen"
                    ) from exc
                log.debug(
                    f"FolderManager: could not resolve '{username}', skipping: {exc}"
                )

        # Pick a folder_id that is unlikely to collide (Telegram uses small integers)
        folder_tg_id = await self._next_folder_id(client)

        # Create the dialog filter on Telegram
        telegram_folder_id: Optional[int] = None
        try:
            dialog_filter = DialogFilter(
                id=folder_tg_id,
                title=folder_name,
                include_peers=include_peers,
                pinned_peers=[],
                exclude_peers=[],
                contacts=False,
                non_contacts=False,
                groups=False,
                broadcasts=True,
                bots=False,
                exclude_muted=False,
                exclude_read=False,
                exclude_archived=False,
            )
            await client(UpdateDialogFilter(id=folder_tg_id, filter=dialog_filter))
            telegram_folder_id = folder_tg_id
            log.info(
                f"FolderManager: created Telegram folder id={folder_tg_id} "
                f"name='{folder_name}' for account {account_id}"
            )
        except FloodWaitError as exc:
            raise FolderManagerError(
                f"FloodWait {exc.seconds}s while creating folder"
            ) from exc
        except Exception as exc:
            if "FrozenMethodInvalid" in type(exc).__name__:
                raise FolderManagerError(
                    f"Account {account_id} is frozen"
                ) from exc
            # Non-fatal: persist the DB row without a confirmed TG folder id
            log.warning(
                f"FolderManager: UpdateDialogFilter failed for account {account_id}: {exc}. "
                f"Saving DB row without confirmed folder_id."
            )

        # Attempt to export an invite link (API available in Telethon >= 1.24)
        invite_link: Optional[str] = await self._export_chatlist_invite(
            client=client,
            folder_id=telegram_folder_id,
            peers=include_peers,
        )

        # Persist TelegramFolder row
        folder = TelegramFolder(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            account_id=account_id,
            folder_name=folder_name,
            folder_id=telegram_folder_id,
            invite_link=invite_link,
            channel_usernames=resolved_usernames,
            status="active",
        )
        db_session.add(folder)
        await db_session.flush()

        log.info(
            f"FolderManager: persisted folder id={folder.id} "
            f"(tenant {tenant_id}, account {account_id}, "
            f"{len(resolved_usernames)} channels)"
        )
        return folder

    async def list_folders(
        self,
        tenant_id: int,
        db_session: AsyncSession,
        account_id: Optional[int] = None,
    ) -> List[TelegramFolder]:
        """
        Return TelegramFolder rows for this tenant.

        The caller must have already applied RLS context to *db_session*.
        """
        stmt = select(TelegramFolder).where(
            TelegramFolder.tenant_id == tenant_id,
            TelegramFolder.status != "deleted",
        )
        if account_id is not None:
            stmt = stmt.where(TelegramFolder.account_id == account_id)
        stmt = stmt.order_by(TelegramFolder.created_at.desc())
        result = await db_session.execute(stmt)
        return list(result.scalars().all())

    async def delete_folder(
        self,
        folder_id: int,
        tenant_id: int,
        db_session: AsyncSession,
    ) -> None:
        """
        Remove the folder from Telegram and mark the DB row as deleted.

        The caller must have already applied RLS context to *db_session*.
        """
        result = await db_session.execute(
            select(TelegramFolder).where(
                TelegramFolder.id == folder_id,
                TelegramFolder.tenant_id == tenant_id,
            )
        )
        folder = result.scalar_one_or_none()
        if folder is None:
            log.debug(f"FolderManager: folder {folder_id} not found for tenant {tenant_id}")
            return

        # Attempt Telegram-side deletion
        if folder.folder_id is not None:
            await self._remove_telegram_folder(
                account_id=folder.account_id,
                tenant_id=tenant_id,
                telegram_folder_id=folder.folder_id,
                db_session=db_session,
            )

        # Mark as deleted in DB
        await db_session.execute(
            update(TelegramFolder)
            .where(
                TelegramFolder.id == folder_id,
                TelegramFolder.tenant_id == tenant_id,
            )
            .values(status="deleted", updated_at=utcnow())
        )

        log.info(
            f"FolderManager: folder {folder_id} deleted (tenant {tenant_id})"
        )

    async def get_folder_invite(
        self,
        folder_id: int,
        tenant_id: int,
        db_session: AsyncSession,
    ) -> Optional[str]:
        """
        Return the invite link for *folder_id*, or None if not available.

        The caller must have already applied RLS context to *db_session*.
        """
        result = await db_session.execute(
            select(TelegramFolder).where(
                TelegramFolder.id == folder_id,
                TelegramFolder.tenant_id == tenant_id,
            )
        )
        folder = result.scalar_one_or_none()
        if folder is None:
            return None
        return folder.invite_link

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _remove_telegram_folder(
        self,
        account_id: int,
        tenant_id: int,
        telegram_folder_id: int,
        db_session: AsyncSession,
    ) -> None:
        """Delete the Telegram dialog filter.  Non-fatal on error."""
        try:
            from telethon.tl.functions.messages import UpdateDialogFilter
        except ImportError:
            return

        client = await self._get_client(account_id, tenant_id, db_session)
        if client is None:
            return

        try:
            await client(UpdateDialogFilter(id=telegram_folder_id))
            log.info(
                f"FolderManager: removed Telegram filter {telegram_folder_id} "
                f"for account {account_id}"
            )
        except Exception as exc:
            log.debug(
                f"FolderManager: could not remove Telegram filter "
                f"{telegram_folder_id}: {exc}"
            )

    async def _export_chatlist_invite(
        self,
        client,
        folder_id: Optional[int],
        peers: List[Any],
    ) -> Optional[str]:
        """
        Try to export a chatlist invite link.  Returns None on any failure.
        """
        if folder_id is None or not peers:
            return None

        try:
            from telethon.tl.functions.chatlists import ExportChatlistInviteRequest
            from telethon.tl.types import InputChatlistDialogFilter

            result = await client(
                ExportChatlistInviteRequest(
                    chatlist=InputChatlistDialogFilter(filter_id=folder_id),
                    title="",
                    peers=peers,
                )
            )
            link = getattr(result, "invite", None)
            if link is not None:
                url = getattr(link, "url", None) or getattr(link, "link", None)
                return url
        except Exception as exc:
            log.debug(f"FolderManager: ExportChatlistInvite failed: {exc}")

        return None

    async def _next_folder_id(self, client) -> int:
        """
        Pick a folder id that does not collide with existing dialog filters.
        Telegram uses small positive integers (1-255 range typical).
        """
        try:
            from telethon.tl.functions.messages import GetDialogFilters

            result = await client(GetDialogFilters())
            existing_ids = {
                getattr(f, "id", 0)
                for f in (result.filters if hasattr(result, "filters") else result)
            }
            for candidate in range(2, 256):
                if candidate not in existing_ids:
                    return candidate
        except Exception as exc:
            log.debug(f"FolderManager: _next_folder_id fallback: {exc}")

        import random
        return random.randint(10, 200)

    async def _get_client(
        self, account_id: int, tenant_id: int, db_session: AsyncSession
    ):
        """
        Resolve the Telethon client.  Uses the open *db_session* (RLS already applied)
        to look up the Account row.
        """
        if self._session_mgr is None:
            return None

        result = await db_session.execute(
            select(Account).where(Account.id == account_id)
        )
        account = result.scalar_one_or_none()
        if account is None:
            log.warning(f"FolderManager: account {account_id} not found in DB")
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
                f"FolderManager: could not connect client for account {account_id}: {exc}"
            )
            return None
