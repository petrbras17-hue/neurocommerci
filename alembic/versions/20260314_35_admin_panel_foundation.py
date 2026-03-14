"""Admin panel foundation — is_platform_admin, admin_accounts, admin_proxies, admin_operations_log"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260314_35"
down_revision = "20260314_34"
branch_labels = None
depends_on = None


def upgrade():
    # Add is_platform_admin to auth_users
    op.add_column(
        "auth_users",
        sa.Column("is_platform_admin", sa.Boolean(), server_default="false", nullable=False),
    )

    # Admin-managed accounts (mirrors filesystem sessions in PostgreSQL index)
    op.create_table(
        "admin_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False, unique=True),
        sa.Column("country", sa.String(5)),
        sa.Column("display_name", sa.String(255)),
        sa.Column("username", sa.String(255)),
        sa.Column("bio", sa.Text()),
        sa.Column("api_id", sa.Integer()),
        sa.Column("api_hash", sa.String(64)),
        sa.Column("dc_id", sa.Integer()),
        sa.Column("session_path", sa.String(512)),
        sa.Column("proxy_id", sa.Integer()),
        sa.Column("two_fa_password", sa.String(128)),
        sa.Column("status", sa.String(32), server_default="uploaded"),
        sa.Column("lifecycle_phase", sa.String(32), server_default="day0"),
        sa.Column("source", sa.String(32)),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("security_hardened_at", sa.DateTime(timezone=True)),
        sa.Column("warmup_started_at", sa.DateTime(timezone=True)),
        sa.Column("profile_change_earliest", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_admin_accounts_workspace", "admin_accounts", ["workspace_id"])
    op.create_index("ix_admin_accounts_status", "admin_accounts", ["status"])

    # Admin-managed proxies
    op.create_table(
        "admin_proxies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(255)),
        sa.Column("password", sa.String(255)),
        sa.Column("proxy_type", sa.String(10), server_default="socks5"),
        sa.Column("country", sa.String(5)),
        sa.Column("status", sa.String(16), server_default="untested"),
        sa.Column("bound_account_id", sa.Integer()),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("last_ip", sa.String(45)),
        sa.Column("supports_https_connect", sa.Boolean()),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_admin_proxies_workspace", "admin_proxies", ["workspace_id"])
    op.create_index("ix_admin_proxies_status", "admin_proxies", ["status"])

    # Operations log
    op.create_table(
        "admin_operations_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer()),
        sa.Column("proxy_id", sa.Integer()),
        sa.Column("module", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_admin_ops_log_workspace", "admin_operations_log", ["workspace_id"])
    op.create_index("ix_admin_ops_log_created", "admin_operations_log", ["created_at"])

    # RLS policies
    for table in ["admin_accounts", "admin_proxies", "admin_operations_log"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"FOR ALL "
            f"USING (workspace_id::text = current_setting('app.current_workspace_id', true)) "
            f"WITH CHECK (workspace_id::text = current_setting('app.current_workspace_id', true))"
        )


def downgrade():
    for table in ["admin_operations_log", "admin_proxies", "admin_accounts"]:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_table("admin_operations_log")
    op.drop_table("admin_proxies")
    op.drop_table("admin_accounts")
    op.drop_column("auth_users", "is_platform_admin")
