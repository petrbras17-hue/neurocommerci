from __future__ import annotations

import asyncio
import collections
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hmac
import hashlib
import logging
import os
from pathlib import Path
import time
from typing import Any, AsyncIterator, List, Literal, Optional

import jwt
import uvicorn
from fastapi import Body, Cookie, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings, validate_critical_secrets
from core.ai_audit import get_tenant_ai_quality_summary, list_internal_ai_audit
from core.assistant_jobs import (
    ASSISTANT_QUEUE_NAMES,
    JOB_TYPE_ASSISTANT_MESSAGE,
    JOB_TYPE_CONTEXT_CONFIRM,
    JOB_TYPE_CREATIVE_GENERATE,
    JOB_TYPE_START_BRIEF,
    AssistantJobError,
    assistant_worker_loop,
    enqueue_app_job,
    get_job_status,
)
from core.assistant_service import (
    AssistantServiceError,
    approve_creative_draft,
    get_assistant_thread,
    get_context_payload,
    list_creative_drafts,
)
from core.web_accounts import (
    WebOnboardingError,
    audit_web_account,
    bind_proxy_for_account,
    get_web_account_timeline,
    list_available_web_proxies,
    list_web_accounts,
    save_uploaded_account_pair,
    save_web_account_note,
)
from core.web_auth import (
    TELEGRAM_AUTH_MAX_AGE_SECONDS,
    EmailAuthError,
    TelegramAuthError,
    WebAuthBundle,
    _refresh_token_hash,
    complete_profile,
    get_me_payload,
    get_team_payload,
    list_user_sessions,
    login_with_email,
    logout_web_session,
    refresh_web_session,
    register_with_email,
    revoke_all_other_sessions,
    revoke_session_by_id,
    verify_telegram_login,
)
from core.lead_funnel import LeadSnapshot, deliver_lead_funnel
from core.telegram_bot_auth import (
    close_redis as _close_bot_auth_redis,
    consume_pending_auth,
    generate_auth_code,
    get_pending_auth,
    init_redis as _init_bot_auth_redis,
)
from core.task_queue import task_queue
from core.proxy_manager import ProxyConfig, ProxyManager, check_proxy_orm, parse_proxy_line
from core.proxy_router import NoAvailableProxyError, ProxyRouter
from core.account_lifecycle import AccountLifecycle, LifecycleTransitionError
from storage.models import (
    Account,
    AccountHealthHistory,
    AccountHealthScore,
    AccountStageEvent,
    AIBudgetCounter,
    AIRequest,
    AlertConfig,
    AnalyticsDailyCache,
    AnalyticsEvent,
    AppJob,
    Campaign,
    CampaignAccount,
    CampaignChannel,
    CampaignRun,
    ChannelDatabase,
    ChannelEntry,
    ChannelMapEntry,
    ChattingConfig,
    CommentABResult,
    CommentStyleTemplate,
    DialogConfig,
    FarmConfig,
    FarmEvent,
    FarmThread,
    HealingAction,
    Lead,
    ParsingJob,
    PlatformAlert,
    ProductBrief,
    ProfileTemplate,
    Proxy,
    PurchaseRequest,
    ReactionJob,
    Subscription,
    TeamMember,
    TelegramFolder,
    Tenant,
    UserParsingResult,
    WarmupConfig,
    WarmupSession,
    WeeklyReport,
    Workspace,
)
from storage.sqlite_db import apply_session_rls_context, async_session, dispose_engine, init_db
from utils.helpers import utcnow
from utils.session_topology import audit_session_topology, quarantine_noncanonical_assets


# ---------------------------------------------------------------------------
# Farm / orchestrator job type constants
# ---------------------------------------------------------------------------

QUEUE_FARM = "farm_tasks"
QUEUE_PARSER = "parser_tasks"
QUEUE_PROFILE = "profile_tasks"

JOB_TYPE_FARM_START = "farm_start"
JOB_TYPE_FARM_STOP = "farm_stop"
JOB_TYPE_FARM_PAUSE = "farm_pause"
JOB_TYPE_FARM_RESUME = "farm_resume"
JOB_TYPE_PARSER_CHANNELS = "parser_channels"
JOB_TYPE_PROFILE_GENERATE = "profile_generate"
JOB_TYPE_PROFILE_MASS_GENERATE = "profile_mass_generate"
JOB_TYPE_PROFILE_APPLY = "profile_apply"
JOB_TYPE_PROFILE_CREATE_CHANNEL = "profile_create_channel"

# Sprint 6 — warmup and health job types
QUEUE_WARMUP = "warmup_tasks"
QUEUE_HEALTH = "health_tasks"

JOB_TYPE_WARMUP_START = "warmup_start"
JOB_TYPE_WARMUP_STOP = "warmup_stop"
JOB_TYPE_WARMUP_RUN_SESSION = "warmup_run_session"
JOB_TYPE_HEALTH_RECALCULATE = "health_recalculate"

# Sprint 7 — advanced module job types
QUEUE_REACTIONS = "reaction_tasks"
QUEUE_CHATTING = "chatting_tasks"
QUEUE_DIALOGS = "dialog_tasks"
QUEUE_USER_PARSER = "user_parser_tasks"
QUEUE_FOLDERS = "folder_tasks"

JOB_TYPE_REACTION_RUN = "reaction_run"
JOB_TYPE_CHATTING_START = "chatting_start"
JOB_TYPE_CHATTING_STOP = "chatting_stop"
JOB_TYPE_DIALOG_START = "dialog_start"
JOB_TYPE_DIALOG_STOP = "dialog_stop"
JOB_TYPE_USER_PARSE = "user_parse"
JOB_TYPE_FOLDER_CREATE = "folder_create"
JOB_TYPE_FOLDER_DELETE = "folder_delete"

# Sprint 8 — Campaigns and Channel Index
QUEUE_CAMPAIGNS = "campaign_tasks"

JOB_TYPE_CAMPAIGN_START = "campaign_start"
JOB_TYPE_CAMPAIGN_STOP = "campaign_stop"
JOB_TYPE_CHANNEL_INDEX = "channel_index"

# Sprint 7 Tasks 2+3 — Account lifecycle
QUEUE_ACCOUNT_LIFECYCLE = "account_lifecycle_tasks"
JOB_TYPE_ACCOUNT_HEALTH_CHECK = "account_health_check"
JOB_TYPE_ACCOUNT_LIFECYCLE_MONITOR = "account_lifecycle_monitor"

# Channel classification (micro-topic taxonomy)
JOB_TYPE_CHANNEL_CLASSIFY_BATCH = "channel_classify_batch"

_FARM_JOB_TYPE_TO_QUEUE: dict[str, str] = {
    JOB_TYPE_FARM_START: QUEUE_FARM,
    JOB_TYPE_FARM_STOP: QUEUE_FARM,
    JOB_TYPE_FARM_PAUSE: QUEUE_FARM,
    JOB_TYPE_FARM_RESUME: QUEUE_FARM,
    JOB_TYPE_PARSER_CHANNELS: QUEUE_PARSER,
    JOB_TYPE_PROFILE_GENERATE: QUEUE_PROFILE,
    JOB_TYPE_PROFILE_MASS_GENERATE: QUEUE_PROFILE,
    JOB_TYPE_PROFILE_APPLY: QUEUE_PROFILE,
    JOB_TYPE_PROFILE_CREATE_CHANNEL: QUEUE_PROFILE,
    # Sprint 6
    JOB_TYPE_WARMUP_START: QUEUE_WARMUP,
    JOB_TYPE_WARMUP_STOP: QUEUE_WARMUP,
    JOB_TYPE_WARMUP_RUN_SESSION: QUEUE_WARMUP,
    JOB_TYPE_HEALTH_RECALCULATE: QUEUE_HEALTH,
    # Sprint 7
    JOB_TYPE_REACTION_RUN: QUEUE_REACTIONS,
    JOB_TYPE_CHATTING_START: QUEUE_CHATTING,
    JOB_TYPE_CHATTING_STOP: QUEUE_CHATTING,
    JOB_TYPE_DIALOG_START: QUEUE_DIALOGS,
    JOB_TYPE_DIALOG_STOP: QUEUE_DIALOGS,
    JOB_TYPE_USER_PARSE: QUEUE_USER_PARSER,
    JOB_TYPE_FOLDER_CREATE: QUEUE_FOLDERS,
    JOB_TYPE_FOLDER_DELETE: QUEUE_FOLDERS,
    # Sprint 8
    JOB_TYPE_CAMPAIGN_START: QUEUE_CAMPAIGNS,
    JOB_TYPE_CAMPAIGN_STOP: QUEUE_CAMPAIGNS,
    JOB_TYPE_CHANNEL_INDEX: QUEUE_CAMPAIGNS,
    # Sprint 7 Tasks 2+3 — Account lifecycle
    JOB_TYPE_ACCOUNT_HEALTH_CHECK: QUEUE_ACCOUNT_LIFECYCLE,
    JOB_TYPE_ACCOUNT_LIFECYCLE_MONITOR: QUEUE_ACCOUNT_LIFECYCLE,
    # Channel classification
    JOB_TYPE_CHANNEL_CLASSIFY_BATCH: QUEUE_CAMPAIGNS,
}


async def _enqueue_within_session(
    *,
    session: "AsyncSession",
    tenant_id: int,
    workspace_id: int | None,
    user_id: int | None,
    job_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an AppJob row in an *already-open* session and push to Redis.

    Use this variant instead of _enqueue_farm_job when the caller already
    holds an open write transaction (e.g. from Depends(tenant_session)).
    Opening a second session from within an open write transaction deadlocks
    SQLite because SQLite only allows one writer at a time.
    """
    queue_name = _FARM_JOB_TYPE_TO_QUEUE.get(job_type)
    if not queue_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported_job_type")
    job = AppJob(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        job_type=job_type,
        queue_name=queue_name,
        status="queued",
        payload=payload or {},
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(job)
    await session.flush()
    job_id = int(job.id)
    try:
        await task_queue.connect()
        await task_queue.enqueue(
            queue_name,
            {
                "job_id": job_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "job_type": job_type,
            },
        )
    except Exception:
        job.status = "failed"
        job.error_code = "queue_unavailable"
        job.completed_at = utcnow()
        job.updated_at = utcnow()
    return {"job_id": job_id, "status": "queued"}


async def _enqueue_farm_job(
    *,
    tenant_id: int,
    workspace_id: int | None,
    user_id: int | None,
    job_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enqueue a farm/parser/profile background job — same pattern as enqueue_app_job."""
    queue_name = _FARM_JOB_TYPE_TO_QUEUE.get(job_type)
    if not queue_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported_job_type")
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=tenant_id, user_id=user_id)
            job = AppJob(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                job_type=job_type,
                queue_name=queue_name,
                status="queued",
                payload=payload or {},
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(job)
            await session.flush()
            job_id = int(job.id)
    try:
        await task_queue.connect()
        await task_queue.enqueue(
            queue_name,
            {
                "job_id": job_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "job_type": job_type,
            },
        )
    except Exception:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id, user_id=user_id)
                job_obj = (await session.execute(
                    select(AppJob).where(AppJob.id == job_id)
                )).scalar_one_or_none()
                if job_obj is not None:
                    job_obj.status = "failed"
                    job_obj.error_code = "queue_unavailable"
                    job_obj.completed_at = utcnow()
                    job_obj.updated_at = utcnow()
    return {"job_id": job_id, "status": "queued"}

log = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# Simple in-memory rate limiter (no external dependency)
# Buckets are keyed by (scope, identifier) and store a deque of timestamps.
# ---------------------------------------------------------------------------

_rate_limit_buckets: dict[tuple[str, str], collections.deque] = collections.defaultdict(collections.deque)
_rate_limit_last_gc = time.monotonic()
_RATE_LIMIT_MAX_BUCKETS = 10_000
_RATE_LIMIT_GC_INTERVAL = 300  # seconds


_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB per file
_MAX_ZIP_BYTES = 50 * 1024 * 1024  # 50 MB for ZIP uploads


async def _read_upload_safe(upload: UploadFile, max_bytes: int = _MAX_UPLOAD_BYTES) -> bytes:
    """Read upload file with size limit to prevent memory exhaustion."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large (max {max_bytes} bytes)",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _escape_like(value: str) -> str:
    """Escape SQL LIKE metacharacters to prevent wildcard injection."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _check_rate_limit(
    scope: str, identifier: str, max_calls: int, window_seconds: int
) -> tuple[int, int]:
    """Check rate limit and return (remaining, reset_seconds).

    Raises HTTP 429 with a Retry-After header when the limit is exceeded.
    Returns the number of calls remaining in the current window and the
    number of seconds until the window fully resets.  Callers that want to
    expose the full quota on every response can pass these values to
    _apply_rate_limit_headers().
    """
    if str(settings.APP_ENV or "").strip().lower() in {"development", "test", "testing"}:
        return max_calls, window_seconds
    global _rate_limit_last_gc
    now = time.monotonic()
    # Periodic garbage collection of stale buckets
    if now - _rate_limit_last_gc > _RATE_LIMIT_GC_INTERVAL:
        _rate_limit_last_gc = now
        cutoff_gc = now - 600  # remove buckets idle for 10 min
        stale = [k for k, v in _rate_limit_buckets.items() if not v or v[-1] < cutoff_gc]
        for k in stale:
            del _rate_limit_buckets[k]
    # Cap total buckets to prevent memory exhaustion
    if len(_rate_limit_buckets) > _RATE_LIMIT_MAX_BUCKETS:
        return max_calls, window_seconds  # degrade gracefully: skip limiting rather than OOM
    bucket = _rate_limit_buckets[(scope, identifier)]
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= max_calls:
        # reset_seconds: time until the oldest call in the window expires
        reset_seconds = int(window_seconds - (now - bucket[0])) + 1 if bucket else window_seconds
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate_limit_exceeded",
            headers={"Retry-After": str(reset_seconds)},
        )
    bucket.append(now)
    remaining = max_calls - len(bucket)
    # reset_seconds: time until the oldest call in the window expires (0 if window is empty)
    reset_seconds = int(window_seconds - (now - bucket[0])) + 1 if bucket else window_seconds
    return remaining, reset_seconds


def _apply_rate_limit_headers(
    response: Response, limit: int, remaining: int, reset_seconds: int
) -> None:
    """Attach standard rate-limit headers to an already-constructed Response.

    Usage (optional, for endpoints that want to expose quota info):

        remaining, reset_seconds = _check_rate_limit("api", key, 60, 60)
        # ... build your response object ...
        _apply_rate_limit_headers(response, 60, remaining, reset_seconds)
    """
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_seconds)


_TRUSTED_PROXIES = frozenset({"127.0.0.1", "::1", "172.16.0.0/12", "10.0.0.0/8", "192.168.0.0/16"})


def _is_trusted_proxy(ip: str) -> bool:
    """Check if IP is a trusted reverse proxy (Docker network or localhost)."""
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
        for network in _TRUSTED_PROXIES:
            if "/" in network:
                if addr in ipaddress.ip_network(network):
                    return True
            elif ip == network:
                return True
    except ValueError:
        pass
    return False


def _client_ip(request: Request) -> str:
    """Extract the real client IP, trusting X-Forwarded-For only from Docker/nginx proxy."""
    direct_ip = request.client.host if request.client else "unknown"
    if _is_trusted_proxy(direct_ip):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            # Take the first (leftmost) IP — the original client
            return forwarded.split(",")[0].strip()
    return direct_ip


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
FRONTEND_DIR = BASE_DIR / "frontend"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"
FRONTEND_INDEX_PATH = FRONTEND_DIST_DIR / "index.html"
FRONTEND_ASSETS_DIR = FRONTEND_DIST_DIR / "assets"


class LeadCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    company: str = Field(min_length=1, max_length=255)
    telegram_username: Optional[str] = Field(default=None, max_length=255)
    use_case: str = Field(min_length=1, max_length=64)
    utm_source: Optional[str] = Field(default=None, max_length=255)

    @field_validator("name", "email", "company", "use_case", mode="before")
    @classmethod
    def strip_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("invalid_email")
        return value.lower()

    @field_validator("telegram_username")
    @classmethod
    def normalize_username(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.startswith("@"):
            normalized = normalized[1:]
        return normalized


class TelegramVerifyPayload(BaseModel):
    id: int
    auth_date: int
    hash: str = Field(min_length=16, max_length=255)
    username: Optional[str] = Field(default=None, max_length=255)
    first_name: Optional[str] = Field(default=None, max_length=255)
    last_name: Optional[str] = Field(default=None, max_length=255)
    photo_url: Optional[str] = Field(default=None, max_length=1024)


class CompleteProfilePayload(BaseModel):
    setup_token: str = Field(min_length=16, max_length=4096)
    email: str = Field(min_length=3, max_length=255)
    company: str = Field(min_length=1, max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("invalid_email")
        return value

    @field_validator("company")
    @classmethod
    def normalize_company(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("empty_company")
        return normalized


class EmailRegisterPayload(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=255)
    company: str = Field(min_length=1, max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("invalid_email")
        return value

    @field_validator("first_name", "company")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("empty_field")
        return normalized


class EmailLoginPayload(BaseModel):
    email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("invalid_email")
        return value


class BindProxyPayload(BaseModel):
    proxy_id: Optional[int] = None
    proxy_string: Optional[str] = Field(default=None, max_length=500)

    @field_validator("proxy_string")
    @classmethod
    def normalize_proxy_string(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("proxy_id")
    @classmethod
    def validate_proxy_id(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        if int(value) <= 0:
            raise ValueError("invalid_proxy_id")
        return int(value)


class ProxyBulkImportPayload(BaseModel):
    lines: str = Field(min_length=1, max_length=500_000)
    proxy_type: str = Field(default="http", max_length=10)

    @field_validator("proxy_type")
    @classmethod
    def validate_proxy_type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"http", "socks5", "socks4"}:
            raise ValueError("proxy_type must be http, socks5, or socks4")
        return value


class ProxyAutoAssignPayload(BaseModel):
    account_id: int = Field(gt=0)
    strategy: str = Field(default="healthiest", max_length=20)

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"healthiest", "round_robin", "random"}:
            raise ValueError("strategy must be healthiest, round_robin, or random")
        return value


class ProxyMassAssignPayload(BaseModel):
    account_ids: list[int] = Field(min_length=1, max_length=500)
    strategy: str = Field(default="round_robin", max_length=20)

    @field_validator("strategy")
    @classmethod
    def validate_mass_strategy(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"healthiest", "round_robin", "random"}:
            raise ValueError("strategy must be healthiest, round_robin, or random")
        return value


class AccountNotePayload(BaseModel):
    notes: str = Field(default="", max_length=4000)

    @field_validator("notes", mode="before")
    @classmethod
    def normalize_notes(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class AssistantMessagePayload(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class CreativeGeneratePayload(BaseModel):
    draft_type: str = Field(min_length=1, max_length=32)
    variant_count: int = Field(default=3, ge=1, le=3)

    @field_validator("draft_type")
    @classmethod
    def normalize_draft_type(cls, value: str) -> str:
        return value.strip().lower()


class CreativeApprovePayload(BaseModel):
    draft_id: int = Field(gt=0)
    selected_variant: Optional[int] = Field(default=None, ge=0, le=2)


class WorkspaceSummaryPayload(BaseModel):
    id: int
    name: str
    settings: dict[str, Any]
    created_at: Optional[str]


class SessionInfoItem(BaseModel):
    id: int
    user_agent: Optional[str]
    ip_address: Optional[str]
    created_at: Optional[str]
    last_used_at: Optional[str]
    expires_at: Optional[str]
    is_current: bool


class SessionListResponse(BaseModel):
    items: list[SessionInfoItem]
    total: int


# ---------------------------------------------------------------------------
# Session Topology — request / response models
# ---------------------------------------------------------------------------

class TopologyItemResponse(BaseModel):
    phone: str
    user_id: Optional[int]
    status_kind: str
    canonical_complete: bool
    safe_to_quarantine: bool
    canonical_session: Optional[str]
    canonical_metadata: Optional[str]
    flat_session: Optional[str]
    flat_metadata: Optional[str]
    legacy_sessions: list[str]
    legacy_metadata: list[str]
    legacy_dirs: list[str]


class TopologySummaryResponse(BaseModel):
    phones_total: int
    status_counts: dict[str, int]
    canonical_complete: int
    with_root_copies: int
    with_legacy_copies: int
    duplicate_copy_phones: int
    duplicate_phones: list[str]
    safe_to_quarantine: int


class TopologyAuditResponse(BaseModel):
    items: list[TopologyItemResponse]
    summary: TopologySummaryResponse


class QuarantinePayload(BaseModel):
    phones: list[str] = Field(default_factory=list)
    dry_run: bool = True


class QuarantineResponse(BaseModel):
    ok: bool
    dry_run: bool
    quarantine_dir: str
    moved_files: int
    moved_phones: list[str]
    files: list[str]
    skipped: list[dict[str, str]]
    skipped_count: int


# ---------------------------------------------------------------------------
# Farm Orchestrator — request / response models
# ---------------------------------------------------------------------------

class FarmCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    comment_prompt: Optional[str] = Field(default=None, max_length=10000)
    comment_tone: str = Field(default="neutral", max_length=50)
    comment_language: str = Field(default="auto", max_length=10)
    comment_all_posts: bool = True
    comment_percentage: int = Field(default=100, ge=1, le=100)
    delay_before_comment_min: int = Field(default=30, ge=0)
    delay_before_comment_max: int = Field(default=120, ge=0)
    delay_before_join_min: int = Field(default=60, ge=0)
    delay_before_join_max: int = Field(default=300, ge=0)
    max_threads: int = Field(default=50, ge=1, le=500)
    ai_protection_mode: str = Field(default="aggressive", max_length=20)
    auto_responder_enabled: bool = False
    auto_responder_prompt: Optional[str] = Field(default=None, max_length=10000)
    auto_responder_redirect_url: Optional[str] = Field(default=None, max_length=2000)
    mode: str = Field(default="multithread", max_length=20)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
        return value

    @field_validator("comment_tone")
    @classmethod
    def validate_tone(cls, value: str) -> str:
        allowed = {"neutral", "hater", "flirt", "native", "custom"}
        if value not in allowed:
            raise ValueError(f"comment_tone must be one of {allowed}")
        return value

    @field_validator("ai_protection_mode")
    @classmethod
    def validate_protection_mode(cls, value: str) -> str:
        allowed = {"off", "aggressive", "conservative"}
        if value not in allowed:
            raise ValueError(f"ai_protection_mode must be one of {allowed}")
        return value


class FarmUpdatePayload(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    comment_prompt: Optional[str] = Field(default=None, max_length=10000)
    comment_tone: Optional[str] = Field(default=None, max_length=50)
    comment_language: Optional[str] = Field(default=None, max_length=10)
    comment_all_posts: Optional[bool] = None
    comment_percentage: Optional[int] = Field(default=None, ge=1, le=100)
    delay_before_comment_min: Optional[int] = Field(default=None, ge=0)
    delay_before_comment_max: Optional[int] = Field(default=None, ge=0)
    delay_before_join_min: Optional[int] = Field(default=None, ge=0)
    delay_before_join_max: Optional[int] = Field(default=None, ge=0)
    max_threads: Optional[int] = Field(default=None, ge=1, le=500)
    ai_protection_mode: Optional[str] = Field(default=None, max_length=20)
    auto_responder_enabled: Optional[bool] = None
    auto_responder_prompt: Optional[str] = Field(default=None, max_length=10000)
    auto_responder_redirect_url: Optional[str] = Field(default=None, max_length=2000)


class FarmStartPayload(BaseModel):
    account_ids: List[int] = Field(min_length=1, max_length=500)
    channel_database_id: int = Field(gt=0)


class ChannelDbCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
        return value


class ChannelImportItem(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    title: Optional[str] = Field(default=None, max_length=300)
    member_count: Optional[int] = None
    has_comments: bool = True
    language: Optional[str] = Field(default=None, max_length=10)
    category: Optional[str] = Field(default=None, max_length=100)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().lstrip("@")
        return value


class ChannelImportPayload(BaseModel):
    channels: List[ChannelImportItem] = Field(min_length=1, max_length=10000)


class ParserChannelsPayload(BaseModel):
    keywords: List[str] = Field(min_length=1, max_length=50)
    filters: Optional[dict[str, Any]] = None
    max_results: int = Field(default=50, ge=1, le=500)
    account_id: Optional[int] = Field(default=None, gt=0)
    target_database_id: Optional[int] = Field(default=None, gt=0)

    @field_validator("keywords", mode="before")
    @classmethod
    def clean_keywords(cls, value: object) -> object:
        if isinstance(value, list):
            return [str(k).strip() for k in value if str(k).strip()]
        return value


class ProfileGeneratePayload(BaseModel):
    account_id: int = Field(gt=0)
    template_id: Optional[int] = Field(default=None, gt=0)


class ProfileMassGeneratePayload(BaseModel):
    account_ids: List[int] = Field(min_length=1, max_length=500)
    template_id: Optional[int] = Field(default=None, gt=0)


class ProfileApplyPayload(BaseModel):
    first_name: Optional[str] = Field(default=None, max_length=255)
    last_name: Optional[str] = Field(default=None, max_length=255)
    bio: Optional[str] = Field(default=None, max_length=1000)
    avatar_url: Optional[str] = Field(default=None, max_length=2000)


class ProfileCreateChannelPayload(BaseModel):
    channel_name: str = Field(min_length=1, max_length=200)
    channel_description: Optional[str] = Field(default=None, max_length=2000)
    first_post_text: Optional[str] = Field(default=None, max_length=4096)
    avatar_url: Optional[str] = Field(default=None, max_length=2000)


class ProfileTemplateCreatePayload(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    gender: Optional[str] = Field(default=None, max_length=10)
    geo: Optional[str] = Field(default=None, max_length=50)
    bio_template: Optional[str] = Field(default=None, max_length=4000)
    channel_name_template: Optional[str] = Field(default=None, max_length=4000)
    channel_description_template: Optional[str] = Field(default=None, max_length=4000)
    channel_first_post_template: Optional[str] = Field(default=None, max_length=4000)
    avatar_style: Optional[str] = Field(default=None, max_length=50)
    avatar_url: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        allowed = {"male", "female", "any"}
        if value not in allowed:
            raise ValueError(f"gender must be one of {allowed}")
        return value

    @field_validator("avatar_style")
    @classmethod
    def validate_avatar_style(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        allowed = {"ai_generated", "library", "custom"}
        if value not in allowed:
            raise ValueError(f"avatar_style must be one of {allowed}")
        return value


# ---------------------------------------------------------------------------
# Sprint 6 — Warmup + Health request/response models
# ---------------------------------------------------------------------------


class WarmupCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mode: str = Field(default="conservative", max_length=20)
    safety_limit_actions_per_hour: int = Field(default=5, ge=1, le=100)
    active_hours_start: int = Field(default=9, ge=0, le=23)
    active_hours_end: int = Field(default=23, ge=0, le=23)
    warmup_duration_minutes: int = Field(default=30, ge=5, le=480)
    interval_between_sessions_hours: int = Field(default=6, ge=1, le=72)
    enable_reactions: bool = True
    enable_read_channels: bool = True
    enable_dialogs_between_accounts: bool = True
    target_channels: List[str] = Field(default_factory=list, max_length=500)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
        return value

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        allowed = {"conservative", "moderate", "aggressive"}
        if value not in allowed:
            raise ValueError(f"mode must be one of {allowed}")
        return value


class WarmupUpdatePayload(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    mode: Optional[str] = Field(default=None, max_length=20)
    safety_limit_actions_per_hour: Optional[int] = Field(default=None, ge=1, le=100)
    active_hours_start: Optional[int] = Field(default=None, ge=0, le=23)
    active_hours_end: Optional[int] = Field(default=None, ge=0, le=23)
    warmup_duration_minutes: Optional[int] = Field(default=None, ge=5, le=480)
    interval_between_sessions_hours: Optional[int] = Field(default=None, ge=1, le=72)
    enable_reactions: Optional[bool] = None
    enable_read_channels: Optional[bool] = None
    enable_dialogs_between_accounts: Optional[bool] = None
    target_channels: Optional[List[str]] = Field(default=None, max_length=500)


# Sprint 7 — advanced module payloads

class ReactionJobCreatePayload(BaseModel):
    channel_username: str = Field(min_length=1, max_length=200)
    reaction_type: str = Field(default="random", max_length=20)
    account_ids: List[int] = Field(min_length=1, max_length=500)
    post_id: Optional[int] = None


class ChattingConfigCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mode: str = Field(default="conservative", max_length=20)
    target_channels: List[str] = Field(default_factory=list, max_length=500)
    prompt_template: Optional[str] = None
    max_messages_per_hour: int = Field(default=5, ge=1, le=50)
    min_delay_seconds: int = Field(default=120, ge=30)
    max_delay_seconds: int = Field(default=600, ge=60)
    account_ids: List[int] = Field(default_factory=list, max_length=500)


class DialogConfigCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dialog_type: str = Field(default="warmup", max_length=30)
    account_pairs: List[List[int]] = Field(default_factory=list, max_length=200)
    prompt_template: Optional[str] = None
    messages_per_session: int = Field(default=5, ge=1, le=20)
    session_interval_hours: int = Field(default=4, ge=1, le=48)


class UserParsePayload(BaseModel):
    channel_username: str = Field(min_length=1, max_length=200)
    account_id: int


class FolderCreatePayload(BaseModel):
    account_id: int
    folder_name: str = Field(min_length=1, max_length=200)
    channel_usernames: List[str] = Field(default_factory=list, max_length=500)


# ---------------------------------------------------------------------------
# Sprint 8 Pydantic models
# ---------------------------------------------------------------------------

VALID_CAMPAIGN_TYPES = {"commenting", "reactions", "chatting", "mixed"}


class CampaignCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    campaign_type: str = Field(default="commenting", max_length=30)
    account_ids: List[int] = Field(default_factory=list, max_length=500)
    channel_database_id: Optional[int] = None
    comment_prompt: Optional[str] = None
    comment_tone: Optional[str] = None
    comment_language: str = Field(default="ru", max_length=10)
    schedule_type: str = Field(default="continuous", max_length=20)
    schedule_config: Optional[dict] = None
    budget_daily_actions: int = Field(default=100, ge=1, le=10000)
    budget_total_actions: Optional[int] = Field(default=None, ge=1)


class CampaignUpdatePayload(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    account_ids: Optional[List[int]] = None
    channel_database_id: Optional[int] = None
    comment_prompt: Optional[str] = None
    comment_tone: Optional[str] = None
    schedule_type: Optional[str] = None
    schedule_config: Optional[dict] = None
    budget_daily_actions: Optional[int] = Field(default=None, ge=1, le=10000)
    budget_total_actions: Optional[int] = Field(default=None, ge=1)


class ChannelMapSearchPayload(BaseModel):
    query: Optional[str] = None
    category: Optional[str] = None
    language: Optional[str] = None
    min_members: Optional[int] = None
    limit: int = Field(default=50, ge=1, le=500)


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MARKETING_PAGES = {
    "home": {
        "path": "/",
        "slug": "home",
        "title": "Telegram Growth OS для брендов — автоматизируй рост в Telegram",
        "description": "NEURO COMMENTING помогает growth-командам находить релевантные Telegram-каналы, запускать кампании и собирать лиды в одном premium workflow.",
        "headline": "Telegram Growth OS для брендов — автоматизируй рост в Telegram",
        "subheadline": "Discovery, кампании, AI-черновики и аналитика в одном рабочем контуре для growth-команды.",
        "bullets": [
            "Находите каналы и сообщества, где уже есть спрос на ваш продукт.",
            "Переходите от discovery к кампании без ручной путаницы между таблицами и чатами.",
            "Получайте AI-черновики, approvals и growth-аналитику в одном месте.",
        ],
        "segment_title": "Для growth-команд, которые хотят видеть Telegram как полноценный revenue-канал",
        "segment_points": [
            "Быстрое исследование ниш и конкурентных площадок.",
            "Кампании и направления без потери контроля и контекста.",
            "От lead capture до операционного follow-up внутри одного процесса.",
        ],
    },
    "ecom": {
        "path": "/ecom",
        "slug": "ecom",
        "title": "Telegram Growth OS для интернет-магазинов",
        "description": "Для ecom-команд: поиск нишевых Telegram-каналов, кампании, подборки и AI-assisted growth workflow.",
        "headline": "Рост интернет-магазина в Telegram без хаоса",
        "subheadline": "Для брендов, которые хотят собирать спрос, находить площадки и быстро запускать Telegram-кампании.",
        "bullets": [
            "Находите каналы и чаты, где уже обсуждают товары, скидки и тематические подборки.",
            "Собирайте кампании под сезонные акции, новые линейки и товарные дропы.",
            "Держите площадки, черновики и follow-up в одном кабинете.",
        ],
        "segment_title": "Что особенно важно для ecom",
        "segment_points": [
            "Товарный спрос и каталоги по нишам.",
            "Сезонные предложения, промо и подборки.",
            "Ускоренный путь от discovery до первых лидов.",
        ],
    },
    "edtech": {
        "path": "/edtech",
        "slug": "edtech",
        "title": "Telegram Growth OS для онлайн-школ",
        "description": "Для edtech-команд: Telegram discovery, AI-черновики, кампании и аналитика для воронок набора.",
        "headline": "Telegram-рост для онлайн-школ и образовательных продуктов",
        "subheadline": "От исследования ниши до запуска кампаний под наборы и вебинары — без потери контекста между маркетингом и операционкой.",
        "bullets": [
            "Находите площадки, где обсуждают карьеру, навыки, переобучение и апгрейд профессии.",
            "Планируйте кампании под вебинары, наборы и evergreen-продукты.",
            "Получайте AI-черновики и активационную аналитику в одной системе.",
        ],
        "segment_title": "Что особенно важно для edtech",
        "segment_points": [
            "Площадки с аудиторией, готовой к обучению.",
            "Наборы, вебинары и evergreen-воронки в одном pipeline.",
            "Понятный follow-up по лидам и интересу к продукту.",
        ],
    },
    "saas": {
        "path": "/saas",
        "slug": "saas",
        "title": "Telegram Growth OS для SaaS-продуктов",
        "description": "Для SaaS-команд: канальный ресерч, AI-черновики, кампании и операционная аналитика роста в Telegram.",
        "headline": "Telegram Growth OS для SaaS-команд",
        "subheadline": "Сводите discovery, кампании, AI-черновики и аналитику в один управляемый pipeline роста.",
        "bullets": [
            "Ищите комьюнити и каналы, где обсуждают ваш use case, боли и конкурентов.",
            "Разводите спрос по сегментам: SMB, agency, startup, product teams.",
            "Управляйте ростом как продуктовой функцией, а не набором хаотичных действий.",
        ],
        "segment_title": "Что особенно важно для SaaS",
        "segment_points": [
            "B2B discovery по нишам и use cases.",
            "Повторяемые кампании и pipeline approvals.",
            "Прозрачная аналитика по лидам, площадкам и активности.",
        ],
    },
}

TRUST_STRIP = [
    "Discovery",
    "Campaign orchestration",
    "AI drafts",
    "Lead capture",
    "Analytics",
]

PROOF_METRICS = [
    {"value": "24/7", "label": "контроль роста и discovery"},
    {"value": "<48ч", "label": "реакция на новые заявки"},
    {"value": "1 OS", "label": "для parser, drafts и campaigns"},
]

FEATURES = [
    {
        "title": "Discovery Engine",
        "body": "Находите релевантные Telegram-каналы, обсуждения и комьюнити по нишам и сценариям роста.",
    },
    {
        "title": "Campaign Control",
        "body": "Управляйте направлениями, approvals, safety rings и кампанийным pipeline в одном рабочем слое.",
    },
    {
        "title": "AI Draft Studio",
        "body": "Готовьте черновики комментариев, ответов и кампанийного контента без потери tone of voice.",
    },
    {
        "title": "Lead Funnel",
        "body": "Собирайте лиды с лендинга, дублируйте их в Sheets и получайте Telegram-уведомления без ручной рутины.",
    },
    {
        "title": "Analytics Layer",
        "body": "Видите кампании, находки, активность и usage в одном growth dashboard, а не по кускам.",
    },
]

PRICING = [
    {
        "name": "Pro",
        "price": "$499/mo",
        "note": "Для первых growth-команд",
        "featured": False,
        "bullets": [
            "Discovery и parser workflow",
            "AI-черновики и lead capture",
            "Стартовый campaign control",
        ],
    },
    {
        "name": "Scale",
        "price": "$1 499/mo",
        "note": "Для команд, которым нужен repeatable growth",
        "featured": True,
        "bullets": [
            "Несколько workspaces и больше каналов",
            "Управляемые approvals и campaign pipeline",
            "Расширенные usage и activation метрики",
        ],
    },
    {
        "name": "Business",
        "price": "$4 999/mo",
        "note": "Для управляемых команд и агентств",
        "featured": False,
        "bullets": [
            "Высокие лимиты и multi-tenant ops",
            "Глубокий parser + drafts workflow",
            "Подготовка к agency и enterprise motion",
        ],
    },
]

FAQ = [
    ("Что такое Telegram Growth OS?", "Это единая операционная система для discovery, кампаний, AI-черновиков, аналитики и growth-команд в Telegram."),
    ("Для кого продукт?", "Для брендов и growth-команд RU/CIS mid-market, которые используют Telegram как acquisition и community-канал."),
    ("Что я получу после заявки?", "Мы свяжемся с вами, уточним ваш use case и покажем, как построить Telegram growth workflow под ваш продукт."),
    ("Нужна ли отдельная команда разработки?", "Нет, продукт рассчитан на маркетинг и growth-операторов. Технический слой уже встроен в платформу."),
    ("Можно ли начать с пилота?", "Да. Стартовый путь рассчитан на пилоты, onboarding и дальнейший переход на полноценный subscription workflow."),
]


@dataclass
class TenantContext:
    user_id: int
    tenant_id: int
    workspace_id: int | None
    role: str
    token_type: str


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "").strip()
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer_token")
    return header.split(" ", 1)[1].strip()


def _decode_jwt(token: str) -> TenantContext:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_ACCESS_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token") from exc

    token_type = str(payload.get("type") or "")
    if token_type != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token_type")

    try:
        user_id = int(payload["sub"])
        tenant_id = int(payload["tenant_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token_payload") from exc

    workspace_raw = payload.get("workspace_id")
    workspace_id = int(workspace_raw) if workspace_raw is not None else None
    return TenantContext(
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role=str(payload.get("role") or "member"),
        token_type=token_type,
    )


async def require_internal_token(request: Request) -> None:
    token = _bearer_token(request)
    if not token or not settings.OPS_API_TOKEN or not hmac.compare_digest(token, settings.OPS_API_TOKEN):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_internal_token")


async def require_admin_auth(request: Request) -> None:
    """Accept either OPS_API_TOKEN (internal) or a JWT with role owner/admin.

    This allows the AdminPage (JWT-authenticated) and internal tooling
    (OPS_API_TOKEN) to both reach the /v1/admin/* endpoints.
    """
    token = _bearer_token(request)
    # Fast path: internal ops token (guard against empty-token bypass)
    if token and settings.OPS_API_TOKEN and hmac.compare_digest(token, settings.OPS_API_TOKEN):
        return
    # Slow path: valid JWT with elevated role
    try:
        ctx = _decode_jwt(token)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_admin_credentials")
    if ctx.role not in ("owner", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin_role_required")


async def get_tenant_context(request: Request) -> TenantContext:
    token = _bearer_token(request)
    tenant_context = _decode_jwt(token)
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(
                session,
                tenant_id=tenant_context.tenant_id,
                user_id=tenant_context.user_id,
            )
            tenant = (await session.execute(
                select(Tenant).where(Tenant.id == tenant_context.tenant_id)
            )).scalar_one_or_none()
            if tenant is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="tenant_not_found")
            if tenant.status == "suspended":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant_suspended")
            membership_query = (
                select(TeamMember)
                .where(
                    TeamMember.user_id == tenant_context.user_id,
                    TeamMember.tenant_id == tenant_context.tenant_id,
                )
                .order_by(TeamMember.id.asc())
            )
            if tenant_context.workspace_id is not None:
                membership_query = membership_query.where(TeamMember.workspace_id == tenant_context.workspace_id)
            membership_result = await session.execute(membership_query)
            membership = membership_result.scalar_one_or_none()
            if membership is None:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant_membership_not_found")
            tenant_context = TenantContext(
                user_id=tenant_context.user_id,
                tenant_id=tenant_context.tenant_id,
                workspace_id=int(membership.workspace_id),
                role=str(membership.role or tenant_context.role),
                token_type=tenant_context.token_type,
            )

    request.state.tenant_context = tenant_context
    return tenant_context


async def tenant_session(
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(
                session,
                tenant_id=tenant_context.tenant_id,
                user_id=tenant_context.user_id,
            )
            yield session


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_critical_secrets(settings)
    await init_db()
    stop_event = asyncio.Event()
    worker_tasks: list[asyncio.Task[Any]] = []
    try:
        try:
            await task_queue.connect()
            for queue_name in ASSISTANT_QUEUE_NAMES:
                worker_tasks.append(
                    asyncio.create_task(
                        assistant_worker_loop(
                            queue_name,
                            stop_event=stop_event,
                            consumer_id=f"ops-api-{queue_name}",
                        )
                    )
                )
        except Exception as exc:  # pragma: no cover - runtime fallback
            log.warning(f"assistant worker startup skipped: {exc}")
        # Init Redis for bot auth (shared state between ops_api and bot containers)
        try:
            await _init_bot_auth_redis(settings.REDIS_URL)
        except Exception as exc:  # pragma: no cover
            log.warning(f"bot auth Redis init skipped: {exc}")
        yield
    finally:
        stop_event.set()
        for task in worker_tasks:
            task.cancel()
        if worker_tasks:
            await asyncio.gather(*worker_tasks, return_exceptions=True)
        await _close_bot_auth_redis()
        await task_queue.close()
        await dispose_engine()


app = FastAPI(title="NEURO COMMENTING Ops API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        [settings.WEBAPP_DEV_ORIGIN, "http://127.0.0.1:5173"]
        if settings.APP_ENV in ("development", "test")
        else [f"https://{settings.PUBLIC_DOMAIN}"] if hasattr(settings, "PUBLIC_DOMAIN") and settings.PUBLIC_DOMAIN
        else []
    ),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.APP_ENV == "production" and settings.PUBLIC_DOMAIN:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
if FRONTEND_ASSETS_DIR.exists():
    app.mount("/app/assets", StaticFiles(directory=str(FRONTEND_ASSETS_DIR)), name="app-assets")
_pwa_icons_dir = FRONTEND_DIR / "public" / "pwa"
if _pwa_icons_dir.exists():
    app.mount("/static/pwa", StaticFiles(directory=str(_pwa_icons_dir)), name="pwa-icons")


_ALLOWED_HOSTS: set[str] = set()
if hasattr(settings, "PUBLIC_DOMAIN") and settings.PUBLIC_DOMAIN:
    _ALLOWED_HOSTS.add(settings.PUBLIC_DOMAIN)
_ALLOWED_HOSTS.update({"localhost", "127.0.0.1", "176-124-221-253.sslip.io"})


def _safe_host(request: Request) -> tuple[str, str]:
    """Return (proto, host) with host validated against allowlist to prevent host header injection."""
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).strip()
    # Strip port for comparison
    host_no_port = host.split(":")[0]
    if host_no_port not in _ALLOWED_HOSTS and settings.APP_ENV not in ("development", "test"):
        host = settings.PUBLIC_DOMAIN if hasattr(settings, "PUBLIC_DOMAIN") and settings.PUBLIC_DOMAIN else request.url.netloc
        proto = "https"
    return proto, host


def _page_context(request: Request, page: dict[str, object]) -> dict[str, object]:
    proto, host = _safe_host(request)
    base_url = f"{proto}://{host}".rstrip("/")
    return {
        "request": request,
        "page": page,
        "trust_strip": TRUST_STRIP,
        "proof_metrics": PROOF_METRICS,
        "features": FEATURES,
        "pricing": PRICING,
        "faq": FAQ,
        "success_message": "Вы в списке — мы напишем вам в ближайшие 48 часов",
        "base_url": base_url,
        "og_image": f"{base_url}/static/og-default.svg",
        "static_css_url": f"{base_url}/static/marketing.css",
    }


def _request_is_secure(request: Request) -> bool:
    forwarded_proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").strip().lower()
    if forwarded_proto == "https":
        return True
    return str(settings.APP_ENV or "").strip().lower() in {"staging", "production"}


def _set_refresh_cookie(response: Response, request: Request, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.WEBAPP_SESSION_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=_request_is_secure(request),
        samesite="lax",
        max_age=max(1, int(settings.JWT_REFRESH_TTL_DAYS)) * 24 * 60 * 60,
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.WEBAPP_SESSION_COOKIE_NAME,
        path="/",
        samesite="lax",
    )


def _auth_bundle_payload(bundle: WebAuthBundle) -> dict[str, Any]:
    return {
        "status": bundle.status,
        "access_token": bundle.access_token,
        "setup_token": bundle.setup_token,
        "user": bundle.user,
        "tenant": bundle.tenant,
        "workspace": bundle.workspace,
        "onboarding": bundle.onboarding,
    }


def _frontend_ready() -> bool:
    return FRONTEND_INDEX_PATH.exists()


def _frontend_unavailable_html() -> str:
    return (
        "<html><head><title>NEURO COMMENTING App</title></head>"
        "<body style='font-family: sans-serif; padding: 32px;'>"
        "<h1>Frontend build not found</h1>"
        "<p>Соберите Vite frontend: <code>cd frontend && npm install && npm run build</code></p>"
        "</body></html>"
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health")
async def health_check() -> JSONResponse:
    """Detailed health check — verifies DB and Redis connectivity."""
    db_ok = False
    redis_ok = False

    # DB probe
    try:
        async with async_session() as session:
            await session.execute(select(func.now()))
        db_ok = True
    except Exception as exc:
        log.warning("health: db probe failed: %s", exc)

    # Redis probe
    try:
        await task_queue.connect()
        redis_ok = await task_queue.ping()
    except Exception as exc:
        log.warning("health: redis probe failed: %s", exc)

    overall = "ok" if (db_ok and redis_ok) else "degraded"
    return JSONResponse(
        status_code=status.HTTP_200_OK if overall == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": overall, "db": db_ok, "redis": redis_ok, "version": "sprint-4"},
    )


@app.get("/auth/telegram/widget-config")
async def telegram_widget_config(request: Request) -> dict[str, Any]:
    proto, host = _safe_host(request)
    return {
        "bot_username": settings.ADMIN_BOT_USERNAME,
        "auth_domain": host,
        "origin": f"{proto}://{host}",
        "max_age_seconds": TELEGRAM_AUTH_MAX_AGE_SECONDS,
    }


@app.post("/auth/telegram/verify")
async def auth_telegram_verify(payload: TelegramVerifyPayload, request: Request) -> JSONResponse:
    _check_rate_limit("auth", _client_ip(request), max_calls=10, window_seconds=60)
    try:
        async with async_session() as session:
            async with session.begin():
                bundle = await verify_telegram_login(
                    session,
                    payload.model_dump(exclude_none=True),
                    user_agent=request.headers.get("user-agent"),
                    ip_address=request.client.host if request.client else None,
                )
        response = JSONResponse(status_code=status.HTTP_200_OK, content=_auth_bundle_payload(bundle))
        if bundle.refresh_token:
            _set_refresh_cookie(response, request, bundle.refresh_token)
        return response
    except Exception:
        log.exception(
            "telegram verify failed (id=%s username=%s auth_date=%s ip=%s ua=%s)",
            payload.id,
            payload.username,
            payload.auth_date,
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
        )
        log.error("telegram verify failed", exc_info=True)
        raise


@app.post("/auth/complete-profile")
async def auth_complete_profile(payload: CompleteProfilePayload, request: Request) -> JSONResponse:
    _check_rate_limit("auth", _client_ip(request), max_calls=10, window_seconds=60)
    try:
        async with async_session() as session:
            async with session.begin():
                bundle = await complete_profile(
                    session,
                    payload.setup_token,
                    email=payload.email,
                    company=payload.company,
                    user_agent=request.headers.get("user-agent"),
                    ip_address=request.client.host if request.client else None,
                )
        response = JSONResponse(status_code=status.HTTP_200_OK, content=_auth_bundle_payload(bundle))
        if bundle.refresh_token:
            _set_refresh_cookie(response, request, bundle.refresh_token)
        return response
    except Exception:
        log.exception(
            "complete profile failed (email=%s company=%s ip=%s ua=%s)",
            payload.email,
            payload.company,
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
        )
        log.error("complete profile failed", exc_info=True)
        raise


@app.post("/auth/register")
async def auth_register(payload: EmailRegisterPayload, request: Request) -> JSONResponse:
    _check_rate_limit("auth", _client_ip(request), max_calls=5, window_seconds=60)
    try:
        async with async_session() as session:
            async with session.begin():
                bundle = await register_with_email(
                    session,
                    email=payload.email,
                    password=payload.password,
                    first_name=payload.first_name,
                    company=payload.company,
                    user_agent=request.headers.get("user-agent"),
                    ip_address=request.client.host if request.client else None,
                )
        response = JSONResponse(status_code=status.HTTP_201_CREATED, content=_auth_bundle_payload(bundle))
        if bundle.refresh_token:
            _set_refresh_cookie(response, request, bundle.refresh_token)
        return response
    except EmailAuthError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception:
        log.exception(
            "email register failed (email=%s ip=%s ua=%s)",
            payload.email,
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
        )
        raise


@app.post("/auth/login")
async def auth_login(payload: EmailLoginPayload, request: Request) -> JSONResponse:
    _check_rate_limit("auth", _client_ip(request), max_calls=10, window_seconds=60)
    try:
        async with async_session() as session:
            async with session.begin():
                bundle = await login_with_email(
                    session,
                    email=payload.email,
                    password=payload.password,
                    user_agent=request.headers.get("user-agent"),
                    ip_address=request.client.host if request.client else None,
                )
        response = JSONResponse(status_code=status.HTTP_200_OK, content=_auth_bundle_payload(bundle))
        if bundle.refresh_token:
            _set_refresh_cookie(response, request, bundle.refresh_token)
        return response
    except EmailAuthError as exc:
        code = str(exc)
        if code == "use_telegram_login":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=code) from exc
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials") from exc
    except Exception:
        log.exception(
            "email login failed (email=%s ip=%s ua=%s)",
            payload.email,
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
        )
        raise


@app.post("/auth/telegram/bot-start")
async def auth_telegram_bot_start(request: Request) -> JSONResponse:
    """Generate an auth code and return the bot deep link."""
    _check_rate_limit("auth", _client_ip(request), max_calls=10, window_seconds=60)
    if not settings.AUTH_BOT_TOKEN:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="bot_auth_not_configured")
    code = await generate_auth_code()
    bot_username = settings.AUTH_BOT_USERNAME
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "code": code,
            "bot_username": bot_username,
            "deep_link": f"https://t.me/{bot_username}?start=auth_{code}",
        },
    )


@app.get("/auth/telegram/bot-check")
async def auth_telegram_bot_check(code: str, request: Request) -> JSONResponse:
    """Poll to check if the bot has confirmed the auth code."""
    _check_rate_limit("auth_poll", _client_ip(request), max_calls=60, window_seconds=60)
    pending = await get_pending_auth(code)
    if not pending:
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "expired"})
    if not pending.get("confirmed"):
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "pending"})

    # Consume the auth data and create/login the user
    tg_user = await consume_pending_auth(code)
    if not tg_user:
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "expired"})

    # Use existing telegram verify flow to create/login user
    try:
        async with async_session() as session:
            async with session.begin():
                bundle = await verify_telegram_login(
                    session,
                    payload=tg_user,
                    bot_token=settings.AUTH_BOT_TOKEN,
                    skip_hash_check=True,
                    user_agent=request.headers.get("user-agent"),
                    ip_address=request.client.host if request.client else None,
                )
        resp_payload = _auth_bundle_payload(bundle)
        response = JSONResponse(status_code=status.HTTP_200_OK, content=resp_payload)
        if bundle.refresh_token:
            _set_refresh_cookie(response, request, bundle.refresh_token)
        return response
    except TelegramAuthError as exc:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "error", "detail": str(exc)},
        )
    except Exception:
        log.exception("bot auth check failed for code=%s", code[:8] if code else "?")
        raise


@app.post("/auth/refresh")
async def auth_refresh(
    request: Request,
    refresh_token: Optional[str] = Cookie(default=None, alias=settings.WEBAPP_SESSION_COOKIE_NAME),
) -> JSONResponse:
    _check_rate_limit("auth", _client_ip(request), max_calls=10, window_seconds=60)
    if not refresh_token:
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "anonymous"})
    async with async_session() as session:
        async with session.begin():
            try:
                bundle = await refresh_web_session(
                    session,
                    refresh_token,
                    user_agent=request.headers.get("user-agent"),
                    ip_address=request.client.host if request.client else None,
                )
            except TelegramAuthError:
                response = JSONResponse(status_code=status.HTTP_200_OK, content={"status": "anonymous"})
                _clear_refresh_cookie(response)
                return response
    response = JSONResponse(status_code=status.HTTP_200_OK, content=_auth_bundle_payload(bundle))
    if bundle.refresh_token:
        _set_refresh_cookie(response, request, bundle.refresh_token)
    return response


@app.post("/auth/logout")
async def auth_logout(
    request: Request,
    refresh_token: Optional[str] = Cookie(default=None, alias=settings.WEBAPP_SESSION_COOKIE_NAME),
) -> JSONResponse:
    _check_rate_limit("auth", _client_ip(request), max_calls=10, window_seconds=60)
    async with async_session() as session:
        async with session.begin():
            await logout_web_session(session, refresh_token)
    response = JSONResponse(status_code=status.HTTP_200_OK, content={"ok": True})
    _clear_refresh_cookie(response)
    return response


@app.get("/auth/me")
async def auth_me(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    return await get_me_payload(
        session,
        auth_user_id=tenant_context.user_id,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
    )


@app.get("/auth/sessions", response_model=SessionListResponse)
async def auth_sessions_list(
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
    refresh_token: Optional[str] = Cookie(default=None, alias=settings.WEBAPP_SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    """List all active sessions for the current user."""
    current_hash = _refresh_token_hash(refresh_token) if refresh_token else None
    items = await list_user_sessions(
        session,
        user_id=tenant_context.user_id,
        tenant_id=tenant_context.tenant_id,
        current_token_hash=current_hash,
    )
    return {"items": items, "total": len(items)}


@app.delete("/auth/sessions/{session_id}", status_code=status.HTTP_200_OK)
async def auth_sessions_revoke_one(
    session_id: int,
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
    refresh_token: Optional[str] = Cookie(default=None, alias=settings.WEBAPP_SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    """Revoke a specific session by id (cannot revoke the current session)."""
    current_hash = _refresh_token_hash(refresh_token) if refresh_token else None
    try:
        await revoke_session_by_id(
            session,
            session_id=session_id,
            user_id=tenant_context.user_id,
            tenant_id=tenant_context.tenant_id,
            current_token_hash=current_hash,
        )
    except TelegramAuthError as exc:
        code = str(exc)
        if code == "session_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=code) from exc
        if code == "cannot_revoke_current_session":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=code) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=code) from exc
    return {"ok": True}


@app.delete("/auth/sessions", status_code=status.HTTP_200_OK)
async def auth_sessions_revoke_all(
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
    refresh_token: Optional[str] = Cookie(default=None, alias=settings.WEBAPP_SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    """Revoke all sessions except the current one."""
    current_hash = _refresh_token_hash(refresh_token) if refresh_token else None
    revoked = await revoke_all_other_sessions(
        session,
        user_id=tenant_context.user_id,
        tenant_id=tenant_context.tenant_id,
        current_token_hash=current_hash,
    )
    return {"ok": True, "revoked": revoked}


@app.get("/v1/me/workspace", response_model=WorkspaceSummaryPayload)
async def me_workspace(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    payload = await get_me_payload(
        session,
        auth_user_id=tenant_context.user_id,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
    )
    return payload["workspace"]


@app.get("/v1/me/team")
async def me_team(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    return await get_team_payload(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
    )


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "marketing/landing.html", _page_context(request, MARKETING_PAGES["home"]))


@app.get("/ecom", response_class=HTMLResponse)
async def landing_ecom(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "marketing/landing.html", _page_context(request, MARKETING_PAGES["ecom"]))


@app.get("/edtech", response_class=HTMLResponse)
async def landing_edtech(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "marketing/landing.html", _page_context(request, MARKETING_PAGES["edtech"]))


@app.get("/saas", response_class=HTMLResponse)
async def landing_saas(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "marketing/landing.html", _page_context(request, MARKETING_PAGES["saas"]))


@app.post("/api/leads")
async def create_lead(payload: LeadCreatePayload, request: Request) -> JSONResponse:
    _check_rate_limit("public", _client_ip(request), max_calls=30, window_seconds=60)
    utm_source = payload.utm_source or request.query_params.get("utm_source")
    lead_snapshot: Optional[LeadSnapshot] = None
    async with async_session() as session:
        async with session.begin():
            lead = Lead(
                name=payload.name,
                email=payload.email,
                company=payload.company,
                telegram_username=payload.telegram_username,
                use_case=payload.use_case,
                utm_source=utm_source,
            )
            session.add(lead)
            await session.flush()
            lead_snapshot = LeadSnapshot(
                lead_id=int(lead.id or 0),
                name=lead.name,
                email=lead.email,
                company=lead.company,
                telegram_username=lead.telegram_username,
                use_case=lead.use_case,
                utm_source=lead.utm_source,
                created_at=lead.created_at or utcnow(),
            )

    if lead_snapshot is not None:
        await deliver_lead_funnel(lead_snapshot)

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "ok": True,
            "message": "Вы в списке — мы напишем вам в ближайшие 48 часов",
            "lead_id": lead_snapshot.lead_id if lead_snapshot else None,
        },
    )


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt() -> str:
    return "User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n"


@app.get("/favicon.ico")
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "og-default.svg", media_type="image/svg+xml")


@app.get("/sitemap.xml")
async def sitemap_xml(request: Request) -> Response:
    base_url = str(request.base_url).rstrip("/")
    urls = "\n".join(
        f"  <url><loc>{base_url}{page['path']}</loc></url>"
        for page in MARKETING_PAGES.values()
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")


@app.get("/v1/accounts")
async def list_accounts(
    tenant_id: Optional[int] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_internal_token),
) -> dict[str, object]:
    async with async_session() as session:
        stmt = select(Account).order_by(Account.id)
        if tenant_id is not None:
            stmt = stmt.where(Account.tenant_id == tenant_id)
        count_result = await session.execute(select(func.count(Account.id)).select_from(stmt.subquery()))
        total = count_result.scalar_one_or_none() or 0
        rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()

    items = [
        {
            "id": row.id,
            "phone": row.phone,
            "status": row.status,
            "health_status": row.health_status,
            "lifecycle_stage": row.lifecycle_stage,
        }
        for row in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/v1/internal/leads")
async def list_recent_leads(
    limit: int = 25,
    _: None = Depends(require_internal_token),
) -> dict[str, object]:
    safe_limit = max(1, min(int(limit), 100))
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Lead).order_by(Lead.created_at.desc(), Lead.id.desc()).limit(safe_limit)
            )
        ).scalars().all()

    items = [
        {
            "id": row.id,
            "name": row.name,
            "email": row.email,
            "company": row.company,
            "telegram_username": row.telegram_username,
            "use_case": row.use_case,
            "utm_source": row.utm_source,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return {"items": items, "total": len(items)}


@app.get("/v1/workspaces")
async def list_workspaces(
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, object]:
    rows = (
        await session.execute(select(Workspace).order_by(Workspace.id).limit(200))
    ).scalars().all()

    items = [
        {
            "id": row.id,
            "name": row.name,
            "settings": row.settings or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return {"items": items, "total": len(items)}


@app.get("/v1/web/accounts")
async def web_list_accounts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    result = await list_web_accounts(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
    )
    all_items = result["items"]
    total = result["total"]
    page_items = all_items[offset: offset + limit]
    return {"items": page_items, "total": total, "limit": limit, "offset": offset}


@app.post("/v1/web/accounts/upload")
async def web_upload_account_pair(
    session_file: UploadFile = File(...),
    metadata_file: UploadFile = File(...),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    session_bytes = await _read_upload_safe(session_file)
    metadata_bytes = await _read_upload_safe(metadata_file)
    if not session_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty_session_file")
    if not metadata_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty_metadata_file")
    return await save_uploaded_account_pair(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        session_filename=session_file.filename or "",
        session_bytes=session_bytes,
        metadata_filename=metadata_file.filename or "",
        metadata_bytes=metadata_bytes,
        actor="web:account_upload",
    )


@app.get("/v1/web/proxies/available")
async def web_list_available_proxies(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    return await list_available_web_proxies(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
    )


# ---------------------------------------------------------------------------
# Proxy Management — /v1/proxies/*
# ---------------------------------------------------------------------------

def _serialize_proxy(proxy: "Proxy", bound_account_phone: Optional[str] = None) -> dict[str, Any]:
    """Serialize a Proxy ORM row to a dict for API responses."""
    # Build safe display URL without password
    safe_url = f"{proxy.proxy_type}://{proxy.host}:{proxy.port}"
    if proxy.username:
        safe_url = f"{proxy.proxy_type}://{proxy.username}:***@{proxy.host}:{proxy.port}"
    return {
        "id": proxy.id,
        "proxy_type": proxy.proxy_type,
        "host": proxy.host,
        "port": proxy.port,
        "username": proxy.username,
        "is_active": proxy.is_active,
        "status": proxy.health_status,
        "health_status": proxy.health_status,
        "consecutive_failures": proxy.consecutive_failures,
        "last_error": proxy.last_error,
        "last_checked": proxy.last_checked.isoformat() if proxy.last_checked else None,
        "last_checked_at": proxy.last_checked.isoformat() if proxy.last_checked else None,
        "last_success_at": proxy.last_success_at.isoformat() if proxy.last_success_at else None,
        "created_at": proxy.created_at.isoformat() if proxy.created_at else None,
        "url": safe_url,
        "bound_account_phone": bound_account_phone,
        "bound_account_id": None,
        "rotation_strategy": proxy.rotation_strategy or "sticky",
        "auto_rotation": proxy.auto_rotation or False,
    }


@app.post("/v1/proxies/bulk-import", status_code=status.HTTP_200_OK)
async def proxies_bulk_import(
    payload: ProxyBulkImportPayload,
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Parse and import proxy lines, skipping duplicates within the tenant."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    imported = 0
    skipped = 0
    errors: list[str] = []

    # Load existing host+port combinations for this tenant to detect duplicates.
    existing_q = await session.execute(
        select(Proxy.host, Proxy.port).where(Proxy.tenant_id == tenant_id)
    )
    existing_pairs: set[tuple[str, int]] = {(row.host, row.port) for row in existing_q}

    for raw_line in payload.lines.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        cfg = parse_proxy_line(line, default_type=payload.proxy_type)
        if cfg is None:
            errors.append(f"parse_error: {line[:120]}")
            continue
        if (cfg.host, cfg.port) in existing_pairs:
            skipped += 1
            continue
        proxy = Proxy(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=tenant_context.user_id,
            proxy_type=cfg.proxy_type,
            host=cfg.host,
            port=cfg.port,
            username=cfg.username,
            password=cfg.password,
            is_active=True,
            health_status="unknown",
            consecutive_failures=0,
            created_at=utcnow(),
        )
        session.add(proxy)
        existing_pairs.add((cfg.host, cfg.port))
        imported += 1

    await session.flush()
    return {"imported": imported, "skipped": skipped, "errors": errors}


@app.get("/v1/proxies")
async def proxies_list(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    health_status: Optional[str] = Query(default=None, alias="status"),
    bound: Optional[bool] = Query(default=None),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """List all proxies for the current tenant with optional filters and summary."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id

    # Base query — RLS is already active in tenant_session, but we also
    # filter explicitly for clarity.
    base_where = [Proxy.tenant_id == tenant_id]
    if health_status:
        allowed_statuses = {"unknown", "alive", "failing", "dead"}
        if health_status in allowed_statuses:
            base_where.append(Proxy.health_status == health_status)

    # Subquery: which proxy_ids have at least one bound account.
    bound_subq = (
        select(Account.proxy_id)
        .where(Account.proxy_id.isnot(None), Account.tenant_id == tenant_id)
        .distinct()
        .subquery()
    )

    if bound is True:
        base_where.append(Proxy.id.in_(select(bound_subq.c.proxy_id)))
    elif bound is False:
        base_where.append(Proxy.id.notin_(select(bound_subq.c.proxy_id)))

    # Count total matching proxies.
    count_stmt = select(func.count()).select_from(Proxy).where(*base_where)
    total: int = (await session.execute(count_stmt)).scalar_one()

    # Fetch page.
    stmt = (
        select(Proxy)
        .where(*base_where)
        .order_by(Proxy.id.desc())
        .offset(offset)
        .limit(limit)
    )
    proxies = (await session.execute(stmt)).scalars().all()

    # Fetch bound account phones for returned proxies in one query.
    proxy_ids = [p.id for p in proxies]
    bound_phone_map: dict[int, str] = {}
    if proxy_ids:
        phone_rows = await session.execute(
            select(Account.proxy_id, Account.phone)
            .where(Account.proxy_id.in_(proxy_ids), Account.tenant_id == tenant_id)
            .distinct(Account.proxy_id)
        )
        for row in phone_rows:
            if row.proxy_id not in bound_phone_map:
                bound_phone_map[row.proxy_id] = row.phone

    # Summary counters for the whole tenant (not just this page).
    summary_rows = await session.execute(
        select(Proxy.health_status, func.count().label("cnt"))
        .where(Proxy.tenant_id == tenant_id)
        .group_by(Proxy.health_status)
    )
    status_counts: dict[str, int] = {row.health_status: row.cnt for row in summary_rows}

    # Bound count.
    bound_count: int = (
        await session.execute(
            select(func.count())
            .select_from(Proxy)
            .where(Proxy.tenant_id == tenant_id, Proxy.id.in_(select(bound_subq.c.proxy_id)))
        )
    ).scalar_one()
    total_proxies: int = (
        await session.execute(
            select(func.count()).select_from(Proxy).where(Proxy.tenant_id == tenant_id)
        )
    ).scalar_one()

    return {
        "items": [
            _serialize_proxy(p, bound_account_phone=bound_phone_map.get(p.id))
            for p in proxies
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "summary": {
            "alive": status_counts.get("alive", 0),
            "dead": status_counts.get("dead", 0),
            "failing": status_counts.get("failing", 0),
            "unknown": status_counts.get("unknown", 0),
            "bound": bound_count,
            "free": total_proxies - bound_count,
        },
    }


@app.post("/v1/proxies/health-check")
async def proxies_health_check(
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Run liveness check on all active proxies for the tenant and update their status."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id

    proxies = (
        await session.execute(
            select(Proxy).where(Proxy.tenant_id == tenant_id, Proxy.is_active.is_(True)).limit(2000)
        )
    ).scalars().all()

    import asyncio

    manager = ProxyManager()
    semaphore = asyncio.Semaphore(20)  # max 20 concurrent checks

    async def _check_one(proxy):
        async with semaphore:
            return proxy, await check_proxy_orm(proxy, manager=manager, timeout=10)

    check_tasks = [_check_one(p) for p in proxies]
    outcomes = await asyncio.gather(*check_tasks, return_exceptions=True)

    results: list[dict[str, Any]] = []
    alive_count = 0

    for outcome in outcomes:
        if isinstance(outcome, Exception):
            continue
        proxy, is_alive = outcome
        now = utcnow()
        proxy.last_checked = now
        if is_alive:
            proxy.health_status = "alive"
            proxy.consecutive_failures = 0
            proxy.last_error = None
            proxy.last_success_at = now
            alive_count += 1
        else:
            proxy.consecutive_failures = (proxy.consecutive_failures or 0) + 1
            proxy.health_status = "dead" if proxy.consecutive_failures >= 3 else "failing"
            proxy.last_error = "health_check_failed"
        safe_url = f"{proxy.proxy_type}://{proxy.host}:{proxy.port}"
        results.append({
            "id": proxy.id,
            "url": safe_url,
            "alive": is_alive,
            "health_status": proxy.health_status,
        })

    return {
        "checked": len(proxies),
        "alive": alive_count,
        "dead": len(proxies) - alive_count,
        "results": results,
    }


@app.post("/v1/proxies/cleanup")
async def proxies_cleanup(
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Delete dead unbound proxies for the tenant."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id

    # Find dead proxies that are NOT bound to any account.
    bound_proxy_ids_q = (
        select(Account.proxy_id)
        .where(Account.proxy_id.isnot(None), Account.tenant_id == tenant_id)
        .distinct()
        .subquery()
    )
    dead_unbound = (
        await session.execute(
            select(Proxy).where(
                Proxy.tenant_id == tenant_id,
                Proxy.health_status == "dead",
                Proxy.id.notin_(select(bound_proxy_ids_q.c.proxy_id)),
            ).limit(5000)
        )
    ).scalars().all()

    # Count bound dead proxies that are kept.
    dead_bound_count: int = (
        await session.execute(
            select(func.count())
            .select_from(Proxy)
            .where(
                Proxy.tenant_id == tenant_id,
                Proxy.health_status == "dead",
                Proxy.id.in_(select(bound_proxy_ids_q.c.proxy_id)),
            )
        )
    ).scalar_one()

    deleted = 0
    for proxy in dead_unbound:
        await session.delete(proxy)
        deleted += 1

    return {"deleted": deleted, "kept_bound": dead_bound_count}


@app.delete("/v1/proxies/{proxy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def proxy_delete(
    proxy_id: int,
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> None:
    """Delete a single proxy if no account is currently bound to it."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id

    proxy = (
        await session.execute(
            select(Proxy).where(Proxy.id == proxy_id, Proxy.tenant_id == tenant_id).with_for_update()
        )
    ).scalar_one_or_none()
    if proxy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proxy_not_found")

    bound_account = (
        await session.execute(
            select(Account.id).where(
                Account.proxy_id == proxy_id,
                Account.tenant_id == tenant_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if bound_account is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="proxy_has_bound_accounts",
        )

    await session.delete(proxy)


@app.post("/v1/proxies/{proxy_id}/check")
async def proxy_single_check(
    proxy_id: int,
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Run a liveness check on a single proxy and return the updated proxy object."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id

    proxy = (
        await session.execute(
            select(Proxy).where(Proxy.id == proxy_id, Proxy.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if proxy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proxy_not_found")

    manager = ProxyManager()
    is_alive = await check_proxy_orm(proxy, manager=manager)
    now = utcnow()
    proxy.last_checked = now
    if is_alive:
        proxy.health_status = "alive"
        proxy.consecutive_failures = 0
        proxy.last_error = None
        proxy.last_success_at = now
    else:
        proxy.consecutive_failures = (proxy.consecutive_failures or 0) + 1
        proxy.health_status = "dead" if proxy.consecutive_failures >= 3 else "failing"
        proxy.last_error = "health_check_failed"

    # Fetch bound phone before serializing.
    bound_phone: Optional[str] = None
    bound_row = (
        await session.execute(
            select(Account.phone).where(
                Account.proxy_id == proxy_id,
                Account.tenant_id == tenant_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if bound_row:
        bound_phone = bound_row

    return _serialize_proxy(proxy, bound_account_phone=bound_phone)


_VALID_ROTATION_STRATEGIES = {"sticky", "round_robin", "geo_match"}


class ProxyRotationStrategyPayload(BaseModel):
    strategy: str
    auto_rotation: Optional[bool] = None


@app.put("/v1/proxies/{proxy_id}/strategy", status_code=status.HTTP_200_OK)
async def proxy_set_strategy(
    proxy_id: int,
    payload: ProxyRotationStrategyPayload,
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Set rotation strategy (sticky/round_robin/geo_match) and optional auto-rotation flag for a proxy."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    if payload.strategy not in _VALID_ROTATION_STRATEGIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid_strategy; allowed: {sorted(_VALID_ROTATION_STRATEGIES)}",
        )

    proxy = (
        await session.execute(
            select(Proxy).where(
                Proxy.id == proxy_id,
                Proxy.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if proxy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proxy_not_found")

    proxy.rotation_strategy = payload.strategy
    if payload.auto_rotation is not None:
        proxy.auto_rotation = payload.auto_rotation

    # Fetch bound phone for serialization
    bound_phone: Optional[str] = (
        await session.execute(
            select(Account.phone).where(
                Account.proxy_id == proxy_id,
                Account.tenant_id == tenant_context.tenant_id,
            ).limit(1)
        )
    ).scalar_one_or_none()

    result = _serialize_proxy(proxy, bound_account_phone=bound_phone)
    result["rotation_strategy"] = proxy.rotation_strategy
    result["auto_rotation"] = proxy.auto_rotation
    return result


# ---------------------------------------------------------------------------
# Proxy Smart Routing — /v1/proxies/auto-assign, mass-assign, load, cleanup-dead
# ---------------------------------------------------------------------------


@app.post("/v1/proxies/auto-assign", status_code=status.HTTP_200_OK)
async def proxies_auto_assign(
    payload: ProxyAutoAssignPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Auto-assign the best available proxy to a single account."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    router = ProxyRouter(session, tenant_id=tenant_context.tenant_id)
    try:
        result = await router.assign_proxy(payload.account_id, strategy=payload.strategy)
    except NoAvailableProxyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return result


@app.post("/v1/proxies/mass-assign", status_code=status.HTTP_200_OK)
async def proxies_mass_assign(
    payload: ProxyMassAssignPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Assign proxies to multiple accounts at once using the chosen strategy."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    router = ProxyRouter(session, tenant_id=tenant_context.tenant_id)
    try:
        result = await router.mass_assign(payload.account_ids, strategy=payload.strategy)
    except NoAvailableProxyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return result


@app.get("/v1/proxies/load", status_code=status.HTTP_200_OK)
async def proxies_load(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Return per-proxy utilization stats sorted by bindings_count ascending."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    router = ProxyRouter(session, tenant_id=tenant_context.tenant_id)
    rows = await router.get_proxy_load()
    return {"items": rows, "total": len(rows)}


@app.post("/v1/proxies/cleanup-dead", status_code=status.HTTP_200_OK)
async def proxies_cleanup_dead(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Unbind all dead/inactive proxies from accounts in this tenant."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    router = ProxyRouter(session, tenant_id=tenant_context.tenant_id)
    return await router.cleanup_dead_bindings()


@app.post("/v1/web/accounts/{account_id}/bind-proxy")
async def web_bind_proxy(
    account_id: int,
    payload: BindProxyPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    return await bind_proxy_for_account(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        account_id=account_id,
        proxy_id=payload.proxy_id,
        proxy_string=payload.proxy_string,
    )


@app.post("/v1/web/accounts/{account_id}/audit")
async def web_audit_account(
    account_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    return await audit_web_account(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        account_id=account_id,
    )


@app.get("/v1/web/accounts/{account_id}/audit")
async def web_get_account_audit(
    account_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    account_payload = await list_web_accounts(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
    )
    for item in account_payload["items"]:
        if int(item["id"]) == int(account_id):
            return {"account": item}
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")


@app.post("/v1/web/accounts/{account_id}/notes")
async def web_save_account_notes(
    account_id: int,
    payload: AccountNotePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    return await save_web_account_note(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        account_id=account_id,
        auth_user_id=tenant_context.user_id,
        notes=payload.notes,
    )


@app.get("/v1/web/accounts/{account_id}/timeline")
async def web_get_account_timeline(
    account_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    return await get_web_account_timeline(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        account_id=account_id,
    )


# ---------------------------------------------------------------------------
# Sprint 7 Tasks 2+3 — Account Management v2 endpoints
# ---------------------------------------------------------------------------


class BulkActionPayload(BaseModel):
    action: Literal["warmup_all", "start_farm", "stop_farm", "pause_farm"]
    account_ids: Optional[List[int]] = None


class AccountBatchSettings(BaseModel):
    proxy_strategy: Optional[Literal["round_robin", "sticky", "geo_match"]] = None
    ai_protection: Optional[Literal["off", "conservative", "aggressive"]] = None
    comment_language: Optional[Literal["ru", "en", "auto"]] = None
    warmup_mode: Optional[Literal["conservative", "moderate", "aggressive"]] = None


class BatchSettingsPayload(BaseModel):
    account_ids: List[int] = Field(..., min_length=1, max_length=500)
    settings: AccountBatchSettings


class CheckDuplicatesPayload(BaseModel):
    phones: List[str] = Field(..., min_length=1, max_length=500)


@app.post("/v1/accounts/bulk-import")
async def accounts_bulk_import(
    request: Request,
    files: List[UploadFile] = File(...),
    tdata_passcode: str = Form(""),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """
    Accept multiple .session + .json pairs, TData ZIP archives, or mixed ZIPs and bulk-import accounts.
    TData archives are auto-converted to session+json pairs via opentele.
    Auto-assigns a free tenant proxy to each imported account when available.

    Form fields:
        files: Upload files (.session, .json, .zip with session+json pairs, or TData ZIP)
        tdata_passcode: Optional local passcode for encrypted TData archives (usually empty)
    """
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    if len(files) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many files ({len(files)}), max 200 per request",
        )
    import io
    import json as _json
    import zipfile

    from core.web_accounts import (
        WebOnboardingError,
        _phone_digits_from_filename,
        normalize_phone,
        upsert_account_from_session_upload,
    )
    from utils.account_uploads import validate_and_normalize_account_metadata, write_normalized_metadata
    from utils.session_topology import canonical_session_paths

    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    from core.web_accounts import get_workspace_runtime_user
    workspace = await get_workspace_runtime_user(session, tenant_id=tenant_id, workspace_id=workspace_id)
    runtime_user_id = int(workspace.runtime_user_id)

    # Resolve free proxies for auto-assignment (cap at 2000 to prevent OOM)
    free_proxy_result = await session.execute(
        select(Proxy).where(
            Proxy.tenant_id == tenant_id,
            Proxy.workspace_id == workspace_id,
            Proxy.is_active == True,
        ).limit(2000)
    )
    all_proxies = list(free_proxy_result.scalars().all())
    used_proxy_ids_result = await session.execute(
        select(Account.proxy_id).where(
            Account.tenant_id == tenant_id,
            Account.proxy_id.isnot(None),
        )
    )
    used_proxy_ids = {row[0] for row in used_proxy_ids_result.fetchall()}
    free_proxies = [p for p in all_proxies if p.id not in used_proxy_ids]
    free_proxy_iter = iter(free_proxies)

    # Collect raw bytes from uploads — expand ZIP if needed
    pair_map: dict[str, dict[str, bytes]] = {}  # phone_digits -> {"session": bytes, "json": bytes}

    async def _absorb_file(fname: str, data: bytes) -> None:
        fname_lower = fname.lower()
        if fname_lower.endswith(".session"):
            digits = "".join(ch for ch in Path(fname).stem if ch.isdigit())
            if digits:
                pair_map.setdefault(digits, {})["session"] = data
        elif fname_lower.endswith(".json"):
            digits = "".join(ch for ch in Path(fname).stem if ch.isdigit())
            if digits:
                pair_map.setdefault(digits, {})["json"] = data

    def _absorb_tdata_result(result: "TDataConversionResult") -> None:
        """Inject TData-converted accounts into pair_map."""
        for acct in result.accounts:
            # TData accounts may not have a phone — use a placeholder from user_id
            phone_digits = "".join(ch for ch in acct.phone if ch.isdigit()) if acct.phone else ""
            if not phone_digits:
                # Generate a temporary identifier; the user will need to verify later
                phone_digits = "tdata_" + hashlib.sha256(acct.session_bytes[:64]).hexdigest()[:8]
            meta_bytes = _json.dumps(acct.metadata, ensure_ascii=False, indent=2).encode("utf-8")
            pair_map.setdefault(phone_digits, {})["session"] = acct.session_bytes
            pair_map.setdefault(phone_digits, {})["json"] = meta_bytes
            pair_map[phone_digits]["_source"] = "tdata"

    errors: list[str] = []
    for upload in files:
        fname = upload.filename or ""
        if fname.lower().endswith(".zip"):
            raw = await _read_upload_safe(upload, max_bytes=_MAX_ZIP_BYTES)
            # Check if ZIP contains TData structure
            from utils.tdata_converter import is_tdata_zip, convert_tdata_zip
            if is_tdata_zip(raw):
                tdata_result = convert_tdata_zip(raw, settings.sessions_path, passcode=tdata_passcode)
                _absorb_tdata_result(tdata_result)
                if tdata_result.errors:
                    errors.extend([f"tdata:{e}" for e in tdata_result.errors])
            else:
                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                        total_decompressed = 0
                        for member in zf.infolist():
                            if member.file_size > _MAX_UPLOAD_BYTES:
                                errors.append(f"zip_member_too_large:{member.filename}")
                                continue
                            total_decompressed += member.file_size
                            if total_decompressed > _MAX_ZIP_BYTES:
                                errors.append("zip_total_decompressed_too_large")
                                break
                            member_data = zf.read(member)
                            await _absorb_file(Path(member.filename).name, member_data)
                except zipfile.BadZipFile:
                    continue
        else:
            raw = await _read_upload_safe(upload, max_bytes=_MAX_UPLOAD_BYTES)
            await _absorb_file(fname, raw)

    imported = 0
    skipped = 0
    auto_proxied = 0

    for digits, parts in pair_map.items():
        if "session" not in parts or "json" not in parts:
            errors.append(f"incomplete_pair:{digits}")
            skipped += 1
            continue
        is_tdata = parts.get("_source") == "tdata"
        # For TData accounts, try to extract phone from metadata if digits key is not a real phone
        phone = normalize_phone(digits)
        if not phone and is_tdata:
            try:
                meta_preview = _json.loads(parts["json"].decode("utf-8"))
                meta_phone = str(meta_preview.get("phone") or "")
                phone = normalize_phone(meta_phone)
            except Exception:
                pass
        if not phone:
            errors.append(f"invalid_phone:{digits}" + (" (tdata: connect to Telegram to discover phone)" if is_tdata else ""))
            skipped += 1
            continue
        phone_digits = "".join(ch for ch in phone if ch.isdigit())
        session_path, metadata_path = canonical_session_paths(
            settings.sessions_path, runtime_user_id, phone
        )
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_bytes(parts["session"])
        try:
            payload_data = _json.loads(parts["json"].decode("utf-8"))
            normalized = validate_and_normalize_account_metadata(
                payload_data,
                expected_phone=phone,
                expected_session_file=f"{phone_digits}.session",
            )
            write_normalized_metadata(metadata_path, normalized)
        except Exception as exc:
            session_path.unlink(missing_ok=True)
            errors.append(f"metadata_error:{digits}:{exc}")
            skipped += 1
            continue
        try:
            account, _ = await upsert_account_from_session_upload(
                session,
                phone=phone,
                session_file=f"{phone_digits}.session",
                runtime_user_id=runtime_user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                reset_runtime_state=True,
            )
            await session.flush()
            # Auto-assign free proxy
            if account.proxy_id is None:
                free_proxy = next(free_proxy_iter, None)
                if free_proxy is not None:
                    account.proxy_id = free_proxy.id
                    auto_proxied += 1
            imported += 1
        except WebOnboardingError as exc:
            errors.append(f"db_error:{digits}:{exc}")
            skipped += 1

    return {
        "imported": imported,
        "skipped": skipped,
        "auto_proxied": auto_proxied,
        "errors": errors,
    }


@app.post("/v1/accounts/{account_id}/discover-phone")
async def account_discover_phone(
    account_id: int,
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """
    Connect to Telegram via Telethon and discover the real phone number for an account.
    Used for TData-imported accounts where the phone is unknown.
    Does NOT call send_code_request — only connects and calls get_me().
    Requires the account to have a bound proxy.
    """
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=10, window_seconds=60)
    from core.web_accounts import get_web_account_record, get_workspace_runtime_user, normalize_phone
    from utils.tdata_converter import discover_phone_from_session
    from utils.session_topology import canonical_session_paths

    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    workspace = await get_workspace_runtime_user(session, tenant_id=tenant_id, workspace_id=workspace_id)
    runtime_user_id = int(workspace.runtime_user_id)

    account, _ = await get_web_account_record(
        session, tenant_id=tenant_id, workspace_id=workspace_id, account_id=account_id,
    )

    # Build proxy tuple if account has a proxy
    proxy_tuple = None
    if account.proxy_id:
        proxy_result = await session.execute(
            select(Proxy).where(Proxy.id == account.proxy_id, Proxy.tenant_id == tenant_id)
        )
        proxy_obj = proxy_result.scalar_one_or_none()
        if proxy_obj:
            proxy_tuple = (
                3,
                str(proxy_obj.host),
                int(proxy_obj.port),
                True,
                str(proxy_obj.username or ""),
                str(proxy_obj.password or ""),
            )

    # Read metadata
    import json as _json
    session_path, metadata_path = canonical_session_paths(
        settings.sessions_path, runtime_user_id, account.phone,
    )
    metadata = {}
    if metadata_path.exists():
        try:
            metadata = _json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not session_path.exists():
        raise HTTPException(status_code=404, detail="session_file_not_found")

    # Discover phone via Telethon
    discovery = await discover_phone_from_session(
        session_path, metadata, proxy=proxy_tuple, timeout=30,
    )

    if not discovery.get("ok"):
        return {
            "ok": False,
            "account_id": account_id,
            "error": discovery.get("error", "unknown"),
            "authorized": discovery.get("authorized", False),
        }

    discovered_phone = normalize_phone(str(discovery.get("phone") or ""))
    first_name = str(discovery.get("first_name") or "")
    last_name = str(discovery.get("last_name") or "")

    if not discovered_phone:
        return {
            "ok": False,
            "account_id": account_id,
            "error": "phone_not_available",
            "user_id": discovery.get("user_id"),
            "authorized": True,
        }

    # Update account phone and metadata if changed
    old_phone = account.phone
    if discovered_phone != old_phone:
        account.phone = discovered_phone
        # Rename session files to match new phone
        new_phone_digits = "".join(ch for ch in discovered_phone if ch.isdigit())
        new_session_path, new_metadata_path = canonical_session_paths(
            settings.sessions_path, runtime_user_id, discovered_phone,
        )
        new_session_path.parent.mkdir(parents=True, exist_ok=True)
        if session_path.exists() and session_path != new_session_path:
            import shutil
            shutil.move(str(session_path), str(new_session_path))
        if metadata_path.exists() and metadata_path != new_metadata_path:
            # Update phone in metadata
            metadata["phone"] = discovered_phone
            metadata["session_file"] = f"{new_phone_digits}.session"
            new_metadata_path.write_text(
                _json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            if metadata_path != new_metadata_path:
                metadata_path.unlink(missing_ok=True)

        account.session_file = f"{new_phone_digits}.session"

    # Update first_name if available
    if first_name:
        account.first_name = first_name
    if last_name:
        account.last_name = last_name

    await session.flush()

    return {
        "ok": True,
        "account_id": account_id,
        "old_phone": old_phone,
        "phone": discovered_phone,
        "first_name": first_name,
        "last_name": last_name,
        "user_id": discovery.get("user_id"),
        "renamed": discovered_phone != old_phone,
    }


@app.post("/v1/accounts/mass-proxy-assign")
async def accounts_mass_proxy_assign(
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Auto-assign free proxies (1:1) to all accounts without a proxy in this tenant/workspace."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    # Fetch unproxied accounts (cap at 2000 for safety)
    unproxied_result = await session.execute(
        select(Account).where(
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
            Account.proxy_id.is_(None),
        ).limit(2000)
    )
    unproxied_accounts = list(unproxied_result.scalars().all())

    # Fetch free proxies
    used_proxy_ids_result = await session.execute(
        select(Account.proxy_id).where(
            Account.tenant_id == tenant_id,
            Account.proxy_id.isnot(None),
        ).limit(5000)
    )
    used_proxy_ids = {row[0] for row in used_proxy_ids_result.fetchall()}
    free_proxy_result = await session.execute(
        select(Proxy).where(
            Proxy.tenant_id == tenant_id,
            Proxy.workspace_id == workspace_id,
            Proxy.is_active == True,
        ).limit(2000)
    )
    free_proxies = [p for p in free_proxy_result.scalars().all() if p.id not in used_proxy_ids]

    assigned = 0
    free_iter = iter(free_proxies)
    for account in unproxied_accounts:
        proxy = next(free_iter, None)
        if proxy is None:
            break
        account.proxy_id = proxy.id
        assigned += 1

    no_proxy_available = len(unproxied_accounts) - assigned
    return {"assigned": assigned, "no_proxy_available": no_proxy_available}


@app.post("/v1/accounts/bulk-action")
async def accounts_bulk_action(
    request: Request,
    payload: BulkActionPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """
    Perform a bulk action on a set of accounts (or all accounts in the tenant workspace).
    Actions: warmup_all | start_farm | stop_farm | pause_farm
    """
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)

    VALID_ACTIONS = {"warmup_all", "start_farm", "stop_farm", "pause_farm"}
    if payload.action not in VALID_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid_action: must be one of {sorted(VALID_ACTIONS)}",
        )

    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    # Resolve target accounts (cap at 2000 for safety, lock rows for status mutation)
    q = select(Account).where(
        Account.tenant_id == tenant_id,
        Account.workspace_id == workspace_id,
    )
    if payload.account_ids:
        q = q.where(Account.id.in_(payload.account_ids))
    q = q.limit(2000).with_for_update()
    result = await session.execute(q)
    accounts = list(result.scalars().all())

    affected = 0
    errors: list[str] = []

    ACTION_TO_STATUS = {
        "start_farm": "active",
        "stop_farm": "cooldown",
        "pause_farm": "cooldown",
    }
    ACTION_TO_JOB_TYPE = {
        "warmup_all": JOB_TYPE_WARMUP_START,
        "start_farm": JOB_TYPE_FARM_START,
        "stop_farm": JOB_TYPE_FARM_STOP,
        "pause_farm": JOB_TYPE_FARM_PAUSE,
    }

    for account in accounts:
        try:
            if payload.action in ACTION_TO_STATUS:
                account.status = ACTION_TO_STATUS[payload.action]

            job_type = ACTION_TO_JOB_TYPE[payload.action]
            job_payload: dict[str, Any] = {"account_id": account.id}
            if payload.action == "warmup_all":
                job_payload["config_id"] = account.id  # best-effort: use account id as config hint
            else:
                job_payload["farm_id"] = account.id

            await _enqueue_within_session(
                session=session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=tenant_context.user_id,
                job_type=job_type,
                payload=job_payload,
            )
            affected += 1
        except Exception as exc:
            errors.append(f"account:{account.id}:{exc}")

    return {"action": payload.action, "affected": affected, "errors": errors}


@app.post("/v1/accounts/batch-settings")
async def accounts_batch_settings(
    request: Request,
    payload: BatchSettingsPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """
    Apply per-account settings to a batch of accounts that belong to the current tenant.
    Only fields explicitly provided (non-None) are updated.
    Returns count of updated accounts.
    """
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)

    settings = payload.settings
    # At least one setting field must be provided
    if all(
        v is None
        for v in (
            settings.proxy_strategy,
            settings.ai_protection,
            settings.comment_language,
            settings.warmup_mode,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at_least_one_settings_field_required",
        )

    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    # Fetch only accounts belonging to this tenant/workspace (cross-tenant safe, cap 500)
    result = await session.execute(
        select(Account)
        .where(
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
            Account.id.in_(payload.account_ids),
        )
        .limit(500)
        .with_for_update()
    )
    accounts_to_update = list(result.scalars().all())

    for account in accounts_to_update:
        if settings.proxy_strategy is not None:
            account.proxy_strategy = settings.proxy_strategy
        if settings.ai_protection is not None:
            account.ai_protection = settings.ai_protection
        if settings.comment_language is not None:
            account.account_comment_language = settings.comment_language
        if settings.warmup_mode is not None:
            account.warmup_mode = settings.warmup_mode

    await session.flush()
    return {"updated": len(accounts_to_update)}


@app.get("/v1/accounts/stats")
async def accounts_stats(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Return aggregate account stats for the current tenant workspace."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    result = await session.execute(
        select(Account).where(
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
        ).limit(5000)
    )
    accounts = list(result.scalars().all())

    by_status: dict[str, int] = {}
    by_lifecycle: dict[str, int] = {}
    proxied = 0
    unproxied = 0

    for acc in accounts:
        s = acc.status or "unknown"
        by_status[s] = by_status.get(s, 0) + 1

        lc = acc.lifecycle_stage or "unknown"
        by_lifecycle[lc] = by_lifecycle.get(lc, 0) + 1

        if acc.proxy_id is not None:
            proxied += 1
        else:
            unproxied += 1

    return {
        "total": len(accounts),
        "by_status": by_status,
        "by_lifecycle": by_lifecycle,
        "proxied": proxied,
        "unproxied": unproxied,
    }


@app.post("/v1/accounts/check-duplicates")
async def accounts_check_duplicates(
    payload: CheckDuplicatesPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Check which of the supplied phone numbers already exist for this tenant workspace."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    # Normalise: strip whitespace and ensure leading +
    def _norm(p: str) -> str:
        digits = "".join(ch for ch in str(p or "") if ch.isdigit())
        return f"+{digits}" if digits else ""

    normalised = [_norm(p) for p in payload.phones]
    unique_phones = list({p for p in normalised if p})

    if not unique_phones:
        return {"duplicates": [], "new": []}

    result = await session.execute(
        select(Account.phone, Account.id).where(
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
            Account.phone.in_(unique_phones),
        )
    )
    rows = result.all()
    existing_map: dict[str, int] = {row[0]: row[1] for row in rows}

    duplicates = [
        {"phone": p, "existing_account_id": existing_map[p]}
        for p in unique_phones
        if p in existing_map
    ]
    new = [p for p in unique_phones if p not in existing_map]

    return {"duplicates": duplicates, "new": new}


@app.get("/v1/accounts/export")
async def accounts_export(
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    status_filter: str = Query(default="all", alias="status"),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> StreamingResponse:
    """Export tenant-scoped accounts as CSV or JSON for download."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=5, window_seconds=60)
    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    stmt = (
        select(Account)
        .where(
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
        )
        .order_by(Account.id.asc())
        .limit(10000)  # Hard ceiling to prevent memory exhaustion
    )
    if status_filter != "all":
        stmt = stmt.where(Account.status == status_filter)

    rows = list((await session.execute(stmt)).scalars().all())

    if format == "json":
        import json as _json

        data = [
            {
                "id": acc.id,
                "phone": acc.phone,
                "status": acc.status,
                "lifecycle_stage": acc.lifecycle_stage,
                "health_status": acc.health_status,
                "risk_level": acc.risk_level,
                "proxy_id": acc.proxy_id,
                "account_age_days": acc.account_age_days,
                "created_at": acc.created_at.isoformat() if acc.created_at else None,
                "last_active_at": acc.last_active_at.isoformat() if acc.last_active_at else None,
                "last_health_check": acc.last_health_check.isoformat() if acc.last_health_check else None,
                "manual_notes": acc.manual_notes,
            }
            for acc in rows
        ]
        content = _json.dumps(data, ensure_ascii=False, indent=2)
        media_type = "application/json"
        filename = "accounts.json"
        return StreamingResponse(
            iter([content.encode("utf-8")]),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # CSV
    import csv
    import io as _io

    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "phone", "status", "lifecycle_stage", "health_status",
        "risk_level", "proxy_id", "account_age_days",
        "created_at", "last_active_at", "last_health_check", "manual_notes",
    ])
    for acc in rows:
        writer.writerow([
            acc.id,
            acc.phone,
            acc.status or "",
            acc.lifecycle_stage or "",
            acc.health_status or "",
            acc.risk_level or "",
            acc.proxy_id or "",
            acc.account_age_days or 0,
            acc.created_at.isoformat() if acc.created_at else "",
            acc.last_active_at.isoformat() if acc.last_active_at else "",
            acc.last_health_check.isoformat() if acc.last_health_check else "",
            acc.manual_notes or "",
        ])

    content_bytes = buf.getvalue().encode("utf-8")
    return StreamingResponse(
        iter([content_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="accounts.csv"'},
    )


_VALID_STAGES = {
    "uploaded", "warming_up", "gate_review", "execution_ready",
    "active_commenting", "cooldown", "restricted", "frozen", "banned", "dead",
}


class LifecycleTransitionPayload(BaseModel):
    target_stage: str = Field(..., max_length=50, description="Target lifecycle stage to transition to")
    reason: str = Field(default="", max_length=500, description="Optional operator-supplied reason")

    @field_validator("target_stage")
    @classmethod
    def validate_stage(cls, v: str) -> str:
        if v not in _VALID_STAGES:
            raise ValueError(f"invalid stage: {v}. Must be one of: {sorted(_VALID_STAGES)}")
        return v


@app.post("/v1/accounts/{account_id}/lifecycle", status_code=200)
async def account_lifecycle_transition(
    account_id: int,
    payload: LifecycleTransitionPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Manually transition an account to a new lifecycle stage.

    Validates that the transition is allowed by the state machine.
    Logs the event to account_stage_events with actor="operator".
    Tenant-scoped: the account must belong to the current tenant workspace.
    """
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)

    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    # Verify account belongs to this tenant / workspace before mutating (lock row).
    result = await session.execute(
        select(Account).where(
            Account.id == account_id,
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
        ).with_for_update()
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="account_not_found",
        )

    lifecycle = AccountLifecycle(session, tenant_id=tenant_id)
    try:
        outcome = await lifecycle.transition(
            account_id,
            payload.target_stage,
            reason=payload.reason or "",
            actor="operator",
        )
    except LifecycleTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return outcome


@app.get("/v1/accounts/{account_id}/lifecycle/history", status_code=200)
async def account_lifecycle_history(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Return the lifecycle stage transition history for an account.

    Tenant-scoped: the account must belong to the current tenant workspace.
    """
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)

    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    result = await session.execute(
        select(Account).where(
            Account.id == account_id,
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="account_not_found",
        )

    lifecycle = AccountLifecycle(session, tenant_id=tenant_id)
    history = await lifecycle.get_stage_history(account_id, limit=limit)
    return {"account_id": account_id, "total": len(history), "items": history}


@app.post("/v1/assistant/start-brief")
async def assistant_start_brief(
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    return await enqueue_app_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_START_BRIEF,
    )


@app.post("/v1/assistant/message")
async def assistant_message(
    payload: AssistantMessagePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    return await enqueue_app_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_ASSISTANT_MESSAGE,
        payload={"message": payload.message},
    )


@app.get("/v1/assistant/thread")
async def assistant_thread(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    result = await get_assistant_thread(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
    )
    all_messages = result.get("messages") or []
    total_messages = len(all_messages)
    result["messages"] = all_messages[offset: offset + limit]
    result["messages_total"] = total_messages
    result["limit"] = limit
    result["offset"] = offset
    return result


@app.get("/v1/context")
async def context_payload(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    return await get_context_payload(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
    )


@app.post("/v1/context/confirm")
async def context_confirm(
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    return await enqueue_app_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_CONTEXT_CONFIRM,
    )


@app.get("/v1/creative/drafts")
async def creative_drafts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    result = await list_creative_drafts(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
    )
    all_items = result["items"]
    total = result["total"]
    page_items = all_items[offset: offset + limit]
    return {"items": page_items, "total": total, "limit": limit, "offset": offset}


@app.post("/v1/creative/generate")
async def creative_generate(
    payload: CreativeGeneratePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    return await enqueue_app_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_CREATIVE_GENERATE,
        payload={
            "draft_type": payload.draft_type,
            "variant_count": payload.variant_count,
        },
    )


@app.post("/v1/creative/approve")
async def creative_approve(
    payload: CreativeApprovePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    return await approve_creative_draft(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        draft_id=payload.draft_id,
        selected_variant=payload.selected_variant,
    )


@app.get("/v1/jobs/{job_id}")
async def app_job_status(
    job_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    try:
        return await get_job_status(
            session,
            tenant_id=tenant_context.tenant_id,
            workspace_id=tenant_context.workspace_id,
            job_id=job_id,
        )
    except AssistantJobError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@app.delete("/v1/jobs/{job_id}")
async def app_job_cancel(
    job_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Cancel a queued or processing job that belongs to the current tenant.

    Uses an atomic UPDATE with status filter to avoid TOCTOU race conditions.
    """
    from sqlalchemy import update as sa_update
    from core.assistant_jobs import _job_response

    now = utcnow()
    result = await session.execute(
        sa_update(AppJob)
        .where(
            AppJob.id == job_id,
            AppJob.tenant_id == tenant_context.tenant_id,
            AppJob.status.in_(("queued", "processing")),
        )
        .values(status="cancelled", completed_at=now, updated_at=now)
    )
    if result.rowcount == 0:
        # Either not found or already completed/cancelled
        job = (
            await session.execute(
                select(AppJob).where(
                    AppJob.id == job_id,
                    AppJob.tenant_id == tenant_context.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"job_cannot_be_cancelled_status_{job.status}",
        )
    await session.flush()
    job = (
        await session.execute(
            select(AppJob).where(
                AppJob.id == job_id,
                AppJob.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one()
    return _job_response(job)


@app.get("/v1/ai/quality-summary")
async def ai_quality_summary(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    return await get_tenant_ai_quality_summary(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
    )


_ai_models_cache: dict[str, Any] | None = None


@app.get("/v1/ai/models")
async def ai_available_models(
    _tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    global _ai_models_cache
    if _ai_models_cache is not None:
        return _ai_models_cache
    from core.ai_router import (
        OPENROUTER_MODEL_CATALOG,
        GEMINI_PRICING_PER_1M,
        DEFAULT_TASK_POLICIES,
        TASK_MODEL_AFFINITY,
    )
    _ai_models_cache = {
        "openrouter_models": {
            model_id: info for model_id, info in OPENROUTER_MODEL_CATALOG.items()
        },
        "gemini_models": {
            model: {"input_per_1m": p[0], "output_per_1m": p[1]}
            for model, p in GEMINI_PRICING_PER_1M.items()
        },
        "task_policies": {
            k: {
                "agent_name": v.agent_name,
                "tier": v.requested_model_tier,
                "approval_required": v.approval_required,
            }
            for k, v in DEFAULT_TASK_POLICIES.items()
        },
        "task_model_affinity": TASK_MODEL_AFFINITY,
        "total_models": len(OPENROUTER_MODEL_CATALOG) + len(GEMINI_PRICING_PER_1M),
    }
    return _ai_models_cache


@app.get("/v1/internal/ai/audit")
async def internal_ai_audit(
    limit: int = 50,
    tenant_id: Optional[int] = None,
    workspace_id: Optional[int] = None,
    _: None = Depends(require_internal_token),
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 500))
    async with async_session() as session:
        async with session.begin():
            return await list_internal_ai_audit(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                limit=safe_limit,
            )



# Sprint 6 endpoints are at the bottom of this file, before exception handlers.


@app.get("/app", response_class=HTMLResponse)
async def app_shell_root() -> Response:
    if _frontend_ready():
        return FileResponse(FRONTEND_INDEX_PATH)
    return HTMLResponse(_frontend_unavailable_html(), status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


@app.get("/app/manifest.webmanifest")
async def serve_manifest() -> Response:
    """Serve PWA manifest."""
    manifest_path = FRONTEND_DIST_DIR / "manifest.webmanifest"
    if not manifest_path.exists():
        manifest_path = FRONTEND_DIR / "public" / "manifest.webmanifest"
    if manifest_path.exists():
        return FileResponse(manifest_path, media_type="application/manifest+json")
    return JSONResponse({"error": "manifest not found"}, status_code=404)


@app.get("/app/sw.js")
async def serve_service_worker() -> Response:
    """Serve PWA service worker with correct scope header."""
    sw_path = FRONTEND_DIST_DIR / "sw.js"
    if sw_path.exists():
        return FileResponse(
            sw_path,
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/app/"},
        )
    return Response("// no service worker", media_type="application/javascript")


@app.get("/app/registerSW.js")
async def serve_register_sw() -> Response:
    """Serve PWA registerSW.js with correct MIME type."""
    rsw_path = FRONTEND_DIST_DIR / "registerSW.js"
    if rsw_path.exists():
        return FileResponse(rsw_path, media_type="application/javascript")
    return Response("// no registerSW", media_type="application/javascript")


@app.get("/app/workbox-{rest:path}")
async def serve_workbox(rest: str) -> Response:
    """Serve Workbox chunks generated by vite-plugin-pwa."""
    wb_path = FRONTEND_DIST_DIR / f"workbox-{rest}"
    if not wb_path.resolve().is_relative_to(FRONTEND_DIST_DIR.resolve()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_path")
    if wb_path.exists():
        return FileResponse(wb_path, media_type="application/javascript")
    return Response(status_code=404)


@app.get("/app/{path:path}", response_class=HTMLResponse)
async def app_shell(path: str) -> Response:
    normalized = str(path or "").strip()
    if normalized.startswith("assets/") and FRONTEND_ASSETS_DIR.exists():
        asset_path = FRONTEND_DIST_DIR / normalized
        if not asset_path.resolve().is_relative_to(FRONTEND_DIST_DIR.resolve()):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_asset_path")
        if asset_path.exists() and asset_path.is_file():
            return FileResponse(asset_path)
    if _frontend_ready():
        return FileResponse(FRONTEND_INDEX_PATH)
    return HTMLResponse(_frontend_unavailable_html(), status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


# ===========================================================================
# Sprint 5 — Farm Orchestrator API
# ===========================================================================

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize_farm(farm: FarmConfig) -> dict[str, Any]:
    return {
        "id": int(farm.id),
        "name": farm.name,
        "status": farm.status,
        "mode": farm.mode,
        "max_threads": farm.max_threads,
        "comment_prompt": farm.comment_prompt,
        "comment_tone": farm.comment_tone,
        "comment_language": farm.comment_language,
        "comment_all_posts": farm.comment_all_posts,
        "comment_percentage": farm.comment_percentage,
        "delay_before_comment_min": farm.delay_before_comment_min,
        "delay_before_comment_max": farm.delay_before_comment_max,
        "delay_before_join_min": farm.delay_before_join_min,
        "delay_before_join_max": farm.delay_before_join_max,
        "ai_protection_mode": farm.ai_protection_mode,
        "auto_responder_enabled": farm.auto_responder_enabled,
        "auto_responder_prompt": farm.auto_responder_prompt,
        "auto_responder_redirect_url": farm.auto_responder_redirect_url,
        "created_at": farm.created_at.isoformat() if farm.created_at else None,
        "updated_at": farm.updated_at.isoformat() if farm.updated_at else None,
    }


def _serialize_farm_thread(t: FarmThread) -> dict[str, Any]:
    return {
        "id": int(t.id),
        "farm_id": int(t.farm_id),
        "account_id": int(t.account_id),
        "thread_index": t.thread_index,
        "status": t.status,
        "assigned_channels": t.assigned_channels or [],
        "folder_invite_link": t.folder_invite_link,
        "stats_comments_sent": t.stats_comments_sent,
        "stats_comments_failed": t.stats_comments_failed,
        "stats_reactions_sent": t.stats_reactions_sent,
        "stats_last_comment_at": t.stats_last_comment_at.isoformat() if t.stats_last_comment_at else None,
        "stats_last_error": t.stats_last_error,
        "health_score": t.health_score,
        "quarantine_until": t.quarantine_until.isoformat() if t.quarantine_until else None,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _serialize_farm_event(e: FarmEvent) -> dict[str, Any]:
    return {
        "id": int(e.id),
        "farm_id": int(e.farm_id),
        "thread_id": int(e.thread_id) if e.thread_id else None,
        "event_type": e.event_type,
        "severity": e.severity,
        "message": e.message,
        "metadata": e.event_metadata or {},
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def _serialize_channel_db(db: ChannelDatabase, channel_count: int = 0) -> dict[str, Any]:
    return {
        "id": int(db.id),
        "name": db.name,
        "source": db.source,
        "status": db.status,
        "channel_count": channel_count,
        "created_at": db.created_at.isoformat() if db.created_at else None,
    }


def _serialize_channel_entry(e: ChannelEntry) -> dict[str, Any]:
    return {
        "id": int(e.id),
        "database_id": int(e.database_id),
        "telegram_id": e.telegram_id,
        "username": e.username,
        "title": e.title,
        "member_count": e.member_count,
        "has_comments": e.has_comments,
        "language": e.language,
        "category": e.category,
        "last_post_at": e.last_post_at.isoformat() if e.last_post_at else None,
        "blacklisted": e.blacklisted,
        "success_rate": e.success_rate,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def _serialize_parsing_job(j: ParsingJob) -> dict[str, Any]:
    return {
        "id": int(j.id),
        "job_type": j.job_type,
        "status": j.status,
        "keywords": j.keywords or [],
        "filters": j.filters or {},
        "max_results": j.max_results,
        "results_count": j.results_count,
        "progress": j.progress if j.progress is not None else 0,
        "account_id": j.account_id,
        "target_database_id": j.target_database_id,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "completed_at": j.completed_at.isoformat() if j.completed_at else None,
        "error": j.error,
        "created_at": j.created_at.isoformat() if j.created_at else None,
    }


def _serialize_profile_template(t: ProfileTemplate) -> dict[str, Any]:
    return {
        "id": int(t.id),
        "name": t.name,
        "gender": t.gender,
        "geo": t.geo,
        "bio_template": t.bio_template,
        "channel_name_template": t.channel_name_template,
        "channel_description_template": t.channel_description_template,
        "channel_first_post_template": t.channel_first_post_template,
        "avatar_style": t.avatar_style,
        "avatar_url": t.avatar_url,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


# ---------------------------------------------------------------------------
# Farm CRUD + Control
# ---------------------------------------------------------------------------

@app.post("/v1/farm", status_code=status.HTTP_201_CREATED)
async def farm_create(
    payload: FarmCreatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    farm = FarmConfig(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        name=payload.name,
        status="stopped",
        mode=payload.mode,
        max_threads=payload.max_threads,
        comment_prompt=payload.comment_prompt,
        comment_tone=payload.comment_tone,
        comment_language=payload.comment_language,
        comment_all_posts=payload.comment_all_posts,
        comment_percentage=payload.comment_percentage,
        delay_before_comment_min=payload.delay_before_comment_min,
        delay_before_comment_max=payload.delay_before_comment_max,
        delay_before_join_min=payload.delay_before_join_min,
        delay_before_join_max=payload.delay_before_join_max,
        ai_protection_mode=payload.ai_protection_mode,
        auto_responder_enabled=payload.auto_responder_enabled,
        auto_responder_prompt=payload.auto_responder_prompt,
        auto_responder_redirect_url=payload.auto_responder_redirect_url,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(farm)
    await session.flush()
    return _serialize_farm(farm)


@app.get("/v1/farm")
async def farm_list(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    base_filter = [
        FarmConfig.tenant_id == tenant_context.tenant_id,
        FarmConfig.workspace_id == tenant_context.workspace_id,
    ]
    rows = (
        await session.execute(
            select(FarmConfig)
            .where(*base_filter)
            .order_by(FarmConfig.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    items = [_serialize_farm(f) for f in rows]
    return {"items": items, "total": len(items)}


@app.get("/v1/farm/{farm_id}")
async def farm_get(
    farm_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    farm = (
        await session.execute(
            select(FarmConfig).where(
                FarmConfig.id == farm_id,
                FarmConfig.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="farm_not_found")

    threads = (
        await session.execute(
            select(FarmThread).where(FarmThread.farm_id == farm_id).order_by(FarmThread.thread_index).limit(500)
        )
    ).scalars().all()

    recent_events = (
        await session.execute(
            select(FarmEvent)
            .where(FarmEvent.farm_id == farm_id)
            .order_by(FarmEvent.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    result = _serialize_farm(farm)
    result["threads_summary"] = {
        "total": len(threads),
        "running": sum(1 for t in threads if t.status not in ("idle", "stopped", "error")),
        "errors": sum(1 for t in threads if t.status == "error"),
        "quarantine": sum(1 for t in threads if t.status == "quarantine"),
    }
    result["recent_events"] = [_serialize_farm_event(e) for e in recent_events]
    return result


@app.put("/v1/farm/{farm_id}")
async def farm_update(
    farm_id: int,
    payload: FarmUpdatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    farm = (
        await session.execute(
            select(FarmConfig).where(
                FarmConfig.id == farm_id,
                FarmConfig.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="farm_not_found")

    update_data = payload.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(farm, field, value)
    farm.updated_at = utcnow()
    return _serialize_farm(farm)


@app.delete("/v1/farm/{farm_id}", status_code=status.HTTP_204_NO_CONTENT)
async def farm_delete(
    farm_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> None:
    farm = (
        await session.execute(
            select(FarmConfig).where(
                FarmConfig.id == farm_id,
                FarmConfig.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="farm_not_found")
    if farm.status not in ("stopped",):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="farm_must_be_stopped_before_deletion",
        )
    # Cascade: delete child rows first (events, then threads)
    from sqlalchemy import delete as sa_delete
    await session.execute(
        sa_delete(FarmEvent).where(FarmEvent.farm_id == farm_id)
    )
    await session.execute(
        sa_delete(FarmThread).where(FarmThread.farm_id == farm_id)
    )
    await session.delete(farm)


@app.post("/v1/farm/{farm_id}/start")
async def farm_start(
    farm_id: int,
    payload: FarmStartPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    farm = (
        await session.execute(
            select(FarmConfig).where(
                FarmConfig.id == farm_id,
                FarmConfig.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="farm_not_found")
    if farm.status == "running":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="farm_already_running")

    # Validate accounts belong to this tenant
    accounts = (
        await session.execute(
            select(Account).where(
                Account.id.in_(payload.account_ids),
                Account.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalars().all()
    if len(accounts) != len(set(payload.account_ids)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_not_found_or_wrong_tenant")

    # Validate channel database belongs to this tenant
    channel_db = (
        await session.execute(
            select(ChannelDatabase).where(
                ChannelDatabase.id == payload.channel_database_id,
                ChannelDatabase.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if channel_db is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel_database_not_found")

    # Load channels from database for distribution (cap at 10000)
    channel_entries = (
        await session.execute(
            select(ChannelEntry).where(
                ChannelEntry.database_id == payload.channel_database_id,
                ChannelEntry.tenant_id == tenant_context.tenant_id,
                ChannelEntry.blacklisted.is_(False),
            ).limit(10000)
        )
    ).scalars().all()
    channel_usernames = [e.username for e in channel_entries if e.username]

    # Create or refresh farm threads (1 per account)
    threads_count = len(accounts)
    channels_per_thread = max(1, len(channel_usernames) // threads_count) if channel_usernames else 0

    # Remove old threads from a prior run
    old_threads = (
        await session.execute(
            select(FarmThread).where(FarmThread.farm_id == farm_id).limit(1000)
        )
    ).scalars().all()
    for t in old_threads:
        await session.delete(t)
    await session.flush()

    new_threads: list[FarmThread] = []
    for idx, account in enumerate(accounts):
        start = idx * channels_per_thread
        end = start + channels_per_thread if idx < threads_count - 1 else len(channel_usernames)
        assigned = channel_usernames[start:end]
        thread = FarmThread(
            tenant_id=tenant_context.tenant_id,
            farm_id=farm_id,
            account_id=int(account.id),
            thread_index=idx,
            status="idle",
            assigned_channels=assigned,
            stats_comments_sent=0,
            stats_comments_failed=0,
            stats_reactions_sent=0,
            health_score=100,
            updated_at=utcnow(),
        )
        session.add(thread)
        new_threads.append(thread)

    farm.status = "running"
    farm.updated_at = utcnow()
    await session.flush()

    thread_ids = [int(t.id) for t in new_threads]

    job_result = await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_FARM_START,
        payload={
            "farm_id": farm_id,
            "thread_ids": thread_ids,
            "channel_database_id": payload.channel_database_id,
        },
    )
    return {
        "status": "starting",
        "threads_count": threads_count,
        "job_id": job_result["job_id"],
    }


@app.post("/v1/farm/{farm_id}/stop")
async def farm_stop(
    farm_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    farm = (
        await session.execute(
            select(FarmConfig).where(
                FarmConfig.id == farm_id,
                FarmConfig.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="farm_not_found")

    farm.status = "stopped"
    farm.updated_at = utcnow()

    job_result = await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_FARM_STOP,
        payload={"farm_id": farm_id},
    )
    return {"status": "stopping", "job_id": job_result["job_id"]}


@app.post("/v1/farm/{farm_id}/pause")
async def farm_pause(
    farm_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    farm = (
        await session.execute(
            select(FarmConfig).where(
                FarmConfig.id == farm_id,
                FarmConfig.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="farm_not_found")
    if farm.status != "running":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="farm_not_running")

    farm.status = "paused"
    farm.updated_at = utcnow()

    job_result = await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_FARM_PAUSE,
        payload={"farm_id": farm_id},
    )
    return {"status": "paused", "job_id": job_result["job_id"]}


@app.post("/v1/farm/{farm_id}/resume")
async def farm_resume(
    farm_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    farm = (
        await session.execute(
            select(FarmConfig).where(
                FarmConfig.id == farm_id,
                FarmConfig.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="farm_not_found")
    if farm.status != "paused":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="farm_not_paused")

    farm.status = "running"
    farm.updated_at = utcnow()

    job_result = await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_FARM_RESUME,
        payload={"farm_id": farm_id},
    )
    return {"status": "running", "job_id": job_result["job_id"]}


@app.get("/v1/farm/{farm_id}/threads")
async def farm_threads(
    farm_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    farm = (
        await session.execute(
            select(FarmConfig).where(
                FarmConfig.id == farm_id,
                FarmConfig.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="farm_not_found")

    threads = (
        await session.execute(
            select(FarmThread, Account.phone)
            .join(Account, Account.id == FarmThread.account_id)
            .where(FarmThread.farm_id == farm_id)
            .order_by(FarmThread.thread_index)
            .limit(500)
        )
    ).all()

    items = []
    for thread, account_phone in threads:
        item = _serialize_farm_thread(thread)
        item["account_phone"] = account_phone
        items.append(item)

    return {"items": items, "total": len(items)}


@app.get("/v1/farm/{farm_id}/events")
async def farm_events(
    farm_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    severity: Optional[str] = Query(default=None),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    farm = (
        await session.execute(
            select(FarmConfig).where(
                FarmConfig.id == farm_id,
                FarmConfig.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="farm_not_found")

    query = select(FarmEvent).where(FarmEvent.farm_id == farm_id)
    count_query = select(func.count()).select_from(FarmEvent).where(FarmEvent.farm_id == farm_id)

    if severity:
        allowed_severities = {s.strip() for s in severity.split(",")}
        query = query.where(FarmEvent.severity.in_(allowed_severities))
        count_query = count_query.where(FarmEvent.severity.in_(allowed_severities))

    total = (await session.execute(count_query)).scalar_one()
    rows = (
        await session.execute(query.order_by(FarmEvent.created_at.desc()).offset(offset).limit(limit))
    ).scalars().all()

    return {"items": [_serialize_farm_event(e) for e in rows], "total": total}


# ---------------------------------------------------------------------------
# Channel Database
# ---------------------------------------------------------------------------

@app.post("/v1/channel-db", status_code=status.HTTP_201_CREATED)
async def channel_db_create(
    payload: ChannelDbCreatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    channel_db = ChannelDatabase(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        name=payload.name,
        source="manual",
        status="active",
        created_at=utcnow(),
    )
    session.add(channel_db)
    await session.flush()
    return _serialize_channel_db(channel_db)


@app.get("/v1/channel-db")
async def channel_db_list(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    # Single query with subquery for counts instead of N+1
    count_subq = (
        select(
            ChannelEntry.database_id,
            func.count().label("channel_count"),
        )
        .group_by(ChannelEntry.database_id)
        .subquery()
    )
    stmt = (
        select(ChannelDatabase, count_subq.c.channel_count)
        .outerjoin(count_subq, ChannelDatabase.id == count_subq.c.database_id)
        .where(
            ChannelDatabase.tenant_id == tenant_context.tenant_id,
            ChannelDatabase.workspace_id == tenant_context.workspace_id,
        )
        .order_by(ChannelDatabase.id.desc())
    )
    rows = (await session.execute(stmt)).all()

    items = [
        _serialize_channel_db(db_row, channel_count=int(count or 0))
        for db_row, count in rows
    ]
    return {"items": items, "total": len(items)}


@app.get("/v1/channel-db/{db_id}")
async def channel_db_get(
    db_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    channel_db = (
        await session.execute(
            select(ChannelDatabase).where(
                ChannelDatabase.id == db_id,
                ChannelDatabase.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if channel_db is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel_database_not_found")

    count = (
        await session.execute(
            select(func.count()).select_from(ChannelEntry).where(ChannelEntry.database_id == db_id)
        )
    ).scalar_one()
    return _serialize_channel_db(channel_db, channel_count=count)


@app.post("/v1/channel-db/{db_id}/import")
async def channel_db_import(
    db_id: int,
    payload: ChannelImportPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    channel_db = (
        await session.execute(
            select(ChannelDatabase).where(
                ChannelDatabase.id == db_id,
                ChannelDatabase.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if channel_db is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel_database_not_found")

    # Fetch existing usernames to detect duplicates
    existing_usernames_rows = (
        await session.execute(
            select(ChannelEntry.username).where(ChannelEntry.database_id == db_id).limit(50000)
        )
    ).scalars().all()
    existing_usernames = {u.lower() for u in existing_usernames_rows if u}

    imported = 0
    skipped = 0
    for ch in payload.channels:
        username_lower = ch.username.lower()
        if username_lower in existing_usernames:
            skipped += 1
            continue
        entry = ChannelEntry(
            tenant_id=tenant_context.tenant_id,
            database_id=db_id,
            username=ch.username,
            title=ch.title,
            member_count=ch.member_count,
            has_comments=ch.has_comments,
            language=ch.language,
            category=ch.category,
            blacklisted=False,
            created_at=utcnow(),
        )
        session.add(entry)
        existing_usernames.add(username_lower)
        imported += 1

    return {"imported": imported, "skipped": skipped}


@app.get("/v1/channel-db/{db_id}/channels")
async def channel_db_channels(
    db_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    blacklisted: Optional[bool] = Query(default=None),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    channel_db = (
        await session.execute(
            select(ChannelDatabase).where(
                ChannelDatabase.id == db_id,
                ChannelDatabase.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if channel_db is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel_database_not_found")

    query = select(ChannelEntry).where(ChannelEntry.database_id == db_id)
    count_query = select(func.count()).select_from(ChannelEntry).where(ChannelEntry.database_id == db_id)

    if blacklisted is not None:
        query = query.where(ChannelEntry.blacklisted.is_(blacklisted))
        count_query = count_query.where(ChannelEntry.blacklisted.is_(blacklisted))

    total = (await session.execute(count_query)).scalar_one()
    rows = (
        await session.execute(query.order_by(ChannelEntry.id).offset(offset).limit(limit))
    ).scalars().all()

    return {"items": [_serialize_channel_entry(e) for e in rows], "total": total}


@app.post("/v1/channel-db/{db_id}/channels/{channel_id}/blacklist")
async def channel_db_toggle_blacklist(
    db_id: int,
    channel_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    entry = (
        await session.execute(
            select(ChannelEntry).where(
                ChannelEntry.id == channel_id,
                ChannelEntry.database_id == db_id,
                ChannelEntry.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel_entry_not_found")

    entry.blacklisted = not entry.blacklisted
    return {"id": int(entry.id), "blacklisted": entry.blacklisted}


@app.delete("/v1/channel-db/{db_id}/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def channel_db_remove_channel(
    db_id: int,
    channel_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> None:
    entry = (
        await session.execute(
            select(ChannelEntry).where(
                ChannelEntry.id == channel_id,
                ChannelEntry.database_id == db_id,
                ChannelEntry.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel_entry_not_found")
    await session.delete(entry)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

@app.post("/v1/parser/channels", status_code=status.HTTP_201_CREATED)
async def parser_start_channels(
    payload: ParserChannelsPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    # Validate account belongs to tenant if specified
    if payload.account_id is not None:
        account = (
            await session.execute(
                select(Account).where(
                    Account.id == payload.account_id,
                    Account.tenant_id == tenant_context.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")

    # Validate target database belongs to tenant if specified
    if payload.target_database_id is not None:
        channel_db = (
            await session.execute(
                select(ChannelDatabase).where(
                    ChannelDatabase.id == payload.target_database_id,
                    ChannelDatabase.tenant_id == tenant_context.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if channel_db is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel_database_not_found")

    parsing_job = ParsingJob(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        account_id=payload.account_id,
        job_type="channels",
        status="pending",
        keywords=payload.keywords,
        filters=payload.filters or {},
        max_results=payload.max_results,
        results_count=0,
        target_database_id=payload.target_database_id,
        created_at=utcnow(),
    )
    session.add(parsing_job)
    await session.flush()
    job_id = int(parsing_job.id)

    farm_job = await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_PARSER_CHANNELS,
        payload={
            "parsing_job_id": job_id,
            "keywords": payload.keywords,
            "filters": payload.filters or {},
            "max_results": payload.max_results,
            "account_id": payload.account_id,
            "target_database_id": payload.target_database_id,
        },
    )
    return {"job_id": job_id, "app_job_id": farm_job["job_id"], "status": "pending"}


@app.get("/v1/parser/jobs")
async def parser_job_list(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(ParsingJob)
            .where(
                ParsingJob.tenant_id == tenant_context.tenant_id,
                ParsingJob.workspace_id == tenant_context.workspace_id,
            )
            .order_by(ParsingJob.id.desc())
            .limit(100)
        )
    ).scalars().all()
    items = [_serialize_parsing_job(j) for j in rows]
    return {"items": items, "total": len(items)}


@app.get("/v1/parser/jobs/{job_id}")
async def parser_job_get(
    job_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    parsing_job = (
        await session.execute(
            select(ParsingJob).where(
                ParsingJob.id == job_id,
                ParsingJob.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if parsing_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="parsing_job_not_found")

    result = _serialize_parsing_job(parsing_job)
    if parsing_job.status == "completed" and parsing_job.target_database_id is not None:
        channel_count = (
            await session.execute(
                select(func.count()).select_from(ChannelEntry).where(
                    ChannelEntry.database_id == parsing_job.target_database_id
                )
            )
        ).scalar_one()
        result["results_in_database"] = channel_count
    return result


@app.delete("/v1/parser/jobs/{job_id}")
async def parser_job_cancel(
    job_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Cancel a pending or running parsing job."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=30, window_seconds=60)
    parsing_job = (
        await session.execute(
            select(ParsingJob).where(
                ParsingJob.id == job_id,
                ParsingJob.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if parsing_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="parsing_job_not_found")
    if parsing_job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot_cancel_job_in_status:{parsing_job.status}",
        )
    parsing_job.status = "cancelled"
    parsing_job.completed_at = utcnow()
    await session.commit()
    return {"job_id": job_id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

@app.post("/v1/profiles/generate")
async def profiles_generate(
    payload: ProfileGeneratePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    account = (
        await session.execute(
            select(Account).where(
                Account.id == payload.account_id,
                Account.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")

    if payload.template_id is not None:
        template = (
            await session.execute(
                select(ProfileTemplate).where(
                    ProfileTemplate.id == payload.template_id,
                    ProfileTemplate.tenant_id == tenant_context.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="profile_template_not_found")

    return await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_PROFILE_GENERATE,
        payload={
            "account_id": payload.account_id,
            "template_id": payload.template_id,
        },
    )


@app.post("/v1/profiles/mass-generate")
async def profiles_mass_generate(
    payload: ProfileMassGeneratePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    accounts = (
        await session.execute(
            select(Account).where(
                Account.id.in_(payload.account_ids),
                Account.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalars().all()
    if len(accounts) != len(set(payload.account_ids)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_not_found_or_wrong_tenant")

    if payload.template_id is not None:
        template = (
            await session.execute(
                select(ProfileTemplate).where(
                    ProfileTemplate.id == payload.template_id,
                    ProfileTemplate.tenant_id == tenant_context.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="profile_template_not_found")

    return await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_PROFILE_MASS_GENERATE,
        payload={
            "account_ids": payload.account_ids,
            "template_id": payload.template_id,
        },
    )


@app.post("/v1/profiles/apply/{account_id}")
async def profiles_apply(
    account_id: int,
    payload: ProfileApplyPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    account = (
        await session.execute(
            select(Account).where(
                Account.id == account_id,
                Account.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")

    return await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_PROFILE_APPLY,
        payload={
            "account_id": account_id,
            "profile": payload.model_dump(exclude_none=True),
        },
    )


@app.post("/v1/profiles/create-channel/{account_id}")
async def profiles_create_channel(
    account_id: int,
    payload: ProfileCreateChannelPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    account = (
        await session.execute(
            select(Account).where(
                Account.id == account_id,
                Account.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")

    return await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_PROFILE_CREATE_CHANNEL,
        payload={
            "account_id": account_id,
            "channel_name": payload.channel_name,
            "channel_description": payload.channel_description,
            "first_post_text": payload.first_post_text,
            "avatar_url": payload.avatar_url,
        },
    )


@app.get("/v1/profiles/templates")
async def profiles_template_list(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(ProfileTemplate)
            .where(
                ProfileTemplate.tenant_id == tenant_context.tenant_id,
                ProfileTemplate.workspace_id == tenant_context.workspace_id,
            )
            .order_by(ProfileTemplate.id.desc())
            .limit(500)
        )
    ).scalars().all()
    items = [_serialize_profile_template(t) for t in rows]
    return {"items": items, "total": len(items)}


@app.post("/v1/profiles/templates", status_code=status.HTTP_201_CREATED)
async def profiles_template_create(
    payload: ProfileTemplateCreatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    template = ProfileTemplate(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        name=payload.name,
        gender=payload.gender,
        geo=payload.geo,
        bio_template=payload.bio_template,
        channel_name_template=payload.channel_name_template,
        channel_description_template=payload.channel_description_template,
        channel_first_post_template=payload.channel_first_post_template,
        avatar_style=payload.avatar_style,
        avatar_url=payload.avatar_url,
        created_at=utcnow(),
    )
    session.add(template)
    await session.flush()
    return _serialize_profile_template(template)


# ---------------------------------------------------------------------------
# Sprint 6 — Warmup / Health / Quarantine endpoints
# ---------------------------------------------------------------------------


def _serialize_warmup_config(c: WarmupConfig, account_count: int = 0) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "mode": c.mode,
        "status": c.status or "stopped",
        "account_count": account_count,
        "safety_limit_per_hour": c.safety_limit_actions_per_hour,
        "active_hours_start": str(c.active_hours_start).zfill(2) + ":00" if c.active_hours_start is not None else "09:00",
        "active_hours_end": str(c.active_hours_end).zfill(2) + ":00" if c.active_hours_end is not None else "23:00",
        "session_duration_minutes": c.warmup_duration_minutes,
        "interval_between_sessions_hours": c.interval_between_sessions_hours,
        "enable_reactions": c.enable_reactions,
        "enable_read_channels": c.enable_read_channels,
        "enable_dialogs": c.enable_dialogs_between_accounts,
        "target_channels": c.target_channels or [],
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _serialize_warmup_session(s: WarmupSession, phone: Optional[str] = None) -> dict[str, Any]:
    return {
        "id": s.id,
        "config_id": s.warmup_id,
        "account_id": s.account_id,
        "account_phone": phone,
        "status": s.status or "pending",
        "actions_performed": s.actions_performed or 0,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        "next_session_at": s.next_session_at.isoformat() if s.next_session_at else None,
    }


def _serialize_health_score(h: AccountHealthScore, phone: Optional[str] = None) -> dict[str, Any]:
    return {
        "account_id": h.account_id,
        "account_phone": phone,
        "health_score": h.health_score or 0,
        "survivability_score": h.survivability_score or 0,
        "flood_wait_count": h.flood_wait_count or 0,
        "spam_block_count": h.spam_block_count or 0,
        "successful_actions": h.successful_actions or 0,
        "factors": h.factors or {},
        "recent_events": [],
        "calculated_at": h.last_calculated_at.isoformat() if h.last_calculated_at else None,
    }


# --- Warmup CRUD ---


@app.get("/v1/warmup")
async def warmup_list(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(WarmupConfig).where(
                WarmupConfig.tenant_id == tenant_context.tenant_id
            ).order_by(WarmupConfig.created_at.desc()).limit(500)
        )
    ).scalars().all()

    items = []
    for c in rows:
        cnt = (
            await session.execute(
                select(func.count(WarmupSession.id)).where(
                    WarmupSession.warmup_id == c.id,
                    WarmupSession.tenant_id == tenant_context.tenant_id,
                )
            )
        ).scalar() or 0
        items.append(_serialize_warmup_config(c, account_count=cnt))

    return {"items": items, "total": len(items)}


@app.get("/v1/warmup/{warmup_id}")
async def warmup_get(
    warmup_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    cfg = (
        await session.execute(
            select(WarmupConfig).where(
                WarmupConfig.id == warmup_id,
                WarmupConfig.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="warmup_not_found")

    cnt = (
        await session.execute(
            select(func.count(WarmupSession.id)).where(
                WarmupSession.warmup_id == cfg.id,
                WarmupSession.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar() or 0
    return _serialize_warmup_config(cfg, account_count=cnt)


@app.post("/v1/warmup", status_code=status.HTTP_201_CREATED)
async def warmup_create(
    payload: WarmupCreatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    cfg = WarmupConfig(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        name=payload.name,
        mode=payload.mode,
        status="stopped",
        safety_limit_actions_per_hour=payload.safety_limit_actions_per_hour,
        active_hours_start=payload.active_hours_start,
        active_hours_end=payload.active_hours_end,
        warmup_duration_minutes=payload.warmup_duration_minutes,
        interval_between_sessions_hours=payload.interval_between_sessions_hours,
        enable_reactions=payload.enable_reactions,
        enable_read_channels=payload.enable_read_channels,
        enable_dialogs_between_accounts=payload.enable_dialogs_between_accounts,
        target_channels=payload.target_channels,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(cfg)
    await session.flush()
    return _serialize_warmup_config(cfg)


@app.put("/v1/warmup/{warmup_id}")
async def warmup_update(
    warmup_id: int,
    payload: WarmupUpdatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    cfg = (
        await session.execute(
            select(WarmupConfig).where(
                WarmupConfig.id == warmup_id,
                WarmupConfig.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="warmup_not_found")
    if cfg.status == "running":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="stop_warmup_first")

    updates = payload.model_dump(exclude_unset=True)
    for key, val in updates.items():
        setattr(cfg, key, val)
    cfg.updated_at = utcnow()
    return _serialize_warmup_config(cfg)


@app.delete("/v1/warmup/{warmup_id}", status_code=status.HTTP_204_NO_CONTENT)
async def warmup_delete(
    warmup_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> None:
    cfg = (
        await session.execute(
            select(WarmupConfig).where(
                WarmupConfig.id == warmup_id,
                WarmupConfig.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="warmup_not_found")
    if cfg.status == "running":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="stop_warmup_first")

    from sqlalchemy import delete as sa_delete
    await session.execute(sa_delete(WarmupSession).where(
        WarmupSession.warmup_id == warmup_id,
        WarmupSession.tenant_id == tenant_context.tenant_id,
    ))
    await session.delete(cfg)


# --- Warmup control ---


@app.post("/v1/warmup/{warmup_id}/start")
async def warmup_start(
    warmup_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    cfg = (
        await session.execute(
            select(WarmupConfig).where(
                WarmupConfig.id == warmup_id,
                WarmupConfig.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="warmup_not_found")
    if cfg.status == "running":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="warmup_already_running")

    cfg.status = "running"
    cfg.updated_at = utcnow()

    job_result = await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_WARMUP_START,
        payload={"warmup_id": warmup_id},
    )
    return {"status": "starting", "job_id": job_result["job_id"]}


@app.post("/v1/warmup/{warmup_id}/stop")
async def warmup_stop(
    warmup_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    cfg = (
        await session.execute(
            select(WarmupConfig).where(
                WarmupConfig.id == warmup_id,
                WarmupConfig.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="warmup_not_found")
    if cfg.status != "running":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="warmup_not_running")

    cfg.status = "stopped"
    cfg.updated_at = utcnow()

    job_result = await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_WARMUP_STOP,
        payload={"warmup_id": warmup_id},
    )
    return {"status": "stopping", "job_id": job_result["job_id"]}


# --- Warmup sessions ---


@app.get("/v1/warmup/{warmup_id}/sessions")
async def warmup_sessions_list(
    warmup_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    cfg = (
        await session.execute(
            select(WarmupConfig).where(
                WarmupConfig.id == warmup_id,
                WarmupConfig.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="warmup_not_found")

    rows = (
        await session.execute(
            select(WarmupSession, Account.phone)
            .outerjoin(Account, Account.id == WarmupSession.account_id)
            .where(
                WarmupSession.warmup_id == warmup_id,
                WarmupSession.tenant_id == tenant_context.tenant_id,
            )
            .order_by(WarmupSession.id)
            .limit(1000)
        )
    ).all()

    items = [_serialize_warmup_session(ws, phone=ph) for ws, ph in rows]
    return {"items": items, "total": len(items)}


# --- Health scores ---


@app.get("/v1/health/scores")
async def health_scores_list(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(AccountHealthScore, Account.phone)
            .outerjoin(Account, Account.id == AccountHealthScore.account_id)
            .where(AccountHealthScore.tenant_id == tenant_context.tenant_id)
            .order_by(AccountHealthScore.health_score.asc())
            .limit(5000)
        )
    ).all()

    items = [_serialize_health_score(h, phone=ph) for h, ph in rows]
    return {"items": items, "total": len(items)}


@app.get("/v1/health/scores/{account_id}")
async def health_score_detail(
    account_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(AccountHealthScore, Account.phone)
            .outerjoin(Account, Account.id == AccountHealthScore.account_id)
            .where(
                AccountHealthScore.account_id == account_id,
                AccountHealthScore.tenant_id == tenant_context.tenant_id,
            )
        )
    ).first()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="health_score_not_found")

    h, phone = row
    result = _serialize_health_score(h, phone=phone)

    # Attach recent farm events for this account's threads (tenant-scoped)
    events = (
        await session.execute(
            select(FarmEvent).where(
                FarmEvent.thread_id.in_(
                    select(FarmThread.id).where(
                        FarmThread.account_id == account_id,
                        FarmThread.tenant_id == tenant_context.tenant_id,
                    )
                )
            ).order_by(FarmEvent.created_at.desc()).limit(20)
        )
    ).scalars().all()

    result["recent_events"] = [
        {
            "event_type": e.event_type,
            "severity": e.severity or "info",
            "message": e.message,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]

    return result


@app.get("/v1/health/scores/{account_id}/history")
async def health_score_history(
    account_id: int,
    days: int = Query(default=30, ge=1, le=365),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Return daily health score snapshots for the given account over the last N days."""
    from datetime import date as _date, timedelta as _td
    cutoff = _date.today() - _td(days=days)

    # Verify the account belongs to this tenant
    account_exists = (
        await session.execute(
            select(Account.id).where(
                Account.id == account_id,
                Account.tenant_id == tenant_context.tenant_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if account_exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")

    rows = (
        await session.execute(
            select(AccountHealthHistory)
            .where(
                AccountHealthHistory.account_id == account_id,
                AccountHealthHistory.tenant_id == tenant_context.tenant_id,
                AccountHealthHistory.snapshot_date >= cutoff,
            )
            .order_by(AccountHealthHistory.snapshot_date.asc())
            .limit(days)
        )
    ).scalars().all()

    items = [
        {
            "date": r.snapshot_date.isoformat(),
            "score": r.health_score or 0,
            "survivability": r.survivability_score or 0,
        }
        for r in rows
    ]
    return {"account_id": account_id, "days": days, "items": items}


@app.post("/v1/health/recalculate")
async def health_recalculate(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    job_result = await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_HEALTH_RECALCULATE,
        payload={},
    )
    return {"status": "recalculating", "job_id": job_result["job_id"]}


# --- Quarantine ---


@app.get("/v1/health/quarantine")
async def quarantine_list(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    now = utcnow()
    base_where = [
        FarmThread.tenant_id == tenant_context.tenant_id,
        FarmThread.status == "quarantine",
        FarmThread.quarantine_until > now,
    ]
    count_row = (
        await session.execute(
            select(func.count(func.distinct(FarmThread.account_id))).where(*base_where)
        )
    ).scalar_one()

    rows = (
        await session.execute(
            select(
                FarmThread.account_id,
                Account.phone,
                FarmThread.stats_last_error,
                func.max(FarmThread.quarantine_until).label("quarantine_until"),
            )
            .outerjoin(Account, Account.id == FarmThread.account_id)
            .where(*base_where)
            .group_by(FarmThread.account_id, Account.phone, FarmThread.stats_last_error)
            .limit(limit)
            .offset(offset)
        )
    ).all()

    items = [
        {
            "account_id": r.account_id,
            "account_phone": r.phone,
            "quarantine_reason": r.stats_last_error,
            "quarantine_until": r.quarantine_until.isoformat() if r.quarantine_until else None,
        }
        for r in rows
    ]
    return {"items": items, "total": count_row, "limit": limit, "offset": offset}


@app.post("/v1/health/quarantine/{account_id}/lift")
async def quarantine_lift(
    account_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    from sqlalchemy import update as sa_update

    result = await session.execute(
        sa_update(FarmThread)
        .where(
            FarmThread.account_id == account_id,
            FarmThread.tenant_id == tenant_context.tenant_id,
            FarmThread.status == "quarantine",
        )
        .values(
            status="idle",
            quarantine_until=None,
            updated_at=utcnow(),
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no_quarantined_threads")
    return {"status": "quarantine_lifted", "threads_updated": result.rowcount}


# ---------------------------------------------------------------------------
# Sprint 7 — Mass Reactions, Chatting, Dialogs, User Parser, Folders
# ---------------------------------------------------------------------------


def _serialize_reaction_job(r: ReactionJob) -> dict[str, Any]:
    return {
        "id": int(r.id),
        "channel_username": r.channel_username,
        "reaction_type": r.reaction_type,
        "post_id": r.post_id,
        "account_ids": r.account_ids or [],
        "status": r.status or "pending",
        "total_reactions": r.total_reactions or 0,
        "successful_reactions": r.successful_reactions or 0,
        "failed_reactions": r.failed_reactions or 0,
        "error": r.error,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


def _serialize_chatting_config(c: ChattingConfig) -> dict[str, Any]:
    return {
        "id": int(c.id),
        "name": c.name,
        "status": c.status or "stopped",
        "mode": c.mode,
        "target_channels": c.target_channels or [],
        "prompt_template": c.prompt_template,
        "max_messages_per_hour": c.max_messages_per_hour,
        "min_delay_seconds": c.min_delay_seconds,
        "max_delay_seconds": c.max_delay_seconds,
        "account_ids": c.account_ids or [],
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _serialize_dialog_config(d: DialogConfig) -> dict[str, Any]:
    return {
        "id": int(d.id),
        "name": d.name,
        "status": d.status or "stopped",
        "dialog_type": d.dialog_type,
        "account_pairs": d.account_pairs or [],
        "prompt_template": d.prompt_template,
        "messages_per_session": d.messages_per_session,
        "session_interval_hours": d.session_interval_hours,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _serialize_user_result(u: UserParsingResult) -> dict[str, Any]:
    return {
        "id": int(u.id),
        "channel_username": u.channel_username,
        "user_telegram_id": u.user_telegram_id,
        "username": u.username,
        "first_name": u.first_name,
        "last_name": u.last_name,
        "bio": u.bio,
        "is_premium": u.is_premium or False,
        "last_seen": u.last_seen.isoformat() if u.last_seen else None,
        "parsed_at": u.parsed_at.isoformat() if u.parsed_at else None,
    }


def _serialize_folder(f: TelegramFolder) -> dict[str, Any]:
    return {
        "id": int(f.id),
        "account_id": int(f.account_id),
        "folder_name": f.folder_name,
        "folder_id": f.folder_id,
        "invite_link": f.invite_link,
        "channel_usernames": f.channel_usernames or [],
        "status": f.status or "active",
        "created_at": f.created_at.isoformat() if f.created_at else None,
        "updated_at": f.updated_at.isoformat() if f.updated_at else None,
    }


# --- Mass Reactions ---


@app.post("/v1/reactions", status_code=status.HTTP_201_CREATED)
async def reactions_create(
    payload: ReactionJobCreatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    job = ReactionJob(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        channel_username=payload.channel_username,
        reaction_type=payload.reaction_type,
        account_ids=payload.account_ids,
        post_id=payload.post_id,
        status="pending",
        created_at=utcnow(),
    )
    session.add(job)
    await session.flush()
    result = _serialize_reaction_job(job)
    await _enqueue_within_session(
        session=session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_REACTION_RUN,
        payload={"reaction_job_id": int(job.id)},
    )
    return result


@app.get("/v1/reactions")
async def reactions_list(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(ReactionJob)
            .where(ReactionJob.tenant_id == tenant_context.tenant_id)
            .order_by(ReactionJob.id.desc())
            .limit(100)
        )
    ).scalars().all()
    items = [_serialize_reaction_job(r) for r in rows]
    return {"items": items, "total": len(items)}


@app.get("/v1/reactions/{job_id}")
async def reactions_get(
    job_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(ReactionJob).where(
                ReactionJob.id == job_id,
                ReactionJob.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reaction_job_not_found")
    return _serialize_reaction_job(row)


# --- Neuro Chatting ---


@app.get("/v1/chatting")
async def chatting_list(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(ChattingConfig)
            .where(ChattingConfig.tenant_id == tenant_context.tenant_id)
            .order_by(ChattingConfig.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    items = [_serialize_chatting_config(c) for c in rows]
    return {"items": items, "total": len(items)}


@app.post("/v1/chatting", status_code=status.HTTP_201_CREATED)
async def chatting_create(
    payload: ChattingConfigCreatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    cfg = ChattingConfig(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        name=payload.name,
        mode=payload.mode,
        target_channels=payload.target_channels,
        prompt_template=payload.prompt_template,
        max_messages_per_hour=payload.max_messages_per_hour,
        min_delay_seconds=payload.min_delay_seconds,
        max_delay_seconds=payload.max_delay_seconds,
        account_ids=payload.account_ids,
        status="stopped",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(cfg)
    await session.flush()
    return _serialize_chatting_config(cfg)


@app.post("/v1/chatting/{config_id}/start")
async def chatting_start(
    config_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    cfg = (
        await session.execute(
            select(ChattingConfig).where(
                ChattingConfig.id == config_id,
                ChattingConfig.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chatting_config_not_found")
    cfg.status = "running"
    cfg.updated_at = utcnow()
    await _enqueue_within_session(
        session=session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_CHATTING_START,
        payload={"config_id": config_id},
    )
    return {"config_id": config_id, "status": "running"}


@app.post("/v1/chatting/{config_id}/stop")
async def chatting_stop(
    config_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    cfg = (
        await session.execute(
            select(ChattingConfig).where(
                ChattingConfig.id == config_id,
                ChattingConfig.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chatting_config_not_found")
    cfg.status = "stopped"
    cfg.updated_at = utcnow()
    await _enqueue_within_session(
        session=session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_CHATTING_STOP,
        payload={"config_id": config_id},
    )
    return {"config_id": config_id, "status": "stopped"}


@app.delete("/v1/chatting/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def chatting_delete(
    config_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> None:
    cfg = (
        await session.execute(
            select(ChattingConfig).where(
                ChattingConfig.id == config_id,
                ChattingConfig.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chatting_config_not_found")
    if cfg.status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="chatting_config_running")
    await session.delete(cfg)


# --- Neuro Dialogs ---


@app.get("/v1/dialogs")
async def dialogs_list(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(DialogConfig)
            .where(DialogConfig.tenant_id == tenant_context.tenant_id)
            .order_by(DialogConfig.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    items = [_serialize_dialog_config(d) for d in rows]
    return {"items": items, "total": len(items)}


@app.post("/v1/dialogs", status_code=status.HTTP_201_CREATED)
async def dialogs_create(
    payload: DialogConfigCreatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    cfg = DialogConfig(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        name=payload.name,
        dialog_type=payload.dialog_type,
        account_pairs=payload.account_pairs,
        prompt_template=payload.prompt_template,
        messages_per_session=payload.messages_per_session,
        session_interval_hours=payload.session_interval_hours,
        status="stopped",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(cfg)
    await session.flush()
    return _serialize_dialog_config(cfg)


@app.post("/v1/dialogs/{config_id}/start")
async def dialogs_start(
    config_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    cfg = (
        await session.execute(
            select(DialogConfig).where(
                DialogConfig.id == config_id,
                DialogConfig.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dialog_config_not_found")
    cfg.status = "running"
    cfg.updated_at = utcnow()
    await _enqueue_within_session(
        session=session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_DIALOG_START,
        payload={"config_id": config_id},
    )
    return {"config_id": config_id, "status": "running"}


@app.post("/v1/dialogs/{config_id}/stop")
async def dialogs_stop(
    config_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    cfg = (
        await session.execute(
            select(DialogConfig).where(
                DialogConfig.id == config_id,
                DialogConfig.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dialog_config_not_found")
    cfg.status = "stopped"
    cfg.updated_at = utcnow()
    await _enqueue_within_session(
        session=session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_DIALOG_STOP,
        payload={"config_id": config_id},
    )
    return {"config_id": config_id, "status": "stopped"}


@app.delete("/v1/dialogs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def dialogs_delete(
    config_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> None:
    cfg = (
        await session.execute(
            select(DialogConfig).where(
                DialogConfig.id == config_id,
                DialogConfig.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dialog_config_not_found")
    await session.delete(cfg)


# --- User Parser ---


@app.post("/v1/user-parser/parse", status_code=status.HTTP_202_ACCEPTED)
async def user_parser_start(
    payload: UserParsePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    account = (
        await session.execute(
            select(Account).where(
                Account.id == payload.account_id,
                Account.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")

    enqueue_result = await _enqueue_within_session(
        session=session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_USER_PARSE,
        payload={
            "channel_username": payload.channel_username,
            "account_id": payload.account_id,
        },
    )
    return {
        "status": "accepted",
        "channel_username": payload.channel_username,
        "job_id": enqueue_result.get("job_id"),
    }


@app.get("/v1/user-parser/results")
async def user_parser_results(
    channel: Optional[str] = Query(default=None, max_length=200),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    q = select(UserParsingResult).where(
        UserParsingResult.tenant_id == tenant_context.tenant_id
    )
    if channel:
        q = q.where(UserParsingResult.channel_username == channel)
    q = q.order_by(UserParsingResult.id.desc()).limit(500)
    rows = (await session.execute(q)).scalars().all()
    items = [_serialize_user_result(u) for u in rows]
    return {"items": items, "total": len(items)}


# --- Folder Manager ---


@app.get("/v1/folders")
async def folders_list(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(TelegramFolder)
            .where(TelegramFolder.tenant_id == tenant_context.tenant_id)
            .order_by(TelegramFolder.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    items = [_serialize_folder(f) for f in rows]
    return {"items": items, "total": len(items)}


@app.post("/v1/folders", status_code=status.HTTP_201_CREATED)
async def folders_create(
    payload: FolderCreatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    account = (
        await session.execute(
            select(Account).where(
                Account.id == payload.account_id,
                Account.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")

    folder = TelegramFolder(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        account_id=payload.account_id,
        folder_name=payload.folder_name,
        channel_usernames=payload.channel_usernames,
        status="active",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(folder)
    await session.flush()
    await _enqueue_within_session(
        session=session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_FOLDER_CREATE,
        payload={
            "folder_id": int(folder.id),
            "account_id": payload.account_id,
        },
    )
    return _serialize_folder(folder)


@app.delete("/v1/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def folders_delete(
    folder_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> None:
    folder = (
        await session.execute(
            select(TelegramFolder).where(
                TelegramFolder.id == folder_id,
                TelegramFolder.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="folder_not_found")
    await _enqueue_within_session(
        session=session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_FOLDER_DELETE,
        payload={"folder_id": folder_id},
    )
    await session.delete(folder)


@app.get("/v1/folders/{folder_id}/invite")
async def folders_invite(
    folder_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    folder = (
        await session.execute(
            select(TelegramFolder).where(
                TelegramFolder.id == folder_id,
                TelegramFolder.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="folder_not_found")
    return {
        "folder_id": folder_id,
        "folder_name": folder.folder_name,
        "invite_link": folder.invite_link,
    }


# ---------------------------------------------------------------------------
# Sprint 8 — serialization helpers
# ---------------------------------------------------------------------------


def _serialize_channel_map_entry(entry: ChannelMapEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "telegram_id": entry.telegram_id,
        "username": entry.username,
        "title": entry.title,
        "description": getattr(entry, "description", None),
        "category": entry.category,
        "subcategory": entry.subcategory,
        "language": entry.language,
        "region": getattr(entry, "region", None),
        "member_count": entry.member_count,
        "has_comments": entry.has_comments,
        "comments_enabled": getattr(entry, "comments_enabled", False),
        "avg_comments_per_post": getattr(entry, "avg_comments_per_post", None),
        "avg_post_reach": entry.avg_post_reach,
        "engagement_rate": entry.engagement_rate,
        "post_frequency_daily": getattr(entry, "post_frequency_daily", None),
        "verified": getattr(entry, "verified", False),
        "source": getattr(entry, "source", None),
        "lat": getattr(entry, "lat", None),
        "lng": getattr(entry, "lng", None),
        "last_indexed_at": entry.last_indexed_at.isoformat() if entry.last_indexed_at else None,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _channel_map_tenant_filter(tenant_id: int):
    """Return filter for platform catalog (tenant_id IS NULL) + tenant's own channels."""
    from sqlalchemy import or_
    return or_(ChannelMapEntry.tenant_id.is_(None), ChannelMapEntry.tenant_id == tenant_id)


def _serialize_campaign(campaign: Campaign) -> dict[str, Any]:
    return {
        "id": campaign.id,
        "name": campaign.name,
        "status": campaign.status,
        "campaign_type": campaign.campaign_type,
        "account_ids": campaign.account_ids or [],
        "channel_database_id": campaign.channel_database_id,
        "comment_prompt": campaign.comment_prompt,
        "comment_tone": campaign.comment_tone,
        "comment_language": campaign.comment_language,
        "schedule_type": campaign.schedule_type,
        "schedule_config": campaign.schedule_config,
        "budget_daily_actions": campaign.budget_daily_actions,
        "budget_total_actions": campaign.budget_total_actions,
        "total_actions_performed": campaign.total_actions_performed,
        "total_comments_sent": campaign.total_comments_sent,
        "total_reactions_sent": campaign.total_reactions_sent,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "completed_at": campaign.completed_at.isoformat() if campaign.completed_at else None,
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
        "updated_at": campaign.updated_at.isoformat() if campaign.updated_at else None,
    }


def _serialize_campaign_run(run: CampaignRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "campaign_id": run.campaign_id,
        "status": run.status,
        "actions_performed": run.actions_performed,
        "comments_sent": run.comments_sent,
        "reactions_sent": run.reactions_sent,
        "errors": run.errors,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "run_log": run.run_log,
    }


def _serialize_analytics_event(event: AnalyticsEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "tenant_id": event.tenant_id,
        "workspace_id": event.workspace_id,
        "event_type": event.event_type,
        "account_id": event.account_id,
        "campaign_id": event.campaign_id,
        "channel_username": event.channel_username,
        "event_data": event.event_data,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


# ---------------------------------------------------------------------------
# Sprint 8 — Channel Map endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/channel-map")
async def channel_map_list(
    category: Optional[str] = Query(default=None),
    language: Optional[str] = Query(default=None),
    region: Optional[str] = Query(default=None),
    min_members: Optional[int] = Query(default=None, ge=0),
    max_members: Optional[int] = Query(default=None),
    has_comments: Optional[bool] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    from sqlalchemy import or_
    tf = _channel_map_tenant_filter(tenant_context.tenant_id)
    q = select(ChannelMapEntry).where(tf)
    count_q = select(func.count()).select_from(ChannelMapEntry).where(tf)
    if category is not None:
        q = q.where(ChannelMapEntry.category == category)
        count_q = count_q.where(ChannelMapEntry.category == category)
    if language is not None:
        q = q.where(ChannelMapEntry.language == language)
        count_q = count_q.where(ChannelMapEntry.language == language)
    if region is not None:
        q = q.where(ChannelMapEntry.region == region)
        count_q = count_q.where(ChannelMapEntry.region == region)
    if min_members is not None:
        q = q.where(ChannelMapEntry.member_count >= min_members)
        count_q = count_q.where(ChannelMapEntry.member_count >= min_members)
    if max_members is not None:
        q = q.where(ChannelMapEntry.member_count <= max_members)
        count_q = count_q.where(ChannelMapEntry.member_count <= max_members)
    if has_comments is not None:
        q = q.where(ChannelMapEntry.comments_enabled == has_comments)
        count_q = count_q.where(ChannelMapEntry.comments_enabled == has_comments)
    if search:
        safe_search = _escape_like(search)
        search_filter = or_(
            ChannelMapEntry.username.ilike(f"%{safe_search}%"),
            ChannelMapEntry.title.ilike(f"%{safe_search}%"),
            ChannelMapEntry.description.ilike(f"%{safe_search}%"),
        )
        q = q.where(search_filter)
        count_q = count_q.where(search_filter)
    total = (await session.execute(count_q)).scalar_one()
    q = q.order_by(ChannelMapEntry.member_count.desc()).offset(offset).limit(limit)
    rows = (await session.execute(q)).scalars().all()
    return {"items": [_serialize_channel_map_entry(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/v1/channel-map/geo")
async def channel_map_geo(
    limit: int = Query(50000, ge=1, le=50000),
    category: Optional[str] = Query(None),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
):
    """Compact geo endpoint for globe visualization — returns only coordinates + minimal data."""
    tf = _channel_map_tenant_filter(tenant_context.tenant_id)
    q = select(
        ChannelMapEntry.id,
        ChannelMapEntry.lat,
        ChannelMapEntry.lng,
        ChannelMapEntry.category,
        ChannelMapEntry.member_count,
        ChannelMapEntry.title,
        ChannelMapEntry.username,
        ChannelMapEntry.language,
        ChannelMapEntry.has_comments,
    ).where(tf).where(ChannelMapEntry.lat.isnot(None)).where(ChannelMapEntry.lng.isnot(None))
    if category:
        q = q.where(ChannelMapEntry.category == category)
    q = q.order_by(ChannelMapEntry.member_count.desc()).limit(limit)
    rows = (await session.execute(q)).fetchall()
    return {
        "points": [
            {
                "id": r.id, "lat": r.lat, "lng": r.lng,
                "cat": r.category, "m": r.member_count,
                "t": r.title, "u": r.username,
                "lang": r.language, "c": r.has_comments,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@app.post("/v1/channel-map/search")
async def channel_map_search(
    payload: ChannelMapSearchPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    from sqlalchemy import or_

    tf = _channel_map_tenant_filter(tenant_context.tenant_id)
    q = select(ChannelMapEntry).where(tf)
    if payload.category is not None:
        q = q.where(ChannelMapEntry.category == payload.category)
    if payload.language is not None:
        q = q.where(ChannelMapEntry.language == payload.language)
    if payload.min_members is not None:
        q = q.where(ChannelMapEntry.member_count >= payload.min_members)
    if payload.query is not None:
        sq = _escape_like(payload.query)
        q = q.where(
            or_(
                ChannelMapEntry.username.ilike(f"%{sq}%"),
                ChannelMapEntry.title.ilike(f"%{sq}%"),
                ChannelMapEntry.description.ilike(f"%{sq}%"),
            )
        )
    q = q.order_by(ChannelMapEntry.member_count.desc()).limit(payload.limit)
    rows = (await session.execute(q)).scalars().all()
    return {"items": [_serialize_channel_map_entry(r) for r in rows], "total": len(rows)}


@app.get("/v1/channel-map/categories")
async def channel_map_categories(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    from sqlalchemy import distinct

    tf = _channel_map_tenant_filter(tenant_context.tenant_id)
    rows = (
        await session.execute(
            select(distinct(ChannelMapEntry.category))
            .where(tf, ChannelMapEntry.category.isnot(None))
            .order_by(ChannelMapEntry.category)
            .limit(1000)
        )
    ).scalars().all()
    return {"categories": list(rows)}


@app.get("/v1/channel-map/stats")
async def channel_map_stats(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    from sqlalchemy import distinct

    tf = _channel_map_tenant_filter(tenant_context.tenant_id)
    total = (
        await session.execute(
            select(func.count()).select_from(ChannelMapEntry).where(tf)
        )
    ).scalar_one()

    cat_result = await session.execute(
        select(ChannelMapEntry.category, func.count(ChannelMapEntry.id))
        .where(tf, ChannelMapEntry.category.isnot(None))
        .group_by(ChannelMapEntry.category)
        .order_by(func.count(ChannelMapEntry.id).desc())
    )
    by_category = {row[0]: row[1] for row in cat_result.all()}

    lang_result = await session.execute(
        select(ChannelMapEntry.language, func.count(ChannelMapEntry.id))
        .where(tf, ChannelMapEntry.language.isnot(None))
        .group_by(ChannelMapEntry.language)
        .order_by(func.count(ChannelMapEntry.id).desc())
    )
    by_language = {row[0]: row[1] for row in lang_result.all()}

    region_result = await session.execute(
        select(ChannelMapEntry.region, func.count(ChannelMapEntry.id))
        .where(tf, ChannelMapEntry.region.isnot(None))
        .group_by(ChannelMapEntry.region)
        .order_by(func.count(ChannelMapEntry.id).desc())
    )
    by_region = {row[0]: row[1] for row in region_result.all()}

    return {
        "total_channels": total,
        "total": total,
        "by_category": by_category,
        "by_language": by_language,
        "by_region": by_region,
    }


def _format_compact_number(n: int | float) -> str:
    """Format a number into a compact human-readable string (e.g. 12.8K, 1.2M)."""
    if n is None:
        return "\u2014"
    if isinstance(n, float) and (n != n):  # NaN check
        return "\u2014"
    abs_n = abs(n)
    if abs_n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs_n >= 1_000:
        return f"{n / 1_000:.1f}K"
    if isinstance(n, float):
        return f"{n:.1f}"
    return str(n)


@app.get("/v1/channel-map/telemetry")
async def channel_map_telemetry(
    mode: str = Query("discovery", pattern="^(discovery|farm|intelligence)$"),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """HUD telemetry cards for the channel-map planet view.

    Returns 4 stat cards whose content depends on the requested *mode*:
    ``intel``, ``farm``, or ``analytics``.
    """
    from sqlalchemy import distinct

    fallback = lambda title, accent=False: {"title": title, "value": "\u2014", "accent": accent}  # noqa: E731

    if mode == "discovery":
        try:
            tf = _channel_map_tenant_filter(tenant_context.tenant_id)
            total = (
                await session.execute(
                    select(func.count()).select_from(ChannelMapEntry).where(tf)
                )
            ).scalar_one()

            top_cat_row = (
                await session.execute(
                    select(ChannelMapEntry.category, func.count(ChannelMapEntry.id))
                    .where(tf, ChannelMapEntry.category.isnot(None))
                    .group_by(ChannelMapEntry.category)
                    .order_by(func.count(ChannelMapEntry.id).desc())
                    .limit(1)
                )
            ).first()
            top_category = top_cat_row[0] if top_cat_row else "\u2014"

            avg_er_val = (
                await session.execute(
                    select(func.avg(ChannelMapEntry.engagement_rate))
                    .where(tf, ChannelMapEntry.engagement_rate.isnot(None))
                )
            ).scalar_one()
            avg_er = f"{avg_er_val:.2f}%" if avg_er_val is not None else "\u2014"

            country_count = (
                await session.execute(
                    select(func.count(distinct(ChannelMapEntry.region)))
                    .where(tf, ChannelMapEntry.region.isnot(None))
                )
            ).scalar_one()

            cards = [
                {"title": "Total Channels", "value": _format_compact_number(total), "accent": True},
                {"title": "Top Category", "value": str(top_category)},
                {"title": "Avg ER", "value": avg_er},
                {"title": "Coverage", "value": f"{country_count} countries"},
            ]
        except Exception:
            cards = [
                fallback("Total Channels", True),
                fallback("Top Category"),
                fallback("Avg ER"),
                fallback("Coverage"),
            ]

    elif mode == "farm":
        try:
            active_threads = (
                await session.execute(
                    select(func.count()).select_from(FarmThread)
                    .where(FarmThread.tenant_id == tenant_context.tenant_id, FarmThread.status == "running")
                )
            ).scalar_one()

            from datetime import timedelta
            one_hour_ago = utcnow() - timedelta(hours=1)
            comments_hr = (
                await session.execute(
                    select(func.count()).select_from(FarmEvent)
                    .where(
                        FarmEvent.tenant_id == tenant_context.tenant_id,
                        FarmEvent.event_type == "comment_sent",
                        FarmEvent.created_at >= one_hour_ago,
                    )
                )
            ).scalar_one()

            total_recent = (
                await session.execute(
                    select(func.count()).select_from(FarmEvent)
                    .where(
                        FarmEvent.tenant_id == tenant_context.tenant_id,
                        FarmEvent.event_type.in_(["comment_sent", "comment_failed"]),
                        FarmEvent.created_at >= one_hour_ago,
                    )
                )
            ).scalar_one()
            delivery_rate = f"{(comments_hr / total_recent * 100):.0f}%" if total_recent > 0 else "\u2014"

            avg_health = (
                await session.execute(
                    select(func.avg(AccountHealthScore.health_score))
                    .where(AccountHealthScore.tenant_id == tenant_context.tenant_id)
                )
            ).scalar_one()
            health_str = f"{avg_health:.0f}/100" if avg_health is not None else "\u2014"

            cards = [
                {"title": "Active Threads", "value": _format_compact_number(active_threads)},
                {"title": "Comments/hr", "value": _format_compact_number(comments_hr)},
                {"title": "Delivery Rate", "value": delivery_rate},
                {"title": "Account Health", "value": health_str},
            ]
        except Exception:
            cards = [
                fallback("Active Threads"),
                fallback("Comments/hr"),
                fallback("Delivery Rate"),
                fallback("Account Health"),
            ]

    else:  # intelligence
        cards = [
            {"title": "ROI Score", "value": "\u2014"},
            {"title": "Cost per Sub", "value": "\u2014"},
            {"title": "Weekly Growth", "value": "\u2014"},
            {"title": "Best Performing", "value": "\u2014"},
        ]

    return {"cards": cards}


# ---------------------------------------------------------------------------
# Channel Map — indexing / refresh / export (admin / internal)
# ---------------------------------------------------------------------------


class ChannelMapIndexPayload(BaseModel):
    """Payload for POST /v1/channel-map/index."""

    usernames: list[str]


class ChannelMapRefreshPayload(BaseModel):
    """Payload for POST /v1/channel-map/refresh."""

    max_age_days: int = Field(default=7, ge=1, le=365)
    limit: int = Field(default=100, ge=1, le=1000)


@app.post("/v1/channel-map/index", status_code=status.HTTP_202_ACCEPTED)
async def channel_map_index(
    payload: ChannelMapIndexPayload,
    _: None = Depends(require_internal_token),
) -> dict[str, Any]:
    """Trigger indexing of specific channels by username (admin/internal).

    Requires OPS_API_TOKEN bearer auth.
    Returns immediately after enqueueing; indexing runs synchronously for
    small batches (<=200 usernames) and returns results.
    """
    if not payload.usernames:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="usernames list must not be empty",
        )
    if len(payload.usernames) > 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="usernames list exceeds maximum of 200",
        )

    from core.channel_indexer import ChannelIndexer

    session_manager = getattr(app.state, "session_manager", None)
    indexer = ChannelIndexer(session_manager=session_manager)

    indexed: list[dict] = []
    async with async_session() as session:
        async with session.begin():
            for username in payload.usernames:
                try:
                    entry = await indexer.index_channel(username.lstrip("@"), session)
                    indexed.append({"username": entry.username, "id": entry.id, "status": "ok"})
                except Exception as exc:
                    log.warning("channel_map_index failed for %s: %s", username, exc)
                    indexed.append({"username": username, "id": None, "status": "error"})

    return {
        "requested": len(payload.usernames),
        "indexed": len([r for r in indexed if r["status"] == "ok"]),
        "results": indexed,
    }


@app.post("/v1/channel-map/refresh", status_code=status.HTTP_202_ACCEPTED)
async def channel_map_refresh(
    payload: ChannelMapRefreshPayload = ChannelMapRefreshPayload(),
    _: None = Depends(require_internal_token),
) -> dict[str, Any]:
    """Refresh stale platform catalog entries (admin/internal).

    Re-fetches live Telegram metadata for channels older than max_age_days.
    Requires OPS_API_TOKEN bearer auth.
    """
    from core.channel_indexer import ChannelIndexer

    session_manager = getattr(app.state, "session_manager", None)
    indexer = ChannelIndexer(session_manager=session_manager)

    async with async_session() as session:
        async with session.begin():
            refreshed = await indexer.refresh_stale(
                max_age_days=payload.max_age_days,
                limit=payload.limit,
                session=session,
            )
    return {"refreshed": refreshed, "max_age_days": payload.max_age_days, "limit": payload.limit}


class SmartDiscoveryPayload(BaseModel):
    """Payload for POST /v1/channel-map/smart-discover."""

    keywords: list[str] = Field(..., min_items=1, max_items=50)
    methods: Optional[list[str]] = Field(
        default=None,
        description="Discovery methods: tgstat, telethon, snowball, global_search"
    )
    max_results: int = Field(default=200, ge=1, le=2000)
    min_members: int = Field(default=1000, ge=0)
    require_comments: bool = Field(default=True)
    language_filter: Optional[str] = Field(default="ru")
    save_to_catalog: bool = Field(default=True, description="Auto-save to platform catalog")


@app.post("/v1/channel-map/smart-discover", status_code=status.HTTP_202_ACCEPTED)
async def channel_map_smart_discover(
    payload: SmartDiscoveryPayload,
    _: None = Depends(require_internal_token),
) -> dict[str, Any]:
    """Run multi-layer smart channel discovery (admin/internal).

    Combines TGStat, Telethon, snowball, and global search methods.
    Requires OPS_API_TOKEN bearer auth.
    """
    from core.smart_channel_discovery import SmartChannelDiscovery

    session_manager = getattr(app.state, "session_manager", None)
    tgstat_token = getattr(settings, "TGSTAT_API_TOKEN", None) or os.environ.get("TGSTAT_API_TOKEN")

    discovery = SmartChannelDiscovery(
        session_manager=session_manager,
        tgstat_token=tgstat_token,
    )

    async with async_session() as session:
        async with session.begin():
            results = await discovery.discover(
                keywords=payload.keywords,
                methods=payload.methods,
                max_results=payload.max_results,
                min_members=payload.min_members,
                require_comments=payload.require_comments,
                language_filter=payload.language_filter,
                db_session=session,
            )

            saved = 0
            if payload.save_to_catalog and results:
                saved = await discovery.save_to_catalog(
                    channels=results,
                    session=session,
                    tenant_id=None,  # platform catalog
                )

    return {
        "discovered": len(results),
        "saved_to_catalog": saved,
        "methods_used": payload.methods or discovery._available_methods(),
        "channels": [
            {
                "username": ch.username,
                "title": ch.title,
                "member_count": ch.member_count,
                "has_comments": ch.has_comments,
                "language": ch.language,
                "source_method": ch.source_method,
            }
            for ch in results[:100]  # first 100 in response
        ],
    }


@app.get("/v1/channel-map/export")
async def channel_map_export(
    category: Optional[str] = Query(default=None),
    language: Optional[str] = Query(default=None),
    region: Optional[str] = Query(default=None),
    min_members: Optional[int] = Query(default=None, ge=0),
    max_members: Optional[int] = Query(default=None),
    has_comments: Optional[bool] = Query(default=None),
    min_spam_score: Optional[float] = Query(default=None, ge=0.0, le=10.0),
    max_spam_score: Optional[float] = Query(default=None, ge=0.0, le=10.0),
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Export filtered channels as JSON.

    Supports all standard channel-map filters plus spam_score range and source.
    Returns up to 5000 rows per request.
    """
    from sqlalchemy import or_

    tf = _channel_map_tenant_filter(tenant_context.tenant_id)
    q = select(ChannelMapEntry).where(tf)

    if category is not None:
        q = q.where(ChannelMapEntry.category == category)
    if language is not None:
        q = q.where(ChannelMapEntry.language == language)
    if region is not None:
        q = q.where(ChannelMapEntry.region == region)
    if min_members is not None:
        q = q.where(ChannelMapEntry.member_count >= min_members)
    if max_members is not None:
        q = q.where(ChannelMapEntry.member_count <= max_members)
    if has_comments is not None:
        q = q.where(ChannelMapEntry.comments_enabled == has_comments)
    if min_spam_score is not None:
        q = q.where(ChannelMapEntry.spam_score >= min_spam_score)
    if max_spam_score is not None:
        q = q.where(ChannelMapEntry.spam_score <= max_spam_score)
    if source is not None:
        q = q.where(ChannelMapEntry.source == source)

    q = q.order_by(ChannelMapEntry.member_count.desc()).limit(limit)
    rows = (await session.execute(q)).scalars().all()

    def _serialize_export(entry: ChannelMapEntry) -> dict[str, Any]:
        base = _serialize_channel_map_entry(entry)
        base["topic_tags"] = getattr(entry, "topic_tags", None)
        base["spam_score"] = getattr(entry, "spam_score", None)
        base["last_refreshed_at"] = (
            entry.last_refreshed_at.isoformat()
            if getattr(entry, "last_refreshed_at", None)
            else None
        )
        return base

    return {
        "total": len(rows),
        "limit": limit,
        "filters": {
            "category": category,
            "language": language,
            "region": region,
            "min_members": min_members,
            "max_members": max_members,
            "has_comments": has_comments,
            "min_spam_score": min_spam_score,
            "max_spam_score": max_spam_score,
            "source": source,
        },
        "items": [_serialize_export(r) for r in rows],
    }


# ---------------------------------------------------------------------------
# Channel Map — micro-topic classification endpoints
# ---------------------------------------------------------------------------

_CATEGORY_TREE_CACHE_KEY = "channel_map:category_tree:{tenant_id}"
_CATEGORY_TREE_CACHE_TTL = 300  # 5 minutes


@app.get("/v1/channel-map/category-tree")
async def channel_map_category_tree(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Return hierarchical category → subcategory → micro_topics tree.

    Result is cached in Redis for 5 minutes keyed by tenant_id.
    """
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    cache_key = _CATEGORY_TREE_CACHE_KEY.format(tenant_id=tenant_context.tenant_id)

    # Try Redis cache first
    try:
        cached = await task_queue.cache_get(cache_key)
        if cached:
            import json as _json
            return _json.loads(cached)
    except Exception:
        pass  # Redis unavailable — proceed without cache

    tf = _channel_map_tenant_filter(tenant_context.tenant_id)

    # Total channel count
    total_channels: int = (
        await session.execute(
            select(func.count()).select_from(ChannelMapEntry).where(tf)
        )
    ).scalar_one()

    # Group by category + subcategory
    cat_sub_rows = (
        await session.execute(
            select(
                ChannelMapEntry.category,
                ChannelMapEntry.subcategory,
                func.count(ChannelMapEntry.id).label("cnt"),
            )
            .where(tf, ChannelMapEntry.category.isnot(None))
            .group_by(ChannelMapEntry.category, ChannelMapEntry.subcategory)
            .order_by(ChannelMapEntry.category, func.count(ChannelMapEntry.id).desc())
        )
    ).all()

    # Collect topic_tags per (category, subcategory) — scan rows with tags
    tagged_rows = (
        await session.execute(
            select(ChannelMapEntry.category, ChannelMapEntry.subcategory, ChannelMapEntry.topic_tags)
            .where(tf, ChannelMapEntry.category.isnot(None), ChannelMapEntry.topic_tags.isnot(None))
            .limit(10000)
        )
    ).all()

    # Build topic index
    from collections import defaultdict
    topic_index: dict[tuple, set] = defaultdict(set)
    for cat, sub, tags in tagged_rows:
        if tags and isinstance(tags, list):
            key = (cat, sub or "")
            for t in tags:
                if isinstance(t, str) and t:
                    topic_index[key].add(t)

    # Assemble category tree
    cat_map: dict[str, dict] = {}
    for cat, sub, cnt in cat_sub_rows:
        if cat not in cat_map:
            cat_map[cat] = {"name": cat, "count": 0, "subcategories": {}}
        cat_map[cat]["count"] += cnt
        sub_key = sub or ""
        if sub_key not in cat_map[cat]["subcategories"]:
            cat_map[cat]["subcategories"][sub_key] = {
                "name": sub or None,
                "count": 0,
                "micro_topics": [],
            }
        cat_map[cat]["subcategories"][sub_key]["count"] += cnt
        topics = sorted(topic_index.get((cat, sub_key), set()))
        cat_map[cat]["subcategories"][sub_key]["micro_topics"] = topics

    # Flatten subcategory dicts
    categories = []
    all_micro_topics: set = set()
    for cat_data in sorted(cat_map.values(), key=lambda x: -x["count"]):
        subcats = sorted(cat_data["subcategories"].values(), key=lambda x: -x["count"])
        for s in subcats:
            all_micro_topics.update(s["micro_topics"])
        categories.append({
            "name": cat_data["name"],
            "count": cat_data["count"],
            "subcategories": subcats,
        })

    response: dict[str, Any] = {
        "categories": categories,
        "total_channels": total_channels,
        "total_categories": len(categories),
        "total_micro_topics": len(all_micro_topics),
    }

    # Cache result
    try:
        import json as _json
        await task_queue.cache_set(cache_key, _json.dumps(response), ex=_CATEGORY_TREE_CACHE_TTL)
    except Exception:
        pass

    return response


class ChannelClassifySinglePayload(BaseModel):
    """Payload for POST /v1/channel-map/{id}/classify — no body required but kept for extensibility."""
    pass


class ChannelClassifyBatchPayload(BaseModel):
    """Payload for POST /v1/channel-map/batch-classify."""
    channel_ids: list[int] = Field(..., min_length=1, max_length=20)


@app.post("/v1/channel-map/{channel_id}/classify")
async def channel_map_classify_single(
    channel_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Trigger AI micro-topic classification for a single channel.

    Runs synchronously and returns the classification result.
    Updates category, subcategory, topic_tags on the channel record.
    """
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=30, window_seconds=60)

    from core.channel_intelligence import classify_channel_topics

    # Verify the channel is visible to this tenant before classifying
    tf = _channel_map_tenant_filter(tenant_context.tenant_id)
    exists = (
        await session.execute(
            select(func.count()).select_from(ChannelMapEntry).where(
                tf, ChannelMapEntry.id == channel_id
            )
        )
    ).scalar_one()
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="channel_not_found",
        )

    result = await classify_channel_topics(
        session,
        channel_id=channel_id,
        tenant_id=tenant_context.tenant_id,
    )

    # Invalidate the category-tree cache for this tenant
    try:
        cache_key = _CATEGORY_TREE_CACHE_KEY.format(tenant_id=tenant_context.tenant_id)
        await task_queue.cache_delete(cache_key)
    except Exception:
        pass

    return result


@app.post("/v1/channel-map/batch-classify", status_code=status.HTTP_202_ACCEPTED)
async def channel_map_batch_classify(
    payload: ChannelClassifyBatchPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Enqueue batch AI classification for up to 20 channels.

    All channel_ids must be visible to the current tenant (platform catalog
    rows with NULL tenant_id are also accessible).
    Returns a job_id for polling.
    """
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=10, window_seconds=60)

    if len(payload.channel_ids) > 20:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="maximum 20 channel_ids per batch-classify request",
        )

    # Validate all requested ids are visible to this tenant
    tf = _channel_map_tenant_filter(tenant_context.tenant_id)
    visible_ids_rows = (
        await session.execute(
            select(ChannelMapEntry.id).where(
                tf, ChannelMapEntry.id.in_(payload.channel_ids)
            )
        )
    ).scalars().all()
    visible_ids = set(visible_ids_rows)
    invisible = [cid for cid in payload.channel_ids if cid not in visible_ids]
    if invisible:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"channel_ids not found or not accessible: {invisible}",
        )

    result = await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_CHANNEL_CLASSIFY_BATCH,
        payload={"channel_ids": list(payload.channel_ids)},
    )
    return result


# ---------------------------------------------------------------------------
# Channel Map — Detail panel endpoints (CM Sprint)
# ---------------------------------------------------------------------------


@app.get("/v1/channel-map/{id}")
async def channel_map_detail(
    id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Return full channel detail by id (tenant-scoped via RLS + tenant filter)."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)

    tf = _channel_map_tenant_filter(tenant_context.tenant_id)
    row = (
        await session.execute(
            select(ChannelMapEntry).where(tf, ChannelMapEntry.id == id)
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="channel_not_found",
        )
    data = _serialize_channel_map_entry(row)
    # Include extra detail fields not in the list serializer
    data["topic_tags"] = getattr(row, "topic_tags", None)
    data["spam_score"] = getattr(row, "spam_score", None)
    data["last_refreshed_at"] = row.last_refreshed_at.isoformat() if getattr(row, "last_refreshed_at", None) else None
    return data


@app.get("/v1/channel-map/{id}/similar")
async def channel_map_similar(
    id: int,
    limit: int = Query(5, ge=1, le=20),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Find similar channels by category, language, and member_count proximity."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=30, window_seconds=60)

    from sqlalchemy import case, literal_column

    tf = _channel_map_tenant_filter(tenant_context.tenant_id)

    # 1. Load the source channel
    source = (
        await session.execute(
            select(ChannelMapEntry).where(tf, ChannelMapEntry.id == id)
        )
    ).scalars().first()
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="channel_not_found",
        )

    src_category = source.category
    src_language = source.language
    src_members = source.member_count or 0

    # 2. Build scoring expression
    score_parts: list = []
    if src_category:
        score_parts.append(
            case((ChannelMapEntry.category == src_category, 3), else_=0)
        )
    if src_language:
        score_parts.append(
            case((ChannelMapEntry.language == src_language, 2), else_=0)
        )
    if src_members > 0:
        low = int(src_members * 0.2)
        high = int(src_members * 5)
        score_parts.append(
            case(
                (
                    ChannelMapEntry.member_count.between(low, high),
                    1,
                ),
                else_=0,
            )
        )

    if score_parts:
        total_score = score_parts[0]
        for part in score_parts[1:]:
            total_score = total_score + part
    else:
        total_score = literal_column("0")

    score_label = total_score.label("sim_score")

    # 3. Query similar channels, excluding the source
    q = (
        select(ChannelMapEntry, score_label)
        .where(tf, ChannelMapEntry.id != id)
        .order_by(score_label.desc(), ChannelMapEntry.member_count.desc())
        .limit(limit)
    )
    rows = (await session.execute(q)).all()
    items = [_serialize_channel_map_entry(row[0]) for row in rows]
    return {"items": items, "total": len(items)}


_ALLOWED_BULK_ACTIONS = {"add_to_farm", "blacklist", "add_to_db", "track"}


class ChannelMapBulkActionPayload(BaseModel):
    action: str
    channel_ids: list[int]


@app.post("/v1/channel-map/bulk-action")
async def channel_map_bulk_action(
    payload: ChannelMapBulkActionPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Apply a bulk action to selected channel-map entries (stub — wired in CM-4)."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=10, window_seconds=60)

    if payload.action not in _ALLOWED_BULK_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid action, allowed: {sorted(_ALLOWED_BULK_ACTIONS)}",
        )
    if len(payload.channel_ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="maximum 100 channel_ids per bulk-action request",
        )
    if len(payload.channel_ids) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="channel_ids must not be empty",
        )

    # Verify all channel IDs are visible to this tenant
    tf = _channel_map_tenant_filter(tenant_context.tenant_id)
    visible_rows = (
        await session.execute(
            select(ChannelMapEntry.id).where(
                tf, ChannelMapEntry.id.in_(payload.channel_ids)
            )
        )
    ).scalars().all()
    visible_ids = set(visible_rows)
    invalid_ids = [cid for cid in payload.channel_ids if cid not in visible_ids]
    if invalid_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"channel_ids not found or not accessible: {invalid_ids[:10]}",
        )

    log.info(
        "channel_map_bulk_action tenant=%s action=%s count=%d ids=%s",
        tenant_context.tenant_id,
        payload.action,
        len(payload.channel_ids),
        payload.channel_ids[:10],
    )

    return {"status": "ok", "action": payload.action, "count": len(payload.channel_ids)}


# ---------------------------------------------------------------------------
# Channel Map v3 — viewport + cluster endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/channel-map/viewport")
async def channel_map_viewport(
    sw_lat: float = Query(..., ge=-90, le=90),
    sw_lng: float = Query(..., ge=-180, le=180),
    ne_lat: float = Query(..., ge=-90, le=90),
    ne_lng: float = Query(..., ge=-180, le=180),
    category: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    min_members: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Return top channels within a bounding box (for viewport list)."""
    _check_rate_limit("api", str(tenant_context.tenant_id))
    tf = _channel_map_tenant_filter(tenant_context.tenant_id)
    q = (
        select(ChannelMapEntry)
        .where(
            tf,
            ChannelMapEntry.lat.isnot(None),
            ChannelMapEntry.lng.isnot(None),
            ChannelMapEntry.lat >= sw_lat,
            ChannelMapEntry.lat <= ne_lat,
            ChannelMapEntry.lng >= sw_lng,
            ChannelMapEntry.lng <= ne_lng,
            ChannelMapEntry.member_count >= min_members,
        )
        .order_by(ChannelMapEntry.member_count.desc())
        .limit(limit)
    )
    if category:
        q = q.where(ChannelMapEntry.category == category)
    if language:
        q = q.where(ChannelMapEntry.language == language)
    rows = (await session.execute(q)).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "title": r.title,
                "username": r.username,
                "category": r.category,
                "member_count": r.member_count,
                "engagement_rate": r.engagement_rate,
                "language": r.language,
                "lat": r.lat,
                "lng": r.lng,
            }
            for r in rows
        ],
    }


@app.get("/v1/channel-map/clusters")
async def channel_map_clusters(
    zoom: int = Query(0, ge=0, le=6),
    category: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Server-side cluster aggregation for the globe at a given zoom level.

    Grid-based: divides lat/lng into cells whose size depends on zoom.
    Returns at most 100 clusters (zoom 0-2) or 300 (zoom 3+).
    """
    import math

    _check_rate_limit("api", str(tenant_context.tenant_id))

    # Cell size in degrees: zoom 0 → 30°, zoom 1 → 20°, zoom 2 → 10°, zoom 3 → 5°, zoom 4 → 2°
    cell_sizes = {0: 30, 1: 20, 2: 10, 3: 5, 4: 2, 5: 1, 6: 0.5}
    cell_size = cell_sizes.get(zoom, 0.5)
    max_clusters = 100 if zoom <= 2 else 300

    tf = _channel_map_tenant_filter(tenant_context.tenant_id)

    # SQL-side grid aggregation — no full table scan into Python memory
    cell_lat_expr = func.floor(ChannelMapEntry.lat / cell_size)
    cell_lng_expr = func.floor(ChannelMapEntry.lng / cell_size)

    base_where = [tf, ChannelMapEntry.lat.isnot(None), ChannelMapEntry.lng.isnot(None)]
    if category:
        base_where.append(ChannelMapEntry.category == category)
    if language:
        base_where.append(ChannelMapEntry.language == language)

    q = (
        select(
            func.avg(ChannelMapEntry.lat).label("avg_lat"),
            func.avg(ChannelMapEntry.lng).label("avg_lng"),
            func.count().label("cnt"),
            func.avg(ChannelMapEntry.member_count).label("avg_members"),
            cell_lat_expr.label("cell_lat"),
            cell_lng_expr.label("cell_lng"),
        )
        .where(*base_where)
        .group_by(cell_lat_expr, cell_lng_expr)
        .order_by(func.count().desc())
        .limit(max_clusters)
    )
    rows = (await session.execute(q)).all()

    # Dominant category per cell — separate grouped query
    dom_q = (
        select(
            func.floor(ChannelMapEntry.lat / cell_size).label("cell_lat"),
            func.floor(ChannelMapEntry.lng / cell_size).label("cell_lng"),
            ChannelMapEntry.category,
            func.count().label("cat_cnt"),
        )
        .where(*base_where, ChannelMapEntry.category.isnot(None))
        .group_by(text("1"), text("2"), ChannelMapEntry.category)
    )
    dom_rows = (await session.execute(dom_q)).all()

    cat_best: dict[tuple[int, int], str] = {}
    cat_max: dict[tuple[int, int], int] = {}
    for r in dom_rows:
        key = (int(r.cell_lat), int(r.cell_lng))
        if r.cat_cnt > cat_max.get(key, 0):
            cat_max[key] = r.cat_cnt
            cat_best[key] = r.category

    total_q = select(func.count()).where(*base_where)
    total_channels = (await session.execute(total_q)).scalar() or 0

    result = []
    for r in rows:
        key = (int(r.cell_lat), int(r.cell_lng))
        result.append({
            "lat": round(float(r.avg_lat), 4),
            "lng": round(float(r.avg_lng), 4),
            "count": r.cnt,
            "dominant_category": cat_best.get(key),
            "avg_members": int(r.avg_members) if r.avg_members else 0,
        })

    return {"clusters": result, "zoom": zoom, "total_channels": total_channels}


# ---------------------------------------------------------------------------
# Sprint 8 — Campaign endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/campaigns")
async def campaigns_list(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    q = (
        select(Campaign)
        .where(Campaign.tenant_id == tenant_context.tenant_id)
        .order_by(Campaign.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await session.execute(q)).scalars().all()
    count_q = select(func.count(Campaign.id)).where(Campaign.tenant_id == tenant_context.tenant_id)
    total = (await session.execute(count_q)).scalar_one_or_none() or 0
    return {"items": [_serialize_campaign(c) for c in rows], "total": total}


@app.post("/v1/campaigns", status_code=status.HTTP_201_CREATED)
async def campaigns_create(
    payload: CampaignCreatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    if payload.campaign_type not in VALID_CAMPAIGN_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid_campaign_type: must be one of {sorted(VALID_CAMPAIGN_TYPES)}",
        )
    campaign = Campaign(
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        name=payload.name,
        campaign_type=payload.campaign_type,
        account_ids=payload.account_ids,
        channel_database_id=payload.channel_database_id,
        comment_prompt=payload.comment_prompt,
        comment_tone=payload.comment_tone,
        comment_language=payload.comment_language,
        schedule_type=payload.schedule_type,
        schedule_config=payload.schedule_config,
        budget_daily_actions=payload.budget_daily_actions,
        budget_total_actions=payload.budget_total_actions,
    )
    session.add(campaign)
    await session.flush()
    return _serialize_campaign(campaign)


@app.get("/v1/campaigns/{campaign_id}")
async def campaigns_get(
    campaign_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    campaign = (
        await session.execute(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    runs = (
        await session.execute(
            select(CampaignRun)
            .where(
                CampaignRun.campaign_id == campaign_id,
                CampaignRun.tenant_id == tenant_context.tenant_id,
            )
            .order_by(CampaignRun.started_at.desc())
            .limit(10)
        )
    ).scalars().all()
    result = _serialize_campaign(campaign)
    result["recent_runs"] = [_serialize_campaign_run(r) for r in runs]
    return result


@app.put("/v1/campaigns/{campaign_id}")
async def campaigns_update(
    campaign_id: int,
    payload: CampaignUpdatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    campaign = (
        await session.execute(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    if campaign.status not in ("draft", "paused"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="campaign_not_editable: only draft/paused campaigns can be updated",
        )
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(campaign, field, value)
    campaign.updated_at = utcnow()
    return _serialize_campaign(campaign)


@app.delete("/v1/campaigns/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def campaigns_delete(
    campaign_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> None:
    campaign = (
        await session.execute(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    if campaign.status not in ("draft", "archived"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="campaign_active: only draft/archived campaigns can be deleted",
        )
    await session.delete(campaign)


@app.post("/v1/campaigns/{campaign_id}/start")
async def campaigns_start(
    campaign_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    campaign = (
        await session.execute(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    if campaign.status == "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="campaign_already_active")
    campaign.status = "active"
    campaign.started_at = campaign.started_at or utcnow()
    campaign.updated_at = utcnow()
    run = CampaignRun(
        campaign_id=campaign_id,
        tenant_id=tenant_context.tenant_id,
        status="running",
        started_at=utcnow(),
    )
    session.add(run)
    await session.flush()
    await _enqueue_within_session(
        session=session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_CAMPAIGN_START,
        payload={"campaign_id": campaign_id, "run_id": int(run.id)},
    )
    return {"campaign_id": campaign_id, "status": "active", "run_id": int(run.id)}


@app.post("/v1/campaigns/{campaign_id}/pause")
async def campaigns_pause(
    campaign_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    campaign = (
        await session.execute(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    if campaign.status != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="campaign_not_active")
    campaign.status = "paused"
    campaign.updated_at = utcnow()
    return {"campaign_id": campaign_id, "status": "paused"}


@app.post("/v1/campaigns/{campaign_id}/resume")
async def campaigns_resume(
    campaign_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    campaign = (
        await session.execute(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    if campaign.status != "paused":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="campaign_not_paused")
    campaign.status = "active"
    campaign.updated_at = utcnow()
    run = CampaignRun(
        campaign_id=campaign_id,
        tenant_id=tenant_context.tenant_id,
        status="running",
        started_at=utcnow(),
    )
    session.add(run)
    await session.flush()
    await _enqueue_within_session(
        session=session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_CAMPAIGN_START,
        payload={"campaign_id": campaign_id, "run_id": int(run.id)},
    )
    return {"campaign_id": campaign_id, "status": "active", "run_id": int(run.id)}


@app.post("/v1/campaigns/{campaign_id}/stop")
async def campaigns_stop(
    campaign_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    campaign = (
        await session.execute(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_context.tenant_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    campaign.status = "completed"
    campaign.completed_at = utcnow()
    campaign.updated_at = utcnow()
    await _enqueue_within_session(
        session=session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_CAMPAIGN_STOP,
        payload={"campaign_id": campaign_id},
    )
    return {"status": "completed"}


@app.get("/v1/campaigns/{campaign_id}/runs")
async def campaigns_runs(
    campaign_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    campaign = (
        await session.execute(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    runs = (
        await session.execute(
            select(CampaignRun)
            .where(
                CampaignRun.campaign_id == campaign_id,
                CampaignRun.tenant_id == tenant_context.tenant_id,
            )
            .order_by(CampaignRun.started_at.desc())
            .limit(100)
        )
    ).scalars().all()
    return {"items": [_serialize_campaign_run(r) for r in runs], "total": len(runs)}


@app.get("/v1/campaigns/{campaign_id}/analytics")
async def campaigns_analytics(
    campaign_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    campaign = (
        await session.execute(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign_not_found")
    runs_count = (
        await session.execute(
            select(func.count()).select_from(CampaignRun).where(
                CampaignRun.campaign_id == campaign_id,
                CampaignRun.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one()
    return {
        "campaign_id": campaign_id,
        "name": campaign.name,
        "status": campaign.status,
        "total_actions_performed": campaign.total_actions_performed,
        "total_comments_sent": campaign.total_comments_sent,
        "total_reactions_sent": campaign.total_reactions_sent,
        "runs_count": runs_count,
        "budget_daily_actions": campaign.budget_daily_actions,
        "budget_total_actions": campaign.budget_total_actions,
    }


# ---------------------------------------------------------------------------
# Sprint 8 — Analytics endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/analytics/dashboard")
async def analytics_dashboard(
    days: int = Query(default=7, ge=1, le=90),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    from datetime import timedelta
    from sqlalchemy import or_

    workspace_id = tenant_context.workspace_id if tenant_context.workspace_id is not None else 0
    since = utcnow() - timedelta(days=days)

    base_filter = [
        AnalyticsEvent.tenant_id == tenant_context.tenant_id,
        AnalyticsEvent.workspace_id == workspace_id,
        AnalyticsEvent.created_at >= since,
    ]

    # Aggregated totals by event type
    event_counts_result = await session.execute(
        select(AnalyticsEvent.event_type, func.count(AnalyticsEvent.id).label("cnt"))
        .where(*base_filter)
        .group_by(AnalyticsEvent.event_type)
    )
    event_counts: dict[str, int] = {row.event_type: row.cnt for row in event_counts_result.all()}

    # Daily breakdown
    daily_result = await session.execute(
        select(
            func.date(AnalyticsEvent.created_at).label("day"),
            AnalyticsEvent.event_type,
            func.count(AnalyticsEvent.id).label("cnt"),
        )
        .where(*base_filter)
        .group_by(func.date(AnalyticsEvent.created_at), AnalyticsEvent.event_type)
        .order_by(func.date(AnalyticsEvent.created_at))
    )
    daily_raw: dict[str, dict[str, int]] = {}
    for row in daily_result.all():
        day_str = str(row.day)
        if day_str not in daily_raw:
            daily_raw[day_str] = {"comments": 0, "reactions": 0, "errors": 0}
        if row.event_type == "comment_sent":
            daily_raw[day_str]["comments"] += row.cnt
        elif row.event_type == "reaction_sent":
            daily_raw[day_str]["reactions"] += row.cnt
        elif row.event_type in ("flood_wait", "spam_block", "account_frozen"):
            daily_raw[day_str]["errors"] += row.cnt
    daily_breakdown = [{"date": d, **v} for d, v in sorted(daily_raw.items())]

    # Top channels
    chan_result = await session.execute(
        select(
            AnalyticsEvent.channel_username,
            func.count(AnalyticsEvent.id).label("total_actions"),
        )
        .where(*base_filter, AnalyticsEvent.channel_username.isnot(None))
        .group_by(AnalyticsEvent.channel_username)
        .order_by(func.count(AnalyticsEvent.id).desc())
        .limit(10)
    )
    top_channels = [
        {"channel": row.channel_username, "actions": row.total_actions, "success_rate": 1.0}
        for row in chan_result.all()
    ]

    # Account activity
    acc_result = await session.execute(
        select(
            AnalyticsEvent.account_id,
            func.count(AnalyticsEvent.id).label("total_actions"),
        )
        .where(*base_filter, AnalyticsEvent.account_id.isnot(None))
        .group_by(AnalyticsEvent.account_id)
        .order_by(func.count(AnalyticsEvent.id).desc())
        .limit(20)
    )
    account_activity = [
        {"account_id": row.account_id, "phone": None, "actions": row.total_actions, "health_score": 1.0}
        for row in acc_result.all()
    ]

    active_campaigns = (
        await session.execute(
            select(func.count()).select_from(Campaign).where(
                Campaign.tenant_id == tenant_context.tenant_id,
                Campaign.status == "active",
            )
        )
    ).scalar_one()

    recent_events_rows = (
        await session.execute(
            select(AnalyticsEvent)
            .where(
                AnalyticsEvent.tenant_id == tenant_context.tenant_id,
                AnalyticsEvent.created_at >= since,
            )
            .order_by(AnalyticsEvent.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    return {
        "days": days,
        "total_comments": event_counts.get("comment_sent", 0),
        "total_reactions": event_counts.get("reaction_sent", 0),
        "total_flood_waits": event_counts.get("flood_wait", 0),
        "total_spam_blocks": event_counts.get("spam_block", 0),
        "active_campaigns": active_campaigns,
        "recent_events": [_serialize_analytics_event(e) for e in recent_events_rows],
        "daily_breakdown": daily_breakdown,
        "top_channels": top_channels,
        "account_activity": account_activity,
    }


@app.get("/v1/analytics/roi")
async def analytics_roi(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    total_campaigns = (
        await session.execute(
            select(func.count()).select_from(Campaign).where(
                Campaign.tenant_id == tenant_context.tenant_id
            )
        )
    ).scalar_one()
    active_campaigns = (
        await session.execute(
            select(func.count()).select_from(Campaign).where(
                Campaign.tenant_id == tenant_context.tenant_id,
                Campaign.status == "active",
            )
        )
    ).scalar_one()
    total_actions = (
        await session.execute(
            select(func.coalesce(func.sum(Campaign.total_actions_performed), 0)).where(
                Campaign.tenant_id == tenant_context.tenant_id
            )
        )
    ).scalar_one()
    total_comments = (
        await session.execute(
            select(func.coalesce(func.sum(Campaign.total_comments_sent), 0)).where(
                Campaign.tenant_id == tenant_context.tenant_id
            )
        )
    ).scalar_one()
    return {
        "total_campaigns": total_campaigns,
        "active_campaigns": active_campaigns,
        "total_actions_performed": int(total_actions),
        "total_comments_sent": int(total_comments),
    }


# ---------------------------------------------------------------------------
# Billing & Plans endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/plans")
async def plans_list(request: Request) -> dict[str, Any]:
    """Public endpoint — list all active plans."""
    _check_rate_limit("public", _client_ip(request), max_calls=30, window_seconds=60)
    from storage.models import Plan
    async with async_session() as session:
        rows = (await session.execute(
            select(Plan).where(Plan.is_active == True).order_by(Plan.sort_order)
        )).scalars().all()
        return {"items": [
            {
                "id": p.id, "slug": p.slug, "name": p.name,
                "price_monthly_rub": p.price_monthly_rub,
                "price_yearly_rub": p.price_yearly_rub,
                "max_accounts": p.max_accounts, "max_channels": p.max_channels,
                "max_comments_per_day": p.max_comments_per_day,
                "max_campaigns": p.max_campaigns,
                "features": p.features or {},
            }
            for p in rows
        ]}


@app.get("/v1/billing/subscription")
async def billing_subscription(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Get current tenant subscription."""
    from storage.models import Plan, Subscription
    row = (await session.execute(
        select(Subscription)
        .where(Subscription.tenant_id == tenant_context.tenant_id)
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if not row:
        return {"subscription": None, "plan": None}
    plan = (await session.execute(
        select(Plan).where(Plan.id == row.plan_id)
    )).scalar_one_or_none()
    return {
        "subscription": {
            "id": row.id, "status": row.status,
            "trial_ends_at": row.trial_ends_at.isoformat() if row.trial_ends_at else None,
            "current_period_start": row.current_period_start.isoformat() if row.current_period_start else None,
            "current_period_end": row.current_period_end.isoformat() if row.current_period_end else None,
            "cancelled_at": row.cancelled_at.isoformat() if row.cancelled_at else None,
            "payment_provider": row.payment_provider,
        },
        "plan": {
            "id": plan.id, "slug": plan.slug, "name": plan.name,
            "price_monthly_rub": plan.price_monthly_rub,
            "features": plan.features or {},
        } if plan else None,
    }


# ---------------------------------------------------------------------------
# Smart Commenting Engine endpoints
# ---------------------------------------------------------------------------


class CommentingPreviewPayload(BaseModel):
    """Payload для предпросмотра комментария без публикации."""
    post_text: str
    channel_title: str = ""
    channel_username: str = ""
    channel_category: str = ""
    existing_comments: list[str] = []
    tone: str = "positive"
    language: str = "auto"
    frequency: str = "all"
    keywords: list[str] = []
    min_existing_comments: int = 1
    custom_prompt: str = ""


@app.post("/v1/commenting/preview")
async def commenting_preview(
    payload: CommentingPreviewPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    """
    Предпросмотр комментария: анализирует пост, оценивает контекст
    и генерирует комментарий без реальной публикации в Telegram.

    Полезно для отладки и настройки конфигурации фермы.
    """
    from core.smart_commenter import build_orchestrator

    orchestrator = build_orchestrator(
        tenant_id=tenant_context.tenant_id,
        farm_id=0,           # preview не привязан к конкретной ферме
        thread_id=None,
        tone=payload.tone,
        language=payload.language,
        frequency=payload.frequency,
        keywords=payload.keywords,
        min_existing_comments=payload.min_existing_comments,
        custom_prompt=payload.custom_prompt,
    )

    channel_info = {
        "title": payload.channel_title,
        "username": payload.channel_username,
        "category": payload.channel_category,
    }

    result = await orchestrator.preview_comment(
        post_text=payload.post_text,
        channel_info=channel_info,
        existing_comments=payload.existing_comments,
    )
    return result


@app.get("/v1/commenting/strategies")
async def commenting_strategies(
    _: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    """
    Возвращает список доступных стратегий и тональностей для Smart Commenting Engine.
    """
    from core.smart_commenter import list_strategies, list_tones

    return {
        "strategies": list_strategies(),
        "tones": list_tones(),
    }


# ---------------------------------------------------------------------------
# Sprint 9 — Client Onboarding & Auto Campaign endpoints
# ---------------------------------------------------------------------------


class AnalyzeProductPayload(BaseModel):
    url: str = Field(min_length=1, max_length=2000)

    @field_validator("url", mode="before")
    @classmethod
    def normalize_url(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
        return value


class CampaignFromBriefPayload(BaseModel):
    brief_id: int = Field(gt=0)
    name: Optional[str] = Field(default=None, max_length=200)
    max_channels: int = Field(default=50, ge=1, le=200)
    max_accounts: int = Field(default=10, ge=1, le=50)


def _brief_to_dict(b: "ProductBrief") -> dict[str, Any]:
    return {
        "id": b.id,
        "tenant_id": b.tenant_id,
        "workspace_id": b.workspace_id,
        "url": b.url,
        "product_name": b.product_name,
        "target_audience": b.target_audience,
        "brand_tone": b.brand_tone,
        "usp": b.usp,
        "keywords": b.keywords or [],
        "suggested_styles": b.suggested_styles or [],
        "daily_volume": b.daily_volume,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


@app.post("/v1/campaigns/analyze-product", status_code=status.HTTP_201_CREATED)
async def campaigns_analyze_product(
    payload: AnalyzeProductPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Analyse a product URL and create a ProductBrief record."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    from core.product_analyzer import analyze_product_url

    brief = await analyze_product_url(
        session,
        url=payload.url,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
    )
    return _brief_to_dict(brief)


@app.get("/v1/campaigns/briefs")
async def campaigns_list_briefs(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """List all product briefs for the current tenant."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    q = (
        select(ProductBrief)
        .where(ProductBrief.tenant_id == tenant_context.tenant_id)
        .order_by(ProductBrief.id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(q)
    briefs = list(result.scalars().all())
    count_result = await session.execute(
        select(func.count(ProductBrief.id)).where(ProductBrief.tenant_id == tenant_context.tenant_id)
    )
    total = count_result.scalar_one_or_none() or 0
    return {
        "items": [_brief_to_dict(b) for b in briefs],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/v1/campaigns/briefs/{brief_id}")
async def campaigns_get_brief(
    brief_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Get a specific product brief."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    result = await session.execute(
        select(ProductBrief).where(
            ProductBrief.id == brief_id,
            ProductBrief.tenant_id == tenant_context.tenant_id,
        )
    )
    brief = result.scalar_one_or_none()
    if brief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="brief_not_found")
    return _brief_to_dict(brief)


@app.post("/v1/campaigns/from-brief", status_code=status.HTTP_201_CREATED)
async def campaigns_create_from_brief(
    payload: CampaignFromBriefPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Auto-create a campaign from a product brief (channels + accounts auto-selected)."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    from core.auto_campaign import AutoCampaignError, create_campaign_from_brief

    try:
        campaign = await create_campaign_from_brief(
            session,
            brief_id=payload.brief_id,
            tenant_id=tenant_context.tenant_id,
            workspace_id=tenant_context.workspace_id,
            user_id=tenant_context.user_id,
            campaign_name=payload.name,
            max_channels=payload.max_channels,
            max_accounts=payload.max_accounts,
        )
    except AutoCampaignError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "id": campaign.id,
        "name": campaign.name,
        "status": campaign.status,
        "brief_id": payload.brief_id,
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
    }


@app.get("/v1/campaigns/{campaign_id}/channels")
async def campaigns_get_channels(
    campaign_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """List channels assigned to a campaign."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    camp_result = await session.execute(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_context.tenant_id)
    )
    if camp_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign_not_found")

    q = (
        select(CampaignChannel)
        .where(
            CampaignChannel.campaign_id == campaign_id,
            CampaignChannel.tenant_id == tenant_context.tenant_id,
        )
        .order_by(CampaignChannel.id.asc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(q)
    rows = list(result.scalars().all())
    count_result = await session.execute(
        select(func.count(CampaignChannel.id)).where(
            CampaignChannel.campaign_id == campaign_id,
            CampaignChannel.tenant_id == tenant_context.tenant_id,
        )
    )
    total = count_result.scalar_one_or_none() or 0
    return {
        "items": [
            {
                "id": r.id,
                "campaign_id": r.campaign_id,
                "channel_id": r.channel_id,
                "channel_username": r.channel_username,
                "status": r.status,
                "comments_count": r.comments_count,
                "last_comment_at": r.last_comment_at.isoformat() if r.last_comment_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/v1/campaigns/{campaign_id}/comments")
async def campaigns_get_comments(
    campaign_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Return recent analytics events (comments) for a campaign."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    camp_result = await session.execute(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_context.tenant_id)
    )
    if camp_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign_not_found")

    q = (
        select(AnalyticsEvent)
        .where(
            AnalyticsEvent.tenant_id == tenant_context.tenant_id,
            AnalyticsEvent.campaign_id == campaign_id,
            AnalyticsEvent.event_type == "comment_sent",
        )
        .order_by(AnalyticsEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(q)
    rows = list(result.scalars().all())
    count_result = await session.execute(
        select(func.count(AnalyticsEvent.id)).where(
            AnalyticsEvent.tenant_id == tenant_context.tenant_id,
            AnalyticsEvent.campaign_id == campaign_id,
            AnalyticsEvent.event_type == "comment_sent",
        )
    )
    total = count_result.scalar_one_or_none() or 0
    return {
        "items": [
            {
                "id": r.id,
                "account_id": r.account_id,
                "channel_username": r.channel_username,
                "event_data": r.event_data or {},
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# Sprint 10 — Analytics & ROI Dashboard endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/analytics/daily")
async def analytics_daily(
    days: int = Query(default=30, ge=1, le=90),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Daily stats breakdown for the last *days* days."""
    from core.analytics_pipeline import get_daily_stats
    rows = await get_daily_stats(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id if tenant_context.workspace_id is not None else 0,
        days=days,
    )
    return {"days": days, "rows": rows}


@app.get("/v1/analytics/channels")
async def analytics_channels(
    days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=20, ge=1, le=100),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Channel comparison ranked by total actions."""
    from core.analytics_pipeline import get_channel_comparison
    rows = await get_channel_comparison(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id if tenant_context.workspace_id is not None else 0,
        days=days,
        limit=limit,
    )
    return {"days": days, "channels": rows}


@app.get("/v1/analytics/heatmap")
async def analytics_heatmap(
    days: int = Query(default=30, ge=1, le=90),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """7x24 hourly activity heatmap data."""
    from core.analytics_pipeline import get_heatmap_data
    rows = await get_heatmap_data(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id if tenant_context.workspace_id is not None else 0,
        days=days,
    )
    return {"days": days, "heatmap": rows}


@app.get("/v1/analytics/top-comments")
async def analytics_top_comments(
    days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=20, ge=1, le=50),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Top-performing comments by reactions count."""
    from core.analytics_pipeline import get_top_comments
    rows = await get_top_comments(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id if tenant_context.workspace_id is not None else 0,
        days=days,
        limit=limit,
    )
    return {"days": days, "comments": rows}


# ---- Weekly Reports -------------------------------------------------------


@app.get("/v1/reports/weekly")
async def weekly_reports_list(
    limit: int = Query(default=10, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """List weekly reports for the current tenant."""
    from core.weekly_report import list_weekly_reports
    reports = await list_weekly_reports(
        session,
        tenant_id=tenant_context.tenant_id,
        limit=limit,
        offset=offset,
    )
    items = [
        {
            "id": r.id,
            "week_start": r.week_start,
            "week_end": r.week_end,
            "report_text": r.report_text,
            "metrics_snapshot": r.metrics_snapshot,
            "generated_at": r.generated_at.isoformat() if r.generated_at else None,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reports
    ]
    return {"items": items, "total": len(items), "limit": limit, "offset": offset}


class GenerateWeeklyReportPayload(BaseModel):
    week_start: Optional[str] = None
    week_end: Optional[str] = None
    send_telegram: bool = True


@app.post("/v1/reports/weekly/generate", status_code=status.HTTP_201_CREATED)
async def weekly_report_generate(
    payload: GenerateWeeklyReportPayload = GenerateWeeklyReportPayload(),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Trigger generation of a weekly report for the current week."""
    from core.weekly_report import generate_weekly_report
    report = await generate_weekly_report(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id if tenant_context.workspace_id is not None else 0,
        send_telegram=payload.send_telegram,
        week_start=payload.week_start,
        week_end=payload.week_end,
    )
    return {
        "id": report.id,
        "week_start": report.week_start,
        "week_end": report.week_end,
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "sent_at": report.sent_at.isoformat() if report.sent_at else None,
    }


@app.get("/v1/reports/weekly/{report_id}")
async def weekly_report_get(
    report_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Get a specific weekly report."""
    from core.weekly_report import get_weekly_report
    report = await get_weekly_report(
        session,
        tenant_id=tenant_context.tenant_id,
        report_id=report_id,
    )
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report_not_found")
    return {
        "id": report.id,
        "week_start": report.week_start,
        "week_end": report.week_end,
        "report_text": report.report_text,
        "metrics_snapshot": report.metrics_snapshot,
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "sent_at": report.sent_at.isoformat() if report.sent_at else None,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }


# ---- Admin endpoints (OPS_API_TOKEN or JWT owner/admin) -------------------


@app.get("/v1/admin/platform-stats")
async def admin_platform_stats(
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    """Platform-wide aggregate statistics for the admin dashboard."""
    async with async_session() as session:
        async with session.begin():
            total_tenants = (await session.execute(
                select(func.count()).select_from(Tenant)
            )).scalar_one()

            total_accounts = (await session.execute(
                select(func.count()).select_from(Account)
            )).scalar_one()

            alive_accounts = (await session.execute(
                select(func.count()).select_from(Account).where(
                    Account.health_status == "alive"
                )
            )).scalar_one()

            banned_accounts = (await session.execute(
                select(func.count()).select_from(Account).where(
                    Account.health_status == "dead"
                )
            )).scalar_one()

            frozen_accounts = (await session.execute(
                select(func.count()).select_from(Account).where(
                    Account.status == "frozen"
                )
            )).scalar_one()

            from storage.models import Proxy as ProxyModel
            total_proxies = (await session.execute(
                select(func.count()).select_from(ProxyModel)
            )).scalar_one()

            alive_proxies = (await session.execute(
                select(func.count()).select_from(ProxyModel).where(
                    ProxyModel.health_status == "alive"
                )
            )).scalar_one()

            active_subscriptions = (await session.execute(
                select(func.count()).select_from(Subscription).where(
                    Subscription.status.in_(["active", "trial"])
                )
            )).scalar_one()

    return {
        "tenants": {"total": total_tenants},
        "accounts": {
            "total": total_accounts,
            "alive": alive_accounts,
            "banned": banned_accounts,
            "frozen": frozen_accounts,
            "other": total_accounts - alive_accounts - banned_accounts - frozen_accounts,
        },
        "proxies": {
            "total": total_proxies,
            "alive": alive_proxies,
            "dead": total_proxies - alive_proxies,
        },
        "subscriptions": {
            "active": active_subscriptions,
        },
    }


@app.get("/v1/admin/ai-spend")
async def admin_ai_spend(
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    """Platform-wide AI token usage and cost estimates."""
    from datetime import date as _date, timedelta as _td
    async with async_session() as session:
        async with session.begin():
            today_str = utcnow().date()
            month_start = today_str.replace(day=1)

            today_tokens_result = await session.execute(
                select(
                    func.coalesce(func.sum(AIRequest.prompt_tokens + AIRequest.completion_tokens), 0),
                    func.coalesce(func.sum(AIRequest.estimated_cost_usd), 0.0),
                ).where(
                    func.date(AIRequest.created_at) == today_str
                )
            )
            today_row = today_tokens_result.one()

            month_tokens_result = await session.execute(
                select(
                    func.coalesce(func.sum(AIRequest.prompt_tokens + AIRequest.completion_tokens), 0),
                    func.coalesce(func.sum(AIRequest.estimated_cost_usd), 0.0),
                ).where(
                    AIRequest.created_at >= month_start
                )
            )
            month_row = month_tokens_result.one()

    return {
        "today": {
            "tokens": int(today_row[0]),
            "estimated_cost_usd": float(today_row[1]),
        },
        "month": {
            "tokens": int(month_row[0]),
            "estimated_cost_usd": float(month_row[1]),
        },
    }


@app.get("/v1/admin/tenant-health")
async def admin_tenant_health(
    limit: int = Query(default=20, ge=1, le=100),
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    """Per-tenant health overview for the admin dashboard."""
    async with async_session() as session:
        async with session.begin():
            tenants_result = await session.execute(
                select(Tenant).order_by(Tenant.created_at.desc()).limit(limit)
            )
            tenants = tenants_result.scalars().all()

            items = []
            for t in tenants:
                account_count = (await session.execute(
                    select(func.count()).select_from(Account).where(
                        Account.tenant_id == t.id
                    )
                )).scalar_one()
                alive_count = (await session.execute(
                    select(func.count()).select_from(Account).where(
                        Account.tenant_id == t.id,
                        Account.health_status == "alive",
                    )
                )).scalar_one()
                items.append({
                    "tenant_id": t.id,
                    "name": t.name,
                    "slug": t.slug,
                    "status": t.status,
                    "accounts_total": account_count,
                    "accounts_alive": alive_count,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                })

    return {"items": items, "total": len(items)}


# ===========================================================================
# Sprint 11 — Self-Healing & Auto-Purchase API
# ===========================================================================

# Job type constants for Sprint 11 queues (mirrored from farm_jobs.py)
JOB_TYPE_HEALTH_SWEEP = "health_sweep"
JOB_TYPE_AUTO_PURCHASE = "auto_purchase"
QUEUE_HEALING = "healing_tasks"

# Register Sprint 11 queues in the farm job type → queue mapping.
_FARM_JOB_TYPE_TO_QUEUE[JOB_TYPE_HEALTH_SWEEP] = QUEUE_HEALING
_FARM_JOB_TYPE_TO_QUEUE[JOB_TYPE_AUTO_PURCHASE] = QUEUE_HEALING


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class PurchaseRequestCreatePayload(BaseModel):
    resource_type: str = Field(..., pattern="^(proxy|account)$")
    quantity: int = Field(..., ge=1, le=10000)
    provider_name: str = Field(..., min_length=1, max_length=100)
    estimated_cost_usd: Optional[float] = None
    details: Optional[dict[str, Any]] = None


class AlertConfigUpdatePayload(BaseModel):
    resource_type: str = Field(..., pattern="^(proxy|account)$")
    threshold_percent: int = Field(..., ge=1, le=99)
    auto_purchase_enabled: bool = False
    notify_telegram: bool = True
    notify_email: bool = False


# ---------------------------------------------------------------------------
# Self-Healing endpoints
# ---------------------------------------------------------------------------


@app.post("/v1/healing/sweep", status_code=status.HTTP_200_OK)
async def healing_sweep(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Trigger an immediate health sweep for the current tenant."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=10, window_seconds=60)
    result = await _enqueue_within_session(
        session=session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=tenant_context.workspace_id,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_HEALTH_SWEEP,
        payload={},
    )
    return result


@app.get("/v1/healing/log")
async def healing_log(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Return paginated self-healing action log for the current tenant."""
    rows = (
        await session.execute(
            select(HealingAction)
            .where(HealingAction.tenant_id == tenant_context.tenant_id)
            .order_by(HealingAction.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    total = (
        await session.execute(
            select(func.count(HealingAction.id)).where(
                HealingAction.tenant_id == tenant_context.tenant_id
            )
        )
    ).scalar_one()

    items = [
        {
            "id": int(r.id),
            "action_type": r.action_type,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "details": r.details or {},
            "outcome": r.outcome,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


@app.get("/v1/healing/predictions")
async def healing_predictions(
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    """Return resource depletion predictions for the current tenant."""
    from core.self_healing import SelfHealingEngine
    engine = SelfHealingEngine()
    return await engine.predict_resource_depletion(tenant_context.tenant_id)


# ---------------------------------------------------------------------------
# Auto-Purchase endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/purchases/requests")
async def list_purchase_requests(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """List all purchase requests for the current tenant."""
    rows = (
        await session.execute(
            select(PurchaseRequest)
            .where(PurchaseRequest.tenant_id == tenant_context.tenant_id)
            .order_by(PurchaseRequest.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    total = (
        await session.execute(
            select(func.count(PurchaseRequest.id)).where(
                PurchaseRequest.tenant_id == tenant_context.tenant_id
            )
        )
    ).scalar_one()

    items = [
        {
            "id": int(r.id),
            "resource_type": r.resource_type,
            "quantity": r.quantity,
            "provider_name": r.provider_name,
            "status": r.status,
            "requested_by": r.requested_by,
            "approved_by": r.approved_by,
            "estimated_cost_usd": r.estimated_cost_usd,
            "actual_cost_usd": r.actual_cost_usd,
            "details": r.details or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "approved_at": r.approved_at.isoformat() if r.approved_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in rows
    ]
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


@app.post("/v1/purchases/requests", status_code=status.HTTP_201_CREATED)
async def create_purchase_request(
    payload: PurchaseRequestCreatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Create a manual purchase request (pending admin approval)."""
    from core.auto_purchase import AutoPurchaseManager
    mgr = AutoPurchaseManager()
    req = await mgr.create_purchase_request(
        tenant_id=tenant_context.tenant_id,
        resource_type=payload.resource_type,
        quantity=payload.quantity,
        provider_name=payload.provider_name,
        requested_by=tenant_context.user_id,
        estimated_cost_usd=payload.estimated_cost_usd,
        details=payload.details,
        session=session,
    )
    return {
        "id": int(req.id),
        "resource_type": req.resource_type,
        "quantity": req.quantity,
        "provider_name": req.provider_name,
        "status": req.status,
        "created_at": req.created_at.isoformat() if req.created_at else None,
    }


@app.post("/v1/purchases/requests/{request_id}/approve")
async def approve_purchase_request(
    request_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Approve a pending purchase request."""
    from core.auto_purchase import AutoPurchaseManager
    mgr = AutoPurchaseManager()
    try:
        req = await mgr.approve_purchase(
            request_id=request_id,
            approved_by=tenant_context.user_id,
            session=session,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"id": int(req.id), "status": req.status, "approved_at": req.approved_at.isoformat() if req.approved_at else None}


@app.post("/v1/purchases/requests/{request_id}/reject")
async def reject_purchase_request(
    request_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Reject a pending purchase request."""
    from core.auto_purchase import AutoPurchaseManager
    mgr = AutoPurchaseManager()
    try:
        req = await mgr.reject_purchase(
            request_id=request_id,
            rejected_by=tenant_context.user_id,
            session=session,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"id": int(req.id), "status": req.status}


@app.get("/v1/purchases/providers")
async def list_purchase_providers(
    _: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    """List available provider names grouped by resource type."""
    from core.auto_purchase import list_providers
    return list_providers()


# ---------------------------------------------------------------------------
# Platform Health endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/platform/health")
async def platform_health_status(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Return overall platform health status (green / yellow / red)."""
    tenant_id = tenant_context.tenant_id

    acc_total = (
        await session.execute(
            select(func.count(Account.id)).where(Account.tenant_id == tenant_id)
        )
    ).scalar() or 0
    acc_alive = (
        await session.execute(
            select(func.count(Account.id)).where(
                Account.tenant_id == tenant_id,
                Account.status == "active",
            )
        )
    ).scalar() or 0

    px_total = (
        await session.execute(
            select(func.count(Proxy.id)).where(Proxy.tenant_id == tenant_id)
        )
    ).scalar() or 0
    px_alive = (
        await session.execute(
            select(func.count(Proxy.id)).where(
                Proxy.tenant_id == tenant_id,
                Proxy.health_status == "alive",
                Proxy.is_active == True,  # noqa: E712
            )
        )
    ).scalar() or 0

    threads_active = (
        await session.execute(
            select(func.count(FarmThread.id)).where(
                FarmThread.tenant_id == tenant_id,
                FarmThread.status.in_(["monitoring", "commenting", "subscribing"]),
            )
        )
    ).scalar() or 0

    unresolved_critical = (
        await session.execute(
            select(func.count(PlatformAlert.id)).where(
                PlatformAlert.tenant_id == tenant_id,
                PlatformAlert.severity == "critical",
                PlatformAlert.is_resolved == False,  # noqa: E712
            )
        )
    ).scalar() or 0

    unresolved_warnings = (
        await session.execute(
            select(func.count(PlatformAlert.id)).where(
                PlatformAlert.tenant_id == tenant_id,
                PlatformAlert.severity == "warning",
                PlatformAlert.is_resolved == False,  # noqa: E712
            )
        )
    ).scalar() or 0

    acc_pct = int(acc_alive * 100 / acc_total) if acc_total else 100
    px_pct = int(px_alive * 100 / px_total) if px_total else 100

    if unresolved_critical > 0 or acc_pct < 10 or px_pct < 10:
        overall = "red"
    elif unresolved_warnings > 0 or acc_pct < 30 or px_pct < 30:
        overall = "yellow"
    else:
        overall = "green"

    return {
        "overall": overall,
        "accounts": {
            "alive": int(acc_alive),
            "total": int(acc_total),
            "alive_percent": acc_pct,
        },
        "proxies": {
            "alive": int(px_alive),
            "total": int(px_total),
            "alive_percent": px_pct,
        },
        "threads_active": int(threads_active),
        "unresolved_critical_alerts": int(unresolved_critical),
        "unresolved_warning_alerts": int(unresolved_warnings),
    }


@app.get("/v1/platform/resources")
async def platform_resources(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Return resource gauges and a short depletion forecast."""
    from core.self_healing import SelfHealingEngine
    engine = SelfHealingEngine()
    predictions = await engine.predict_resource_depletion(tenant_context.tenant_id)
    return predictions


@app.get("/v1/platform/alerts")
async def platform_alerts(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_resolved: bool = Query(default=False),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Return active (and optionally resolved) platform alerts."""
    stmt = select(PlatformAlert).where(
        PlatformAlert.tenant_id == tenant_context.tenant_id
    )
    if not include_resolved:
        stmt = stmt.where(PlatformAlert.is_resolved == False)  # noqa: E712

    rows = (
        await session.execute(
            stmt.order_by(PlatformAlert.created_at.desc()).offset(offset).limit(limit)
        )
    ).scalars().all()

    total = (
        await session.execute(
            select(func.count(PlatformAlert.id)).where(
                PlatformAlert.tenant_id == tenant_context.tenant_id,
                *([] if include_resolved else [PlatformAlert.is_resolved == False]),  # noqa: E712
            )
        )
    ).scalar_one()

    items = [
        {
            "id": int(a.id),
            "alert_type": a.alert_type,
            "severity": a.severity,
            "message": a.message,
            "is_resolved": a.is_resolved,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        }
        for a in rows
    ]
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


@app.post("/v1/platform/alerts/configure")
async def configure_platform_alerts(
    payload: AlertConfigUpdatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Upsert alert threshold configuration for a resource type."""
    existing = (
        await session.execute(
            select(AlertConfig).where(
                AlertConfig.tenant_id == tenant_context.tenant_id,
                AlertConfig.resource_type == payload.resource_type,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        cfg = AlertConfig(
            tenant_id=tenant_context.tenant_id,
            resource_type=payload.resource_type,
            threshold_percent=payload.threshold_percent,
            auto_purchase_enabled=payload.auto_purchase_enabled,
            notify_telegram=payload.notify_telegram,
            notify_email=payload.notify_email,
        )
        session.add(cfg)
    else:
        existing.threshold_percent = payload.threshold_percent
        existing.auto_purchase_enabled = payload.auto_purchase_enabled
        existing.notify_telegram = payload.notify_telegram
        existing.notify_email = payload.notify_email
        cfg = existing

    await session.flush()
    return {
        "resource_type": cfg.resource_type,
        "threshold_percent": cfg.threshold_percent,
        "auto_purchase_enabled": cfg.auto_purchase_enabled,
        "notify_telegram": cfg.notify_telegram,
        "notify_email": cfg.notify_email,
    }


# ---------------------------------------------------------------------------
# Sprint 8 — Comment Quality Dashboard API
# ---------------------------------------------------------------------------


@app.get("/v1/comments/feed")
async def comments_feed(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    account_id: Optional[int] = Query(default=None),
    channel_username: Optional[str] = Query(default=None),
    style_name: Optional[str] = Query(default=None),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Лента последних A/B результатов комментариев с пагинацией."""
    stmt = (
        select(CommentABResult)
        .where(CommentABResult.tenant_id == tenant_context.tenant_id)
    )
    if account_id is not None:
        stmt = stmt.where(CommentABResult.account_id == account_id)
    if channel_username:
        stmt = stmt.where(CommentABResult.channel_username.ilike(f"%{_escape_like(channel_username)}%"))
    if style_name:
        stmt = stmt.where(CommentABResult.style_name == style_name)

    count_result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    total = int(count_result.scalar_one() or 0)

    rows = (
        await session.execute(
            stmt.order_by(CommentABResult.posted_at.desc().nullslast())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    items = [
        {
            "id": r.id,
            "style_name": r.style_name,
            "tone": r.tone,
            "channel_username": r.channel_username,
            "account_id": r.account_id,
            "reactions_count": r.reactions_count,
            "replies_count": r.replies_count,
            "was_deleted": r.was_deleted,
            "posted_at": r.posted_at.isoformat() if r.posted_at else None,
            "measured_at": r.measured_at.isoformat() if r.measured_at else None,
        }
        for r in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/v1/comments/stats")
async def comments_stats(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Агрегированная статистика: сегодня, неделя, месяц."""
    from datetime import timedelta
    from sqlalchemy import Integer as _Integer

    now = utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    month_start = today_start - timedelta(days=30)

    base_filter = CommentABResult.tenant_id == tenant_context.tenant_id

    async def _count(start: Any) -> int:
        res = await session.execute(
            select(func.count(CommentABResult.id))
            .where(base_filter, CommentABResult.posted_at >= start)
        )
        return int(res.scalar_one() or 0)

    async def _avg_reactions(start: Any) -> float:
        res = await session.execute(
            select(func.avg(CommentABResult.reactions_count))
            .where(base_filter, CommentABResult.posted_at >= start)
        )
        return round(float(res.scalar_one() or 0), 2)

    async def _ban_rate(start: Any) -> float:
        res = await session.execute(
            select(func.avg(func.cast(CommentABResult.was_deleted, _Integer)))
            .where(base_filter, CommentABResult.posted_at >= start)
        )
        return round(float(res.scalar_one() or 0), 3)

    today_count, week_count, month_count = (
        await _count(today_start),
        await _count(week_start),
        await _count(month_start),
    )
    today_avg_reactions = await _avg_reactions(today_start)
    week_ban_rate = await _ban_rate(week_start)

    # Top performing style this week by avg reactions
    top_row = (
        await session.execute(
            select(CommentABResult.style_name, func.avg(CommentABResult.reactions_count).label("avg_r"))
            .where(base_filter, CommentABResult.posted_at >= week_start)
            .group_by(CommentABResult.style_name)
            .order_by(func.avg(CommentABResult.reactions_count).desc())
            .limit(1)
        )
    ).one_or_none()
    top_style = top_row[0] if top_row else None

    return {
        "today": {"comments": today_count, "avg_reactions": today_avg_reactions},
        "week": {"comments": week_count, "ban_rate": week_ban_rate, "top_style": top_style},
        "month": {"comments": month_count},
    }


@app.get("/v1/comments/styles")
async def comments_ab_styles(
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    """Список стилей с A/B результатами."""
    from core.smart_commenter import get_ab_stats, list_styles

    available_styles = list_styles()
    ab_data = await get_ab_stats(tenant_context.tenant_id)
    ab_map = {row["style_name"]: row for row in ab_data}
    for style in available_styles:
        stats = ab_map.get(style["id"], {})
        style["ab_stats"] = {
            "total_comments": stats.get("total_comments", 0),
            "avg_reactions": stats.get("avg_reactions", 0.0),
            "avg_replies": stats.get("avg_replies", 0.0),
            "deletion_rate": stats.get("deletion_rate", 0.0),
        }
    return {"styles": available_styles, "ab_results": ab_data}


class CommentPreviewPayload(BaseModel):
    post_text: str = Field(min_length=1, max_length=5000)
    channel_title: str = Field(default="", max_length=300)
    channel_username: str = Field(default="", max_length=100)
    channel_category: str = Field(default="", max_length=100)
    existing_comments: list[str] = Field(default_factory=list)
    tone: str = Field(default="positive", max_length=50)
    style: str = Field(default="", max_length=50)
    language: str = Field(default="auto", max_length=10)


@app.post("/v1/comments/preview")
async def comments_preview(
    payload: CommentPreviewPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    """Предпросмотр комментария по стилю + тональности без публикации."""
    from core.smart_commenter import ALL_STYLES, build_orchestrator

    style_rotation = [payload.style] if payload.style in ALL_STYLES else []
    orchestrator = build_orchestrator(
        tenant_id=tenant_context.tenant_id,
        farm_id=0,
        thread_id=None,
        tone=payload.tone,
        language=payload.language,
        style_rotation=style_rotation,
    )
    channel_info = {
        "title": payload.channel_title,
        "username": payload.channel_username,
        "category": payload.channel_category,
    }
    result = await orchestrator.preview_comment(
        post_text=payload.post_text,
        channel_info=channel_info,
        existing_comments=payload.existing_comments,
    )
    return result


# ---------------------------------------------------------------------------
# Custom Comment Styles CRUD
# ---------------------------------------------------------------------------


class CustomStyleCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    system_prompt: str = Field(default="", max_length=8000)
    examples: list[str] = Field(default_factory=list, max_length=20)
    tone: str = Field(default="positive", max_length=50)
    workspace_id: Optional[int] = Field(default=None)


class CustomStyleUpdatePayload(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=2000)
    system_prompt: Optional[str] = Field(default=None, max_length=8000)
    examples: Optional[list[str]] = Field(default=None, max_length=20)
    tone: Optional[str] = Field(default=None, max_length=50)
    is_active: Optional[bool] = Field(default=None)


def _style_to_dict(s: "CommentStyleTemplate") -> dict:
    return {
        "id": s.id,
        "tenant_id": s.tenant_id,
        "workspace_id": s.workspace_id,
        "name": s.name,
        "description": s.description,
        "system_prompt": s.system_prompt,
        "examples": s.examples or [],
        "tone": s.tone,
        "is_active": s.is_active,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


@app.get("/v1/comments/custom-styles")
async def list_custom_styles(
    workspace_id: Optional[int] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """List tenant's custom comment styles."""
    stmt = select(CommentStyleTemplate).where(
        CommentStyleTemplate.tenant_id == tenant_context.tenant_id
    )
    if workspace_id is not None:
        stmt = stmt.where(CommentStyleTemplate.workspace_id == workspace_id)
    if is_active is not None:
        stmt = stmt.where(CommentStyleTemplate.is_active == is_active)

    count_result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    total = int(count_result.scalar_one() or 0)

    rows = (
        await session.execute(
            stmt.order_by(CommentStyleTemplate.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    return {"items": [_style_to_dict(r) for r in rows], "total": total}


@app.post("/v1/comments/custom-styles", status_code=201)
async def create_custom_style(
    payload: CustomStyleCreatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Create a new custom comment style for the tenant."""
    if payload.tone not in {"positive", "hater", "emotional", "expert", "witty", ""}:
        raise HTTPException(status_code=422, detail="invalid_tone")

    # Validate workspace belongs to the tenant if provided
    if payload.workspace_id is not None:
        ws_check = await session.execute(
            select(Workspace).where(
                Workspace.id == payload.workspace_id,
                Workspace.tenant_id == tenant_context.tenant_id,
            )
        )
        if ws_check.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="workspace_not_found")

    style = CommentStyleTemplate(
        tenant_id=tenant_context.tenant_id,
        workspace_id=payload.workspace_id,
        name=payload.name,
        description=payload.description or None,
        system_prompt=payload.system_prompt or None,
        examples=payload.examples if payload.examples else None,
        tone=payload.tone or None,
        is_active=True,
    )
    session.add(style)
    await session.flush()
    await session.refresh(style)
    return _style_to_dict(style)


@app.put("/v1/comments/custom-styles/{style_id}")
async def update_custom_style(
    style_id: int,
    payload: CustomStyleUpdatePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Update a custom comment style."""
    result = await session.execute(
        select(CommentStyleTemplate).where(
            CommentStyleTemplate.id == style_id,
            CommentStyleTemplate.tenant_id == tenant_context.tenant_id,
        )
    )
    style = result.scalar_one_or_none()
    if style is None:
        raise HTTPException(status_code=404, detail="style_not_found")

    if payload.name is not None:
        style.name = payload.name
    if payload.description is not None:
        style.description = payload.description
    if payload.system_prompt is not None:
        style.system_prompt = payload.system_prompt or None
    if payload.examples is not None:
        style.examples = payload.examples if payload.examples else None
    if payload.tone is not None:
        if payload.tone not in {"positive", "hater", "emotional", "expert", "witty", ""}:
            raise HTTPException(status_code=422, detail="invalid_tone")
        style.tone = payload.tone or None
    if payload.is_active is not None:
        style.is_active = payload.is_active

    style.updated_at = utcnow()
    await session.flush()
    await session.refresh(style)
    return _style_to_dict(style)


@app.delete("/v1/comments/custom-styles/{style_id}", status_code=204)
async def delete_custom_style(
    style_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> None:
    """Delete a custom comment style."""
    result = await session.execute(
        select(CommentStyleTemplate).where(
            CommentStyleTemplate.id == style_id,
            CommentStyleTemplate.tenant_id == tenant_context.tenant_id,
        )
    )
    style = result.scalar_one_or_none()
    if style is None:
        raise HTTPException(status_code=404, detail="style_not_found")
    await session.delete(style)


# ---------------------------------------------------------------------------
# Sprint 8 — Channel Intelligence v2 API
# ---------------------------------------------------------------------------


class ChannelRecommendationsPayload(BaseModel):
    niche_description: str = Field(min_length=1, max_length=2000)
    keywords: list[str] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=200)


@app.post("/v1/channels/recommendations")
async def channels_recommendations(
    payload: ChannelRecommendationsPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    """AI-рекомендации каналов по нише тенанта."""
    from core.ai_router import route_ai_task as _route_ai
    from core.channel_intelligence import ChannelMatcher
    try:
        from storage.sqlite_db import get_redis_client
        redis = await get_redis_client()
    except Exception:
        redis = None

    matcher = ChannelMatcher(redis_client=redis, ai_router_func=_route_ai)
    channels = await matcher.find_matching_channels(
        tenant_id=tenant_context.tenant_id,
        niche_description=payload.niche_description,
        keywords=payload.keywords,
        limit=payload.limit,
    )
    return {"channels": channels, "total": len(channels)}


@app.post("/v1/channels/blacklist/{channel_entry_id}")
async def channels_blacklist(
    channel_entry_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Вручную добавить канал в чёрный список."""
    entry = (
        await session.execute(
            select(ChannelEntry).where(
                ChannelEntry.id == channel_entry_id,
                ChannelEntry.tenant_id == tenant_context.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel_entry_not_found")
    entry.blacklisted = True
    return {"id": channel_entry_id, "blacklisted": True}


@app.get("/v1/channels/quality-scores")
async def channels_quality_scores(
    limit: int = Query(default=50, ge=1, le=200),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    """Рейтинг качества каналов по success_rate, банам, slow_mode."""
    from core.ai_router import route_ai_task as _route_ai
    from core.channel_intelligence import ChannelQualityScorer
    try:
        from storage.sqlite_db import get_redis_client
        redis = await get_redis_client()
    except Exception:
        redis = None

    scorer = ChannelQualityScorer(redis_client=redis, ai_router_func=_route_ai)
    rankings = await scorer.get_quality_rankings(
        tenant_id=tenant_context.tenant_id,
        limit=limit,
    )
    return {"channels": rankings, "total": len(rankings)}


@app.post("/v1/channels/refresh-scores")
async def channels_refresh_scores(
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    """Пересчёт quality_score для всех каналов тенанта."""
    _check_rate_limit("api", f"refresh_scores:{tenant_context.tenant_id}", max_calls=3, window_seconds=300)

    from core.ai_router import route_ai_task as _route_ai
    from core.channel_intelligence import ChannelQualityScorer
    try:
        from storage.sqlite_db import get_redis_client
        redis = await get_redis_client()
    except Exception:
        redis = None

    scorer = ChannelQualityScorer(redis_client=redis, ai_router_func=_route_ai)
    count = await scorer.score_all(tenant_id=tenant_context.tenant_id)
    return {"scored": count}



# ---------------------------------------------------------------------------
# Account Approval Gate — /v1/accounts/pending-review, approve, reject, bulk-approve
# ---------------------------------------------------------------------------


class RejectAccountPayload(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class BulkApprovePayload(BaseModel):
    account_ids: List[int] = Field(..., min_length=1, max_length=100)


@app.get("/v1/accounts/pending-review")
async def accounts_pending_review(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """List accounts currently in the gate_review lifecycle stage."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    # Count total for pagination header
    from sqlalchemy import func as sa_func
    count_result = await session.execute(
        select(sa_func.count(Account.id))
        .where(
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
            Account.lifecycle_stage == "gate_review",
        )
    )
    total = count_result.scalar() or 0

    # Paginated query
    accounts_result = await session.execute(
        select(Account)
        .where(
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
            Account.lifecycle_stage == "gate_review",
        )
        .order_by(Account.created_at.asc(), Account.id.asc())
        .limit(limit)
        .offset(offset)
    )
    page_accounts = list(accounts_result.scalars().all())

    account_ids = [int(a.id) for a in page_accounts]

    health_map: dict[int, AccountHealthScore] = {}
    if account_ids:
        hs_result = await session.execute(
            select(AccountHealthScore).where(
                AccountHealthScore.tenant_id == tenant_id,
                AccountHealthScore.account_id.in_(account_ids),
            )
        )
        for hs in hs_result.scalars().all():
            health_map[int(hs.account_id)] = hs

    warmup_map: dict[int, dict[str, int]] = {}
    if account_ids:
        ws_result = await session.execute(
            select(WarmupSession).where(
                WarmupSession.tenant_id == tenant_id,
                WarmupSession.account_id.in_(account_ids),
            )
        )
        for ws in ws_result.scalars().all():
            acc_id = int(ws.account_id)
            stats = warmup_map.setdefault(
                acc_id,
                {"sessions_total": 0, "sessions_completed": 0, "actions_performed": 0},
            )
            stats["sessions_total"] += 1
            if ws.status == "completed":
                stats["sessions_completed"] += 1
            stats["actions_performed"] += int(ws.actions_performed or 0)

    items = []
    for account in page_accounts:
        hs = health_map.get(int(account.id))
        warmup_stats = warmup_map.get(
            int(account.id),
            {"sessions_total": 0, "sessions_completed": 0, "actions_performed": 0},
        )
        items.append({
            "id": account.id,
            "phone": account.phone,
            "lifecycle_stage": account.lifecycle_stage,
            "health_status": account.health_status,
            "status": account.status,
            "health_score": hs.health_score if hs else None,
            "survivability_score": hs.survivability_score if hs else None,
            "warmup_sessions_completed": warmup_stats["sessions_completed"],
            "warmup_sessions_total": warmup_stats["sessions_total"],
            "warmup_actions_performed": warmup_stats["actions_performed"],
            "proxy_status": "bound" if account.proxy_id is not None else "unbound",
            "proxy_id": account.proxy_id,
            "manual_notes": account.manual_notes,
            "created_at": account.created_at.isoformat() if account.created_at else None,
            "last_active_at": account.last_active_at.isoformat() if account.last_active_at else None,
        })

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.post("/v1/accounts/{account_id}/approve")
async def account_approve(
    account_id: int,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Approve a gate_review account, advancing it to execution_ready."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    acct_result = await session.execute(
        select(Account).where(
            Account.id == account_id,
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
        )
    )
    account: Optional[Account] = acct_result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")

    if account.lifecycle_stage != "gate_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"account_not_in_gate_review (current: {account.lifecycle_stage})",
        )

    try:
        lifecycle = AccountLifecycle(session, tenant_id=tenant_id)
        transition_result = await lifecycle.transition(
            account_id,
            "execution_ready",
            reason="operator_approved",
            actor=f"operator:{tenant_context.user_id}",
        )
    except LifecycleTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return {
        "ok": True,
        "account_id": account_id,
        "new_stage": transition_result["new_stage"],
    }


@app.post("/v1/accounts/{account_id}/reject")
async def account_reject(
    account_id: int,
    payload: RejectAccountPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Reject a gate_review account, sending it back to warming_up."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    acct_result = await session.execute(
        select(Account).where(
            Account.id == account_id,
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
        )
    )
    account: Optional[Account] = acct_result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")

    if account.lifecycle_stage != "gate_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"account_not_in_gate_review (current: {account.lifecycle_stage})",
        )

    try:
        lifecycle = AccountLifecycle(session, tenant_id=tenant_id)
        transition_result = await lifecycle.transition(
            account_id,
            "warming_up",
            reason=f"operator_rejected: {payload.reason}",
            actor=f"operator:{tenant_context.user_id}",
        )
    except LifecycleTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return {
        "ok": True,
        "account_id": account_id,
        "new_stage": transition_result["new_stage"],
        "reason": payload.reason,
    }


@app.post("/v1/accounts/bulk-approve")
async def accounts_bulk_approve(
    payload: BulkApprovePayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    """Approve multiple gate_review accounts in a single request."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=60, window_seconds=60)
    tenant_id = tenant_context.tenant_id
    workspace_id = tenant_context.workspace_id

    if len(payload.account_ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="too_many_ids (max 100)",
        )

    bulk_result = await session.execute(
        select(Account).where(
            Account.id.in_(payload.account_ids),
            Account.tenant_id == tenant_id,
            Account.workspace_id == workspace_id,
        )
    )
    found_accounts = {int(a.id): a for a in bulk_result.scalars().all()}

    lifecycle = AccountLifecycle(session, tenant_id=tenant_id)
    approved_count = 0
    errors: list[dict[str, Any]] = []

    for aid in payload.account_ids:
        acct = found_accounts.get(aid)
        if acct is None:
            errors.append({"account_id": aid, "error": "not_found"})
            continue
        if acct.lifecycle_stage != "gate_review":
            errors.append({
                "account_id": aid,
                "error": f"not_in_gate_review (current: {acct.lifecycle_stage})",
            })
            continue
        try:
            await lifecycle.transition(
                aid,
                "execution_ready",
                reason="operator_approved_bulk",
                actor=f"operator:{tenant_context.user_id}",
            )
            approved_count += 1
        except (LifecycleTransitionError, ValueError) as exc:
            errors.append({"account_id": aid, "error": str(exc)})

    return {
        "ok": True,
        "approved_count": approved_count,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Session Topology endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/sessions/topology", response_model=TopologyAuditResponse)
async def get_session_topology(
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    """Run a full session topology audit for the current tenant."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=5, window_seconds=60)
    result = await session.execute(
        select(Account.user_id)
        .where(
            Account.tenant_id == tenant_context.tenant_id,
            Account.user_id.isnot(None),
        )
        .distinct()
    )
    known_user_ids = [row[0] for row in result.all()]
    base_dir = settings.sessions_path
    audit = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: audit_session_topology(base_dir, known_user_ids=known_user_ids),
    )
    # Strip absolute server paths — return relative paths only.
    base_str = str(base_dir)
    def _strip(v: Any) -> Any:
        if isinstance(v, str) and v.startswith(base_str):
            return v[len(base_str):].lstrip("/")
        if isinstance(v, list):
            return [_strip(i) for i in v]
        if isinstance(v, dict):
            return {k: _strip(val) for k, val in v.items()}
        return v
    return _strip(audit)


@app.get("/v1/sessions/topology/summary", response_model=TopologySummaryResponse)
async def get_session_topology_summary(
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    """Return only the summary statistics from a session topology audit."""
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=5, window_seconds=60)
    result = await session.execute(
        select(Account.user_id)
        .where(
            Account.tenant_id == tenant_context.tenant_id,
            Account.user_id.isnot(None),
        )
        .distinct()
    )
    known_user_ids = [row[0] for row in result.all()]
    base_dir = settings.sessions_path
    audit = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: audit_session_topology(base_dir, known_user_ids=known_user_ids),
    )
    return audit["summary"]


@app.post("/v1/sessions/quarantine", response_model=QuarantineResponse)
async def quarantine_sessions(
    payload: QuarantinePayload,
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    """Move non-canonical session copies into a quarantine folder.

    If phones list is empty, all eligible phones in the tenant scope are processed.
    Set dry_run=true (default) to preview without moving files.
    """
    _check_rate_limit("api", str(tenant_context.tenant_id), max_calls=5, window_seconds=60)
    result = await session.execute(
        select(Account.user_id)
        .where(
            Account.tenant_id == tenant_context.tenant_id,
            Account.user_id.isnot(None),
        )
        .distinct()
    )
    known_user_ids = [row[0] for row in result.all()]
    base_dir = settings.sessions_path
    phones = payload.phones if payload.phones else None
    quarantine_result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: quarantine_noncanonical_assets(
            base_dir,
            known_user_ids=known_user_ids,
            phones=phones,
            dry_run=payload.dry_run,
        ),
    )
    return quarantine_result


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


def _sanitize_validation_errors(errors: list[dict]) -> list[dict]:
    """Sanitize Pydantic v2 validation errors for JSON serialization.

    Pydantic v2 may include non-serializable objects (e.g. ValueError) in the
    ``ctx`` field of error dicts.  Convert them to plain strings so
    ``JSONResponse`` does not raise ``TypeError``.
    """
    sanitized: list[dict] = []
    for err in errors:
        clean = dict(err)
        ctx = clean.get("ctx")
        if isinstance(ctx, dict):
            clean["ctx"] = {
                k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                for k, v in ctx.items()
            }
        sanitized.append(clean)
    return sanitized


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = _sanitize_validation_errors(exc.errors())
    first = errors[0] if errors else {}
    field = ".".join(str(loc) for loc in first.get("loc", []))
    msg = first.get("msg", "validation_error")
    detail = f"{field}: {msg}" if field else msg
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": detail, "errors": errors},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(TelegramAuthError)
async def telegram_auth_exception_handler(_: Request, exc: TelegramAuthError) -> JSONResponse:
    log.warning("telegram auth rejected: %s", exc)
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})


@app.exception_handler(WebOnboardingError)
async def web_onboarding_exception_handler(_: Request, exc: WebOnboardingError) -> JSONResponse:
    log.warning("web onboarding rejected: %s", exc)
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})


@app.exception_handler(AssistantServiceError)
async def assistant_service_exception_handler(_: Request, exc: AssistantServiceError) -> JSONResponse:
    log.warning("assistant service rejected: %s", exc)
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unexpected_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unexpected ops_api exception: %s", exc)
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "internal_error"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
