# Channel Intelligence Engine Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Channel Intelligence Engine that profiles channels, tracks join requests, learns from bans, and drives AI-powered autonomous account behavior via OpenRouter 100+ models.

**Architecture:** Three-layer module (ChannelProfiler + JoinRequestTracker + BanPatternLearner) backed by PostgreSQL with RLS + Redis cache. Integrates with existing FarmThread state machine and ai_router. New AI task types registered for channel rules extraction, ban analysis, and autonomous life decisions.

**Tech Stack:** Python 3.9+, SQLAlchemy, Alembic, Redis, Telethon, OpenRouter API, existing ai_router.py

---

## File Structure

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `core/channel_intelligence.py` | ChannelProfiler, JoinRequestTracker, BanPatternLearner classes |
| Create | `tests/test_channel_intelligence.py` | Unit tests for all three components |
| Create | `alembic/versions/20260312_19_channel_intelligence.py` | Migration: 3 new tables + RLS |
| Modify | `storage/models.py` | Add ChannelProfile, ChannelBanEvent, ChannelJoinRequest ORM models |
| Modify | `core/ai_router.py` | Add new task policies + fallback chains |
| Modify | `core/farm_thread.py` | Integrate channel_intel.get_rules() + ban recording |
| Modify | `core/liveliness_agent.py` | Upgrade to AI-driven action selection |
| Modify | `config.py` | Add CI_* env vars |

---

## Chunk 1: ORM Models + Migration

### Task 1: Add ORM Models to storage/models.py

**Files:**
- Modify: `storage/models.py` (after FarmEvent class, ~line 901)
- Test: `tests/test_channel_intelligence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_channel_intelligence.py
"""Tests for Channel Intelligence Engine."""

import pytest
from storage.models import ChannelProfile, ChannelBanEvent, ChannelJoinRequest


def test_channel_profile_model_exists():
    """ChannelProfile ORM model has expected columns."""
    assert ChannelProfile.__tablename__ == "channel_profiles"
    cols = {c.name for c in ChannelProfile.__table__.columns}
    assert "tenant_id" in cols
    assert "telegram_id" in cols
    assert "channel_type" in cols
    assert "is_private" in cols
    assert "slow_mode_seconds" in cols
    assert "ai_extracted_rules" in cols
    assert "learned_rules" in cols
    assert "ban_risk" in cols
    assert "success_rate" in cols


def test_channel_ban_event_model_exists():
    """ChannelBanEvent ORM model has expected columns."""
    assert ChannelBanEvent.__tablename__ == "channel_ban_events"
    cols = {c.name for c in ChannelBanEvent.__table__.columns}
    assert "tenant_id" in cols
    assert "channel_profile_id" in cols
    assert "account_id" in cols
    assert "ban_type" in cols
    assert "ai_analysis" in cols


def test_channel_join_request_model_exists():
    """ChannelJoinRequest ORM model has expected columns."""
    assert ChannelJoinRequest.__tablename__ == "channel_join_requests"
    cols = {c.name for c in ChannelJoinRequest.__table__.columns}
    assert "tenant_id" in cols
    assert "account_id" in cols
    assert "telegram_id" in cols
    assert "status" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_channel_intelligence.py -v`
Expected: FAIL — ImportError: cannot import name 'ChannelProfile'

- [ ] **Step 3: Write minimal implementation — add models to storage/models.py**

Add after the `FarmEvent` class (around line 901):

```python
class ChannelProfile(Base):
    """Intelligence profile for a channel — rules, risk, learned behavior."""
    __tablename__ = "channel_profiles"
    __table_args__ = (
        Index("ix_channel_profiles_tenant_id", "tenant_id"),
        Index("ix_channel_profiles_telegram_id", "telegram_id"),
        UniqueConstraint("tenant_id", "telegram_id", name="uq_channel_profiles_tenant_telegram"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    channel_entry_id = Column(Integer, ForeignKey("channel_entries.id"), nullable=True)
    telegram_id = Column(BigInteger, nullable=False)
    username = Column(String(100), nullable=True)
    title = Column(String(300), nullable=True)
    channel_type = Column(String(20), default="channel")  # channel, supergroup, megagroup, chat
    is_private = Column(Boolean, default=False)
    slow_mode_seconds = Column(Integer, default=0)
    no_links = Column(Boolean, default=False)
    no_forwards = Column(Boolean, default=False)
    linked_chat_id = Column(BigInteger, nullable=True)
    pinned_rules_text = Column(Text, nullable=True)
    ai_extracted_rules = Column(JSONType, nullable=True)
    learned_rules = Column(JSONType, nullable=True)
    ban_risk = Column(String(10), default="low")  # low, medium, high, critical
    success_rate = Column(Float, default=1.0)
    total_comments = Column(Integer, default=0)
    total_bans = Column(Integer, default=0)
    safe_comment_interval_sec = Column(Integer, default=0)
    last_profiled_at = Column(DateTime, nullable=True)
    last_ban_analysis_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ChannelBanEvent(Base):
    """Log of ban/mute events for collective learning."""
    __tablename__ = "channel_ban_events"
    __table_args__ = (
        Index("ix_channel_ban_events_tenant_id", "tenant_id"),
        Index("ix_channel_ban_events_channel_profile_id", "channel_profile_id"),
        Index("ix_channel_ban_events_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    channel_profile_id = Column(Integer, ForeignKey("channel_profiles.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    ban_type = Column(String(20), nullable=False)  # mute, kicked, banned, restricted, slow_mode_hit, flood_severe
    last_action_before_ban = Column(JSONType, nullable=True)
    ai_analysis = Column(JSONType, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class ChannelJoinRequest(Base):
    """Tracks pending join requests to private channels/groups."""
    __tablename__ = "channel_join_requests"
    __table_args__ = (
        Index("ix_channel_join_requests_tenant_id", "tenant_id"),
        Index("ix_channel_join_requests_status", "status"),
        Index("ix_channel_join_requests_account_id", "account_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    channel_profile_id = Column(Integer, ForeignKey("channel_profiles.id"), nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    telegram_id = Column(BigInteger, nullable=False)
    status = Column(String(10), default="pending")  # pending, accepted, rejected, expired
    requested_at = Column(DateTime, default=utcnow)
    resolved_at = Column(DateTime, nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_channel_intelligence.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add storage/models.py tests/test_channel_intelligence.py
git commit -m "feat: add ChannelProfile, ChannelBanEvent, ChannelJoinRequest ORM models"
```

### Task 2: Create Alembic Migration

**Files:**
- Create: `alembic/versions/20260312_19_channel_intelligence.py`

- [ ] **Step 1: Create migration file**

```python
"""Channel Intelligence Engine tables.

Revision ID: 20260312_19
Revises: 20260311_18
Create Date: 2026-03-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260312_19"
down_revision = "20260311_18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- channel_profiles ---
    op.create_table(
        "channel_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("channel_entry_id", sa.Integer(), sa.ForeignKey("channel_entries.id"), nullable=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(100), nullable=True),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("channel_type", sa.String(20), server_default="channel"),
        sa.Column("is_private", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("slow_mode_seconds", sa.Integer(), server_default=sa.text("0")),
        sa.Column("no_links", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("no_forwards", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("linked_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("pinned_rules_text", sa.Text(), nullable=True),
        sa.Column("ai_extracted_rules", JSONB(), nullable=True),
        sa.Column("learned_rules", JSONB(), nullable=True),
        sa.Column("ban_risk", sa.String(10), server_default="low"),
        sa.Column("success_rate", sa.Float(), server_default=sa.text("1.0")),
        sa.Column("total_comments", sa.Integer(), server_default=sa.text("0")),
        sa.Column("total_bans", sa.Integer(), server_default=sa.text("0")),
        sa.Column("safe_comment_interval_sec", sa.Integer(), server_default=sa.text("0")),
        sa.Column("last_profiled_at", sa.DateTime(), nullable=True),
        sa.Column("last_ban_analysis_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "telegram_id", name="uq_channel_profiles_tenant_telegram"),
    )
    op.create_index("ix_channel_profiles_tenant_id", "channel_profiles", ["tenant_id"])
    op.create_index("ix_channel_profiles_telegram_id", "channel_profiles", ["telegram_id"])

    # --- channel_ban_events ---
    op.create_table(
        "channel_ban_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("channel_profile_id", sa.Integer(), sa.ForeignKey("channel_profiles.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("ban_type", sa.String(20), nullable=False),
        sa.Column("last_action_before_ban", JSONB(), nullable=True),
        sa.Column("ai_analysis", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_channel_ban_events_tenant_id", "channel_ban_events", ["tenant_id"])
    op.create_index("ix_channel_ban_events_channel_profile_id", "channel_ban_events", ["channel_profile_id"])
    op.create_index("ix_channel_ban_events_created_at", "channel_ban_events", ["created_at"])

    # --- channel_join_requests ---
    op.create_table(
        "channel_join_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("channel_profile_id", sa.Integer(), sa.ForeignKey("channel_profiles.id"), nullable=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(10), server_default="pending"),
        sa.Column("requested_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_channel_join_requests_tenant_id", "channel_join_requests", ["tenant_id"])
    op.create_index("ix_channel_join_requests_status", "channel_join_requests", ["status"])
    op.create_index("ix_channel_join_requests_account_id", "channel_join_requests", ["account_id"])

    # --- RLS policies ---
    for table in ("channel_profiles", "channel_ban_events", "channel_join_requests"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::int)"
        )


def downgrade() -> None:
    for table in ("channel_join_requests", "channel_ban_events", "channel_profiles"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
```

- [ ] **Step 2: Verify migration compiles**

Run: `.venv/bin/python -c "import alembic.versions.20260312_19_channel_intelligence"`
Expected: No import errors (or just verify file syntax)

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/20260312_19_channel_intelligence.py
git commit -m "feat: add channel_intelligence migration with 3 tables + RLS"
```

---

## Chunk 2: Config + AI Router Task Policies

### Task 3: Add Config Vars

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Add CI_* env vars to config.py Settings class**

Add after the `DIGEST_MAX_PER_MINUTE` block:

```python
    # -- Channel Intelligence --
    CI_PROFILE_REFRESH_HOURS: int = Field(default=24)
    CI_JOIN_POLL_INTERVAL_SEC: int = Field(default=300)
    CI_BAN_RISK_CRITICAL_THRESHOLD: float = Field(default=50.0)
    CI_REDIS_CACHE_TTL_SEC: int = Field(default=3600)
```

- [ ] **Step 2: Commit**

```bash
git add config.py
git commit -m "feat: add Channel Intelligence config vars"
```

### Task 4: Register New AI Task Policies in ai_router.py

**Files:**
- Modify: `core/ai_router.py` (DEFAULT_TASK_POLICIES dict, ~line 127)

- [ ] **Step 1: Add new task policies**

Add to `DEFAULT_TASK_POLICIES` dict after the `"behavior_pattern_optimization"` entry (~line 222):

```python
    # --- Channel Intelligence ---
    "channel_rules_extraction": TaskPolicy(
        task_type="channel_rules_extraction",
        agent_name="Channel Intelligence Agent",
        requested_model_tier=TIER_WORKER,
        output_contract_type="json_object",
    ),
    "ban_analysis": TaskPolicy(
        task_type="ban_analysis",
        agent_name="Channel Intelligence Agent",
        requested_model_tier=TIER_WORKER,
        output_contract_type="json_object",
    ),
    "comment_adaptation": TaskPolicy(
        task_type="comment_adaptation",
        agent_name="Channel Intelligence Agent",
        requested_model_tier=TIER_WORKER,
        output_contract_type="json_object",
    ),
    "ban_pattern_global": TaskPolicy(
        task_type="ban_pattern_global",
        agent_name="Channel Intelligence Agent",
        requested_model_tier=TIER_BOSS,
        output_contract_type="json_object",
        approval_required=True,
    ),
    # --- Autonomous Life (AI-driven) ---
    "life_next_action": TaskPolicy(
        task_type="life_next_action",
        agent_name="Liveliness Agent",
        requested_model_tier=TIER_WORKER,
        output_contract_type="json_object",
    ),
    "life_story_reaction": TaskPolicy(
        task_type="life_story_reaction",
        agent_name="Liveliness Agent",
        requested_model_tier=TIER_WORKER,
        output_contract_type="json_object",
    ),
    "life_search_query": TaskPolicy(
        task_type="life_search_query",
        agent_name="Liveliness Agent",
        requested_model_tier=TIER_WORKER,
        output_contract_type="json_object",
    ),
    "life_channel_reading": TaskPolicy(
        task_type="life_channel_reading",
        agent_name="Liveliness Agent",
        requested_model_tier=TIER_WORKER,
        output_contract_type="json_object",
    ),
    "strategy_optimization": TaskPolicy(
        task_type="strategy_optimization",
        agent_name="Strategy Agent",
        requested_model_tier=TIER_BOSS,
        output_contract_type="json_object",
        approval_required=True,
    ),
```

- [ ] **Step 2: Run existing ai_router tests**

Run: `.venv/bin/python -m pytest tests/test_ai_router.py -v`
Expected: 6 passed (no regression)

- [ ] **Step 3: Commit**

```bash
git add core/ai_router.py
git commit -m "feat: register channel_intelligence + liveliness AI task policies"
```

---

## Chunk 3: Core Channel Intelligence Module

### Task 5: ChannelProfiler — Tests

**Files:**
- Test: `tests/test_channel_intelligence.py`

- [ ] **Step 1: Write unit tests for ChannelProfiler**

Append to `tests/test_channel_intelligence.py`:

```python
from core.channel_intelligence import (
    ChannelProfiler,
    JoinRequestTracker,
    BanPatternLearner,
    compute_ban_risk,
    _RULES_EXTRACTION_PROMPT,
    _BAN_ANALYSIS_PROMPT,
)


def test_compute_ban_risk_low():
    assert compute_ban_risk(success_rate=0.95, total_bans=1, total_comments=100) == "low"


def test_compute_ban_risk_medium():
    assert compute_ban_risk(success_rate=0.80, total_bans=5, total_comments=25) == "medium"


def test_compute_ban_risk_high():
    assert compute_ban_risk(success_rate=0.60, total_bans=10, total_comments=25) == "high"


def test_compute_ban_risk_critical():
    assert compute_ban_risk(success_rate=0.30, total_bans=20, total_comments=30) == "critical"


def test_compute_ban_risk_no_data():
    """No comments yet should return low (benefit of the doubt)."""
    assert compute_ban_risk(success_rate=1.0, total_bans=0, total_comments=0) == "low"


def test_rules_extraction_prompt_exists():
    assert "JSON" in _RULES_EXTRACTION_PROMPT
    assert "max_messages_per_hour" in _RULES_EXTRACTION_PROMPT


def test_ban_analysis_prompt_exists():
    assert "JSON" in _BAN_ANALYSIS_PROMPT
    assert "cause" in _BAN_ANALYSIS_PROMPT


def test_channel_profiler_class_exists():
    profiler = ChannelProfiler(redis_client=None, ai_router_func=None)
    assert profiler is not None


def test_join_request_tracker_class_exists():
    tracker = JoinRequestTracker(redis_client=None)
    assert tracker is not None


def test_ban_pattern_learner_class_exists():
    learner = BanPatternLearner(redis_client=None, ai_router_func=None)
    assert learner is not None
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv/bin/python -m pytest tests/test_channel_intelligence.py -v`
Expected: 3 pass (model tests), 10 fail (ImportError for channel_intelligence module)

### Task 6: ChannelProfiler + BanPatternLearner + JoinRequestTracker — Implementation

**Files:**
- Create: `core/channel_intelligence.py`

- [ ] **Step 3: Write core/channel_intelligence.py**

```python
"""
Channel Intelligence Engine — profiles channels, tracks join requests,
learns from bans, and provides rules to FarmThread.

Three classes:
- ChannelProfiler: API + AI profiling of channels
- JoinRequestTracker: real-time events + polling for private channel joins
- BanPatternLearner: AI analysis of bans → learned rules
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

import redis.asyncio as aioredis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from storage.models import (
    ChannelProfile,
    ChannelBanEvent,
    ChannelJoinRequest,
    ChannelEntry,
    FarmEvent,
)
from storage.sqlite_db import async_session, apply_session_rls_context
from utils.helpers import utcnow

log = logging.getLogger(__name__)

# Redis key patterns
_PROFILE_CACHE_KEY = "ci:{tenant_id}:{telegram_id}"
_PENDING_JOINS_KEY = "ci:joins:{tenant_id}:{account_id}"
_LAST_COMMENT_KEY = "ci:last_comment:{tenant_id}:{channel_id}:{account_id}"

# ---------------------------------------------------------------------------
# AI Prompts
# ---------------------------------------------------------------------------

_RULES_EXTRACTION_PROMPT = """Extract channel rules from the pinned message below.
Return valid JSON with these fields:
{
  "max_messages_per_hour": <int or null>,
  "topics_allowed": [<list of strings or empty>],
  "topics_banned": [<list of strings or empty>],
  "links_policy": "allowed" | "forbidden" | "with_approval",
  "language": "ru" | "en" | "any",
  "custom_rules": [<list of extracted rule strings>]
}
If no rules found, return: {}

Pinned message:
---
{pinned_text}
---"""

_BAN_ANALYSIS_PROMPT = """An account was banned/muted in a Telegram channel. Analyze why.

Channel: {title} (@{username})
Channel rules: {rules}
Ban type: {ban_type}
Last 5 actions before ban:
{actions}

Return valid JSON:
{{
  "cause": "<one-sentence explanation>",
  "new_rules": ["<rules to add to channel profile>"],
  "safe_interval_sec": <recommended seconds between comments>,
  "risk_adjustment": "increase" | "decrease" | "unchanged"
}}"""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def compute_ban_risk(
    success_rate: float,
    total_bans: int,
    total_comments: int,
) -> str:
    """Compute ban risk level from success metrics."""
    if total_comments == 0:
        return "low"
    if success_rate > 0.90:
        return "low"
    if success_rate > 0.70:
        return "medium"
    if success_rate >= settings.CI_BAN_RISK_CRITICAL_THRESHOLD / 100:
        return "high"
    return "critical"


def _cache_key(tenant_id: int, telegram_id: int) -> str:
    return _PROFILE_CACHE_KEY.format(tenant_id=tenant_id, telegram_id=telegram_id)


# ---------------------------------------------------------------------------
# ChannelProfiler
# ---------------------------------------------------------------------------

class ChannelProfiler:
    """Profiles channels via Telethon API + AI rules extraction."""

    def __init__(
        self,
        redis_client: Optional[aioredis.Redis],
        ai_router_func: Optional[Callable],
    ) -> None:
        self.redis = redis_client
        self.route_ai_task = ai_router_func

    async def profile_channel(
        self,
        client: Any,
        channel_entity: Any,
        tenant_id: int,
        channel_entry_id: Optional[int] = None,
    ) -> ChannelProfile:
        """Full-profile a channel: API metadata + AI rules from pinned message.

        `client` is a connected Telethon TelegramClient.
        `channel_entity` is a resolved Telethon Channel/Chat entity.
        """
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.types import (
            InputMessagesFilterPinned,
            ChannelParticipantsAdmins,
        )

        telegram_id = channel_entity.id
        username = getattr(channel_entity, "username", None) or ""
        title = getattr(channel_entity, "title", None) or ""

        # 1. Get full channel info
        full = await client(GetFullChannelRequest(channel=channel_entity))
        full_chat = full.full_chat

        slow_mode = getattr(full_chat, "slowmode_seconds", 0) or 0
        linked_chat_id = getattr(full_chat, "linked_chat_id", None)
        is_private = not getattr(channel_entity, "username", None)

        # Determine type
        if getattr(channel_entity, "megagroup", False):
            channel_type = "megagroup"
        elif getattr(channel_entity, "gigagroup", False):
            channel_type = "supergroup"
        elif getattr(channel_entity, "broadcast", False):
            channel_type = "channel"
        else:
            channel_type = "chat"

        # Check banned_rights
        default_rights = getattr(full_chat, "default_banned_rights", None)
        no_links = False
        no_forwards = getattr(channel_entity, "noforwards", False)
        if default_rights:
            no_links = getattr(default_rights, "embed_links", False)

        # 2. Get pinned message
        pinned_text = None
        ai_rules = None
        try:
            pinned_msgs = await client.get_messages(
                channel_entity, limit=1, filter=InputMessagesFilterPinned
            )
            if pinned_msgs and pinned_msgs[0]:
                pinned_text = pinned_msgs[0].text or ""
        except Exception as exc:
            log.debug("profile_channel: no pinned message for %s: %s", telegram_id, exc)

        # 3. AI extraction from pinned rules
        if pinned_text and len(pinned_text) > 20 and self.route_ai_task:
            try:
                prompt = _RULES_EXTRACTION_PROMPT.format(pinned_text=pinned_text[:2000])
                result = await self.route_ai_task(
                    task_type="channel_rules_extraction",
                    tenant_id=tenant_id,
                    payload={"prompt": prompt},
                )
                if result and result.ok and result.parsed:
                    ai_rules = result.parsed
            except Exception as exc:
                log.warning("profile_channel: AI rules extraction failed: %s", exc)

        # 4. Upsert to DB
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                existing = await session.execute(
                    select(ChannelProfile).where(
                        ChannelProfile.tenant_id == tenant_id,
                        ChannelProfile.telegram_id == telegram_id,
                    )
                )
                profile = existing.scalar_one_or_none()

                if profile is None:
                    profile = ChannelProfile(
                        tenant_id=tenant_id,
                        channel_entry_id=channel_entry_id,
                        telegram_id=telegram_id,
                        username=username,
                        title=title,
                        channel_type=channel_type,
                        is_private=is_private,
                        slow_mode_seconds=slow_mode,
                        no_links=no_links,
                        no_forwards=no_forwards,
                        linked_chat_id=linked_chat_id,
                        pinned_rules_text=pinned_text,
                        ai_extracted_rules=ai_rules,
                        last_profiled_at=utcnow(),
                    )
                    session.add(profile)
                else:
                    profile.channel_type = channel_type
                    profile.is_private = is_private
                    profile.slow_mode_seconds = slow_mode
                    profile.no_links = no_links
                    profile.no_forwards = no_forwards
                    profile.linked_chat_id = linked_chat_id
                    profile.pinned_rules_text = pinned_text
                    if ai_rules:
                        profile.ai_extracted_rules = ai_rules
                    profile.last_profiled_at = utcnow()

                await session.flush()
                profile_dict = self._to_cache_dict(profile)

        # 5. Cache in Redis
        if self.redis:
            try:
                key = _cache_key(tenant_id, telegram_id)
                await self.redis.setex(
                    key,
                    settings.CI_REDIS_CACHE_TTL_SEC,
                    json.dumps(profile_dict, default=str),
                )
            except Exception:
                pass

        log.info(
            "channel_profiler: profiled %s (@%s) type=%s private=%s slow=%s",
            telegram_id, username, channel_type, is_private, slow_mode,
        )
        return profile

    async def get_rules(self, tenant_id: int, telegram_id: int) -> dict:
        """Get cached channel rules. Falls back to DB if not in Redis."""
        # Try Redis first
        if self.redis:
            try:
                key = _cache_key(tenant_id, telegram_id)
                cached = await self.redis.get(key)
                if cached:
                    return json.loads(cached)
            except Exception:
                pass

        # Fallback to DB
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(ChannelProfile).where(
                        ChannelProfile.tenant_id == tenant_id,
                        ChannelProfile.telegram_id == telegram_id,
                    )
                )
                profile = result.scalar_one_or_none()
                if profile:
                    d = self._to_cache_dict(profile)
                    # Re-cache
                    if self.redis:
                        try:
                            await self.redis.setex(
                                _cache_key(tenant_id, telegram_id),
                                settings.CI_REDIS_CACHE_TTL_SEC,
                                json.dumps(d, default=str),
                            )
                        except Exception:
                            pass
                    return d

        return {}

    @staticmethod
    def _to_cache_dict(p: ChannelProfile) -> dict:
        return {
            "telegram_id": p.telegram_id,
            "channel_type": p.channel_type,
            "is_private": p.is_private,
            "slow_mode_seconds": p.slow_mode_seconds,
            "no_links": p.no_links,
            "no_forwards": p.no_forwards,
            "linked_chat_id": p.linked_chat_id,
            "ai_extracted_rules": p.ai_extracted_rules or {},
            "learned_rules": p.learned_rules or {},
            "ban_risk": p.ban_risk,
            "success_rate": p.success_rate,
            "safe_comment_interval_sec": p.safe_comment_interval_sec,
        }


# ---------------------------------------------------------------------------
# JoinRequestTracker
# ---------------------------------------------------------------------------

class JoinRequestTracker:
    """Tracks pending join requests to private channels."""

    def __init__(self, redis_client: Optional[aioredis.Redis]) -> None:
        self.redis = redis_client

    async def create_request(
        self,
        tenant_id: int,
        account_id: int,
        telegram_id: int,
        channel_profile_id: Optional[int] = None,
    ) -> ChannelJoinRequest:
        """Record a new pending join request."""
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                req = ChannelJoinRequest(
                    tenant_id=tenant_id,
                    channel_profile_id=channel_profile_id,
                    account_id=account_id,
                    telegram_id=telegram_id,
                    status="pending",
                    requested_at=utcnow(),
                )
                session.add(req)
                await session.flush()

        # Track in Redis for fast lookup
        if self.redis:
            try:
                key = _PENDING_JOINS_KEY.format(tenant_id=tenant_id, account_id=account_id)
                await self.redis.sadd(key, str(telegram_id))
            except Exception:
                pass

        log.info("join_tracker: created request account=%s channel=%s", account_id, telegram_id)
        return req

    async def mark_accepted(
        self,
        tenant_id: int,
        account_id: int,
        telegram_id: int,
    ) -> None:
        """Mark a join request as accepted."""
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                await session.execute(
                    update(ChannelJoinRequest)
                    .where(
                        ChannelJoinRequest.tenant_id == tenant_id,
                        ChannelJoinRequest.account_id == account_id,
                        ChannelJoinRequest.telegram_id == telegram_id,
                        ChannelJoinRequest.status == "pending",
                    )
                    .values(status="accepted", resolved_at=utcnow())
                )

        if self.redis:
            try:
                key = _PENDING_JOINS_KEY.format(tenant_id=tenant_id, account_id=account_id)
                await self.redis.srem(key, str(telegram_id))
            except Exception:
                pass

        log.info("join_tracker: accepted account=%s channel=%s", account_id, telegram_id)

    async def mark_rejected(
        self,
        tenant_id: int,
        account_id: int,
        telegram_id: int,
    ) -> None:
        """Mark a join request as rejected."""
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                await session.execute(
                    update(ChannelJoinRequest)
                    .where(
                        ChannelJoinRequest.tenant_id == tenant_id,
                        ChannelJoinRequest.account_id == account_id,
                        ChannelJoinRequest.telegram_id == telegram_id,
                        ChannelJoinRequest.status == "pending",
                    )
                    .values(status="rejected", resolved_at=utcnow())
                )

        if self.redis:
            try:
                key = _PENDING_JOINS_KEY.format(tenant_id=tenant_id, account_id=account_id)
                await self.redis.srem(key, str(telegram_id))
            except Exception:
                pass

    async def get_pending(
        self,
        tenant_id: int,
        account_id: int,
    ) -> list[ChannelJoinRequest]:
        """Get all pending join requests for an account."""
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(ChannelJoinRequest).where(
                        ChannelJoinRequest.tenant_id == tenant_id,
                        ChannelJoinRequest.account_id == account_id,
                        ChannelJoinRequest.status == "pending",
                    )
                )
                return list(result.scalars().all())

    async def expire_old_requests(self, tenant_id: int, max_age_days: int = 7) -> int:
        """Expire join requests older than max_age_days."""
        from datetime import timedelta
        cutoff = utcnow() - timedelta(days=max_age_days)
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    update(ChannelJoinRequest)
                    .where(
                        ChannelJoinRequest.tenant_id == tenant_id,
                        ChannelJoinRequest.status == "pending",
                        ChannelJoinRequest.requested_at < cutoff,
                    )
                    .values(status="expired", resolved_at=utcnow())
                )
                return result.rowcount


# ---------------------------------------------------------------------------
# BanPatternLearner
# ---------------------------------------------------------------------------

class BanPatternLearner:
    """Analyzes bans via AI and updates channel profiles with learned rules."""

    def __init__(
        self,
        redis_client: Optional[aioredis.Redis],
        ai_router_func: Optional[Callable],
    ) -> None:
        self.redis = redis_client
        self.route_ai_task = ai_router_func

    async def record_and_analyze(
        self,
        tenant_id: int,
        channel_telegram_id: int,
        account_id: int,
        ban_type: str,
        last_actions: list[dict] | None = None,
    ) -> Optional[dict]:
        """Record a ban event and optionally run AI analysis.

        Returns AI analysis dict if available, None otherwise.
        """
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)

                # Find or create channel profile
                result = await session.execute(
                    select(ChannelProfile).where(
                        ChannelProfile.tenant_id == tenant_id,
                        ChannelProfile.telegram_id == channel_telegram_id,
                    )
                )
                profile = result.scalar_one_or_none()

                profile_id = profile.id if profile else None

                # Record ban event
                event = ChannelBanEvent(
                    tenant_id=tenant_id,
                    channel_profile_id=profile_id or 0,
                    account_id=account_id,
                    ban_type=ban_type,
                    last_action_before_ban=last_actions,
                )
                session.add(event)

                # Update profile stats
                if profile:
                    profile.total_bans = (profile.total_bans or 0) + 1
                    total = (profile.total_comments or 0)
                    bans = profile.total_bans
                    if total > 0:
                        profile.success_rate = max(0.0, (total - bans) / total)
                    profile.ban_risk = compute_ban_risk(
                        profile.success_rate, bans, total
                    )

                await session.flush()

        # AI analysis (fire and forget if no ai_router)
        ai_result = None
        if self.route_ai_task and profile:
            try:
                actions_text = json.dumps(last_actions or [], default=str, ensure_ascii=False)
                rules_text = json.dumps(
                    profile.ai_extracted_rules or {}, default=str, ensure_ascii=False
                )
                prompt = _BAN_ANALYSIS_PROMPT.format(
                    title=profile.title or "Unknown",
                    username=profile.username or "",
                    rules=rules_text,
                    ban_type=ban_type,
                    actions=actions_text[:2000],
                )
                result = await self.route_ai_task(
                    task_type="ban_analysis",
                    tenant_id=tenant_id,
                    payload={"prompt": prompt},
                )
                if result and result.ok and result.parsed:
                    ai_result = result.parsed
                    await self._apply_ai_analysis(tenant_id, channel_telegram_id, ai_result)
            except Exception as exc:
                log.warning("ban_learner: AI analysis failed: %s", exc)

        # Invalidate Redis cache
        if self.redis:
            try:
                await self.redis.delete(_cache_key(tenant_id, channel_telegram_id))
            except Exception:
                pass

        log.info(
            "ban_learner: recorded ban type=%s channel=%s account=%s risk=%s",
            ban_type, channel_telegram_id, account_id,
            profile.ban_risk if profile else "unknown",
        )
        return ai_result

    async def _apply_ai_analysis(
        self,
        tenant_id: int,
        telegram_id: int,
        analysis: dict,
    ) -> None:
        """Merge AI analysis results into channel profile."""
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await session.execute(
                    select(ChannelProfile).where(
                        ChannelProfile.tenant_id == tenant_id,
                        ChannelProfile.telegram_id == telegram_id,
                    )
                )
                profile = result.scalar_one_or_none()
                if not profile:
                    return

                # Merge new rules
                new_rules = analysis.get("new_rules", [])
                existing = profile.learned_rules or {}
                existing_list = existing.get("rules", [])
                for rule in new_rules:
                    if rule not in existing_list:
                        existing_list.append(rule)
                existing["rules"] = existing_list
                profile.learned_rules = existing

                # Update safe interval
                suggested = analysis.get("safe_interval_sec")
                if suggested and isinstance(suggested, (int, float)):
                    profile.safe_comment_interval_sec = max(
                        profile.safe_comment_interval_sec or 0,
                        int(suggested),
                    )

                # Update ban analysis timestamp
                profile.last_ban_analysis_at = utcnow()

                # Store AI analysis on the event too
                await session.execute(
                    update(ChannelBanEvent)
                    .where(
                        ChannelBanEvent.tenant_id == tenant_id,
                        ChannelBanEvent.channel_profile_id == profile.id,
                    )
                    .order_by(ChannelBanEvent.created_at.desc())
                    .limit(1)
                    .values(ai_analysis=analysis)
                )

                await session.flush()
```

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/python -m pytest tests/test_channel_intelligence.py -v`
Expected: 13 passed

- [ ] **Step 5: Run full test suite to check no regressions**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All existing tests pass

- [ ] **Step 6: Commit**

```bash
git add core/channel_intelligence.py tests/test_channel_intelligence.py
git commit -m "feat: implement Channel Intelligence Engine (profiler, join tracker, ban learner)"
```

---

## Chunk 4: FarmThread Integration

### Task 7: Integrate Channel Intelligence into FarmThread

**Files:**
- Modify: `core/farm_thread.py`

- [ ] **Step 1: Add channel_intel parameter to FarmThread.__init__**

In `core/farm_thread.py`, add to `__init__` parameters (after `publish_event_func`):

```python
        channel_intel: Any = None,  # ChannelProfiler instance
```

And store it:
```python
        self.channel_intel = channel_intel
```

- [ ] **Step 2: Add should_comment method**

Add after the `stats` property:

```python
    async def should_comment(self, channel_id: int) -> tuple[bool, str]:
        """Check channel rules before commenting. Returns (allowed, reason)."""
        if not self.channel_intel:
            return True, "ok"

        rules = await self.channel_intel.get_rules(self.tenant_id, channel_id)
        if not rules:
            return True, "no_profile"

        # Skip critical-risk channels
        if rules.get("ban_risk") == "critical":
            return False, "channel_too_risky"

        # Respect safe comment interval
        interval = rules.get("safe_comment_interval_sec", 0)
        if interval > 0 and self.redis:
            key = f"ci:last_comment:{self.tenant_id}:{channel_id}:{self.account_id}"
            last = await self.redis.get(key)
            if last:
                import time
                elapsed = time.time() - float(last)
                if elapsed < interval:
                    return False, "too_soon"

        return True, "ok"
```

- [ ] **Step 3: Add record_comment_success and record_ban methods**

```python
    async def _record_comment_success(self, channel_id: int) -> None:
        """Record successful comment timestamp for rate limiting."""
        if self.redis:
            import time
            key = f"ci:last_comment:{self.tenant_id}:{channel_id}:{self.account_id}"
            await self.redis.setex(key, 3600, str(time.time()))

    async def _record_ban(self, channel_id: int, ban_type: str) -> None:
        """Record a ban event via BanPatternLearner."""
        if not self.channel_intel:
            return
        try:
            from core.channel_intelligence import BanPatternLearner
            # Get learner from channel_intel if it has one, or create inline
            learner = getattr(self.channel_intel, '_ban_learner', None)
            if learner:
                await learner.record_and_analyze(
                    tenant_id=self.tenant_id,
                    channel_telegram_id=channel_id,
                    account_id=self.account_id,
                    ban_type=ban_type,
                )
        except Exception as exc:
            log.debug("farm_thread: ban record failed: %s", exc)
```

- [ ] **Step 4: Integrate into the main commenting loop**

In the `run()` method, inside the post processing loop, wrap the comment with should_comment check. Find the section around line 207-217 and modify:

Before `comment_text = await self.generate_comment(...)`:
```python
                    # Check channel intelligence rules
                    channel_id = post.get("channel_id") or 0
                    allowed, reason = await self.should_comment(channel_id)
                    if not allowed:
                        log.info("Thread %s: skipping comment, reason=%s", self.thread_id, reason)
                        continue
```

After successful `post_comment()`, add:
```python
                            await self._record_comment_success(channel_id)
```

In the exception handlers for `UserBannedInChannelError` / `ChatWriteForbiddenError` (currently grouped under generic Exception), add:
```python
                        await self._record_ban(channel_id, "banned")
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add core/farm_thread.py
git commit -m "feat: integrate Channel Intelligence into FarmThread (should_comment, ban recording)"
```

---

## Chunk 5: AI-Driven Liveliness Upgrade

### Task 8: Upgrade LifelinessAgent with AI-driven action selection

**Files:**
- Modify: `core/liveliness_agent.py`

- [ ] **Step 1: Add AI action selection to AccountLifeLoop**

In `AccountLifeLoop`, add a method that replaces the fixed-weight activity selection with an AI call when available:

```python
    async def _pick_next_action_ai(self) -> tuple[str, dict]:
        """Use AI to pick the next action. Falls back to weighted random."""
        if not self._route_ai_task:
            return self._pick_next_action_weighted()

        try:
            import random
            prompt = (
                f"You are simulating a real Telegram user. "
                f"Time: {_now_str(self._tz)}. "
                f"Recent actions: {self._recent_actions[-3:]}. "
                f"Mood: {random.choice(['active', 'lazy', 'curious', 'bored'])}. "
                f"Pick ONE action from: read_channel, view_stories, search, idle. "
                f"Return JSON: {{\"action\": \"...\", \"duration_min\": N}}"
            )
            result = await self._route_ai_task(
                task_type="life_next_action",
                tenant_id=self._tenant_id,
                payload={"prompt": prompt},
            )
            if result and result.ok and result.parsed:
                action = result.parsed.get("action", "read_channel")
                duration = result.parsed.get("duration_min", 3)
                return action, {"duration_min": duration}
        except Exception as exc:
            log.debug("life_loop: AI action pick failed, using weighted: %s", exc)

        return self._pick_next_action_weighted()

    def _pick_next_action_weighted(self) -> tuple[str, dict]:
        """Original weighted random selection as fallback."""
        import random
        roll = random.random()
        if roll < 0.40:
            return "read_channel", {}
        elif roll < 0.65:
            return "view_stories", {}
        elif roll < 0.85:
            return "search", {}
        else:
            return "idle", {}
```

- [ ] **Step 2: Wire AI selection into the main loop**

Replace the existing activity selection call in the loop body with `_pick_next_action_ai()`.

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_liveliness_agent.py tests/test_channel_intelligence.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add core/liveliness_agent.py
git commit -m "feat: upgrade LifelinessAgent with AI-driven action selection via OpenRouter"
```

---

## Chunk 6: Final Integration + Full Test Run

### Task 9: Compile check all new/modified files

- [ ] **Step 1: Compile check**

```bash
.venv/bin/python -c "
import core.channel_intelligence
import core.farm_thread
import core.liveliness_agent
import core.ai_router
import storage.models
print('All modules compile OK')
"
```

- [ ] **Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All tests pass, no regressions

- [ ] **Step 3: Final commit with all remaining changes**

```bash
git add -A
git commit -m "feat: Channel Intelligence Engine — complete implementation"
```

---

## Summary of Deliverables

| # | What | File |
|---|------|------|
| 1 | 3 new ORM models (ChannelProfile, ChannelBanEvent, ChannelJoinRequest) | storage/models.py |
| 2 | Alembic migration with RLS | alembic/versions/20260312_19_* |
| 3 | Channel Intelligence module (3 classes) | core/channel_intelligence.py |
| 4 | Unit tests (13+ tests) | tests/test_channel_intelligence.py |
| 5 | 9 new AI task policies | core/ai_router.py |
| 6 | FarmThread integration (should_comment, ban recording) | core/farm_thread.py |
| 7 | AI-driven liveliness | core/liveliness_agent.py |
| 8 | Config vars | config.py |
