"""Sprint 15 Task B — Agency Package: agencies, agency_clients, agency_invites tables with RLS.

Revision ID: 20260314_34
Revises: 20260314_33
"""
from alembic import op
import sqlalchemy as sa

revision = "20260314_34"
down_revision = "20260314_33"
branch_labels = None
depends_on = None

# RLS policy expression shared across all three tables.
# Rows visible only when their owning tenant matches the session-local tenant_id.
_RLS_EXPR = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::integer"

# For agency_clients / agency_invites we resolve tenant_id through the agencies table.
_RLS_CLIENTS_EXPR = (
    "agency_id IN ("
    "  SELECT id FROM agencies"
    "  WHERE tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::integer"
    ")"
)
_RLS_INVITES_EXPR = _RLS_CLIENTS_EXPR  # same join path


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    # ------------------------------------------------------------------
    # agencies
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS agencies (
            id                  SERIAL PRIMARY KEY,
            tenant_id           INTEGER NOT NULL REFERENCES tenants(id) UNIQUE,
            name                VARCHAR(255) NOT NULL,
            slug                VARCHAR(100) NOT NULL UNIQUE,
            custom_logo_url     VARCHAR(500),
            custom_brand_name   VARCHAR(255),
            custom_accent_color VARCHAR(7),
            custom_domain       VARCHAR(255),
            revenue_share_pct   FLOAT NOT NULL DEFAULT 20.0,
            max_clients         INTEGER NOT NULL DEFAULT 50,
            is_active           BOOLEAN NOT NULL DEFAULT TRUE,
            created_at          TIMESTAMPTZ DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_agencies_tenant_id ON agencies (tenant_id)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_agencies_slug ON agencies (slug)"
    ))
    op.execute(sa.text("ALTER TABLE agencies ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE agencies FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS agencies_isolation ON agencies"))
    op.execute(sa.text(f"""
        CREATE POLICY agencies_isolation
        ON agencies
        USING ({_RLS_EXPR})
        WITH CHECK ({_RLS_EXPR})
    """))

    # ------------------------------------------------------------------
    # agency_clients
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS agency_clients (
            id                   SERIAL PRIMARY KEY,
            agency_id            INTEGER NOT NULL REFERENCES agencies(id),
            client_tenant_id     INTEGER NOT NULL REFERENCES tenants(id) UNIQUE,
            client_name          VARCHAR(255) NOT NULL,
            client_contact_email VARCHAR(255),
            status               VARCHAR(50) NOT NULL DEFAULT 'active',
            notes                TEXT,
            total_revenue_rub    FLOAT NOT NULL DEFAULT 0.0,
            agency_earned_rub    FLOAT NOT NULL DEFAULT 0.0,
            created_at           TIMESTAMPTZ DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_agency_clients_agency_id ON agency_clients (agency_id)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_agency_clients_client_tenant_id ON agency_clients (client_tenant_id)"
    ))
    op.execute(sa.text("ALTER TABLE agency_clients ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE agency_clients FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS agency_clients_isolation ON agency_clients"))
    op.execute(sa.text(f"""
        CREATE POLICY agency_clients_isolation
        ON agency_clients
        USING ({_RLS_CLIENTS_EXPR})
        WITH CHECK ({_RLS_CLIENTS_EXPR})
    """))

    # ------------------------------------------------------------------
    # agency_invites
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS agency_invites (
            id           SERIAL PRIMARY KEY,
            agency_id    INTEGER NOT NULL REFERENCES agencies(id),
            invite_code  VARCHAR(64) NOT NULL UNIQUE,
            client_email VARCHAR(255),
            max_uses     INTEGER NOT NULL DEFAULT 1,
            used_count   INTEGER NOT NULL DEFAULT 0,
            expires_at   TIMESTAMPTZ,
            created_at   TIMESTAMPTZ DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_agency_invites_agency_id ON agency_invites (agency_id)"
    ))
    op.execute(sa.text("ALTER TABLE agency_invites ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE agency_invites FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS agency_invites_isolation ON agency_invites"))
    op.execute(sa.text(f"""
        CREATE POLICY agency_invites_isolation
        ON agency_invites
        USING ({_RLS_INVITES_EXPR})
        WITH CHECK ({_RLS_INVITES_EXPR})
    """))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    op.execute(sa.text("DROP TABLE IF EXISTS agency_invites"))
    op.execute(sa.text("DROP TABLE IF EXISTS agency_clients"))
    op.execute(sa.text("DROP TABLE IF EXISTS agencies"))
