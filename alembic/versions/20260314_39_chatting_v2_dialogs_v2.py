"""Sprint 21: Chatting v2 + Dialogs v2 — chatting_configs_v2, dm_inbox, dm_messages, chatting_presets, auto_responder_configs."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260314_39"
down_revision = "20260314_38"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(JSONB, "postgresql")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_workspace_isolation ON {table} "
        f"USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::int) "
        f"WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::int)"
    )


def upgrade():
    # ── chatting_configs_v2 ──
    op.create_table(
        "chatting_configs_v2",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("mode", sa.String(24), server_default="interval"),
        sa.Column("interval_percent", sa.Integer, server_default="10"),
        sa.Column("trigger_keywords", JSONType, nullable=True),
        sa.Column("semantic_topics", JSONType, nullable=True),
        sa.Column("product_name", sa.String(128), nullable=True),
        sa.Column("product_description", sa.Text, nullable=True),
        sa.Column("product_problems_solved", sa.Text, nullable=True),
        sa.Column("mention_frequency", sa.String(24), server_default="subtle"),
        sa.Column("context_depth", sa.Integer, server_default="5"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chatting_configs_v2_workspace_id", "chatting_configs_v2", ["workspace_id"])
    _rls("chatting_configs_v2")

    # ── dm_inbox ──
    op.create_table(
        "dm_inbox",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.Column("account_phone", sa.String(20), nullable=True),
        sa.Column("peer_id", sa.BigInteger, nullable=False),
        sa.Column("peer_name", sa.String(128), nullable=True),
        sa.Column("peer_username", sa.String(64), nullable=True),
        sa.Column("last_message_text", sa.Text, nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unread_count", sa.Integer, server_default="0"),
        sa.Column("is_auto_responding", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "account_id", "peer_id", name="uq_dm_inbox_ws_acct_peer"),
    )
    op.create_index("ix_dm_inbox_workspace_id", "dm_inbox", ["workspace_id"])
    op.create_index("ix_dm_inbox_account_id", "dm_inbox", ["account_id"])
    _rls("dm_inbox")

    # ── dm_messages ──
    op.create_table(
        "dm_messages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("inbox_id", sa.Integer, nullable=False),
        sa.Column("sender", sa.String(16), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_dm_messages_workspace_id", "dm_messages", ["workspace_id"])
    op.create_index("ix_dm_messages_inbox_id", "dm_messages", ["inbox_id"])
    _rls("dm_messages")

    # ── chatting_presets ──
    op.create_table(
        "chatting_presets",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("config", JSONType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chatting_presets_workspace_id", "chatting_presets", ["workspace_id"])
    _rls("chatting_presets")

    # ── auto_responder_configs ──
    op.create_table(
        "auto_responder_configs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("account_id", sa.Integer, nullable=True),
        sa.Column("product_name", sa.String(128), nullable=True),
        sa.Column("product_description", sa.Text, nullable=True),
        sa.Column("tone", sa.String(24), server_default="friendly"),
        sa.Column("max_responses_per_day", sa.Integer, server_default="20"),
        sa.Column("responses_today", sa.Integer, server_default="0"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_auto_responder_configs_workspace_id", "auto_responder_configs", ["workspace_id"])
    _rls("auto_responder_configs")


def downgrade():
    for tbl in ["auto_responder_configs", "chatting_presets", "dm_messages", "dm_inbox", "chatting_configs_v2"]:
        op.execute(f"DROP POLICY IF EXISTS {tbl}_workspace_isolation ON {tbl}")
        op.drop_table(tbl)
