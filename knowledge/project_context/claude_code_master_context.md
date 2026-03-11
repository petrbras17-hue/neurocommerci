# Claude Code Master Context

Last updated: 2026-03-11
Repo root: `/Users/braslavskii/NEURO COMMENTING`

This file is the consolidated handoff for Claude Code. It compresses the current repo state, sprint history, VPS status, recent local work, and the safest next steps into one place.

## 1. What This Project Is

NEURO COMMENTING started as a Telegram commenting/control-plane system and is being rebuilt into a multi-tenant SaaS.

Current product direction:
- Telegram Growth OS for RU/CIS mid-market brands
- Python-first stack
- FastAPI + SQLAlchemy + Alembic + PostgreSQL + Redis + Docker Compose
- React/Vite web shell mounted inside the FastAPI app

Important product split:
- legacy Telegram runtime still exists in the repo
- new SaaS shell, auth, assistant, and AI routing layers are the active product direction
- do not introduce a second backend/frontend stack unless explicitly required

## 2. Source-Of-Truth Read Order

Read these in this order before making changes:
1. `CLAUDE.md`
2. `knowledge/project_context/claude_code_master_context.md`
3. `knowledge/project_context/change_register.md`
4. `README.md`
5. `RUNBOOK.md`
6. `knowledge/project_context/sprint3_4_live_audit.md`
7. `knowledge/project_context/ai_orchestrator_foundation.md`

Optional deeper context:
- `knowledge/project_context/claude_saas_scrum_master.md`
- `knowledge/project_context/deployment-runbook.md`
- `ops/DEPLOY_AEZA.md`
- `knowledge/project_context/account_onboarding_runbook.md`
- `knowledge/project_context/account_onboarding_memory.md`

## 3. Current Repo Ground Truth

Current local branch:
- `sprint/3-telegram-first-auth-shell`

Current HEAD:
- `c37798e` - `Harden AI router JSON contracts and model routing`
- commit timestamp: 2026-03-10 09:53 +0400

Last committed milestone before that:
- `c483d56` - `Add hybrid OpenRouter AI orchestrator foundation`
- commit timestamp: 2026-03-10 05:32 +0400

Current local working tree:
- dirty
- 40 tracked files modified vs `HEAD`
- diff summary: about 7,954 insertions and 1,634 deletions
- many untracked project files also exist: new scripts, docs, migration `20260310_08`, Claude helpers, ops helpers, and runtime artifacts

Interpretation:
- the repo contains a substantial amount of local work after the last commit
- this work is not fully committed and not cleanly packaged for release yet

## 4. Current VPS Ground Truth

Official safe baseline recorded in project memory:
- branch: `sprint/1-tenant-foundation`
- commit: `2c3c516`
- deploy path: `/opt/neuro-commenting`
- deploy mode: git checkout

Safe baseline services:
- `db`
- `redis`
- `ops_api`
- `bot`

Paused outside the safe baseline:
- `packager`
- `worker_a`
- `worker_b`

Critical nuance:
- there is evidence of a live Sprint 3/4 public audit on the VPS/public domain
- however the change register still marks the official safe baseline as Sprint 1
- therefore the safest interpretation is:
  - Sprint 3/4 surfaces were live-tested in isolation
  - final promotion/deploy of the latest local changes was not completed

## 5. Where Work Most Likely Stopped

The final concentrated coding burst on 2026-03-10 appears to be the Sprint 4 stabilization pass.

Key local file timestamps from that pass:
- `2026-03-10 14:16` - `alembic/versions/20260310_08_ai_jobs_and_quality.py`
- `2026-03-10 14:30` - `core/task_queue.py`
- `2026-03-10 14:31` - `core/ai_audit.py`
- `2026-03-10 14:34` - `core/assistant_jobs.py`
- `2026-03-10 14:35` - `core/ai_router.py`
- `2026-03-10 14:38` - `frontend/src/api.ts`

Later context/documentation activity:
- `2026-03-10 15:43` - `knowledge/project_context/account_onboarding_memory.md`
- `2026-03-11 01:06` to `01:08` - new Claude agents/commands were added under `.claude/`

Practical conclusion:
- the last engineering focus was AI stabilization, assistant job queueing, quality telemetry, and release tooling for Claude Code
- there is no evidence in the repo that these final changes were fully deployed to the VPS safe baseline

## 6. Sprint Timeline

### Pre-SaaS legacy track

Legacy Telegram control-plane, parser, worker, packager, recovery, proxy, and anti-fraud logic still exist in the repo. They matter operationally, but they are not the primary product direction for the SaaS rebuild.

### Sprint 1

Status:
- complete
- deployed and verified on VPS

Delivered:
- FastAPI `ops_api`
- tenant/workspace primitives
- JWT tenant auth
- internal ops token compatibility
- PostgreSQL RLS foundations
- Alembic migrations
- env profiles
- Sprint 1 tests

Verified facts:
- Postgres app role `nc` is `NOSUPERUSER`
- Postgres app role `nc` is `NOBYPASSRLS`
- `pytest tests/test_tenant_foundation.py -v` passed on VPS

### Sprint 2

Status:
- implemented locally
- not marked as finally deployed in the ledger

Delivered:
- public marketing landing routes
- lead capture API
- Google Sheets mirror for leads
- Telegram lead/digest delivery
- internal lead summary endpoint

Relevant files:
- `README.md`
- `ops_api.py`
- `core/lead_funnel.py`
- `alembic/versions/20260309_02_marketing_leads.py`

### Sprint 3

Status:
- implemented locally
- publicly live-audited
- not promoted as safe VPS baseline

Delivered:
- Telegram-first auth
- refresh token cookies
- React/Vite workspace shell
- tenant-scoped web onboarding for accounts and proxies

Relevant routes:
- `/app`
- `/app/login`
- `/auth/telegram/verify`
- `/auth/complete-profile`
- `/auth/refresh`
- `/auth/logout`
- `/auth/me`
- `/v1/web/accounts*`
- `/v1/web/proxies/available`

Relevant files:
- `ops_api.py`
- `core/web_auth.py`
- `core/web_accounts.py`
- `frontend/src/*`
- `alembic/versions/20260310_03_sprint3_auth_shell.py`
- `alembic/versions/20260310_04_auth_bootstrap_rls.py`
- `alembic/versions/20260310_05_rls_nullif_settings.py`

### Sprint 3 + 4 prep

Status:
- locally implemented
- live-audited in isolated mode
- still pending clean release promotion

Delivered:
- operator-first dashboard/accounts UX
- account notes and timeline
- assistant/context/creative surfaces
- Google Sheets mirror on context confirm
- Telegram digest integration on context confirm
- frontend crash fix for structured creative variants

Relevant files:
- `core/assistant_service.py`
- `core/digest_service.py`
- `frontend/src/pages/AssistantPage.tsx`
- `frontend/src/pages/ContextPage.tsx`
- `frontend/src/pages/CreativePage.tsx`
- `frontend/src/pages/DashboardPage.tsx`
- `knowledge/project_context/sprint3_4_live_audit.md`

### Sprint 4 foundation

Status:
- committed in `c483d56`
- local stabilization continued after the commit
- not marked as finally deployed

Delivered:
- hybrid Gemini + OpenRouter AI router
- boss / manager / worker model tiers
- centralized policy-aware model routing
- budget guardrails
- AI telemetry entities
- assistant/creative integration through one backend layer

Relevant files:
- `core/ai_router.py`
- `core/ai_orchestrator.py`
- `storage/models.py`
- `tests/test_ai_router.py`
- `knowledge/project_context/ai_orchestrator_foundation.md`
- `alembic/versions/20260310_07_ai_orchestrator_foundation.py`

### Sprint 4 stabilization

Status:
- partially committed in `c37798e`
- more local work remains uncommitted
- not deployed as a stable VPS release

Delivered or in progress:
- stronger JSON contracts
- safer model ordering for manager/worker tasks
- Kimi removed from strict JSON routes
- assistant job queue entity `app_jobs`
- AI quality summary helpers
- internal AI audit endpoint
- additional quality telemetry columns

Relevant files:
- `alembic/versions/20260310_08_ai_jobs_and_quality.py`
- `core/assistant_jobs.py`
- `core/ai_audit.py`
- `core/task_queue.py`
- `ops_api.py`
- `frontend/src/api.ts`

## 7. Current Architecture Map

### Control plane

Primary files:
- `main.py`
- `admin/bot_admin.py`
- `core/engine.py`
- `core/ops_service.py`

### SaaS API / web shell

Primary files:
- `ops_api.py`
- `core/web_auth.py`
- `core/web_accounts.py`
- `core/assistant_service.py`
- `frontend/src/`

### Execution plane

Primary files:
- `worker.py`
- `packager_worker.py`
- `recovery_worker.py`
- `parser_service.py`
- `scheduler_service.py`

### State and storage

Primary files:
- `storage/models.py`
- `storage/sqlite_db.py`
- `core/task_queue.py`
- Postgres and Redis via Docker Compose

### Operational docs

Primary files:
- `README.md`
- `RUNBOOK.md`
- `QUICK_START.md`
- `knowledge/project_context/change_register.md`
- `knowledge/project_context/deployment-runbook.md`
- `ops/DEPLOY_AEZA.md`

## 8. What Changed Locally Beyond HEAD

The current dirty working tree includes four main buckets:

### 1. AI stabilization and observability

- new migration `20260310_08_ai_jobs_and_quality.py`
- `app_jobs` table
- added quality fields on `ai_requests` and `ai_request_attempts`
- new `core/assistant_jobs.py`
- new `core/ai_audit.py`
- queue and audit endpoints in `ops_api.py`

### 2. Assistant shell + frontend wiring

- `frontend/src/api.ts`
- `frontend/src/pages/AssistantPage.tsx`
- `frontend/src/pages/ContextPage.tsx`
- `frontend/src/pages/CreativePage.tsx`
- `frontend/src/pages/DashboardPage.tsx`

### 3. Large legacy/runtime edits still present locally

- `admin/bot_admin.py`
- `worker.py`
- `packager_worker.py`
- `channels/*`
- `comments/*`
- `core/account_manager.py`
- `core/engine.py`
- `utils/channel_setup.py`

These may span more than one sprint and should be reviewed carefully before any release.

### 4. Project memory / Claude tooling

- `.claude/agents/*`
- `.claude/commands/*`
- `.claude/skills/sprint-context/SKILL.md`
- `knowledge/project_context/*`

## 9. Verified Locally On 2026-03-11

Tests run through the project virtualenv:

- `./.venv/bin/pytest tests/test_ai_router.py -q` -> `6 passed`
- `./.venv/bin/pytest tests/test_web_assistant.py -q` -> `4 passed`

Warnings observed:
- Python 3.9 end-of-life warning from `google-auth`
- `urllib3` LibreSSL warning on local macOS Python build

Meaning:
- the current AI router and web assistant suites pass in the local virtualenv
- do not assume broader repo health from these two suites alone

## 10. What Is Very Likely Not On The VPS Safe Baseline

Unless proven otherwise by a fresh VPS audit, assume these are still local-only or only partially live-tested:

- `20260310_08` migration
- `app_jobs` queue-backed assistant processing
- `/v1/jobs/{job_id}`
- `/v1/ai/quality-summary`
- `/v1/internal/ai/audit`
- latest AI router JSON hardening from `c37798e`
- local post-`c37798e` dirty-tree AI stabilization changes
- latest frontend wiring related to job polling / audit visibility

## 11. Safe Next Release Checklist

Before any VPS rollout:
1. review `git status` and separate intentional source changes from runtime artifacts
2. commit or consciously shelve the intended Sprint 4 stabilization patch set
3. confirm VPS env contains `OPENROUTER_API_KEY` and the expected AI env vars
4. run `alembic upgrade head`
5. rebuild frontend assets if the deploy path serves `frontend/dist`
6. restart only the necessary services first: `ops_api`, `bot`
7. keep `packager` and workers paused unless the release explicitly needs them
8. run live smoke:
   - `/auth/me`
   - `/app/assistant`
   - start brief
   - assistant message
   - context confirm
   - creative generate
   - creative approve
   - quality summary endpoint
   - internal audit endpoint if allowed

## 12. Hard Constraints For Future Claude Sessions

Do not break these:
- keep Python/FastAPI as the primary app stack
- keep DB changes in Alembic
- keep tenant isolation strict
- do not bypass RLS assumptions
- do not enable Telegram-side account execution from assistant surfaces
- keep Google Sheets and Telegram digest as sinks, not source of truth
- keep boss-tier AI actions policy-gated when applicable
- do not treat parser/comment runtime as safe to auto-expand without explicit approval

## 13. Recommended First Message From Claude Code

When starting a new Claude Code session, it should first confirm:
- current branch
- `HEAD` commit
- dirty working tree status
- official VPS safe baseline
- whether Sprint 3/4 audit deployment exists separately from the baseline
- what exactly is pending deploy

If Claude cannot verify one of these facts from the repo, it should say so explicitly instead of guessing.
