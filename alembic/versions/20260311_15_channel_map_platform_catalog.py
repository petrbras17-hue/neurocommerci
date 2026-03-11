"""Make channel_map_entries tenant_id nullable and add catalog columns.

Revision ID: 20260311_15
Revises: 20260311_14
"""
from alembic import op
import sqlalchemy as sa

revision = "20260311_15"
down_revision = "20260311_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    # Make tenant_id nullable for platform-level catalog
    op.alter_column("channel_map_entries", "tenant_id", existing_type=sa.Integer(), nullable=True)

    # Add new columns
    op.add_column("channel_map_entries", sa.Column("description", sa.String(2000), nullable=True))
    op.add_column("channel_map_entries", sa.Column("region", sa.String(10), nullable=True))
    op.add_column("channel_map_entries", sa.Column("comments_enabled", sa.Boolean(), server_default="false"))
    op.add_column("channel_map_entries", sa.Column("avg_comments_per_post", sa.Integer(), nullable=True))
    op.add_column("channel_map_entries", sa.Column("post_frequency_daily", sa.Float(), nullable=True))
    op.add_column("channel_map_entries", sa.Column("verified", sa.Boolean(), server_default="false"))
    op.add_column("channel_map_entries", sa.Column("source", sa.String(50), nullable=True))

    # Add index on username for search
    op.create_index("ix_channel_map_entries_username", "channel_map_entries", ["username"])
    op.create_index("ix_channel_map_entries_region", "channel_map_entries", ["region"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    op.drop_index("ix_channel_map_entries_region")
    op.drop_index("ix_channel_map_entries_username")
    op.drop_column("channel_map_entries", "source")
    op.drop_column("channel_map_entries", "verified")
    op.drop_column("channel_map_entries", "post_frequency_daily")
    op.drop_column("channel_map_entries", "avg_comments_per_post")
    op.drop_column("channel_map_entries", "comments_enabled")
    op.drop_column("channel_map_entries", "region")
    op.drop_column("channel_map_entries", "description")
    op.alter_column("channel_map_entries", "tenant_id", existing_type=sa.Integer(), nullable=False)
