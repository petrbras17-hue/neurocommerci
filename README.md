# NEURO COMMENTING

Sprint 1 foundation for the multi-tenant SaaS control plane.

## Runtime profiles

Use one of:

- `.env.development`
- `.env.staging`
- `.env.production`

Copy the selected file into `.env` before running services.

## Required env vars for Sprint 1

- `APP_ENV`
- `DATABASE_URL`
- `REDIS_URL`
- `OPS_API_TOKEN`
- `JWT_ACCESS_SECRET`
- `JWT_REFRESH_SECRET`
- `JWT_ALGORITHM`
- `JWT_ACCESS_TTL_MINUTES`
- `JWT_REFRESH_TTL_DAYS`

Existing Telegram/Gemini/runtime vars from `.env.example` still apply for the legacy control plane.

## Database safety requirements

- The application DB user must not be a PostgreSQL superuser.
- The application DB user must not have the `BYPASSRLS` privilege.
- Tenant-scoped RLS context uses transaction-local settings, so `SET LOCAL` semantics must run inside an active transaction block.

## Local startup

1. Copy `.env.development` to `.env`
2. Start infrastructure:
   - `docker compose up -d db redis`
3. Apply migrations:
   - `alembic upgrade head`
4. Start API:
   - `python ops_api.py`

If your local `pg_data` volume was created before the Sprint 1 RLS change, recreate the database container or manually repair the `nc` role so it is not `SUPERUSER` and does not have `BYPASSRLS`.

## Alembic

- Create migration: `alembic revision -m "message"`
- Upgrade: `alembic upgrade head`
- Downgrade one step: `alembic downgrade -1`

## Tests

- Unit/integration tests: `pytest tests/test_tenant_foundation.py`
- Existing smoke checks: `bash scripts/ci_smoke.sh`

## Auth modes

There are two auth paths in Sprint 1:

- `OPS_API_TOKEN` bearer token for internal bot/worker/control-plane traffic
- JWT bearer token for tenant-scoped SaaS endpoints

Internal endpoints are compatibility routes for the existing system.
The new tenant-scoped SaaS surface starts with `GET /v1/workspaces`.

## `GET /v1/workspaces`

JWT-only tenant endpoint.

Returns:

```json
{
  "items": [
    {
      "id": 1,
      "name": "Main Workspace",
      "settings": {},
      "created_at": "2026-03-08T12:00:00"
    }
  ],
  "total": 1
}
```

Rows are filtered by `tenant_id` via PostgreSQL RLS. The query does not need an explicit `WHERE tenant_id = ...` clause.
