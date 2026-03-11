# Runtime Inventory

- Control plane:
  - `main.py`
  - `admin/bot_admin.py`
  - `core/engine.py`
- Execution plane:
  - `worker.py`
  - `packager_worker.py`
- State:
  - Postgres or SQLite via `storage/sqlite_db.py`
  - Redis via `core/task_queue.py` and `core/redis_state.py`
- Assets:
  - Canonical sessions: `data/sessions/<user_id>/<phone>.session|json`
  - Legacy assets require reconciliation via `scripts/reconcile_accounts_with_sessions.py`
