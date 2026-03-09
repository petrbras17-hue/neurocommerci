"""Sprint 3 telegram auth shell and tenant bridge

Revision ID: 20260310_03
Revises: 20260309_02
Create Date: 2026-03-10 11:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260310_03"
down_revision = "20260309_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_users", sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
    op.add_column("auth_users", sa.Column("telegram_username", sa.String(length=255), nullable=True))
    op.add_column("auth_users", sa.Column("first_name", sa.String(length=255), nullable=True))
    op.add_column("auth_users", sa.Column("last_name", sa.String(length=255), nullable=True))
    op.add_column("auth_users", sa.Column("company", sa.String(length=255), nullable=True))
    op.add_column("auth_users", sa.Column("last_login_at", sa.DateTime(), nullable=True))
    op.create_unique_constraint("uq_auth_users_telegram_user_id", "auth_users", ["telegram_user_id"])

    op.add_column("workspaces", sa.Column("runtime_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_workspaces_runtime_user_id_users",
        "workspaces",
        "users",
        ["runtime_user_id"],
        ["id"],
    )
    op.create_index("ix_workspaces_runtime_user_id", "workspaces", ["runtime_user_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("ip_address", sa.String(length=128), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_tenant_id", "refresh_tokens", ["tenant_id"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])

    op.add_column("accounts", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.add_column("accounts", sa.Column("workspace_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_accounts_tenant_id_tenants", "accounts", "tenants", ["tenant_id"], ["id"])
    op.create_foreign_key("fk_accounts_workspace_id_workspaces", "accounts", "workspaces", ["workspace_id"], ["id"])
    op.create_index("ix_accounts_tenant_id", "accounts", ["tenant_id"])
    op.create_index("ix_accounts_workspace_id", "accounts", ["workspace_id"])

    op.add_column("proxies", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.add_column("proxies", sa.Column("workspace_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_proxies_tenant_id_tenants", "proxies", "tenants", ["tenant_id"], ["id"])
    op.create_foreign_key("fk_proxies_workspace_id_workspaces", "proxies", "workspaces", ["workspace_id"], ["id"])
    op.create_index("ix_proxies_tenant_id", "proxies", ["tenant_id"])
    op.create_index("ix_proxies_workspace_id", "proxies", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_proxies_workspace_id", table_name="proxies")
    op.drop_index("ix_proxies_tenant_id", table_name="proxies")
    op.drop_constraint("fk_proxies_workspace_id_workspaces", "proxies", type_="foreignkey")
    op.drop_constraint("fk_proxies_tenant_id_tenants", "proxies", type_="foreignkey")
    op.drop_column("proxies", "workspace_id")
    op.drop_column("proxies", "tenant_id")

    op.drop_index("ix_accounts_workspace_id", table_name="accounts")
    op.drop_index("ix_accounts_tenant_id", table_name="accounts")
    op.drop_constraint("fk_accounts_workspace_id_workspaces", "accounts", type_="foreignkey")
    op.drop_constraint("fk_accounts_tenant_id_tenants", "accounts", type_="foreignkey")
    op.drop_column("accounts", "workspace_id")
    op.drop_column("accounts", "tenant_id")

    op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_tenant_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index("ix_workspaces_runtime_user_id", table_name="workspaces")
    op.drop_constraint("fk_workspaces_runtime_user_id_users", "workspaces", type_="foreignkey")
    op.drop_column("workspaces", "runtime_user_id")

    op.drop_constraint("uq_auth_users_telegram_user_id", "auth_users", type_="unique")
    op.drop_column("auth_users", "last_login_at")
    op.drop_column("auth_users", "company")
    op.drop_column("auth_users", "last_name")
    op.drop_column("auth_users", "first_name")
    op.drop_column("auth_users", "telegram_username")
    op.drop_column("auth_users", "telegram_user_id")
