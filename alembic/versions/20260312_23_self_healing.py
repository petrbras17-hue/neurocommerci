"""Sprint 11: Self-Healing & Auto-Purchase tables.

Revision ID: 20260312_22
Revises: 20260312_21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260312_22"
down_revision = "20260312_21"
branch_labels = None
depends_on = None


def _table_names(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(
    inspector: sa.Inspector,
    table_name: str,
    index_name: str,
    columns: list[str],
    unique: bool = False,
) -> None:
    if index_name not in _index_names(inspector, table_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def _enable_tenant_rls(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}"))
    tenant_scope = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"
    op.execute(
        sa.text(
            f"""
            CREATE POLICY {table_name}_tenant_isolation
            ON {table_name}
            USING ({tenant_scope});
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)
    is_postgresql = bind.dialect.name == "postgresql"

    # --- healing_actions ---
    if "healing_actions" not in existing_tables:
        details_col = (
            sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
            if is_postgresql
            else sa.Column("details", sa.JSON(), nullable=True)
        )
        op.create_table(
            "healing_actions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("action_type", sa.String(50), nullable=False),
            sa.Column("target_type", sa.String(20), nullable=False),  # account, proxy, thread
            sa.Column("target_id", sa.Integer(), nullable=True),
            details_col,
            sa.Column("outcome", sa.String(20), nullable=False, server_default="pending"),
            # pending, success, failed
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        existing_tables.add("healing_actions")

    # --- purchase_requests ---
    if "purchase_requests" not in existing_tables:
        details_col2 = (
            sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
            if is_postgresql
            else sa.Column("details", sa.JSON(), nullable=True)
        )
        op.create_table(
            "purchase_requests",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("resource_type", sa.String(20), nullable=False),  # proxy, account
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("provider_name", sa.String(100), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            # pending, approved, rejected, completed, failed
            sa.Column(
                "requested_by",
                sa.Integer(),
                sa.ForeignKey("auth_users.id"),
                nullable=True,
            ),
            sa.Column(
                "approved_by",
                sa.Integer(),
                sa.ForeignKey("auth_users.id"),
                nullable=True,
            ),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
            sa.Column("actual_cost_usd", sa.Float(), nullable=True),
            details_col2,
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )
        existing_tables.add("purchase_requests")

    # --- platform_alerts ---
    if "platform_alerts" not in existing_tables:
        op.create_table(
            "platform_alerts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("alert_type", sa.String(50), nullable=False),
            sa.Column("severity", sa.String(10), nullable=False, server_default="info"),
            # info, warning, critical
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column(
                "is_resolved",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false" if is_postgresql else "0"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
        )
        existing_tables.add("platform_alerts")

    # --- alert_configs ---
    if "alert_configs" not in existing_tables:
        op.create_table(
            "alert_configs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("resource_type", sa.String(20), nullable=False),  # proxy, account
            sa.Column("threshold_percent", sa.Integer(), nullable=False, server_default="10"),
            sa.Column(
                "auto_purchase_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false" if is_postgresql else "0"),
            ),
            sa.Column(
                "notify_telegram",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true" if is_postgresql else "1"),
            ),
            sa.Column(
                "notify_email",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false" if is_postgresql else "0"),
            ),
            sa.UniqueConstraint(
                "tenant_id", "resource_type", name="uq_alert_configs_tenant_resource"
            ),
        )
        existing_tables.add("alert_configs")

    # --- indexes ---
    inspector = sa.inspect(bind)
    for table_name, index_name, columns in (
        ("healing_actions", "ix_healing_actions_tenant_id", ["tenant_id"]),
        ("healing_actions", "ix_healing_actions_action_type", ["action_type"]),
        ("healing_actions", "ix_healing_actions_created_at", ["created_at"]),
        ("purchase_requests", "ix_purchase_requests_tenant_id", ["tenant_id"]),
        ("purchase_requests", "ix_purchase_requests_status", ["status"]),
        ("purchase_requests", "ix_purchase_requests_created_at", ["created_at"]),
        ("platform_alerts", "ix_platform_alerts_tenant_id", ["tenant_id"]),
        ("platform_alerts", "ix_platform_alerts_severity", ["severity"]),
        ("platform_alerts", "ix_platform_alerts_is_resolved", ["is_resolved"]),
        ("alert_configs", "ix_alert_configs_tenant_id", ["tenant_id"]),
    ):
        if table_name in existing_tables:
            _create_index_if_missing(inspector, table_name, index_name, columns)

    # --- RLS (PostgreSQL only) ---
    if is_postgresql:
        for table_name in (
            "healing_actions",
            "purchase_requests",
            "platform_alerts",
            "alert_configs",
        ):
            if table_name in existing_tables:
                _enable_tenant_rls(table_name)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)

    if bind.dialect.name == "postgresql":
        for table_name in (
            "alert_configs",
            "platform_alerts",
            "purchase_requests",
            "healing_actions",
        ):
            if table_name in existing_tables:
                op.execute(
                    sa.text(
                        f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}"
                    )
                )

    for table_name in (
        "alert_configs",
        "platform_alerts",
        "purchase_requests",
        "healing_actions",
    ):
        if table_name in existing_tables:
            op.drop_table(table_name)
