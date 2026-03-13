"""Security hardening: RLS WITH CHECK, missing indexes, unique constraints, FORCE RLS gaps.

Revision ID: 20260313_27
Revises: 20260313_26
"""
from __future__ import annotations

from alembic import op

revision = "20260313_27"
down_revision = "20260313_26"
branch_labels = None
depends_on = None

# Tables that need FORCE RLS but were missed in migration _24
_FORCE_RLS_GAPS = [
    "healing_actions",
    "purchase_requests",
    "platform_alerts",
    "alert_configs",
    "analytics_daily_cache",
    "weekly_reports",
    "channel_profiles",
    "channel_ban_events",
    "channel_join_requests",
    "usage_events",
]

# RLS policies that have USING but no WITH CHECK — need to be re-created
# Format: (table, policy_name, using_clause)
_POLICIES_NEEDING_WITH_CHECK = [
    (
        "healing_actions",
        "healing_actions_isolation",
        "(current_setting('app.bootstrap', true) = '1') OR (tenant_id = (NULLIF(current_setting('app.tenant_id', true), ''))::integer)",
    ),
    (
        "purchase_requests",
        "purchase_requests_isolation",
        "(current_setting('app.bootstrap', true) = '1') OR (tenant_id = (NULLIF(current_setting('app.tenant_id', true), ''))::integer)",
    ),
    (
        "platform_alerts",
        "platform_alerts_isolation",
        "(current_setting('app.bootstrap', true) = '1') OR (tenant_id = (NULLIF(current_setting('app.tenant_id', true), ''))::integer)",
    ),
    (
        "alert_configs",
        "alert_configs_isolation",
        "(current_setting('app.bootstrap', true) = '1') OR (tenant_id = (NULLIF(current_setting('app.tenant_id', true), ''))::integer)",
    ),
    (
        "channel_profiles",
        "channel_profiles_isolation",
        "(current_setting('app.bootstrap', true) = '1') OR (tenant_id = (NULLIF(current_setting('app.tenant_id', true), ''))::integer)",
    ),
    (
        "channel_ban_events",
        "channel_ban_events_isolation",
        "(current_setting('app.bootstrap', true) = '1') OR (tenant_id = (NULLIF(current_setting('app.tenant_id', true), ''))::integer)",
    ),
    (
        "channel_join_requests",
        "channel_join_requests_isolation",
        "(current_setting('app.bootstrap', true) = '1') OR (tenant_id = (NULLIF(current_setting('app.tenant_id', true), ''))::integer)",
    ),
]

# Missing indexes for performance
_MISSING_INDEXES = [
    ("ix_posts_channel_id", "posts", ["channel_id"]),
    ("ix_comments_account_id", "comments", ["account_id"]),
    ("ix_comments_post_id", "comments", ["post_id"]),
    ("ix_policy_events_account_id", "policy_events", ["account_id"]),
    ("ix_policy_events_created_at", "policy_events", ["created_at"]),
    ("ix_account_stage_events_account_id", "account_stage_events", ["account_id"]),
    ("ix_account_onboarding_runs_account_id", "account_onboarding_runs", ["account_id"]),
]

# Missing unique constraints
_MISSING_UNIQUES = [
    ("uq_channel_entries_db_telegram", "channel_entries", ["database_id", "telegram_id"]),
    ("uq_campaign_channels_campaign_channel", "campaign_channels", ["campaign_id", "channel_id"]),
    ("uq_campaign_accounts_campaign_account", "campaign_accounts", ["campaign_id", "account_id"]),
    ("uq_weekly_reports_tenant_week", "weekly_reports", ["tenant_id", "week_start"]),
]


def upgrade() -> None:
    # 1. Apply FORCE RLS to tables missed in migration _24
    for table in _FORCE_RLS_GAPS:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # 2. Re-create RLS policies WITH CHECK clause
    for table, policy, clause in _POLICIES_NEEDING_WITH_CHECK:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} "
            f"USING ({clause}) "
            f"WITH CHECK ({clause})"
        )

    # 3. Add missing indexes (idempotent)
    for idx_name, table, columns in _MISSING_INDEXES:
        cols = ", ".join(columns)
        op.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({cols})")

    # 4. Add missing unique constraints (with IF NOT EXISTS via try/except at SQL level)
    for uq_name, table, columns in _MISSING_UNIQUES:
        cols = ", ".join(columns)
        # Use DO block to skip if constraint already exists
        op.execute(
            f"DO $$ BEGIN "
            f"ALTER TABLE {table} ADD CONSTRAINT {uq_name} UNIQUE ({cols}); "
            f"EXCEPTION WHEN duplicate_table THEN NULL; "
            f"WHEN duplicate_object THEN NULL; "
            f"END $$"
        )


def downgrade() -> None:
    # Remove unique constraints
    for uq_name, table, _ in reversed(_MISSING_UNIQUES):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {uq_name}")

    # Remove indexes
    for idx_name, table, _ in reversed(_MISSING_INDEXES):
        op.drop_index(idx_name, table_name=table)

    # Revert policies to USING-only (remove WITH CHECK)
    for table, policy, clause in _POLICIES_NEEDING_WITH_CHECK:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"CREATE POLICY {policy} ON {table} USING ({clause})")

    # Remove FORCE RLS from gap tables
    for table in _FORCE_RLS_GAPS:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
