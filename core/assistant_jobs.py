from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from core.assistant_service import (
    AssistantServiceError,
    confirm_context,
    generate_creative_draft,
    post_assistant_message,
    start_business_brief,
)
from core.task_queue import task_queue
from storage.models import AppJob
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log


JOB_TYPE_START_BRIEF = "assistant_start_brief"
JOB_TYPE_ASSISTANT_MESSAGE = "assistant_message"
JOB_TYPE_CONTEXT_CONFIRM = "context_confirm"
JOB_TYPE_CREATIVE_GENERATE = "creative_generate"

QUEUE_ASSISTANT = "assistant_tasks"
QUEUE_CONTEXT = "context_tasks"
QUEUE_CREATIVE = "creative_tasks"

JOB_TYPE_TO_QUEUE = {
    JOB_TYPE_START_BRIEF: QUEUE_ASSISTANT,
    JOB_TYPE_ASSISTANT_MESSAGE: QUEUE_ASSISTANT,
    JOB_TYPE_CONTEXT_CONFIRM: QUEUE_CONTEXT,
    JOB_TYPE_CREATIVE_GENERATE: QUEUE_CREATIVE,
}

ASSISTANT_QUEUE_NAMES = (QUEUE_ASSISTANT, QUEUE_CONTEXT, QUEUE_CREATIVE)


class AssistantJobError(RuntimeError):
    pass


def _queue_name_for_job_type(job_type: str) -> str:
    queue_name = JOB_TYPE_TO_QUEUE.get(str(job_type or "").strip())
    if not queue_name:
        raise AssistantJobError("unsupported_job_type")
    return queue_name


def _result_summary(job_type: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    body = dict(payload or {})
    if job_type == JOB_TYPE_START_BRIEF:
        brief = body.get("brief") or {}
        thread = body.get("thread") or {}
        return {
            "brief_status": brief.get("status"),
            "completeness_score": brief.get("completeness_score"),
            "thread_id": thread.get("id"),
            "message_count": len(body.get("messages") or []),
        }
    if job_type == JOB_TYPE_ASSISTANT_MESSAGE:
        brief = body.get("brief") or {}
        return {
            "brief_status": brief.get("status"),
            "completeness_score": brief.get("completeness_score"),
            "missing_fields": brief.get("missing_fields") or [],
            "message_count": len(body.get("messages") or []),
            "recommendations_count": len(body.get("recommendations") or []),
        }
    if job_type == JOB_TYPE_CONTEXT_CONFIRM:
        brief = body.get("brief") or {}
        return {
            "brief_status": brief.get("status"),
            "confirmed_at": brief.get("confirmed_at"),
            "google_sheets_ok": bool((body.get("google_sheets") or {}).get("ok")),
            "digest_ok": bool((body.get("digest_notification") or {}).get("ok")),
        }
    if job_type == JOB_TYPE_CREATIVE_GENERATE:
        draft = body.get("draft") or {}
        variants = draft.get("variants") or []
        return {
            "draft_id": draft.get("id"),
            "draft_type": draft.get("draft_type"),
            "status": draft.get("status"),
            "variant_count": len(variants),
        }
    return {}


def _job_response(job: AppJob) -> dict[str, Any]:
    return {
        "id": int(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "error_code": job.error_code,
        "result_summary": job.result_summary or {},
    }


async def enqueue_app_job(
    *,
    tenant_id: int,
    workspace_id: int | None,
    user_id: int | None,
    job_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    queue_name = _queue_name_for_job_type(job_type)
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
    except Exception as exc:
        async with async_session() as session:
            async with session.begin():
                await apply_session_rls_context(session, tenant_id=tenant_id, user_id=user_id)
                job = (await session.execute(
                    select(AppJob).where(AppJob.id == job_id, AppJob.tenant_id == tenant_id)
                )).scalar_one_or_none()
                if job is not None:
                    job.status = "failed"
                    job.error_code = "queue_unavailable"
                    job.completed_at = utcnow()
                    job.updated_at = utcnow()
                    job.result_summary = {"message": str(exc)}
        raise AssistantJobError("assistant_queue_unavailable") from exc
    return {"job_id": job_id, "status": "queued"}


async def get_job_status(
    session,
    *,
    tenant_id: int,
    workspace_id: int | None,
    job_id: int,
) -> dict[str, Any]:
    query = select(AppJob).where(AppJob.id == int(job_id), AppJob.tenant_id == tenant_id)
    if workspace_id is not None:
        query = query.where(AppJob.workspace_id == workspace_id)
    job = (await session.execute(query)).scalar_one_or_none()
    if job is None:
        raise AssistantJobError("job_not_found")
    return _job_response(job)


async def _mark_job_running(*, tenant_id: int, user_id: int | None, job_id: int) -> AppJob | None:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=tenant_id, user_id=user_id)
            job = (await session.execute(
                select(AppJob).where(AppJob.id == int(job_id), AppJob.tenant_id == tenant_id)
            )).scalar_one_or_none()
            if job is None:
                return None
            if job.status == "running":
                return job
            if job.status in {"succeeded", "failed"}:
                return job
            job.status = "running"
            job.started_at = job.started_at or utcnow()
            job.updated_at = utcnow()
            job.attempt_count = int(job.attempt_count or 0) + 1
            await session.flush()
            return job


async def _mark_job_succeeded(
    *,
    tenant_id: int,
    user_id: int | None,
    job_id: int,
    result: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=tenant_id, user_id=user_id)
            job = (await session.execute(
                select(AppJob).where(AppJob.id == int(job_id), AppJob.tenant_id == tenant_id)
            )).scalar_one_or_none()
            if job is None:
                return
            job.status = "succeeded"
            job.result = result
            job.result_summary = summary
            job.error_code = None
            job.completed_at = utcnow()
            job.updated_at = utcnow()


async def _mark_job_failed(
    *,
    tenant_id: int,
    user_id: int | None,
    job_id: int,
    error_code: str,
    message: str | None = None,
) -> None:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=tenant_id, user_id=user_id)
            job = (await session.execute(
                select(AppJob).where(AppJob.id == int(job_id), AppJob.tenant_id == tenant_id)
            )).scalar_one_or_none()
            if job is None:
                return
            job.status = "failed"
            job.error_code = error_code
            job.completed_at = utcnow()
            job.updated_at = utcnow()
            job.result_summary = {"message": message or error_code}


async def _execute_job_payload(*, job_type: str, tenant_id: int, workspace_id: int | None, user_id: int | None, payload: dict[str, Any]) -> dict[str, Any]:
    if workspace_id is None:
        raise AssistantJobError("workspace_required")
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=tenant_id, user_id=user_id)
            if job_type == JOB_TYPE_START_BRIEF:
                return await start_business_brief(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=int(workspace_id),
                    user_id=user_id,
                )
            if job_type == JOB_TYPE_ASSISTANT_MESSAGE:
                return await post_assistant_message(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=int(workspace_id),
                    user_id=user_id,
                    message=str((payload or {}).get("message") or ""),
                )
            if job_type == JOB_TYPE_CONTEXT_CONFIRM:
                return await confirm_context(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=int(workspace_id),
                    user_id=user_id,
                )
            if job_type == JOB_TYPE_CREATIVE_GENERATE:
                return await generate_creative_draft(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=int(workspace_id),
                    user_id=user_id,
                    draft_type=str((payload or {}).get("draft_type") or ""),
                    variant_count=int((payload or {}).get("variant_count") or 3),
                )
    raise AssistantJobError("unsupported_job_type")


async def process_app_job(*, job_id: int, tenant_id: int, workspace_id: int | None, user_id: int | None, job_type: str) -> bool:
    job = await _mark_job_running(tenant_id=tenant_id, user_id=user_id, job_id=job_id)
    if job is None:
        return False
    if job.status in {"succeeded", "failed"}:
        return True
    try:
        result = await _execute_job_payload(
            job_type=job_type,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            payload=job.payload or {},
        )
        await _mark_job_succeeded(
            tenant_id=tenant_id,
            user_id=user_id,
            job_id=job_id,
            result=result,
            summary=_result_summary(job_type, result),
        )
        return True
    except AssistantServiceError as exc:
        await _mark_job_failed(
            tenant_id=tenant_id,
            user_id=user_id,
            job_id=job_id,
            error_code=str(exc),
            message=str(exc),
        )
        return False
    except Exception as exc:  # pragma: no cover - defensive
        log.exception(f"assistant_jobs process_app_job failed: job_id={job_id} type={job_type}")
        await _mark_job_failed(
            tenant_id=tenant_id,
            user_id=user_id,
            job_id=job_id,
            error_code=exc.__class__.__name__,
            message=str(exc),
        )
        return False


async def drain_one_queue(queue_name: str, *, consumer_id: str = "assistant-worker", timeout: int = 0) -> bool:
    task = await task_queue.reserve(queue_name, consumer_id=consumer_id, timeout=timeout, lease_sec=300)
    if not task:
        return False
    task_id = str(task.get("_task_id") or "")
    try:
        await process_app_job(
            job_id=int(task["job_id"]),
            tenant_id=int(task["tenant_id"]),
            workspace_id=int(task["workspace_id"]) if task.get("workspace_id") is not None else None,
            user_id=int(task["user_id"]) if task.get("user_id") is not None else None,
            job_type=str(task["job_type"]),
        )
        await task_queue.ack(queue_name, task_id)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        log.exception(f"assistant_jobs drain failed: queue={queue_name} task_id={task_id}")
        await task_queue.dead_letter(queue_name, task_id, task, reason=str(exc))
        return False


async def process_pending_jobs(*, max_jobs: int = 50, consumer_id: str = "assistant-worker-test") -> int:
    processed = 0
    await task_queue.connect()
    while processed < max_jobs:
        made_progress = False
        for queue_name in ASSISTANT_QUEUE_NAMES:
            if processed >= max_jobs:
                break
            handled = await drain_one_queue(queue_name, consumer_id=consumer_id, timeout=0)
            if handled:
                processed += 1
                made_progress = True
        if not made_progress:
            break
    return processed


async def assistant_worker_loop(queue_name: str, *, stop_event: asyncio.Event, consumer_id: str) -> None:
    await task_queue.connect()
    while not stop_event.is_set():
        try:
            handled = await drain_one_queue(queue_name, consumer_id=consumer_id, timeout=1)
            if not handled:
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:  # pragma: no cover - runtime safety
            raise
        except Exception:
            log.exception(f"assistant_jobs worker loop error: queue={queue_name}")
            await asyncio.sleep(1.0)
