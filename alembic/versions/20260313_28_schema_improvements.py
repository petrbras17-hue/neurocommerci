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


def _safe_add_column(table: str, col_name: str, col_sql: str) -> None:
    """Add column only if it doesn't already exist."""
    op.execute(
        f"DO $$ BEGIN "
        f"ALTER TABLE {table} ADD COLUMN {col_name} {col_sql}; "
        f"EXCEPTION WHEN duplicate_column THEN NULL; "
        f"END $$"
    )


def _safe_alter_type(table: str, column: str, new_type: str) -> None:
    """Alter column type only if the column exists."""
    op.execute(
        f"DO $$ BEGIN "
        f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type} USING {column}::{new_type}; "
        f"EXCEPTION WHEN undefined_column THEN NULL; "
        f"END $$"
    )


def upgrade() -> None:
    # 1. Add missing created_at columns (idempotent)
    _safe_add_column("campaign_runs", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _safe_add_column("account_health_scores", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _safe_add_column("alert_configs", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _safe_add_column("alert_configs", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    # 2. Convert Float money columns to Numeric for precision (skip if column missing)
    _safe_alter_type("ai_model_profiles", "input_cost_per_1m", "NUMERIC(10,6)")
    _safe_alter_type("ai_model_profiles", "output_cost_per_1m", "NUMERIC(10,6)")
    _safe_alter_type("ai_requests", "estimated_cost_usd", "NUMERIC(12,6)")
    _safe_alter_type("ai_requests", "actual_cost_usd", "NUMERIC(12,6)")
    _safe_alter_type("ai_request_attempts", "cost_usd", "NUMERIC(12,6)")
    _safe_alter_type("ai_budget_counters", "total_cost_usd", "NUMERIC(12,6)")

    # 3. Add server_default for analytics_daily_cache
    op.execute(
        "DO $$ BEGIN "
        "ALTER TABLE analytics_daily_cache ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP; "
        "EXCEPTION WHEN undefined_column THEN NULL; "
        "END $$"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "ALTER TABLE analytics_daily_cache ALTER COLUMN created_at DROP DEFAULT; "
        "EXCEPTION WHEN undefined_column THEN NULL; "
        "END $$"
    )

    _safe_alter_type("ai_budget_counters", "total_cost_usd", "DOUBLE PRECISION")
    _safe_alter_type("ai_request_attempts", "cost_usd", "DOUBLE PRECISION")
    _safe_alter_type("ai_requests", "actual_cost_usd", "DOUBLE PRECISION")
    _safe_alter_type("ai_requests", "estimated_cost_usd", "DOUBLE PRECISION")
    _safe_alter_type("ai_model_profiles", "output_cost_per_1m", "DOUBLE PRECISION")
    _safe_alter_type("ai_model_profiles", "input_cost_per_1m", "DOUBLE PRECISION")

    for col in ["updated_at", "created_at"]:
        op.execute(
            f"DO $$ BEGIN "
            f"ALTER TABLE alert_configs DROP COLUMN {col}; "
            f"EXCEPTION WHEN undefined_column THEN NULL; "
            f"END $$"
        )
    for table in ["account_health_scores", "campaign_runs"]:
        op.execute(
            f"DO $$ BEGIN "
            f"ALTER TABLE {table} DROP COLUMN created_at; "
            f"EXCEPTION WHEN undefined_column THEN NULL; "
            f"END $$"
        )
