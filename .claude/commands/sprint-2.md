Implement Sprint 2 only.

Read first:
- @CLAUDE.md
- @knowledge/project_context/claude_saas_scrum_master.md
- @knowledge/project_context/change_register.md
- @README.md
- @ops_api.py
- @storage/models.py
- @alembic/env.py
- @tests/test_tenant_foundation.py

Scope:
- public landing pages in FastAPI + Jinja2
- lead capture API and migration
- pages: `/`, `/ecom`, `/edtech`, `/saas`
- basic SEO routes

Rules:
- Python-only
- do not add Next.js, Node.js, or Prisma
- do not touch Telegram runtime flows
- `leads` is platform-level, not tenant-scoped
- public routes must stay outside tenant/subscription enforcement
- stay inside Sprint 2 scope

At the end:
- run compile checks
- run Sprint 1 tests
- run Sprint 2 tests
- update @knowledge/project_context/change_register.md
- summarize changed files
- provide a short VPS deploy note
