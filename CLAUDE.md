# NEURO COMMENTING

Team-shared Claude Code memory for the SaaS rebuild.

Start here on every session:
- Read @knowledge/project_context/claude_code_master_context.md for the latest consolidated repo + VPS + sprint handoff.
- Read @knowledge/project_context/claude_code_prompts.md for ready-to-use Claude Code prompts.
- Read @README.md for current repo setup.
- Read @knowledge/project_context/claude_saas_scrum_master.md for the full Scrum, VPS, sprint, and product context.
- Read @knowledge/project_context/change_register.md for the live delivery ledger.

## Current Ground Truth
- Product direction: multi-tenant SaaS "Telegram Growth OS" for RU/CIS mid-market brands.
- Active local branch: `sprint/3-telegram-first-auth-shell`
- Last committed HEAD: `c37798e` (Harden AI router JSON contracts and model routing)
- Sprints 1-4 foundation implemented locally; Sprint 1 deployed and verified on VPS.
- Safe production baseline on VPS:
  - path: `/opt/neuro-commenting`
  - deploy mode: git checkout
  - branch: `sprint/1-tenant-foundation`
  - commit: `2c3c516`
- Current safe services: `db`, `redis`, `ops_api`, `bot`.
- `packager`, `worker_a`, and `worker_b` are intentionally not part of the Sprint 1 baseline.
- Next deploy target: Sprint 4 stabilization patch (pending commit + VPS rollout).

## Working Rules
- Default stack for upcoming SaaS sprints: Python + FastAPI + SQLAlchemy + Alembic + PostgreSQL + Redis.
- Do not introduce a second app stack unless the sprint explicitly requires it.
- Keep all new database changes in Alembic migrations.
- Keep tenant isolation strict: every SaaS query must be tenant-safe via RLS or scoped ORM access.
- Public marketing routes and pre-signup lead capture are platform-level and not tenant-scoped.
- Treat old Telegram anti-ban and appeal automation as legacy context, not as the default SaaS direction.

## Scrum Rules
- Stay within the current sprint scope.
- Before coding:
  - confirm current branch and commit,
  - read the sprint section in the Scrum master file,
  - check the change register for the latest status and blockers.
- After coding:
  - run relevant tests,
  - update the change register,
  - leave a short deploy note if the sprint changes VPS behavior.

## Project Helpers
- Use the project slash commands in `.claude/commands/`.
- Prefer the project agents in `.claude/agents/` for sprint delivery, QA, and VPS audits.
- Prefer the project skill `.claude/skills/sprint-context/` when the task is about loading or refreshing sprint context.

## Legacy Note
- Older project files and some existing `.claude/agents/` entries describe the historical Telegram runtime.
- For SaaS sprints, prefer the new Scrum and Sprint files imported above.
