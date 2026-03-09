"""Sprint 1 SaaS tenant foundation

Revision ID: 20260308_01
Revises:
Create Date: 2026-03-08 22:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260308_01"
down_revision = None
branch_labels = None
depends_on = None


def _create_policy_if_missing(name: str, table: str, using_sql: str, with_check_sql: str | None = None) -> None:
    check_clause = f" WITH CHECK ({with_check_sql})" if with_check_sql else ""
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = '{table}'
                      AND policyname = '{name}'
                ) THEN
                    CREATE POLICY {name}
                    ON {table}
                    USING ({using_sql}){check_clause};
                END IF;
            END
            $$;
            """
        )
    )


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))


def upgrade() -> None:
    op.create_table(
        "auth_users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=255), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_workspaces_tenant_id", "workspaces", ["tenant_id"])
    op.create_table(
        "team_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_team_members_workspace_user"),
    )
    op.create_index("ix_team_members_tenant_id", "team_members", ["tenant_id"])
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_usage_events_tenant_id", "usage_events", ["tenant_id"])
    op.create_index("ix_usage_events_event_type", "usage_events", ["event_type"])
    op.create_index("ix_usage_events_tenant_created_at", "usage_events", ["tenant_id", "created_at"])

    for table in ("auth_users", "tenants", "workspaces", "team_members", "usage_events"):
        _enable_rls(table)

    _create_policy_if_missing(
        "auth_users_isolation",
        "auth_users",
        "id = current_setting('app.user_id', true)::integer",
        "id = current_setting('app.user_id', true)::integer",
    )
    _create_policy_if_missing(
        "tenants_isolation",
        "tenants",
        "id = current_setting('app.tenant_id', true)::integer",
        "id = current_setting('app.tenant_id', true)::integer",
    )
    _create_policy_if_missing(
        "workspaces_isolation",
        "workspaces",
        "tenant_id = current_setting('app.tenant_id', true)::integer",
        "tenant_id = current_setting('app.tenant_id', true)::integer",
    )
    _create_policy_if_missing(
        "team_members_isolation",
        "team_members",
        "tenant_id = current_setting('app.tenant_id', true)::integer",
        "tenant_id = current_setting('app.tenant_id', true)::integer",
    )
    _create_policy_if_missing(
        "usage_events_isolation",
        "usage_events",
        "tenant_id = current_setting('app.tenant_id', true)::integer",
        "tenant_id = current_setting('app.tenant_id', true)::integer",
    )


def downgrade() -> None:
    for table in ("usage_events", "team_members", "workspaces", "tenants", "auth_users"):
        op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_isolation ON {table}"))
    op.drop_index("ix_usage_events_event_type", table_name="usage_events")
    op.drop_index("ix_usage_events_tenant_id", table_name="usage_events")
    op.drop_index("ix_usage_events_tenant_created_at", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_index("ix_team_members_tenant_id", table_name="team_members")
    op.drop_table("team_members")
    op.drop_index("ix_workspaces_tenant_id", table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_table("tenants")
    op.drop_table("auth_users")
