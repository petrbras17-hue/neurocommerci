"""account batch settings columns

Revision ID: 20260313_30
Revises: 20260313_29b
Create Date: 2026-03-13
"""

from alembic import op
import sqlalchemy as sa

revision = "20260313_30"
down_revision = "20260313_29b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("proxy_strategy", sa.String(20), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("ai_protection", sa.String(20), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("account_comment_language", sa.String(10), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("warmup_mode", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "warmup_mode")
    op.drop_column("accounts", "account_comment_language")
    op.drop_column("accounts", "ai_protection")
    op.drop_column("accounts", "proxy_strategy")
