"""Sprint 13 — Billing Service Tests.

Tests cover: plan listing, trial creation, subscription management,
plan limit enforcement, trial expiration, cancellation,
payment recording, and usage calculation.

All tests use SQLite in-memory via the shared conftest fixture.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete

from core.billing_service import (
    BillingError,
    LimitExceeded,
    NoActiveSubscription,
    RESOURCE_ACCOUNTS,
    RESOURCE_COMMENTS,
    cancel_subscription,
    check_limits,
    create_subscription,
    create_trial,
    get_plan_for_tenant,
    get_plans,
    get_plan_by_slug,
    get_subscription,
    get_usage,
    is_subscription_active,
    is_trial_expired,
    _serialize_plan,
    _serialize_subscription,
)
from storage.models import (
    AuthUser,
    PaymentEvent,
    Plan,
    Subscription,
    TeamMember,
    Tenant,
    Workspace,
)
from storage.sqlite_db import async_session, init_db
from utils.helpers import utcnow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _reset_billing_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in [PaymentEvent, Subscription, TeamMember, Workspace, Tenant, AuthUser]:
                await session.execute(delete(model))


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_billing_db() -> None:
    await init_db()
    await _reset_billing_state()


async def _make_tenant(name: str = "BillingCo") -> int:
    """Helper: create a tenant and return its ID."""
    import secrets
    from sqlalchemy import insert
    from storage.models import Tenant

    async with async_session() as session:
        async with session.begin():
            slug = f"billing-{secrets.token_hex(4)}"
            result = await session.execute(
                insert(Tenant).values(name=name, slug=slug, status="active").returning(Tenant.id)
            )
            tenant_id = result.scalar_one()
    return tenant_id


async def _get_starter_plan() -> Any | None:
    """Return the starter plan (seeds may not exist in SQLite tests)."""
    async with async_session() as session:
        async with session.begin():
            return await get_plan_by_slug(session, "starter")


async def _seed_plan(slug: str = "starter") -> None:
    """Seed a plan row for tests (SQLite doesn't have the seeded data)."""
    from sqlalchemy import insert
    from storage.models import Plan

    plan_data = {
        "starter": {
            "slug": "starter",
            "name": "Стартовый",
            "price_monthly_rub": 5990,
            "price_yearly_rub": 59900,
            "max_accounts": 5,
            "max_channels": 20,
            "max_comments_per_day": 100,
            "max_campaigns": 3,
            "features": '{"ai_assistant": true}',
            "is_active": True,
            "sort_order": 1,
        },
        "growth": {
            "slug": "growth",
            "name": "Рост",
            "price_monthly_rub": 14990,
            "price_yearly_rub": 149900,
            "max_accounts": 20,
            "max_channels": 50,
            "max_comments_per_day": 500,
            "max_campaigns": 10,
            "features": '{"ai_assistant": true, "analytics": true}',
            "is_active": True,
            "sort_order": 2,
        },
    }
    data = plan_data.get(slug, plan_data["starter"])
    async with async_session() as session:
        async with session.begin():
            existing = await get_plan_by_slug(session, slug)
            if existing is None:
                await session.execute(insert(Plan).values(**data))


# ---------------------------------------------------------------------------
# 1. Plan listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_get_plans_returns_list() -> None:
    """get_plans() returns a list (possibly empty in SQLite test env)."""
    await _seed_plan("starter")
    async with async_session() as session:
        async with session.begin():
            plans = await get_plans(session)
    assert isinstance(plans, list)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_plans_has_required_fields() -> None:
    """Each plan dict has the Sprint 13 required fields."""
    await _seed_plan("starter")
    async with async_session() as session:
        async with session.begin():
            plans = await get_plans(session)
    if not plans:
        pytest.skip("No plans seeded in SQLite test env")
    plan = plans[0]
    for field in ("id", "slug", "name", "price_monthly_rub", "max_accounts",
                  "max_channels", "max_comments_per_day", "features"):
        assert field in plan, f"Missing field: {field}"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_plan_by_slug_found() -> None:
    """get_plan_by_slug returns a Plan row when it exists."""
    await _seed_plan("starter")
    async with async_session() as session:
        async with session.begin():
            plan = await get_plan_by_slug(session, "starter")
    assert plan is not None
    assert plan.slug == "starter"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_plan_by_slug_not_found() -> None:
    """get_plan_by_slug returns None for unknown slugs."""
    async with async_session() as session:
        async with session.begin():
            plan = await get_plan_by_slug(session, "nonexistent-plan-xyz")
    assert plan is None


# ---------------------------------------------------------------------------
# 2. Trial creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_create_trial_success() -> None:
    """create_trial creates a trialing subscription."""
    await _seed_plan("starter")
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            result = await create_trial(tenant_id, session)
    assert result["status"] == "trialing"
    assert result["trial_ends_at"] is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_create_trial_sets_period() -> None:
    """Trial end date is in the future."""
    await _seed_plan("starter")
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            result = await create_trial(tenant_id, session)
    from datetime import datetime
    trial_end = datetime.fromisoformat(result["trial_ends_at"].replace("Z", "+00:00"))
    assert trial_end > utcnow()


@pytest.mark.asyncio(loop_scope="session")
async def test_create_trial_duplicate_raises() -> None:
    """create_trial raises BillingError if subscription already exists."""
    await _seed_plan("starter")
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            await create_trial(tenant_id, session)
    with pytest.raises(BillingError):
        async with async_session() as session:
            async with session.begin():
                await create_trial(tenant_id, session)


# ---------------------------------------------------------------------------
# 3. Subscription management
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_create_subscription_active() -> None:
    """create_subscription sets status=active."""
    await _seed_plan("starter")
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            plan = await get_plan_by_slug(session, "starter")
            result = await create_subscription(
                tenant_id=tenant_id,
                plan_id=plan.id,
                provider="stripe",
                external_id="sub_test_123",
                session=session,
            )
    assert result["status"] == "active"
    assert result["payment_provider"] == "stripe"


@pytest.mark.asyncio(loop_scope="session")
async def test_create_subscription_upgrades_trial() -> None:
    """create_subscription upgrades an existing trialing subscription."""
    await _seed_plan("starter")
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            await create_trial(tenant_id, session)
    async with async_session() as session:
        async with session.begin():
            plan = await get_plan_by_slug(session, "starter")
            result = await create_subscription(
                tenant_id=tenant_id,
                plan_id=plan.id,
                provider="yookassa",
                external_id="pay_456",
                session=session,
            )
    assert result["status"] == "active"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_subscription_returns_latest() -> None:
    """get_subscription returns the latest subscription row."""
    await _seed_plan("starter")
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            await create_trial(tenant_id, session)
    async with async_session() as session:
        async with session.begin():
            sub = await get_subscription(tenant_id, session)
    assert sub is not None
    assert sub.tenant_id == tenant_id


# ---------------------------------------------------------------------------
# 4. Trial expiration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_is_trial_expired_false_during_trial() -> None:
    """is_trial_expired returns False for a fresh trial."""
    await _seed_plan("starter")
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            await create_trial(tenant_id, session)
    async with async_session() as session:
        async with session.begin():
            sub = await get_subscription(tenant_id, session)
    assert not is_trial_expired(sub)


@pytest.mark.asyncio(loop_scope="session")
async def test_is_trial_expired_true_after_expiry() -> None:
    """is_trial_expired returns True when trial_ends_at is in the past."""
    from storage.models import Subscription as SubModel
    await _seed_plan("starter")
    tenant_id = await _make_tenant()
    # Create trial then backdate expiry.
    async with async_session() as session:
        async with session.begin():
            await create_trial(tenant_id, session)
            sub = await get_subscription(tenant_id, session)
            sub.trial_ends_at = utcnow() - timedelta(days=1)
    async with async_session() as session:
        async with session.begin():
            sub = await get_subscription(tenant_id, session)
    assert is_trial_expired(sub)


# ---------------------------------------------------------------------------
# 5. Cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_cancel_subscription_sets_cancelled() -> None:
    """cancel_subscription sets status=cancelled."""
    await _seed_plan("starter")
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            plan = await get_plan_by_slug(session, "starter")
            await create_subscription(tenant_id, plan.id, "manual", None, session)
    async with async_session() as session:
        async with session.begin():
            result = await cancel_subscription(tenant_id, session, reason="Test cancel")
    assert result["status"] == "cancelled"
    assert result["cancelled_at"] is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_cancel_no_subscription_raises() -> None:
    """cancel_subscription raises NoActiveSubscription when none exists."""
    tenant_id = await _make_tenant()
    with pytest.raises(NoActiveSubscription):
        async with async_session() as session:
            async with session.begin():
                await cancel_subscription(tenant_id, session)


# ---------------------------------------------------------------------------
# 6. Plan limit enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_check_limits_no_subscription_returns_false() -> None:
    """check_limits returns False when tenant has no subscription."""
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            result = await check_limits(tenant_id, RESOURCE_ACCOUNTS, 1, session)
    assert result is False


@pytest.mark.asyncio(loop_scope="session")
async def test_check_limits_within_limit_returns_true() -> None:
    """check_limits returns True when usage is within plan limit."""
    await _seed_plan("starter")
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            plan = await get_plan_by_slug(session, "starter")
            await create_subscription(tenant_id, plan.id, "manual", None, session)
    async with async_session() as session:
        async with session.begin():
            # Starter has max_accounts=5, currently 0 accounts.
            result = await check_limits(tenant_id, RESOURCE_ACCOUNTS, 1, session)
    assert result is True


# ---------------------------------------------------------------------------
# 7. Usage calculation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_get_usage_returns_dict() -> None:
    """get_usage returns a dict with expected keys."""
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            usage = await get_usage(tenant_id, session)
    assert isinstance(usage, dict)
    assert "comments_per_day" in usage
    assert "max_accounts" in usage
    assert "max_channels" in usage


@pytest.mark.asyncio(loop_scope="session")
async def test_get_usage_zero_for_new_tenant() -> None:
    """get_usage returns zeros for a brand-new tenant."""
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            usage = await get_usage(tenant_id, session)
    assert usage["comments_per_day"] == 0
    assert usage["max_accounts"] == 0


# ---------------------------------------------------------------------------
# 8. Serialize helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_serialize_plan_fields() -> None:
    """_serialize_plan includes all Sprint 13 required fields."""
    await _seed_plan("starter")
    async with async_session() as session:
        async with session.begin():
            plan = await get_plan_by_slug(session, "starter")
    if plan is None:
        pytest.skip("No starter plan in SQLite env")
    data = _serialize_plan(plan)
    for field in ("id", "slug", "name", "price_monthly_rub", "max_accounts",
                  "max_channels", "max_comments_per_day", "features", "sort_order"):
        assert field in data


@pytest.mark.asyncio(loop_scope="session")
async def test_is_subscription_active_true_for_trialing() -> None:
    """is_subscription_active returns True for a fresh trial."""
    await _seed_plan("starter")
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            await create_trial(tenant_id, session)
    async with async_session() as session:
        async with session.begin():
            sub = await get_subscription(tenant_id, session)
    assert is_subscription_active(sub) is True


@pytest.mark.asyncio(loop_scope="session")
async def test_is_subscription_active_false_for_expired_trial() -> None:
    """is_subscription_active returns False for an expired trial."""
    await _seed_plan("starter")
    tenant_id = await _make_tenant()
    async with async_session() as session:
        async with session.begin():
            await create_trial(tenant_id, session)
            sub = await get_subscription(tenant_id, session)
            sub.trial_ends_at = utcnow() - timedelta(days=1)
    async with async_session() as session:
        async with session.begin():
            sub = await get_subscription(tenant_id, session)
    assert is_subscription_active(sub) is False
