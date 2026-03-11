"""
ORM модели для SQLite базы данных.
"""

from datetime import datetime

from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, Boolean, DateTime, Text, ForeignKey,
    UniqueConstraint, JSON, Index,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.dialects.postgresql import JSONB

from utils.helpers import utcnow


JSONType = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class AuthUser(Base):
    """Минимальный auth principal для SaaS JWT."""
    __tablename__ = "auth_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(BigInteger, unique=True, nullable=True)
    telegram_username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    email = Column(String(255), unique=True, nullable=True)
    company = Column(String(255), nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class Tenant(Base):
    """SaaS tenant."""
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(120), unique=True, nullable=False)
    status = Column(String(20), default="active")  # active, suspended
    created_at = Column(DateTime, default=utcnow)


class Workspace(Base):
    """Workspace внутри tenant."""
    __tablename__ = "workspaces"
    __table_args__ = (
        Index("ix_workspaces_tenant_id", "tenant_id"),
        Index("ix_workspaces_runtime_user_id", "runtime_user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    settings = Column(JSONType, nullable=True)
    runtime_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)


class TeamMember(Base):
    """Участник workspace/team."""
    __tablename__ = "team_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_team_members_workspace_user"),
        Index("ix_team_members_tenant_id", "tenant_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=False)
    role = Column(String(20), default="member")  # owner, admin, member
    created_at = Column(DateTime, default=utcnow)


class UsageEvent(Base):
    """Skeleton usage metering/event log."""
    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_tenant_id", "tenant_id"),
        Index("ix_usage_events_event_type", "event_type"),
        Index("ix_usage_events_tenant_created_at", "tenant_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    event_type = Column(String(120), nullable=False)
    meta = Column(JSONType, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class RefreshToken(Base):
    """Хранилище refresh token ротации для web auth."""
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_tenant_id", "tenant_id"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    token_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    last_used_at = Column(DateTime, nullable=True)
    user_agent = Column(String(500), nullable=True)
    ip_address = Column(String(128), nullable=True)


class Lead(Base):
    """Публичный маркетинговый лид до регистрации в SaaS."""
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_email", "email"),
        Index("ix_leads_created_at", "created_at"),
        Index("ix_leads_utm_source", "utm_source"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    company = Column(String(255), nullable=False)
    telegram_username = Column(String(255), nullable=True)
    use_case = Column(String(64), nullable=False)
    utm_source = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utcnow)


class User(Base):
    """Пользователь SaaS-платформы."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(100), nullable=True)
    first_name = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    # Per-user product settings
    product_name = Column(String(100), default="")
    product_bot_link = Column(String(300), default="")
    product_bot_username = Column(String(100), default="")
    product_avatar_path = Column(String(300), default="")
    product_short_desc = Column(String(300), default="")
    product_features = Column(String(500), default="")
    product_category = Column(String(20), default="VPN")
    product_channel_prefix = Column(String(50), default="")
    scenario_b_ratio = Column(Float, default=0.3)
    max_daily_comments = Column(Integer, default=35)
    min_delay = Column(Integer, default=120)
    max_delay = Column(Integer, default=600)
    max_accounts = Column(Integer, default=3)
    created_at = Column(DateTime, default=utcnow)
    last_active_at = Column(DateTime, default=utcnow)

    accounts = relationship("Account", back_populates="user")
    channels = relationship("Channel", back_populates="user")
    proxies = relationship("Proxy", back_populates="user")


class Account(Base):
    """Telegram аккаунт для комментирования."""
    __tablename__ = "accounts"
    __table_args__ = (
        Index("ix_accounts_tenant_id", "tenant_id"),
        Index("ix_accounts_workspace_id", "workspace_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(20), unique=True, nullable=False)
    session_file = Column(String(255), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=True)
    proxy_id = Column(Integer, ForeignKey("proxies.id"), nullable=True)
    status = Column(String(20), default="active")  # active, cooldown, banned, flood_wait
    cooldown_until = Column(DateTime, nullable=True)
    comments_today = Column(Integer, default=0)
    total_comments = Column(Integer, default=0)
    days_active = Column(Integer, default=0)  # для прогрева
    persona_style = Column(String(50), default="casual")  # casual, formal, slang, tech
    channel_link = Column(String(500), nullable=True)  # Ссылка на канал-переходник аккаунта
    api_id = Column(Integer, nullable=True)  # API ID, с которым создана сессия
    health_status = Column(String(20), default="unknown")  # unknown, alive, dead, expired
    lifecycle_stage = Column(String(20), default="uploaded")
    # Values: uploaded, packaging, warming_up, gate_review, active_commenting, restricted, packaging_error
    account_role = Column(String(32), default="comment_candidate")
    # Values: parser_candidate, parser_active, comment_candidate, execution_ready, needs_attention
    risk_score = Column(Float, default=0.0)
    risk_level = Column(String(20), default="low")  # low, medium, high, critical
    last_violation_at = Column(DateTime, nullable=True)
    violation_count_24h = Column(Integer, default=0)
    quarantined_until = Column(DateTime, nullable=True)
    restriction_reason = Column(String(64), nullable=True)  # frozen, restricted, duplicate_session, etc.
    last_probe_at = Column(DateTime, nullable=True)
    capabilities_json = Column(Text, nullable=True)  # serialized capability probe result
    last_health_check = Column(DateTime, nullable=True)
    session_backup_at = Column(DateTime, nullable=True)
    account_age_days = Column(Integer, default=0)  # Возраст аккаунта (из register_time)
    manual_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    last_active_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="accounts")
    proxy = relationship("Proxy", back_populates="accounts")
    comments = relationship("Comment", back_populates="account")


class PolicyEvent(Base):
    """События compliance policy engine."""
    __tablename__ = "policy_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(String(32), nullable=False)
    event_name = Column(String(64), nullable=False)
    decision = Column(String(20), nullable=False)  # allow|warn|block|quarantine
    severity = Column(String(20), nullable=False)  # low|medium|high|critical
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    phone = Column(String(20), nullable=True)
    worker_id = Column(String(64), nullable=True)
    details_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class AccountRiskState(Base):
    """Агрегированное риск-состояние аккаунта."""
    __tablename__ = "account_risk_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, unique=True)
    phone = Column(String(20), nullable=False)
    risk_score = Column(Float, default=0.0)
    risk_level = Column(String(20), default="low")
    violation_count_24h = Column(Integer, default=0)
    last_violation_at = Column(DateTime, nullable=True)
    quarantined_until = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utcnow)


class AccountStageEvent(Base):
    """История переходов lifecycle_stage."""
    __tablename__ = "account_stage_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    phone = Column(String(20), nullable=False)
    from_stage = Column(String(32), nullable=True)
    to_stage = Column(String(32), nullable=False)
    actor = Column(String(64), default="system")
    reason = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=utcnow)


class AccountOnboardingRun(Base):
    """Один пошаговый цикл настройки аккаунта."""
    __tablename__ = "account_onboarding_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    phone = Column(String(20), nullable=False)
    status = Column(String(20), default="active")  # active, paused, completed, cancelled
    mode = Column(String(20), default="bot")  # bot, cli
    source_channel = Column(String(32), default="bot")
    current_step = Column(String(64), default="start")
    last_result = Column(String(32), default="pending")
    notes = Column(Text, nullable=True)
    started_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)
    completed_at = Column(DateTime, nullable=True)


class AccountOnboardingStep(Base):
    """Журнал шагов ручной или bot-first настройки аккаунта."""
    __tablename__ = "account_onboarding_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("account_onboarding_runs.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    phone = Column(String(20), nullable=False)
    step_key = Column(String(64), nullable=False)
    actor = Column(String(64), default="system")
    source = Column(String(32), default="bot")
    channel = Column(String(32), default="bot")
    result = Column(String(32), default="ok")
    notes = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class AccountDraftArtifact(Base):
    """Черновики и подтверждённые apply-артефакты для human-gated workflow."""
    __tablename__ = "account_draft_artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    phone = Column(String(20), nullable=False)
    artifact_kind = Column(String(32), nullable=False)
    # profile, channel, content, comment, reply
    status = Column(String(20), default="draft")
    # draft, reviewed, approved, applied, skipped, failed
    selected_variant = Column(Integer, default=0)
    payload_json = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class BusinessBrief(Base):
    """Базовый growth-brief клиента для AI assistant layer."""
    __tablename__ = "business_briefs"
    __table_args__ = (
        Index("ix_business_briefs_tenant_id", "tenant_id"),
        Index("ix_business_briefs_workspace_id", "workspace_id"),
        UniqueConstraint("tenant_id", "workspace_id", name="uq_business_briefs_tenant_workspace"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=True)
    status = Column(String(20), default="draft")  # draft, confirmed, archived
    product_name = Column(String(255), nullable=True)
    offer_summary = Column(Text, nullable=True)
    target_audience = Column(Text, nullable=True)
    competitors = Column(JSONType, nullable=True)
    tone_of_voice = Column(String(255), nullable=True)
    pain_points = Column(JSONType, nullable=True)
    telegram_goals = Column(JSONType, nullable=True)
    website_url = Column(String(500), nullable=True)
    channel_url = Column(String(500), nullable=True)
    bot_url = Column(String(500), nullable=True)
    summary_text = Column(Text, nullable=True)
    completeness_score = Column(Float, default=0.0)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class BusinessAsset(Base):
    """Ссылки и approved assets, связанные с business brief."""
    __tablename__ = "business_assets"
    __table_args__ = (
        Index("ix_business_assets_tenant_id", "tenant_id"),
        Index("ix_business_assets_workspace_id", "workspace_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    brief_id = Column(Integer, ForeignKey("business_briefs.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=True)
    asset_type = Column(String(32), nullable=False)  # website, channel, bot, image_prompt, visual, doc
    title = Column(String(255), nullable=False)
    value = Column(Text, nullable=True)
    meta = Column(JSONType, nullable=True)
    status = Column(String(20), default="active")  # active, archived
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class AssistantThread(Base):
    """Диалоговый поток AI-ассистента внутри workspace."""
    __tablename__ = "assistant_threads"
    __table_args__ = (
        Index("ix_assistant_threads_tenant_id", "tenant_id"),
        Index("ix_assistant_threads_workspace_id", "workspace_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=True)
    brief_id = Column(Integer, ForeignKey("business_briefs.id"), nullable=True)
    thread_kind = Column(String(32), default="growth_brief")
    status = Column(String(20), default="active")  # active, paused, archived
    title = Column(String(255), nullable=True)
    last_step = Column(String(64), default="start_brief")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class AssistantMessage(Base):
    """Сообщения в thread ассистента."""
    __tablename__ = "assistant_messages"
    __table_args__ = (
        Index("ix_assistant_messages_thread_id", "thread_id"),
        Index("ix_assistant_messages_tenant_id", "tenant_id"),
        Index("ix_assistant_messages_workspace_id", "workspace_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(Integer, ForeignKey("assistant_threads.id"), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=True)
    role = Column(String(20), nullable=False)  # system, assistant, user
    content = Column(Text, nullable=False)
    meta = Column(JSONType, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class AssistantRecommendation(Base):
    """Структурированные рекомендации ассистента по следующему шагу."""
    __tablename__ = "assistant_recommendations"
    __table_args__ = (
        Index("ix_assistant_recommendations_tenant_id", "tenant_id"),
        Index("ix_assistant_recommendations_workspace_id", "workspace_id"),
        Index("ix_assistant_recommendations_thread_id", "thread_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(Integer, ForeignKey("assistant_threads.id"), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=True)
    recommendation_type = Column(String(32), nullable=False)  # next_step, draft, parser, positioning
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    payload = Column(JSONType, nullable=True)
    status = Column(String(20), default="active")  # active, accepted, dismissed, archived
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class CreativeDraft(Base):
    """Черновики креативов, которые ассистент генерирует из business context."""
    __tablename__ = "creative_drafts"
    __table_args__ = (
        Index("ix_creative_drafts_tenant_id", "tenant_id"),
        Index("ix_creative_drafts_workspace_id", "workspace_id"),
        Index("ix_creative_drafts_brief_id", "brief_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=True)
    brief_id = Column(Integer, ForeignKey("business_briefs.id"), nullable=True)
    draft_type = Column(String(32), nullable=False)  # post, comment, ad_copy, image_prompt
    status = Column(String(20), default="draft")  # draft, approved, rejected, archived
    title = Column(String(255), nullable=True)
    input_prompt = Column(Text, nullable=True)
    content_text = Column(Text, nullable=True)
    meta = Column(JSONType, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class ManualAction(Base):
    """Ручные действия оператора и клиента внутри web shell."""
    __tablename__ = "manual_actions"
    __table_args__ = (
        Index("ix_manual_actions_tenant_id", "tenant_id"),
        Index("ix_manual_actions_workspace_id", "workspace_id"),
        Index("ix_manual_actions_account_id", "account_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    action_type = Column(String(32), nullable=False)  # note, approval, manual_step
    title = Column(String(255), nullable=False)
    notes = Column(Text, nullable=True)
    payload = Column(JSONType, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class AIModelProfile(Base):
    """Каталог доступных AI-моделей и их стоимости."""
    __tablename__ = "ai_model_profiles"
    __table_args__ = (
        Index("ix_ai_model_profiles_provider", "provider"),
        Index("ix_ai_model_profiles_tier", "model_tier"),
        UniqueConstraint("provider", "model_name", name="uq_ai_model_profiles_provider_model"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(32), nullable=False)  # gemini_direct, openrouter
    model_name = Column(String(255), nullable=False)
    model_tier = Column(String(20), nullable=False)  # boss, manager, worker
    is_active = Column(Boolean, default=True)
    input_cost_per_1m = Column(Float, default=0.0)
    output_cost_per_1m = Column(Float, default=0.0)
    max_context_tokens = Column(Integer, nullable=True)
    capabilities = Column(JSONType, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class AITaskPolicy(Base):
    """Правила маршрутизации AI-задач per-tenant или глобально."""
    __tablename__ = "ai_task_policies"
    __table_args__ = (
        Index("ix_ai_task_policies_tenant_id", "tenant_id"),
        Index("ix_ai_task_policies_task_type", "task_type"),
        UniqueConstraint("tenant_id", "task_type", name="uq_ai_task_policies_tenant_task"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    task_type = Column(String(64), nullable=False)
    requested_model_tier = Column(String(20), nullable=False)
    allow_downgrade = Column(Boolean, default=True)
    approval_required = Column(Boolean, default=False)
    output_contract_type = Column(String(32), default="json_object")
    latency_target_ms = Column(Integer, nullable=True)
    max_budget_usd = Column(Float, nullable=True)
    policy = Column(JSONType, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class AIRequest(Base):
    """Аудит каждого AI-запроса через единый router."""
    __tablename__ = "ai_requests"
    __table_args__ = (
        Index("ix_ai_requests_tenant_id", "tenant_id"),
        Index("ix_ai_requests_workspace_id", "workspace_id"),
        Index("ix_ai_requests_task_type", "task_type"),
        Index("ix_ai_requests_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=True)
    surface = Column(String(64), nullable=False)
    task_type = Column(String(64), nullable=False)
    agent_name = Column(String(64), nullable=False)
    requested_model_tier = Column(String(20), nullable=False)
    executed_model_tier = Column(String(20), nullable=True)
    requested_provider = Column(String(32), nullable=True)
    executed_provider = Column(String(32), nullable=True)
    executed_model = Column(String(255), nullable=True)
    status = Column(String(32), default="pending")  # pending, succeeded, failed, blocked
    outcome = Column(String(32), default="executed_as_requested")
    output_contract_type = Column(String(32), default="json_object")
    latency_ms = Column(Integer, nullable=True)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    estimated_cost_usd = Column(Float, default=0.0)
    fallback_used = Column(Boolean, default=False)
    reason_code = Column(String(64), nullable=True)
    json_parse_failed = Column(Boolean, default=False)
    json_repair_applied = Column(Boolean, default=False)
    json_repair_strategy = Column(String(64), nullable=True)
    parsed_without_repair = Column(Boolean, default=False)
    downgraded_by_budget_policy = Column(Boolean, default=False)
    blocked_by_budget_policy = Column(Boolean, default=False)
    quality_score = Column(Float, nullable=True)
    quality_flags = Column(JSONType, nullable=True)
    meta = Column(JSONType, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)
    completed_at = Column(DateTime, nullable=True)


class AIRequestAttempt(Base):
    """Отдельные попытки provider/model внутри одного AIRequest."""
    __tablename__ = "ai_request_attempts"
    __table_args__ = (
        Index("ix_ai_request_attempts_ai_request_id", "ai_request_id"),
        Index("ix_ai_request_attempts_tenant_id", "tenant_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ai_request_id = Column(Integer, ForeignKey("ai_requests.id"), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    provider = Column(String(32), nullable=False)
    model_name = Column(String(255), nullable=False)
    status = Column(String(32), nullable=False)  # succeeded, failed
    latency_ms = Column(Integer, nullable=True)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    estimated_cost_usd = Column(Float, default=0.0)
    fallback_used = Column(Boolean, default=False)
    reason_code = Column(String(64), nullable=True)
    json_parse_failed = Column(Boolean, default=False)
    json_repair_applied = Column(Boolean, default=False)
    json_repair_strategy = Column(String(64), nullable=True)
    parsed_without_repair = Column(Boolean, default=False)
    response_meta = Column(JSONType, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class AIBudgetLimit(Base):
    """Лимиты расходов AI per tenant."""
    __tablename__ = "ai_budget_limits"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_ai_budget_limits_tenant_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    daily_budget_usd = Column(Float, nullable=True)
    monthly_budget_usd = Column(Float, nullable=True)
    boss_daily_budget_usd = Column(Float, nullable=True)
    hard_stop_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class AIBudgetCounter(Base):
    """Агрегированные счётчики usage/cost по периодам."""
    __tablename__ = "ai_budget_counters"
    __table_args__ = (
        Index("ix_ai_budget_counters_tenant_id", "tenant_id"),
        Index("ix_ai_budget_counters_period_start", "period_start"),
        UniqueConstraint(
            "tenant_id",
            "period_type",
            "period_start",
            "model_tier",
            "provider",
            name="uq_ai_budget_counters_scope",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    period_type = Column(String(16), nullable=False)  # daily, monthly
    period_start = Column(DateTime, nullable=False)
    model_tier = Column(String(20), nullable=False)
    provider = Column(String(32), nullable=False)
    request_count = Column(Integer, default=0)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    estimated_cost_usd = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=utcnow)


class AIEscalation(Base):
    """Эскалации в boss-tier и бюджетные downgrade/stop решения."""
    __tablename__ = "ai_escalations"
    __table_args__ = (
        Index("ix_ai_escalations_tenant_id", "tenant_id"),
        Index("ix_ai_escalations_ai_request_id", "ai_request_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=True)
    ai_request_id = Column(Integer, ForeignKey("ai_requests.id"), nullable=True)
    task_type = Column(String(64), nullable=False)
    from_tier = Column(String(20), nullable=True)
    to_tier = Column(String(20), nullable=True)
    trigger_type = Column(String(32), nullable=False)  # manual, policy, contradiction
    reason_code = Column(String(64), nullable=True)
    approved_by_user = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)


class AIAgentRun(Base):
    """Логический запуск одного агентного workstream."""
    __tablename__ = "ai_agent_runs"
    __table_args__ = (
        Index("ix_ai_agent_runs_tenant_id", "tenant_id"),
        Index("ix_ai_agent_runs_ai_request_id", "ai_request_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=True)
    ai_request_id = Column(Integer, ForeignKey("ai_requests.id"), nullable=True)
    agent_name = Column(String(64), nullable=False)
    task_type = Column(String(64), nullable=False)
    requested_model_tier = Column(String(20), nullable=False)
    executed_model_tier = Column(String(20), nullable=True)
    status = Column(String(32), default="pending")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class AppJob(Base):
    """Фоновая задача продуктового слоя (assistant/context/creative)."""
    __tablename__ = "app_jobs"
    __table_args__ = (
        Index("ix_app_jobs_tenant_id", "tenant_id"),
        Index("ix_app_jobs_workspace_id", "workspace_id"),
        Index("ix_app_jobs_job_type", "job_type"),
        Index("ix_app_jobs_status", "status"),
        Index("ix_app_jobs_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=True)
    job_type = Column(String(64), nullable=False)
    queue_name = Column(String(64), nullable=False)
    status = Column(String(20), default="queued")  # queued, running, succeeded, failed
    payload = Column(JSONType, nullable=True)
    result = Column(JSONType, nullable=True)
    result_summary = Column(JSONType, nullable=True)
    error_code = Column(String(64), nullable=True)
    attempt_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utcnow)


class FarmConfig(Base):
    """Конфигурация фермы — многопоточный commenting farm."""
    __tablename__ = "farm_configs"
    __table_args__ = (
        Index("ix_farm_configs_tenant_id", "tenant_id"),
        Index("ix_farm_configs_workspace_id", "workspace_id"),
        Index("ix_farm_configs_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    name = Column(String(200), nullable=False)
    status = Column(String(20), default="stopped")  # stopped, running, paused
    mode = Column(String(20), default="multithread")  # multithread, standard
    max_threads = Column(Integer, default=50)
    comment_prompt = Column(Text, nullable=True)
    comment_tone = Column(String(50), default="neutral")  # neutral, hater, flirt, native, custom
    comment_language = Column(String(10), default="auto")
    comment_all_posts = Column(Boolean, default=True)
    comment_percentage = Column(Integer, default=100)
    delay_before_comment_min = Column(Integer, default=30)
    delay_before_comment_max = Column(Integer, default=120)
    delay_before_join_min = Column(Integer, default=60)
    delay_before_join_max = Column(Integer, default=300)
    ai_protection_mode = Column(String(20), default="aggressive")  # off, aggressive, conservative
    auto_responder_enabled = Column(Boolean, default=False)
    auto_responder_prompt = Column(Text, nullable=True)
    auto_responder_redirect_url = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class FarmThread(Base):
    """Поток фермы (1 поток = 1 аккаунт)."""
    __tablename__ = "farm_threads"
    __table_args__ = (
        Index("ix_farm_threads_tenant_id", "tenant_id"),
        Index("ix_farm_threads_farm_id", "farm_id"),
        Index("ix_farm_threads_account_id", "account_id"),
        Index("ix_farm_threads_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    farm_id = Column(Integer, ForeignKey("farm_configs.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    thread_index = Column(Integer, nullable=False)
    status = Column(String(30), default="idle")
    # idle, subscribing, monitoring, commenting, cooldown, quarantine, error, stopped
    assigned_channels = Column(JSONType, nullable=True)
    folder_invite_link = Column(Text, nullable=True)
    stats_comments_sent = Column(Integer, default=0)
    stats_comments_failed = Column(Integer, default=0)
    stats_reactions_sent = Column(Integer, default=0)
    stats_last_comment_at = Column(DateTime, nullable=True)
    stats_last_error = Column(Text, nullable=True)
    health_score = Column(Integer, default=100)
    quarantine_until = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utcnow)


class ChannelDatabase(Base):
    """База каналов для таргетинга."""
    __tablename__ = "channel_databases"
    __table_args__ = (
        Index("ix_channel_databases_tenant_id", "tenant_id"),
        Index("ix_channel_databases_workspace_id", "workspace_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    name = Column(String(200), nullable=False)
    source = Column(String(20), default="manual")  # manual, parsed, map
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=utcnow)


class ChannelEntry(Base):
    """Канал в базе каналов."""
    __tablename__ = "channel_entries"
    __table_args__ = (
        Index("ix_channel_entries_tenant_id", "tenant_id"),
        Index("ix_channel_entries_database_id", "database_id"),
        Index("ix_channel_entries_blacklisted", "blacklisted"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    database_id = Column(Integer, ForeignKey("channel_databases.id"), nullable=False)
    telegram_id = Column(BigInteger, nullable=True)
    username = Column(String(100), nullable=True)
    title = Column(String(300), nullable=True)
    member_count = Column(Integer, nullable=True)
    has_comments = Column(Boolean, default=True)
    language = Column(String(10), nullable=True)
    category = Column(String(100), nullable=True)
    last_post_at = Column(DateTime, nullable=True)
    blacklisted = Column(Boolean, default=False)
    success_rate = Column(Float, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class ParsingJob(Base):
    """Задача парсинга каналов/пользователей."""
    __tablename__ = "parsing_jobs"
    __table_args__ = (
        Index("ix_parsing_jobs_tenant_id", "tenant_id"),
        Index("ix_parsing_jobs_workspace_id", "workspace_id"),
        Index("ix_parsing_jobs_status", "status"),
        Index("ix_parsing_jobs_account_id", "account_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    job_type = Column(String(20), nullable=False)  # channels, users
    status = Column(String(20), default="pending")  # pending, running, completed, failed
    keywords = Column(JSONType, nullable=True)
    filters = Column(JSONType, nullable=True)
    max_results = Column(Integer, default=50)
    results_count = Column(Integer, default=0)
    target_database_id = Column(Integer, ForeignKey("channel_databases.id"), nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class ProfileTemplate(Base):
    """Шаблон AI-профиля для массовой генерации."""
    __tablename__ = "profile_templates"
    __table_args__ = (
        Index("ix_profile_templates_tenant_id", "tenant_id"),
        Index("ix_profile_templates_workspace_id", "workspace_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    name = Column(String(200), nullable=True)
    gender = Column(String(10), nullable=True)  # male, female, any
    geo = Column(String(50), nullable=True)
    bio_template = Column(Text, nullable=True)
    channel_name_template = Column(Text, nullable=True)
    channel_description_template = Column(Text, nullable=True)
    channel_first_post_template = Column(Text, nullable=True)
    avatar_style = Column(String(50), nullable=True)  # ai_generated, library, custom
    avatar_url = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class FarmEvent(Base):
    """Лог событий фермы для real-time стриминга."""
    __tablename__ = "farm_events"
    __table_args__ = (
        Index("ix_farm_events_tenant_id", "tenant_id"),
        Index("ix_farm_events_farm_id", "farm_id"),
        Index("ix_farm_events_thread_id", "thread_id"),
        Index("ix_farm_events_created_at", "created_at"),
        Index("ix_farm_events_severity", "severity"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    farm_id = Column(Integer, ForeignKey("farm_configs.id"), nullable=False)
    thread_id = Column(Integer, ForeignKey("farm_threads.id"), nullable=True)
    event_type = Column(String(50), nullable=False)
    # thread_started, thread_stopped, comment_sent, comment_failed,
    # channel_joined, channel_left, quarantine_entered, quarantine_lifted,
    # mute_detected, flood_wait, error, health_change
    severity = Column(String(10), default="info")  # info, warn, error
    message = Column(Text, nullable=True)
    event_metadata = Column("metadata", JSONType, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class WarmupConfig(Base):
    """Конфигурация автопрогрева аккаунтов."""
    __tablename__ = "warmup_configs"
    __table_args__ = (
        Index("ix_warmup_configs_tenant_id", "tenant_id"),
        Index("ix_warmup_configs_workspace_id", "workspace_id"),
        Index("ix_warmup_configs_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    name = Column(String(200), nullable=False)
    status = Column(String(20), default="stopped")  # stopped, running, paused
    mode = Column(String(20), default="conservative")  # conservative, moderate, aggressive
    safety_limit_actions_per_hour = Column(Integer, default=5)
    active_hours_start = Column(Integer, default=9)
    active_hours_end = Column(Integer, default=23)
    warmup_duration_minutes = Column(Integer, default=30)
    interval_between_sessions_hours = Column(Integer, default=6)
    enable_reactions = Column(Boolean, default=True)
    enable_read_channels = Column(Boolean, default=True)
    enable_dialogs_between_accounts = Column(Boolean, default=True)
    target_channels = Column(JSONType, nullable=True)  # list of channel usernames
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class WarmupSession(Base):
    """Индивидуальная сессия прогрева одного аккаунта."""
    __tablename__ = "warmup_sessions"
    __table_args__ = (
        Index("ix_warmup_sessions_tenant_id", "tenant_id"),
        Index("ix_warmup_sessions_warmup_id", "warmup_id"),
        Index("ix_warmup_sessions_account_id", "account_id"),
        Index("ix_warmup_sessions_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    warmup_id = Column(Integer, ForeignKey("warmup_configs.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    status = Column(String(20), default="pending")  # pending, running, completed, failed
    actions_performed = Column(Integer, default=0)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    next_session_at = Column(DateTime, nullable=True)


class AccountHealthScore(Base):
    """Health and survivability scoring per account per tenant."""
    __tablename__ = "account_health_scores"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account_id", name="uq_account_health_scores_tenant_account"),
        Index("ix_account_health_scores_tenant_id", "tenant_id"),
        Index("ix_account_health_scores_account_id", "account_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    health_score = Column(Integer, default=100)       # 0-100 current operational stability
    survivability_score = Column(Integer, default=100)  # 0-100 predicted longevity
    flood_wait_count = Column(Integer, default=0)
    spam_block_count = Column(Integer, default=0)
    successful_actions = Column(Integer, default=0)
    hours_without_error = Column(Integer, default=0)
    profile_completeness = Column(Integer, default=0)  # 0-100
    account_age_days = Column(Integer, default=0)
    last_calculated_at = Column(DateTime, nullable=True)
    factors = Column(JSONType, nullable=True)  # detailed factor breakdown


class ContentTemplate(Base):
    """DB-first контент-шаблоны (посты/комменты)."""
    __tablename__ = "content_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    scope = Column(String(32), nullable=False, default="packaging_post")  # packaging_post | comment
    name = Column(String(120), nullable=False)
    version = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    text_template = Column(Text, nullable=True)
    media_file_id = Column(String(255), nullable=True)
    media_type = Column(String(20), nullable=True)  # photo|video|document
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class Proxy(Base):
    """Прокси-сервер."""
    __tablename__ = "proxies"
    __table_args__ = (
        Index("ix_proxies_tenant_id", "tenant_id"),
        Index("ix_proxies_workspace_id", "workspace_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=True)
    proxy_type = Column(String(10), default="socks5")  # socks5, http
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String(255), nullable=True)
    password = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    health_status = Column(String(20), default="unknown")  # unknown, alive, failing, dead
    consecutive_failures = Column(Integer, default=0)
    last_error = Column(String(255), nullable=True)
    last_checked = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    invalidated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="proxies")
    accounts = relationship("Account", back_populates="proxy")

    @property
    def url(self) -> str:
        auth = f"{self.username}:{self.password}@" if self.username else ""
        return f"{self.proxy_type}://{auth}{self.host}:{self.port}"


class Channel(Base):
    """Telegram канал для мониторинга."""
    __tablename__ = "channels"
    __table_args__ = (
        UniqueConstraint("user_id", "telegram_id", name="uq_channels_user_telegram"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    telegram_id = Column(BigInteger, nullable=False)
    username = Column(String(255), nullable=True)
    title = Column(String(500), nullable=False)
    subscribers = Column(Integer, default=0)
    topic = Column(String(100), nullable=True)  # vpn, ai, services, etc.
    comments_enabled = Column(Boolean, default=True)
    discussion_group_id = Column(BigInteger, nullable=True)  # ID группы обсуждений
    review_state = Column(String(20), default="discovered")  # discovered, candidate, approved, blocked
    publish_mode = Column(String(20), default="research_only")  # research_only, draft_only, auto_allowed
    permission_basis = Column(String(32), default="")  # owned, partner, admin_added, unknown
    review_note = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    is_blacklisted = Column(Boolean, default=False)
    last_post_checked = Column(Integer, default=0)  # ID последнего проверенного поста
    last_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="channels")
    posts = relationship("Post", back_populates="channel")


class Post(Base):
    """Пост в канале."""
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=False)
    telegram_post_id = Column(Integer, nullable=False)
    text = Column(Text, nullable=True)
    relevance_score = Column(Float, default=0.0)
    is_commented = Column(Boolean, default=False)
    posted_at = Column(DateTime, nullable=True)
    discovered_at = Column(DateTime, default=utcnow)

    channel = relationship("Channel", back_populates="posts")
    comments = relationship("Comment", back_populates="post")


class Comment(Base):
    """Отправленный комментарий."""
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    text = Column(Text, nullable=False)
    scenario = Column(String(1), nullable=False)  # A или B
    status = Column(String(20), default="sent")  # sent, failed, deleted
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    account = relationship("Account", back_populates="comments")
    post = relationship("Post", back_populates="comments")


class ReactionJob(Base):
    """Задача массового выставления реакций на посты."""
    __tablename__ = "reaction_jobs"
    __table_args__ = (
        Index("ix_reaction_jobs_tenant_id", "tenant_id"),
        Index("ix_reaction_jobs_status", "status"),
        Index("ix_reaction_jobs_farm_id", "farm_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    farm_id = Column(Integer, ForeignKey("farm_configs.id"), nullable=True)
    channel_username = Column(String(200), nullable=False)
    post_id = Column(Integer, nullable=True)  # specific post or null for latest
    reaction_type = Column(String(20), default="random")  # random, thumbs_up, fire, heart, etc.
    account_ids = Column(JSONType, nullable=True)  # list of account IDs to react with
    status = Column(String(20), default="pending")  # pending, running, completed, failed
    total_reactions = Column(Integer, default=0)
    successful_reactions = Column(Integer, default=0)
    failed_reactions = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    completed_at = Column(DateTime, nullable=True)


class ChattingConfig(Base):
    """Конфигурация автоматического чатинга в каналах."""
    __tablename__ = "chatting_configs"
    __table_args__ = (
        Index("ix_chatting_configs_tenant_id", "tenant_id"),
        Index("ix_chatting_configs_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    name = Column(String(200), nullable=False)
    status = Column(String(20), default="stopped")  # stopped, running, paused
    mode = Column(String(20), default="conservative")  # conservative, moderate, aggressive
    target_channels = Column(JSONType, nullable=True)  # channels where chatting happens
    prompt_template = Column(Text, nullable=True)  # AI prompt for generating chat messages
    max_messages_per_hour = Column(Integer, default=5)
    min_delay_seconds = Column(Integer, default=120)
    max_delay_seconds = Column(Integer, default=600)
    account_ids = Column(JSONType, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class DialogConfig(Base):
    """Конфигурация диалогов между аккаунтами (прогрев, вовлечение, поддержка)."""
    __tablename__ = "dialog_configs"
    __table_args__ = (
        Index("ix_dialog_configs_tenant_id", "tenant_id"),
        Index("ix_dialog_configs_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    name = Column(String(200), nullable=False)
    status = Column(String(20), default="stopped")  # stopped, running, paused
    dialog_type = Column(String(30), default="warmup")  # warmup, engagement, support
    account_pairs = Column(JSONType, nullable=True)  # pairs of account IDs for dialogs
    prompt_template = Column(Text, nullable=True)
    messages_per_session = Column(Integer, default=5)
    session_interval_hours = Column(Integer, default=4)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class UserParsingResult(Base):
    """Результат парсинга пользователей из каналов."""
    __tablename__ = "user_parsing_results"
    __table_args__ = (
        Index("ix_user_parsing_results_tenant_id", "tenant_id"),
        Index("ix_user_parsing_results_channel_username", "channel_username"),
        Index("ix_user_parsing_results_user_telegram_id", "user_telegram_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    job_id = Column(Integer, nullable=True)  # ref to parsing job
    channel_username = Column(String(200), nullable=True)
    user_telegram_id = Column(BigInteger, nullable=True)
    username = Column(String(200), nullable=True)
    first_name = Column(String(200), nullable=True)
    last_name = Column(String(200), nullable=True)
    bio = Column(Text, nullable=True)
    is_premium = Column(Boolean, default=False)
    last_seen = Column(DateTime, nullable=True)
    parsed_at = Column(DateTime, default=utcnow)


class TelegramFolder(Base):
    """Telegram папка (folder) с каналами, привязанная к аккаунту."""
    __tablename__ = "telegram_folders"
    __table_args__ = (
        Index("ix_telegram_folders_tenant_id", "tenant_id"),
        Index("ix_telegram_folders_account_id", "account_id"),
        Index("ix_telegram_folders_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    folder_name = Column(String(200), nullable=False)
    folder_id = Column(Integer, nullable=True)  # Telegram folder ID
    invite_link = Column(String(500), nullable=True)
    channel_usernames = Column(JSONType, nullable=True)  # list of channels in folder
    status = Column(String(20), default="active")  # active, archived
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Sprint 8 — Intelligence & Scale
# ---------------------------------------------------------------------------


class ChannelMapEntry(Base):
    """Глобальный индекс Telegram-каналов для поиска и таргетинга."""
    __tablename__ = "channel_map_entries"
    __table_args__ = (
        Index("ix_channel_map_entries_tenant_id", "tenant_id"),
        Index("ix_channel_map_entries_category", "category"),
        Index("ix_channel_map_entries_language", "language"),
        Index("ix_channel_map_entries_member_count", "member_count"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    telegram_id = Column(BigInteger, nullable=True)
    username = Column(String(200), nullable=True)
    title = Column(String(500), nullable=True)
    category = Column(String(100), nullable=True)       # tech, crypto, marketing, ecom, edtech …
    subcategory = Column(String(100), nullable=True)
    language = Column(String(10), nullable=True)
    member_count = Column(Integer, default=0)
    has_comments = Column(Boolean, default=False)
    avg_post_reach = Column(Integer, nullable=True)
    engagement_rate = Column(Float, nullable=True)
    last_indexed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class Campaign(Base):
    """Рекламная кампания: настройки, бюджет, тип активности."""
    __tablename__ = "campaigns"
    __table_args__ = (
        Index("ix_campaigns_tenant_id", "tenant_id"),
        Index("ix_campaigns_status", "status"),
        Index("ix_campaigns_campaign_type", "campaign_type"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    name = Column(String(200), nullable=False)
    status = Column(String(20), default="draft")        # draft, active, paused, completed, archived
    campaign_type = Column(String(30), default="commenting")  # commenting, reactions, chatting, mixed
    account_ids = Column(JSONType, nullable=True)        # list[int] — assigned account PKs
    channel_database_id = Column(Integer, ForeignKey("channel_databases.id"), nullable=True)
    comment_prompt = Column(Text, nullable=True)
    comment_tone = Column(String(50), nullable=True)
    comment_language = Column(String(10), default="ru")
    schedule_type = Column(String(20), default="continuous")  # continuous, scheduled, burst
    schedule_config = Column(JSONType, nullable=True)   # {start_time, end_time, days_of_week, burst_count}
    budget_daily_actions = Column(Integer, default=100)
    budget_total_actions = Column(Integer, nullable=True)
    total_actions_performed = Column(Integer, default=0)
    total_comments_sent = Column(Integer, default=0)
    total_reactions_sent = Column(Integer, default=0)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class CampaignRun(Base):
    """Один цикл запуска кампании: счётчики, лог ошибок."""
    __tablename__ = "campaign_runs"
    __table_args__ = (
        Index("ix_campaign_runs_tenant_id", "tenant_id"),
        Index("ix_campaign_runs_campaign_id", "campaign_id"),
        Index("ix_campaign_runs_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    status = Column(String(20), default="pending")      # pending, running, completed, failed
    actions_performed = Column(Integer, default=0)
    comments_sent = Column(Integer, default=0)
    reactions_sent = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    run_log = Column(JSONType, nullable=True)           # [{timestamp, action, result}]


class AnalyticsEvent(Base):
    """Поток событий аналитики: комменты, реакции, блокировки, старт кампаний."""
    __tablename__ = "analytics_events"
    __table_args__ = (
        Index("ix_analytics_events_tenant_id", "tenant_id"),
        Index("ix_analytics_events_event_type", "event_type"),
        Index("ix_analytics_events_account_id", "account_id"),
        Index("ix_analytics_events_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    # comment_sent, reaction_sent, flood_wait, spam_block, account_frozen, campaign_started …
    event_type = Column(String(50), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    campaign_id = Column(Integer, nullable=True)        # soft ref — no FK to avoid cascade issues
    channel_username = Column(String(200), nullable=True)
    event_data = Column(JSONType, nullable=True)
    created_at = Column(DateTime, default=utcnow)


