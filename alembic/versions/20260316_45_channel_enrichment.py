"""Channel enrichment columns and subscriber_snapshots table.

Adds:
  1. 7 enrichment columns to channel_map_entries:
       avatar_url, last_post_content, last_post_date, last_post_views,
       last_post_media_type, growth_rate_7d, growth_rate_30d
  2. New table subscriber_snapshots (daily audience snapshots per channel).
  3. FORCE RLS on subscriber_snapshots — row access is governed via the
     parent channel_map_entries.tenant_id join (the table itself has no
     tenant_id column, so the policy uses a sub-select).
  4. Indexes on new columns and on subscriber_snapshots.

Revision ID: 20260316_45
Revises: 20260316_44
Create Date: 2026-03-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260316_45"
down_revision = "20260316_44"
branch_labels = None
depends_on = None

# Tenant isolation expression used on tables that carry tenant_id directly.
_TENANT_SCOPE = (
    "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::integer"
)

# Tenant isolation for subscriber_snapshots — no direct tenant_id column,
# access is gated through the parent channel_map_entries row.
_SS_TENANT_SCOPE = (
    "channel_id IN ("
    "  SELECT id FROM channel_map_entries"
    "  WHERE tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::integer"
    "     OR tenant_id IS NULL"
    ")"
)


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ── 1. Enrichment columns on channel_map_entries ──────────────────────────

    op.add_column(
        "channel_map_entries",
        sa.Column("avatar_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "channel_map_entries",
        sa.Column("last_post_content", sa.Text(), nullable=True),
    )
    op.add_column(
        "channel_map_entries",
        sa.Column("last_post_date", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "channel_map_entries",
        sa.Column("last_post_views", sa.Integer(), nullable=True),
    )
    op.add_column(
        "channel_map_entries",
        sa.Column("last_post_media_type", sa.String(50), nullable=True),
    )
    op.add_column(
        "channel_map_entries",
        sa.Column("growth_rate_7d", sa.Float(), nullable=True),
    )
    op.add_column(
        "channel_map_entries",
        sa.Column("growth_rate_30d", sa.Float(), nullable=True),
    )

    # Index on last_post_date for timeline queries
    op.create_index(
        "ix_cme_last_post_date",
        "channel_map_entries",
        ["last_post_date"],
        if_not_exists=True,
    )

    # Index on growth_rate_7d for "fastest growing" sort
    op.create_index(
        "ix_cme_growth_7d",
        "channel_map_entries",
        ["growth_rate_7d"],
        if_not_exists=True,
    )

    # ── 2. Create subscriber_snapshots ────────────────────────────────────────

    op.create_table(
        "subscriber_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "channel_id",
            sa.Integer(),
            sa.ForeignKey("channel_map_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("subscriber_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_post_reach", sa.Integer(), nullable=True),
        sa.Column("engagement_rate", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Unique constraint: one snapshot per channel per day
    op.create_unique_constraint(
        "uq_subscriber_snapshot_channel_date",
        "subscriber_snapshots",
        ["channel_id", "date"],
    )

    # Composite index for time-series queries per channel
    op.create_index(
        "ix_ss_channel_date",
        "subscriber_snapshots",
        ["channel_id", "date"],
    )

    # Index on date for cross-channel daily aggregations
    op.create_index(
        "ix_ss_date",
        "subscriber_snapshots",
        ["date"],
        if_not_exists=True,
    )

    # ── 3. RLS on subscriber_snapshots (PostgreSQL only) ──────────────────────

    if is_pg:
        op.execute(sa.text("ALTER TABLE subscriber_snapshots ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text("ALTER TABLE subscriber_snapshots FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(
            f"CREATE POLICY subscriber_snapshots_isolation "
            f"ON subscriber_snapshots "
            f"USING ({_SS_TENANT_SCOPE}) "
            f"WITH CHECK ({_SS_TENANT_SCOPE})"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ── Drop subscriber_snapshots ─────────────────────────────────────────────

    if is_pg:
        op.execute(sa.text("DROP POLICY IF EXISTS subscriber_snapshots_isolation ON subscriber_snapshots"))

    op.drop_index("ix_ss_date", table_name="subscriber_snapshots", if_exists=True)
    op.drop_index("ix_ss_channel_date", table_name="subscriber_snapshots", if_exists=True)
    op.drop_constraint(
        "uq_subscriber_snapshot_channel_date",
        "subscriber_snapshots",
        type_="unique",
    )
    op.drop_table("subscriber_snapshots")

    # ── Drop enrichment columns from channel_map_entries ─────────────────────

    op.drop_index("ix_cme_growth_7d", table_name="channel_map_entries", if_exists=True)
    op.drop_index("ix_cme_last_post_date", table_name="channel_map_entries", if_exists=True)

    op.drop_column("channel_map_entries", "growth_rate_30d")
    op.drop_column("channel_map_entries", "growth_rate_7d")
    op.drop_column("channel_map_entries", "last_post_media_type")
    op.drop_column("channel_map_entries", "last_post_views")
    op.drop_column("channel_map_entries", "last_post_date")
    op.drop_column("channel_map_entries", "last_post_content")
    op.drop_column("channel_map_entries", "avatar_url")
