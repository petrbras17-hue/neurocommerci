"""Sprint 24: Farm Launch Orchestration + Anti-Fraud Intelligence — farm_launch_plans, antifraud_scores, pattern_detections, scaling_history."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260314_42"
down_revision = "20260314_41"
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
    # ── farm_launch_plans ──
    op.create_table(
        "farm_launch_plans",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("farm_id", sa.Integer, nullable=False),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("scaling_curve", sa.String(24), server_default="gradual"),
        sa.Column("custom_curve", JSONType, nullable=True),
        sa.Column("day_1_limit", sa.Integer, server_default="2"),
        sa.Column("day_3_limit", sa.Integer, server_default="5"),
        sa.Column("day_7_limit", sa.Integer, server_default="10"),
        sa.Column("day_14_limit", sa.Integer, server_default="20"),
        sa.Column("day_30_limit", sa.Integer, server_default="-1"),
        sa.Column("current_day", sa.Integer, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("health_gate_threshold", sa.Integer, server_default="40"),
        sa.Column("auto_reduce_factor", sa.Float, server_default="0.5"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_farm_launch_plans_workspace_id", "farm_launch_plans", ["workspace_id"])
    op.create_index("ix_farm_launch_plans_farm_id", "farm_launch_plans", ["farm_id"])
    _rls("farm_launch_plans")

    # ── antifraud_scores ──
    op.create_table(
        "antifraud_scores",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("risk_score", sa.Float, nullable=False),
        sa.Column("risk_factors", JSONType, nullable=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_antifraud_scores_workspace_id", "antifraud_scores", ["workspace_id"])
    op.create_index("ix_antifraud_scores_account_id", "antifraud_scores", ["account_id"])
    op.create_index("ix_antifraud_scores_action_type", "antifraud_scores", ["action_type"])
    _rls("antifraud_scores")

    # ── pattern_detections ──
    op.create_table(
        "pattern_detections",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("pattern_type", sa.String(64), nullable=False),
        sa.Column("accounts_involved", JSONType, nullable=True),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("is_resolved", sa.Boolean, server_default="false"),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pattern_detections_workspace_id", "pattern_detections", ["workspace_id"])
    op.create_index("ix_pattern_detections_pattern_type", "pattern_detections", ["pattern_type"])
    op.create_index("ix_pattern_detections_is_resolved", "pattern_detections", ["is_resolved"])
    _rls("pattern_detections")

    # ── scaling_history ──
    op.create_table(
        "scaling_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("farm_id", sa.Integer, nullable=False),
        sa.Column("account_id", sa.Integer, nullable=True),
        sa.Column("day_number", sa.Integer, nullable=True),
        sa.Column("max_allowed", sa.Integer, nullable=True),
        sa.Column("actual_performed", sa.Integer, nullable=True),
        sa.Column("was_health_gated", sa.Boolean, server_default="false"),
        sa.Column("was_antifraud_gated", sa.Boolean, server_default="false"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_scaling_history_workspace_id", "scaling_history", ["workspace_id"])
    op.create_index("ix_scaling_history_farm_id", "scaling_history", ["farm_id"])
    op.create_index("ix_scaling_history_account_id", "scaling_history", ["account_id"])
    _rls("scaling_history")


def downgrade():
    op.drop_table("scaling_history")
    op.drop_table("pattern_detections")
    op.drop_table("antifraud_scores")
    op.drop_table("farm_launch_plans")
