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

## Required env vars for Docker Compose

- `DB_PASSWORD` — password for the `nc` PostgreSQL app role (used in all service `DATABASE_URL` values)
- `POSTGRES_SUPERUSER_PASSWORD` — password for the `postgres` superuser (used by the `db` container init)

Both must be set in `.env` before running `docker compose up`. There are no default fallbacks.

Existing Telegram/Gemini/runtime vars from `.env.example` still apply for the legacy control plane.

## Sprint 2 public marketing surface

Sprint 2 adds public marketing routes inside the existing FastAPI app:

- `/`
- `/ecom`
- `/edtech`
- `/saas`
- `POST /api/leads`
- `GET /v1/internal/leads`
- `/robots.txt`
- `/sitemap.xml`

Notes:
- the landing pages are public and do not require JWT
- the `leads` table is platform-level and not tenant-scoped
- public routes must remain outside tenant/subscription enforcement
- after `POST /api/leads`, the app tries three side effects:
  - save lead in PostgreSQL
  - mirror lead into Google Sheets
  - send Telegram notifications to the admin bot and digest chat
- DB save is the source of truth; Sheets/Telegram failures must not break the form

Optional env/config for Sprint 2 lead delivery:

- `ADMIN_BOT_TOKEN`
- `ADMIN_TELEGRAM_ID`
- `DIGEST_BOT_TOKEN`
- `DIGEST_CHAT_ID`
- `GOOGLE_SHEETS_CREDENTIALS_FILE`
- `CHANNELS_SPREADSHEET_ID` (used as the default spreadsheet for lead mirroring)

## Sprint 3 web workspace shell

Sprint 3 adds a Vite + React workspace shell on top of the existing FastAPI app.

Public/protected surfaces:

- `/app`
- `/app/login`
- `/app/dashboard`
- `/app/accounts`
- `/app/campaigns`
- `/app/parser`
- `/app/analytics`
- `/app/billing`
- `/app/settings`

Backend auth endpoints:

- `POST /auth/telegram/verify`
- `POST /auth/complete-profile`
- `POST /auth/refresh`
- `POST /auth/logout`
- `GET /auth/me`
- `GET /v1/me/workspace`
- `GET /v1/me/team`

Web onboarding/account endpoints:

- `POST /v1/web/accounts/upload`
- `GET /v1/web/accounts`
- `GET /v1/web/proxies/available`
- `POST /v1/web/accounts/{id}/bind-proxy`
- `POST /v1/web/accounts/{id}/audit`
- `GET /v1/web/accounts/{id}/audit`

Sprint 3 defaults:

- Telegram-first auth via the main bot login widget
- Russian-only UI
- pair upload only: `.session + .json`
- account/proxy data is tenant/workspace scoped for the web surface

Telegram Login Widget note:

- before production use, configure BotFather:
  - `/setdomain`
  - `176-124-221-253.sslip.io`
- local `127.0.0.1` development falls back to a helpful widget notice instead of rendering the widget iframe

Frontend local development:

1. install frontend deps:
   - `cd frontend && npm install`
2. run Vite dev server:
   - `npm run dev`
3. in another shell run FastAPI:
   - `python ops_api.py`

Frontend production build:

- `cd frontend && npm run build`

The built assets are emitted to `frontend/dist` and served by FastAPI/nginx through `/app`.

## Sprint 4 AI assistant layer

Sprint 4 adds the first operator-safe AI assistant layer on top of the web shell.

Public/protected surfaces:

- `/app/assistant`
- `/app/context`
- `/app/creative`

Assistant/context/creative endpoints:

- `POST /v1/assistant/start-brief`
- `POST /v1/assistant/message`
- `GET /v1/assistant/thread`
- `GET /v1/context`
- `POST /v1/context/confirm`
- `GET /v1/creative/drafts`
- `POST /v1/creative/generate`
- `POST /v1/creative/approve`
- `POST /v1/web/accounts/{id}/notes`
- `GET /v1/web/accounts/{id}/timeline`

Sprint 4 defaults:

- Russian-only operator UX
- Postgres is the source of truth for assistant/context/creative data
- Google Sheets and Telegram digest are integration sinks only
- no Telegram-side account execution is triggered by the assistant layer
- all live acceptance data should be marked with `TEST` / `AUDIT`

## AI orchestrator foundation

The AI stack now supports a hybrid routing model:

- `gemini_direct` for fast/default paths
- `openrouter` for boss/manager/fallback paths

Task routing is centralized in `core/ai_router.py`.
Feature code should not call provider SDKs directly for assistant/context/creative flows.

Configured model tiers:

- `boss`
- `manager`
- `worker`

Budget controls:

- `AI_DAILY_BUDGET_USD`
- `AI_MONTHLY_BUDGET_USD`
- `AI_BOSS_DAILY_BUDGET_USD`
- `AI_HARD_STOP_ENABLED`

Provider/routing env:

- `OPENROUTER_API_KEY`
- `OPENROUTER_BASE_URL`
- `OPENROUTER_DEFAULT_REFERER`
- `OPENROUTER_DEFAULT_TITLE`
- `AI_DEFAULT_MODE`
- `AI_ALLOWED_PROVIDER_ORDER`
- `AI_BOSS_MODELS`
- `AI_MANAGER_MODELS`
- `AI_WORKER_MODELS`

Current routing defaults:

- `brief_extraction` -> worker
- `assistant_reply` -> manager
- `creative_variants` -> manager
- `parser_query_suggestions` -> worker
- `campaign_strategy_summary` -> boss, approval required
- `weekly_marketing_report` -> manager

Current outcomes:

- `executed_as_requested`
- `downgraded_by_budget_policy`
- `blocked_by_budget_policy`

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
- Marketing pages + lead capture: `pytest tests/test_marketing_site.py`
- Lead funnel side effects: `pytest tests/test_lead_funnel.py`
- Sprint 3 auth + onboarding APIs: `pytest tests/test_web_auth.py tests/test_web_accounts.py`
- Sprint 4 assistant + creative flows: `pytest tests/test_web_assistant.py`
- AI router + budget controls: `pytest tests/test_ai_router.py`
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
