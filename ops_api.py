from __future__ import annotations

import asyncio
import collections
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
from pathlib import Path
import time
import traceback
from typing import Any, AsyncIterator, List, Optional

import jwt
import uvicorn
from fastapi import Cookie, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
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
    complete_profile,
    get_me_payload,
    get_team_payload,
    login_with_email,
    logout_web_session,
    refresh_web_session,
    register_with_email,
    verify_telegram_login,
)
from core.lead_funnel import LeadSnapshot, deliver_lead_funnel
from core.task_queue import task_queue
from storage.models import (
    Account,
    AccountHealthScore,
    AnalyticsEvent,
    AppJob,
    Campaign,
    CampaignRun,
    ChannelDatabase,
    ChannelEntry,
    ChannelMapEntry,
    ChattingConfig,
    DialogConfig,
    FarmConfig,
    FarmEvent,
    FarmThread,
    Lead,
    ParsingJob,
    ProfileTemplate,
    ReactionJob,
    TeamMember,
    TelegramFolder,
    Tenant,
    UserParsingResult,
    WarmupConfig,
    WarmupSession,
    Workspace,
)
from storage.sqlite_db import apply_session_rls_context, async_session, dispose_engine, init_db
from utils.helpers import utcnow


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
                job_obj = await session.get(AppJob, job_id)
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


def _check_rate_limit(scope: str, identifier: str, max_calls: int, window_seconds: int) -> None:
    """Raise HTTP 429 if the caller has exceeded max_calls within window_seconds."""
    if str(settings.APP_ENV or "").strip().lower() in {"development", "test", "testing"}:
        return
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
        return  # degrade gracefully: skip limiting rather than OOM
    bucket = _rate_limit_buckets[(scope, identifier)]
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= max_calls:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate_limit_exceeded",
        )
    bucket.append(now)


def _client_ip(request: Request) -> str:
    # Use direct connection IP — do not trust X-Forwarded-For to prevent spoofing.
    # If behind a trusted reverse proxy, configure proxy middleware instead.
    return request.client.host if request.client else "unknown"


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
    account_ids: List[int] = Field(min_length=1)
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
    channels: List[ChannelImportItem] = Field(min_length=1)


class ParserChannelsPayload(BaseModel):
    keywords: List[str] = Field(min_length=1)
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
    account_ids: List[int] = Field(min_length=1)
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
    target_channels: List[str] = Field(default_factory=list)

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
    target_channels: Optional[List[str]] = None


# Sprint 7 — advanced module payloads

class ReactionJobCreatePayload(BaseModel):
    channel_username: str = Field(min_length=1, max_length=200)
    reaction_type: str = Field(default="random", max_length=20)
    account_ids: List[int] = Field(min_length=1)
    post_id: Optional[int] = None


class ChattingConfigCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mode: str = Field(default="conservative", max_length=20)
    target_channels: List[str] = Field(default_factory=list)
    prompt_template: Optional[str] = None
    max_messages_per_hour: int = Field(default=5, ge=1, le=50)
    min_delay_seconds: int = Field(default=120, ge=30)
    max_delay_seconds: int = Field(default=600, ge=60)
    account_ids: List[int] = Field(default_factory=list)


class DialogConfigCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dialog_type: str = Field(default="warmup", max_length=30)
    account_pairs: List[List[int]] = Field(default_factory=list)
    prompt_template: Optional[str] = None
    messages_per_session: int = Field(default=5, ge=1, le=20)
    session_interval_hours: int = Field(default=4, ge=1, le=48)


class UserParsePayload(BaseModel):
    channel_username: str = Field(min_length=1, max_length=200)
    account_id: int


class FolderCreatePayload(BaseModel):
    account_id: int
    folder_name: str = Field(min_length=1, max_length=200)
    channel_usernames: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Sprint 8 Pydantic models
# ---------------------------------------------------------------------------

VALID_CAMPAIGN_TYPES = {"commenting", "reactions", "chatting", "mixed"}


class CampaignCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    campaign_type: str = Field(default="commenting", max_length=30)
    account_ids: List[int] = Field(default_factory=list)
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
    if token != settings.OPS_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_internal_token")


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
            tenant = await session.get(Tenant, tenant_context.tenant_id)
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
        yield
    finally:
        stop_event.set()
        for task in worker_tasks:
            task.cancel()
        if worker_tasks:
            await asyncio.gather(*worker_tasks, return_exceptions=True)
        await task_queue.close()
        await dispose_engine()


app = FastAPI(title="NEURO COMMENTING Ops API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.WEBAPP_DEV_ORIGIN, "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
if FRONTEND_ASSETS_DIR.exists():
    app.mount("/app/assets", StaticFiles(directory=str(FRONTEND_ASSETS_DIR)), name="app-assets")


def _page_context(request: Request, page: dict[str, object]) -> dict[str, object]:
    forwarded_proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).strip()
    base_url = f"{forwarded_proto}://{forwarded_host}".rstrip("/")
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
    forwarded_proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).strip()
    return {
        "bot_username": settings.ADMIN_BOT_USERNAME,
        "auth_domain": forwarded_host,
        "origin": f"{forwarded_proto}://{forwarded_host}",
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
        print(
            "telegram verify failed:\n" + traceback.format_exc(),
            flush=True,
        )
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
        print(
            "complete profile failed:\n" + traceback.format_exc(),
            flush=True,
        )
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
    refresh_token: Optional[str] = Cookie(default=None, alias=settings.WEBAPP_SESSION_COOKIE_NAME),
) -> JSONResponse:
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
        workspace_id=int(tenant_context.workspace_id or 0),
    )


@app.get("/v1/me/workspace", response_model=WorkspaceSummaryPayload)
async def me_workspace(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    payload = await get_me_payload(
        session,
        auth_user_id=tenant_context.user_id,
        tenant_id=tenant_context.tenant_id,
        workspace_id=int(tenant_context.workspace_id or 0),
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
    _: None = Depends(require_internal_token),
) -> dict[str, object]:
    async with async_session() as session:
        stmt = select(Account).order_by(Account.id)
        if tenant_id is not None:
            stmt = stmt.where(Account.tenant_id == tenant_id)
        rows = (await session.execute(stmt)).scalars().all()

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
    return {"items": items, "total": len(items)}


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
        await session.execute(select(Workspace).order_by(Workspace.id))
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
    session_bytes = await session_file.read()
    metadata_bytes = await metadata_file.read()
    if not session_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty_session_file")
    if not metadata_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty_metadata_file")
    return await save_uploaded_account_pair(
        session,
        tenant_id=tenant_context.tenant_id,
        workspace_id=int(tenant_context.workspace_id or 0),
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
        workspace_id=int(tenant_context.workspace_id or 0),
    )


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
        workspace_id=int(tenant_context.workspace_id or 0),
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
        workspace_id=int(tenant_context.workspace_id or 0),
        account_id=account_id,
    )


@app.post("/v1/assistant/start-brief")
async def assistant_start_brief(
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    return await enqueue_app_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
        workspace_id=int(tenant_context.workspace_id or 0),
    )


@app.post("/v1/context/confirm")
async def context_confirm(
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    return await enqueue_app_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
            workspace_id=int(tenant_context.workspace_id or 0) or None,
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
    async with async_session() as session:
        async with session.begin():
            return await list_internal_ai_audit(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                limit=limit,
            )



# Sprint 6 endpoints are at the bottom of this file, before exception handlers.


@app.get("/app", response_class=HTMLResponse)
async def app_shell_root() -> Response:
    if _frontend_ready():
        return FileResponse(FRONTEND_INDEX_PATH)
    return HTMLResponse(_frontend_unavailable_html(), status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


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
        workspace_id=int(tenant_context.workspace_id or 0),
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
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(FarmConfig)
            .where(
                FarmConfig.tenant_id == tenant_context.tenant_id,
                FarmConfig.workspace_id == int(tenant_context.workspace_id or 0),
            )
            .order_by(FarmConfig.id.desc())
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
            select(FarmThread).where(FarmThread.farm_id == farm_id).order_by(FarmThread.thread_index)
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
            )
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
            )
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

    # Load channels from database for distribution
    channel_entries = (
        await session.execute(
            select(ChannelEntry).where(
                ChannelEntry.database_id == payload.channel_database_id,
                ChannelEntry.tenant_id == tenant_context.tenant_id,
                ChannelEntry.blacklisted.is_(False),
            )
        )
    ).scalars().all()
    channel_usernames = [e.username for e in channel_entries if e.username]

    # Create or refresh farm threads (1 per account)
    threads_count = len(accounts)
    channels_per_thread = max(1, len(channel_usernames) // threads_count) if channel_usernames else 0

    # Remove old threads from a prior run
    old_threads = (
        await session.execute(
            select(FarmThread).where(FarmThread.farm_id == farm_id)
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
            )
        )
    ).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="farm_not_found")

    farm.status = "stopped"
    farm.updated_at = utcnow()

    job_result = await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
            )
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
            )
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
            ChannelDatabase.workspace_id == int(tenant_context.workspace_id or 0),
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
            select(ChannelEntry.username).where(ChannelEntry.database_id == db_id)
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
                ParsingJob.workspace_id == int(tenant_context.workspace_id or 0),
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
                ProfileTemplate.workspace_id == int(tenant_context.workspace_id or 0),
            )
            .order_by(ProfileTemplate.id.desc())
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
        workspace_id=int(tenant_context.workspace_id or 0),
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


def _serialize_warmup_session(s: WarmupSession, phone: str | None = None) -> dict[str, Any]:
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


def _serialize_health_score(h: AccountHealthScore, phone: str | None = None) -> dict[str, Any]:
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
            ).order_by(WarmupConfig.created_at.desc())
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
            )
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
            )
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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
            )
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
        workspace_id=int(tenant_context.workspace_id or 0) or None,
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


@app.post("/v1/health/recalculate")
async def health_recalculate(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    job_result = await _enqueue_farm_job(
        tenant_id=tenant_context.tenant_id,
        workspace_id=int(tenant_context.workspace_id or 0) or None,
        user_id=tenant_context.user_id,
        job_type=JOB_TYPE_HEALTH_RECALCULATE,
        payload={},
    )
    return {"status": "recalculating", "job_id": job_result["job_id"]}


# --- Quarantine ---


@app.get("/v1/health/quarantine")
async def quarantine_list(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    now = utcnow()
    rows = (
        await session.execute(
            select(
                FarmThread.account_id,
                Account.phone,
                FarmThread.stats_last_error,
                func.max(FarmThread.quarantine_until).label("quarantine_until"),
            )
            .outerjoin(Account, Account.id == FarmThread.account_id)
            .where(
                FarmThread.tenant_id == tenant_context.tenant_id,
                FarmThread.status == "quarantine",
                FarmThread.quarantine_until > now,
            )
            .group_by(FarmThread.account_id, Account.phone, FarmThread.stats_last_error)
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
    return {"items": items, "total": len(items)}


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
        workspace_id=int(tenant_context.workspace_id or 0),
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
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(ChattingConfig)
            .where(ChattingConfig.tenant_id == tenant_context.tenant_id)
            .order_by(ChattingConfig.id.desc())
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
            )
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
            )
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
            )
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
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(DialogConfig)
            .where(DialogConfig.tenant_id == tenant_context.tenant_id)
            .order_by(DialogConfig.id.desc())
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
            )
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
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(TelegramFolder)
            .where(TelegramFolder.tenant_id == tenant_context.tenant_id)
            .order_by(TelegramFolder.id.desc())
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
        workspace_id=int(tenant_context.workspace_id or 0),
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
        "category": entry.category,
        "subcategory": entry.subcategory,
        "language": entry.language,
        "member_count": entry.member_count,
        "has_comments": entry.has_comments,
        "avg_post_reach": entry.avg_post_reach,
        "engagement_rate": entry.engagement_rate,
        "last_indexed_at": entry.last_indexed_at.isoformat() if entry.last_indexed_at else None,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


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
    min_members: Optional[int] = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    q = select(ChannelMapEntry).where(ChannelMapEntry.tenant_id == tenant_context.tenant_id)
    if category is not None:
        q = q.where(ChannelMapEntry.category == category)
    if language is not None:
        q = q.where(ChannelMapEntry.language == language)
    if min_members is not None:
        q = q.where(ChannelMapEntry.member_count >= min_members)
    total_q = select(func.count()).select_from(ChannelMapEntry).where(
        ChannelMapEntry.tenant_id == tenant_context.tenant_id
    )
    total = (await session.execute(total_q)).scalar_one()
    q = q.order_by(ChannelMapEntry.member_count.desc()).offset(offset).limit(limit)
    rows = (await session.execute(q)).scalars().all()
    return {"items": [_serialize_channel_map_entry(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@app.post("/v1/channel-map/search")
async def channel_map_search(
    payload: ChannelMapSearchPayload,
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    from sqlalchemy import or_

    q = select(ChannelMapEntry).where(ChannelMapEntry.tenant_id == tenant_context.tenant_id)
    if payload.category is not None:
        q = q.where(ChannelMapEntry.category == payload.category)
    if payload.language is not None:
        q = q.where(ChannelMapEntry.language == payload.language)
    if payload.min_members is not None:
        q = q.where(ChannelMapEntry.member_count >= payload.min_members)
    if payload.query is not None:
        q = q.where(
            or_(
                ChannelMapEntry.username.ilike(f"%{payload.query}%"),
                ChannelMapEntry.title.ilike(f"%{payload.query}%"),
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

    rows = (
        await session.execute(
            select(distinct(ChannelMapEntry.category))
            .where(
                ChannelMapEntry.tenant_id == tenant_context.tenant_id,
                ChannelMapEntry.category.isnot(None),
            )
            .order_by(ChannelMapEntry.category)
        )
    ).scalars().all()
    return {"categories": list(rows)}


@app.get("/v1/channel-map/stats")
async def channel_map_stats(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    from sqlalchemy import distinct

    total = (
        await session.execute(
            select(func.count()).select_from(ChannelMapEntry).where(
                ChannelMapEntry.tenant_id == tenant_context.tenant_id
            )
        )
    ).scalar_one()

    cat_result = await session.execute(
        select(ChannelMapEntry.category, func.count(ChannelMapEntry.id))
        .where(
            ChannelMapEntry.tenant_id == tenant_context.tenant_id,
            ChannelMapEntry.category.isnot(None),
        )
        .group_by(ChannelMapEntry.category)
        .order_by(func.count(ChannelMapEntry.id).desc())
    )
    by_category = {row[0]: row[1] for row in cat_result.all()}

    lang_result = await session.execute(
        select(ChannelMapEntry.language, func.count(ChannelMapEntry.id))
        .where(
            ChannelMapEntry.tenant_id == tenant_context.tenant_id,
            ChannelMapEntry.language.isnot(None),
        )
        .group_by(ChannelMapEntry.language)
        .order_by(func.count(ChannelMapEntry.id).desc())
    )
    by_language = {row[0]: row[1] for row in lang_result.all()}

    return {
        "total_channels": total,
        "total": total,
        "by_category": by_category,
        "by_language": by_language,
    }


# ---------------------------------------------------------------------------
# Sprint 8 — Campaign endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/campaigns")
async def campaigns_list(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(Campaign)
            .where(Campaign.tenant_id == tenant_context.tenant_id)
            .order_by(Campaign.created_at.desc())
        )
    ).scalars().all()
    return {"items": [_serialize_campaign(c) for c in rows], "total": len(rows)}


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
        workspace_id=int(tenant_context.workspace_id or 0),
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
            )
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
            )
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
            )
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
            )
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
            )
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

    workspace_id = tenant_context.workspace_id or 0
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
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
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
    print("unexpected ops_api exception:\n" + traceback.format_exc(), flush=True)
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "internal_error"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
