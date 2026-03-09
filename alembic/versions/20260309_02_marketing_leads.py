"""Sprint 2 marketing leads

Revision ID: 20260309_02
Revises: 20260308_01
Create Date: 2026-03-09 20:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260309_02"
down_revision = "20260308_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("company", sa.String(length=255), nullable=False),
        sa.Column("telegram_username", sa.String(length=255), nullable=True),
        sa.Column("use_case", sa.String(length=64), nullable=False),
        sa.Column("utm_source", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_leads_email", "leads", ["email"])
    op.create_index("ix_leads_created_at", "leads", ["created_at"])
    op.create_index("ix_leads_utm_source", "leads", ["utm_source"])


def downgrade() -> None:
    op.drop_index("ix_leads_utm_source", table_name="leads")
    op.drop_index("ix_leads_created_at", table_name="leads")
    op.drop_index("ix_leads_email", table_name="leads")
    op.drop_table("leads")
