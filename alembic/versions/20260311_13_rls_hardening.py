"""Harden RLS on refresh_tokens, accounts, and proxies tables

Revision ID: sprint5_rls_hardening
Revises: 20260311_12
Create Date: 2026-03-11 17:00:00

Applies tenant isolation policies to three tables that carry sensitive
per-tenant data but were not covered by earlier RLS migrations:

  - refresh_tokens  — web auth session credentials
  - accounts        — Telegram accounts owned per tenant/workspace
  - proxies         — proxy records owned per tenant/workspace

All three tables already have a tenant_id FK column. The policy uses the
standard NULLIF guard so that a missing app.tenant_id session variable
evaluates to NULL and blocks all rows rather than returning everything.

Skipped automatically on SQLite (dialect guard).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "sprint5_rls_hardening"
down_revision = "20260311_12"
branch_labels = None
depends_on = None


_TENANT_SCOPE = (
    "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::integer"
)


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_isolation ON {table}"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY {table}_isolation
            ON {table}
            USING ({_TENANT_SCOPE})
            WITH CHECK ({_TENANT_SCOPE});
            """
        )
    )


def _disable_rls(table: str) -> None:
    op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_isolation ON {table}"))
    op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in ("refresh_tokens", "accounts", "proxies"):
        _enable_rls(table)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in ("refresh_tokens", "accounts", "proxies"):
        _disable_rls(table)
