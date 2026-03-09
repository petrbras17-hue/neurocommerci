from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from storage.models import Account, AuthUser, Proxy, RefreshToken, TeamMember, Tenant, User, Workspace
from utils.helpers import utcnow


TELEGRAM_AUTH_MAX_AGE_SECONDS = 3600


class TelegramAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class WebAuthBundle:
    access_token: str | None
    refresh_token: str | None
    setup_token: str | None
    status: str
    user: dict[str, Any]
    tenant: dict[str, Any]
    workspace: dict[str, Any]
    onboarding: dict[str, Any]


def _slugify(raw: str, fallback: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(raw or ""))
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized[:80] or fallback


def _refresh_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _telegram_secret_key(bot_token: str) -> bytes:
    return hashlib.sha256(bot_token.encode("utf-8")).digest()


def _telegram_check_string(payload: dict[str, Any]) -> str:
    parts = []
    for key in sorted(payload.keys()):
        if key == "hash":
            continue
        value = payload.get(key)
        if value in (None, ""):
            continue
        parts.append(f"{key}={value}")
    return "\n".join(parts)


def verify_telegram_widget_payload(payload: dict[str, Any]) -> dict[str, Any]:
    token = str(settings.ADMIN_BOT_TOKEN or "").strip()
    if not token:
        raise TelegramAuthError("telegram_login_not_configured")

    provided_hash = str(payload.get("hash") or "").strip()
    if not provided_hash:
        raise TelegramAuthError("missing_telegram_hash")

    auth_date_raw = payload.get("auth_date")
    try:
        auth_date = int(auth_date_raw)
    except (TypeError, ValueError) as exc:
        raise TelegramAuthError("invalid_auth_date") from exc

    now_ts = int(utcnow().timestamp())
    if auth_date > now_ts + 30 or now_ts - auth_date > TELEGRAM_AUTH_MAX_AGE_SECONDS:
        raise TelegramAuthError("telegram_auth_expired")

    data_check_string = _telegram_check_string(payload)
    expected_hash = hmac.new(
        _telegram_secret_key(token),
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, provided_hash):
        raise TelegramAuthError("invalid_telegram_hash")

    try:
        telegram_user_id = int(payload["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TelegramAuthError("invalid_telegram_user_id") from exc

    return {
        "telegram_user_id": telegram_user_id,
        "telegram_username": str(payload.get("username") or "").strip() or None,
        "first_name": str(payload.get("first_name") or "").strip() or None,
        "last_name": str(payload.get("last_name") or "").strip() or None,
        "photo_url": str(payload.get("photo_url") or "").strip() or None,
        "auth_date": auth_date,
    }


def make_access_token(*, user_id: int, tenant_id: int, workspace_id: int, role: str) -> str:
    payload = {
        "sub": str(int(user_id)),
        "tenant_id": int(tenant_id),
        "workspace_id": int(workspace_id),
        "role": str(role),
        "type": "access",
        "exp": utcnow() + timedelta(minutes=max(1, int(settings.JWT_ACCESS_TTL_MINUTES))),
    }
    return jwt.encode(payload, settings.JWT_ACCESS_SECRET, algorithm=settings.JWT_ALGORITHM)


def make_setup_token(*, user_id: int, tenant_id: int, workspace_id: int, role: str) -> str:
    payload = {
        "sub": str(int(user_id)),
        "tenant_id": int(tenant_id),
        "workspace_id": int(workspace_id),
        "role": str(role),
        "type": "setup",
        "exp": utcnow() + timedelta(minutes=15),
    }
    return jwt.encode(payload, settings.JWT_ACCESS_SECRET, algorithm=settings.JWT_ALGORITHM)


def make_refresh_token(*, user_id: int, tenant_id: int, workspace_id: int, role: str) -> str:
    payload = {
        "sub": str(int(user_id)),
        "tenant_id": int(tenant_id),
        "workspace_id": int(workspace_id),
        "role": str(role),
        "type": "refresh",
        "jti": secrets.token_hex(16),
        "exp": utcnow() + timedelta(days=max(1, int(settings.JWT_REFRESH_TTL_DAYS))),
    }
    return jwt.encode(payload, settings.JWT_REFRESH_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_refresh_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_REFRESH_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.PyJWTError as exc:
        raise TelegramAuthError("invalid_refresh_token") from exc
    if str(payload.get("type") or "") != "refresh":
        raise TelegramAuthError("invalid_refresh_token_type")
    return payload


def decode_setup_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_ACCESS_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.PyJWTError as exc:
        raise TelegramAuthError("invalid_setup_token") from exc
    if str(payload.get("type") or "") != "setup":
        raise TelegramAuthError("invalid_setup_token_type")
    return payload


def _ip_string(raw_ip: str | None) -> str | None:
    value = str(raw_ip or "").strip()
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return value[:128]


async def _ensure_runtime_user(session: AsyncSession, telegram_identity: dict[str, Any]) -> User:
    telegram_user_id = int(telegram_identity["telegram_user_id"])
    existing = await session.execute(select(User).where(User.telegram_id == telegram_user_id))
    runtime_user = existing.scalar_one_or_none()
    if runtime_user is not None:
        runtime_user.username = telegram_identity.get("telegram_username") or runtime_user.username
        runtime_user.first_name = telegram_identity.get("first_name") or runtime_user.first_name
        runtime_user.is_active = True
        return runtime_user

    runtime_user = User(
        telegram_id=telegram_user_id,
        username=telegram_identity.get("telegram_username"),
        first_name=telegram_identity.get("first_name"),
        is_active=True,
        is_admin=False,
    )
    session.add(runtime_user)
    await session.flush()
    return runtime_user


async def _bootstrap_membership(
    session: AsyncSession,
    *,
    auth_user: AuthUser,
    telegram_identity: dict[str, Any],
) -> tuple[Tenant, Workspace, TeamMember]:
    membership_result = await session.execute(
        select(TeamMember, Workspace, Tenant)
        .join(Workspace, TeamMember.workspace_id == Workspace.id)
        .join(Tenant, TeamMember.tenant_id == Tenant.id)
        .where(TeamMember.user_id == auth_user.id)
        .order_by(TeamMember.id.asc())
    )
    existing = membership_result.first()
    if existing is not None:
        membership, workspace, tenant = existing
        if workspace.runtime_user_id is None:
            runtime_user = await _ensure_runtime_user(session, telegram_identity)
            workspace.runtime_user_id = runtime_user.id
        return tenant, workspace, membership

    runtime_user = await _ensure_runtime_user(session, telegram_identity)
    fallback_name = telegram_identity.get("first_name") or telegram_identity.get("telegram_username") or f"Tenant {auth_user.id}"
    tenant = Tenant(
        name=fallback_name,
        slug=_slugify(fallback_name, fallback=f"tg-{telegram_identity['telegram_user_id']}"),
        status="active",
    )
    session.add(tenant)
    await session.flush()

    workspace = Workspace(
        tenant_id=tenant.id,
        name="Основное пространство",
        runtime_user_id=runtime_user.id,
        settings={"onboarding": {"status": "profile_incomplete"}},
    )
    session.add(workspace)
    await session.flush()

    membership = TeamMember(
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        user_id=auth_user.id,
        role="owner",
    )
    session.add(membership)
    await session.flush()
    return tenant, workspace, membership


async def _build_onboarding_state(session: AsyncSession, *, tenant_id: int, workspace_id: int) -> dict[str, Any]:
    accounts_result = await session.execute(
        select(Account).where(Account.tenant_id == tenant_id, Account.workspace_id == workspace_id).order_by(Account.id.asc())
    )
    accounts = list(accounts_result.scalars().all())
    proxies_result = await session.execute(
        select(Proxy).where(Proxy.tenant_id == tenant_id, Proxy.workspace_id == workspace_id).order_by(Proxy.id.asc())
    )
    proxies = list(proxies_result.scalars().all())
    ready_accounts = sum(
        1
        for account in accounts
        if str(account.status or "") == "active" and str(account.health_status or "") == "alive"
    )
    return {
        "profile_complete": True,
        "accounts_total": len(accounts),
        "accounts_ready": ready_accounts,
        "proxies_total": len(proxies),
        "next_step": (
            "upload_account"
            if not accounts
            else ("bind_proxy" if not proxies else ("run_audit" if ready_accounts == 0 else "done"))
        ),
    }


def _user_payload(user: AuthUser) -> dict[str, Any]:
    return {
        "id": user.id,
        "telegram_user_id": user.telegram_user_id,
        "telegram_username": user.telegram_username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "company": user.company,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def _tenant_payload(tenant: Tenant, *, role: str) -> dict[str, Any]:
    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "status": tenant.status,
        "role": role,
        "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
    }


def _workspace_payload(workspace: Workspace) -> dict[str, Any]:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "settings": workspace.settings or {},
        "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
    }


async def _issue_refresh_record(
    session: AsyncSession,
    *,
    auth_user: AuthUser,
    tenant: Tenant,
    workspace: Workspace,
    role: str,
    user_agent: str | None,
    ip_address: str | None,
) -> str:
    refresh_token = make_refresh_token(
        user_id=auth_user.id,
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        role=role,
    )
    refresh_row = RefreshToken(
        user_id=auth_user.id,
        tenant_id=tenant.id,
        token_hash=_refresh_token_hash(refresh_token),
        expires_at=utcnow() + timedelta(days=max(1, int(settings.JWT_REFRESH_TTL_DAYS))),
        user_agent=(str(user_agent or "").strip() or None),
        ip_address=_ip_string(ip_address),
    )
    session.add(refresh_row)
    return refresh_token


async def verify_telegram_login(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> WebAuthBundle:
    telegram_identity = verify_telegram_widget_payload(payload)
    auth_result = await session.execute(
        select(AuthUser).where(AuthUser.telegram_user_id == int(telegram_identity["telegram_user_id"]))
    )
    auth_user = auth_result.scalar_one_or_none()
    if auth_user is None:
        auth_user = AuthUser(
            telegram_user_id=int(telegram_identity["telegram_user_id"]),
            telegram_username=telegram_identity.get("telegram_username"),
            first_name=telegram_identity.get("first_name"),
            last_name=telegram_identity.get("last_name"),
            last_login_at=utcnow(),
        )
        session.add(auth_user)
        await session.flush()
    else:
        auth_user.telegram_username = telegram_identity.get("telegram_username") or auth_user.telegram_username
        auth_user.first_name = telegram_identity.get("first_name") or auth_user.first_name
        auth_user.last_name = telegram_identity.get("last_name") or auth_user.last_name
        auth_user.last_login_at = utcnow()

    tenant, workspace, membership = await _bootstrap_membership(
        session,
        auth_user=auth_user,
        telegram_identity=telegram_identity,
    )

    profile_complete = bool(str(auth_user.email or "").strip()) and bool(str(auth_user.company or "").strip())
    if not profile_complete:
        setup_token = make_setup_token(
            user_id=auth_user.id,
            tenant_id=tenant.id,
            workspace_id=workspace.id,
            role=membership.role,
        )
        onboarding = {
            "profile_complete": False,
            "accounts_total": 0,
            "accounts_ready": 0,
            "proxies_total": 0,
            "next_step": "complete_profile",
        }
        return WebAuthBundle(
            access_token=None,
            refresh_token=None,
            setup_token=setup_token,
            status="profile_incomplete",
            user=_user_payload(auth_user),
            tenant=_tenant_payload(tenant, role=membership.role),
            workspace=_workspace_payload(workspace),
            onboarding=onboarding,
        )

    access_token = make_access_token(
        user_id=auth_user.id,
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        role=membership.role,
    )
    refresh_token = await _issue_refresh_record(
        session,
        auth_user=auth_user,
        tenant=tenant,
        workspace=workspace,
        role=membership.role,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    onboarding = await _build_onboarding_state(session, tenant_id=tenant.id, workspace_id=workspace.id)
    return WebAuthBundle(
        access_token=access_token,
        refresh_token=refresh_token,
        setup_token=None,
        status="authorized",
        user=_user_payload(auth_user),
        tenant=_tenant_payload(tenant, role=membership.role),
        workspace=_workspace_payload(workspace),
        onboarding=onboarding,
    )


async def complete_profile(
    session: AsyncSession,
    setup_token: str,
    *,
    email: str,
    company: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> WebAuthBundle:
    payload = decode_setup_token(setup_token)
    auth_user = await session.get(AuthUser, int(payload["sub"]))
    if auth_user is None:
        raise TelegramAuthError("auth_user_not_found")

    membership_result = await session.execute(
        select(TeamMember, Workspace, Tenant)
        .join(Workspace, TeamMember.workspace_id == Workspace.id)
        .join(Tenant, TeamMember.tenant_id == Tenant.id)
        .where(
            TeamMember.user_id == auth_user.id,
            TeamMember.tenant_id == int(payload["tenant_id"]),
            TeamMember.workspace_id == int(payload["workspace_id"]),
        )
    )
    row = membership_result.first()
    if row is None:
        raise TelegramAuthError("membership_not_found")
    membership, workspace, tenant = row

    existing_email = await session.execute(
        select(AuthUser).where(AuthUser.email == email, AuthUser.id != auth_user.id)
    )
    if existing_email.scalar_one_or_none() is not None:
        raise TelegramAuthError("email_already_in_use")

    auth_user.email = str(email or "").strip().lower()
    auth_user.company = str(company or "").strip()
    auth_user.last_login_at = utcnow()
    if not tenant.name or tenant.name.startswith("Tenant ") or tenant.name == (auth_user.first_name or ""):
        tenant.name = auth_user.company
    if workspace.name == "Основное пространство":
        workspace.name = f"{auth_user.company} — workspace"

    access_token = make_access_token(
        user_id=auth_user.id,
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        role=membership.role,
    )
    refresh_token = await _issue_refresh_record(
        session,
        auth_user=auth_user,
        tenant=tenant,
        workspace=workspace,
        role=membership.role,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    onboarding = await _build_onboarding_state(session, tenant_id=tenant.id, workspace_id=workspace.id)
    return WebAuthBundle(
        access_token=access_token,
        refresh_token=refresh_token,
        setup_token=None,
        status="authorized",
        user=_user_payload(auth_user),
        tenant=_tenant_payload(tenant, role=membership.role),
        workspace=_workspace_payload(workspace),
        onboarding=onboarding,
    )


async def refresh_web_session(
    session: AsyncSession,
    refresh_token: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> WebAuthBundle:
    payload = decode_refresh_token(refresh_token)
    token_hash = _refresh_token_hash(refresh_token)
    token_row_result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    token_row = token_row_result.scalar_one_or_none()
    if token_row is None or token_row.revoked_at is not None or token_row.expires_at <= utcnow():
        raise TelegramAuthError("refresh_token_revoked")

    auth_user = await session.get(AuthUser, int(payload["sub"]))
    tenant = await session.get(Tenant, int(payload["tenant_id"]))
    workspace = await session.get(Workspace, int(payload["workspace_id"]))
    if auth_user is None or tenant is None or workspace is None:
        raise TelegramAuthError("refresh_context_not_found")
    membership_result = await session.execute(
        select(TeamMember).where(
            TeamMember.user_id == auth_user.id,
            TeamMember.tenant_id == tenant.id,
            TeamMember.workspace_id == workspace.id,
        )
    )
    membership = membership_result.scalar_one_or_none()
    if membership is None:
        raise TelegramAuthError("refresh_membership_not_found")

    token_row.revoked_at = utcnow()
    token_row.last_used_at = utcnow()

    access_token = make_access_token(
        user_id=auth_user.id,
        tenant_id=tenant.id,
        workspace_id=workspace.id,
        role=membership.role,
    )
    new_refresh_token = await _issue_refresh_record(
        session,
        auth_user=auth_user,
        tenant=tenant,
        workspace=workspace,
        role=membership.role,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    onboarding = await _build_onboarding_state(session, tenant_id=tenant.id, workspace_id=workspace.id)
    return WebAuthBundle(
        access_token=access_token,
        refresh_token=new_refresh_token,
        setup_token=None,
        status="authorized",
        user=_user_payload(auth_user),
        tenant=_tenant_payload(tenant, role=membership.role),
        workspace=_workspace_payload(workspace),
        onboarding=onboarding,
    )


async def logout_web_session(session: AsyncSession, refresh_token: str | None) -> None:
    if not refresh_token:
        return
    token_hash = _refresh_token_hash(refresh_token)
    result = await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    token_row = result.scalar_one_or_none()
    if token_row is not None and token_row.revoked_at is None:
        token_row.revoked_at = utcnow()


async def get_me_payload(
    session: AsyncSession,
    *,
    auth_user_id: int,
    tenant_id: int,
    workspace_id: int,
) -> dict[str, Any]:
    auth_user = await session.get(AuthUser, int(auth_user_id))
    tenant = await session.get(Tenant, int(tenant_id))
    workspace = await session.get(Workspace, int(workspace_id))
    if auth_user is None or tenant is None or workspace is None:
        raise TelegramAuthError("me_context_not_found")

    membership_result = await session.execute(
        select(TeamMember).where(
            TeamMember.user_id == auth_user.id,
            TeamMember.tenant_id == tenant.id,
            TeamMember.workspace_id == workspace.id,
        )
    )
    membership = membership_result.scalar_one_or_none()
    if membership is None:
        raise TelegramAuthError("membership_not_found")

    onboarding = await _build_onboarding_state(session, tenant_id=tenant.id, workspace_id=workspace.id)
    return {
        "user": _user_payload(auth_user),
        "tenant": _tenant_payload(tenant, role=membership.role),
        "workspace": _workspace_payload(workspace),
        "onboarding": onboarding,
    }


async def get_team_payload(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int,
) -> dict[str, Any]:
    result = await session.execute(
        select(TeamMember, AuthUser)
        .join(AuthUser, TeamMember.user_id == AuthUser.id)
        .where(
            TeamMember.tenant_id == tenant_id,
            TeamMember.workspace_id == workspace_id,
        )
        .order_by(TeamMember.id.asc())
    )
    items = []
    for membership, user in result.all():
        items.append(
            {
                "user_id": user.id,
                "role": membership.role,
                "telegram_username": user.telegram_username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "company": user.company,
            }
        )
    return {"items": items, "total": len(items)}
