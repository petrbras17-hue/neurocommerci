"""Add intelligence columns to channel_map_entries: topic_tags, spam_score,
last_refreshed_at.

Revision ID: 20260311_18
Revises: 20260311_17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260311_18"
down_revision = "20260311_17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        # SQLite: use generic JSON type
        op.add_column(
            "channel_map_entries",
            sa.Column("topic_tags", sa.JSON(), nullable=True),
        )
        op.add_column(
            "channel_map_entries",
            sa.Column("spam_score", sa.Float(), nullable=True),
        )
        op.add_column(
            "channel_map_entries",
            sa.Column("last_refreshed_at", sa.DateTime(), nullable=True),
        )
    else:
        # PostgreSQL: use JSONB for topic_tags
        op.add_column(
            "channel_map_entries",
            sa.Column(
                "topic_tags",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )
        op.add_column(
            "channel_map_entries",
            sa.Column("spam_score", sa.Float(), nullable=True),
        )
        op.add_column(
            "channel_map_entries",
            sa.Column("last_refreshed_at", sa.DateTime(), nullable=True),
        )
        # Index to efficiently query stale channels
        op.create_index(
            "ix_channel_map_entries_last_refreshed_at",
            "channel_map_entries",
            ["last_refreshed_at"],
        )
        op.create_index(
            "ix_channel_map_entries_spam_score",
            "channel_map_entries",
            ["spam_score"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if not is_sqlite:
        op.drop_index(
            "ix_channel_map_entries_spam_score",
            table_name="channel_map_entries",
        )
        op.drop_index(
            "ix_channel_map_entries_last_refreshed_at",
            table_name="channel_map_entries",
        )

    op.drop_column("channel_map_entries", "last_refreshed_at")
    op.drop_column("channel_map_entries", "spam_score")
    op.drop_column("channel_map_entries", "topic_tags")
