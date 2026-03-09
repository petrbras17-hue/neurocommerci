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
    email = Column(String(255), unique=True, nullable=True)
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
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    settings = Column(JSONType, nullable=True)
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

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(20), unique=True, nullable=False)
    session_file = Column(String(255), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
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

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
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
