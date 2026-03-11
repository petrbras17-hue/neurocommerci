"""Warmup configs, warmup sessions, and account health scores

Revision ID: 20260311_10
Revises: 20260311_09
Create Date: 2026-03-11 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260311_10"
down_revision = "20260311_09"
branch_labels = None
depends_on = None


def _table_names(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(
    inspector: sa.Inspector, table_name: str, index_name: str, columns: list[str]
) -> None:
    if index_name not in _index_names(inspector, table_name):
        op.create_index(index_name, table_name, columns)


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
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)

    if "warmup_configs" not in existing_tables:
        op.create_table(
            "warmup_configs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="stopped"),
            sa.Column("mode", sa.String(length=20), nullable=False, server_default="conservative"),
            sa.Column("safety_limit_actions_per_hour", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("active_hours_start", sa.Integer(), nullable=False, server_default="9"),
            sa.Column("active_hours_end", sa.Integer(), nullable=False, server_default="23"),
            sa.Column("warmup_duration_minutes", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("interval_between_sessions_hours", sa.Integer(), nullable=False, server_default="6"),
            sa.Column("enable_reactions", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("enable_read_channels", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column(
                "enable_dialogs_between_accounts",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column("target_channels", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        existing_tables.add("warmup_configs")
        inspector = sa.inspect(bind)

    if "warmup_sessions" not in existing_tables:
        op.create_table(
            "warmup_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("warmup_id", sa.Integer(), sa.ForeignKey("warmup_configs.id"), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("actions_performed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("next_session_at", sa.DateTime(), nullable=True),
        )
        existing_tables.add("warmup_sessions")
        inspector = sa.inspect(bind)

    if "account_health_scores" not in existing_tables:
        op.create_table(
            "account_health_scores",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("health_score", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("survivability_score", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("flood_wait_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("spam_block_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("successful_actions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("hours_without_error", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("profile_completeness", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("account_age_days", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_calculated_at", sa.DateTime(), nullable=True),
            sa.Column("factors", sa.JSON(), nullable=True),
            sa.UniqueConstraint(
                "tenant_id", "account_id", name="uq_account_health_scores_tenant_account"
            ),
        )
        existing_tables.add("account_health_scores")
        inspector = sa.inspect(bind)

    for table_name, index_name, columns in (
        ("warmup_configs", "ix_warmup_configs_tenant_id", ["tenant_id"]),
        ("warmup_configs", "ix_warmup_configs_workspace_id", ["workspace_id"]),
        ("warmup_configs", "ix_warmup_configs_status", ["status"]),
        ("warmup_sessions", "ix_warmup_sessions_tenant_id", ["tenant_id"]),
        ("warmup_sessions", "ix_warmup_sessions_warmup_id", ["warmup_id"]),
        ("warmup_sessions", "ix_warmup_sessions_account_id", ["account_id"]),
        ("warmup_sessions", "ix_warmup_sessions_status", ["status"]),
        ("account_health_scores", "ix_account_health_scores_tenant_id", ["tenant_id"]),
        ("account_health_scores", "ix_account_health_scores_account_id", ["account_id"]),
    ):
        if table_name in existing_tables:
            _create_index_if_missing(sa.inspect(bind), table_name, index_name, columns)

    if bind.dialect.name == "postgresql":
        for table_name in (
            "warmup_configs",
            "warmup_sessions",
            "account_health_scores",
        ):
            if table_name in existing_tables:
                _enable_tenant_rls(table_name)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)

    if bind.dialect.name == "postgresql":
        for table_name in (
            "account_health_scores",
            "warmup_sessions",
            "warmup_configs",
        ):
            if table_name in existing_tables:
                op.execute(
                    sa.text(f"DROP POLICY IF EXISTS {table_name}_isolation ON {table_name}")
                )

    for table_name in (
        "account_health_scores",
        "warmup_sessions",
        "warmup_configs",
    ):
        if table_name in existing_tables:
            op.drop_table(table_name)
