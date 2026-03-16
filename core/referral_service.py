"""
Реферальная система NEURO COMMENTING.

Механика:
- Каждый user получает уникальный 8-символьный referral_code
- Новый user регистрируется с ?ref=CODE → создаётся referral запись
- Награда: 20% от первого платежа реферала (credit на аккаунт)

Уровни:
- Bronze (1-4): 10% recurring 12 мес
- Silver (5-14): 15% recurring 12 мес
- Gold (15-49): 20% recurring 18 мес
- Platinum (50+): 25% recurring 24 мес
"""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from utils.helpers import utcnow

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REFERRAL_CODE_LENGTH = 8
REFERRAL_CODE_ALPHABET = string.ascii_uppercase + string.digits

# Default reward: 20% of first payment
DEFAULT_REWARD_PERCENT = 20

# Referral tier definitions
REFERRAL_TIERS = [
    {"name": "bronze", "min_referrals": 1, "commission_percent": 10, "commission_months": 12},
    {"name": "silver", "min_referrals": 5, "commission_percent": 15, "commission_months": 12},
    {"name": "gold", "min_referrals": 15, "commission_percent": 20, "commission_months": 18},
    {"name": "platinum", "min_referrals": 50, "commission_percent": 25, "commission_months": 24},
]


class ReferralError(Exception):
    """Base error for referral operations."""


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------

def generate_referral_code(user_id: int) -> str:
    """Generate a unique 8-character alphanumeric referral code.

    Uses cryptographically secure random + user_id suffix for collision resistance.
    """
    random_part = "".join(secrets.choice(REFERRAL_CODE_ALPHABET) for _ in range(REFERRAL_CODE_LENGTH - 2))
    # Last 2 chars derived from user_id for extra uniqueness
    suffix = f"{user_id % 100:02d}"
    # Replace digits with letters if needed to keep it alphanumeric
    code = (random_part + suffix).upper()[:REFERRAL_CODE_LENGTH]
    return code


async def get_or_create_referral_code(
    user_id: int,
    session: AsyncSession,
) -> str:
    """Get existing referral code for user, or generate and persist a new one.

    Lazy generation: code is created on first request, then cached in auth_users.referral_code.
    """
    from storage.models import AuthUser

    row = (await session.execute(
        select(AuthUser.referral_code).where(AuthUser.id == user_id)
    )).scalar_one_or_none()

    if row is not None and row:
        return row

    # Generate a new unique code, retry on collision (extremely unlikely)
    for _ in range(10):
        code = generate_referral_code(user_id)
        existing = (await session.execute(
            select(AuthUser.id).where(AuthUser.referral_code == code)
        )).scalar_one_or_none()
        if existing is None:
            break
    else:
        raise ReferralError("failed_to_generate_unique_code")

    await session.execute(
        update(AuthUser).where(AuthUser.id == user_id).values(referral_code=code)
    )
    await session.flush()
    log.info("Generated referral code %s for user_id=%d", code, user_id)
    return code


# ---------------------------------------------------------------------------
# Referral tracking
# ---------------------------------------------------------------------------

async def track_referral(
    new_user_id: int,
    referral_code: str,
    session: AsyncSession,
) -> dict[str, Any] | None:
    """Record a referral when a new user signs up with ?ref=CODE.

    Returns the created referral dict, or None if the code is invalid / self-referral.
    """
    from storage.models import AuthUser, Referral

    # Find the referrer by code
    referrer = (await session.execute(
        select(AuthUser).where(AuthUser.referral_code == referral_code)
    )).scalar_one_or_none()

    if referrer is None:
        log.warning("Invalid referral code: %s", referral_code)
        return None

    # Block self-referral
    if referrer.id == new_user_id:
        log.warning("Self-referral attempt: user_id=%d code=%s", new_user_id, referral_code)
        return None

    # Check if already referred
    existing = (await session.execute(
        select(Referral).where(Referral.referred_id == new_user_id)
    )).scalar_one_or_none()

    if existing is not None:
        log.warning("User %d already referred, skipping duplicate", new_user_id)
        return None

    now = utcnow()
    referral = Referral(
        referrer_id=referrer.id,
        referred_id=new_user_id,
        referral_code=referral_code,
        status="pending",
        reward_amount=0,
        created_at=now,
    )
    session.add(referral)
    await session.flush()

    log.info(
        "Referral tracked: referrer=%d referred=%d code=%s",
        referrer.id, new_user_id, referral_code,
    )
    return _serialize_referral(referral)


# ---------------------------------------------------------------------------
# Stats & listing
# ---------------------------------------------------------------------------

def _get_tier(total_referrals: int) -> dict[str, Any]:
    """Determine referral tier based on total confirmed referrals."""
    tier = {"name": "none", "commission_percent": 0, "commission_months": 0, "min_referrals": 0}
    for t in REFERRAL_TIERS:
        if total_referrals >= t["min_referrals"]:
            tier = t
    return tier


async def get_referral_stats(
    user_id: int,
    session: AsyncSession,
) -> dict[str, Any]:
    """Get referral statistics for a user.

    Returns: total_referrals, total_earned, pending_rewards, referral_code, tier.
    """
    from storage.models import Referral

    code = await get_or_create_referral_code(user_id, session)

    # Count referrals by status
    rows = (await session.execute(
        select(
            Referral.status,
            func.count(Referral.id).label("cnt"),
            func.coalesce(func.sum(Referral.reward_amount), 0).label("total_amount"),
        )
        .where(Referral.referrer_id == user_id)
        .group_by(Referral.status)
    )).all()

    total_referrals = 0
    total_earned = 0
    pending_rewards = 0
    confirmed_count = 0

    for row in rows:
        cnt = int(row.cnt)
        amount = int(row.total_amount)
        total_referrals += cnt
        if row.status == "rewarded":
            total_earned += amount
            confirmed_count += cnt
        elif row.status == "pending":
            pending_rewards += amount
        elif row.status == "confirmed":
            confirmed_count += cnt

    tier = _get_tier(confirmed_count)

    return {
        "referral_code": code,
        "referral_link": f"https://neurocommenting.io/r/{code}",
        "total_referrals": total_referrals,
        "confirmed_referrals": confirmed_count,
        "total_earned": total_earned,
        "pending_rewards": pending_rewards,
        "tier": tier["name"],
        "commission_percent": tier["commission_percent"],
        "commission_months": tier["commission_months"],
    }


async def list_referrals(
    user_id: int,
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List referrals for a user with pagination."""
    from storage.models import AuthUser, Referral

    # Total count
    total = (await session.execute(
        select(func.count(Referral.id)).where(Referral.referrer_id == user_id)
    )).scalar_one()

    # Referrals with referred user info
    stmt = (
        select(Referral, AuthUser.email, AuthUser.first_name, AuthUser.telegram_username)
        .join(AuthUser, AuthUser.id == Referral.referred_id)
        .where(Referral.referrer_id == user_id)
        .order_by(Referral.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()

    items = []
    for ref, email, first_name, tg_username in rows:
        item = _serialize_referral(ref)
        # Mask email for privacy: show first 2 chars + domain
        masked_email = None
        if email:
            parts = email.split("@")
            if len(parts) == 2:
                masked_email = parts[0][:2] + "***@" + parts[1]
        item["referred_name"] = first_name or tg_username or masked_email or "Anonymous"
        items.append(item)

    return {"items": items, "total": total}


# ---------------------------------------------------------------------------
# Reward processing
# ---------------------------------------------------------------------------

async def process_referral_reward(
    referred_tenant_id: int,
    payment_amount: int,
    session: AsyncSession,
) -> dict[str, Any] | None:
    """Process referral reward when a referred user makes their first payment.

    Awards 20% of the payment amount to the referrer as credit.
    Returns the updated referral dict or None if no referral exists.
    """
    from storage.models import AuthUser, Referral, TeamMember

    # Find the referred user's auth_user_id from their tenant
    member = (await session.execute(
        select(TeamMember.user_id).where(
            TeamMember.tenant_id == referred_tenant_id,
            TeamMember.role == "owner",
        )
    )).scalar_one_or_none()

    if member is None:
        return None

    referred_user_id = member

    # Find pending referral for this user
    referral = (await session.execute(
        select(Referral).where(
            Referral.referred_id == referred_user_id,
            Referral.status.in_(["pending", "confirmed"]),
        )
    )).scalar_one_or_none()

    if referral is None:
        return None

    # Calculate reward (20% of payment)
    reward = int(payment_amount * DEFAULT_REWARD_PERCENT / 100)

    if reward <= 0:
        return None

    now = utcnow()
    referral.status = "rewarded"
    referral.reward_amount = reward
    referral.rewarded_at = now

    await session.flush()

    log.info(
        "Referral reward processed: referral_id=%d referrer=%d reward=%d from payment=%d",
        referral.id, referral.referrer_id, reward, payment_amount,
    )

    return _serialize_referral(referral)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _serialize_referral(ref: Any) -> dict[str, Any]:
    """Serialize a Referral ORM instance to dict."""
    created_at = ref.created_at
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    rewarded_at = getattr(ref, "rewarded_at", None)
    if isinstance(rewarded_at, datetime):
        rewarded_at = rewarded_at.isoformat()
    return {
        "id": ref.id,
        "referrer_id": ref.referrer_id,
        "referred_id": ref.referred_id,
        "referral_code": ref.referral_code,
        "status": ref.status,
        "reward_amount": ref.reward_amount,
        "created_at": created_at,
        "rewarded_at": rewarded_at,
    }
