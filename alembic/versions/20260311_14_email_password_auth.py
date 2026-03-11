"""Add password_hash to auth_users for email/password login

Revision ID: 20260311_14
Revises: sprint5_rls_hardening
Create Date: 2026-03-11 20:00:00

Adds a nullable password_hash column to auth_users so that existing
Telegram-only users keep their rows intact (password_hash stays NULL)
while new email-registered users have a bcrypt hash stored.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260311_14"
down_revision = "sprint5_rls_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_users",
        sa.Column("password_hash", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("auth_users", "password_hash")
