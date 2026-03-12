"""Add channel intelligence tables: channel_profiles, channel_ban_events,
channel_join_requests.

Revision ID: 20260312_19
Revises: 20260311_18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260312_19"
down_revision = "20260311_18"
branch_labels = None
depends_on = None


def _table_names(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(
    inspector: sa.Inspector,
    table_name: str,
    index_name: str,
    columns: list[str],
    unique: bool = False,
) -> None:
    if index_name not in _index_names(inspector, table_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def _enable_tenant_rls(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}"))
    tenant_scope = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::int"
    op.execute(
        sa.text(
            f"""
            CREATE POLICY {table_name}_tenant_isolation
            ON {table_name}
            USING ({tenant_scope});
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)
    is_postgresql = bind.dialect.name == "postgresql"

    # --- channel_profiles ---
    if "channel_profiles" not in existing_tables:
        if is_postgresql:
            op.create_table(
                "channel_profiles",
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column(
                    "tenant_id",
                    sa.Integer(),
                    sa.ForeignKey("tenants.id"),
                    nullable=False,
                ),
                sa.Column(
                    "channel_entry_id",
                    sa.Integer(),
                    sa.ForeignKey("channel_entries.id"),
                    nullable=True,
                ),
                sa.Column("telegram_id", sa.BigInteger(), nullable=False),
                sa.Column("username", sa.String(length=100), nullable=True),
                sa.Column("title", sa.String(length=300), nullable=True),
                sa.Column(
                    "channel_type",
                    sa.String(length=20),
                    nullable=False,
                    server_default="channel",
                ),
                sa.Column(
                    "is_private",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
                sa.Column(
                    "slow_mode_seconds",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
                sa.Column(
                    "no_links",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
                sa.Column(
                    "no_forwards",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
                sa.Column("linked_chat_id", sa.BigInteger(), nullable=True),
                sa.Column("pinned_rules_text", sa.Text(), nullable=True),
                sa.Column(
                    "ai_extracted_rules",
                    postgresql.JSONB(astext_type=sa.Text()),
                    nullable=True,
                ),
                sa.Column(
                    "learned_rules",
                    postgresql.JSONB(astext_type=sa.Text()),
                    nullable=True,
                ),
                sa.Column(
                    "ban_risk",
                    sa.String(length=10),
                    nullable=False,
                    server_default="low",
                ),
                sa.Column(
                    "success_rate",
                    sa.Float(),
                    nullable=False,
                    server_default="1.0",
                ),
                sa.Column(
                    "total_comments",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
                sa.Column(
                    "total_bans",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
                sa.Column(
                    "safe_comment_interval_sec",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
                sa.Column("last_profiled_at", sa.DateTime(), nullable=True),
                sa.Column("last_ban_analysis_at", sa.DateTime(), nullable=True),
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
                sa.UniqueConstraint("tenant_id", "telegram_id", name="uq_channel_profiles_tenant_telegram"),
            )
        else:
            op.create_table(
                "channel_profiles",
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column(
                    "tenant_id",
                    sa.Integer(),
                    sa.ForeignKey("tenants.id"),
                    nullable=False,
                ),
                sa.Column(
                    "channel_entry_id",
                    sa.Integer(),
                    sa.ForeignKey("channel_entries.id"),
                    nullable=True,
                ),
                sa.Column("telegram_id", sa.BigInteger(), nullable=False),
                sa.Column("username", sa.String(length=100), nullable=True),
                sa.Column("title", sa.String(length=300), nullable=True),
                sa.Column(
                    "channel_type",
                    sa.String(length=20),
                    nullable=False,
                    server_default="channel",
                ),
                sa.Column(
                    "is_private",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                ),
                sa.Column(
                    "slow_mode_seconds",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
                sa.Column(
                    "no_links",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                ),
                sa.Column(
                    "no_forwards",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                ),
                sa.Column("linked_chat_id", sa.BigInteger(), nullable=True),
                sa.Column("pinned_rules_text", sa.Text(), nullable=True),
                sa.Column("ai_extracted_rules", sa.JSON(), nullable=True),
                sa.Column("learned_rules", sa.JSON(), nullable=True),
                sa.Column(
                    "ban_risk",
                    sa.String(length=10),
                    nullable=False,
                    server_default="low",
                ),
                sa.Column(
                    "success_rate",
                    sa.Float(),
                    nullable=False,
                    server_default="1.0",
                ),
                sa.Column(
                    "total_comments",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
                sa.Column(
                    "total_bans",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
                sa.Column(
                    "safe_comment_interval_sec",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
                sa.Column("last_profiled_at", sa.DateTime(), nullable=True),
                sa.Column("last_ban_analysis_at", sa.DateTime(), nullable=True),
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
                sa.UniqueConstraint("tenant_id", "telegram_id", name="uq_channel_profiles_tenant_telegram"),
            )
        existing_tables.add("channel_profiles")
        inspector = sa.inspect(bind)

    # --- channel_ban_events ---
    if "channel_ban_events" not in existing_tables:
        if is_postgresql:
            op.create_table(
                "channel_ban_events",
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column(
                    "tenant_id",
                    sa.Integer(),
                    sa.ForeignKey("tenants.id"),
                    nullable=False,
                ),
                sa.Column(
                    "channel_profile_id",
                    sa.Integer(),
                    sa.ForeignKey("channel_profiles.id"),
                    nullable=False,
                ),
                sa.Column(
                    "account_id",
                    sa.Integer(),
                    sa.ForeignKey("accounts.id"),
                    nullable=False,
                ),
                sa.Column("ban_type", sa.String(length=20), nullable=False),
                sa.Column(
                    "last_action_before_ban",
                    postgresql.JSONB(astext_type=sa.Text()),
                    nullable=True,
                ),
                sa.Column(
                    "ai_analysis",
                    postgresql.JSONB(astext_type=sa.Text()),
                    nullable=True,
                ),
                sa.Column(
                    "created_at",
                    sa.DateTime(),
                    nullable=False,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                ),
            )
        else:
            op.create_table(
                "channel_ban_events",
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column(
                    "tenant_id",
                    sa.Integer(),
                    sa.ForeignKey("tenants.id"),
                    nullable=False,
                ),
                sa.Column(
                    "channel_profile_id",
                    sa.Integer(),
                    sa.ForeignKey("channel_profiles.id"),
                    nullable=False,
                ),
                sa.Column(
                    "account_id",
                    sa.Integer(),
                    sa.ForeignKey("accounts.id"),
                    nullable=False,
                ),
                sa.Column("ban_type", sa.String(length=20), nullable=False),
                sa.Column("last_action_before_ban", sa.JSON(), nullable=True),
                sa.Column("ai_analysis", sa.JSON(), nullable=True),
                sa.Column(
                    "created_at",
                    sa.DateTime(),
                    nullable=False,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                ),
            )
        existing_tables.add("channel_ban_events")
        inspector = sa.inspect(bind)

    # --- channel_join_requests ---
    if "channel_join_requests" not in existing_tables:
        op.create_table(
            "channel_join_requests",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id"),
                nullable=False,
            ),
            sa.Column(
                "channel_profile_id",
                sa.Integer(),
                sa.ForeignKey("channel_profiles.id"),
                nullable=True,
            ),
            sa.Column(
                "account_id",
                sa.Integer(),
                sa.ForeignKey("accounts.id"),
                nullable=False,
            ),
            sa.Column("telegram_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=10),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "requested_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
        )
        existing_tables.add("channel_join_requests")
        inspector = sa.inspect(bind)

    # --- indexes ---
    for table_name, index_name, columns in (
        ("channel_profiles", "ix_channel_profiles_tenant_id", ["tenant_id"]),
        ("channel_profiles", "ix_channel_profiles_telegram_id", ["telegram_id"]),
        ("channel_ban_events", "ix_channel_ban_events_tenant_id", ["tenant_id"]),
        (
            "channel_ban_events",
            "ix_channel_ban_events_channel_profile_id",
            ["channel_profile_id"],
        ),
        ("channel_ban_events", "ix_channel_ban_events_created_at", ["created_at"]),
        ("channel_join_requests", "ix_channel_join_requests_tenant_id", ["tenant_id"]),
        ("channel_join_requests", "ix_channel_join_requests_status", ["status"]),
        ("channel_join_requests", "ix_channel_join_requests_account_id", ["account_id"]),
    ):
        if table_name in existing_tables:
            _create_index_if_missing(sa.inspect(bind), table_name, index_name, columns)

    # --- RLS (PostgreSQL only) ---
    if is_postgresql:
        for table_name in (
            "channel_profiles",
            "channel_ban_events",
            "channel_join_requests",
        ):
            if table_name in existing_tables:
                _enable_tenant_rls(table_name)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)

    # drop RLS policies first (PostgreSQL only)
    if bind.dialect.name == "postgresql":
        for table_name in (
            "channel_join_requests",
            "channel_ban_events",
            "channel_profiles",
        ):
            if table_name in existing_tables:
                op.execute(
                    sa.text(
                        f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}"
                    )
                )

    # drop tables in reverse creation order
    for table_name in (
        "channel_join_requests",
        "channel_ban_events",
        "channel_profiles",
    ):
        if table_name in existing_tables:
            op.drop_table(table_name)
