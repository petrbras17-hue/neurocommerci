"""Operator shell notes and assistant layer tables

Revision ID: 20260310_06
Revises: 20260310_05
Create Date: 2026-03-10 02:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260310_06"
down_revision = "20260310_05"
branch_labels = None
depends_on = None


def _table_names(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(inspector: sa.Inspector, table_name: str, index_name: str, columns: list[str]) -> None:
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

    if "accounts" in existing_tables and "manual_notes" not in _column_names(inspector, "accounts"):
        op.add_column("accounts", sa.Column("manual_notes", sa.Text(), nullable=True))

    if "business_briefs" not in existing_tables:
        op.create_table(
            "business_briefs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("product_name", sa.String(length=255), nullable=True),
            sa.Column("offer_summary", sa.Text(), nullable=True),
            sa.Column("target_audience", sa.Text(), nullable=True),
            sa.Column("competitors", sa.JSON(), nullable=True),
            sa.Column("tone_of_voice", sa.String(length=255), nullable=True),
            sa.Column("pain_points", sa.JSON(), nullable=True),
            sa.Column("telegram_goals", sa.JSON(), nullable=True),
            sa.Column("website_url", sa.String(length=500), nullable=True),
            sa.Column("channel_url", sa.String(length=500), nullable=True),
            sa.Column("bot_url", sa.String(length=500), nullable=True),
            sa.Column("summary_text", sa.Text(), nullable=True),
            sa.Column("completeness_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("tenant_id", "workspace_id", name="uq_business_briefs_tenant_workspace"),
        )
        existing_tables.add("business_briefs")
        inspector = sa.inspect(bind)
    if "business_assets" not in existing_tables:
        op.create_table(
            "business_assets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("brief_id", sa.Integer(), sa.ForeignKey("business_briefs.id"), nullable=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=True),
            sa.Column("asset_type", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("business_assets")
        inspector = sa.inspect(bind)
    if "assistant_threads" not in existing_tables:
        op.create_table(
            "assistant_threads",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=True),
            sa.Column("brief_id", sa.Integer(), sa.ForeignKey("business_briefs.id"), nullable=True),
            sa.Column("thread_kind", sa.String(length=32), nullable=False, server_default="growth_brief"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("title", sa.String(length=255), nullable=True),
            sa.Column("last_step", sa.String(length=64), nullable=False, server_default="start_brief"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("assistant_threads")
        inspector = sa.inspect(bind)
    if "assistant_messages" not in existing_tables:
        op.create_table(
            "assistant_messages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("thread_id", sa.Integer(), sa.ForeignKey("assistant_threads.id"), nullable=False),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=True),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("assistant_messages")
        inspector = sa.inspect(bind)
    if "assistant_recommendations" not in existing_tables:
        op.create_table(
            "assistant_recommendations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("thread_id", sa.Integer(), sa.ForeignKey("assistant_threads.id"), nullable=False),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=True),
            sa.Column("recommendation_type", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("assistant_recommendations")
        inspector = sa.inspect(bind)
    if "creative_drafts" not in existing_tables:
        op.create_table(
            "creative_drafts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=True),
            sa.Column("brief_id", sa.Integer(), sa.ForeignKey("business_briefs.id"), nullable=True),
            sa.Column("draft_type", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("title", sa.String(length=255), nullable=True),
            sa.Column("input_prompt", sa.Text(), nullable=True),
            sa.Column("content_text", sa.Text(), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("creative_drafts")
        inspector = sa.inspect(bind)
    if "manual_actions" not in existing_tables:
        op.create_table(
            "manual_actions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=True),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=True),
            sa.Column("action_type", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("manual_actions")
        inspector = sa.inspect(bind)

    for table_name, index_name, columns in (
        ("business_briefs", "ix_business_briefs_tenant_id", ["tenant_id"]),
        ("business_briefs", "ix_business_briefs_workspace_id", ["workspace_id"]),
        ("business_assets", "ix_business_assets_tenant_id", ["tenant_id"]),
        ("business_assets", "ix_business_assets_workspace_id", ["workspace_id"]),
        ("assistant_threads", "ix_assistant_threads_tenant_id", ["tenant_id"]),
        ("assistant_threads", "ix_assistant_threads_workspace_id", ["workspace_id"]),
        ("assistant_messages", "ix_assistant_messages_thread_id", ["thread_id"]),
        ("assistant_messages", "ix_assistant_messages_tenant_id", ["tenant_id"]),
        ("assistant_messages", "ix_assistant_messages_workspace_id", ["workspace_id"]),
        ("assistant_recommendations", "ix_assistant_recommendations_thread_id", ["thread_id"]),
        ("assistant_recommendations", "ix_assistant_recommendations_tenant_id", ["tenant_id"]),
        ("assistant_recommendations", "ix_assistant_recommendations_workspace_id", ["workspace_id"]),
        ("creative_drafts", "ix_creative_drafts_tenant_id", ["tenant_id"]),
        ("creative_drafts", "ix_creative_drafts_workspace_id", ["workspace_id"]),
        ("creative_drafts", "ix_creative_drafts_brief_id", ["brief_id"]),
        ("manual_actions", "ix_manual_actions_tenant_id", ["tenant_id"]),
        ("manual_actions", "ix_manual_actions_workspace_id", ["workspace_id"]),
        ("manual_actions", "ix_manual_actions_account_id", ["account_id"]),
    ):
        if table_name in existing_tables:
            _create_index_if_missing(sa.inspect(bind), table_name, index_name, columns)

    if bind.dialect.name == "postgresql":
        for table_name in (
            "business_briefs",
            "business_assets",
            "assistant_threads",
            "assistant_messages",
            "assistant_recommendations",
            "creative_drafts",
            "manual_actions",
        ):
            _enable_tenant_rls(table_name)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)

    for table_name in (
        "manual_actions",
        "creative_drafts",
        "assistant_recommendations",
        "assistant_messages",
        "assistant_threads",
        "business_assets",
        "business_briefs",
    ):
        if table_name in existing_tables:
            op.drop_table(table_name)

    if "accounts" in existing_tables and "manual_notes" in _column_names(inspector, "accounts"):
        op.drop_column("accounts", "manual_notes")
