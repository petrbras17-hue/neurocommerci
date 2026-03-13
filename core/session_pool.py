"""
SessionPool — centralized TelegramClient pool keyed by account_id.

Provides a persistent, auto-reconnecting pool of TelegramClient instances
with a hard ceiling on simultaneous connections and idle-timeout eviction.

Design goals
------------
- One TelegramClient per account_id, never more.
- Thread-safe: one asyncio.Lock per account_id prevents concurrent connect races.
- NEVER calls send_code_request — if a session is not authorized, raises
  SessionDeadError so callers can mark the account dead in the DB.
- Proxy comes from DB Account.proxy_id — enforces 1 IP = 1 account.
- Device fingerprint comes from per-account metadata JSON.
- Idle clients are disconnected after idle_timeout_sec (default 10 min).
- Configurable max_concurrent ceiling (default 20).

Relationship to legacy SessionManager
--------------------------------------
core/session_manager.py is the legacy phone-keyed LRU pool used by FarmThread,
WarmupEngine, and AccountManager.  SessionPool is the new account_id-keyed,
fully async pool intended for the SaaS control plane.  Both can coexist; the
legacy pool will be migrated incrementally.

Usage
-----
    pool = SessionPool(sessions_dir=settings.sessions_path)

    async with some_db_session as db:
        client = await pool.get_client(account_id, db_session=db)
    try:
        await client.send_message(...)
    finally:
        await pool.release_client(account_id)

    # Shutdown
    await pool.disconnect_all()
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Dict, Optional

from config import settings
from utils.logger import log


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class SessionDeadError(Exception):
    """
    The account session cannot be used.

    Raised when:
    - AuthKeyUnregisteredError / SessionRevokedError / AuthKeyDuplicatedError
      comes back from Telegram (session revoked remotely).
    - The .session file is missing on disk.
    - The metadata JSON has no api_id / api_hash.
    - The client connects but is_user_authorized() returns False.

    Callers MUST mark the account as dead in the DB and stop retrying.
    NEVER call send_code_request in response to this error.
    """


class PoolCapacityError(Exception):
    """
    Raised when max_concurrent active connections is already reached and the
    requested account_id is not already in the pool.
    """


# ---------------------------------------------------------------------------
# Internal pool entry
# ---------------------------------------------------------------------------


class _ClientStatus(Enum):
    IDLE = auto()
    IN_USE = auto()


@dataclass
class _PoolEntry:
    account_id: int
    phone: str
    client: object  # TelegramClient (or a mock in tests)
    status: _ClientStatus = _ClientStatus.IDLE
    last_used_at: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_used_at = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used_at

    def is_idle_expired(self, timeout_sec: float) -> bool:
        return self.status == _ClientStatus.IDLE and self.idle_seconds() > timeout_sec


# ---------------------------------------------------------------------------
# SessionPool
# ---------------------------------------------------------------------------


class SessionPool:
    """
    Centralized TelegramClient pool — one client per account, auto-reconnect.

    Parameters
    ----------
    sessions_dir : Path
        Root directory where .session and .json metadata files are stored.
        Supports flat layout (sessions_dir/{phone}.session) and tenant-scoped
        layout (sessions_dir/{user_id}/{phone}.session).
    max_concurrent : int
        Hard ceiling on simultaneously connected clients.
    idle_timeout_sec : float
        Seconds of inactivity before a client is eligible for eviction by
        evict_idle_clients().
    """

    DEFAULT_IDLE_TIMEOUT_SEC: float = 600.0  # 10 minutes

    def __init__(
        self,
        sessions_dir: Path,
        max_concurrent: int = 20,
        idle_timeout_sec: float = DEFAULT_IDLE_TIMEOUT_SEC,
    ) -> None:
        self._sessions_dir = Path(sessions_dir)
        self._max_concurrent = max_concurrent
        self._idle_timeout_sec = idle_timeout_sec

        # account_id -> _PoolEntry (connected clients)
        self._pool: Dict[int, _PoolEntry] = {}

        # One asyncio.Lock per account_id to prevent concurrent-connect races.
        self._account_locks: Dict[int, asyncio.Lock] = {}

        # Global lock protecting _pool and _account_locks dicts.
        self._pool_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_client(
        self, account_id: int, *, db_session=None, tenant_id: int | None = None
    ) -> object:
        """
        Return a connected, authorized TelegramClient for *account_id*.

        On a pool hit the existing client is re-verified (cheap is_connected
        check + is_user_authorized on reconnect) and returned immediately.

        On a pool miss the method loads account data from *db_session*, builds
        and connects a fresh TelegramClient, verifies authorization, caches it,
        and returns it.

        Parameters
        ----------
        account_id : int
            Primary key of the Account ORM row.
        db_session : AsyncSession, optional
            A live SQLAlchemy async session.  Required on a pool miss.
            Not needed (but harmless) on a pool hit.
        tenant_id : int, optional
            Defense-in-depth tenant filter applied to Account/Proxy queries.

        Raises
        ------
        SessionDeadError
            Session not authorized, .session file missing, or metadata invalid.
        PoolCapacityError
            max_concurrent limit reached and account is not already in pool.
        RuntimeError
            db_session was None on a pool miss.
        """
        account_lock = await self._get_account_lock(account_id)
        async with account_lock:
            entry = self._pool.get(account_id)
            if entry is not None:
                alive = await self._ensure_alive(entry)
                if alive:
                    entry.status = _ClientStatus.IN_USE
                    entry.touch()
                    return entry.client
                # Client unrecoverable — evict before trying a fresh connect.
                await self._evict_unlocked(account_id)

            # Pool miss path.
            if len(self._pool) >= self._max_concurrent:
                raise PoolCapacityError(
                    f"SessionPool: pool full ({len(self._pool)}/{self._max_concurrent}). "
                    f"Release a client before requesting account_id={account_id}."
                )

            if db_session is None:
                raise RuntimeError(
                    f"SessionPool.get_client: db_session is required for a pool miss "
                    f"(account_id={account_id})."
                )

            phone = await self._resolve_phone(account_id, db_session)
            client = await self._build_and_connect(
                account_id, phone, db_session=db_session, tenant_id=tenant_id
            )

            entry = _PoolEntry(
                account_id=account_id,
                phone=phone,
                client=client,
                status=_ClientStatus.IN_USE,
            )
            entry.touch()
            async with self._pool_lock:
                self._pool[account_id] = entry

            log.info(
                "SessionPool: account_id=%d (%s) connected and pooled (pool_size=%d)",
                account_id,
                phone,
                len(self._pool),
            )
            return client

    async def release_client(self, account_id: int) -> None:
        """Mark a client as idle (available for reuse).  No-op if not in pool."""
        async with self._pool_lock:
            entry = self._pool.get(account_id)
        if entry is not None:
            entry.status = _ClientStatus.IDLE
            entry.touch()

    async def disconnect_client(self, account_id: int) -> None:
        """Disconnect and remove the client for *account_id* from the pool."""
        account_lock = await self._get_account_lock(account_id)
        async with account_lock:
            await self._evict_unlocked(account_id)

    async def disconnect_all(self) -> None:
        """Disconnect all pooled clients.  Call during application shutdown."""
        async with self._pool_lock:
            account_ids = list(self._pool.keys())

        for aid in account_ids:
            try:
                await self.disconnect_client(aid)
            except Exception as exc:
                log.warning(
                    "SessionPool.disconnect_all: error evicting account_id=%d: %s",
                    aid,
                    exc,
                )
        log.info("SessionPool: all clients disconnected")

    async def health_check(self, account_id: int) -> dict:
        """
        Return a health dict for *account_id* without raising.

        Keys: account_id, in_pool, connected, authorized, status, idle_sec.
        """
        async with self._pool_lock:
            entry = self._pool.get(account_id)

        if entry is None:
            return {
                "account_id": account_id,
                "in_pool": False,
                "connected": False,
                "authorized": False,
            }

        client = entry.client
        connected = self._client_is_connected(client)
        authorized = False
        if connected:
            try:
                authorized = await client.is_user_authorized()
            except Exception:
                authorized = False

        return {
            "account_id": account_id,
            "in_pool": True,
            "connected": connected,
            "authorized": authorized,
            "status": entry.status.name,
            "idle_sec": round(entry.idle_seconds(), 1),
        }

    async def evict_idle_clients(self) -> int:
        """
        Disconnect clients that have been idle longer than idle_timeout_sec.

        Call periodically from a background task.
        Returns the number of clients evicted.
        """
        async with self._pool_lock:
            expired = [
                aid
                for aid, entry in self._pool.items()
                if entry.is_idle_expired(self._idle_timeout_sec)
            ]

        count = 0
        for aid in expired:
            try:
                await self.disconnect_client(aid)
                count += 1
                log.debug("SessionPool: idle-evicted account_id=%d", aid)
            except Exception as exc:
                log.warning(
                    "SessionPool.evict_idle_clients: error for account_id=%d: %s",
                    aid,
                    exc,
                )
        return count

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def active_count(self) -> int:
        """Number of currently pooled clients (IDLE + IN_USE)."""
        return len(self._pool)

    @property
    def pool_stats(self) -> dict:
        """Snapshot of pool health metrics."""
        idle = sum(1 for e in self._pool.values() if e.status == _ClientStatus.IDLE)
        in_use = sum(1 for e in self._pool.values() if e.status == _ClientStatus.IN_USE)
        total = len(self._pool)
        return {
            "total": total,
            "idle": idle,
            "in_use": in_use,
            "active": total,
            "max_concurrent": self._max_concurrent,
            "capacity_used_pct": round(100 * total / self._max_concurrent, 1)
            if self._max_concurrent
            else 0,
        }

    # ------------------------------------------------------------------
    # Synchronous cache access (thread-safe, no DB needed)
    # ------------------------------------------------------------------

    def try_get_cached(self, account_id: int) -> Any:
        """Get a cached client synchronously without acquiring the async lock.

        Returns the TelegramClient if found and idle, None otherwise.
        This is cooperative-safe within a single asyncio event loop iteration
        (no await between read and write). NOT safe across OS threads.
        """
        entry = self._pool.get(account_id)
        if entry is not None and entry.status != _ClientStatus.IN_USE:
            entry.status = _ClientStatus.IN_USE
            entry.touch()
            return entry.client
        return None

    def try_release_cached(self, account_id: int) -> None:
        """Release a cached client synchronously."""
        entry = self._pool.get(account_id)
        if entry is not None:
            entry.status = _ClientStatus.IDLE
            entry.touch()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    _MAX_ACCOUNT_LOCKS = 500  # prevent unbounded dict growth

    async def _get_account_lock(self, account_id: int) -> asyncio.Lock:
        async with self._pool_lock:
            if account_id not in self._account_locks:
                # Prune stale locks for accounts no longer in pool.
                if len(self._account_locks) >= self._MAX_ACCOUNT_LOCKS:
                    stale = [
                        aid for aid in self._account_locks
                        if aid not in self._pool and not self._account_locks[aid].locked()
                    ]
                    for aid in stale:
                        del self._account_locks[aid]
                self._account_locks[account_id] = asyncio.Lock()
            return self._account_locks[account_id]

    async def _evict_unlocked(self, account_id: int) -> None:
        """Disconnect and remove an entry.  Must NOT already hold _pool_lock."""
        async with self._pool_lock:
            entry = self._pool.pop(account_id, None)
            # Clean up the per-account lock to prevent unbounded dict growth.
            self._account_locks.pop(account_id, None)

        if entry is None:
            return

        client = entry.client
        try:
            if self._client_is_connected(client):
                await client.disconnect()
        except Exception as exc:
            log.debug(
                "SessionPool._evict_unlocked: disconnect error account_id=%d: %s",
                account_id,
                exc,
            )

    async def _ensure_alive(self, entry: _PoolEntry) -> bool:
        """
        Verify the pooled client is connected and authorized.

        Attempts one reconnect if disconnected.  Returns False (do NOT raise)
        on auth errors so the caller can evict and re-raise SessionDeadError.
        """
        client = entry.client
        connected = self._client_is_connected(client)

        if not connected:
            log.info(
                "SessionPool: reconnecting account_id=%d phone=%s",
                entry.account_id,
                entry.phone,
            )
            try:
                await client.connect()
                connected = True
            except Exception as exc:
                cls = type(exc).__name__
                if cls in (
                    "AuthKeyUnregisteredError",
                    "SessionRevokedError",
                    "AuthKeyDuplicatedError",
                ):
                    log.warning(
                        "SessionPool: auth error reconnecting account_id=%d: %s",
                        entry.account_id,
                        exc,
                    )
                else:
                    log.warning(
                        "SessionPool: connect error account_id=%d: %s",
                        entry.account_id,
                        exc,
                    )
                return False

        try:
            authorized = await client.is_user_authorized()
        except Exception:
            authorized = False

        if not authorized:
            log.warning(
                "SessionPool: account_id=%d connected but not authorized",
                entry.account_id,
            )
            return False

        return True

    async def _build_and_connect(
        self, account_id: int, phone: str, *, db_session, tenant_id: int | None = None
    ) -> object:
        """
        Load Account + Proxy rows from DB, build TelegramClient, connect, verify.

        Raises
        ------
        SessionDeadError  — session file missing / metadata invalid / not authorized
        RuntimeError      — unexpected build failure
        """
        from sqlalchemy import select
        from storage.models import Account, Proxy

        # Re-load Account to get proxy_id and session_file.
        # Defense-in-depth: add tenant_id filter alongside RLS.
        query = select(Account).where(Account.id == account_id)
        if tenant_id is not None:
            query = query.where(Account.tenant_id == tenant_id)
        result = await db_session.execute(query)
        account = result.scalar_one_or_none()
        if account is None:
            raise SessionDeadError(f"account_id={account_id} not found in DB")

        # Resolve .session file path.
        session_path = self._resolve_session_path(account)
        if not session_path.with_suffix(".session").exists():
            raise SessionDeadError(
                f"account_id={account_id} phone={phone}: "
                f"session file not found at {session_path}.session"
            )

        # Load metadata JSON for api_id, api_hash, device fingerprint.
        metadata = self._load_metadata(account, session_path)
        api_id = (
            int(metadata.get("app_id") or 0)
            or int(account.api_id or 0)
            or settings.TELEGRAM_API_ID
        )
        api_hash = (
            str(metadata.get("app_hash") or "").strip()
            or settings.TELEGRAM_API_HASH
        )

        if not api_id or not api_hash:
            raise SessionDeadError(
                f"account_id={account_id} phone={phone}: missing api_id/api_hash"
            )

        # Load proxy from DB if bound. Defense-in-depth: scope to tenant.
        proxy_tuple = None
        if account.proxy_id:
            proxy_query = select(Proxy).where(Proxy.id == account.proxy_id)
            if tenant_id is not None:
                proxy_query = proxy_query.where(Proxy.tenant_id == tenant_id)
            proxy_result = await db_session.execute(proxy_query)
            proxy_row = proxy_result.scalar_one_or_none()
            if proxy_row:
                proxy_tuple = self._proxy_to_telethon(proxy_row)

        # Build TelegramClient.
        client = self._create_telethon_client(
            session_path=str(session_path),
            api_id=api_id,
            api_hash=api_hash,
            metadata=metadata,
            proxy=proxy_tuple,
        )

        # Connect.
        try:
            await client.connect()
        except Exception as exc:
            cls = type(exc).__name__
            if cls in (
                "AuthKeyUnregisteredError",
                "SessionRevokedError",
                "AuthKeyDuplicatedError",
            ):
                raise SessionDeadError(
                    f"account_id={account_id} phone={phone}: {exc}"
                ) from exc
            raise

        # Verify authorization — NEVER call send_code_request.
        authorized = False
        try:
            authorized = await client.is_user_authorized()
        except Exception as exc:
            cls = type(exc).__name__
            if cls in ("AuthKeyUnregisteredError", "SessionRevokedError"):
                try:
                    await client.disconnect()
                except Exception:
                    pass
                raise SessionDeadError(
                    f"account_id={account_id} phone={phone}: auth check: {exc}"
                ) from exc
            raise

        if not authorized:
            try:
                await client.disconnect()
            except Exception:
                pass
            raise SessionDeadError(
                f"account_id={account_id} phone={phone}: not authorized — session dead"
            )

        proxy_label = (
            f"{proxy_tuple[1]}:{proxy_tuple[2]}" if proxy_tuple else "none"
        )
        log.info(
            "SessionPool: client ready account_id=%d phone=%s proxy=%s",
            account_id,
            phone,
            proxy_label,
        )
        return client

    def _resolve_session_path(self, account) -> Path:
        """
        Return the session base path (without .session extension) for an account.

        Supports:
          - Absolute path in account.session_file.
          - Tenant-scoped layout: sessions_dir/{user_id}/{stem}.session
          - Flat layout: sessions_dir/{stem}.session
        """
        session_filename = Path(account.session_file)
        if session_filename.is_absolute():
            return session_filename.with_suffix("")

        stem = session_filename.stem

        if account.user_id:
            tenant_path = self._sessions_dir / str(account.user_id) / stem
            if tenant_path.with_suffix(".session").exists():
                return tenant_path

        return self._sessions_dir / stem

    def _load_metadata(self, account, session_path: Path) -> dict:
        """
        Load per-account JSON metadata for api_id/api_hash + device fingerprint.

        Search order:
          1. session_path.parent / {phone}.json
          2. session_path.parent / {stem}.json

        Returns empty dict if not found — callers fall back to account.api_id
        and settings defaults.
        """
        phone = account.phone
        candidates = [
            session_path.parent / f"{phone}.json",
            session_path.parent / f"{session_path.stem}.json",
        ]
        for path in candidates:
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    return payload if isinstance(payload, dict) else {}
                except Exception as exc:
                    log.debug(
                        "SessionPool._load_metadata: parse error %s: %s", path, exc
                    )
        return {}

    @staticmethod
    def _proxy_to_telethon(proxy_row) -> tuple:
        """
        Convert a Proxy ORM row to a Telethon proxy tuple.

        Format: (type_int, host, port, rdns, username, password)
        Type ints: 1=SOCKS4, 2=SOCKS5, 3=HTTP/HTTPS
        """
        _type_map = {"socks5": 2, "socks4": 1, "http": 3, "https": 3}
        t = _type_map.get(str(proxy_row.proxy_type or "socks5").lower(), 2)
        return (
            t,
            proxy_row.host,
            int(proxy_row.port),
            True,  # rdns=True — resolves hostnames on the proxy side
            proxy_row.username or None,
            proxy_row.password or None,
        )

    @staticmethod
    def _create_telethon_client(
        session_path: str,
        api_id: int,
        api_hash: str,
        metadata: dict,
        proxy: Optional[tuple],
    ) -> object:
        """
        Construct a TelegramClient with device fingerprint from metadata JSON.

        This is the single construction point for pooled clients.
        standalone_helpers.build_client() is still used for one-off scripts.
        """
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise RuntimeError(
                "telethon is not installed — cannot build TelegramClient"
            ) from exc

        return TelegramClient(
            session_path,
            api_id=api_id,
            api_hash=api_hash,
            proxy=proxy,
            device_model=metadata.get("device", "Samsung Galaxy S23"),
            system_version=metadata.get("sdk", "SDK 29"),
            app_version=metadata.get("app_version", "12.4.3"),
            lang_code=metadata.get("lang_pack", "ru"),
            system_lang_code=metadata.get("system_lang_pack", "ru-ru"),
            timeout=30,
            connection_retries=5,
            retry_delay=5,
        )

    @staticmethod
    def _client_is_connected(client) -> bool:
        """Safe is_connected() call that never raises."""
        try:
            fn = getattr(client, "is_connected", None)
            return bool(fn() if callable(fn) else False)
        except Exception:
            return False

    async def _resolve_phone(self, account_id: int, db_session) -> str:
        """Load just the phone number for a given account_id."""
        from sqlalchemy import select
        from storage.models import Account

        result = await db_session.execute(
            select(Account.phone).where(Account.id == account_id)
        )
        row = result.first()
        return row[0] if row else str(account_id)
