"""Add lat/lng columns to channel_map_entries for globe visualization.

Revision ID: 20260313_25
Revises: 20260313_24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260313_25"
down_revision = "20260313_24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: columns may already exist from prior manual schema changes
    op.execute(
        "DO $$ BEGIN "
        "ALTER TABLE channel_map_entries ADD COLUMN lat FLOAT; "
        "EXCEPTION WHEN duplicate_column THEN NULL; "
        "END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "ALTER TABLE channel_map_entries ADD COLUMN lng FLOAT; "
        "EXCEPTION WHEN duplicate_column THEN NULL; "
        "END $$"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_channel_map_entries_lat ON channel_map_entries (lat)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_channel_map_entries_lng ON channel_map_entries (lng)")


def downgrade() -> None:
    op.drop_index("ix_channel_map_entries_lng", table_name="channel_map_entries")
    op.drop_index("ix_channel_map_entries_lat", table_name="channel_map_entries")
    op.drop_column("channel_map_entries", "lng")
    op.drop_column("channel_map_entries", "lat")
