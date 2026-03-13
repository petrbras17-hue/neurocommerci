"""Add proxy rotation strategy columns and account health history table.

Revision ID: 20260313_29b
Revises: 20260313_29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260313_29b"
down_revision = "20260313_29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add rotation strategy columns to proxies
    op.add_column("proxies", sa.Column("rotation_strategy", sa.String(20), server_default="sticky", nullable=True))
    op.add_column("proxies", sa.Column("auto_rotation", sa.Boolean(), server_default="false", nullable=True))

    # 2. Create account_health_history table
    op.create_table(
        "account_health_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("health_score", sa.Integer(), server_default="100", nullable=True),
        sa.Column("survivability_score", sa.Integer(), server_default="100", nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "account_id", "snapshot_date", name="uq_health_history_tenant_account_date"),
    )
    op.create_index("ix_account_health_history_tenant_id", "account_health_history", ["tenant_id"])
    op.create_index("ix_account_health_history_account_id", "account_health_history", ["account_id"])
    op.create_index("ix_account_health_history_snapshot_date", "account_health_history", ["snapshot_date"])

    # 3. Enable RLS on account_health_history
    op.execute("ALTER TABLE account_health_history ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE account_health_history FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON account_health_history "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::integer) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::integer)"
    )


def downgrade() -> None:
    # Reverse order
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON account_health_history")
    op.drop_index("ix_account_health_history_snapshot_date", table_name="account_health_history")
    op.drop_index("ix_account_health_history_account_id", table_name="account_health_history")
    op.drop_index("ix_account_health_history_tenant_id", table_name="account_health_history")
    op.drop_table("account_health_history")
    op.drop_column("proxies", "auto_rotation")
    op.drop_column("proxies", "rotation_strategy")
