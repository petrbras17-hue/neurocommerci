from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from config import settings
from core.ai_router import (
    OUTCOME_BLOCKED,
    OUTCOME_DOWNGRADED,
    OUTCOME_EXECUTED,
    PROVIDER_GEMINI,
    TIER_BOSS,
    TIER_MANAGER,
    _extract_json_dict,
    route_ai_task,
    ProviderCallResult,
)
from storage.models import (
    AIAgentRun,
    AIBudgetCounter,
    AIBudgetLimit,
    AIEscalation,
    AIRequest,
    AIRequestAttempt,
    AITaskPolicy,
    AuthUser,
    TeamMember,
    Tenant,
    Workspace,
)
from storage.sqlite_db import async_session, init_db
from utils.helpers import utcnow

_SEQ = 0


async def _reset_ai_state() -> None:
    async with async_session() as session:
        async with session.begin():
            for model in [
                AIAgentRun,
                AIBudgetCounter,
                AIBudgetLimit,
                AIEscalation,
                AIRequestAttempt,
                AIRequest,
                AITaskPolicy,
                TeamMember,
                Workspace,
                Tenant,
                AuthUser,
            ]:
                await session.execute(delete(model))


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AI_DEFAULT_MODE", "hybrid")
    monkeypatch.setattr(settings, "AI_ALLOWED_PROVIDER_ORDER", "gemini_direct,openrouter")
    monkeypatch.setattr(settings, "AI_MANAGER_MODELS", "gemini_direct:gemini-3-pro-preview")
    monkeypatch.setattr(settings, "AI_WORKER_MODELS", "gemini_direct:gemini-3-flash-preview")
    monkeypatch.setattr(settings, "AI_BOSS_MODELS", "openrouter:openai/gpt-5.4,gemini_direct:gemini-3-pro-preview")
    monkeypatch.setattr(settings, "AI_DAILY_BUDGET_USD", 100.0)
    monkeypatch.setattr(settings, "AI_MONTHLY_BUDGET_USD", 500.0)
    monkeypatch.setattr(settings, "AI_BOSS_DAILY_BUDGET_USD", 5.0)
    monkeypatch.setattr(settings, "AI_HARD_STOP_ENABLED", True)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    await init_db()
    await _reset_ai_state()
    yield
    await _reset_ai_state()


async def _tenant_scope() -> tuple[int, int, int]:
    global _SEQ
    _SEQ += 1
    async with async_session() as session:
        async with session.begin():
            user = AuthUser(email=f"router-{_SEQ}@example.com")
            session.add(user)
            await session.flush()
            tenant = Tenant(name=f"Router Tenant {_SEQ}", slug=f"router-tenant-{_SEQ}")
            session.add(tenant)
            await session.flush()
            workspace = Workspace(tenant_id=tenant.id, name="Main")
            session.add(workspace)
            await session.flush()
            session.add(TeamMember(tenant_id=tenant.id, workspace_id=workspace.id, user_id=user.id, role="owner"))
            await session.flush()
            return int(user.id), int(tenant.id), int(workspace.id)


@pytest.mark.asyncio
async def test_route_ai_task_records_usage_with_gemini_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, tenant_id, workspace_id = await _tenant_scope()

    async def fake_gemini(**_: object) -> ProviderCallResult:
        return ProviderCallResult(
            ok=True,
            provider=PROVIDER_GEMINI,
            model_name="gemini-3-pro-preview",
            parsed={"reply": "ok"},
            latency_ms=123,
            prompt_tokens=111,
            completion_tokens=37,
            estimated_cost_usd=0.0012,
        )

    monkeypatch.setattr("core.ai_router._call_gemini_json", fake_gemini)

    async with async_session() as session:
        async with session.begin():
            result = await route_ai_task(
                session,
                task_type="assistant_reply",
                prompt="TEST",
                system_instruction="Return JSON",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                surface="assistant",
            )
            assert result.ok is True
            assert result.outcome == OUTCOME_EXECUTED
            assert result.provider == PROVIDER_GEMINI
            assert result.executed_tier == TIER_MANAGER

        requests = (await session.execute(select(AIRequest))).scalars().all()
        counters = (await session.execute(select(AIBudgetCounter))).scalars().all()
        assert len(requests) == 1
        assert requests[0].prompt_tokens == 111
        assert requests[0].estimated_cost_usd == pytest.approx(0.0012)
        assert len(counters) == 2  # daily + monthly


@pytest.mark.asyncio
async def test_boss_budget_downgrades_to_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, tenant_id, workspace_id = await _tenant_scope()
    monkeypatch.setattr(settings, "AI_BOSS_DAILY_BUDGET_USD", 1.0)

    async with async_session() as session:
        async with session.begin():
            session.add(
                AIRequest(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    surface="assistant",
                    task_type="previous_boss_task",
                    agent_name="CEO Strategy Agent",
                    requested_model_tier=TIER_BOSS,
                    executed_model_tier=TIER_BOSS,
                    executed_provider=PROVIDER_GEMINI,
                    executed_model="gemini-3-pro-preview",
                    status="succeeded",
                    outcome=OUTCOME_EXECUTED,
                    estimated_cost_usd=1.5,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                    completed_at=utcnow(),
                )
            )

    async def fake_gemini(**_: object) -> ProviderCallResult:
        return ProviderCallResult(
            ok=True,
            provider=PROVIDER_GEMINI,
            model_name="gemini-3-pro-preview",
            parsed={"summary": "ok"},
            latency_ms=80,
            prompt_tokens=10,
            completion_tokens=20,
            estimated_cost_usd=0.01,
        )

    monkeypatch.setattr("core.ai_router._call_gemini_json", fake_gemini)

    async with async_session() as session:
        async with session.begin():
            result = await route_ai_task(
                session,
                task_type="campaign_strategy_summary",
                prompt="TEST",
                system_instruction="Return JSON",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                surface="assistant",
                policy_context={"manual_boss_approval": True},
            )
            assert result.ok is True
            assert result.outcome == OUTCOME_DOWNGRADED
            assert result.executed_tier == TIER_MANAGER
            assert result.provider == PROVIDER_GEMINI


@pytest.mark.asyncio
async def test_monthly_budget_blocks_request(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, tenant_id, workspace_id = await _tenant_scope()
    monkeypatch.setattr(settings, "AI_MONTHLY_BUDGET_USD", 1.0)
    monkeypatch.setattr(settings, "AI_HARD_STOP_ENABLED", True)

    async with async_session() as session:
        async with session.begin():
            session.add(
                AIRequest(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    surface="assistant",
                    task_type="previous_task",
                    agent_name="COO Operations Agent",
                    requested_model_tier=TIER_MANAGER,
                    executed_model_tier=TIER_MANAGER,
                    executed_provider=PROVIDER_GEMINI,
                    executed_model="gemini-3-pro-preview",
                    status="succeeded",
                    outcome=OUTCOME_EXECUTED,
                    estimated_cost_usd=1.1,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                    completed_at=utcnow(),
                )
            )

    async def fail_if_called(**_: object) -> ProviderCallResult:
        raise AssertionError("provider must not be called when budget blocks request")

    monkeypatch.setattr("core.ai_router._call_gemini_json", fail_if_called)

    async with async_session() as session:
        async with session.begin():
            result = await route_ai_task(
                session,
                task_type="assistant_reply",
                prompt="TEST",
                system_instruction="Return JSON",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                surface="assistant",
            )
            assert result.ok is False
            assert result.outcome == OUTCOME_BLOCKED
            assert result.reason_code == "monthly_budget_exceeded"


@pytest.mark.asyncio
async def test_openrouter_fallback_executes_when_gemini_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, tenant_id, workspace_id = await _tenant_scope()
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(settings, "AI_MANAGER_MODELS", "gemini_direct:gemini-3-pro-preview,openrouter:openai/gpt-5.4")

    async def fail_gemini(**_: object) -> ProviderCallResult:
        return ProviderCallResult(
            ok=False,
            provider=PROVIDER_GEMINI,
            model_name="gemini-3-pro-preview",
            parsed=None,
            reason_code="timeout",
        )

    async def ok_openrouter(**_: object) -> ProviderCallResult:
        return ProviderCallResult(
            ok=True,
            provider="openrouter",
            model_name="openai/gpt-5.4",
            parsed={"reply": "boss-approved"},
            latency_ms=220,
            prompt_tokens=50,
            completion_tokens=60,
            estimated_cost_usd=0.12,
        )

    monkeypatch.setattr("core.ai_router._call_gemini_json", fail_gemini)
    monkeypatch.setattr("core.ai_router._call_openrouter_json", ok_openrouter)

    async with async_session() as session:
        async with session.begin():
            result = await route_ai_task(
                session,
                task_type="assistant_reply",
                prompt="TEST",
                system_instruction="Return JSON",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                surface="assistant",
            )
            assert result.ok is True
            assert result.provider == "openrouter"
            assert result.fallback_used is True


def test_extract_json_dict_repairs_common_model_noise() -> None:
    assert _extract_json_dict("```json\n{\"reply\":\"ok\",}\n```") == {"reply": "ok"}
    assert _extract_json_dict("{'reply': 'ok', 'count': 2}") == {"reply": "ok", "count": 2}


@pytest.mark.asyncio
async def test_boss_policy_requires_manual_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, tenant_id, workspace_id = await _tenant_scope()

    async def fail_if_called(**_: object) -> ProviderCallResult:
        raise AssertionError("provider must not be called without boss approval")

    monkeypatch.setattr("core.ai_router._call_gemini_json", fail_if_called)
    monkeypatch.setattr("core.ai_router._call_openrouter_json", fail_if_called)

    async with async_session() as session:
        async with session.begin():
            result = await route_ai_task(
                session,
                task_type="campaign_strategy_summary",
                prompt="TEST",
                system_instruction="Return JSON",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                surface="assistant",
            )
            assert result.ok is False
            assert result.outcome == OUTCOME_BLOCKED
            assert result.requested_tier == TIER_BOSS
            assert result.reason_code == "boss_approval_required"
