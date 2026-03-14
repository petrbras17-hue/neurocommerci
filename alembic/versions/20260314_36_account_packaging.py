"""Account packaging — profile, avatar, channel columns on admin_accounts."""

from alembic import op
import sqlalchemy as sa

revision = "20260314_36"
down_revision = "20260314_35"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admin_accounts", sa.Column("profile_gender", sa.String(16)))
    op.add_column("admin_accounts", sa.Column("profile_age_range", sa.String(16)))
    op.add_column("admin_accounts", sa.Column("profile_country", sa.String(4)))
    op.add_column("admin_accounts", sa.Column("profile_profession", sa.String(64)))
    op.add_column("admin_accounts", sa.Column("profile_bio", sa.Text()))
    op.add_column("admin_accounts", sa.Column("profile_first_name", sa.String(64)))
    op.add_column("admin_accounts", sa.Column("profile_last_name", sa.String(64)))
    op.add_column("admin_accounts", sa.Column("profile_username", sa.String(64)))
    op.add_column("admin_accounts", sa.Column("avatar_path", sa.String(256)))
    op.add_column("admin_accounts", sa.Column("channel_id", sa.BigInteger()))
    op.add_column("admin_accounts", sa.Column("channel_username", sa.String(64)))
    op.add_column("admin_accounts", sa.Column("channel_title", sa.String(128)))
    op.add_column("admin_accounts", sa.Column("profile_applied_at", sa.DateTime(timezone=True)))
    op.add_column("admin_accounts", sa.Column("channel_created_at", sa.DateTime(timezone=True)))
    op.add_column(
        "admin_accounts",
        sa.Column("packaging_status", sa.String(24), server_default="not_started"),
    )


def downgrade():
    for col in [
        "packaging_status",
        "channel_created_at",
        "profile_applied_at",
        "channel_title",
        "channel_username",
        "channel_id",
        "avatar_path",
        "profile_username",
        "profile_last_name",
        "profile_first_name",
        "profile_bio",
        "profile_profession",
        "profile_country",
        "profile_age_range",
        "profile_gender",
    ]:
        op.drop_column("admin_accounts", col)
