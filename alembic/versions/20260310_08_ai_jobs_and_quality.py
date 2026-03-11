"""AI stabilization jobs and quality telemetry

Revision ID: 20260310_08
Revises: 20260310_07
Create Date: 2026-03-10 23:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260310_08"
down_revision = "20260310_07"
branch_labels = None
depends_on = None


def _table_names(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(inspector: sa.Inspector, table_name: str, index_name: str, columns: list[str]) -> None:
    if index_name not in _index_names(inspector, table_name):
        op.create_index(index_name, table_name, columns)


def _enable_tenant_rls(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS {table_name}_isolation ON {table_name}"))
    tenant_scope = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::integer"
    op.execute(
        sa.text(
            f"""
            CREATE POLICY {table_name}_isolation
            ON {table_name}
            USING ({tenant_scope})
            WITH CHECK ({tenant_scope});
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)

    if "ai_requests" in existing_tables:
        ai_request_columns = {column["name"] for column in inspector.get_columns("ai_requests")}
        for column_name, column in (
            ("json_parse_failed", sa.Column("json_parse_failed", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("json_repair_applied", sa.Column("json_repair_applied", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("json_repair_strategy", sa.Column("json_repair_strategy", sa.String(length=64), nullable=True)),
            ("parsed_without_repair", sa.Column("parsed_without_repair", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("downgraded_by_budget_policy", sa.Column("downgraded_by_budget_policy", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("blocked_by_budget_policy", sa.Column("blocked_by_budget_policy", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("quality_score", sa.Column("quality_score", sa.Float(), nullable=True)),
        ):
            if column_name not in ai_request_columns:
                op.add_column("ai_requests", column)
        inspector = sa.inspect(bind)

    if "ai_request_attempts" in existing_tables:
        attempt_columns = {column["name"] for column in inspector.get_columns("ai_request_attempts")}
        for column_name, column in (
            ("json_parse_failed", sa.Column("json_parse_failed", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("json_repair_applied", sa.Column("json_repair_applied", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("json_repair_strategy", sa.Column("json_repair_strategy", sa.String(length=64), nullable=True)),
            ("parsed_without_repair", sa.Column("parsed_without_repair", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
        ):
            if column_name not in attempt_columns:
                op.add_column("ai_request_attempts", column)
        inspector = sa.inspect(bind)

    if "app_jobs" not in existing_tables:
        op.create_table(
            "app_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=True),
            sa.Column("job_type", sa.String(length=64), nullable=False),
            sa.Column("queue_name", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("result_summary", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("app_jobs")
        inspector = sa.inspect(bind)

    for table_name, index_name, columns in (
        ("app_jobs", "ix_app_jobs_tenant_id", ["tenant_id"]),
        ("app_jobs", "ix_app_jobs_workspace_id", ["workspace_id"]),
        ("app_jobs", "ix_app_jobs_job_type", ["job_type"]),
        ("app_jobs", "ix_app_jobs_status", ["status"]),
        ("app_jobs", "ix_app_jobs_created_at", ["created_at"]),
    ):
        if table_name in existing_tables:
            _create_index_if_missing(sa.inspect(bind), table_name, index_name, columns)

    if bind.dialect.name == "postgresql" and "app_jobs" in existing_tables:
        _enable_tenant_rls("app_jobs")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)

    if bind.dialect.name == "postgresql" and "app_jobs" in existing_tables:
        op.execute(sa.text("DROP POLICY IF EXISTS app_jobs_isolation ON app_jobs"))

    if "app_jobs" in existing_tables:
        op.drop_table("app_jobs")

    if "ai_request_attempts" in existing_tables:
        for column_name in (
            "parsed_without_repair",
            "json_repair_strategy",
            "json_repair_applied",
            "json_parse_failed",
        ):
            columns = {column["name"] for column in sa.inspect(bind).get_columns("ai_request_attempts")}
            if column_name in columns:
                op.drop_column("ai_request_attempts", column_name)

    if "ai_requests" in existing_tables:
        for column_name in (
            "quality_score",
            "blocked_by_budget_policy",
            "downgraded_by_budget_policy",
            "parsed_without_repair",
            "json_repair_strategy",
            "json_repair_applied",
            "json_parse_failed",
        ):
            columns = {column["name"] for column in sa.inspect(bind).get_columns("ai_requests")}
            if column_name in columns:
                op.drop_column("ai_requests", column_name)
