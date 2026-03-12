"""Add comment quality tables: comment_style_templates, comment_ab_results.

Revision ID: 20260312_20
Revises: 20260312_19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260312_20"
down_revision = "20260312_19"
branch_labels = None
depends_on = None

# JSON/JSONB column type helper (same pattern used in the rest of the project)
_JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _table_names(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(
    inspector: sa.Inspector,
    table_name: str,
    index_name: str,
    columns: list[str],
    unique: bool = False,
) -> None:
    if index_name not in _index_names(inspector, table_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def _enable_tenant_rls(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))


def _create_tenant_rls_policy(table_name: str) -> None:
    policy_name = f"tenant_isolation_{table_name}"
    op.execute(
        sa.text(
            f"CREATE POLICY {policy_name} ON {table_name} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::integer)"
        )
    )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = _table_names(inspector)

    # ── comment_style_templates ────────────────────────────────────────────────
    if "comment_style_templates" not in existing_tables:
        op.create_table(
            "comment_style_templates",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("template_pattern", sa.Text, nullable=True),
            sa.Column("tone", sa.String(50), nullable=True),
            sa.Column("is_active", sa.Boolean, default=True, nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime, nullable=True, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime, nullable=True, server_default=sa.func.now()),
        )
        inspector = sa.inspect(op.get_bind())

    _create_index_if_missing(inspector, "comment_style_templates", "ix_comment_style_templates_tenant_id", ["tenant_id"])
    _create_index_if_missing(inspector, "comment_style_templates", "ix_comment_style_templates_is_active", ["is_active"])

    # RLS
    _enable_tenant_rls("comment_style_templates")
    _create_tenant_rls_policy("comment_style_templates")

    # ── comment_ab_results ─────────────────────────────────────────────────────
    if "comment_ab_results" not in existing_tables:
        op.create_table(
            "comment_ab_results",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False),
            # Optional FK to farm_events (comment_sent event) — nullable to allow
            # standalone preview-driven records.
            sa.Column("farm_event_id", sa.Integer, sa.ForeignKey("farm_events.id"), nullable=True),
            sa.Column("style_name", sa.String(100), nullable=False),
            sa.Column("tone", sa.String(50), nullable=True),
            # Channel may be tracked by username or channel_entry FK
            sa.Column("channel_username", sa.String(100), nullable=True),
            sa.Column("channel_entry_id", sa.Integer, sa.ForeignKey("channel_entries.id"), nullable=True),
            sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=True),
            sa.Column("reactions_count", sa.Integer, default=0, nullable=False, server_default="0"),
            sa.Column("replies_count", sa.Integer, default=0, nullable=False, server_default="0"),
            sa.Column("was_deleted", sa.Boolean, default=False, nullable=False, server_default="false"),
            sa.Column("posted_at", sa.DateTime, nullable=True),
            sa.Column("measured_at", sa.DateTime, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=True, server_default=sa.func.now()),
        )
        inspector = sa.inspect(op.get_bind())

    _create_index_if_missing(inspector, "comment_ab_results", "ix_comment_ab_results_tenant_id", ["tenant_id"])
    _create_index_if_missing(inspector, "comment_ab_results", "ix_comment_ab_results_style_name", ["style_name"])
    _create_index_if_missing(inspector, "comment_ab_results", "ix_comment_ab_results_posted_at", ["posted_at"])
    _create_index_if_missing(inspector, "comment_ab_results", "ix_comment_ab_results_account_id", ["account_id"])

    # RLS
    _enable_tenant_rls("comment_ab_results")
    _create_tenant_rls_policy("comment_ab_results")

    # ── Add quality_score columns to channel_profiles if missing ──────────────
    inspector = sa.inspect(op.get_bind())
    existing_cols = {c["name"] for c in inspector.get_columns("channel_profiles")}

    if "quality_score" not in existing_cols:
        op.add_column("channel_profiles", sa.Column("quality_score", sa.Float, nullable=True))
    if "quality_scored_at" not in existing_cols:
        op.add_column("channel_profiles", sa.Column("quality_scored_at", sa.DateTime, nullable=True))

    # ── Seed default style templates for every existing tenant ─────────────────
    # We use raw SQL to avoid ORM dependency in migrations.
    # This is optional data seeding — skip if already exists.
    op.execute(sa.text("""
        INSERT INTO comment_style_templates
            (tenant_id, name, description, template_pattern, tone, is_active)
        SELECT
            t.id,
            s.name,
            s.description,
            s.template_pattern,
            s.tone,
            true
        FROM tenants t
        CROSS JOIN (
            VALUES
              ('question',      'Вопрос',           'Задаёт уточняющий вопрос по теме',                'Интересно, а {{angle}}?',                           'positive'),
              ('agree',         'Согласие',          'Выражает согласие с автором',                     'Полностью согласен. {{point}}.',                    'positive'),
              ('supplement',    'Дополнение',        'Добавляет факт или аргумент',                     'Ещё добавлю: {{point}}.',                           'expert'),
              ('joke',          'Шутка',             'Лёгкая ирония или шутка',                         '{{point}} 😄 Ну и дела...',                         'witty'),
              ('expert',        'Экспертное мнение', 'Профессиональная оценка с конкретным фактом',     'Из практики: {{point}}.',                           'expert'),
              ('personal',      'Личный опыт',       'Личная история от первого лица',                  'У меня был похожий опыт: {{point}}.',               'emotional'),
              ('quote',         'Цитата из поста',   'Цитирует фрагмент и комментирует',                '"{{point}}" — именно! Потому что...',               'positive'),
              ('emoji',         'Эмодзи-реакция',    'Короткая эмоциональная реакция с эмодзи',         '{{tone_emoji}} {{point}}',                          'emotional'),
              ('controversial', 'Спорное мнение',    'Мягкое несогласие / провокация мысли',            'Хм, а вы уверены что {{point}}? Спорно.',           'hater'),
              ('gratitude',     'Благодарность',     'Благодарит автора за информацию',                 'Спасибо за пост! {{point}} — именно то, что нужно.','positive')
        ) AS s(name, description, template_pattern, tone)
        WHERE NOT EXISTS (
            SELECT 1 FROM comment_style_templates cst
            WHERE cst.tenant_id = t.id AND cst.name = s.name
        )
    """))


def downgrade() -> None:
    # Drop tables in reverse dependency order
    for table in ("comment_ab_results", "comment_style_templates"):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
