"""Sprint 22: Parsing v2 — group_parsing_jobs, message_parsing_jobs, message_parsing_results, parsing_templates."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260314_40"
down_revision = "20260314_39"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(JSONB, "postgresql")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_workspace_isolation ON {table} "
        f"USING (workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::int) "
        f"WITH CHECK (workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::int)"
    )


def _rls_system_visible(table: str) -> None:
    """RLS policy that allows reading system templates (workspace_id IS NULL) plus own workspace rows."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_workspace_isolation ON {table} "
        f"USING ("
        f"  workspace_id IS NULL "
        f"  OR workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::int"
        f") "
        f"WITH CHECK ("
        f"  workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::int"
        f")"
    )


def upgrade():
    # ── group_parsing_jobs ──
    op.create_table(
        "group_parsing_jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("keywords", JSONType, nullable=False),
        sa.Column("status", sa.String(16), server_default="pending"),
        sa.Column("filters", JSONType, nullable=True),
        sa.Column("results_count", sa.Integer, server_default="0"),
        sa.Column("progress", sa.Integer, server_default="0"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_group_parsing_jobs_workspace_id", "group_parsing_jobs", ["workspace_id"])
    op.create_index("ix_group_parsing_jobs_status", "group_parsing_jobs", ["status"])
    _rls("group_parsing_jobs")

    # ── message_parsing_jobs ──
    op.create_table(
        "message_parsing_jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("keywords", JSONType, nullable=True),
        sa.Column("date_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), server_default="pending"),
        sa.Column("results_count", sa.Integer, server_default="0"),
        sa.Column("progress", sa.Integer, server_default="0"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_message_parsing_jobs_workspace_id", "message_parsing_jobs", ["workspace_id"])
    op.create_index("ix_message_parsing_jobs_status", "message_parsing_jobs", ["status"])
    _rls("message_parsing_jobs")

    # ── message_parsing_results ──
    op.create_table(
        "message_parsing_results",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=False),
        sa.Column("job_id", sa.Integer, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("first_name", sa.String(128), nullable=True),
        sa.Column("message_text", sa.Text, nullable=True),
        sa.Column("message_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("channel_id", sa.BigInteger, nullable=True),
        sa.Column("channel_title", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_message_parsing_results_workspace_id", "message_parsing_results", ["workspace_id"])
    op.create_index("ix_message_parsing_results_job_id", "message_parsing_results", ["job_id"])
    _rls("message_parsing_results")

    # ── parsing_templates ──
    op.create_table(
        "parsing_templates",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("keywords", JSONType, nullable=False),
        sa.Column("filters", JSONType, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_system", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_parsing_templates_workspace_id", "parsing_templates", ["workspace_id"])
    op.create_index("ix_parsing_templates_category", "parsing_templates", ["category"])
    _rls_system_visible("parsing_templates")

    # ── Seed system templates ──
    op.execute("""
        INSERT INTO parsing_templates (workspace_id, name, category, keywords, filters, description, is_system) VALUES
        (NULL, 'Crypto / Web3', 'crypto', '["крипто","биткоин","эфириум","nft","defi","web3","трейдинг","альткоин"]'::jsonb, NULL, 'Крипто и Web3 каналы', true),
        (NULL, 'Lead Generation', 'lead_gen', '["лиды","продажи","клиенты","b2b","CRM","конверсия","лидогенерация"]'::jsonb, NULL, 'Каналы про лиды и продажи', true),
        (NULL, 'Programming', 'programming', '["python","javascript","разработка","devops","backend","frontend","программирование"]'::jsonb, NULL, 'Каналы для разработчиков', true),
        (NULL, 'SMM / Marketing', 'smm', '["smm","маркетинг","реклама","таргет","продвижение","контент","бренд"]'::jsonb, NULL, 'Маркетинг и SMM каналы', true),
        (NULL, 'E-commerce', 'ecommerce', '["интернет-магазин","маркетплейс","wildberries","ozon","товары","dropshipping"]'::jsonb, NULL, 'E-commerce и маркетплейсы', true),
        (NULL, 'Education', 'education', '["курсы","обучение","онлайн-школа","образование","вебинар","менторство"]'::jsonb, NULL, 'Образование и курсы', true)
    """)


def downgrade():
    op.drop_table("parsing_templates")
    op.drop_table("message_parsing_results")
    op.drop_table("message_parsing_jobs")
    op.drop_table("group_parsing_jobs")
