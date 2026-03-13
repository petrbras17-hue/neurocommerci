"""Add progress column to parsing_jobs.

Revision ID: 20260313_29
Revises: 20260313_28
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260313_29"
down_revision = "20260313_28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parsing_jobs",
        sa.Column("progress", sa.Integer(), nullable=True, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("parsing_jobs", "progress")
