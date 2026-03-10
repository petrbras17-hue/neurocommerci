# NEURO COMMENTING — Change Register

This is the human-readable delivery ledger. Update it after each sprint or meaningful VPS change.

## Live Status

| Field | Value |
|---|---|
| Current local branch | `sprint/3-telegram-first-auth-shell` |
| Last committed HEAD | `c37798e` |
| VPS safe branch | `sprint/1-tenant-foundation` |
| VPS safe commit | `2c3c516` |
| VPS deploy path | `/opt/neuro-commenting` |
| VPS deploy mode | `git checkout` |
| Safe baseline services | `db`, `redis`, `ops_api`, `bot` |
| Paused outside safe baseline | `packager`, `worker_a`, `worker_b` |
| Current completed sprint | `Sprint 4 foundation` |
| Next planned sprint | `AI orchestrator live rollout` |

## Delivery Ledger

| Date | Sprint | Branch | Commit | Area | Summary | Local Status | VPS Status | Next Step |
|---|---|---|---|---|---|---|---|---|
| 2026-03-08 | Sprint 1 | `sprint/1-tenant-foundation` | `f335b1d` | Tenant foundation | Initial FastAPI + Alembic + RLS implementation landed. | Green | Not yet | Fix transaction-scoped tenant lookup and validate on VPS. |
| 2026-03-08 | Sprint 1 | `sprint/1-tenant-foundation` | `ecfa4fb` | RLS correctness | Tenant lookup moved inside active transaction before RLS reads. | Green | Not yet | Deploy Sprint 1 to VPS. |
| 2026-03-09 | Sprint 1 | `sprint/1-tenant-foundation` | `2c3c516` | VPS baseline | Added healthcheck script, converted VPS to git checkout, fixed Postgres role, applied Alembic, verified tests on VPS. | Green | Green | Start Sprint 2: public landing + lead capture. |
| 2026-03-09 | Sprint 2 | `sprint/1-tenant-foundation` | `working-tree` | Premium lead funnel | Upgraded the marketing site to a premium SaaS landing, added Google Sheets + Telegram lead delivery, and added an internal lead summary endpoint. | Green | Not deployed | Commit Sprint 2 v2 and roll it out to VPS. |
| 2026-03-10 | Sprint 3 | `sprint/1-tenant-foundation` | `working-tree` | Telegram-first workspace shell | Added Telegram-first auth, refresh-token cookies, React/Vite workspace shell, and tenant-scoped web onboarding for accounts/proxies. | Green | Not deployed | Commit Sprint 3 and deploy after BotFather `/setdomain` is confirmed. |
| 2026-03-10 | Sprint 3 + 4 prep | `working-tree` | `working-tree` | Operator shell + AI assistant layer | Finished operator-first dashboard/accounts UX, added account notes/timeline, and introduced Gemini-first assistant/context/creative backend + React surfaces. | Green | Not deployed | Commit this safe-shell + assistant layer and deploy after branch review. |
| 2026-03-10 | Sprint 3/4 audit | `working-tree` | `working-tree` | Assistant digest integration | Added digest delivery on context confirmation and included company in brief mirror payload to enable isolated live integration audit. | Green | Pending deploy | Deploy this assistant integration patch and run live Gemini + Sheets + digest verification. |
| 2026-03-10 | Sprint 3/4 audit | `working-tree` | `working-tree` | Creative page variant rendering fix | Fixed `/app/creative` to render structured draft variants without React runtime crashes on populated assistant tenants. | Green | Pending deploy | Redeploy frontend bundle and re-run public populated UI smoke. |
| 2026-03-10 | Sprint 4 foundation | `working-tree` | `working-tree` | AI orchestrator | Added a hybrid Gemini + OpenRouter router with boss/manager/worker tiers, budget guardrails, telemetry tables, and assistant/creative integration via a single policy-aware router. | Green | Not deployed | Set `OPENROUTER_API_KEY`, deploy migration + backend, then run live routed AI audit on VPS. |
| 2026-03-10 | Sprint 4 stabilization | `sprint/3-telegram-first-auth-shell` | `working-tree` | Structured output hardening | Tightened JSON contracts, moved reliable OpenRouter models earlier for manager/worker tasks, and excluded Kimi from strict JSON routes until a dedicated loose-output profile exists. | Green | Pending deploy | Commit, deploy to VPS, then run live assistant -> context -> creative audit with cost trace. |
| 2026-03-11 | Sprint 4 security patch | `sprint/3-telegram-first-auth-shell` | `working-tree` | Tenant isolation + hardcoded creds | Fixed cross-tenant refresh token/logout/get_me in web_auth.py, removed hardcoded DB password defaults from docker-compose.yml, added cross-tenant isolation test. | Green | Pending deploy | Commit stabilization patch, run full test suite, then deploy to VPS. |

## Update Rules

After each sprint or major VPS change:
1. add a new row to this ledger
2. update the `Live Status` table if branch/commit/baseline changed
3. keep the summary to one sentence
4. point `Next Step` to the next actionable handoff
