"""Add RLS policies for subscriptions and payment_events tables.

Revision ID: 20260311_17
Revises: 20260311_16
"""
from alembic import op
import sqlalchemy as sa

revision = "20260311_17"
down_revision = "20260311_16"
branch_labels = None
depends_on = None


def _enable_tenant_rls(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS {table_name}_isolation ON {table_name}"))
    tenant_scope = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::integer"
    op.execute(
        sa.text(
            f"""
            CREATE POLICY {table_name}_isolation
            ON {table_name}
            USING ({tenant_scope})
            WITH CHECK ({tenant_scope});
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    _enable_tenant_rls("subscriptions")
    _enable_tenant_rls("payment_events")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    op.execute(sa.text("DROP POLICY IF EXISTS subscriptions_isolation ON subscriptions"))
    op.execute(sa.text("ALTER TABLE subscriptions DISABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS payment_events_isolation ON payment_events"))
    op.execute(sa.text("ALTER TABLE payment_events DISABLE ROW LEVEL SECURITY"))
