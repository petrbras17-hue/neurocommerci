# Session Topology

- Canonical runtime layout:
  - `data/sessions/<users.id>/<phone>.session`
  - `data/sessions/<users.id>/<phone>.json`
- Runtime normal-path:
  - `SessionManager` expects canonical layout for accounts with `user_id`.
  - Missing canonical file is a blocker, not a silent fallback.
- Migration path:
  - `scripts/reconcile_accounts_with_sessions.py --migrate-layout`
  - Flat and legacy nested folders are import sources only.
- Ownership rule:
  - `accounts.user_id` is the authority for session ownership.
