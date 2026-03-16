"""Sprint 13 — Billing enhancements: add missing plan columns, payments table, FORCE RLS, seed plans.

Revision ID: 20260314_33
Revises: 20260314_32
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260314_33"
down_revision = "20260314_32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    json_type = JSONB

    # -- Enhance plans table with sprint-13 columns --
    # Add new columns if they don't already exist.
    # We use server-side ALTER TABLE with IF NOT EXISTS-safe approach.
    op.execute(sa.text(
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS price_rub INTEGER NOT NULL DEFAULT 0"
    ))
    op.execute(sa.text(
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS price_usd INTEGER DEFAULT NULL"
    ))
    op.execute(sa.text(
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS comments_per_day INTEGER NOT NULL DEFAULT 100"
    ))
    op.execute(sa.text(
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS max_farms INTEGER NOT NULL DEFAULT 5"
    ))
    op.execute(sa.text(
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS ai_tier VARCHAR(20) NOT NULL DEFAULT 'worker'"
    ))
    op.execute(sa.text(
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS display_name VARCHAR(100)"
    ))

    # -- Enhance subscriptions table --
    op.execute(sa.text(
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS cancel_reason TEXT DEFAULT NULL"
    ))

    # -- Create payments table (idempotent) --
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id),
            subscription_id INTEGER REFERENCES subscriptions(id),
            amount INTEGER NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'RUB',
            payment_provider VARCHAR(20) NOT NULL,
            external_payment_id VARCHAR(255),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_payments_tenant_id ON payments (tenant_id)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_payments_subscription_id ON payments (subscription_id)"
    ))

    # -- FORCE RLS on billing tables --
    for table in ("subscriptions", "payment_events", "payments"):
        op.execute(sa.text(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"
        ))
        op.execute(sa.text(
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
        ))
        op.execute(sa.text(
            f"DROP POLICY IF EXISTS {table}_isolation ON {table}"
        ))
        tenant_scope = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::integer"
        op.execute(sa.text(f"""
            CREATE POLICY {table}_isolation
            ON {table}
            USING ({tenant_scope})
            WITH CHECK ({tenant_scope})
        """))

    # -- Re-seed plans with Sprint 13 data --
    # Upsert all 5 canonical plans using ON CONFLICT on slug.
    # Prices aligned with pricing_strategy.md (2026-03-16):
    # Starter 4990, Growth 12990, Pro 29990, Agency 79990 RUB/mo
    # Annual = monthly * 12 * 0.8 (20% discount)
    # price_rub = price_monthly_rub * 100 (kopecks for payment providers)
    op.execute(sa.text("""
        INSERT INTO plans
            (slug, name, display_name, price_monthly_rub, price_yearly_rub,
             price_rub, price_usd, max_accounts, max_channels, max_comments_per_day,
             comments_per_day, max_campaigns, max_farms, ai_tier,
             features, is_active, sort_order, created_at)
        VALUES
            ('starter', 'Starter', 'Starter', 4990, 47900,
             499000, 55, 5, 50, 100,
             100, 3, 1, 'worker',
             '{"ai_assistant": true, "analytics": true, "smart_commenter": true, "parser": true, "warmup": true, "warmup_phases": 3, "comment_styles": 3}'::jsonb,
             true, 1, NOW()),
            ('growth', 'Growth', 'Growth', 12990, 124700,
             1299000, 140, 20, 200, 500,
             500, 10, 3, 'manager',
             '{"ai_assistant": true, "analytics": true, "smart_commenter": true, "parser": true, "warmup": true, "warmup_phases": 7, "comment_styles": 7, "comment_ab": true, "farm": true, "channel_map": true, "channel_map_ai": true, "content_factory": true, "content_factory_formats": 3, "mass_reactions": true, "user_parser": true, "folders": true, "weekly_reports": true}'::jsonb,
             true, 2, NOW()),
            ('pro', 'Pro', 'Pro', 29990, 287900,
             2999000, 330, 50, 500, 1500,
             1500, 30, 10, 'manager',
             '{"ai_assistant": true, "analytics": true, "smart_commenter": true, "parser": true, "warmup": true, "warmup_phases": 7, "comment_styles": 10, "comment_ab": true, "farm": true, "channel_map": true, "channel_map_ai": true, "content_factory": true, "content_factory_formats": 6, "mass_reactions": true, "user_parser": true, "folders": true, "weekly_reports": true, "neuro_chatting": true, "neuro_dialogs": true, "api_access": "read", "priority_support": true, "self_healing": true, "custom_styles": 3}'::jsonb,
             true, 3, NOW()),
            ('agency', 'Agency', 'Agency', 79990, 767900,
             7999000, 880, 200, 2000, 5000,
             5000, 100, 50, 'boss',
             '{"ai_assistant": true, "analytics": true, "smart_commenter": true, "parser": true, "warmup": true, "warmup_phases": 7, "comment_styles": 10, "comment_ab": true, "farm": true, "channel_map": true, "channel_map_ai": true, "content_factory": true, "content_factory_formats": 6, "mass_reactions": true, "user_parser": true, "folders": true, "weekly_reports": true, "neuro_chatting": true, "neuro_dialogs": true, "api_access": "full", "priority_support": true, "self_healing": true, "auto_purchase": true, "custom_styles": -1, "white_label": true, "multi_client": true, "max_client_workspaces": 50, "revenue_share": true}'::jsonb,
             true, 4, NOW()),
            ('enterprise', 'Enterprise', 'Enterprise', 0, 0,
             0, 0, 999999, 999999, 999999,
             999999, 999999, 999999, 'boss',
             '{"ai_assistant": true, "analytics": true, "smart_commenter": true, "parser": true, "warmup": true, "warmup_phases": 7, "comment_styles": 10, "comment_ab": true, "farm": true, "channel_map": true, "channel_map_ai": true, "content_factory": true, "content_factory_formats": 6, "mass_reactions": true, "user_parser": true, "folders": true, "weekly_reports": true, "neuro_chatting": true, "neuro_dialogs": true, "api_access": "full", "priority_support": true, "self_healing": true, "auto_purchase": true, "custom_styles": -1, "white_label": true, "multi_client": true, "revenue_share": true, "custom_sla": true, "dedicated_vps": true}'::jsonb,
             true, 5, NOW())
        ON CONFLICT (slug) DO UPDATE SET
            name = EXCLUDED.name,
            display_name = EXCLUDED.display_name,
            price_monthly_rub = EXCLUDED.price_monthly_rub,
            price_yearly_rub = EXCLUDED.price_yearly_rub,
            price_rub = EXCLUDED.price_rub,
            price_usd = EXCLUDED.price_usd,
            max_accounts = EXCLUDED.max_accounts,
            max_channels = EXCLUDED.max_channels,
            max_comments_per_day = EXCLUDED.max_comments_per_day,
            comments_per_day = EXCLUDED.comments_per_day,
            max_campaigns = EXCLUDED.max_campaigns,
            max_farms = EXCLUDED.max_farms,
            ai_tier = EXCLUDED.ai_tier,
            features = EXCLUDED.features,
            is_active = EXCLUDED.is_active,
            sort_order = EXCLUDED.sort_order
    """))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    # Remove payments table
    op.execute(sa.text("DROP TABLE IF EXISTS payments"))

    # Remove added plan columns
    for col in ("price_rub", "price_usd", "comments_per_day", "max_farms", "ai_tier", "display_name"):
        op.execute(sa.text(f"ALTER TABLE plans DROP COLUMN IF EXISTS {col}"))

    # Remove added subscription column
    op.execute(sa.text("ALTER TABLE subscriptions DROP COLUMN IF EXISTS cancel_reason"))

    # Disable FORCE RLS (leave RLS enabled with policies for safety)
    for table in ("subscriptions", "payment_events"):
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
