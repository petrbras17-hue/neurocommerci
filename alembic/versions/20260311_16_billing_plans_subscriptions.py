"""Add billing tables: plans, subscriptions, payment_events.

Revision ID: 20260311_16
Revises: 20260311_15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260311_16"
down_revision = "20260311_15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    json_type = JSONB if bind.dialect.name == "postgresql" else sa.JSON

    op.create_table(
        "plans",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("price_monthly_rub", sa.Integer, server_default="0"),
        sa.Column("price_yearly_rub", sa.Integer, server_default="0"),
        sa.Column("max_accounts", sa.Integer, server_default="1"),
        sa.Column("max_channels", sa.Integer, server_default="10"),
        sa.Column("max_comments_per_day", sa.Integer, server_default="50"),
        sa.Column("max_campaigns", sa.Integer, server_default="1"),
        sa.Column("features", json_type, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("sort_order", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("status", sa.String(30), server_default="trial"),
        sa.Column("trial_ends_at", sa.DateTime, nullable=True),
        sa.Column("current_period_start", sa.DateTime, nullable=True),
        sa.Column("current_period_end", sa.DateTime, nullable=True),
        sa.Column("cancelled_at", sa.DateTime, nullable=True),
        sa.Column("payment_provider", sa.String(30), nullable=True),
        sa.Column("external_subscription_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_index("ix_subscriptions_tenant_id", "subscriptions", ["tenant_id"])

    op.create_table(
        "payment_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("subscription_id", sa.Integer, sa.ForeignKey("subscriptions.id"), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("amount_rub", sa.Integer, server_default="0"),
        sa.Column("payment_provider", sa.String(30), nullable=True),
        sa.Column("external_payment_id", sa.String(255), nullable=True),
        sa.Column("metadata", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_payment_events_tenant_id", "payment_events", ["tenant_id"])

    # Seed default plans
    op.execute("""
        INSERT INTO plans (slug, name, price_monthly_rub, price_yearly_rub, max_accounts, max_channels, max_comments_per_day, max_campaigns, features, is_active, sort_order, created_at)
        VALUES
        ('trial', 'Пробный', 0, 0, 1, 10, 25, 1, '{"ai_assistant": true, "analytics": false, "profiles": false}', true, 0, NOW()),
        ('starter', 'Стартовый', 4990, 49900, 3, 50, 100, 3, '{"ai_assistant": true, "analytics": true, "profiles": false}', true, 1, NOW()),
        ('growth', 'Рост', 9990, 99900, 10, 200, 500, 10, '{"ai_assistant": true, "analytics": true, "profiles": true, "chatting": true}', true, 2, NOW()),
        ('enterprise', 'Enterprise', 29990, 299900, 50, 1000, 2000, 50, '{"ai_assistant": true, "analytics": true, "profiles": true, "chatting": true, "priority_support": true}', true, 3, NOW())
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    op.drop_table("payment_events")
    op.drop_table("subscriptions")
    op.drop_table("plans")
