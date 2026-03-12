"""
Sprint 9 — Auto Campaign Creator Service

Takes a ProductBrief and automatically:
- selects matching channels from channel_map_entries
- assigns available accounts
- creates a Campaign entity with all associations
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import (
    Account,
    Campaign,
    CampaignAccount,
    CampaignChannel,
    ChannelMapEntry,
    ProductBrief,
)
from utils.helpers import utcnow

log = logging.getLogger("uvicorn.error")

_MAX_AUTO_CHANNELS = 50
_MAX_AUTO_ACCOUNTS = 10


class AutoCampaignError(Exception):
    pass


async def create_campaign_from_brief(
    session: AsyncSession,
    *,
    brief_id: int,
    tenant_id: int,
    workspace_id: int,
    user_id: int | None,
    campaign_name: str | None = None,
    max_channels: int = _MAX_AUTO_CHANNELS,
    max_accounts: int = _MAX_AUTO_ACCOUNTS,
) -> Campaign:
    """
    Auto-create a Campaign from a ProductBrief.

    - Loads the brief to get keywords and tone.
    - Queries channel_map_entries for keyword matches.
    - Grabs available (active, non-quarantined) accounts.
    - Creates Campaign, CampaignChannel, and CampaignAccount rows.
    - Returns the flushed Campaign ORM object (caller owns transaction).
    """
    brief = await session.get(ProductBrief, brief_id)
    if brief is None or brief.tenant_id != tenant_id:
        raise AutoCampaignError("brief_not_found")

    name = campaign_name or f"Кампания: {brief.product_name or 'Продукт'}"
    keywords: list[str] = list(brief.keywords or [])
    daily_volume: int = int(brief.daily_volume or 30)

    # --- Select matching channels ---
    channel_rows: list[ChannelMapEntry] = []
    if keywords:
        # Search channels whose title, description, or category contains any keyword
        from sqlalchemy import or_

        keyword_filters = []
        for kw in keywords[:10]:  # limit keyword count to avoid query explosion
            kw_lower = f"%{kw.lower()}%"
            keyword_filters.append(ChannelMapEntry.title.ilike(kw_lower))
            keyword_filters.append(ChannelMapEntry.description.ilike(kw_lower))
            keyword_filters.append(ChannelMapEntry.category.ilike(kw_lower))

        q = (
            select(ChannelMapEntry)
            .where(
                or_(*keyword_filters),
                ChannelMapEntry.has_comments == True,  # noqa: E712
                # Tenant-scoped: own channels OR shared catalog (tenant_id IS NULL)
                or_(
                    ChannelMapEntry.tenant_id == tenant_id,
                    ChannelMapEntry.tenant_id.is_(None),
                ),
            )
            .order_by(ChannelMapEntry.member_count.desc())
            .limit(max_channels)
        )
        result = await session.execute(q)
        channel_rows = list(result.scalars().all())

    # --- Select available accounts ---
    q_accounts = (
        select(Account)
        .where(
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
            Account.status == "active",
            Account.health_status.in_(["alive", "unknown"]),
        )
        .order_by(Account.id.asc())
        .limit(max_accounts)
    )
    result_accounts = await session.execute(q_accounts)
    account_rows: list[Account] = list(result_accounts.scalars().all())

    # --- Build comment prompt from brief ---
    comment_prompt = _build_comment_prompt(brief)

    # --- Create Campaign ---
    campaign = Campaign(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        name=name,
        status="draft",
        campaign_type="commenting",
        account_ids=[acc.id for acc in account_rows],
        comment_prompt=comment_prompt,
        comment_tone=str(brief.brand_tone or "native"),
        comment_language="ru",
        schedule_type="continuous",
        budget_daily_actions=daily_volume,
        total_actions_performed=0,
        total_comments_sent=0,
        total_reactions_sent=0,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(campaign)
    await session.flush()

    # --- Create CampaignChannel rows ---
    for ch in channel_rows:
        cc = CampaignChannel(
            tenant_id=tenant_id,
            campaign_id=campaign.id,
            channel_id=ch.id,
            channel_username=ch.username,
            status="active",
            comments_count=0,
            created_at=utcnow(),
        )
        session.add(cc)

    # --- Create CampaignAccount rows ---
    for acc in account_rows:
        ca = CampaignAccount(
            tenant_id=tenant_id,
            campaign_id=campaign.id,
            account_id=acc.id,
            status="active",
            comments_today=0,
            created_at=utcnow(),
        )
        session.add(ca)

    await session.flush()
    return campaign


def _build_comment_prompt(brief: ProductBrief) -> str:
    parts: list[str] = []
    if brief.product_name:
        parts.append(f"Продукт: {brief.product_name}")
    if brief.usp:
        parts.append(f"УТП: {brief.usp}")
    if brief.target_audience:
        parts.append(f"Аудитория: {brief.target_audience}")
    if brief.brand_tone:
        parts.append(f"Тональность: {brief.brand_tone}")
    if parts:
        return "\n".join(parts)
    return "Пишите естественные, нативные комментарии релевантные теме поста."
