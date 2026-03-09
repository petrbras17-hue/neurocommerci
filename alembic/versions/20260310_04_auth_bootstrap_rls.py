"""Allow bootstrap auth flow to create initial SaaS rows under RLS

Revision ID: 20260310_04
Revises: 20260310_03
Create Date: 2026-03-10 22:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260310_04"
down_revision = "20260310_03"
branch_labels = None
depends_on = None


def _replace_policy(table: str, name: str, using_sql: str, with_check_sql: str) -> None:
    op.execute(sa.text(f"DROP POLICY IF EXISTS {name} ON {table}"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY {name}
            ON {table}
            USING ({using_sql})
            WITH CHECK ({with_check_sql});
            """
        )
    )


def upgrade() -> None:
    bootstrap_guard = "current_setting('app.bootstrap', true) = '1'"
    auth_user_scope = "id = current_setting('app.user_id', true)::integer"
    tenant_scope = "id = current_setting('app.tenant_id', true)::integer"
    tenant_fk_scope = "tenant_id = current_setting('app.tenant_id', true)::integer"

    _replace_policy(
        "auth_users",
        "auth_users_isolation",
        f"({bootstrap_guard}) OR ({auth_user_scope})",
        f"({bootstrap_guard}) OR ({auth_user_scope})",
    )
    _replace_policy(
        "tenants",
        "tenants_isolation",
        f"({bootstrap_guard}) OR ({tenant_scope})",
        f"({bootstrap_guard}) OR ({tenant_scope})",
    )
    _replace_policy(
        "workspaces",
        "workspaces_isolation",
        f"({bootstrap_guard}) OR ({tenant_fk_scope})",
        f"({bootstrap_guard}) OR ({tenant_fk_scope})",
    )
    _replace_policy(
        "team_members",
        "team_members_isolation",
        f"({bootstrap_guard}) OR ({tenant_fk_scope})",
        f"({bootstrap_guard}) OR ({tenant_fk_scope})",
    )


def downgrade() -> None:
    auth_user_scope = "id = current_setting('app.user_id', true)::integer"
    tenant_scope = "id = current_setting('app.tenant_id', true)::integer"
    tenant_fk_scope = "tenant_id = current_setting('app.tenant_id', true)::integer"

    _replace_policy("auth_users", "auth_users_isolation", auth_user_scope, auth_user_scope)
    _replace_policy("tenants", "tenants_isolation", tenant_scope, tenant_scope)
    _replace_policy("workspaces", "workspaces_isolation", tenant_fk_scope, tenant_fk_scope)
    _replace_policy("team_members", "team_members_isolation", tenant_fk_scope, tenant_fk_scope)
