"""Security fix: add FORCE ROW LEVEL SECURITY to all tenant-scoped tables.

Without FORCE, the table owner role bypasses RLS policies entirely.
This migration ensures all 51 tenant-scoped tables have FORCE enabled.

Also fixes RLS policy setting name on channel_profiles, channel_ban_events,
channel_join_requests (was app.current_tenant_id, should be app.tenant_id).

Revision ID: 20260313_24
Revises: 20260312_23
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260313_24"
down_revision = "20260312_23"
branch_labels = None
depends_on = None

# All tenant-scoped tables that need FORCE ROW LEVEL SECURITY
_TABLES_NEEDING_FORCE = [
    # 20260310_06 — operator shell + assistant
    "business_briefs",
    "business_assets",
    "assistant_threads",
    "assistant_messages",
    "assistant_recommendations",
    "creative_drafts",
    "manual_actions",
    # 20260310_07 — AI orchestrator
    "ai_task_policies",
    "ai_requests",
    "ai_request_attempts",
    "ai_budget_limits",
    "ai_budget_counters",
    "ai_escalations",
    "ai_agent_runs",
    # 20260310_08 — jobs + quality
    "app_jobs",
    # 20260311_09 — farm orchestrator
    "farm_configs",
    "farm_threads",
    "channel_databases",
    "channel_entries",
    "parsing_jobs",
    "profile_templates",
    "farm_events",
    # 20260311_10 — warmup + health
    "warmup_configs",
    "warmup_sessions",
    "account_health_scores",
    # 20260311_11 — advanced modules
    "reaction_jobs",
    "chatting_configs",
    "dialog_configs",
    "user_parsing_results",
    "telegram_folders",
    # 20260311_12 — intelligence scale
    "channel_map_entries",
    "campaigns",
    "campaign_runs",
    "analytics_events",
    # 20260311_13 — RLS hardening
    "refresh_tokens",
    "accounts",
    "proxies",
    # 20260311_17 — billing RLS
    "subscriptions",
    "payment_events",
    # 20260312_20 — campaigns onboarding
    "product_briefs",
    "campaign_channels",
    "campaign_accounts",
    # 20260312_20 — comment quality
    "comment_style_templates",
    "comment_ab_results",
]

# Tables whose RLS policy references wrong setting name
_TABLES_WRONG_SETTING = [
    "channel_profiles",
    "channel_ban_events",
    "channel_join_requests",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    # 1. Add FORCE ROW LEVEL SECURITY to all tables missing it
    for table in _TABLES_NEEDING_FORCE:
        if table in existing:
            op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

    # 2. Fix RLS policy setting name on channel intelligence tables
    correct_scope = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"
    for table in _TABLES_WRONG_SETTING:
        if table in existing:
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
            op.execute(sa.text(
                f"CREATE POLICY {table}_tenant_isolation ON {table} "
                f"USING ({correct_scope})"
            ))


def downgrade() -> None:
    # Downgrade is intentionally a no-op: removing FORCE is never desired
    pass
