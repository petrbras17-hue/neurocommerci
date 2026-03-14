"""Channel Map v3: spatial index on lat/lng for viewport queries.

Revision ID: 20260314_32
"""

from alembic import op

revision = "20260314_32"
down_revision = "20260313_31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_channel_map_entries_lat_lng",
        "channel_map_entries",
        ["lat", "lng"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_channel_map_entries_lat_lng", table_name="channel_map_entries")
