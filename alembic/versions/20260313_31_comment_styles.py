"""Add workspace_id, system_prompt, examples to comment_style_templates; enable FORCE RLS.

Revision ID: 20260313_31
Revises: 20260313_30
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260313_31"
down_revision = "20260313_30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add new columns to comment_style_templates
    op.add_column(
        "comment_style_templates",
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=True),
    )
    op.add_column(
        "comment_style_templates",
        sa.Column("system_prompt", sa.Text(), nullable=True),
    )
    op.add_column(
        "comment_style_templates",
        sa.Column("examples", JSONB(), nullable=True),
    )

    # 2. Index on workspace_id for tenant-scoped queries
    op.create_index(
        "ix_comment_style_templates_workspace_id",
        "comment_style_templates",
        ["workspace_id"],
    )

    # 3. Enable RLS on the table (may already have a policy from earlier migration;
    #    this ensures FORCE RLS is active so the app role cannot bypass it)
    op.execute("ALTER TABLE comment_style_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE comment_style_templates FORCE ROW LEVEL SECURITY")

    # 4. Create RLS policy if it doesn't already exist (idempotent via DO block)
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'comment_style_templates'
                  AND policyname = 'tenant_isolation'
            ) THEN
                CREATE POLICY tenant_isolation ON comment_style_templates
                    USING (tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::integer)
                    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::integer);
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Remove the RLS policy we may have created (leave FORCE RLS alone —
    # reverting security settings is intentionally omitted)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'comment_style_templates'
                  AND policyname = 'tenant_isolation'
            ) THEN
                DROP POLICY tenant_isolation ON comment_style_templates;
            END IF;
        END
        $$;
        """
    )

    op.drop_index("ix_comment_style_templates_workspace_id", table_name="comment_style_templates")
    op.drop_column("comment_style_templates", "examples")
    op.drop_column("comment_style_templates", "system_prompt")
    op.drop_column("comment_style_templates", "workspace_id")
