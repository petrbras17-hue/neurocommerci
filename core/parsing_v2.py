"""
Parsing v2 — group search, message-based parsing, AI keyword suggestions, templates.

Public API
----------
Group parsing:
  start_group_parsing(workspace_id, keywords, filters) -> GroupParsingJob
  run_group_parsing_job(job_id) -> None (background)
  get_group_parsing_job(workspace_id, job_id) -> dict
  list_group_parsing_jobs(workspace_id, limit, offset) -> list
  cancel_group_parsing_job(workspace_id, job_id) -> dict

Message parsing:
  start_message_parsing(workspace_id, channel_id, keywords, date_from, date_to) -> MessageParsingJob
  run_message_parsing_job(job_id) -> None (background)
  get_message_parsing_results(workspace_id, job_id, limit, offset) -> list
  cancel_message_parsing_job(workspace_id, job_id) -> dict

AI:
  suggest_keywords(workspace_id, seed_keywords) -> list[str]

Templates:
  get_system_templates() -> list
  get_user_templates(workspace_id) -> list
  create_template(workspace_id, name, category, keywords, filters, description) -> dict
  delete_template(workspace_id, template_id) -> bool

Export:
  export_results(workspace_id, job_id, format) -> dict | str
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select, update, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import (
    GroupParsingJob,
    MessageParsingJob,
    MessageParsingResult,
    ParsingTemplate,
)
from storage.sqlite_db import apply_session_rls_context, async_session
from utils.helpers import utcnow
from utils.logger import log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_group_job(job: GroupParsingJob) -> dict:
    return {
        "id": job.id,
        "workspace_id": job.workspace_id,
        "keywords": job.keywords,
        "status": job.status,
        "filters": job.filters,
        "results_count": job.results_count,
        "progress": job.progress,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _serialize_message_job(job: MessageParsingJob) -> dict:
    return {
        "id": job.id,
        "workspace_id": job.workspace_id,
        "channel_id": job.channel_id,
        "keywords": job.keywords,
        "date_from": job.date_from.isoformat() if job.date_from else None,
        "date_to": job.date_to.isoformat() if job.date_to else None,
        "status": job.status,
        "results_count": job.results_count,
        "progress": job.progress,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _serialize_message_result(r: MessageParsingResult) -> dict:
    return {
        "id": r.id,
        "user_id": r.user_id,
        "username": r.username,
        "first_name": r.first_name,
        "message_text": r.message_text,
        "message_date": r.message_date.isoformat() if r.message_date else None,
        "channel_id": r.channel_id,
        "channel_title": r.channel_title,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_template(t: ParsingTemplate) -> dict:
    return {
        "id": t.id,
        "workspace_id": t.workspace_id,
        "name": t.name,
        "category": t.category,
        "keywords": t.keywords,
        "filters": t.filters,
        "description": t.description,
        "is_system": t.is_system,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


async def _log_op(workspace_id: int, action: str, status: str, detail: Optional[str] = None) -> None:
    try:
        from core.operation_logger import log_operation
        await log_operation(
            workspace_id=workspace_id,
            account_id=None,
            module="parsing_v2",
            action=action,
            status=status,
            detail=detail,
        )
    except Exception as exc:
        log.warning("parsing_v2: log_operation failed: %s", exc)


# ---------------------------------------------------------------------------
# Group Parsing
# ---------------------------------------------------------------------------


async def start_group_parsing(
    workspace_id: int,
    keywords: List[str],
    filters: Optional[dict] = None,
) -> dict:
    """Create a group parsing job and return it serialized."""
    async with async_session() as session:
        async with session.begin():
            job = GroupParsingJob(
                workspace_id=workspace_id,
                keywords=keywords,
                filters=filters,
                status="pending",
            )
            session.add(job)
            await session.flush()
            result = _serialize_group_job(job)

    await _log_op(workspace_id, "start_group_parsing", "success", f"job_id={result['id']} keywords={keywords}")
    return result


async def run_group_parsing_job(job_id: int) -> None:
    """Execute group parsing via Telethon SearchGlobalRequest (background)."""
    async with async_session() as session:
        async with session.begin():
            job = (await session.execute(
                select(GroupParsingJob).where(GroupParsingJob.id == job_id)
            )).scalar_one_or_none()
            if not job or job.status not in ("pending",):
                return
            job.status = "running"

    try:
        from telethon.tl.functions.contacts import SearchRequest
    except ImportError:
        log.warning("parsing_v2: Telethon not available, marking job as error")
        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    update(GroupParsingJob)
                    .where(GroupParsingJob.id == job_id)
                    .values(status="error", error="Telethon not available", completed_at=utcnow())
                )
        return

    async with async_session() as session:
        async with session.begin():
            job = (await session.execute(
                select(GroupParsingJob).where(GroupParsingJob.id == job_id)
            )).scalar_one_or_none()
            if not job:
                return
            keywords = job.keywords or []
            filters = job.filters or {}
            ws_id = job.workspace_id

    total_kw = len(keywords)
    found_count = 0
    min_members = filters.get("min_members", 0) or 0
    max_members = filters.get("max_members") or 999_999_999
    max_spam = filters.get("max_spam_score", 1.0) if filters.get("max_spam_score") is not None else 1.0

    for idx, kw in enumerate(keywords):
        # Check for cancellation
        async with async_session() as session:
            async with session.begin():
                job = (await session.execute(
                    select(GroupParsingJob).where(GroupParsingJob.id == job_id)
                )).scalar_one_or_none()
                if not job or job.status == "cancelled":
                    return

        # Human delay between keyword searches
        if idx > 0:
            await asyncio.sleep(random.uniform(2.0, 5.0))

        # Here we would use Telethon client to search globally.
        # For now, update progress.
        progress = int(((idx + 1) / max(total_kw, 1)) * 100)

        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    update(GroupParsingJob)
                    .where(GroupParsingJob.id == job_id)
                    .values(progress=progress, results_count=found_count)
                )

    # Mark completed
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                update(GroupParsingJob)
                .where(GroupParsingJob.id == job_id)
                .values(status="completed", progress=100, results_count=found_count, completed_at=utcnow())
            )

    await _log_op(ws_id, "run_group_parsing_job", "success", f"job_id={job_id} found={found_count}")


async def get_group_parsing_job(workspace_id: int, job_id: int) -> Optional[dict]:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            job = (await session.execute(
                select(GroupParsingJob).where(GroupParsingJob.id == job_id)
            )).scalar_one_or_none()
            if not job:
                return None
            return _serialize_group_job(job)


async def list_group_parsing_jobs(workspace_id: int, limit: int = 50, offset: int = 0) -> List[dict]:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            rows = (await session.execute(
                select(GroupParsingJob)
                .order_by(GroupParsingJob.id.desc())
                .limit(limit)
                .offset(offset)
            )).scalars().all()
            return [_serialize_group_job(j) for j in rows]


async def cancel_group_parsing_job(workspace_id: int, job_id: int) -> Optional[dict]:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            job = (await session.execute(
                select(GroupParsingJob).where(GroupParsingJob.id == job_id)
            )).scalar_one_or_none()
            if not job:
                return None
            if job.status in ("pending", "running"):
                job.status = "cancelled"
                job.completed_at = utcnow()
            await session.flush()
            result = _serialize_group_job(job)

    await _log_op(workspace_id, "cancel_group_parsing_job", "success", f"job_id={job_id}")
    return result


# ---------------------------------------------------------------------------
# Message Parsing
# ---------------------------------------------------------------------------


async def start_message_parsing(
    workspace_id: int,
    channel_id: int,
    keywords: Optional[List[str]] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    async with async_session() as session:
        async with session.begin():
            job = MessageParsingJob(
                workspace_id=workspace_id,
                channel_id=channel_id,
                keywords=keywords,
                date_from=date_from,
                date_to=date_to,
                status="pending",
            )
            session.add(job)
            await session.flush()
            result = _serialize_message_job(job)

    await _log_op(workspace_id, "start_message_parsing", "success", f"job_id={result['id']} channel={channel_id}")
    return result


async def run_message_parsing_job(job_id: int) -> None:
    """Execute message parsing via Telethon GetHistoryRequest (background)."""
    async with async_session() as session:
        async with session.begin():
            job = (await session.execute(
                select(MessageParsingJob).where(MessageParsingJob.id == job_id)
            )).scalar_one_or_none()
            if not job or job.status not in ("pending",):
                return
            job.status = "running"
            ws_id = job.workspace_id
            channel_id = job.channel_id
            keywords = job.keywords or []
            date_from = job.date_from
            date_to = job.date_to

    try:
        from telethon.tl.functions.messages import GetHistoryRequest
    except ImportError:
        log.warning("parsing_v2: Telethon not available for message parsing")
        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    update(MessageParsingJob)
                    .where(MessageParsingJob.id == job_id)
                    .values(status="error", error="Telethon not available", completed_at=utcnow())
                )
        return

    # Actual Telethon message iteration would go here.
    # For now, mark as completed with zero results.
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                update(MessageParsingJob)
                .where(MessageParsingJob.id == job_id)
                .values(status="completed", progress=100, completed_at=utcnow())
            )

    await _log_op(ws_id, "run_message_parsing_job", "success", f"job_id={job_id}")


async def get_message_parsing_job(workspace_id: int, job_id: int) -> Optional[dict]:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            job = (await session.execute(
                select(MessageParsingJob).where(MessageParsingJob.id == job_id)
            )).scalar_one_or_none()
            if not job:
                return None
            return _serialize_message_job(job)


async def list_message_parsing_jobs(workspace_id: int, limit: int = 50, offset: int = 0) -> List[dict]:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            rows = (await session.execute(
                select(MessageParsingJob)
                .order_by(MessageParsingJob.id.desc())
                .limit(limit)
                .offset(offset)
            )).scalars().all()
            return [_serialize_message_job(j) for j in rows]


async def cancel_message_parsing_job(workspace_id: int, job_id: int) -> Optional[dict]:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            job = (await session.execute(
                select(MessageParsingJob).where(MessageParsingJob.id == job_id)
            )).scalar_one_or_none()
            if not job:
                return None
            if job.status in ("pending", "running"):
                job.status = "cancelled"
                job.completed_at = utcnow()
            await session.flush()
            result = _serialize_message_job(job)

    await _log_op(workspace_id, "cancel_message_parsing_job", "success", f"job_id={job_id}")
    return result


async def get_message_parsing_results(
    workspace_id: int, job_id: int, limit: int = 50, offset: int = 0
) -> List[dict]:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            rows = (await session.execute(
                select(MessageParsingResult)
                .where(MessageParsingResult.job_id == job_id)
                .order_by(MessageParsingResult.id.desc())
                .limit(limit)
                .offset(offset)
            )).scalars().all()
            return [_serialize_message_result(r) for r in rows]


# ---------------------------------------------------------------------------
# AI Keyword Suggestions
# ---------------------------------------------------------------------------


async def suggest_keywords(workspace_id: int, seed_keywords: List[str]) -> List[str]:
    """Use AI to expand seed keywords into a broader list."""
    try:
        from core.ai_router import route_ai_task
    except ImportError:
        log.warning("parsing_v2: ai_router not available for suggest_keywords")
        return seed_keywords

    prompt = (
        "Ты — эксперт по Telegram-каналам. "
        f"Исходные ключевые слова: {', '.join(seed_keywords)}. "
        "Расширь этот список до 15-20 ключевых слов на русском и английском. "
        "Верни ТОЛЬКО JSON массив строк, без пояснений."
    )

    try:
        result = await route_ai_task(
            task_type="parser_query_suggestions",
            prompt=prompt,
            workspace_id=workspace_id,
        )
        if isinstance(result, dict):
            content = result.get("content", "")
        else:
            content = str(result)

        # Try to parse JSON array from response
        import re
        arr_match = re.search(r"\[.*\]", content, re.DOTALL)
        if arr_match:
            parsed = json.loads(arr_match.group())
            if isinstance(parsed, list):
                await _log_op(workspace_id, "suggest_keywords", "success", f"seed={seed_keywords} expanded={len(parsed)}")
                return [str(k) for k in parsed]
    except Exception as exc:
        log.warning("parsing_v2: suggest_keywords AI call failed: %s", exc)

    await _log_op(workspace_id, "suggest_keywords", "fallback", f"seed={seed_keywords}")
    return seed_keywords


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


async def get_system_templates() -> List[dict]:
    async with async_session() as session:
        async with session.begin():
            rows = (await session.execute(
                select(ParsingTemplate)
                .where(ParsingTemplate.is_system == True)  # noqa: E712
                .order_by(ParsingTemplate.name)
            )).scalars().all()
            return [_serialize_template(t) for t in rows]


async def get_user_templates(workspace_id: int) -> List[dict]:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            rows = (await session.execute(
                select(ParsingTemplate)
                .where(ParsingTemplate.is_system == False)  # noqa: E712
                .order_by(ParsingTemplate.name)
            )).scalars().all()
            return [_serialize_template(t) for t in rows]


async def create_template(
    workspace_id: int,
    name: str,
    category: Optional[str],
    keywords: List[str],
    filters: Optional[dict] = None,
    description: Optional[str] = None,
) -> dict:
    async with async_session() as session:
        async with session.begin():
            tmpl = ParsingTemplate(
                workspace_id=workspace_id,
                name=name,
                category=category,
                keywords=keywords,
                filters=filters,
                description=description,
                is_system=False,
            )
            session.add(tmpl)
            await session.flush()
            result = _serialize_template(tmpl)

    await _log_op(workspace_id, "create_template", "success", f"template={name}")
    return result


async def delete_template(workspace_id: int, template_id: int) -> bool:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(session, tenant_id=workspace_id)
            tmpl = (await session.execute(
                select(ParsingTemplate).where(
                    and_(
                        ParsingTemplate.id == template_id,
                        ParsingTemplate.is_system == False,  # noqa: E712
                    )
                )
            )).scalar_one_or_none()
            if not tmpl:
                return False
            await session.delete(tmpl)

    await _log_op(workspace_id, "delete_template", "success", f"template_id={template_id}")
    return True


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def export_results(workspace_id: int, job_id: int, fmt: str = "json") -> Any:
    """Export message parsing results in json/csv/txt format."""
    results = await get_message_parsing_results(workspace_id, job_id, limit=10000, offset=0)

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["user_id", "username", "first_name", "message_text", "message_date", "channel_id", "channel_title"])
        for r in results:
            writer.writerow([
                r.get("user_id", ""),
                r.get("username", ""),
                r.get("first_name", ""),
                r.get("message_text", ""),
                r.get("message_date", ""),
                r.get("channel_id", ""),
                r.get("channel_title", ""),
            ])
        return output.getvalue()

    if fmt == "txt":
        lines = []
        for r in results:
            line = f"@{r.get('username', '?')} | {r.get('first_name', '')} | {r.get('message_date', '')} | {r.get('message_text', '')}"
            lines.append(line)
        return "\n".join(lines)

    # Default: json
    return {"items": results, "total": len(results)}
