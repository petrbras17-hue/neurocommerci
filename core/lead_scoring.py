"""Lead scoring engine — двумерный скоринг (Profile + Engagement).

Profile Score (0–80):  демография, компания, email, вертикаль, источник.
Engagement Score (0–120): продуктовые действия (регистрация, аккаунты, фарм, AI, активность).

Категории:
  Cold       0–30
  Warm      31–70
  Hot       71–120
  PQL      121+
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import (
    Account,
    AuthUser,
    ChannelMapEntry,
    FarmConfig,
    Lead,
    Tenant,
    UsageEvent,
    WarmupSession,
    Workspace,
)
from storage.sqlite_db import async_session
from utils.helpers import utcnow
from utils.logger import log

# ---------------------------------------------------------------------------
# Free email domains (non-corporate)
# ---------------------------------------------------------------------------
_FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com",
    "mail.ru", "inbox.ru", "list.ru", "bk.ru",
    "yandex.ru", "yandex.com", "ya.ru",
    "outlook.com", "hotmail.com", "live.com",
    "icloud.com", "me.com",
    "protonmail.com", "proton.me",
    "rambler.ru",
    "yahoo.com", "yahoo.co.uk",
    "zoho.com",
    "aol.com",
    "tutanota.com", "tuta.io",
})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LeadScore:
    lead_id: int
    email: str
    name: str
    company: str
    use_case: Optional[str]
    utm_source: Optional[str]
    telegram_username: Optional[str]
    created_at: Optional[datetime]

    # Linked auth_user (if registered)
    auth_user_id: Optional[int]

    # Scores
    profile_score: int
    engagement_score: int
    total_score: int
    category: str

    # Engagement breakdown (for transparency)
    engagement_details: Dict[str, bool]


# ---------------------------------------------------------------------------
# Profile scoring helpers
# ---------------------------------------------------------------------------

def _is_corporate_email(email: str) -> bool:
    """Return True if email domain is not in the free-provider list."""
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].lower().strip()
    return domain not in _FREE_EMAIL_DOMAINS


def _profile_score(lead: Any) -> int:
    """Compute profile score (0–80) from lead fields."""
    score = 0

    # Компания указана: +20
    if lead.company and lead.company.strip():
        score += 20

    # Корпоративный email: +15
    if _is_corporate_email(lead.email or ""):
        score += 15

    # Телефон / Telegram username указан: +10
    if lead.telegram_username and lead.telegram_username.strip():
        score += 10

    # Вертикаль: +15
    uc = (lead.use_case or "").lower().strip()
    if uc in ("ecom", "edtech", "saas"):
        score += 15

    # UTM source bonus (proxy for intent): +10
    utm = (lead.utm_source or "").lower().strip()
    if utm in ("yandex", "google", "referral", "telegram"):
        score += 10

    # Cap at 80
    return min(score, 80)


# ---------------------------------------------------------------------------
# Engagement scoring (async, hits DB)
# ---------------------------------------------------------------------------

async def _engagement_score(
    session: AsyncSession,
    auth_user_id: Optional[int],
    tenant_id: Optional[int],
) -> tuple[int, Dict[str, bool]]:
    """Compute engagement score (0–120) by checking product tables.

    Returns (score, details_dict).
    """
    score = 0
    details: Dict[str, bool] = {}

    if auth_user_id is None or tenant_id is None:
        return score, details

    # --- Registered: +30 ---
    details["registered"] = True
    score += 30

    # --- First account uploaded: +20 ---
    acct_count_q = select(func.count(Account.id)).where(Account.tenant_id == tenant_id)
    acct_count = (await session.execute(acct_count_q)).scalar() or 0
    has_account = acct_count >= 1
    details["account_uploaded"] = has_account
    if has_account:
        score += 20

    # --- Farm started: +25 ---
    farm_q = select(func.count(FarmConfig.id)).where(
        FarmConfig.tenant_id == tenant_id,
        FarmConfig.status.in_(["running", "paused", "stopped"]),
    )
    farm_count = (await session.execute(farm_q)).scalar() or 0
    has_farm = farm_count >= 1
    details["farm_started"] = has_farm
    if has_farm:
        score += 25

    # --- AI comment sent: +15 (usage_event with type containing 'comment') ---
    comment_q = select(func.count(UsageEvent.id)).where(
        UsageEvent.tenant_id == tenant_id,
        UsageEvent.event_type.in_([
            "comment_sent", "farm_comment", "smart_comment",
        ]),
    )
    comment_count = (await session.execute(comment_q)).scalar() or 0
    has_comment = comment_count >= 1
    details["ai_comment_sent"] = has_comment
    if has_comment:
        score += 15

    # --- Channel Map used: +10 ---
    channel_map_q = select(func.count(UsageEvent.id)).where(
        UsageEvent.tenant_id == tenant_id,
        UsageEvent.event_type.in_([
            "channel_map_view", "channel_map_search", "channel_map_drill",
        ]),
    )
    channel_map_used = ((await session.execute(channel_map_q)).scalar() or 0) >= 1
    details["channel_map_used"] = channel_map_used
    if channel_map_used:
        score += 10

    # --- Content Factory used: +10 ---
    cf_q = select(func.count(UsageEvent.id)).where(
        UsageEvent.tenant_id == tenant_id,
        UsageEvent.event_type.in_([
            "content_factory", "content_generate",
        ]),
    )
    cf_used = ((await session.execute(cf_q)).scalar() or 0) >= 1
    details["content_factory_used"] = cf_used
    if cf_used:
        score += 10

    # --- 3+ days of activity: +10 ---
    days_q = select(
        func.count(func.distinct(func.date(UsageEvent.created_at)))
    ).where(UsageEvent.tenant_id == tenant_id)
    active_days = (await session.execute(days_q)).scalar() or 0
    has_3_days = active_days >= 3
    details["three_plus_active_days"] = has_3_days
    if has_3_days:
        score += 10

    return min(score, 120), details


# ---------------------------------------------------------------------------
# Category logic
# ---------------------------------------------------------------------------

def get_lead_category(total_score: int) -> str:
    """Map numeric score to human-readable category."""
    if total_score >= 121:
        return "PQL"
    if total_score >= 71:
        return "Hot"
    if total_score >= 31:
        return "Warm"
    return "Cold"


# ---------------------------------------------------------------------------
# Resolve lead -> auth_user -> tenant
# ---------------------------------------------------------------------------

async def _resolve_auth_user(
    session: AsyncSession,
    lead_email: str,
) -> Optional[tuple[int, Optional[int]]]:
    """Try to find auth_user by email. Returns (auth_user_id, tenant_id) or None."""
    if not lead_email:
        return None

    row = (
        await session.execute(
            select(AuthUser.id, AuthUser.id.label("uid"))
            .where(AuthUser.email == lead_email)
        )
    ).first()

    if row is None:
        return None

    auth_user_id = row[0]

    # Find tenant via workspace membership
    ws_row = (
        await session.execute(
            select(Workspace.tenant_id).where(
                Workspace.runtime_user_id == auth_user_id
            ).limit(1)
        )
    ).first()

    tenant_id = ws_row[0] if ws_row else None
    return auth_user_id, tenant_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def score_lead(lead_id: int) -> Optional[LeadScore]:
    """Compute full score for a single lead by ID.

    Returns ``None`` if the lead does not exist.
    """
    async with async_session() as session:
        lead = (
            await session.execute(select(Lead).where(Lead.id == lead_id))
        ).scalar_one_or_none()
        if lead is None:
            return None

        p_score = _profile_score(lead)

        auth_info = await _resolve_auth_user(session, lead.email)
        auth_user_id: Optional[int] = None
        tenant_id: Optional[int] = None
        if auth_info:
            auth_user_id, tenant_id = auth_info

        e_score, e_details = await _engagement_score(session, auth_user_id, tenant_id)

        total = p_score + e_score
        category = get_lead_category(total)

        return LeadScore(
            lead_id=lead.id,
            email=lead.email,
            name=lead.name,
            company=lead.company,
            use_case=lead.use_case,
            utm_source=lead.utm_source,
            telegram_username=lead.telegram_username,
            created_at=lead.created_at,
            auth_user_id=auth_user_id,
            profile_score=p_score,
            engagement_score=e_score,
            total_score=total,
            category=category,
            engagement_details=e_details,
        )


async def get_all_scored_leads() -> List[LeadScore]:
    """Score every lead in the database. Returns list sorted by total_score desc."""
    results: List[LeadScore] = []

    async with async_session() as session:
        leads: Sequence[Any] = (
            await session.execute(
                select(Lead).order_by(Lead.created_at.desc())
            )
        ).scalars().all()

        for lead in leads:
            p_score = _profile_score(lead)

            auth_info = await _resolve_auth_user(session, lead.email)
            auth_user_id: Optional[int] = None
            tenant_id: Optional[int] = None
            if auth_info:
                auth_user_id, tenant_id = auth_info

            e_score, e_details = await _engagement_score(session, auth_user_id, tenant_id)

            total = p_score + e_score
            category = get_lead_category(total)

            results.append(LeadScore(
                lead_id=lead.id,
                email=lead.email,
                name=lead.name,
                company=lead.company,
                use_case=lead.use_case,
                utm_source=lead.utm_source,
                telegram_username=lead.telegram_username,
                created_at=lead.created_at,
                auth_user_id=auth_user_id,
                profile_score=p_score,
                engagement_score=e_score,
                total_score=total,
                category=category,
                engagement_details=e_details,
            ))

    results.sort(key=lambda ls: ls.total_score, reverse=True)
    return results


async def get_pql_leads() -> List[LeadScore]:
    """Return only PQL leads (total_score >= 121)."""
    all_leads = await get_all_scored_leads()
    return [ls for ls in all_leads if ls.category == "PQL"]


def lead_score_to_dict(ls: LeadScore) -> Dict[str, Any]:
    """Serialize a LeadScore to a JSON-safe dict for API responses."""
    return {
        "lead_id": ls.lead_id,
        "email": ls.email,
        "name": ls.name,
        "company": ls.company,
        "use_case": ls.use_case,
        "utm_source": ls.utm_source,
        "telegram_username": ls.telegram_username,
        "created_at": ls.created_at.isoformat() if ls.created_at else None,
        "auth_user_id": ls.auth_user_id,
        "profile_score": ls.profile_score,
        "engagement_score": ls.engagement_score,
        "total_score": ls.total_score,
        "category": ls.category,
        "engagement_details": ls.engagement_details,
    }
