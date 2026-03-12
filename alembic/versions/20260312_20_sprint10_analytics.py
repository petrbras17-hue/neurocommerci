"""Sprint 10 — analytics daily cache + weekly reports tables with RLS.

Revision ID: 20260312_23
Revises: 20260312_22
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260312_23"
down_revision = "20260312_22"
branch_labels = None
depends_on = None


def _table_names(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = _table_names(inspector)

    # -----------------------------------------------------------------------
    # analytics_daily_cache
    # -----------------------------------------------------------------------
    if "analytics_daily_cache" not in existing:
        op.create_table(
            "analytics_daily_cache",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("date", sa.String(10), nullable=False),
            sa.Column("metric_type", sa.String(50), nullable=False),
            sa.Column("value", sa.Float, default=0.0),
            sa.Column("metadata", postgresql.JSONB, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("updated_at", sa.DateTime, nullable=True),
            sa.UniqueConstraint("tenant_id", "date", "metric_type", name="uq_analytics_daily_cache_scope"),
        )
        op.create_index("ix_analytics_daily_cache_tenant_id", "analytics_daily_cache", ["tenant_id"])
        op.create_index("ix_analytics_daily_cache_date", "analytics_daily_cache", ["date"])

        op.execute(sa.text("ALTER TABLE analytics_daily_cache ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text("ALTER TABLE analytics_daily_cache FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(
            "CREATE POLICY analytics_daily_cache_tenant_isolation "
            "ON analytics_daily_cache "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::int)"
        ))

    # -----------------------------------------------------------------------
    # weekly_reports
    # -----------------------------------------------------------------------
    if "weekly_reports" not in existing:
        op.create_table(
            "weekly_reports",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("week_start", sa.String(10), nullable=False),
            sa.Column("week_end", sa.String(10), nullable=False),
            sa.Column("report_text", sa.Text, nullable=True),
            sa.Column("metrics_snapshot", postgresql.JSONB, nullable=True),
            sa.Column("generated_at", sa.DateTime, nullable=True),
            sa.Column("sent_at", sa.DateTime, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=True),
        )
        op.create_index("ix_weekly_reports_tenant_id", "weekly_reports", ["tenant_id"])
        op.create_index("ix_weekly_reports_week_start", "weekly_reports", ["week_start"])

        op.execute(sa.text("ALTER TABLE weekly_reports ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text("ALTER TABLE weekly_reports FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(
            "CREATE POLICY weekly_reports_tenant_isolation "
            "ON weekly_reports "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::int)"
        ))


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS weekly_reports_tenant_isolation ON weekly_reports"))
    op.execute(sa.text("DROP POLICY IF EXISTS analytics_daily_cache_tenant_isolation ON analytics_daily_cache"))

    op.drop_table("weekly_reports")
    op.drop_table("analytics_daily_cache")
