"""
Sprint 17: Admin Proxy Management Service.

Handles: import, test (HTTP/SOCKS5/HTTPS CONNECT), bind/unbind, health checks.
Enforces golden rule: 1 proxy = 1 account, NEVER shared.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import AdminProxy, AdminAccount
from core.admin_onboarding import log_operation

logger = logging.getLogger(__name__)


# ── Import ─────────────────────────────────────────────────────────


async def import_proxies(
    db: AsyncSession,
    workspace_id: int,
    lines: list[str],
    proxy_type: str = "socks5",
    country: Optional[str] = None,
) -> list[AdminProxy]:
    """
    Parse host:port:user:pass lines and create DB records.
    Skips duplicates (same host:port already in workspace).
    """
    proxies = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(":")
        if len(parts) < 2:
            continue

        host = parts[0]
        try:
            port = int(parts[1])
        except ValueError:
            continue
        username = parts[2] if len(parts) > 2 else None
        password = parts[3] if len(parts) > 3 else None

        # Check duplicate
        existing = await db.execute(
            select(AdminProxy).where(
                and_(
                    AdminProxy.workspace_id == workspace_id,
                    AdminProxy.host == host,
                    AdminProxy.port == port,
                )
            )
        )
        if existing.scalar_one_or_none():
            continue

        proxy = AdminProxy(
            workspace_id=workspace_id,
            host=host,
            port=port,
            username=username,
            password=password,
            proxy_type=proxy_type,
            country=country,
            status="untested",
        )
        db.add(proxy)
        proxies.append(proxy)

    if proxies:
        await db.flush()
        await log_operation(
            db, workspace_id, "proxy", "import",
            "success", f"Imported {len(proxies)} proxies",
        )

    return proxies


# ── Test ───────────────────────────────────────────────────────────


def _test_proxy_http(host: str, port: int, user: str, password: str, timeout: int = 8) -> bool:
    """Quick HTTP connectivity test via curl."""
    auth = f"{user}:{password}@" if user and password else ""
    proxy_url = f"http://{auth}{host}:{port}"
    cmd = [
        "curl", "-x", proxy_url,
        "-s", "-o", "/dev/null", "-w", "%{http_code}",
        "--connect-timeout", str(timeout),
        "https://api.ipify.org",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        return result.stdout.strip() == "200"
    except Exception:
        return False


def _test_proxy_socks5(host: str, port: int, user: str, password: str, timeout: int = 8) -> bool:
    """SOCKS5 connectivity test via curl."""
    auth = f"{user}:{password}@" if user and password else ""
    proxy_url = f"socks5://{auth}{host}:{port}"
    cmd = [
        "curl", "-x", proxy_url,
        "-s", "-o", "/dev/null", "-w", "%{http_code}",
        "--connect-timeout", str(timeout),
        "https://api.ipify.org",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        return result.stdout.strip() == "200"
    except Exception:
        return False


def _test_proxy_https_connect(host: str, port: int, user: str, password: str, timeout: int = 10) -> bool:
    """HTTPS CONNECT tunnel test (needed for CAPTCHA bridge)."""
    try:
        import requests
        auth = f"{user}:{password}@" if user and password else ""
        proxy_url = f"http://{auth}{host}:{port}"
        r = requests.get(
            "https://telegram.org/",
            proxies={"https": proxy_url},
            timeout=timeout,
            allow_redirects=False,
        )
        return r.status_code in (200, 301, 302)
    except Exception:
        return False


def _get_proxy_ip(host: str, port: int, user: str, password: str, proxy_type: str = "socks5", timeout: int = 8) -> Optional[str]:
    """Get the external IP address through the proxy."""
    scheme = "socks5" if proxy_type == "socks5" else "http"
    auth = f"{user}:{password}@" if user and password else ""
    proxy_url = f"{scheme}://{auth}{host}:{port}"
    cmd = [
        "curl", "-x", proxy_url,
        "-s", "--connect-timeout", str(timeout),
        "https://api.ipify.org",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        ip = result.stdout.strip()
        # Basic IP validation
        parts = ip.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            return ip
        return None
    except Exception:
        return None


async def test_proxy(
    db: AsyncSession,
    proxy: AdminProxy,
) -> dict:
    """
    Test proxy: HTTP + SOCKS5 + HTTPS CONNECT.
    Updates proxy status and last_tested_at.
    Returns test results dict.
    """
    loop = asyncio.get_event_loop()
    user = proxy.username or ""
    pw = proxy.password or ""

    # Run tests in thread pool to avoid blocking
    http_ok = await loop.run_in_executor(
        None, _test_proxy_http, proxy.host, proxy.port, user, pw
    )
    socks5_ok = await loop.run_in_executor(
        None, _test_proxy_socks5, proxy.host, proxy.port, user, pw
    )
    https_ok = await loop.run_in_executor(
        None, _test_proxy_https_connect, proxy.host, proxy.port, user, pw
    )

    # Get IP
    external_ip = None
    if http_ok or socks5_ok:
        pt = proxy.proxy_type or "socks5"
        external_ip = await loop.run_in_executor(
            None, _get_proxy_ip, proxy.host, proxy.port, user, pw, pt
        )

    # Determine status
    alive = http_ok or socks5_ok
    now = datetime.now(timezone.utc)

    proxy.status = "alive" if alive else "dead"
    proxy.last_tested_at = now
    proxy.supports_https_connect = https_ok
    if external_ip:
        proxy.last_ip = external_ip
    await db.flush()

    result = {
        "proxy_id": proxy.id,
        "http": http_ok,
        "socks5": socks5_ok,
        "https_connect": https_ok,
        "external_ip": external_ip,
        "status": proxy.status,
        "is_dual": alive and https_ok,
    }

    await log_operation(
        db, proxy.workspace_id, "proxy", "test",
        "success" if alive else "error",
        f"HTTP={http_ok} SOCKS5={socks5_ok} HTTPS={https_ok} IP={external_ip}",
        proxy_id=proxy.id,
    )
    return result


async def test_all_proxies(
    db: AsyncSession,
    workspace_id: int,
) -> list[dict]:
    """Test all proxies in workspace. Returns list of test results."""
    q = select(AdminProxy).where(AdminProxy.workspace_id == workspace_id)
    result = await db.execute(q)
    proxies = list(result.scalars().all())

    results = []
    for proxy in proxies:
        r = await test_proxy(db, proxy)
        results.append(r)

    return results


# ── Bind / Unbind ──────────────────────────────────────────────────


async def bind_proxy_to_account(
    db: AsyncSession,
    proxy: AdminProxy,
    account: AdminAccount,
) -> bool:
    """
    Bind proxy to account (1:1 strict).
    Golden rule: 1 IP = 1 account.
    """
    # Check proxy not already bound
    if proxy.bound_account_id and proxy.bound_account_id != account.id:
        raise ValueError(
            f"Proxy {proxy.host}:{proxy.port} already bound to account ID {proxy.bound_account_id}. "
            f"1 IP = 1 account — NEVER share proxies."
        )

    # Check account doesn't already have a different proxy (workspace-scoped)
    existing = await db.execute(
        select(AdminProxy).where(
            and_(
                AdminProxy.bound_account_id == account.id,
                AdminProxy.id != proxy.id,
                AdminProxy.workspace_id == proxy.workspace_id,
            )
        )
    )
    existing_proxy = existing.scalar_one_or_none()
    if existing_proxy:
        raise ValueError(
            f"Account {account.phone} already has proxy {existing_proxy.host}:{existing_proxy.port}. "
            f"Unbind first before rebinding."
        )

    proxy.bound_account_id = account.id
    account.proxy_id = proxy.id
    await db.flush()

    await log_operation(
        db, proxy.workspace_id, "proxy", "bind",
        "success", f"Bound proxy {proxy.host}:{proxy.port} to account {account.phone}",
        account_id=account.id, proxy_id=proxy.id,
    )
    return True


async def unbind_proxy(db: AsyncSession, proxy: AdminProxy) -> bool:
    """Remove proxy-account binding."""
    if not proxy.bound_account_id:
        return False

    old_account_id = proxy.bound_account_id

    # Clear account's proxy_id
    account = await db.execute(
        select(AdminAccount).where(AdminAccount.id == old_account_id)
    )
    acct = account.scalar_one_or_none()
    if acct:
        acct.proxy_id = None

    proxy.bound_account_id = None
    await db.flush()

    await log_operation(
        db, proxy.workspace_id, "proxy", "unbind",
        "success", f"Unbound proxy {proxy.host}:{proxy.port}",
        account_id=old_account_id, proxy_id=proxy.id,
    )
    return True


# ── Queries ────────────────────────────────────────────────────────


async def get_proxy(db: AsyncSession, proxy_id: int, workspace_id: Optional[int] = None) -> Optional[AdminProxy]:
    """Get single proxy by ID, optionally filtered by workspace for tenant safety."""
    q = select(AdminProxy).where(AdminProxy.id == proxy_id)
    if workspace_id is not None:
        q = q.where(AdminProxy.workspace_id == workspace_id)
    result = await db.execute(q)
    return result.scalar_one_or_none()


async def list_proxies(
    db: AsyncSession,
    workspace_id: int,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AdminProxy]:
    """List proxies with optional status filter."""
    q = select(AdminProxy).where(AdminProxy.workspace_id == workspace_id)
    if status:
        q = q.where(AdminProxy.status == status)
    q = q.order_by(AdminProxy.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_free_proxies(
    db: AsyncSession,
    workspace_id: int,
    country: Optional[str] = None,
) -> list[AdminProxy]:
    """Get unbound alive proxies, optionally filtered by country."""
    q = select(AdminProxy).where(
        and_(
            AdminProxy.workspace_id == workspace_id,
            AdminProxy.status == "alive",
            AdminProxy.bound_account_id.is_(None),
        )
    )
    if country:
        q = q.where(AdminProxy.country == country)
    q = q.order_by(AdminProxy.created_at.desc())
    result = await db.execute(q)
    return list(result.scalars().all())


async def delete_proxy(db: AsyncSession, proxy: AdminProxy) -> bool:
    """Delete proxy record. Must not be bound."""
    if proxy.bound_account_id:
        raise ValueError("Cannot delete bound proxy — unbind first")

    await log_operation(
        db, proxy.workspace_id, "proxy", "delete",
        "success", f"Deleted proxy {proxy.host}:{proxy.port}",
        proxy_id=proxy.id,
    )
    await db.delete(proxy)
    await db.flush()
    return True


def build_proxy_tuple(proxy: AdminProxy) -> tuple:
    """Build Telethon-compatible proxy tuple from AdminProxy."""
    if proxy.proxy_type == "socks5":
        import socks
        return (socks.SOCKS5, proxy.host, proxy.port, True, proxy.username, proxy.password)
    else:
        # HTTP with CONNECT
        return (3, proxy.host, proxy.port, True, proxy.username, proxy.password)
