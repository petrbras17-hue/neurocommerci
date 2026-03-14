"""Sprint 20: Neurocommenting v2 — blacklists, whitelists, farm presets, auto-DM, farm targeting columns."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260314_38"
down_revision = "20260314_37"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade():
    # ── channel_blacklists ──
    op.create_table(
        "channel_blacklists",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("channel_username", sa.String(64), nullable=True),
        sa.Column("channel_title", sa.String(256), nullable=True),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "channel_id", name="uq_channel_blacklists_ws_ch"),
    )
    op.create_index("ix_channel_blacklists_workspace_id", "channel_blacklists", ["workspace_id"])
    op.execute("ALTER TABLE channel_blacklists ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE channel_blacklists FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY channel_blacklists_tenant_isolation ON channel_blacklists "
        "USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::int) "
        "WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::int)"
    )

    # ── channel_whitelists ──
    op.create_table(
        "channel_whitelists",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("channel_username", sa.String(64), nullable=True),
        sa.Column("channel_title", sa.String(256), nullable=True),
        sa.Column("successful_comments", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "channel_id", name="uq_channel_whitelists_ws_ch"),
    )
    op.create_index("ix_channel_whitelists_workspace_id", "channel_whitelists", ["workspace_id"])
    op.execute("ALTER TABLE channel_whitelists ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE channel_whitelists FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY channel_whitelists_tenant_isolation ON channel_whitelists "
        "USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::int) "
        "WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::int)"
    )

    # ── farm_presets ──
    op.create_table(
        "farm_presets",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("config", JSONType, nullable=False),
        sa.Column("targeting_mode", sa.String(24), server_default="all"),
        sa.Column("targeting_params", JSONType, nullable=True),
        sa.Column("comment_as_channel", sa.Boolean, server_default="false"),
        sa.Column("auto_dm_enabled", sa.Boolean, server_default="false"),
        sa.Column("auto_dm_message", sa.Text, nullable=True),
        sa.Column("language", sa.String(8), server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_farm_presets_workspace_id", "farm_presets", ["workspace_id"])
    op.execute("ALTER TABLE farm_presets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE farm_presets FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY farm_presets_tenant_isolation ON farm_presets "
        "USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::int) "
        "WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::int)"
    )

    # ── auto_dm_configs ──
    op.create_table(
        "auto_dm_configs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("farm_id", sa.Integer, nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("max_dms_per_day", sa.Integer, server_default="10"),
        sa.Column("dms_sent_today", sa.Integer, server_default="0"),
        sa.Column("last_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_auto_dm_configs_workspace_id", "auto_dm_configs", ["workspace_id"])
    op.create_index("ix_auto_dm_configs_farm_id", "auto_dm_configs", ["farm_id"])
    op.execute("ALTER TABLE auto_dm_configs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE auto_dm_configs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY auto_dm_configs_tenant_isolation ON auto_dm_configs "
        "USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::int) "
        "WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::int)"
    )

    # ── Alter farm_configs: add v2 columns ──
    op.add_column("farm_configs", sa.Column("targeting_mode", sa.String(24), server_default="all"))
    op.add_column("farm_configs", sa.Column("targeting_params", JSONType, nullable=True))
    op.add_column("farm_configs", sa.Column("comment_as_channel", sa.Boolean, server_default="false"))
    op.add_column("farm_configs", sa.Column("auto_dm_enabled", sa.Boolean, server_default="false"))
    op.add_column("farm_configs", sa.Column("auto_dm_message", sa.Text, nullable=True))
    op.add_column("farm_configs", sa.Column("language_mode", sa.String(8), server_default="auto"))


def downgrade():
    # farm_configs columns
    for col in ["language_mode", "auto_dm_message", "auto_dm_enabled",
                "comment_as_channel", "targeting_params", "targeting_mode"]:
        op.drop_column("farm_configs", col)

    # auto_dm_configs
    op.execute("DROP POLICY IF EXISTS auto_dm_configs_tenant_isolation ON auto_dm_configs")
    op.drop_table("auto_dm_configs")

    # farm_presets
    op.execute("DROP POLICY IF EXISTS farm_presets_tenant_isolation ON farm_presets")
    op.drop_table("farm_presets")

    # channel_whitelists
    op.execute("DROP POLICY IF EXISTS channel_whitelists_tenant_isolation ON channel_whitelists")
    op.drop_table("channel_whitelists")

    # channel_blacklists
    op.execute("DROP POLICY IF EXISTS channel_blacklists_tenant_isolation ON channel_blacklists")
    op.drop_table("channel_blacklists")
