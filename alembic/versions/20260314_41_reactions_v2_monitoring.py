"""Sprint 23: Reactions v2 + Real-Time Monitoring — reaction_monitoring_configs, reaction_blacklists, account_status_live, module_throughput."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260314_41"
down_revision = "20260314_40"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(JSONB, "postgresql")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_workspace_isolation ON {table} "
        f"USING (workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::int) "
        f"WITH CHECK (workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::int)"
    )


def upgrade():
    # ── reaction_monitoring_configs ──
    op.create_table(
        "reaction_monitoring_configs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("channel_title", sa.String(256), nullable=True),
        sa.Column("reaction_emoji", sa.String(8), server_default="👍"),
        sa.Column("react_within_seconds", sa.Integer, server_default="30"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("accounts_assigned", JSONType, nullable=True),
        sa.Column("max_reactions_per_hour", sa.Integer, server_default="30"),
        sa.Column("reactions_this_hour", sa.Integer, server_default="0"),
        sa.Column("use_channel_reaction", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_reaction_monitoring_configs_workspace_id", "reaction_monitoring_configs", ["workspace_id"])
    op.create_index("ix_reaction_monitoring_configs_channel_id", "reaction_monitoring_configs", ["channel_id"])
    _rls("reaction_monitoring_configs")

    # ── reaction_blacklists ──
    op.create_table(
        "reaction_blacklists",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("channel_title", sa.String(256), nullable=True),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "channel_id", name="uq_reaction_blacklists_ws_channel"),
    )
    op.create_index("ix_reaction_blacklists_workspace_id", "reaction_blacklists", ["workspace_id"])
    _rls("reaction_blacklists")

    # ── account_status_live ──
    op.create_table(
        "account_status_live",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.Column("account_phone", sa.String(20), nullable=True),
        sa.Column("current_module", sa.String(32), nullable=True),
        sa.Column("current_action", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "account_id", name="uq_account_status_live_ws_account"),
    )
    op.create_index("ix_account_status_live_workspace_id", "account_status_live", ["workspace_id"])
    op.create_index("ix_account_status_live_current_module", "account_status_live", ["current_module"])
    _rls("account_status_live")

    # ── module_throughput ──
    op.create_table(
        "module_throughput",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("module", sa.String(32), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actions_count", sa.Integer, server_default="0"),
        sa.Column("errors_count", sa.Integer, server_default="0"),
        sa.Column("avg_latency_ms", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_module_throughput_workspace_id", "module_throughput", ["workspace_id"])
    op.create_index("ix_module_throughput_module", "module_throughput", ["module"])
    op.create_index("ix_module_throughput_period_start", "module_throughput", ["period_start"])
    _rls("module_throughput")


def downgrade():
    op.drop_table("module_throughput")
    op.drop_table("account_status_live")
    op.drop_table("reaction_blacklists")
    op.drop_table("reaction_monitoring_configs")
