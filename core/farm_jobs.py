"""
Farm Jobs — background job processor for farm, parser, and profile tasks.

Mirrors the assistant_jobs.py pattern: polls Redis queues, loads AppJob rows,
dispatches to the appropriate service, and updates job status.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from core.task_queue import task_queue
from storage.models import AppJob
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log


# Job types (must match ops_api.py constants)
JOB_TYPE_FARM_START = "farm_start"
JOB_TYPE_FARM_STOP = "farm_stop"
JOB_TYPE_FARM_PAUSE = "farm_pause"
JOB_TYPE_FARM_RESUME = "farm_resume"
JOB_TYPE_PARSER_CHANNELS = "parser_channels"
JOB_TYPE_PROFILE_GENERATE = "profile_generate"
JOB_TYPE_PROFILE_MASS_GENERATE = "profile_mass_generate"
JOB_TYPE_PROFILE_APPLY = "profile_apply"
JOB_TYPE_PROFILE_CREATE_CHANNEL = "profile_create_channel"

# Sprint 6
JOB_TYPE_WARMUP_START = "warmup_start"
JOB_TYPE_WARMUP_STOP = "warmup_stop"
JOB_TYPE_HEALTH_RECALCULATE = "health_recalculate"

# Sprint 7
JOB_TYPE_REACTION_RUN = "reaction_run"
JOB_TYPE_CHATTING_START = "chatting_start"
JOB_TYPE_CHATTING_STOP = "chatting_stop"
JOB_TYPE_DIALOG_START = "dialog_start"
JOB_TYPE_DIALOG_STOP = "dialog_stop"
JOB_TYPE_USER_PARSE = "user_parse"
JOB_TYPE_FOLDER_CREATE = "folder_create"
JOB_TYPE_FOLDER_DELETE = "folder_delete"

# Sprint 8
JOB_TYPE_CAMPAIGN_START = "campaign_start"
JOB_TYPE_CAMPAIGN_STOP = "campaign_stop"

# Sprint 7 Tasks 2+3 — Account lifecycle
JOB_TYPE_ACCOUNT_HEALTH_CHECK = "account_health_check"
JOB_TYPE_ACCOUNT_LIFECYCLE_MONITOR = "account_lifecycle_monitor"

# Sprint 11 — Self-Healing & Auto-Purchase
JOB_TYPE_HEALTH_SWEEP = "health_sweep"
JOB_TYPE_AUTO_PURCHASE = "auto_purchase"

# Channel classification (micro-topic taxonomy)
JOB_TYPE_CHANNEL_CLASSIFY_BATCH = "channel_classify_batch"

QUEUE_FARM = "farm_tasks"
QUEUE_PARSER = "parser_tasks"
QUEUE_PROFILE = "profile_tasks"
QUEUE_WARMUP = "warmup_tasks"
QUEUE_HEALTH = "health_tasks"
QUEUE_REACTIONS = "reaction_tasks"
QUEUE_CHATTING = "chatting_tasks"
QUEUE_DIALOGS = "dialog_tasks"
QUEUE_USER_PARSER = "user_parser_tasks"
QUEUE_FOLDERS = "folder_tasks"
QUEUE_CAMPAIGNS = "campaign_tasks"
QUEUE_ACCOUNT_LIFECYCLE = "account_lifecycle_tasks"
QUEUE_HEALING = "healing_tasks"

FARM_QUEUE_NAMES = (
    QUEUE_FARM, QUEUE_PARSER, QUEUE_PROFILE,
    QUEUE_WARMUP, QUEUE_HEALTH, QUEUE_REACTIONS,
    QUEUE_CHATTING, QUEUE_DIALOGS, QUEUE_USER_PARSER,
    QUEUE_FOLDERS, QUEUE_CAMPAIGNS, QUEUE_ACCOUNT_LIFECYCLE,
    QUEUE_HEALING,
)


class FarmJobError(RuntimeError):
    pass


async def _process_farm_job(job: AppJob) -> dict[str, Any]:
    """Dispatch a single farm job to the appropriate handler."""
    job_type = job.job_type
    payload = job.payload or {}
    tenant_id = job.tenant_id
    workspace_id = job.workspace_id

    if job_type in (JOB_TYPE_FARM_START, JOB_TYPE_FARM_STOP,
                    JOB_TYPE_FARM_PAUSE, JOB_TYPE_FARM_RESUME):
        return await _handle_farm_control(job_type, payload, tenant_id)

    if job_type == JOB_TYPE_PARSER_CHANNELS:
        return await _handle_parser_channels(payload, tenant_id, workspace_id)

    if job_type in (JOB_TYPE_PROFILE_GENERATE, JOB_TYPE_PROFILE_MASS_GENERATE):
        return await _handle_profile_generate(job_type, payload, tenant_id)

    if job_type == JOB_TYPE_PROFILE_APPLY:
        return await _handle_profile_apply(payload, tenant_id)

    if job_type == JOB_TYPE_PROFILE_CREATE_CHANNEL:
        return await _handle_profile_create_channel(payload, tenant_id)

    # Sprint 6 — warmup and health
    if job_type == JOB_TYPE_WARMUP_START:
        return await _handle_warmup_start(payload, tenant_id)
    if job_type == JOB_TYPE_WARMUP_STOP:
        return await _handle_warmup_stop(payload, tenant_id)
    if job_type == JOB_TYPE_HEALTH_RECALCULATE:
        return await _handle_health_recalculate(payload, tenant_id)

    # Sprint 7 — advanced modules
    if job_type == JOB_TYPE_REACTION_RUN:
        return await _handle_reaction_run(payload, tenant_id)
    if job_type in (JOB_TYPE_CHATTING_START, JOB_TYPE_CHATTING_STOP):
        return await _handle_chatting(job_type, payload, tenant_id)
    if job_type in (JOB_TYPE_DIALOG_START, JOB_TYPE_DIALOG_STOP):
        return await _handle_dialog(job_type, payload, tenant_id)
    if job_type == JOB_TYPE_USER_PARSE:
        return await _handle_user_parse(payload, tenant_id)
    if job_type in (JOB_TYPE_FOLDER_CREATE, JOB_TYPE_FOLDER_DELETE):
        return await _handle_folder(job_type, payload, tenant_id)

    # Sprint 8 — campaigns
    if job_type in (JOB_TYPE_CAMPAIGN_START, JOB_TYPE_CAMPAIGN_STOP):
        return await _handle_campaign(job_type, payload, tenant_id)

    # Sprint 7 Tasks 2+3 — account lifecycle
    if job_type == JOB_TYPE_ACCOUNT_HEALTH_CHECK:
        return await _handle_account_health_check(payload, tenant_id)
    if job_type == JOB_TYPE_ACCOUNT_LIFECYCLE_MONITOR:
        return await _handle_account_lifecycle_monitor(payload, tenant_id)

    # Sprint 11 — self-healing and auto-purchase
    if job_type == JOB_TYPE_HEALTH_SWEEP:
        return await _handle_health_sweep(payload, tenant_id)
    if job_type == JOB_TYPE_AUTO_PURCHASE:
        return await _handle_auto_purchase(payload, tenant_id)

    # Channel micro-topic classification
    if job_type == JOB_TYPE_CHANNEL_CLASSIFY_BATCH:
        return await _handle_channel_classify_batch(payload, tenant_id)

    raise FarmJobError(f"unsupported_job_type: {job_type}")


async def _handle_farm_control(
    job_type: str, payload: dict, tenant_id: int
) -> dict[str, Any]:
    """Handle farm start/stop/pause/resume jobs."""
    from core.farm_orchestrator import FarmOrchestrator
    from core.session_manager import SessionManager
    from core.task_queue import task_queue as tq
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        orchestrator = FarmOrchestrator(
            session_manager=sm, task_queue=tq, redis_client=redis_client
        )
        farm_id = payload["farm_id"]

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                if job_type == JOB_TYPE_FARM_START:
                    result = await orchestrator.start_farm(farm_id, session)
                elif job_type == JOB_TYPE_FARM_STOP:
                    result = await orchestrator.stop_farm(farm_id, session)
                elif job_type == JOB_TYPE_FARM_PAUSE:
                    await orchestrator.pause_farm(farm_id, session)
                    result = {"status": "paused"}
                elif job_type == JOB_TYPE_FARM_RESUME:
                    await orchestrator.resume_farm(farm_id, session)
                    result = {"status": "resumed"}
                else:
                    result = {"error": "unknown_farm_action"}
        return result
    finally:
        await redis_client.aclose()


async def _handle_parser_channels(
    payload: dict, tenant_id: int, workspace_id: int
) -> dict[str, Any]:
    """Handle channel parsing jobs."""
    from core.channel_parser_service import ChannelParserService
    from core.session_manager import SessionManager
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        parser = ChannelParserService(session_manager=sm, redis_client=redis_client)
        job_id = payload["job_id"]

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                await parser.run_parsing_job(job_id, session)
        return {"status": "completed", "job_id": job_id}
    finally:
        await redis_client.aclose()


async def _handle_profile_generate(
    job_type: str, payload: dict, tenant_id: int
) -> dict[str, Any]:
    """Handle profile generation jobs."""
    from core.profile_factory import ProfileFactory
    from core.session_manager import SessionManager
    from core.ai_router import route_ai_task
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        factory = ProfileFactory(
            session_manager=sm,
            ai_router_func=route_ai_task,
            redis_client=redis_client,
        )

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)

                if job_type == JOB_TYPE_PROFILE_GENERATE:
                    result = await factory.generate_profile(
                        template_id=payload["template_id"],
                        account_id=payload["account_id"],
                        tenant_id=tenant_id,
                        session=session,
                    )
                else:
                    result = await factory.mass_generate_profiles(
                        account_ids=payload["account_ids"],
                        template_id=payload["template_id"],
                        tenant_id=tenant_id,
                        session=session,
                    )
        return {"status": "completed", "result": result}
    finally:
        await redis_client.aclose()


async def _handle_profile_apply(
    payload: dict, tenant_id: int
) -> dict[str, Any]:
    """Handle profile apply jobs."""
    from core.profile_factory import ProfileFactory
    from core.session_manager import SessionManager
    from core.ai_router import route_ai_task
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        factory = ProfileFactory(
            session_manager=sm,
            ai_router_func=route_ai_task,
            redis_client=redis_client,
        )

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await factory.apply_profile(
                    account_id=payload["account_id"],
                    profile=payload["profile"],
                    tenant_id=tenant_id,
                    session=session,
                )
        return {"status": "completed", "result": result}
    finally:
        await redis_client.aclose()


async def _handle_profile_create_channel(
    payload: dict, tenant_id: int
) -> dict[str, Any]:
    """Handle channel creation + pinning jobs."""
    from core.profile_factory import ProfileFactory
    from core.session_manager import SessionManager
    from core.ai_router import route_ai_task
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        factory = ProfileFactory(
            session_manager=sm,
            ai_router_func=route_ai_task,
            redis_client=redis_client,
        )

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await factory.create_and_pin_channel(
                    account_id=payload["account_id"],
                    template=payload.get("template", {}),
                    tenant_id=tenant_id,
                    session=session,
                )
        return {"status": "completed", "result": result}
    finally:
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# Sprint 6 handlers — warmup and health
# ---------------------------------------------------------------------------

async def _handle_warmup_start(payload: dict, tenant_id: int) -> dict[str, Any]:
    from core.warmup_engine import WarmupEngine
    from core.session_manager import SessionManager
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        engine = WarmupEngine(session_manager=sm, redis_client=redis_client)
        config_id = payload["config_id"]

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                await engine.start(config_id, session)
        return {"status": "started", "config_id": config_id}
    finally:
        await redis_client.aclose()


async def _handle_warmup_stop(payload: dict, tenant_id: int) -> dict[str, Any]:
    from core.warmup_engine import WarmupEngine
    from core.session_manager import SessionManager
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        engine = WarmupEngine(session_manager=sm, redis_client=redis_client)
        config_id = payload["config_id"]

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                await engine.stop(config_id, session)
        return {"status": "stopped", "config_id": config_id}
    finally:
        await redis_client.aclose()


async def _handle_health_recalculate(payload: dict, tenant_id: int) -> dict[str, Any]:
    from core.health_scorer import HealthScorer

    scorer = HealthScorer()
    account_ids = payload.get("account_ids", [])

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=tenant_id)
            results = []
            for aid in account_ids:
                score = await scorer.calculate_and_upsert(aid, tenant_id, session)
                results.append({"account_id": aid, "health": score.get("health_score") if isinstance(score, dict) else None})
    return {"status": "completed", "results": results}


# ---------------------------------------------------------------------------
# Sprint 7 handlers — reactions, chatting, dialogs, user parser, folders
# ---------------------------------------------------------------------------

async def _handle_reaction_run(payload: dict, tenant_id: int) -> dict[str, Any]:
    from core.mass_reactions import MassReactionService
    from core.session_manager import SessionManager
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        svc = MassReactionService(session_manager=sm, redis_client=redis_client)
        job_id = payload["reaction_job_id"]

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                await svc.run_job(job_id, session)
        return {"status": "completed", "job_id": job_id}
    finally:
        await redis_client.aclose()


async def _handle_chatting(job_type: str, payload: dict, tenant_id: int) -> dict[str, Any]:
    from core.neuro_chatting import NeuroChatting
    from core.session_manager import SessionManager
    from core.ai_router import route_ai_task
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        chatting = NeuroChatting(
            session_manager=sm, ai_router_func=route_ai_task, redis_client=redis_client
        )
        config_id = payload["config_id"]

        if job_type == JOB_TYPE_CHATTING_START:
            async with async_session() as session:
                async with session.begin():
                    await apply_session_rls_context(session, tenant_id=tenant_id)
                    await chatting.start(config_id, session)
            return {"status": "started", "config_id": config_id}
        else:
            async with async_session() as session:
                async with session.begin():
                    await apply_session_rls_context(session, tenant_id=tenant_id)
                    await chatting.stop(config_id, session)
            return {"status": "stopped", "config_id": config_id}
    finally:
        await redis_client.aclose()


async def _handle_dialog(job_type: str, payload: dict, tenant_id: int) -> dict[str, Any]:
    from core.neuro_dialogs import NeuroDialogs
    from core.session_manager import SessionManager
    from core.ai_router import route_ai_task
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        dialogs = NeuroDialogs(
            session_manager=sm, ai_router_func=route_ai_task, redis_client=redis_client
        )
        config_id = payload["config_id"]

        if job_type == JOB_TYPE_DIALOG_START:
            async with async_session() as session:
                async with session.begin():
                    await apply_session_rls_context(session, tenant_id=tenant_id)
                    await dialogs.start(config_id, session)
            return {"status": "started", "config_id": config_id}
        else:
            async with async_session() as session:
                async with session.begin():
                    await apply_session_rls_context(session, tenant_id=tenant_id)
                    await dialogs.stop(config_id, session)
            return {"status": "stopped", "config_id": config_id}
    finally:
        await redis_client.aclose()


async def _handle_user_parse(payload: dict, tenant_id: int) -> dict[str, Any]:
    from core.user_parser import UserParser
    from core.session_manager import SessionManager
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        parser = UserParser(session_manager=sm, redis_client=redis_client)
        channel_username = payload["channel_username"]
        account_id = payload.get("account_id")

        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id)
                result = await parser.parse_channel_members(
                    channel_username=channel_username,
                    account_id=account_id,
                    tenant_id=tenant_id,
                    session=session,
                )
        return {"status": "completed", "result": result}
    finally:
        await redis_client.aclose()


async def _handle_folder(job_type: str, payload: dict, tenant_id: int) -> dict[str, Any]:
    from core.folder_manager import FolderManager
    from core.session_manager import SessionManager
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        fm = FolderManager(session_manager=sm, redis_client=redis_client)

        if job_type == JOB_TYPE_FOLDER_CREATE:
            async with async_session() as session:
                async with session.begin():
                    await apply_session_rls_context(session, tenant_id=tenant_id)
                    result = await fm.create_folder(
                        name=payload["name"],
                        channel_usernames=payload.get("channel_usernames", []),
                        account_id=payload["account_id"],
                        tenant_id=tenant_id,
                        session=session,
                    )
            return {"status": "completed", "result": result}
        else:
            folder_id = payload["folder_id"]
            async with async_session() as session:
                async with session.begin():
                    await apply_session_rls_context(session, tenant_id=tenant_id)
                    await fm.delete_folder(folder_id, tenant_id, session)
            return {"status": "deleted", "folder_id": folder_id}
    finally:
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# Sprint 8 handlers — campaigns
# ---------------------------------------------------------------------------

async def _handle_campaign(job_type: str, payload: dict, tenant_id: int) -> dict[str, Any]:
    from core.campaign_manager import CampaignManager
    from core.session_manager import SessionManager
    from core.ai_router import route_ai_task
    from config import settings
    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        sm = SessionManager(settings)
        cm = CampaignManager(
            session_manager=sm, ai_router_func=route_ai_task, redis_client=redis_client
        )
        campaign_id = payload["campaign_id"]

        if job_type == JOB_TYPE_CAMPAIGN_START:
            async with async_session() as session:
                async with session.begin():
                    await apply_session_rls_context(session, tenant_id=tenant_id)
                    await cm.start_campaign(campaign_id, session)
            return {"status": "started", "campaign_id": campaign_id}
        else:
            async with async_session() as session:
                async with session.begin():
                    await apply_session_rls_context(session, tenant_id=tenant_id)
                    await cm.stop_campaign(campaign_id, session)
            return {"status": "stopped", "campaign_id": campaign_id}
    finally:
        await redis_client.aclose()


async def _handle_account_health_check(payload: dict, tenant_id: int) -> dict[str, Any]:
    """
    Sprint 7 Task 2 — account health check job handler.

    For each account in warming_up / active_commenting / gate_review:
    - validates proxy liveness via ProxyManager.validate_proxy
    - updates health_status accordingly
    - transitions warming_up accounts to gate_review after 48 h
    - marks lifecycle_stage = "restricted" after 3 consecutive dead proxy checks
    """
    from datetime import timedelta

    from sqlalchemy import select as sa_select

    from config import settings
    from core.proxy_manager import ProxyManager, ProxyConfig
    from storage.models import Account

    warmup_threshold_hours: int = int(payload.get("warmup_threshold_hours", 48))
    consecutive_dead_threshold: int = int(payload.get("consecutive_dead_threshold", 3))

    proxy_manager = ProxyManager(settings=settings)
    updated: list[dict] = []

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=tenant_id)
            result = await session.execute(
                sa_select(Account).where(
                    Account.tenant_id == tenant_id,
                    Account.lifecycle_stage.in_(["warming_up", "active_commenting", "gate_review"]),
                )
            )
            accounts: list[Account] = list(result.scalars().all())

            for account in accounts:
                proxy_alive: bool | None = None

                if account.proxy_id is not None:
                    from storage.models import Proxy
                    proxy_row_result = await session.execute(
                        sa_select(Proxy).where(
                            Proxy.id == account.proxy_id,
                            Proxy.tenant_id == tenant_id,
                        )
                    )
                    proxy_row = proxy_row_result.scalar_one_or_none()
                    if proxy_row is not None:
                        proxy_cfg = ProxyConfig(
                            proxy_type=proxy_row.proxy_type or "socks5",
                            host=proxy_row.host,
                            port=int(proxy_row.port),
                            username=proxy_row.username,
                            password=proxy_row.password,
                        )
                        try:
                            proxy_alive = await proxy_manager.validate_proxy(proxy_cfg, timeout=10)
                        except Exception:
                            proxy_alive = False

                if proxy_alive is False:
                    # increment consecutive failure counter using restriction_reason field as marker
                    current_fails_str = account.restriction_reason or ""
                    if current_fails_str.startswith("proxy_dead_x"):
                        try:
                            fails = int(current_fails_str.replace("proxy_dead_x", "")) + 1
                        except ValueError:
                            fails = 1
                    else:
                        fails = 1
                    account.health_status = "dead"
                    account.last_health_check = utcnow()
                    if fails >= consecutive_dead_threshold:
                        account.lifecycle_stage = "restricted"
                        account.restriction_reason = "proxy_dead"
                    else:
                        account.restriction_reason = f"proxy_dead_x{fails}"
                    updated.append({"account_id": account.id, "health_status": "dead", "proxy_alive": False})

                elif proxy_alive is True:
                    # clear any dead marker
                    if account.health_status != "alive":
                        account.health_status = "alive"
                    if account.restriction_reason and account.restriction_reason.startswith("proxy_dead"):
                        account.restriction_reason = None
                    account.last_health_check = utcnow()

                    # transition warming_up → gate_review after threshold
                    if account.lifecycle_stage == "warming_up" and account.created_at is not None:
                        age_hours = (utcnow() - account.created_at).total_seconds() / 3600
                        if age_hours >= warmup_threshold_hours:
                            account.lifecycle_stage = "gate_review"
                    updated.append({"account_id": account.id, "health_status": "alive", "proxy_alive": True})

                else:
                    # No proxy attached — mark as unknown, still check warmup age transition
                    account.last_health_check = utcnow()
                    if account.lifecycle_stage == "warming_up" and account.created_at is not None:
                        age_hours = (utcnow() - account.created_at).total_seconds() / 3600
                        if age_hours >= warmup_threshold_hours:
                            account.lifecycle_stage = "gate_review"
                    updated.append({"account_id": account.id, "health_status": account.health_status, "proxy_alive": None})

    log.info("account_health_check: tenant=%s checked=%d", tenant_id, len(updated))
    return {"status": "completed", "tenant_id": tenant_id, "checked": len(updated), "results": updated}


async def _handle_account_lifecycle_monitor(payload: dict, tenant_id: int) -> dict[str, Any]:
    """
    Sprint 7 Task 3 — periodic account lifecycle monitor job handler.

    Runs as a cron-style periodic job (enqueued every 30 min by the scheduler).
    - frozen accounts with cooldown expired → lifecycle_stage reset to gate_review
    - warming_up beyond configured days → gate_review
    - accounts with consecutive_failures > threshold → quarantine
    Sends a digest notification if any transitions occurred.
    """
    from sqlalchemy import select as sa_select

    from config import settings
    from storage.models import Account
    from core.digest_service import digest_configured, send_digest_text

    warmup_max_days: int = int(payload.get("warmup_max_days", 7))
    consecutive_failure_threshold: int = int(payload.get("consecutive_failure_threshold", 5))

    transitions: list[dict] = []
    scanned_count = 0

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=tenant_id)
            result = await session.execute(
                sa_select(Account).where(Account.tenant_id == tenant_id)
            )
            all_accounts: list[Account] = list(result.scalars().all())
            scanned_count = len(all_accounts)
            now = utcnow()

            for account in all_accounts:
                prev_stage = account.lifecycle_stage

                # frozen with expired cooldown → gate_review
                if account.lifecycle_stage == "frozen" and account.cooldown_until is not None:
                    if now >= account.cooldown_until:
                        account.lifecycle_stage = "gate_review"
                        account.cooldown_until = None
                        transitions.append({
                            "account_id": account.id,
                            "from": "frozen",
                            "to": "gate_review",
                            "reason": "cooldown_expired",
                        })

                # warming_up too long → gate_review
                elif account.lifecycle_stage == "warming_up" and account.created_at is not None:
                    age_days = (now - account.created_at).total_seconds() / 86400
                    if age_days >= warmup_max_days:
                        account.lifecycle_stage = "gate_review"
                        transitions.append({
                            "account_id": account.id,
                            "from": "warming_up",
                            "to": "gate_review",
                            "reason": "warmup_duration_exceeded",
                        })

                # excessive consecutive failures → quarantine
                if (
                    account.lifecycle_stage not in ("restricted", "frozen", "quarantine")
                    and account.lifecycle_stage == prev_stage  # no transition yet this cycle
                ):
                    consec_str = account.restriction_reason or ""
                    if consec_str.startswith("proxy_dead_x"):
                        try:
                            fails = int(consec_str.replace("proxy_dead_x", ""))
                        except ValueError:
                            fails = 0
                        if fails >= consecutive_failure_threshold:
                            account.lifecycle_stage = "frozen"
                            account.restriction_reason = "quarantined_by_monitor"
                            from datetime import timedelta
                            account.quarantined_until = now + timedelta(hours=24)
                            transitions.append({
                                "account_id": account.id,
                                "from": prev_stage,
                                "to": "frozen",
                                "reason": "consecutive_failures_threshold",
                            })

    if transitions and digest_configured():
        lines = [f"Account lifecycle monitor — tenant {tenant_id}"]
        for t in transitions:
            lines.append(f"• account #{t['account_id']}: {t['from']} → {t['to']} ({t['reason']})")
        try:
            await send_digest_text("\n".join(lines))
        except Exception as exc:
            log.warning("account_lifecycle_monitor: digest send failed: %s", exc)

    log.info(
        "account_lifecycle_monitor: tenant=%s scanned=%d transitions=%d",
        tenant_id,
        scanned_count,
        len(transitions),
    )
    return {"status": "completed", "tenant_id": tenant_id, "transitions": len(transitions), "details": transitions}


# ---------------------------------------------------------------------------
# Sprint 11 handlers — self-healing and auto-purchase
# ---------------------------------------------------------------------------


async def _handle_health_sweep(payload: dict, tenant_id: int) -> dict[str, Any]:
    """Run the SelfHealingEngine health sweep for the tenant."""
    from core.self_healing import SelfHealingEngine

    engine = SelfHealingEngine()
    result = await engine.run_health_sweep(tenant_id)
    return {"status": "completed", "result": result}


async def _handle_auto_purchase(payload: dict, tenant_id: int) -> dict[str, Any]:
    """Run AutoPurchaseManager.check_resource_levels for the tenant."""
    from core.auto_purchase import AutoPurchaseManager

    mgr = AutoPurchaseManager()
    result = await mgr.check_resource_levels(tenant_id)
    return {"status": "completed", "result": result}


async def _handle_channel_classify_batch(payload: dict, tenant_id: int) -> dict[str, Any]:
    """Run classify_channels_batch for the given channel_ids."""
    from core.channel_intelligence import classify_channels_batch

    channel_ids: list[int] = list(payload.get("channel_ids") or [])
    if not channel_ids:
        return {"status": "completed", "results": [], "total": 0}

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=tenant_id)
            results = await classify_channels_batch(session, channel_ids, tenant_id)

    classified = sum(1 for r in results if not r.get("skipped"))
    return {
        "status": "completed",
        "total": len(results),
        "classified": classified,
        "skipped": len(results) - classified,
        "results": results,
    }


async def farm_worker_loop(poll_interval: float = 2.0) -> None:
    """
    Main worker loop for farm/parser/profile jobs.

    Polls all farm queue names, picks up queued jobs, processes them.
    Mirrors assistant_worker_loop() from core/assistant_jobs.py.
    """
    log.info("farm_worker_loop started, queues=%s", FARM_QUEUE_NAMES)
    while True:
        try:
            for queue_name in FARM_QUEUE_NAMES:
                raw = await task_queue.dequeue(queue_name, timeout=0)
                if not raw:
                    continue

                job_id = raw.get("job_id")
                if not job_id:
                    log.warning("farm_worker: dequeued item without job_id: %s", raw)
                    continue

                async with async_session() as session:
                    async with session.begin():
                        job = (
                            await session.execute(
                                select(AppJob).where(AppJob.id == int(job_id))
                            )
                        ).scalar_one_or_none()
                        if not job:
                            log.warning("farm_worker: job %s not found", job_id)
                            continue
                        if job.status != "queued":
                            log.info("farm_worker: job %s already %s", job_id, job.status)
                            continue

                        job.status = "running"
                        job.updated_at = utcnow()

                try:
                    result = await _process_farm_job(job)
                    async with async_session() as session:
                        async with session.begin():
                            j = (
                                await session.execute(
                                    select(AppJob).where(AppJob.id == int(job_id))
                                )
                            ).scalar_one_or_none()
                            if j:
                                j.status = "completed"
                                j.result = result
                                j.updated_at = utcnow()
                    log.info("farm_worker: job %s completed", job_id)

                except Exception as exc:
                    log.error("farm_worker: job %s failed: %s", job_id, exc, exc_info=True)
                    async with async_session() as session:
                        async with session.begin():
                            j = (
                                await session.execute(
                                    select(AppJob).where(AppJob.id == int(job_id))
                                )
                            ).scalar_one_or_none()
                            if j:
                                j.status = "failed"
                                j.result = {"error": str(exc)}
                                j.updated_at = utcnow()

        except asyncio.CancelledError:
            log.info("farm_worker_loop cancelled")
            break
        except Exception as exc:
            log.error("farm_worker_loop error: %s", exc, exc_info=True)

        await asyncio.sleep(poll_interval)
