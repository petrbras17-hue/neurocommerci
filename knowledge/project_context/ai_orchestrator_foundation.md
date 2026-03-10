# AI Orchestrator Foundation

Date: 2026-03-10

## Goal

Build a hybrid AI orchestration layer for NEURO COMMENTING where:

- `gemini_direct` remains the fast default provider
- `openrouter` becomes the boss/manager/fallback plane
- all assistant/context/creative model calls route through one policy-aware backend layer
- budget and downgrade decisions are enforced centrally

## Current hierarchy

- `boss`
  - strategic synthesis
  - contradiction resolution
  - high-stakes review
  - requires policy/approval where configured
- `manager`
  - assistant replies
  - creative generation
  - synthesis and review
- `worker`
  - brief extraction
  - parser suggestions
  - low-cost structured tasks

## Source of truth

- Postgres is the source of truth
- Google Sheets and Telegram digest are sinks only

## Current task routing defaults

- `brief_extraction` -> `worker`
- `assistant_reply` -> `manager`
- `creative_variants` -> `manager`
- `parser_query_suggestions` -> `worker`
- `campaign_strategy_summary` -> `boss` with manual approval requirement
- `weekly_marketing_report` -> `manager`

## Providers

- `gemini_direct`
- `openrouter`

## Current production-first model matrix

- `boss`
  - `openrouter:openai/gpt-5.4`
  - `openrouter:anthropic/claude-opus-4.6`
- `manager`
  - `openrouter:anthropic/claude-sonnet-4.6`
  - `openrouter:google/gemini-2.5-pro`
  - `gemini_direct:gemini-3-pro-preview`
- `worker`
  - `openrouter:google/gemini-2.5-flash`
  - `gemini_direct:gemini-3-flash-preview`

`moonshotai/kimi-k2.5` is intentionally excluded from strict JSON routes for now and should only return after a dedicated loose-output validation pass.

## Outcomes

- `executed_as_requested`
- `downgraded_by_budget_policy`
- `blocked_by_budget_policy`

## Budget controls

- `AI_DAILY_BUDGET_USD`
- `AI_MONTHLY_BUDGET_USD`
- `AI_BOSS_DAILY_BUDGET_USD`
- `AI_HARD_STOP_ENABLED`

## Telemetry entities

- `ai_model_profiles`
- `ai_task_policies`
- `ai_requests`
- `ai_request_attempts`
- `ai_budget_limits`
- `ai_budget_counters`
- `ai_escalations`
- `ai_agent_runs`

## Integration scope

Integrated now:

- `/app/assistant`
- `/app/context`
- `/app/creative`

Not integrated yet:

- Telegram-side account actions
- packager/worker execution
- legacy comment runtime

## Operational note

After live OpenRouter rollout:

1. keep boss-tier gated by manual approval/policy
2. monitor `json_parse_failed` by provider/model
3. confirm worker/manager routes stay under daily budget caps
4. verify:
   - provider chosen
   - executed model tier
   - estimated cost
   - fallback_used
   - Sheets mirror
   - digest notification
