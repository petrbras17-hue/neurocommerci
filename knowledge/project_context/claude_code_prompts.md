# Claude Code Prompt Pack

Last updated: 2026-03-11

Use these prompts in Claude Code. They are written to keep the model inside the current architecture and sprint history.

## Prompt 1: Bootstrap And Understand The Project

```text
You are working inside /Users/braslavskii/NEURO COMMENTING.

Before doing anything, read these files in order:
1. CLAUDE.md
2. knowledge/project_context/claude_code_master_context.md
3. knowledge/project_context/change_register.md
4. README.md
5. RUNBOOK.md
6. knowledge/project_context/sprint3_4_live_audit.md
7. knowledge/project_context/ai_orchestrator_foundation.md

Then give me a short factual status report with:
- current branch
- HEAD commit
- dirty working tree summary
- official VPS safe baseline
- what was live-audited on VPS
- what is still pending deploy
- the next safest step

Do not change code yet. Do not guess when evidence is missing.
Answer in Russian.
```

## Prompt 2: Full Technical Audit

```text
Audit this repository as a senior engineer.

Mandatory context files:
- CLAUDE.md
- knowledge/project_context/claude_code_master_context.md
- knowledge/project_context/change_register.md
- README.md
- RUNBOOK.md

Audit goals:
- identify bugs
- identify release risks
- identify tenant-safety or RLS risks
- identify deployment gaps between local repo and VPS baseline
- identify weak tests or missing coverage
- identify dead or contradictory documentation

Constraints:
- do not make changes yet
- prioritize findings over summaries
- cite exact files and lines
- separate confirmed facts from inference
- answer in Russian
```

## Prompt 3: Sprint 4 Stabilization Only

```text
Continue only Sprint 4 stabilization work in /Users/braslavskii/NEURO COMMENTING.

Read first:
- CLAUDE.md
- knowledge/project_context/claude_code_master_context.md
- knowledge/project_context/change_register.md
- knowledge/project_context/ai_orchestrator_foundation.md
- knowledge/project_context/sprint3_4_live_audit.md

Scope:
- AI router hardening
- JSON contract reliability
- assistant job queueing
- AI quality telemetry
- assistant/context/creative stability

Do not:
- introduce a second stack
- redesign unrelated legacy runtime
- touch billing
- enable Telegram-side account execution from assistant surfaces

Work order:
1. restate current Sprint 4 status
2. inspect the dirty working tree
3. find the smallest safe patch set
4. implement it
5. run the most relevant tests
6. summarize what remains before VPS deploy

Answer in Russian. Keep changes pragmatic.
```

## Prompt 4: VPS Release Audit

```text
Prepare a release audit for the latest local Sprint 3/4 and Sprint 4 stabilization work.

Read first:
- CLAUDE.md
- knowledge/project_context/claude_code_master_context.md
- knowledge/project_context/change_register.md
- knowledge/project_context/deployment-runbook.md
- ops/DEPLOY_AEZA.md
- knowledge/project_context/sprint3_4_live_audit.md

Deliver:
- what is definitely on the VPS safe baseline
- what is only local
- what was live-audited but not promoted
- which migrations are pending
- which env vars must exist before deploy
- the minimum safe deploy order
- the smoke test checklist after deploy

Do not deploy anything yet.
Answer in Russian.
```

## Prompt 5: Improve Only, Do Not Rewrite

```text
Work in /Users/braslavskii/NEURO COMMENTING as a careful maintainer.

You must improve the current architecture, not replace it.

Hard rules:
- FastAPI stays the main backend
- React/Vite web shell stays
- DB changes go through Alembic only
- keep tenant safety and RLS intact
- keep Google Sheets and Telegram digest as sinks only
- do not remove project memory files
- do not revert unrelated dirty working tree changes

First read:
- CLAUDE.md
- knowledge/project_context/claude_code_master_context.md
- knowledge/project_context/change_register.md

Then:
1. explain what area you are changing and why
2. make the smallest high-value change
3. run focused verification
4. report residual risks

Answer in Russian.
```

## Prompt 6: Build A Clean Handoff After Work

```text
After finishing any meaningful change in this repo, update the handoff context.

Required files to check:
- knowledge/project_context/claude_code_master_context.md
- knowledge/project_context/change_register.md
- CLAUDE.md

Do this:
- add or refresh the factual sprint/deploy status
- record what was changed
- record what is still pending deploy
- keep summaries short and concrete
- do not invent VPS facts you did not verify

Answer in Russian and show exactly which files you updated.
```
