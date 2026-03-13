"""Schema improvements: audit columns, Numeric for money, missing FKs.

Revision ID: 20260313_28
Revises: 20260313_27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260313_28"
down_revision = "20260313_27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add missing created_at columns
    op.add_column("campaign_runs", sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True))
    op.add_column("account_health_scores", sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True))
    op.add_column("alert_configs", sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True))
    op.add_column("alert_configs", sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True))

    # 2. Convert Float money columns to Numeric for precision
    # ai_model_profiles
    op.alter_column("ai_model_profiles", "input_cost_per_1m", type_=sa.Numeric(precision=10, scale=6), existing_type=sa.Float())
    op.alter_column("ai_model_profiles", "output_cost_per_1m", type_=sa.Numeric(precision=10, scale=6), existing_type=sa.Float())
    # ai_requests
    op.alter_column("ai_requests", "estimated_cost_usd", type_=sa.Numeric(precision=12, scale=6), existing_type=sa.Float())
    op.alter_column("ai_requests", "actual_cost_usd", type_=sa.Numeric(precision=12, scale=6), existing_type=sa.Float())
    # ai_request_attempts
    op.alter_column("ai_request_attempts", "cost_usd", type_=sa.Numeric(precision=12, scale=6), existing_type=sa.Float())
    # ai_budget_counters
    op.alter_column("ai_budget_counters", "total_cost_usd", type_=sa.Numeric(precision=12, scale=6), existing_type=sa.Float())

    # 3. Add server_default for analytics_daily_cache
    op.alter_column("analytics_daily_cache", "created_at", server_default=sa.text("CURRENT_TIMESTAMP"))


def downgrade() -> None:
    op.alter_column("analytics_daily_cache", "created_at", server_default=None)

    op.alter_column("ai_budget_counters", "total_cost_usd", type_=sa.Float(), existing_type=sa.Numeric(precision=12, scale=6))
    op.alter_column("ai_request_attempts", "cost_usd", type_=sa.Float(), existing_type=sa.Numeric(precision=12, scale=6))
    op.alter_column("ai_requests", "actual_cost_usd", type_=sa.Float(), existing_type=sa.Numeric(precision=12, scale=6))
    op.alter_column("ai_requests", "estimated_cost_usd", type_=sa.Float(), existing_type=sa.Numeric(precision=12, scale=6))
    op.alter_column("ai_model_profiles", "output_cost_per_1m", type_=sa.Float(), existing_type=sa.Numeric(precision=10, scale=6))
    op.alter_column("ai_model_profiles", "input_cost_per_1m", type_=sa.Float(), existing_type=sa.Numeric(precision=10, scale=6))

    op.drop_column("alert_configs", "updated_at")
    op.drop_column("alert_configs", "created_at")
    op.drop_column("account_health_scores", "created_at")
    op.drop_column("campaign_runs", "created_at")
