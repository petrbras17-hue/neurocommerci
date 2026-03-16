"""Composite indexes for performance optimization across heavy query paths.

Covers:
  1. channel_map_entries  — category/language/member_count filter + lat/lng spatial + tenant timeline + GIN topic_tags
  2. account_activity_logs — per-account timeline + tenant/action-type timeline
  3. farm_events           — per-farm timeline + account/action_type lookup
  4. ai_requests           — tenant/task cost aggregation
  5. app_jobs              — partial index on queued status for worker dequeue
  6. accounts              — tenant/status farm polling + lifecycle/health polling

Revision ID: 20260316_44
Revises: 20260316_43
Create Date: 2026-03-16
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260316_44"
down_revision = "20260316_43"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ── 1. channel_map_entries ────────────────────────────────────────────────

    # Category + language + member_count DESC — used by Discovery panel filters
    op.create_index(
        "ix_cme_cat_lang_members",
        "channel_map_entries",
        ["category", "language", sa.text("member_count DESC")],
        if_not_exists=True,
    )

    # Lat/lng spatial lookup — used by viewport cluster queries
    op.create_index(
        "ix_cme_lat_lng",
        "channel_map_entries",
        ["lat", "lng"],
        if_not_exists=True,
    )

    # Tenant-scoped timeline — used by admin and tenant list endpoints
    op.create_index(
        "ix_cme_tenant_created",
        "channel_map_entries",
        ["tenant_id", sa.text("created_at DESC")],
        if_not_exists=True,
    )

    # GIN index on JSONB topic_tags — used by micro-topic classification queries
    if is_pg:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_cme_topic_tags "
            "ON channel_map_entries USING GIN(topic_tags)"
        )

    # ── 2. account_activity_logs ──────────────────────────────────────────────

    # Per-account descending timeline — used by account detail / timeline endpoint
    op.create_index(
        "ix_aal_account_created",
        "account_activity_logs",
        ["account_id", sa.text("created_at DESC")],
        if_not_exists=True,
    )

    # Tenant + action_type descending — used by ops dashboard action-type filters
    op.create_index(
        "ix_aal_tenant_type_created",
        "account_activity_logs",
        ["tenant_id", "action_type", sa.text("created_at DESC")],
        if_not_exists=True,
    )

    # ── 3. farm_events ────────────────────────────────────────────────────────

    # Per-farm descending timeline — used by /v1/farm/{id}/events
    op.create_index(
        "ix_fe_farm_created",
        "farm_events",
        ["farm_id", sa.text("created_at DESC")],
        if_not_exists=True,
    )

    # Account + action_type lookup — used by per-thread event counts
    op.create_index(
        "ix_fe_account_action",
        "farm_events",
        ["account_id", "action_type"],
        if_not_exists=True,
    )

    # ── 4. ai_requests ────────────────────────────────────────────────────────

    # Tenant + task_type + created_at — used by cost aggregation and quality summary
    op.create_index(
        "ix_air_tenant_task_created",
        "ai_requests",
        ["tenant_id", "task_type", sa.text("created_at DESC")],
        if_not_exists=True,
    )

    # ── 5. app_jobs — partial index on queued rows only ───────────────────────

    if is_pg:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_aj_queued "
            "ON app_jobs(status, created_at) WHERE status = 'queued'"
        )
    else:
        # SQLite fallback (dev/test only) — no partial index support
        op.create_index(
            "ix_aj_status_created",
            "app_jobs",
            ["status", "created_at"],
            if_not_exists=True,
        )

    # ── 6. accounts ───────────────────────────────────────────────────────────

    # Tenant + status — used by farm polling to enumerate active accounts
    op.create_index(
        "ix_acc_tenant_status",
        "accounts",
        ["tenant_id", "status"],
        if_not_exists=True,
    )

    # Lifecycle stage + health status — used by self-healing and warmup scheduler
    op.create_index(
        "ix_acc_lifecycle",
        "accounts",
        ["lifecycle_stage", "health_status"],
        if_not_exists=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # accounts
    op.drop_index("ix_acc_lifecycle", table_name="accounts", if_exists=True)
    op.drop_index("ix_acc_tenant_status", table_name="accounts", if_exists=True)

    # app_jobs
    if is_pg:
        op.execute("DROP INDEX IF EXISTS ix_aj_queued")
    else:
        op.drop_index("ix_aj_status_created", table_name="app_jobs", if_exists=True)

    # ai_requests
    op.drop_index("ix_air_tenant_task_created", table_name="ai_requests", if_exists=True)

    # farm_events
    op.drop_index("ix_fe_account_action", table_name="farm_events", if_exists=True)
    op.drop_index("ix_fe_farm_created", table_name="farm_events", if_exists=True)

    # account_activity_logs
    op.drop_index("ix_aal_tenant_type_created", table_name="account_activity_logs", if_exists=True)
    op.drop_index("ix_aal_account_created", table_name="account_activity_logs", if_exists=True)

    # channel_map_entries
    if is_pg:
        op.execute("DROP INDEX IF EXISTS ix_cme_topic_tags")
    op.drop_index("ix_cme_tenant_created", table_name="channel_map_entries", if_exists=True)
    op.drop_index("ix_cme_lat_lng", table_name="channel_map_entries", if_exists=True)
    op.drop_index("ix_cme_cat_lang_members", table_name="channel_map_entries", if_exists=True)
