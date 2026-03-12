"""Sprint 9: product_briefs, campaign_channels, campaign_accounts tables

Revision ID: 20260312_20
Revises: 20260312_19
Create Date: 2026-03-12 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260312_21"
down_revision = "20260312_20"
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


def _jsonb_or_json() -> sa.types.TypeEngine:
    """Return JSONB on PostgreSQL, JSON on other dialects (SQLite tests)."""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)
    is_postgres = bind.dialect.name == "postgresql"

    # ------------------------------------------------------------------
    # product_briefs
    # ------------------------------------------------------------------
    if "product_briefs" not in existing_tables:
        op.create_table(
            "product_briefs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=True),
            sa.Column("url", sa.String(length=2000), nullable=True),
            sa.Column("product_name", sa.String(length=500), nullable=True),
            sa.Column("target_audience", sa.Text(), nullable=True),
            sa.Column("brand_tone", sa.String(length=200), nullable=True),
            sa.Column("usp", sa.Text(), nullable=True),
            sa.Column("keywords", _jsonb_or_json(), nullable=True),
            sa.Column("suggested_styles", _jsonb_or_json(), nullable=True),
            sa.Column("daily_volume", sa.Integer(), nullable=True),
            sa.Column("analysis_raw", _jsonb_or_json(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        existing_tables.add("product_briefs")
        inspector = sa.inspect(bind)
        _create_index_if_missing(inspector, "product_briefs", "ix_product_briefs_tenant_id", ["tenant_id"])
        _create_index_if_missing(inspector, "product_briefs", "ix_product_briefs_workspace_id", ["workspace_id"])

    # ------------------------------------------------------------------
    # campaign_channels
    # ------------------------------------------------------------------
    if "campaign_channels" not in existing_tables:
        op.create_table(
            "campaign_channels",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
            sa.Column("channel_id", sa.Integer(), sa.ForeignKey("channel_map_entries.id"), nullable=True),
            sa.Column("channel_username", sa.String(length=200), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("comments_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_comment_at", sa.DateTime(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        existing_tables.add("campaign_channels")
        inspector = sa.inspect(bind)
        _create_index_if_missing(inspector, "campaign_channels", "ix_campaign_channels_tenant_id", ["tenant_id"])
        _create_index_if_missing(inspector, "campaign_channels", "ix_campaign_channels_campaign_id", ["campaign_id"])

    # ------------------------------------------------------------------
    # campaign_accounts
    # ------------------------------------------------------------------
    if "campaign_accounts" not in existing_tables:
        op.create_table(
            "campaign_accounts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("comments_today", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_comment_at", sa.DateTime(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        existing_tables.add("campaign_accounts")
        inspector = sa.inspect(bind)
        _create_index_if_missing(inspector, "campaign_accounts", "ix_campaign_accounts_tenant_id", ["tenant_id"])
        _create_index_if_missing(inspector, "campaign_accounts", "ix_campaign_accounts_campaign_id", ["campaign_id"])
        _create_index_if_missing(inspector, "campaign_accounts", "ix_campaign_accounts_account_id", ["account_id"])

    # ------------------------------------------------------------------
    # brief_id column on campaigns (optional FK to product_briefs)
    # ------------------------------------------------------------------
    if is_postgres:
        existing_cols = {c["name"] for c in inspector.get_columns("campaigns")}
        if "brief_id" not in existing_cols:
            op.add_column(
                "campaigns",
                sa.Column("brief_id", sa.Integer(), sa.ForeignKey("product_briefs.id"), nullable=True),
            )

    # ------------------------------------------------------------------
    # RLS (PostgreSQL only)
    # ------------------------------------------------------------------
    if is_postgres:
        _enable_tenant_rls("product_briefs")
        _enable_tenant_rls("campaign_channels")
        _enable_tenant_rls("campaign_accounts")


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute(sa.text("DROP POLICY IF EXISTS campaign_accounts_isolation ON campaign_accounts"))
        op.execute(sa.text("ALTER TABLE campaign_accounts DISABLE ROW LEVEL SECURITY"))
        op.execute(sa.text("DROP POLICY IF EXISTS campaign_channels_isolation ON campaign_channels"))
        op.execute(sa.text("ALTER TABLE campaign_channels DISABLE ROW LEVEL SECURITY"))
        op.execute(sa.text("DROP POLICY IF EXISTS product_briefs_isolation ON product_briefs"))
        op.execute(sa.text("ALTER TABLE product_briefs DISABLE ROW LEVEL SECURITY"))

        # Drop brief_id column from campaigns if it was added
        try:
            op.drop_column("campaigns", "brief_id")
        except Exception:
            pass

    op.drop_table("campaign_accounts")
    op.drop_table("campaign_channels")
    op.drop_table("product_briefs")
