"""AI orchestrator foundation tables

Revision ID: 20260310_07
Revises: 20260310_06
Create Date: 2026-03-10 17:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260310_07"
down_revision = "20260310_06"
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

    if "ai_model_profiles" not in existing_tables:
        op.create_table(
            "ai_model_profiles",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("model_name", sa.String(length=255), nullable=False),
            sa.Column("model_tier", sa.String(length=20), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("input_cost_per_1m", sa.Float(), nullable=False, server_default="0"),
            sa.Column("output_cost_per_1m", sa.Float(), nullable=False, server_default="0"),
            sa.Column("max_context_tokens", sa.Integer(), nullable=True),
            sa.Column("capabilities", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("provider", "model_name", name="uq_ai_model_profiles_provider_model"),
        )
        existing_tables.add("ai_model_profiles")
        inspector = sa.inspect(bind)

    if "ai_task_policies" not in existing_tables:
        op.create_table(
            "ai_task_policies",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=True),
            sa.Column("task_type", sa.String(length=64), nullable=False),
            sa.Column("requested_model_tier", sa.String(length=20), nullable=False),
            sa.Column("allow_downgrade", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("output_contract_type", sa.String(length=32), nullable=False, server_default="json_object"),
            sa.Column("latency_target_ms", sa.Integer(), nullable=True),
            sa.Column("max_budget_usd", sa.Float(), nullable=True),
            sa.Column("policy", sa.JSON(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("tenant_id", "task_type", name="uq_ai_task_policies_tenant_task"),
        )
        existing_tables.add("ai_task_policies")
        inspector = sa.inspect(bind)

    if "ai_requests" not in existing_tables:
        op.create_table(
            "ai_requests",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=True),
            sa.Column("surface", sa.String(length=64), nullable=False),
            sa.Column("task_type", sa.String(length=64), nullable=False),
            sa.Column("agent_name", sa.String(length=64), nullable=False),
            sa.Column("requested_model_tier", sa.String(length=20), nullable=False),
            sa.Column("executed_model_tier", sa.String(length=20), nullable=True),
            sa.Column("requested_provider", sa.String(length=32), nullable=True),
            sa.Column("executed_provider", sa.String(length=32), nullable=True),
            sa.Column("executed_model", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("outcome", sa.String(length=32), nullable=False, server_default="executed_as_requested"),
            sa.Column("output_contract_type", sa.String(length=32), nullable=False, server_default="json_object"),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("reason_code", sa.String(length=64), nullable=True),
            sa.Column("quality_flags", sa.JSON(), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )
        existing_tables.add("ai_requests")
        inspector = sa.inspect(bind)

    if "ai_request_attempts" not in existing_tables:
        op.create_table(
            "ai_request_attempts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("ai_request_id", sa.Integer(), sa.ForeignKey("ai_requests.id"), nullable=False),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("model_name", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("reason_code", sa.String(length=64), nullable=True),
            sa.Column("response_meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("ai_request_attempts")
        inspector = sa.inspect(bind)

    if "ai_budget_limits" not in existing_tables:
        op.create_table(
            "ai_budget_limits",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("daily_budget_usd", sa.Float(), nullable=True),
            sa.Column("monthly_budget_usd", sa.Float(), nullable=True),
            sa.Column("boss_daily_budget_usd", sa.Float(), nullable=True),
            sa.Column("hard_stop_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("tenant_id", name="uq_ai_budget_limits_tenant_id"),
        )
        existing_tables.add("ai_budget_limits")
        inspector = sa.inspect(bind)

    if "ai_budget_counters" not in existing_tables:
        op.create_table(
            "ai_budget_counters",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("period_type", sa.String(length=16), nullable=False),
            sa.Column("period_start", sa.DateTime(), nullable=False),
            sa.Column("model_tier", sa.String(length=20), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint(
                "tenant_id",
                "period_type",
                "period_start",
                "model_tier",
                "provider",
                name="uq_ai_budget_counters_scope",
            ),
        )
        existing_tables.add("ai_budget_counters")
        inspector = sa.inspect(bind)

    if "ai_escalations" not in existing_tables:
        op.create_table(
            "ai_escalations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=True),
            sa.Column("ai_request_id", sa.Integer(), sa.ForeignKey("ai_requests.id"), nullable=True),
            sa.Column("task_type", sa.String(length=64), nullable=False),
            sa.Column("from_tier", sa.String(length=20), nullable=True),
            sa.Column("to_tier", sa.String(length=20), nullable=True),
            sa.Column("trigger_type", sa.String(length=32), nullable=False),
            sa.Column("reason_code", sa.String(length=64), nullable=True),
            sa.Column("approved_by_user", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("ai_escalations")
        inspector = sa.inspect(bind)

    if "ai_agent_runs" not in existing_tables:
        op.create_table(
            "ai_agent_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("auth_users.id"), nullable=True),
            sa.Column("ai_request_id", sa.Integer(), sa.ForeignKey("ai_requests.id"), nullable=True),
            sa.Column("agent_name", sa.String(length=64), nullable=False),
            sa.Column("task_type", sa.String(length=64), nullable=False),
            sa.Column("requested_model_tier", sa.String(length=20), nullable=False),
            sa.Column("executed_model_tier", sa.String(length=20), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        existing_tables.add("ai_agent_runs")
        inspector = sa.inspect(bind)

    for table_name, index_name, columns in (
        ("ai_model_profiles", "ix_ai_model_profiles_provider", ["provider"]),
        ("ai_model_profiles", "ix_ai_model_profiles_tier", ["model_tier"]),
        ("ai_task_policies", "ix_ai_task_policies_tenant_id", ["tenant_id"]),
        ("ai_task_policies", "ix_ai_task_policies_task_type", ["task_type"]),
        ("ai_requests", "ix_ai_requests_tenant_id", ["tenant_id"]),
        ("ai_requests", "ix_ai_requests_workspace_id", ["workspace_id"]),
        ("ai_requests", "ix_ai_requests_task_type", ["task_type"]),
        ("ai_requests", "ix_ai_requests_created_at", ["created_at"]),
        ("ai_request_attempts", "ix_ai_request_attempts_ai_request_id", ["ai_request_id"]),
        ("ai_request_attempts", "ix_ai_request_attempts_tenant_id", ["tenant_id"]),
        ("ai_budget_counters", "ix_ai_budget_counters_tenant_id", ["tenant_id"]),
        ("ai_budget_counters", "ix_ai_budget_counters_period_start", ["period_start"]),
        ("ai_escalations", "ix_ai_escalations_tenant_id", ["tenant_id"]),
        ("ai_escalations", "ix_ai_escalations_ai_request_id", ["ai_request_id"]),
        ("ai_agent_runs", "ix_ai_agent_runs_tenant_id", ["tenant_id"]),
        ("ai_agent_runs", "ix_ai_agent_runs_ai_request_id", ["ai_request_id"]),
    ):
        if table_name in existing_tables:
            _create_index_if_missing(sa.inspect(bind), table_name, index_name, columns)

    if bind.dialect.name == "postgresql":
        for table_name in (
            "ai_task_policies",
            "ai_requests",
            "ai_request_attempts",
            "ai_budget_limits",
            "ai_budget_counters",
            "ai_escalations",
            "ai_agent_runs",
        ):
            _enable_tenant_rls(table_name)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = _table_names(inspector)

    for table_name in (
        "ai_agent_runs",
        "ai_escalations",
        "ai_budget_counters",
        "ai_budget_limits",
        "ai_request_attempts",
        "ai_requests",
        "ai_task_policies",
        "ai_model_profiles",
    ):
        if table_name in existing_tables:
            op.drop_table(table_name)
