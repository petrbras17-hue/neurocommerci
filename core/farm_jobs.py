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

QUEUE_FARM = "farm_tasks"
QUEUE_PARSER = "parser_tasks"
QUEUE_PROFILE = "profile_tasks"

FARM_QUEUE_NAMES = (QUEUE_FARM, QUEUE_PARSER, QUEUE_PROFILE)


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
