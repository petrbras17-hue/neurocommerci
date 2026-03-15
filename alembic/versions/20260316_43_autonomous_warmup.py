"""Autonomous Warmup 'Living Account' — packaging presets table, persona extensions.

account_personas, account_phase_history, and accounts.warmup_phase/warmup_day/next_session_at
already exist from earlier sprints. This migration adds:
  1. account_packaging_presets table (new)
  2. Missing columns on account_personas: country, reply_probability, weekend_activity, generated_by, persona_prompt
  3. Scheduler polling index on accounts

Revision ID: 20260316_43
Revises: 20260315_35
Create Date: 2026-03-16
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260316_43"
down_revision = "20260315_35"
branch_labels = None
depends_on = None

_TENANT_SCOPE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::integer"


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB(astext_type=sa.Text()) if is_pg else sa.JSON()

    # ── 1. account_packaging_presets (new table) ─────────────────────
    op.create_table(
        "account_packaging_presets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("display_name", sa.String(70), nullable=True),
        sa.Column("bio", sa.String(150), nullable=True),
        sa.Column("avatar_path", sa.String(500), nullable=True),
        sa.Column("username", sa.String(32), nullable=True),
        sa.Column("channel_name", sa.String(255), nullable=True),
        sa.Column("channel_description", sa.Text(), nullable=True),
        sa.Column("channel_pin_text", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), server_default="'manual'"),
        sa.Column("status", sa.String(20), server_default="'draft'"),
        sa.Column("apply_at", sa.DateTime(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("apply_log", json_type, nullable=True),
        sa.Column("persona_prompt", sa.Text(), nullable=True),
        sa.Column("generation_params", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_packaging_presets_tenant_id", "account_packaging_presets", ["tenant_id"])
    op.create_index("ix_packaging_presets_account_status", "account_packaging_presets", ["account_id", "status"])

    if is_pg:
        op.execute(sa.text("ALTER TABLE account_packaging_presets ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text("ALTER TABLE account_packaging_presets FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(
            f"CREATE POLICY account_packaging_presets_isolation "
            f"ON account_packaging_presets "
            f"USING ({_TENANT_SCOPE}) WITH CHECK ({_TENANT_SCOPE})"
        ))

    # ── 2. Extend account_personas with missing columns ──────────────
    op.add_column("account_personas", sa.Column("country", sa.String(4), nullable=True))
    op.add_column("account_personas", sa.Column("reply_probability", sa.Float(), server_default="0.15"))
    op.add_column("account_personas", sa.Column("weekend_activity", sa.Float(), server_default="0.6"))
    op.add_column("account_personas", sa.Column("generated_by", sa.String(50), nullable=True))
    op.add_column("account_personas", sa.Column("persona_prompt", sa.Text(), nullable=True))

    # ── 3. Scheduler polling index on accounts ───────────────────────
    if is_pg:
        op.execute(sa.text(
            "CREATE INDEX IF NOT EXISTS ix_accounts_warmup_poll "
            "ON accounts(next_session_at, warmup_phase) "
            "WHERE health_status NOT IN ('dead', 'frozen', 'banned')"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.execute(sa.text("DROP INDEX IF EXISTS ix_accounts_warmup_poll"))

    op.drop_column("account_personas", "persona_prompt")
    op.drop_column("account_personas", "generated_by")
    op.drop_column("account_personas", "weekend_activity")
    op.drop_column("account_personas", "reply_probability")
    op.drop_column("account_personas", "country")

    if is_pg:
        op.execute(sa.text("DROP POLICY IF EXISTS account_packaging_presets_isolation ON account_packaging_presets"))
    op.drop_index("ix_packaging_presets_account_status", table_name="account_packaging_presets")
    op.drop_index("ix_packaging_presets_tenant_id", table_name="account_packaging_presets")
    op.drop_table("account_packaging_presets")
