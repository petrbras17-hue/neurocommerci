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
    op.add_column("channel_map_entries", sa.Column("lat", sa.Float(), nullable=True))
    op.add_column("channel_map_entries", sa.Column("lng", sa.Float(), nullable=True))
    op.create_index("ix_channel_map_entries_lat", "channel_map_entries", ["lat"])
    op.create_index("ix_channel_map_entries_lng", "channel_map_entries", ["lng"])


def downgrade() -> None:
    op.drop_index("ix_channel_map_entries_lng", table_name="channel_map_entries")
    op.drop_index("ix_channel_map_entries_lat", table_name="channel_map_entries")
    op.drop_column("channel_map_entries", "lng")
    op.drop_column("channel_map_entries", "lat")
