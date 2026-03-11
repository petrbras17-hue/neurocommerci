"""Intelligence & Scale: channel map, campaigns, campaign runs, analytics events

Revision ID: 20260311_12
Revises: 20260311_11
Create Date: 2026-03-11 15:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260311_12"
down_revision = "20260311_11"
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

    if "channel_map_entries" not in existing_tables:
        op.create_table(
            "channel_map_entries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("telegram_id", sa.BigInteger(), nullable=True),
            sa.Column("username", sa.String(length=200), nullable=True),
            sa.Column("title", sa.String(length=500), nullable=True),
            sa.Column("category", sa.String(length=100), nullable=True),
            sa.Column("subcategory", sa.String(length=100), nullable=True),
            sa.Column("language", sa.String(length=10), nullable=True),
            sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("has_comments", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("avg_post_reach", sa.Integer(), nullable=True),
            sa.Column("engagement_rate", sa.Float(), nullable=True),
            sa.Column("last_indexed_at", sa.DateTime(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        existing_tables.add("channel_map_entries")
        inspector = sa.inspect(bind)

    if "campaigns" not in existing_tables:
        op.create_table(
            "campaigns",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("campaign_type", sa.String(length=30), nullable=False, server_default="commenting"),
            sa.Column("account_ids", sa.JSON(), nullable=True),
            sa.Column(
                "channel_database_id",
                sa.Integer(),
                sa.ForeignKey("channel_databases.id"),
                nullable=True,
            ),
            sa.Column("comment_prompt", sa.Text(), nullable=True),
            sa.Column("comment_tone", sa.String(length=50), nullable=True),
            sa.Column("comment_language", sa.String(length=10), nullable=False, server_default="ru"),
            sa.Column("schedule_type", sa.String(length=20), nullable=False, server_default="continuous"),
            sa.Column("schedule_config", sa.JSON(), nullable=True),
            sa.Column("budget_daily_actions", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("budget_total_actions", sa.Integer(), nullable=True),
            sa.Column("total_actions_performed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_comments_sent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_reactions_sent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
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
        existing_tables.add("campaigns")
        inspector = sa.inspect(bind)

    if "campaign_runs" not in existing_tables:
        op.create_table(
            "campaign_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("actions_performed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("comments_sent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reactions_sent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("run_log", sa.JSON(), nullable=True),
        )
        existing_tables.add("campaign_runs")
        inspector = sa.inspect(bind)

    if "analytics_events" not in existing_tables:
        op.create_table(
            "analytics_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("event_type", sa.String(length=50), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=True),
            sa.Column("campaign_id", sa.Integer(), nullable=True),
            sa.Column("channel_username", sa.String(length=200), nullable=True),
            sa.Column("event_data", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        existing_tables.add("analytics_events")
        inspector = sa.inspect(bind)

    for table_name, index_name, columns in (
        ("channel_map_entries", "ix_channel_map_entries_tenant_id", ["tenant_id"]),
        ("channel_map_entries", "ix_channel_map_entries_category", ["category"]),
        ("channel_map_entries", "ix_channel_map_entries_language", ["language"]),
        ("channel_map_entries", "ix_channel_map_entries_member_count", ["member_count"]),
        ("campaigns", "ix_campaigns_tenant_id", ["tenant_id"]),
        ("campaigns", "ix_campaigns_status", ["status"]),
        ("campaigns", "ix_campaigns_campaign_type", ["campaign_type"]),
        ("campaign_runs", "ix_campaign_runs_tenant_id", ["tenant_id"]),
        ("campaign_runs", "ix_campaign_runs_campaign_id", ["campaign_id"]),
        ("campaign_runs", "ix_campaign_runs_status", ["status"]),
        ("analytics_events", "ix_analytics_events_tenant_id", ["tenant_id"]),
        ("analytics_events", "ix_analytics_events_event_type", ["event_type"]),
        ("analytics_events", "ix_analytics_events_account_id", ["account_id"]),
        ("analytics_events", "ix_analytics_events_created_at", ["created_at"]),
    ):
        if table_name in existing_tables:
            _create_index_if_missing(sa.inspect(bind), table_name, index_name, columns)

    if bind.dialect.name == "postgresql":
        for table_name in (
            "channel_map_entries",
            "campaigns",
            "campaign_runs",
            "analytics_events",
        ):
            if table_name in existing_tables:
                _enable_tenant_rls(table_name)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)

    if bind.dialect.name == "postgresql":
        for table_name in (
            "analytics_events",
            "campaign_runs",
            "campaigns",
            "channel_map_entries",
        ):
            if table_name in existing_tables:
                op.execute(
                    sa.text(f"DROP POLICY IF EXISTS {table_name}_isolation ON {table_name}")
                )

    for table_name in (
        "analytics_events",
        "campaign_runs",
        "campaigns",
        "channel_map_entries",
    ):
        if table_name in existing_tables:
            op.drop_table(table_name)
