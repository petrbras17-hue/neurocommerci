"""Add account_activity_logs table for detailed per-action tracking.

Revision ID: 20260315_35
Revises: 20260314_42
Create Date: 2026-03-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260315_35"
down_revision = "20260314_42"
branch_labels = None
depends_on = None

_TENANT_SCOPE = (
    "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::integer"
)


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    json_type = postgresql.JSONB(astext_type=sa.Text()) if is_pg else sa.JSON()

    op.create_table(
        "account_activity_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("details", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_account_activity_logs_tenant_id", "account_activity_logs", ["tenant_id"])
    op.create_index("ix_account_activity_logs_account_id", "account_activity_logs", ["account_id"])
    op.create_index("ix_account_activity_logs_action_type", "account_activity_logs", ["action_type"])
    op.create_index("ix_account_activity_logs_created_at", "account_activity_logs", ["created_at"])

    if is_pg:
        op.execute(sa.text("ALTER TABLE account_activity_logs ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text("ALTER TABLE account_activity_logs FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(
            f"CREATE POLICY account_activity_logs_isolation "
            f"ON account_activity_logs "
            f"USING ({_TENANT_SCOPE}) "
            f"WITH CHECK ({_TENANT_SCOPE})"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("DROP POLICY IF EXISTS account_activity_logs_isolation ON account_activity_logs"))
    op.drop_index("ix_account_activity_logs_created_at", table_name="account_activity_logs")
    op.drop_index("ix_account_activity_logs_action_type", table_name="account_activity_logs")
    op.drop_index("ix_account_activity_logs_account_id", table_name="account_activity_logs")
    op.drop_index("ix_account_activity_logs_tenant_id", table_name="account_activity_logs")
    op.drop_table("account_activity_logs")
