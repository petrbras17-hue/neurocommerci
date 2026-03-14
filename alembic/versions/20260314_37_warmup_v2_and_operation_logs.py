"""Warmup v2 — operation_logs table, warmup_configs schedule columns, warmup_sessions progress columns."""

from alembic import op
import sqlalchemy as sa

revision = "20260314_37"
down_revision = "20260314_36"
branch_labels = None
depends_on = None


def upgrade():
    # ── New table: operation_logs ──
    op.create_table(
        "operation_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("account_id", sa.Integer, nullable=True),
        sa.Column("module", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_operation_logs_workspace_id", "operation_logs", ["workspace_id"])
    op.create_index("ix_operation_logs_module_created", "operation_logs", ["module", "created_at"])
    op.create_index("ix_operation_logs_account_id", "operation_logs", ["account_id"])

    # RLS
    op.execute("ALTER TABLE operation_logs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE operation_logs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY operation_logs_tenant_isolation ON operation_logs "
        "USING (workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::int) "
        "WITH CHECK (workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::int)"
    )

    # ── Alter warmup_configs: add schedule columns ──
    op.add_column("warmup_configs", sa.Column("schedule_start_hour", sa.Integer, server_default="9"))
    op.add_column("warmup_configs", sa.Column("schedule_end_hour", sa.Integer, server_default="22"))
    op.add_column("warmup_configs", sa.Column("sessions_per_day", sa.Integer, server_default="3"))
    op.add_column("warmup_configs", sa.Column("session_duration_minutes", sa.Integer, server_default="30"))
    op.add_column("warmup_configs", sa.Column("enable_story_viewing", sa.Boolean, server_default="true"))
    op.add_column("warmup_configs", sa.Column("enable_channel_joining", sa.Boolean, server_default="true"))
    op.add_column("warmup_configs", sa.Column("enable_dialogs", sa.Boolean, server_default="false"))
    op.add_column("warmup_configs", sa.Column("max_channels_to_join", sa.Integer, server_default="3"))

    # ── Alter warmup_sessions: add progress columns ──
    op.add_column("warmup_sessions", sa.Column("actions_completed", sa.Integer, server_default="0"))
    op.add_column("warmup_sessions", sa.Column("channels_visited", sa.Integer, server_default="0"))
    op.add_column("warmup_sessions", sa.Column("stories_viewed", sa.Integer, server_default="0"))
    op.add_column("warmup_sessions", sa.Column("channels_joined", sa.Integer, server_default="0"))
    op.add_column("warmup_sessions", sa.Column("days_warmed", sa.Integer, server_default="0"))
    op.add_column("warmup_sessions", sa.Column("progress_pct", sa.Integer, server_default="0"))


def downgrade():
    # warmup_sessions
    for col in ["progress_pct", "days_warmed", "channels_joined", "stories_viewed", "channels_visited", "actions_completed"]:
        op.drop_column("warmup_sessions", col)

    # warmup_configs
    for col in ["max_channels_to_join", "enable_dialogs", "enable_channel_joining", "enable_story_viewing",
                "session_duration_minutes", "sessions_per_day", "schedule_end_hour", "schedule_start_hour"]:
        op.drop_column("warmup_configs", col)

    # operation_logs
    op.execute("DROP POLICY IF EXISTS operation_logs_tenant_isolation ON operation_logs")
    op.drop_table("operation_logs")
