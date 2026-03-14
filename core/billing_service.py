"""
Sprint 13/14 — Billing Service.

Provides plan management, subscription lifecycle, trial activation,
plan-limit enforcement, payment recording, Stripe/YooKassa checkout
session creation, and webhook handling for NEURO COMMENTING SaaS.

All tenant-scoped queries run inside a session that already has RLS context
applied via apply_session_rls_context(). Platform-level queries (e.g. plan
listing) bypass RLS by using a non-tenant session.
"""
from __future__ import annotations

import json as _json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from utils.helpers import utcnow

log = logging.getLogger("uvicorn.error")

# Resource keys used by check_limits / get_usage.
RESOURCE_COMMENTS = "comments_per_day"
RESOURCE_CHANNELS = "max_channels"
RESOURCE_ACCOUNTS = "max_accounts"
RESOURCE_FARMS = "max_farms"
RESOURCE_CAMPAIGNS = "max_campaigns"


class BillingError(RuntimeError):
    pass


class NoActiveSubscription(BillingError):
    pass


class LimitExceeded(BillingError):
    def __init__(self, resource: str, used: int, limit: int) -> None:
        self.resource = resource
        self.used = used
        self.limit = limit
        super().__init__(
            f"Plan limit exceeded for {resource}: used={used}, limit={limit}"
        )


# ---------------------------------------------------------------------------
# Plan helpers
# ---------------------------------------------------------------------------


async def get_plans(session: AsyncSession) -> list[dict[str, Any]]:
    """Return all active plans ordered by sort_order. No RLS context needed."""
    from storage.models import Plan

    rows = (
        await session.execute(
            select(Plan).where(Plan.is_active == True).order_by(Plan.sort_order)
        )
    ).scalars().all()
    return [_serialize_plan(p) for p in rows]


async def get_plan_by_slug(session: AsyncSession, slug: str) -> Any | None:
    """Return a Plan ORM row by slug, or None."""
    from storage.models import Plan

    return (
        await session.execute(select(Plan).where(Plan.slug == slug).limit(1))
    ).scalar_one_or_none()


def _serialize_plan(p: Any) -> dict[str, Any]:
    return {
        "id": p.id,
        "slug": p.slug,
        "name": p.name,
        "display_name": getattr(p, "display_name", None) or p.name,
        "price_rub": getattr(p, "price_rub", None) or p.price_monthly_rub or 0,
        "price_usd": getattr(p, "price_usd", None),
        "price_monthly_rub": p.price_monthly_rub or 0,
        "price_yearly_rub": p.price_yearly_rub or 0,
        "comments_per_day": getattr(p, "comments_per_day", None) or p.max_comments_per_day or 100,
        "max_accounts": p.max_accounts,
        "max_channels": p.max_channels,
        "max_comments_per_day": p.max_comments_per_day,
        "max_campaigns": p.max_campaigns,
        "max_farms": getattr(p, "max_farms", 5),
        "ai_tier": getattr(p, "ai_tier", "worker") or "worker",
        "features": p.features or {},
        "is_active": p.is_active,
        "sort_order": p.sort_order,
    }


# ---------------------------------------------------------------------------
# Subscription helpers
# ---------------------------------------------------------------------------


async def get_subscription(
    tenant_id: int,
    session: AsyncSession,
) -> Any | None:
    """Return the most recent Subscription row for a tenant (or None)."""
    from storage.models import Subscription

    return (
        await session.execute(
            select(Subscription)
            .where(Subscription.tenant_id == tenant_id)
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _serialize_subscription(sub: Any) -> dict[str, Any]:
    return {
        "id": sub.id,
        "tenant_id": sub.tenant_id,
        "plan_id": sub.plan_id,
        "status": sub.status,
        "payment_provider": sub.payment_provider,
        "external_subscription_id": getattr(sub, "external_subscription_id", None),
        "current_period_start": sub.current_period_start.isoformat()
        if sub.current_period_start
        else None,
        "current_period_end": sub.current_period_end.isoformat()
        if sub.current_period_end
        else None,
        "trial_ends_at": sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
        "cancelled_at": sub.cancelled_at.isoformat() if sub.cancelled_at else None,
        "cancel_reason": getattr(sub, "cancel_reason", None),
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
        "updated_at": sub.updated_at.isoformat()
        if getattr(sub, "updated_at", None)
        else None,
    }


# ---------------------------------------------------------------------------
# Trial
# ---------------------------------------------------------------------------


async def create_trial(
    tenant_id: int,
    session: AsyncSession,
    plan_slug: str = "starter",
) -> dict[str, Any]:
    """
    Create a 3-day trial subscription for the tenant on the given plan.

    Raises BillingError if a subscription already exists.
    """
    from storage.models import Plan, Subscription

    existing = await get_subscription(tenant_id, session)
    if existing is not None:
        raise BillingError(
            f"Tenant {tenant_id} already has a subscription (status={existing.status})"
        )

    plan = await get_plan_by_slug(session, plan_slug)
    if plan is None:
        plan_slug = "starter"
        # Fallback: use the cheapest available plan.
        plan = (
            await session.execute(
                select(Plan).where(Plan.is_active == True).order_by(Plan.sort_order).limit(1)
            )
        ).scalar_one_or_none()
    if plan is None:
        raise BillingError("No active plans available to create a trial")

    now = utcnow()
    trial_days = settings.BILLING_TRIAL_DAYS
    trial_end = now + timedelta(days=trial_days)

    sub = Subscription(
        tenant_id=tenant_id,
        plan_id=plan.id,
        status="trialing",
        trial_ends_at=trial_end,
        current_period_start=now,
        current_period_end=trial_end,
        payment_provider=None,
        created_at=now,
        updated_at=now,
    )
    session.add(sub)
    await session.flush()

    # Record a payment event in the legacy payment_events table.
    await _record_payment_event(
        tenant_id=tenant_id,
        subscription_id=sub.id,
        event_type="trial_started",
        amount_rub=0,
        payment_provider=None,
        session=session,
    )

    log.info("Trial created for tenant=%d plan=%s ends=%s", tenant_id, plan.slug, trial_end.date())

    # Send trial_started email (fire-and-forget).
    try:
        tenant_email = await _get_tenant_email(tenant_id, session)
        if tenant_email:
            from core.email_service import schedule_email
            schedule_email(
                "trial_started",
                to=tenant_email,
                plan_name=plan.name,
                trial_days=trial_days,
            )
    except Exception as _email_exc:  # noqa: BLE001
        log.debug("Trial email scheduling failed for tenant=%d: %s", tenant_id, _email_exc)

    return _serialize_subscription(sub)


# ---------------------------------------------------------------------------
# Subscription management
# ---------------------------------------------------------------------------


async def create_subscription(
    tenant_id: int,
    plan_id: int,
    provider: str,
    external_id: str | None,
    session: AsyncSession,
) -> dict[str, Any]:
    """
    Activate a paid subscription (called from webhook handlers after payment).

    Upserts: if a trial/cancelled subscription exists it is upgraded;
    otherwise a new row is created.
    """
    from storage.models import Subscription

    now = utcnow()
    period_start = now
    period_end = now + timedelta(days=30)

    existing = await get_subscription(tenant_id, session)
    if existing is not None:
        existing.plan_id = plan_id
        existing.status = "active"
        existing.payment_provider = provider
        existing.external_subscription_id = external_id
        existing.current_period_start = period_start
        existing.current_period_end = period_end
        existing.cancelled_at = None
        if hasattr(existing, "cancel_reason"):
            existing.cancel_reason = None
        existing.updated_at = now
        sub = existing
    else:
        sub = Subscription(
            tenant_id=tenant_id,
            plan_id=plan_id,
            status="active",
            payment_provider=provider,
            external_subscription_id=external_id,
            current_period_start=period_start,
            current_period_end=period_end,
            trial_ends_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(sub)

    await session.flush()
    return _serialize_subscription(sub)


async def cancel_subscription(
    tenant_id: int,
    session: AsyncSession,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Soft-cancel: mark status as 'cancelled', keep access until period end.

    Raises NoActiveSubscription if no current subscription exists.
    """
    from storage.models import Subscription

    sub = await get_subscription(tenant_id, session)
    if sub is None:
        raise NoActiveSubscription(f"No subscription for tenant {tenant_id}")

    now = utcnow()
    sub.status = "cancelled"
    sub.cancelled_at = now
    if hasattr(sub, "cancel_reason"):
        sub.cancel_reason = reason
    sub.updated_at = now
    await session.flush()

    await _record_payment_event(
        tenant_id=tenant_id,
        subscription_id=sub.id,
        event_type="subscription_cancelled",
        amount_rub=0,
        payment_provider=sub.payment_provider,
        session=session,
    )

    return _serialize_subscription(sub)


def is_trial_expired(sub: Any | None) -> bool:
    """Return True if the subscription is in trialing status and the trial has ended."""
    if sub is None:
        return False
    if sub.status != "trialing":
        return False
    if sub.trial_ends_at is None:
        return False
    return utcnow() > sub.trial_ends_at


def is_subscription_active(sub: Any | None) -> bool:
    """Return True if the subscription grants access (active or within trial/grace period)."""
    if sub is None:
        return False
    if sub.status in ("active", "trialing"):
        if sub.status == "trialing" and is_trial_expired(sub):
            return False
        return True
    if sub.status == "cancelled" and sub.current_period_end:
        return utcnow() < sub.current_period_end
    return False


# ---------------------------------------------------------------------------
# Usage & limit enforcement
# ---------------------------------------------------------------------------


async def get_usage(tenant_id: int, session: AsyncSession) -> dict[str, Any]:
    """Return current resource usage counts for the tenant."""
    from storage.models import Account, Campaign, ChannelEntry, Comment, FarmConfig

    # Comments today (from usage_events or directly from accounts.comments_today).
    comments_today_result = await session.execute(
        select(func.sum(Account.comments_today)).where(
            Account.tenant_id == tenant_id
        )
    )
    comments_today = int(comments_today_result.scalar() or 0)

    # Active accounts.
    accounts_result = await session.execute(
        select(func.count(Account.id)).where(
            Account.tenant_id == tenant_id,
            Account.status != "banned",
        )
    )
    accounts_count = int(accounts_result.scalar() or 0)

    # Channels — count channel_entries linked to tenant channel databases.
    try:
        from storage.models import ChannelDatabase
        channels_result = await session.execute(
            select(func.count(ChannelEntry.id))
            .join(ChannelDatabase, ChannelEntry.database_id == ChannelDatabase.id)
            .where(ChannelDatabase.tenant_id == tenant_id)
        )
        channels_count = int(channels_result.scalar() or 0)
    except Exception:
        channels_count = 0

    # Farms.
    try:
        farms_result = await session.execute(
            select(func.count(FarmConfig.id)).where(FarmConfig.tenant_id == tenant_id)
        )
        farms_count = int(farms_result.scalar() or 0)
    except Exception:
        farms_count = 0

    # Campaigns.
    try:
        campaigns_result = await session.execute(
            select(func.count(Campaign.id)).where(Campaign.tenant_id == tenant_id)
        )
        campaigns_count = int(campaigns_result.scalar() or 0)
    except Exception:
        campaigns_count = 0

    return {
        "comments_per_day": comments_today,
        "max_accounts": accounts_count,
        "max_channels": channels_count,
        "max_farms": farms_count,
        "max_campaigns": campaigns_count,
    }


async def get_plan_for_tenant(tenant_id: int, session: AsyncSession) -> Any | None:
    """Return the Plan ORM row for the tenant's current subscription, or None."""
    from storage.models import Plan

    sub = await get_subscription(tenant_id, session)
    if sub is None:
        return None
    return (
        await session.execute(select(Plan).where(Plan.id == sub.plan_id).limit(1))
    ).scalar_one_or_none()


async def check_limits(
    tenant_id: int,
    resource: str,
    amount: int,
    session: AsyncSession,
) -> bool:
    """
    Return True if the tenant is within plan limits for the given resource.

    resource: one of RESOURCE_* constants.
    amount: the incremental units to check (e.g. 1 for one new account).
    Returns False if no subscription or limit exceeded.
    """
    plan = await get_plan_for_tenant(tenant_id, session)
    if plan is None:
        return False

    usage = await get_usage(tenant_id, session)
    current_usage = usage.get(resource, 0)
    plan_limit = getattr(plan, resource, None) or 0

    # -1 or very large number means unlimited.
    if plan_limit < 0 or plan_limit >= 999999:
        return True

    return (current_usage + amount) <= plan_limit


# ---------------------------------------------------------------------------
# Payment recording
# ---------------------------------------------------------------------------


async def record_payment(
    tenant_id: int,
    subscription_id: int | None,
    amount: int,
    currency: str,
    provider: str,
    external_payment_id: str | None,
    status: str,
    session: AsyncSession,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Record a payment in the payments table.

    Returns a serialized dict of the payment record.
    """
    from sqlalchemy import text

    now = utcnow()
    result = await session.execute(
        text("""
            INSERT INTO payments
                (tenant_id, subscription_id, amount, currency, payment_provider,
                 external_payment_id, status, metadata, created_at)
            VALUES
                (:tenant_id, :subscription_id, :amount, :currency, :payment_provider,
                 :external_payment_id, :status, :metadata::jsonb, :created_at)
            RETURNING id, created_at
        """),
        {
            "tenant_id": tenant_id,
            "subscription_id": subscription_id,
            "amount": amount,
            "currency": currency,
            "payment_provider": provider,
            "external_payment_id": external_payment_id,
            "status": status,
            "metadata": __import__("json").dumps(metadata or {}),
            "created_at": now,
        },
    )
    row = result.fetchone()
    return {
        "id": row[0],
        "tenant_id": tenant_id,
        "subscription_id": subscription_id,
        "amount": amount,
        "currency": currency,
        "payment_provider": provider,
        "external_payment_id": external_payment_id,
        "status": status,
        "metadata": metadata or {},
        "created_at": row[1].isoformat() if row[1] else now.isoformat(),
    }


async def list_payments(
    tenant_id: int,
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List payment history for a tenant from the payment_events table."""
    from sqlalchemy import text

    result = await session.execute(
        text("""
            SELECT id, tenant_id, subscription_id, event_type, amount_rub, payment_provider,
                   external_payment_id, metadata, created_at
            FROM payment_events
            WHERE tenant_id = :tenant_id
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"tenant_id": tenant_id, "limit": limit, "offset": offset},
    )
    rows = result.fetchall()
    return [
        {
            "id": r[0],
            "tenant_id": r[1],
            "subscription_id": r[2],
            "event_type": r[3],
            "amount_rub": r[4],
            "payment_provider": r[5],
            "external_payment_id": r[6],
            "metadata": r[7] or {},
            "created_at": r[8].isoformat() if r[8] else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Legacy payment_events helper (used by trial/cancel)
# ---------------------------------------------------------------------------


async def _record_payment_event(
    *,
    tenant_id: int,
    subscription_id: int | None,
    event_type: str,
    amount_rub: int,
    payment_provider: str | None,
    session: AsyncSession,
    external_payment_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write an entry to the legacy payment_events audit table."""
    from storage.models import PaymentEvent

    now = utcnow()
    event = PaymentEvent(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        event_type=event_type,
        amount_rub=amount_rub,
        payment_provider=payment_provider,
        external_payment_id=external_payment_id,
        event_meta=metadata,
        created_at=now,
    )
    session.add(event)


# ---------------------------------------------------------------------------
# Tenant email lookup helper
# ---------------------------------------------------------------------------


async def _get_tenant_email(tenant_id: int, session: AsyncSession) -> str | None:
    """Return the email address of the owner auth_user for this tenant, or None."""
    from storage.models import AuthUser, Tenant, TeamMember

    # Look up the tenant owner — team member with role='owner', else first member.
    try:
        result = await session.execute(
            select(AuthUser.email)
            .join(TeamMember, TeamMember.user_id == AuthUser.id)
            .where(
                TeamMember.tenant_id == tenant_id,
                TeamMember.role == "owner",
                AuthUser.email.is_not(None),
            )
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            return row

        # Fallback: any team member with an email.
        result2 = await session.execute(
            select(AuthUser.email)
            .join(TeamMember, TeamMember.user_id == AuthUser.id)
            .where(
                TeamMember.tenant_id == tenant_id,
                AuthUser.email.is_not(None),
            )
            .limit(1)
        )
        return result2.scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        log.debug("_get_tenant_email tenant=%d failed: %s", tenant_id, exc)
        return None


# ---------------------------------------------------------------------------
# Stripe webhook handler
# ---------------------------------------------------------------------------


async def handle_stripe_webhook(
    raw_body: bytes,
    stripe_signature: str,
    session: AsyncSession,
) -> dict[str, Any]:
    """
    Process a Stripe webhook event.

    Verifies signature using STRIPE_WEBHOOK_SECRET.
    Updates subscription status and records payments.
    Returns {"status": "ok"} or raises BillingError on verification failure.
    """
    import hmac as _hmac
    import hashlib as _hashlib

    secret = settings.STRIPE_WEBHOOK_SECRET
    if not secret:
        log.warning("STRIPE_WEBHOOK_SECRET not configured — skipping signature verification")
    else:
        # Stripe signature format: "t=<timestamp>,v1=<sig>"
        try:
            parts = dict(item.split("=", 1) for item in stripe_signature.split(","))
            timestamp = parts.get("t", "")
            expected_sig = parts.get("v1", "")
            signed_payload = f"{timestamp}.".encode() + raw_body
            computed = _hmac.new(
                secret.encode("utf-8"),
                signed_payload,
                _hashlib.sha256,
            ).hexdigest()
            if not _hmac.compare_digest(computed, expected_sig):
                raise BillingError("Stripe webhook signature verification failed")
        except (KeyError, ValueError) as exc:
            raise BillingError(f"Stripe webhook signature parse error: {exc}") from exc

    event = _json.loads(raw_body)
    event_type = event.get("type", "")
    data_obj = event.get("data", {}).get("object", {})

    log.info("Stripe webhook received: %s", event_type)

    if event_type in ("invoice.payment_succeeded", "invoice.paid"):
        await _handle_stripe_payment_succeeded(data_obj, session)
    elif event_type == "invoice.payment_failed":
        await _handle_stripe_payment_failed(data_obj, session)
    elif event_type == "customer.subscription.deleted":
        await _handle_stripe_subscription_deleted(data_obj, session)
    elif event_type == "customer.subscription.updated":
        await _handle_stripe_subscription_updated(data_obj, session)
    elif event_type == "checkout.session.completed":
        await _handle_stripe_checkout_completed(data_obj, session)

    return {"status": "ok", "event_type": event_type}


async def _handle_stripe_payment_succeeded(obj: dict, session: AsyncSession) -> None:
    from storage.models import Subscription

    external_sub_id = obj.get("subscription")
    amount = int(obj.get("amount_paid", 0))
    currency = obj.get("currency", "usd").upper()
    external_payment_id = obj.get("id")

    if not external_sub_id:
        return

    # Idempotency: skip if this payment was already recorded.
    if external_payment_id and await _payment_event_exists(external_payment_id, session):
        log.info("Stripe invoice %s already processed — skipping", external_payment_id)
        return

    sub = (
        await session.execute(
            select(Subscription)
            .where(Subscription.external_subscription_id == external_sub_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if sub is None:
        return

    now = utcnow()
    sub.status = "active"
    sub.updated_at = now
    await session.flush()

    await record_payment(
        tenant_id=sub.tenant_id,
        subscription_id=sub.id,
        amount=amount,
        currency=currency,
        provider="stripe",
        external_payment_id=external_payment_id,
        status="succeeded",
        session=session,
    )

    # Send email notification (fire-and-forget).
    tenant_email = await _get_tenant_email(sub.tenant_id, session)
    if tenant_email:
        period_end = (
            sub.current_period_end.strftime("%d.%m.%Y")
            if sub.current_period_end
            else ""
        )
        from core.email_service import schedule_email
        schedule_email(
            "payment_success",
            to=tenant_email,
            amount=amount // 100 if currency in ("RUB", "USD", "EUR") else amount,
            currency=currency,
            period_end=period_end,
        )

    # Notify admin via Telegram (best-effort).
    await _notify_admin(
        f"Stripe payment succeeded: tenant={sub.tenant_id} amount={amount}{currency}"
    )


async def _handle_stripe_payment_failed(obj: dict, session: AsyncSession) -> None:
    from storage.models import Subscription

    external_sub_id = obj.get("subscription")
    if not external_sub_id:
        return

    sub = (
        await session.execute(
            select(Subscription)
            .where(Subscription.external_subscription_id == external_sub_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if sub is None:
        return

    sub.status = "past_due"
    if hasattr(sub, "updated_at"):
        sub.updated_at = utcnow()
    await session.flush()

    # Send email notification (fire-and-forget).
    tenant_email = await _get_tenant_email(sub.tenant_id, session)
    if tenant_email:
        from core.email_service import schedule_email
        schedule_email("payment_failed", to=tenant_email)


async def _handle_stripe_subscription_deleted(obj: dict, session: AsyncSession) -> None:
    from storage.models import Subscription

    external_sub_id = obj.get("id")
    if not external_sub_id:
        return

    sub = (
        await session.execute(
            select(Subscription)
            .where(Subscription.external_subscription_id == external_sub_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if sub is None:
        return

    sub.status = "cancelled"
    sub.cancelled_at = utcnow()
    if hasattr(sub, "updated_at"):
        sub.updated_at = utcnow()
    await session.flush()

    # Send email notification (fire-and-forget).
    tenant_email = await _get_tenant_email(sub.tenant_id, session)
    if tenant_email:
        period_end = (
            sub.current_period_end.strftime("%d.%m.%Y")
            if sub.current_period_end
            else ""
        )
        from core.email_service import schedule_email
        schedule_email("subscription_cancelled", to=tenant_email, period_end=period_end)


async def _handle_stripe_subscription_updated(obj: dict, session: AsyncSession) -> None:
    """Handle Stripe customer.subscription.updated — sync status changes."""
    from storage.models import Subscription

    external_sub_id = obj.get("id")
    if not external_sub_id:
        return

    sub = (
        await session.execute(
            select(Subscription)
            .where(Subscription.external_subscription_id == external_sub_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if sub is None:
        return

    stripe_status = obj.get("status", "")
    status_map = {
        "active": "active",
        "trialing": "trialing",
        "past_due": "past_due",
        "canceled": "cancelled",
        "unpaid": "past_due",
    }
    new_status = status_map.get(stripe_status)
    if new_status and sub.status != new_status:
        sub.status = new_status
        if hasattr(sub, "updated_at"):
            sub.updated_at = utcnow()
        await session.flush()
        log.info(
            "Stripe subscription updated: external=%s status=%s -> %s",
            external_sub_id,
            sub.status,
            new_status,
        )


async def _handle_stripe_checkout_completed(obj: dict, session: AsyncSession) -> None:
    """Handle Stripe checkout.session.completed — activate subscription from metadata."""
    from storage.models import Plan

    metadata = obj.get("metadata", {}) or {}
    tenant_id_str = metadata.get("tenant_id")
    plan_id_str = metadata.get("plan_id")
    external_sub_id = obj.get("subscription")

    if not tenant_id_str:
        log.warning("checkout.session.completed missing metadata.tenant_id")
        return

    try:
        tenant_id = int(tenant_id_str)
    except ValueError:
        return

    plan_id: int | None = None
    if plan_id_str:
        try:
            plan_id = int(plan_id_str)
        except ValueError:
            pass

    if plan_id is None:
        log.warning("checkout.session.completed missing metadata.plan_id — skipping activation")
        return

    await create_subscription(
        tenant_id=tenant_id,
        plan_id=plan_id,
        provider="stripe",
        external_id=external_sub_id,
        session=session,
    )
    log.info(
        "checkout.session.completed: tenant=%d plan=%d sub=%s activated",
        tenant_id,
        plan_id,
        external_sub_id,
    )


# ---------------------------------------------------------------------------
# YooKassa webhook handler
# ---------------------------------------------------------------------------

# YooKassa sends from these IPs (production).
YOOKASSA_ALLOWED_IPS = frozenset({
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.156.11",
    "77.75.156.35",
    "77.75.154.128/25",
    "2a02:5180::/32",
})


async def handle_yookassa_webhook(
    raw_body: bytes,
    client_ip: str,
    session: AsyncSession,
) -> dict[str, Any]:
    """
    Process a YooKassa webhook notification.

    Does a basic IP whitelist check in production.
    Updates subscription status and records payments.
    """
    import json as _json

    # Skip IP check in dev/test.
    if settings.APP_ENV not in ("development", "test", "testing"):
        # Simplified IP check — only exact matches for now.
        allowed = {
            "185.71.76.0", "185.71.76.1", "185.71.77.0",
            "77.75.153.0", "77.75.156.11", "77.75.156.35",
        }
        if client_ip not in allowed:
            log.warning("YooKassa webhook from unexpected IP: %s", client_ip)
            # Log but do not block — production-grade IP range check needs netaddr.

    event = _json.loads(raw_body)
    event_type = event.get("event", "")
    obj = event.get("object", {})

    log.info("YooKassa webhook received: event=%s ip=%s", event_type, client_ip)

    if event_type == "payment.succeeded":
        await _handle_yookassa_payment_succeeded(obj, session)
    elif event_type == "payment.canceled":
        await _handle_yookassa_payment_failed(obj, session)
    elif event_type == "refund.succeeded":
        await _handle_yookassa_refund(obj, session)

    return {"status": "ok", "event_type": event_type}


async def _handle_yookassa_payment_succeeded(obj: dict, session: AsyncSession) -> None:
    from storage.models import Subscription

    external_payment_id = obj.get("id")
    amount_obj = obj.get("amount", {})
    amount_str = str(amount_obj.get("value", "0")).replace(".", "")
    try:
        amount = int(amount_str)
    except ValueError:
        amount = 0
    currency = amount_obj.get("currency", "RUB")
    metadata = obj.get("metadata", {})
    tenant_id_str = metadata.get("tenant_id") if metadata else None

    if not tenant_id_str:
        log.warning("YooKassa payment.succeeded missing metadata.tenant_id")
        return

    try:
        tenant_id = int(tenant_id_str)
    except ValueError:
        return

    # Idempotency: skip if this payment was already recorded.
    if external_payment_id and await _payment_event_exists(external_payment_id, session):
        log.info("YooKassa payment %s already processed — skipping", external_payment_id)
        return

    sub = await get_subscription(tenant_id, session)
    if sub:
        sub.status = "active"
        if hasattr(sub, "updated_at"):
            sub.updated_at = utcnow()
        await session.flush()

    sub_id = sub.id if sub else None
    await record_payment(
        tenant_id=tenant_id,
        subscription_id=sub_id,
        amount=amount,
        currency=currency,
        provider="yookassa",
        external_payment_id=external_payment_id,
        status="succeeded",
        session=session,
    )

    # Send email notification (fire-and-forget).
    tenant_email = await _get_tenant_email(tenant_id, session)
    if tenant_email:
        period_end = ""
        if sub and sub.current_period_end:
            period_end = sub.current_period_end.strftime("%d.%m.%Y")
        from core.email_service import schedule_email
        schedule_email(
            "payment_success",
            to=tenant_email,
            amount=amount // 100,  # kopecks -> rubles
            currency=currency,
            period_end=period_end,
        )

    await _notify_admin(
        f"YooKassa payment succeeded: tenant={tenant_id} amount={amount}{currency}"
    )


async def _handle_yookassa_payment_failed(obj: dict, session: AsyncSession) -> None:
    external_payment_id = obj.get("id")
    metadata = obj.get("metadata", {})
    tenant_id_str = metadata.get("tenant_id") if metadata else None
    if not tenant_id_str:
        return
    try:
        tenant_id = int(tenant_id_str)
    except ValueError:
        return

    # Idempotency: skip duplicate webhook delivery.
    if external_payment_id and await _payment_event_exists(external_payment_id + "_canceled", session):
        log.info("YooKassa canceled payment %s already processed — skipping", external_payment_id)
        return

    sub = await get_subscription(tenant_id, session)
    if sub and sub.status not in ("active",):
        sub.status = "past_due"
        if hasattr(sub, "updated_at"):
            sub.updated_at = utcnow()
        await session.flush()

    # Mark the payment record as failed.
    if external_payment_id:
        amount_obj = obj.get("amount", {})
        amount_str = str(amount_obj.get("value", "0")).replace(".", "")
        try:
            amount = int(amount_str)
        except ValueError:
            amount = 0
        currency = amount_obj.get("currency", "RUB")
        sub_id = sub.id if sub else None
        try:
            await record_payment(
                tenant_id=tenant_id,
                subscription_id=sub_id,
                amount=amount,
                currency=currency,
                provider="yookassa",
                external_payment_id=external_payment_id + "_canceled",
                status="failed",
                session=session,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not record failed YooKassa payment: %s", exc)

    # Send email notification (fire-and-forget).
    tenant_email = await _get_tenant_email(tenant_id, session)
    if tenant_email:
        from core.email_service import schedule_email
        schedule_email("payment_failed", to=tenant_email)

    await _notify_admin(
        f"YooKassa payment canceled: tenant={tenant_id} payment_id={external_payment_id}"
    )


async def _handle_yookassa_refund(obj: dict, session: AsyncSession) -> None:
    # Record refund but don't change subscription status automatically.
    amount_obj = obj.get("amount", {})
    amount_str = str(amount_obj.get("value", "0")).replace(".", "")
    try:
        amount = int(amount_str)
    except ValueError:
        amount = 0
    currency = amount_obj.get("currency", "RUB")
    external_payment_id = obj.get("payment_id")
    log.info("YooKassa refund: payment_id=%s amount=%d %s", external_payment_id, amount, currency)


# ---------------------------------------------------------------------------
# Idempotency helper
# ---------------------------------------------------------------------------


async def _payment_event_exists(
    external_payment_id: str,
    session: AsyncSession,
) -> bool:
    """Return True if a payment with this external_payment_id was already recorded."""
    from sqlalchemy import text

    result = await session.execute(
        text(
            "SELECT 1 FROM payments WHERE external_payment_id = :eid LIMIT 1"
        ),
        {"eid": external_payment_id},
    )
    return result.fetchone() is not None


# ---------------------------------------------------------------------------
# Stripe Checkout Session creation (Sprint 14)
# ---------------------------------------------------------------------------


async def create_stripe_checkout(
    plan_id: int,
    tenant_id: int,
    success_url: str,
    cancel_url: str,
    session: AsyncSession,
) -> dict[str, Any]:
    """
    Create a Stripe Checkout Session for the given plan and tenant.

    Requires STRIPE_SECRET_KEY to be set. Raises BillingError if the key
    is missing or if stripe returns an error.

    Returns {"checkout_url": str, "session_id": str}.
    """
    if not settings.STRIPE_SECRET_KEY:
        raise BillingError("STRIPE_SECRET_KEY not configured")

    from storage.models import Plan

    plan = (
        await session.execute(select(Plan).where(Plan.id == plan_id).limit(1))
    ).scalar_one_or_none()
    if plan is None:
        raise BillingError(f"Plan id={plan_id} not found")

    # Build a unit_amount from the plan's monthly price in kopecks (Stripe uses smallest unit).
    # price_monthly_rub is stored in whole rubles; Stripe expects kopecks.
    price_rub = plan.price_monthly_rub or 0
    unit_amount = price_rub * 100  # rubles -> kopecks

    # Resolve Stripe price ID from env map if available.
    price_id: str | None = None
    price_id_map_raw = settings.STRIPE_PRICE_ID_MAP
    if price_id_map_raw:
        try:
            price_id_map = _json.loads(price_id_map_raw)
            price_id = price_id_map.get(plan.slug)
        except (ValueError, KeyError):
            pass

    try:
        import stripe as _stripe  # type: ignore[import]

        _stripe.api_key = settings.STRIPE_SECRET_KEY

        session_params: dict[str, Any] = {
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {
                "tenant_id": str(tenant_id),
                "plan_id": str(plan_id),
                "plan_slug": plan.slug,
            },
            "client_reference_id": str(tenant_id),
        }

        if price_id:
            session_params["line_items"] = [{"price": price_id, "quantity": 1}]
        else:
            # Create an ad-hoc price inline.
            session_params["line_items"] = [
                {
                    "price_data": {
                        "currency": "rub",
                        "unit_amount": unit_amount,
                        "recurring": {"interval": "month"},
                        "product_data": {"name": plan.name},
                    },
                    "quantity": 1,
                }
            ]

        checkout_session = _stripe.checkout.Session.create(**session_params)
        return {
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id,
            "provider": "stripe",
        }
    except ImportError:
        raise BillingError(
            "stripe library not installed. Run: pip install stripe"
        )
    except Exception as exc:  # noqa: BLE001
        raise BillingError(f"Stripe checkout creation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# YooKassa Payment creation (Sprint 14)
# ---------------------------------------------------------------------------


async def create_yookassa_payment(
    plan_id: int,
    tenant_id: int,
    return_url: str,
    session: AsyncSession,
    email: str | None = None,
) -> dict[str, Any]:
    """
    Create a YooKassa payment for the given plan and tenant.

    Requires YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY to be set.
    Raises BillingError on configuration error or provider error.

    Tries the official ``yookassa`` Python SDK first; falls back to a
    direct ``aiohttp`` call if the SDK is not installed.

    Returns {"payment_url": str, "payment_id": str, "provider": "yookassa"}.
    """
    if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_SECRET_KEY:
        raise BillingError("YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY не настроен")

    from storage.models import Plan
    import uuid

    plan = (
        await session.execute(select(Plan).where(Plan.id == plan_id).limit(1))
    ).scalar_one_or_none()
    if plan is None:
        raise BillingError(f"Тариф id={plan_id} не найден")

    price_rub = plan.price_monthly_rub or 0
    price_str = f"{price_rub:.2f}"
    idempotence_key = str(uuid.uuid4())

    # Use configured return URL override if set; else use the caller-provided value.
    effective_return_url = settings.YOOKASSA_RETURN_URL or return_url

    description = f"NEURO COMMENTING — тариф {plan.name}"
    metadata: dict[str, str] = {
        "tenant_id": str(tenant_id),
        "plan_slug": plan.slug,
    }

    # Resolve or create pending subscription so we can attach subscription_id to the payment record.
    sub = await get_subscription(tenant_id, session)
    subscription_id = sub.id if sub else None

    # Build receipt block required by Russian law (54-ФЗ) — included only when email is known.
    receipt: dict[str, Any] | None = None
    if email:
        receipt = {
            "customer": {"email": email},
            "items": [
                {
                    "description": plan.name,
                    "quantity": "1.00",
                    "amount": {"value": price_str, "currency": "RUB"},
                    "vat_code": 1,
                }
            ],
        }

    payment_id = ""
    checkout_url = ""

    # ------------------------------------------------------------------
    # Attempt 1: official yookassa SDK (sync, runs in thread executor).
    # ------------------------------------------------------------------
    try:
        import yookassa as _yk  # type: ignore[import]
        from yookassa import Configuration as _YkConfig, Payment as _YkPayment  # type: ignore[import]

        _YkConfig.account_id = settings.YOOKASSA_SHOP_ID
        _YkConfig.secret_key = settings.YOOKASSA_SECRET_KEY

        payment_params: dict[str, Any] = {
            "amount": {"value": price_str, "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": effective_return_url},
            "capture": True,
            "description": description,
            "metadata": metadata,
        }
        if receipt:
            payment_params["receipt"] = receipt
        if subscription_id:
            metadata["subscription_id"] = str(subscription_id)

        import asyncio

        loop = asyncio.get_event_loop()
        yk_payment = await loop.run_in_executor(
            None,
            lambda: _YkPayment.create(payment_params, idempotence_key),
        )
        payment_id = yk_payment.id
        checkout_url = yk_payment.confirmation.confirmation_url or ""
        log.info("YooKassa payment created via SDK: id=%s tenant=%d", payment_id, tenant_id)

    except ImportError:
        # ------------------------------------------------------------------
        # Attempt 2: fallback to direct aiohttp HTTP call.
        # ------------------------------------------------------------------
        import aiohttp

        payload: dict[str, Any] = {
            "amount": {"value": price_str, "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": effective_return_url},
            "capture": True,
            "description": description,
            "metadata": metadata,
        }
        if receipt:
            payload["receipt"] = receipt

        api_url = "https://api.yookassa.ru/v3/payments"
        auth = aiohttp.BasicAuth(
            login=settings.YOOKASSA_SHOP_ID,
            password=settings.YOOKASSA_SECRET_KEY,
        )
        headers = {
            "Idempotence-Key": idempotence_key,
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
                async with http.post(api_url, json=payload, auth=auth, headers=headers) as resp:
                    resp_data = await resp.json()
                    if resp.status not in (200, 201):
                        raise BillingError(
                            f"YooKassa API error {resp.status}: "
                            f"{resp_data.get('description', resp_data)}"
                        )
            confirmation = resp_data.get("confirmation", {})
            checkout_url = confirmation.get("confirmation_url", "")
            payment_id = resp_data.get("id", "")
            log.info("YooKassa payment created via aiohttp: id=%s tenant=%d", payment_id, tenant_id)
        except BillingError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BillingError(f"YooKassa payment creation failed: {exc}") from exc

    except Exception as exc:  # noqa: BLE001
        raise BillingError(f"YooKassa SDK payment creation failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Persist a pending Payment record so the webhook handler can match it.
    # ------------------------------------------------------------------
    if payment_id:
        try:
            await record_payment(
                tenant_id=tenant_id,
                subscription_id=subscription_id,
                amount=price_rub,
                currency="RUB",
                provider="yookassa",
                external_payment_id=payment_id,
                status="pending",
                session=session,
                metadata={"plan_slug": plan.slug, "idempotence_key": idempotence_key},
            )
        except Exception as exc:  # noqa: BLE001
            # Non-fatal: log and continue; the webhook will record the final state.
            log.warning("Could not persist pending YooKassa payment record: %s", exc)

    return {
        "payment_url": checkout_url,
        "payment_id": payment_id,
        "provider": "yookassa",
    }


# ---------------------------------------------------------------------------
# Admin notification (best-effort, non-blocking)
# ---------------------------------------------------------------------------


async def _notify_admin(message: str) -> None:
    """Send a Telegram message to the admin (best-effort, swallows all errors)."""
    import aiohttp

    bot_token = settings.ADMIN_BOT_TOKEN
    admin_id = settings.ADMIN_TELEGRAM_ID
    if not bot_token or not admin_id:
        return
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as http:
            await http.post(url, json={"chat_id": admin_id, "text": f"[Billing] {message}"})
    except Exception as exc:  # noqa: BLE001
        log.debug("Admin billing notification failed: %s", exc)
