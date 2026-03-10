# NEURO COMMENTING — Claude SaaS Scrum Master File

Last updated: 2026-03-11

This is the single long-form handoff file for Claude Code. It is intended to keep product, Scrum, VPS, and sprint context stable across sessions.

## 1. Product Definition

NEURO COMMENTING is being rebuilt as a multi-tenant SaaS product:
- positioning: Telegram Growth OS for RU/CIS mid-market brands
- deployment mode: VPS-first, git-based rollout
- current engineering stack: Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL, Redis, Docker Compose
- revenue model target: subscription + usage, then agency and enterprise expansion

## 2. Current Engineering Ground Truth

### Local repo
- active branch: `sprint/3-telegram-first-auth-shell`
- last committed HEAD: `c37798e` (Harden AI router JSON contracts and model routing)
- Sprint 1 verified commit: `2c3c516`
- Sprints 1-4 foundation implemented locally; Sprint 4 security patch pending commit

### VPS
- host: `176.124.221.253`
- user: `deploy`
- project path: `/opt/neuro-commenting`
- deploy mode: normal git checkout, not ad-hoc file copy
- current safe baseline services:
  - `db`
  - `redis`
  - `ops_api`
  - `bot`
- intentionally stopped outside safe Sprint 1 baseline:
  - `packager`
  - `worker_a`
  - `worker_b`

### Sprint 1 verified facts
- Sprint 1 is deployed and verified on VPS
- Postgres app role `nc` is `NOSUPERUSER` and `NOBYPASSRLS`
- Alembic migration is applied
- `pytest tests/test_tenant_foundation.py -v` passed on VPS

## 3. Scrum Operating Model

Use 2-week sprints. Each sprint should be handled in this order:

1. Read this file and the live change register.
2. Confirm current branch, commit, and VPS baseline.
3. Stay inside sprint scope. Do not add features from future sprints.
4. Implement only what the sprint asks for.
5. Run compile/tests.
6. Update the live change register.
7. If needed, leave a short VPS deploy note.

Definition of done for every sprint:
- code is committed
- tests for that sprint pass locally
- if deployed, VPS status is recorded in the ledger
- blockers and deferred items are explicitly documented

## 4. Sprint Roadmap

### Sprint 1 — Foundation & Tenant Primitives
Goal:
- freeze architecture
- add tenant/workspace primitives
- add usage event skeleton

Status:
- done
- deployed and verified on VPS

Delivered:
- FastAPI `ops_api`
- tenant JWT context
- internal token auth compatibility
- new tenant tables
- PostgreSQL RLS
- Alembic bootstrap
- usage event logger
- env profiles
- Sprint 1 tests

### Sprint 2 — Landing Page v1 + Lead Capture
Goal:
- build the public-facing marketing site
- add waitlist form and DB-backed lead capture

Recommended implementation constraints:
- Python-only
- FastAPI + Jinja2 templates
- no Next.js or second app stack
- `leads` is platform-level, not tenant-scoped
- public routes must bypass tenant auth and subscription enforcement

Expected deliverables:
- `/`
- `/ecom`
- `/edtech`
- `/saas`
- `POST /api/leads`
- `leads` table and migration
- basic SEO routes

### Sprint 3 — Authentication + Web Workspace Shell
Goal:
- add register/login/refresh/logout
- create user + tenant + default workspace
- add protected workspace shell

Expected deliverables:
- auth tables and token flow
- password hashing
- refresh token rotation
- basic protected web shell
- onboarding wizard

### Sprint 4 — Billing, Subscriptions, Plan Enforcement
Goal:
- enable first real customer payment
- enforce plan access and limits

Expected deliverables:
- plans
- subscriptions
- 14-day trial
- Stripe
- YooKassa
- plan enforcement middleware

### Sprint 5 — Campaigns + Parser Dashboard
Goal:
- user can create campaigns
- user can search channels and save them to campaigns
- user can review AI drafts

Expected deliverables:
- campaigns
- destinations
- campaign runs
- parser UI and saved searches
- drafts queue UI

### Sprint 6 — Analytics + Usage Dashboards
Goal:
- show ROI and product activation
- expose usage and KPI data to customers and internal ops

Expected deliverables:
- analytics events pipeline
- customer analytics dashboard
- usage meters
- internal activation report

### Sprint 7 — Scale Infrastructure
Goal:
- harden runtime for 20-40 concurrent paying customers

Expected deliverables:
- queue throughput hardening
- DLQ/retry discipline
- structured logs
- tenant isolation audit suite
- `/health` with DB/Redis/queue depth

### Sprint 8 — Agency Package Scaffolding
Goal:
- add agency data model and tenant-to-client scaffolding

Expected deliverables:
- agency client entities
- client workspace allocation model
- agency billing groundwork

### Sprint 9 — Team Collaboration + Governance
Goal:
- approvals and collaboration inside workspaces
- vertical parser templates

Expected deliverables:
- approval governance
- comments and task assignment
- niche templates and collaboration flows

### Sprint 10 — Agency Portal + Partner Revenue Share
Goal:
- build the first true agency-facing UI/API layer

Expected deliverables:
- agency portal UI
- partner revenue share logic
- contract billing API

### Sprint 11 — Full ROI + Attribution
Goal:
- enterprise-grade visibility into campaign outcomes

Expected deliverables:
- ROI dashboards
- attribution layer
- trend alerts

### Sprint 12 — Enterprise Packaging + Revenue Ops
Goal:
- make the product sellable to larger customers

Expected deliverables:
- enterprise packaging
- revenue ops dashboard
- sales and onboarding copy

## 5. What the Founder Must Provide

### Already needed now
- GitHub repo access
- VPS access
- bot tokens
- Gemini API key

### Needed by Sprint 2
- domain for the marketing site
- approved product copy:
  - headline
  - features
  - pricing
  - FAQ
- brand assets if available

### Needed by Sprint 4
- Stripe account and keys
- YooKassa account and keys
- legal/business billing details

### Likely needed later
- PostHog or alternative analytics decision
- Sentry DSN
- stronger VPS or extra node(s)
- customer support email/domain setup

## 6. Revenue Expectations

When revenue can start:
- Sprint 2-3: manual sales can start, because landing + lead capture + early product shell exist
- Sprint 4: first true SaaS revenue can start via subscriptions and trials
- Sprint 5-8: stronger repeatability, product value, and expansion motion

Practical path:
- early revenue from demos + manual onboarding
- recurring revenue begins after billing is live
- larger growth comes after campaigns, parser UI, analytics, and agency support exist

## 7. Recommended Claude Code Setup

Use official Claude Code features as the primary extension mechanism:

### Project memory
- root `CLAUDE.md`
- imported context files

### Project settings
- `.claude/settings.json`

### Project subagents
- `.claude/agents/`

### Project skills
- `.claude/skills/`

### Project slash commands
- `.claude/commands/`

### MCP / plugin layer
- use official Claude Code MCP/project plugin flow where helpful

Recommended built-in Claude Code commands to use often:
- `/memory`
- `/agents`
- `/mcp`
- `/model`
- `/config`
- `/hooks`
- `/review`
- `/status`
- `/ide`

### How to use them in practice
- `/memory` — inspect project memory and loaded `CLAUDE.md`
- `/agents` — inspect and launch project subagents from `.claude/agents/`
- `/mcp` — authenticate or inspect connected MCP servers
- `/model` — choose the current session model; for Opus work, select the latest Opus available in the account
- `/config` — inspect project settings and precedence

### Important scope model
- project-shared Claude settings live in `.claude/settings.json`
- local-only overrides should live in `.claude/settings.local.json`
- project-shared subagents live in `.claude/agents/`
- project-shared slash commands live in `.claude/commands/`
- project-shared skills live in `.claude/skills/`
- project memory lives in `CLAUDE.md`
- project-shared MCP config should live in `.mcp.json`

## 8. Recommended Official Plugins / MCP / Skills

Use these only if relevant and after reviewing their source or configuration.

### High-value official or official-directory picks
1. GitHub plugin
   - use for PRs, issues, workflow visibility
2. Context7 plugin
   - use for version-accurate docs lookup
3. CLAUDE.md Management plugin
   - use to keep project memory fresh
4. Security Guidance plugin
   - use for safe write/edit reminders
5. Sentry plugin
   - use later when production error monitoring matters
6. Plugin Developer Toolkit
   - use if this project later publishes its own Claude plugin

### Core MCP capabilities to consider later
- GitHub MCP
- Sentry MCP
- docs lookup MCP
- internal project MCP only after strict review

### Practical install/use notes
- Plugin discovery/install should be done from the official Claude plugin directory or from Claude Code plugin flows.
- MCP servers can be added from the terminal. Official patterns:
  - local stdio server:
    - `claude mcp add --transport stdio myserver -- npx server`
  - JSON config:
    - `claude mcp add-json my-server '{"type":"http","url":"https://mcp.example.com/mcp"}'`
  - import from Claude Desktop:
    - `claude mcp add-from-claude-desktop`
- After adding an MCP server, use `/mcp` inside Claude Code to authenticate and inspect it.

### Recommended plugin shortlist for this repo
- GitHub
- Context7
- CLAUDE.md Management
- Security Guidance
- Sentry
- Plugin Developer Toolkit
- Linear
- Playwright

### Official reference URLs
- Claude Code overview:
  - `https://docs.anthropic.com/en/docs/claude-code/overview`
- Claude Code subagents:
  - `https://docs.anthropic.com/en/docs/claude-code/sub-agents`
- Claude Code hooks:
  - `https://docs.anthropic.com/en/docs/claude-code/hooks`
- Claude Code MCP:
  - `https://docs.anthropic.com/en/docs/claude-code/mcp`
- Claude Code slash commands:
  - `https://docs.anthropic.com/en/docs/claude-code/slash-commands`
- Claude Code settings:
  - `https://code.claude.com/docs/en/settings`
- Claude plugin directory:
  - `https://claude.com/plugins`

## 9. Recommended Project Subagents

Prefer the new SaaS sprint agents in `.claude/agents/`:
- `sprint-product-manager`
- `saas-backend-implementer`
- `qa-tenant-auditor`
- `vps-release-auditor`

Legacy Telegram-focused agents may still exist in the repo. They should not be the default for SaaS sprint work.

## 10. Recommended First Prompts for Claude Code

### Prompt 1 — bootstrap context
Use this at the start of a session:

```text
Read CLAUDE.md, knowledge/project_context/claude_saas_scrum_master.md, and knowledge/project_context/change_register.md.
Then summarize:
1. current sprint status,
2. current VPS baseline,
3. branch and commit to work from,
4. the next highest-priority task,
5. anything that is intentionally out of scope.
Do not code yet.
```

### Prompt 2 — Sprint 2 execution
Use this for Sprint 2 implementation:

```text
You are continuing NEURO COMMENTING after Sprint 1.

Read:
- CLAUDE.md
- README.md
- knowledge/project_context/claude_saas_scrum_master.md
- knowledge/project_context/change_register.md
- ops_api.py
- storage/models.py
- alembic/env.py
- tests/test_tenant_foundation.py

Implement Sprint 2 only:
- public landing pages in FastAPI + Jinja2
- leads table via Alembic
- POST /api/leads
- pages: /, /ecom, /edtech, /saas
- basic SEO routes

Rules:
- Python-only
- do not add Next.js, Node.js, or Prisma
- do not touch Telegram runtime flows
- leads is platform-level, not tenant-scoped
- public routes must stay outside tenant/subscription enforcement

At the end:
- run compile checks
- run Sprint 1 tests
- run Sprint 2 tests
- update the change register
- give a short VPS deploy note
```

### Prompt 3 — sprint audit
Use this after a sprint lands:

```text
Audit the current sprint delivery.

Read:
- CLAUDE.md
- knowledge/project_context/claude_saas_scrum_master.md
- knowledge/project_context/change_register.md

Then verify:
1. branch and commit
2. changed files
3. tests that passed
4. what is deployed on VPS
5. remaining blockers

Finally:
- update the change register
- propose the exact next sprint handoff prompt
```

### Prompt 4 — Claude Code session setup
Use this in a fresh Claude Code session after selecting the latest Opus model in `/model`:

```text
Use the project sprint-context skill and then read:
- CLAUDE.md
- knowledge/project_context/claude_saas_scrum_master.md
- knowledge/project_context/change_register.md

Then:
1. identify the active sprint,
2. identify the current VPS-safe baseline,
3. list the project agents and slash commands that are relevant right now,
4. recommend which official plugin or MCP integration would actually help for this sprint,
5. stop and wait for implementation instructions.
```

## 11. Rules for Maintaining This File

Update this file when any of the following change:
- current sprint status
- VPS baseline
- deployment path
- stack decision
- roadmap or sprint ordering
- required third-party accounts or keys

Do not bloat this file with per-commit detail; put delivery events in the change register.
