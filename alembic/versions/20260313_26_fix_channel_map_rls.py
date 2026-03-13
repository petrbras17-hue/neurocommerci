"""Fix channel_map_entries RLS policy to allow platform-catalog rows (tenant_id IS NULL).

Revision ID: 20260313_26
Revises: 20260313_25
"""
from __future__ import annotations

from alembic import op

revision = "20260313_26"
down_revision = "20260313_25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS channel_map_entries_isolation ON channel_map_entries")
    op.execute(
        """
        CREATE POLICY channel_map_entries_isolation ON channel_map_entries
        USING (
            (current_setting('app.bootstrap', true) = '1')
            OR (tenant_id IS NULL)
            OR (tenant_id = (NULLIF(current_setting('app.tenant_id', true), ''))::integer)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS channel_map_entries_isolation ON channel_map_entries")
    op.execute(
        """
        CREATE POLICY channel_map_entries_isolation ON channel_map_entries
        USING (
            (current_setting('app.bootstrap', true) = '1')
            OR (tenant_id = (NULLIF(current_setting('app.tenant_id', true), ''))::integer)
        )
        """
    )
