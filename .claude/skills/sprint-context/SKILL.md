---
name: sprint-context
description: Load the current NEURO COMMENTING Scrum, sprint, VPS, and ledger context before implementation or review work.
allowed-tools: Read, Grep, Glob
---

# Sprint Context Loader

Use this skill whenever the task is about:
- starting a new sprint
- handing work off to another model
- checking what is deployed on VPS
- verifying current branch, commit, and sprint status
- restoring project context after a long gap

## Read in this order

1. `CLAUDE.md`
2. `knowledge/project_context/claude_saas_scrum_master.md`
3. `knowledge/project_context/change_register.md`
4. `README.md`

## Return

Provide a short structured summary:
- current sprint
- completed sprint(s)
- VPS baseline
- current branch and commit
- next highest-priority task
- required founder inputs for the next sprint

Do not code as part of this skill unless explicitly asked after the summary.
