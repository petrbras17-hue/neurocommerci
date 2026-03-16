"""Referral system: referrals table + referral_code on auth_users.

Covers:
  1. referrals table with FK to auth_users, unique constraint on referred_id
  2. referral_code column on auth_users (unique, nullable, indexed)
  3. FORCE RLS policy on referrals (tenant-free — uses auth_users.id directly)

Revision ID: 20260316_46
Revises: 20260316_45
Create Date: 2026-03-16
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260316_46"
down_revision = "20260316_45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ── 1. Add referral_code to auth_users ───────────────────────────────────
    op.add_column(
        "auth_users",
        sa.Column("referral_code", sa.String(8), nullable=True),
    )
    op.create_index("ix_auth_users_referral_code", "auth_users", ["referral_code"], unique=True)

    # ── 2. Create referrals table ────────────────────────────────────────────
    op.create_table(
        "referrals",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("referrer_id", sa.Integer, sa.ForeignKey("auth_users.id"), nullable=False),
        sa.Column("referred_id", sa.Integer, sa.ForeignKey("auth_users.id"), nullable=False),
        sa.Column("referral_code", sa.String(8), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("reward_amount", sa.Integer, server_default="0"),
        sa.Column("rewarded_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("referred_id", name="uq_referrals_referred_id"),
    )
    op.create_index("ix_referrals_referrer_id", "referrals", ["referrer_id"])
    op.create_index("ix_referrals_referral_code", "referrals", ["referral_code"])
    op.create_index("ix_referrals_status", "referrals", ["status"])

    # ── 3. FORCE RLS on PostgreSQL ───────────────────────────────────────────
    if is_pg:
        # referrals: users can only see their own referrals (as referrer)
        op.execute("ALTER TABLE referrals ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE referrals FORCE ROW LEVEL SECURITY")
        op.execute("""
            CREATE POLICY referrals_isolation_policy ON referrals
                USING (
                    referrer_id = NULLIF(current_setting('app.current_user_id', true), '')::int
                    OR referred_id = NULLIF(current_setting('app.current_user_id', true), '')::int
                )
                WITH CHECK (
                    referrer_id = NULLIF(current_setting('app.current_user_id', true), '')::int
                )
        """)


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.execute("DROP POLICY IF EXISTS referrals_isolation_policy ON referrals")
        op.execute("ALTER TABLE referrals DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_referrals_status", table_name="referrals")
    op.drop_index("ix_referrals_referral_code", table_name="referrals")
    op.drop_index("ix_referrals_referrer_id", table_name="referrals")
    op.drop_table("referrals")

    op.drop_index("ix_auth_users_referral_code", table_name="auth_users")
    op.drop_column("auth_users", "referral_code")
