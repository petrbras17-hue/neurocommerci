from __future__ import annotations

import asyncio
import ast
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import httpx
from google import genai
from google.genai import types
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.gemini_models import normalize_model_name
from storage.models import (
    AIBudgetCounter,
    AIBudgetLimit,
    AIAgentRun,
    AIEscalation,
    AIRequest,
    AIRequestAttempt,
    AITaskPolicy,
)
from utils.helpers import utcnow
from utils.logger import log


PROVIDER_GEMINI = "gemini_direct"
PROVIDER_OPENROUTER = "openrouter"
TIER_BOSS = "boss"
TIER_MANAGER = "manager"
TIER_WORKER = "worker"

OUTCOME_EXECUTED = "executed_as_requested"
OUTCOME_DOWNGRADED = "downgraded_by_budget_policy"
OUTCOME_BLOCKED = "blocked_by_budget_policy"

RESPONSE_STATUS_SUCCEEDED = "succeeded"
RESPONSE_STATUS_FAILED = "failed"
RESPONSE_STATUS_BLOCKED = "blocked"


@dataclass(frozen=True)
class TaskPolicy:
    task_type: str
    agent_name: str
    requested_model_tier: str
    output_contract_type: str = "json_object"
    allow_downgrade: bool = True
    approval_required: bool = False
    latency_target_ms: int | None = None
    max_budget_usd: float | None = None


@dataclass(frozen=True)
class BudgetDecision:
    outcome: str
    executed_tier: str | None
    reason_code: str | None
    hard_stop: bool


@dataclass(frozen=True)
class CandidateModel:
    provider: str
    model_name: str
    model_tier: str


@dataclass
class ProviderCallResult:
    ok: bool
    provider: str
    model_name: str
    parsed: dict[str, Any] | None
    raw_text: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    reason_code: str | None = None
    response_meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class RoutedTaskResult:
    ok: bool
    parsed: dict[str, Any] | None
    ai_request_id: int | None
    outcome: str
    requested_tier: str
    executed_tier: str | None
    provider: str | None
    model_name: str | None
    latency_ms: int | None
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    fallback_used: bool
    reason_code: str | None
    response_meta: dict[str, Any] | None = None

    def as_meta(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "ai_request_id": self.ai_request_id,
            "outcome": self.outcome,
            "requested_tier": self.requested_tier,
            "executed_tier": self.executed_tier,
            "provider": self.provider,
            "model_name": self.model_name,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "fallback_used": self.fallback_used,
            "reason_code": self.reason_code,
            "response_meta": self.response_meta or {},
        }


DEFAULT_TASK_POLICIES: dict[str, TaskPolicy] = {
    "brief_extraction": TaskPolicy(
        task_type="brief_extraction",
        agent_name="Research & Parser Agent",
        requested_model_tier=TIER_WORKER,
        output_contract_type="json_object",
    ),
    "assistant_reply": TaskPolicy(
        task_type="assistant_reply",
        agent_name="COO Operations Agent",
        requested_model_tier=TIER_MANAGER,
        output_contract_type="json_object",
    ),
    "creative_variants": TaskPolicy(
        task_type="creative_variants",
        agent_name="Creative Director Agent",
        requested_model_tier=TIER_MANAGER,
        output_contract_type="json_object",
    ),
    "parser_query_suggestions": TaskPolicy(
        task_type="parser_query_suggestions",
        agent_name="Research & Parser Agent",
        requested_model_tier=TIER_WORKER,
        output_contract_type="json_object",
    ),
    "campaign_strategy_summary": TaskPolicy(
        task_type="campaign_strategy_summary",
        agent_name="CEO Strategy Agent",
        requested_model_tier=TIER_BOSS,
        output_contract_type="json_object",
        approval_required=True,
    ),
    "weekly_marketing_report": TaskPolicy(
        task_type="weekly_marketing_report",
        agent_name="Reporting Agent",
        requested_model_tier=TIER_MANAGER,
        output_contract_type="json_object",
    ),
}


GEMINI_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "gemini-3-pro-preview": (2.0, 12.0),
    "gemini-3.1-pro-preview": (2.0, 12.0),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-3-flash-preview": (0.30, 2.50),
    "gemini-3.1-flash-preview": (0.30, 2.50),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}


_gemini_client: genai.Client | None = None
_gemini_client_initialized = False


def _parse_csv(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _provider_order() -> list[str]:
    providers = _parse_csv(settings.AI_ALLOWED_PROVIDER_ORDER)
    return providers or [PROVIDER_GEMINI, PROVIDER_OPENROUTER]


def _default_model_refs_for_tier(tier: str) -> list[str]:
    if tier == TIER_BOSS:
        refs = _parse_csv(settings.AI_BOSS_MODELS)
        if refs:
            return refs
        return [
            "openrouter:openai/gpt-5.4",
            "openrouter:anthropic/claude-opus-4.6",
        ]
    if tier == TIER_MANAGER:
        refs = _parse_csv(settings.AI_MANAGER_MODELS)
        if refs:
            return refs
        return [
            "openrouter:anthropic/claude-sonnet-4.6",
            "openrouter:google/gemini-2.5-pro",
            f"{PROVIDER_GEMINI}:{normalize_model_name(settings.GEMINI_MODEL)}",
        ]
    refs = _parse_csv(settings.AI_WORKER_MODELS)
    if refs:
        return refs
    return [
        "openrouter:google/gemini-2.5-flash",
        f"{PROVIDER_GEMINI}:{normalize_model_name(settings.GEMINI_FLASH_MODEL)}",
    ]


def _parse_model_ref(ref: str, default_tier: str) -> CandidateModel | None:
    raw = str(ref or "").strip()
    if not raw:
        return None
    if ":" in raw:
        provider, model_name = raw.split(":", 1)
    else:
        provider, model_name = PROVIDER_GEMINI, raw
    provider = provider.strip()
    model_name = normalize_model_name(model_name.strip())
    if not provider or not model_name:
        return None
    return CandidateModel(provider=provider, model_name=model_name, model_tier=default_tier)


def _resolve_candidates(model_tier: str, allowed_providers: Iterable[str] | None = None) -> list[CandidateModel]:
    allow = list(allowed_providers or _provider_order())
    refs = _default_model_refs_for_tier(model_tier)
    candidates = [_parse_model_ref(ref, model_tier) for ref in refs]
    filtered = [candidate for candidate in candidates if candidate and candidate.provider in allow]
    ordered: list[CandidateModel] = []
    for provider in allow:
        for candidate in filtered:
            if candidate.provider == provider and candidate not in ordered:
                ordered.append(candidate)
    return ordered


def _estimate_gemini_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
    in_price, out_price = GEMINI_PRICING_PER_1M.get(
        normalize_model_name(model_name),
        GEMINI_PRICING_PER_1M.get("gemini-2.5-flash", (0.30, 2.50)),
    )
    return round(((prompt_tokens / 1_000_000) * in_price) + ((completion_tokens / 1_000_000) * out_price), 6)


def _extract_json_dict(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    cleaned = raw.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    candidates = [cleaned]
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(cleaned[first : last + 1])
    for candidate in candidates:
        candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        try:
            parsed = ast.literal_eval(candidate)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_contract_instruction(system_instruction: str) -> str:
    suffix = (
        "\n\nSTRICT OUTPUT CONTRACT:\n"
        "- Return exactly one JSON object.\n"
        "- No markdown fences.\n"
        "- No prose before or after JSON.\n"
        "- Use double quotes for all keys and strings.\n"
        "- Do not include trailing commas.\n"
        "- If some value is unknown, omit that field."
    )
    base = str(system_instruction or "").strip()
    if "STRICT OUTPUT CONTRACT" in base:
        return base
    return f"{base}{suffix}" if base else suffix.strip()


def _get_gemini_client() -> genai.Client | None:
    global _gemini_client_initialized, _gemini_client
    if _gemini_client_initialized:
        return _gemini_client
    _gemini_client_initialized = True
    if not settings.GEMINI_API_KEY:
        return None
    try:
        _gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning(f"ai_router gemini init failed: {exc}")
    return _gemini_client


def _period_start(now: datetime, period_type: str) -> datetime:
    if period_type == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _load_task_policy(session: AsyncSession, tenant_id: int, task_type: str) -> TaskPolicy:
    row = (
        await session.execute(
            select(AITaskPolicy)
            .where(
                AITaskPolicy.task_type == task_type,
                AITaskPolicy.is_active.is_(True),
                or_(AITaskPolicy.tenant_id == tenant_id, AITaskPolicy.tenant_id.is_(None)),
            )
            .order_by(AITaskPolicy.tenant_id.desc().nulls_last(), AITaskPolicy.id.desc())
        )
    ).scalars().first()
    if row is None:
        return DEFAULT_TASK_POLICIES.get(
            task_type,
            TaskPolicy(
                task_type=task_type,
                agent_name="COO Operations Agent",
                requested_model_tier=TIER_WORKER,
            ),
        )
    return TaskPolicy(
        task_type=row.task_type,
        agent_name=(row.policy or {}).get("agent_name") or DEFAULT_TASK_POLICIES.get(task_type, TaskPolicy(task_type, "COO Operations Agent", TIER_WORKER)).agent_name,
        requested_model_tier=row.requested_model_tier,
        output_contract_type=row.output_contract_type or "json_object",
        allow_downgrade=bool(row.allow_downgrade),
        approval_required=bool(row.approval_required),
        latency_target_ms=row.latency_target_ms,
        max_budget_usd=row.max_budget_usd,
    )


async def _effective_budget_limits(session: AsyncSession, tenant_id: int) -> dict[str, Any]:
    override = (
        await session.execute(
            select(AIBudgetLimit).where(AIBudgetLimit.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    return {
        "daily_budget_usd": float(
            override.daily_budget_usd if override and override.daily_budget_usd is not None else settings.AI_DAILY_BUDGET_USD
        ),
        "monthly_budget_usd": float(
            override.monthly_budget_usd if override and override.monthly_budget_usd is not None else settings.AI_MONTHLY_BUDGET_USD
        ),
        "boss_daily_budget_usd": float(
            override.boss_daily_budget_usd if override and override.boss_daily_budget_usd is not None else settings.AI_BOSS_DAILY_BUDGET_USD
        ),
        "hard_stop_enabled": bool(
            override.hard_stop_enabled if override is not None else settings.AI_HARD_STOP_ENABLED
        ),
    }


async def _spent_cost(
    session: AsyncSession,
    tenant_id: int,
    *,
    since: datetime,
    tier: str | None = None,
) -> float:
    conditions = [
        AIRequest.tenant_id == tenant_id,
        AIRequest.created_at >= since,
        AIRequest.status == RESPONSE_STATUS_SUCCEEDED,
    ]
    if tier:
        conditions.append(AIRequest.executed_model_tier == tier)
    total = (
        await session.execute(select(func.coalesce(func.sum(AIRequest.estimated_cost_usd), 0.0)).where(*conditions))
    ).scalar_one()
    return float(total or 0.0)


async def evaluate_budget_guardrail(
    session: AsyncSession,
    *,
    tenant_id: int,
    requested_model_tier: str,
    allow_downgrade: bool = True,
) -> BudgetDecision:
    now = utcnow()
    limits = await _effective_budget_limits(session, tenant_id)
    daily_spend = await _spent_cost(session, tenant_id, since=_period_start(now, "daily"))
    monthly_spend = await _spent_cost(session, tenant_id, since=_period_start(now, "monthly"))
    boss_daily_spend = await _spent_cost(session, tenant_id, since=_period_start(now, "daily"), tier=TIER_BOSS)

    if limits["monthly_budget_usd"] > 0 and monthly_spend >= limits["monthly_budget_usd"]:
        return BudgetDecision(
            outcome=OUTCOME_BLOCKED if limits["hard_stop_enabled"] else OUTCOME_DOWNGRADED,
            executed_tier=None if limits["hard_stop_enabled"] else TIER_WORKER,
            reason_code="monthly_budget_exceeded",
            hard_stop=limits["hard_stop_enabled"],
        )

    if requested_model_tier == TIER_BOSS and limits["boss_daily_budget_usd"] > 0 and boss_daily_spend >= limits["boss_daily_budget_usd"]:
        if allow_downgrade:
            return BudgetDecision(
                outcome=OUTCOME_DOWNGRADED,
                executed_tier=TIER_MANAGER,
                reason_code="boss_daily_budget_exceeded",
                hard_stop=False,
            )
        return BudgetDecision(
            outcome=OUTCOME_BLOCKED,
            executed_tier=None,
            reason_code="boss_daily_budget_exceeded",
            hard_stop=True,
        )

    if limits["daily_budget_usd"] > 0 and daily_spend >= limits["daily_budget_usd"]:
        if allow_downgrade and requested_model_tier != TIER_WORKER:
            return BudgetDecision(
                outcome=OUTCOME_DOWNGRADED,
                executed_tier=TIER_WORKER,
                reason_code="daily_budget_exceeded",
                hard_stop=False,
            )
        return BudgetDecision(
            outcome=OUTCOME_BLOCKED if limits["hard_stop_enabled"] else OUTCOME_DOWNGRADED,
            executed_tier=None if limits["hard_stop_enabled"] else TIER_WORKER,
            reason_code="daily_budget_exceeded",
            hard_stop=limits["hard_stop_enabled"],
        )

    return BudgetDecision(
        outcome=OUTCOME_EXECUTED,
        executed_tier=requested_model_tier,
        reason_code=None,
        hard_stop=False,
    )


async def _record_budget_counter(
    session: AsyncSession,
    *,
    tenant_id: int,
    provider: str,
    model_tier: str,
    prompt_tokens: int,
    completion_tokens: int,
    estimated_cost_usd: float,
) -> None:
    now = utcnow()
    for period_type in ("daily", "monthly"):
        start = _period_start(now, period_type)
        counter = (
            await session.execute(
                select(AIBudgetCounter).where(
                    AIBudgetCounter.tenant_id == tenant_id,
                    AIBudgetCounter.period_type == period_type,
                    AIBudgetCounter.period_start == start,
                    AIBudgetCounter.model_tier == model_tier,
                    AIBudgetCounter.provider == provider,
                )
            )
        ).scalar_one_or_none()
        if counter is None:
            counter = AIBudgetCounter(
                tenant_id=tenant_id,
                period_type=period_type,
                period_start=start,
                model_tier=model_tier,
                provider=provider,
                request_count=0,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
            )
            session.add(counter)
            await session.flush()
        counter.request_count = int(counter.request_count or 0) + 1
        counter.prompt_tokens = int(counter.prompt_tokens or 0) + int(prompt_tokens or 0)
        counter.completion_tokens = int(counter.completion_tokens or 0) + int(completion_tokens or 0)
        counter.estimated_cost_usd = round(float(counter.estimated_cost_usd or 0.0) + float(estimated_cost_usd or 0.0), 6)
        counter.updated_at = now


async def _call_gemini_json(
    *,
    model_name: str,
    prompt: str,
    system_instruction: str,
    max_output_tokens: int,
    temperature: float,
) -> ProviderCallResult:
    client = _get_gemini_client()
    if client is None:
        return ProviderCallResult(
            ok=False,
            provider=PROVIDER_GEMINI,
            model_name=model_name,
            parsed=None,
            reason_code="gemini_not_configured",
        )
    started = utcnow()
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_json_contract_instruction(system_instruction),
                    temperature=max(0.1, min(temperature, 0.35)),
                    max_output_tokens=max_output_tokens,
                    response_mime_type="application/json",
                ),
            ),
            timeout=35.0,
        )
    except asyncio.TimeoutError:
        return ProviderCallResult(
            ok=False,
            provider=PROVIDER_GEMINI,
            model_name=model_name,
            parsed=None,
            reason_code="timeout",
        )
    except Exception as exc:  # pragma: no cover - defensive
        return ProviderCallResult(
            ok=False,
            provider=PROVIDER_GEMINI,
            model_name=model_name,
            parsed=None,
            reason_code=f"error:{exc.__class__.__name__}",
        )

    text = str(getattr(response, "text", "") or "")
    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = int(
        getattr(usage, "prompt_token_count", 0)
        or getattr(usage, "input_token_count", 0)
        or 0
    )
    completion_tokens = int(
        getattr(usage, "candidates_token_count", 0)
        or getattr(usage, "output_token_count", 0)
        or 0
    )
    parsed = _extract_json_dict(text)
    latency_ms = max(1, int((utcnow() - started).total_seconds() * 1000))
    return ProviderCallResult(
        ok=parsed is not None,
        provider=PROVIDER_GEMINI,
        model_name=model_name,
        parsed=parsed,
        raw_text=text,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=_estimate_gemini_cost(model_name, prompt_tokens, completion_tokens),
        reason_code=None if parsed is not None else "json_parse_failed",
        response_meta={"usage_metadata_present": usage is not None},
    )


async def _call_openrouter_json(
    *,
    model_name: str,
    prompt: str,
    system_instruction: str,
    max_output_tokens: int,
    temperature: float,
) -> ProviderCallResult:
    if not settings.OPENROUTER_API_KEY:
        return ProviderCallResult(
            ok=False,
            provider=PROVIDER_OPENROUTER,
            model_name=model_name,
            parsed=None,
            reason_code="openrouter_not_configured",
        )

    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    if settings.OPENROUTER_DEFAULT_REFERER:
        headers["HTTP-Referer"] = settings.OPENROUTER_DEFAULT_REFERER
    if settings.OPENROUTER_DEFAULT_TITLE:
        headers["X-Title"] = settings.OPENROUTER_DEFAULT_TITLE

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": _json_contract_instruction(system_instruction)},
            {"role": "user", "content": prompt},
        ],
        "temperature": max(0.1, min(temperature, 0.35)),
        "max_tokens": max_output_tokens,
        "response_format": {"type": "json_object"},
        "provider": {
            "order": _provider_order(),
            "allow_fallbacks": bool(True),
            "require_parameters": bool(False),
        },
    }

    started = utcnow()
    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            response = await client.post(
                f"{settings.OPENROUTER_BASE_URL.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        return ProviderCallResult(
            ok=False,
            provider=PROVIDER_OPENROUTER,
            model_name=model_name,
            parsed=None,
            reason_code=f"http_{exc.response.status_code}",
            response_meta={"body": exc.response.text[:1000]},
        )
    except Exception as exc:  # pragma: no cover - defensive
        return ProviderCallResult(
            ok=False,
            provider=PROVIDER_OPENROUTER,
            model_name=model_name,
            parsed=None,
            reason_code=f"error:{exc.__class__.__name__}",
        )

    choice = ((data.get("choices") or [{}])[0]) if isinstance(data, dict) else {}
    message = choice.get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    parsed = _extract_json_dict(str(content))
    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    estimated_cost = usage.get("cost")
    if estimated_cost is None:
        estimated_cost = 0.0
    latency_ms = max(1, int((utcnow() - started).total_seconds() * 1000))
    return ProviderCallResult(
        ok=parsed is not None,
        provider=PROVIDER_OPENROUTER,
        model_name=model_name,
        parsed=parsed,
        raw_text=str(content),
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=round(float(estimated_cost or 0.0), 6),
        reason_code=None if parsed is not None else "json_parse_failed",
        response_meta={
            "provider": data.get("provider"),
            "id": data.get("id"),
        },
    )


async def _create_ai_request(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int | None,
    user_id: int | None,
    surface: str,
    task_type: str,
    agent_name: str,
    requested_model_tier: str,
    requested_provider: str | None,
    output_contract_type: str,
    meta: dict[str, Any] | None,
) -> AIRequest:
    row = AIRequest(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        surface=surface,
        task_type=task_type,
        agent_name=agent_name,
        requested_model_tier=requested_model_tier,
        requested_provider=requested_provider,
        output_contract_type=output_contract_type,
        meta=meta or {},
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def _record_attempt(
    session: AsyncSession,
    *,
    request_id: int,
    tenant_id: int,
    attempt_number: int,
    result: ProviderCallResult,
    fallback_used: bool,
) -> None:
    session.add(
        AIRequestAttempt(
            ai_request_id=request_id,
            tenant_id=tenant_id,
            attempt_number=attempt_number,
            provider=result.provider,
            model_name=result.model_name,
            status=RESPONSE_STATUS_SUCCEEDED if result.ok else RESPONSE_STATUS_FAILED,
            latency_ms=result.latency_ms or None,
            prompt_tokens=result.prompt_tokens or 0,
            completion_tokens=result.completion_tokens or 0,
            estimated_cost_usd=result.estimated_cost_usd or 0.0,
            fallback_used=fallback_used,
            reason_code=result.reason_code,
            response_meta=result.response_meta or {},
            created_at=utcnow(),
        )
    )


async def _record_agent_run(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int | None,
    user_id: int | None,
    request_id: int,
    policy: TaskPolicy,
    executed_tier: str | None,
    status: str,
) -> None:
    session.add(
        AIAgentRun(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            ai_request_id=request_id,
            agent_name=policy.agent_name,
            task_type=policy.task_type,
            requested_model_tier=policy.requested_model_tier,
            executed_model_tier=executed_tier,
            status=status,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )


async def escalate_to_boss(
    session: AsyncSession,
    *,
    tenant_id: int,
    workspace_id: int | None,
    user_id: int | None,
    ai_request_id: int | None,
    task_type: str,
    from_tier: str | None,
    reason_code: str,
    approved_by_user: bool = False,
) -> None:
    session.add(
        AIEscalation(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            ai_request_id=ai_request_id,
            task_type=task_type,
            from_tier=from_tier,
            to_tier=TIER_BOSS,
            trigger_type="manual" if approved_by_user else "policy",
            reason_code=reason_code,
            approved_by_user=approved_by_user,
            created_at=utcnow(),
        )
    )


async def route_ai_task(
    session: AsyncSession,
    *,
    task_type: str,
    prompt: str,
    system_instruction: str,
    tenant_id: int,
    workspace_id: int | None = None,
    user_id: int | None = None,
    policy_context: dict[str, Any] | None = None,
    max_output_tokens: int = 700,
    temperature: float = 0.4,
    surface: str = "assistant",
) -> RoutedTaskResult:
    policy_context = policy_context or {}
    policy = await _load_task_policy(session, tenant_id, task_type)
    if policy.approval_required and not bool(policy_context.get("manual_boss_approval")):
        await escalate_to_boss(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            ai_request_id=None,
            task_type=task_type,
            from_tier=policy.requested_model_tier,
            reason_code="boss_approval_required",
            approved_by_user=False,
        )
        return RoutedTaskResult(
            ok=False,
            parsed=None,
            ai_request_id=None,
            outcome=OUTCOME_BLOCKED,
            requested_tier=policy.requested_model_tier,
            executed_tier=None,
            provider=None,
            model_name=None,
            latency_ms=None,
            prompt_tokens=0,
            completion_tokens=0,
            estimated_cost_usd=0.0,
            fallback_used=False,
            reason_code="boss_approval_required",
        )

    budget_decision = await evaluate_budget_guardrail(
        session,
        tenant_id=tenant_id,
        requested_model_tier=policy.requested_model_tier,
        allow_downgrade=policy.allow_downgrade,
    )
    if budget_decision.outcome == OUTCOME_BLOCKED:
        blocked_request = await _create_ai_request(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            surface=surface,
            task_type=task_type,
            agent_name=policy.agent_name,
            requested_model_tier=policy.requested_model_tier,
            requested_provider=None,
            output_contract_type=policy.output_contract_type,
            meta={"policy_context": policy_context},
        )
        blocked_request.status = RESPONSE_STATUS_BLOCKED
        blocked_request.outcome = OUTCOME_BLOCKED
        blocked_request.reason_code = budget_decision.reason_code
        blocked_request.completed_at = utcnow()
        blocked_request.updated_at = utcnow()
        await _record_agent_run(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            request_id=blocked_request.id,
            policy=policy,
            executed_tier=None,
            status=RESPONSE_STATUS_BLOCKED,
        )
        return RoutedTaskResult(
            ok=False,
            parsed=None,
            ai_request_id=blocked_request.id,
            outcome=OUTCOME_BLOCKED,
            requested_tier=policy.requested_model_tier,
            executed_tier=None,
            provider=None,
            model_name=None,
            latency_ms=None,
            prompt_tokens=0,
            completion_tokens=0,
            estimated_cost_usd=0.0,
            fallback_used=False,
            reason_code=budget_decision.reason_code,
        )

    executed_tier = budget_decision.executed_tier or policy.requested_model_tier
    candidates = _resolve_candidates(executed_tier, policy_context.get("allowed_providers"))
    if settings.AI_DEFAULT_MODE == "gemini_only":
        candidates = [candidate for candidate in candidates if candidate.provider == PROVIDER_GEMINI]
    elif settings.AI_DEFAULT_MODE == "openrouter_only":
        candidates = [candidate for candidate in candidates if candidate.provider == PROVIDER_OPENROUTER]

    ai_request = await _create_ai_request(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        surface=surface,
        task_type=task_type,
        agent_name=policy.agent_name,
        requested_model_tier=policy.requested_model_tier,
        requested_provider=candidates[0].provider if candidates else None,
        output_contract_type=policy.output_contract_type,
        meta={"policy_context": policy_context, "candidate_count": len(candidates)},
    )

    if budget_decision.outcome == OUTCOME_DOWNGRADED and executed_tier != policy.requested_model_tier:
        await session.flush()
        session.add(
            AIEscalation(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                ai_request_id=ai_request.id,
                task_type=task_type,
                from_tier=policy.requested_model_tier,
                to_tier=executed_tier,
                trigger_type="policy",
                reason_code=budget_decision.reason_code,
                approved_by_user=False,
                created_at=utcnow(),
            )
        )

    if not candidates:
        ai_request.status = RESPONSE_STATUS_FAILED
        ai_request.outcome = budget_decision.outcome
        ai_request.executed_model_tier = executed_tier
        ai_request.reason_code = "no_model_candidates"
        ai_request.completed_at = utcnow()
        ai_request.updated_at = utcnow()
        await _record_agent_run(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            request_id=ai_request.id,
            policy=policy,
            executed_tier=executed_tier,
            status=RESPONSE_STATUS_FAILED,
        )
        return RoutedTaskResult(
            ok=False,
            parsed=None,
            ai_request_id=ai_request.id,
            outcome=budget_decision.outcome,
            requested_tier=policy.requested_model_tier,
            executed_tier=executed_tier,
            provider=None,
            model_name=None,
            latency_ms=None,
            prompt_tokens=0,
            completion_tokens=0,
            estimated_cost_usd=0.0,
            fallback_used=False,
            reason_code="no_model_candidates",
        )

    attempts = 0
    for candidate in candidates:
        attempts += 1
        if candidate.provider == PROVIDER_GEMINI:
            result = await _call_gemini_json(
                model_name=candidate.model_name,
                prompt=prompt,
                system_instruction=system_instruction,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )
        else:
            result = await _call_openrouter_json(
                model_name=candidate.model_name,
                prompt=prompt,
                system_instruction=system_instruction,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )
        await _record_attempt(
            session,
            request_id=ai_request.id,
            tenant_id=tenant_id,
            attempt_number=attempts,
            result=result,
            fallback_used=attempts > 1,
        )
        if not result.ok:
            continue

        ai_request.executed_model_tier = executed_tier
        ai_request.executed_provider = result.provider
        ai_request.executed_model = result.model_name
        ai_request.status = RESPONSE_STATUS_SUCCEEDED
        ai_request.outcome = budget_decision.outcome
        ai_request.latency_ms = result.latency_ms
        ai_request.prompt_tokens = result.prompt_tokens
        ai_request.completion_tokens = result.completion_tokens
        ai_request.estimated_cost_usd = result.estimated_cost_usd
        ai_request.fallback_used = attempts > 1 or budget_decision.outcome == OUTCOME_DOWNGRADED
        ai_request.reason_code = budget_decision.reason_code
        ai_request.quality_flags = {
            "parsed_ok": True,
            "fallback_used": ai_request.fallback_used,
        }
        ai_request.completed_at = utcnow()
        ai_request.updated_at = utcnow()
        await _record_budget_counter(
            session,
            tenant_id=tenant_id,
            provider=result.provider,
            model_tier=executed_tier,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            estimated_cost_usd=result.estimated_cost_usd,
        )
        await _record_agent_run(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            request_id=ai_request.id,
            policy=policy,
            executed_tier=executed_tier,
            status=RESPONSE_STATUS_SUCCEEDED,
        )
        return RoutedTaskResult(
            ok=True,
            parsed=result.parsed,
            ai_request_id=ai_request.id,
            outcome=budget_decision.outcome,
            requested_tier=policy.requested_model_tier,
            executed_tier=executed_tier,
            provider=result.provider,
            model_name=result.model_name,
            latency_ms=result.latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            estimated_cost_usd=result.estimated_cost_usd,
            fallback_used=bool(ai_request.fallback_used),
            reason_code=budget_decision.reason_code,
            response_meta=result.response_meta,
        )

    ai_request.executed_model_tier = executed_tier
    ai_request.status = RESPONSE_STATUS_FAILED
    ai_request.outcome = budget_decision.outcome
    ai_request.reason_code = "all_provider_attempts_failed"
    ai_request.quality_flags = {"parsed_ok": False, "fallback_used": attempts > 1}
    ai_request.completed_at = utcnow()
    ai_request.updated_at = utcnow()
    await _record_agent_run(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        request_id=ai_request.id,
        policy=policy,
        executed_tier=executed_tier,
        status=RESPONSE_STATUS_FAILED,
    )
    return RoutedTaskResult(
        ok=False,
        parsed=None,
        ai_request_id=ai_request.id,
        outcome=budget_decision.outcome,
        requested_tier=policy.requested_model_tier,
        executed_tier=executed_tier,
        provider=None,
        model_name=None,
        latency_ms=None,
        prompt_tokens=0,
        completion_tokens=0,
        estimated_cost_usd=0.0,
        fallback_used=attempts > 1,
        reason_code="all_provider_attempts_failed",
    )


async def record_ai_usage(
    session: AsyncSession,
    *,
    tenant_id: int,
    provider: str,
    model_tier: str,
    prompt_tokens: int,
    completion_tokens: int,
    estimated_cost_usd: float,
) -> None:
    await _record_budget_counter(
        session,
        tenant_id=tenant_id,
        provider=provider,
        model_tier=model_tier,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=estimated_cost_usd,
    )
