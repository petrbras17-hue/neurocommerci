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

## Project Skills

The project includes custom Claude Code skills in `.claude/skills/`. Use `/skill-name` to invoke them directly.

### Built-in Project Skills

| Skill | Path | Purpose |
|-------|------|---------|
| `/sprint-context` | `.claude/skills/sprint-context/` | Load Scrum, sprint, VPS, and ledger context before any work. |
| `/neuro-ops` | `.claude/skills/neuro-ops/` | VPS operations: deploy, monitor, troubleshoot, query DB, view logs, check health. |
| `/channel-ops` | `.claude/skills/channel-ops/` | Channel map: parse, enrich, refresh, export, import channels. |
| `/check-status` | `.claude/skills/check-status/` | Quick account and system status check. |
| `/compile-all` | `.claude/skills/compile-all/` | Compile-check all Python and TypeScript. |
| `/vps-deploy` | `.claude/skills/vps-deploy/` | VPS deployment workflow. |
| `/account-lifecycle` | `.claude/skills/account-lifecycle/` | Account lifecycle management. |
| `/proxy-management` | `.claude/skills/proxy-management/` | Proxy health and rotation. |
| `/telegram-parser` | `.claude/skills/telegram-parser/` | Telegram channel parsing. |
| `/social-parser` | `.claude/skills/social-parser/` | Social media parsing. |
| `/web-parser` | `.claude/skills/web-parser/` | Web content parsing. |

### Recommended External Skills (install via `npx skills add`)

These community and official skills complement the project stack:

```bash
# Security audit (Trail of Bits — static analysis, code auditing)
npx skills add trailofbits/skills

# PostgreSQL best practices (Supabase)
npx skills add supabase/agent-skills

# React/frontend patterns (Vercel)
npx skills add vercel-labs/agent-skills

# Web app testing via Playwright (Anthropic official)
npx skills add anthropics/skills --skill webapp-testing

# Frontend design (Anthropic official)
npx skills add anthropics/skills --skill frontend-design

# Docker validation & security
npx skills add jezweb/claude-skills

# Code review + FastAPI + React patterns (Beagle)
npx skills add existential-birds/beagle

# Superpowers skills library (20+ battle-tested skills)
npx skills add obra/superpowers
```

### Recommended MCP Servers

```bash
# Playwright browser automation (already configured)
claude mcp add playwright -- npx @anthropic-ai/mcp-playwright

# Telegram Bot MCP (send messages, manage chats)
claude mcp add telegram -- npx @anthropic-ai/mcp-telegram

# Context7 — version-accurate docs lookup
claude mcp add context7 -- npx @anthropic-ai/mcp-context7

# Hound — supply chain security scanning
claude mcp add hound -- npx @anthropic-ai/mcp-hound
```

### Skill Installation Notes

- Personal skills: `~/.claude/skills/<name>/SKILL.md` (all projects)
- Project skills: `.claude/skills/<name>/SKILL.md` (this project only)
- External skills install to project `.claude/skills/` by default
- Use `npx skills add <repo> -g` for global install
- Use `npx skills add <repo> --list` to preview before installing
- Always review external skill code before installing

## Legacy Note
- Older project files and some existing `.claude/agents/` entries describe the historical Telegram runtime.
- For SaaS sprints, prefer the new Scrum and Sprint files imported above.
