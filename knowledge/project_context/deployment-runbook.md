# Deployment Runbook

- Target runtime: single VPS, Docker Compose, Postgres, Redis, bot, packager, worker pool.
- Before start:
  - reconcile sessions into canonical layout
  - verify parser account and worker heartbeats
  - confirm queue sizes and blocker summary with `scripts/runtime_status.py`
- Health expectations:
  - worker heartbeats present
  - queue leases/inflight visible
  - no unexpected duplicate session assets
  - no active accounts with `no_owner` or `session_missing`
