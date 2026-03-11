"""Farm orchestrator tables

Revision ID: 20260311_09
Revises: 20260310_08
Create Date: 2026-03-11 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260311_09"
down_revision = "20260310_08"
branch_labels = None
depends_on = None


def _table_names(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


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

    if "farm_configs" not in existing_tables:
        op.create_table(
            "farm_configs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="stopped"),
            sa.Column("mode", sa.String(length=20), nullable=False, server_default="multithread"),
            sa.Column("max_threads", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("comment_prompt", sa.Text(), nullable=True),
            sa.Column("comment_tone", sa.String(length=50), nullable=False, server_default="neutral"),
            sa.Column("comment_language", sa.String(length=10), nullable=False, server_default="auto"),
            sa.Column("comment_all_posts", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("comment_percentage", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("delay_before_comment_min", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("delay_before_comment_max", sa.Integer(), nullable=False, server_default="120"),
            sa.Column("delay_before_join_min", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("delay_before_join_max", sa.Integer(), nullable=False, server_default="300"),
            sa.Column("ai_protection_mode", sa.String(length=20), nullable=False, server_default="aggressive"),
            sa.Column("auto_responder_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("auto_responder_prompt", sa.Text(), nullable=True),
            sa.Column("auto_responder_redirect_url", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("farm_configs")
        inspector = sa.inspect(bind)

    if "farm_threads" not in existing_tables:
        op.create_table(
            "farm_threads",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farm_configs.id"), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("thread_index", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="idle"),
            sa.Column("assigned_channels", sa.JSON(), nullable=True),
            sa.Column("folder_invite_link", sa.Text(), nullable=True),
            sa.Column("stats_comments_sent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stats_comments_failed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stats_reactions_sent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stats_last_comment_at", sa.DateTime(), nullable=True),
            sa.Column("stats_last_error", sa.Text(), nullable=True),
            sa.Column("health_score", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("quarantine_until", sa.DateTime(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("farm_threads")
        inspector = sa.inspect(bind)

    if "channel_databases" not in existing_tables:
        op.create_table(
            "channel_databases",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("source", sa.String(length=20), nullable=False, server_default="manual"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("channel_databases")
        inspector = sa.inspect(bind)

    if "channel_entries" not in existing_tables:
        op.create_table(
            "channel_entries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("database_id", sa.Integer(), sa.ForeignKey("channel_databases.id"), nullable=False),
            sa.Column("telegram_id", sa.BigInteger(), nullable=True),
            sa.Column("username", sa.String(length=100), nullable=True),
            sa.Column("title", sa.String(length=300), nullable=True),
            sa.Column("member_count", sa.Integer(), nullable=True),
            sa.Column("has_comments", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("language", sa.String(length=10), nullable=True),
            sa.Column("category", sa.String(length=100), nullable=True),
            sa.Column("last_post_at", sa.DateTime(), nullable=True),
            sa.Column("blacklisted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("success_rate", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("channel_entries")
        inspector = sa.inspect(bind)

    if "parsing_jobs" not in existing_tables:
        op.create_table(
            "parsing_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=True),
            sa.Column("job_type", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("keywords", sa.JSON(), nullable=True),
            sa.Column("filters", sa.JSON(), nullable=True),
            sa.Column("max_results", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("results_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("target_database_id", sa.Integer(), sa.ForeignKey("channel_databases.id"), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("parsing_jobs")
        inspector = sa.inspect(bind)

    if "profile_templates" not in existing_tables:
        op.create_table(
            "profile_templates",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=True),
            sa.Column("gender", sa.String(length=10), nullable=True),
            sa.Column("geo", sa.String(length=50), nullable=True),
            sa.Column("bio_template", sa.Text(), nullable=True),
            sa.Column("channel_name_template", sa.Text(), nullable=True),
            sa.Column("channel_description_template", sa.Text(), nullable=True),
            sa.Column("channel_first_post_template", sa.Text(), nullable=True),
            sa.Column("avatar_style", sa.String(length=50), nullable=True),
            sa.Column("avatar_url", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("profile_templates")
        inspector = sa.inspect(bind)

    if "farm_events" not in existing_tables:
        op.create_table(
            "farm_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farm_configs.id"), nullable=False),
            sa.Column("thread_id", sa.Integer(), sa.ForeignKey("farm_threads.id"), nullable=True),
            sa.Column("event_type", sa.String(length=50), nullable=False),
            sa.Column("severity", sa.String(length=10), nullable=False, server_default="info"),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("farm_events")
        inspector = sa.inspect(bind)

    for table_name, index_name, columns in (
        ("farm_configs", "ix_farm_configs_tenant_id", ["tenant_id"]),
        ("farm_configs", "ix_farm_configs_workspace_id", ["workspace_id"]),
        ("farm_configs", "ix_farm_configs_status", ["status"]),
        ("farm_threads", "ix_farm_threads_tenant_id", ["tenant_id"]),
        ("farm_threads", "ix_farm_threads_farm_id", ["farm_id"]),
        ("farm_threads", "ix_farm_threads_account_id", ["account_id"]),
        ("farm_threads", "ix_farm_threads_status", ["status"]),
        ("channel_databases", "ix_channel_databases_tenant_id", ["tenant_id"]),
        ("channel_databases", "ix_channel_databases_workspace_id", ["workspace_id"]),
        ("channel_entries", "ix_channel_entries_tenant_id", ["tenant_id"]),
        ("channel_entries", "ix_channel_entries_database_id", ["database_id"]),
        ("channel_entries", "ix_channel_entries_blacklisted", ["blacklisted"]),
        ("parsing_jobs", "ix_parsing_jobs_tenant_id", ["tenant_id"]),
        ("parsing_jobs", "ix_parsing_jobs_workspace_id", ["workspace_id"]),
        ("parsing_jobs", "ix_parsing_jobs_status", ["status"]),
        ("parsing_jobs", "ix_parsing_jobs_account_id", ["account_id"]),
        ("profile_templates", "ix_profile_templates_tenant_id", ["tenant_id"]),
        ("profile_templates", "ix_profile_templates_workspace_id", ["workspace_id"]),
        ("farm_events", "ix_farm_events_tenant_id", ["tenant_id"]),
        ("farm_events", "ix_farm_events_farm_id", ["farm_id"]),
        ("farm_events", "ix_farm_events_thread_id", ["thread_id"]),
        ("farm_events", "ix_farm_events_created_at", ["created_at"]),
        ("farm_events", "ix_farm_events_severity", ["severity"]),
    ):
        if table_name in existing_tables:
            _create_index_if_missing(sa.inspect(bind), table_name, index_name, columns)

    if bind.dialect.name == "postgresql":
        for table_name in (
            "farm_configs",
            "farm_threads",
            "channel_databases",
            "channel_entries",
            "parsing_jobs",
            "profile_templates",
            "farm_events",
        ):
            if table_name in existing_tables:
                _enable_tenant_rls(table_name)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)

    if bind.dialect.name == "postgresql":
        for table_name in (
            "farm_events",
            "profile_templates",
            "parsing_jobs",
            "channel_entries",
            "channel_databases",
            "farm_threads",
            "farm_configs",
        ):
            if table_name in existing_tables:
                op.execute(sa.text(f"DROP POLICY IF EXISTS {table_name}_isolation ON {table_name}"))

    for table_name in (
        "farm_events",
        "profile_templates",
        "parsing_jobs",
        "channel_entries",
        "channel_databases",
        "farm_threads",
        "farm_configs",
    ):
        if table_name in existing_tables:
            op.drop_table(table_name)
