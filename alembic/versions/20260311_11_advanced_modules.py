"""Advanced modules: reaction jobs, chatting configs, dialog configs, user parsing results, telegram folders

Revision ID: 20260311_11
Revises: 20260311_10
Create Date: 2026-03-11 14:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260311_11"
down_revision = "20260311_10"
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

    if "reaction_jobs" not in existing_tables:
        op.create_table(
            "reaction_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farm_configs.id"), nullable=True),
            sa.Column("channel_username", sa.String(length=200), nullable=False),
            sa.Column("post_id", sa.Integer(), nullable=True),
            sa.Column("reaction_type", sa.String(length=20), nullable=False, server_default="random"),
            sa.Column("account_ids", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("total_reactions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("successful_reactions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_reactions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )
        existing_tables.add("reaction_jobs")
        inspector = sa.inspect(bind)

    if "chatting_configs" not in existing_tables:
        op.create_table(
            "chatting_configs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="stopped"),
            sa.Column("mode", sa.String(length=20), nullable=False, server_default="conservative"),
            sa.Column("target_channels", sa.JSON(), nullable=True),
            sa.Column("prompt_template", sa.Text(), nullable=True),
            sa.Column("max_messages_per_hour", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("min_delay_seconds", sa.Integer(), nullable=False, server_default="120"),
            sa.Column("max_delay_seconds", sa.Integer(), nullable=False, server_default="600"),
            sa.Column("account_ids", sa.JSON(), nullable=True),
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
        existing_tables.add("chatting_configs")
        inspector = sa.inspect(bind)

    if "dialog_configs" not in existing_tables:
        op.create_table(
            "dialog_configs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="stopped"),
            sa.Column("dialog_type", sa.String(length=30), nullable=False, server_default="warmup"),
            sa.Column("account_pairs", sa.JSON(), nullable=True),
            sa.Column("prompt_template", sa.Text(), nullable=True),
            sa.Column("messages_per_session", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("session_interval_hours", sa.Integer(), nullable=False, server_default="4"),
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
        existing_tables.add("dialog_configs")
        inspector = sa.inspect(bind)

    if "user_parsing_results" not in existing_tables:
        op.create_table(
            "user_parsing_results",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=True),
            sa.Column("channel_username", sa.String(length=200), nullable=True),
            sa.Column("user_telegram_id", sa.BigInteger(), nullable=True),
            sa.Column("username", sa.String(length=200), nullable=True),
            sa.Column("first_name", sa.String(length=200), nullable=True),
            sa.Column("last_name", sa.String(length=200), nullable=True),
            sa.Column("bio", sa.Text(), nullable=True),
            sa.Column("is_premium", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("last_seen", sa.DateTime(), nullable=True),
            sa.Column(
                "parsed_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        existing_tables.add("user_parsing_results")
        inspector = sa.inspect(bind)

    if "telegram_folders" not in existing_tables:
        op.create_table(
            "telegram_folders",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
            sa.Column("folder_name", sa.String(length=200), nullable=False),
            sa.Column("folder_id", sa.Integer(), nullable=True),
            sa.Column("invite_link", sa.String(length=500), nullable=True),
            sa.Column("channel_usernames", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
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
        existing_tables.add("telegram_folders")
        inspector = sa.inspect(bind)

    for table_name, index_name, columns in (
        ("reaction_jobs", "ix_reaction_jobs_tenant_id", ["tenant_id"]),
        ("reaction_jobs", "ix_reaction_jobs_status", ["status"]),
        ("reaction_jobs", "ix_reaction_jobs_farm_id", ["farm_id"]),
        ("chatting_configs", "ix_chatting_configs_tenant_id", ["tenant_id"]),
        ("chatting_configs", "ix_chatting_configs_status", ["status"]),
        ("dialog_configs", "ix_dialog_configs_tenant_id", ["tenant_id"]),
        ("dialog_configs", "ix_dialog_configs_status", ["status"]),
        ("user_parsing_results", "ix_user_parsing_results_tenant_id", ["tenant_id"]),
        ("user_parsing_results", "ix_user_parsing_results_channel_username", ["channel_username"]),
        ("user_parsing_results", "ix_user_parsing_results_user_telegram_id", ["user_telegram_id"]),
        ("telegram_folders", "ix_telegram_folders_tenant_id", ["tenant_id"]),
        ("telegram_folders", "ix_telegram_folders_account_id", ["account_id"]),
        ("telegram_folders", "ix_telegram_folders_status", ["status"]),
    ):
        if table_name in existing_tables:
            _create_index_if_missing(sa.inspect(bind), table_name, index_name, columns)

    if bind.dialect.name == "postgresql":
        for table_name in (
            "reaction_jobs",
            "chatting_configs",
            "dialog_configs",
            "user_parsing_results",
            "telegram_folders",
        ):
            if table_name in existing_tables:
                _enable_tenant_rls(table_name)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)

    if bind.dialect.name == "postgresql":
        for table_name in (
            "telegram_folders",
            "user_parsing_results",
            "dialog_configs",
            "chatting_configs",
            "reaction_jobs",
        ):
            if table_name in existing_tables:
                op.execute(
                    sa.text(f"DROP POLICY IF EXISTS {table_name}_isolation ON {table_name}")
                )

    for table_name in (
        "telegram_folders",
        "user_parsing_results",
        "dialog_configs",
        "chatting_configs",
        "reaction_jobs",
    ):
        if table_name in existing_tables:
            op.drop_table(table_name)
