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
    # 1. Add rotation strategy columns to proxies (idempotent)
    op.execute(
        "DO $$ BEGIN "
        "ALTER TABLE proxies ADD COLUMN rotation_strategy VARCHAR(20) DEFAULT 'sticky'; "
        "EXCEPTION WHEN duplicate_column THEN NULL; "
        "END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "ALTER TABLE proxies ADD COLUMN auto_rotation BOOLEAN DEFAULT false; "
        "EXCEPTION WHEN duplicate_column THEN NULL; "
        "END $$"
    )

    # 2. Create account_health_history table (idempotent — may exist from prior run)
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS account_health_history (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id),
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            snapshot_date DATE NOT NULL,
            health_score INTEGER DEFAULT 100,
            survivability_score INTEGER DEFAULT 100,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_health_history_tenant_account_date UNIQUE (tenant_id, account_id, snapshot_date)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_account_health_history_tenant_id ON account_health_history (tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_account_health_history_account_id ON account_health_history (account_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_account_health_history_snapshot_date ON account_health_history (snapshot_date)")

    # 3. Enable RLS on account_health_history
    op.execute("ALTER TABLE account_health_history ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE account_health_history FORCE ROW LEVEL SECURITY")
    op.execute(
        "DO $$ BEGIN "
        "CREATE POLICY tenant_isolation ON account_health_history "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::integer) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::integer); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
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
