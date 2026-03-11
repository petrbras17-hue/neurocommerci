# NEURO COMMENTING — Master Sprint Plan

Last updated: 2026-03-11
Status: Active execution

## Site Access

- **Public URL**: https://176-124-221-253.sslip.io/
- **App URL**: https://176-124-221-253.sslip.io/app
- **VPS**: 176.124.221.253 (user: deploy, path: /opt/neuro-commenting)
- **nginx**: SSL via Let's Encrypt, proxies 443 -> 127.0.0.1:8081
- **Direct port 8081**: NOT accessible externally (127.0.0.1 only)

## Current State (after Sprint 5)

- HEAD: `2c77889` (Sprint 5: security hardening + performance fixes)
- All 13+ migrations applied including RLS hardening
- 4/4 containers healthy: db, redis, ops_api, bot
- Frontend: Dark Terminal design, 470KB JS, 27KB CSS
- Security: JWT validation, RLS on all tenant tables, CORS restricted, path traversal guarded

## Revised Sprint Roadmap (billing LAST)

### Sprint 6: Product Stability & Testing (NEXT)
**Goal**: Make every feature work perfectly, test on DartVPN bot

Tasks:
1. Fix Telegram Login Widget (BotFather /setdomain for 176-124-221-253.sslip.io)
2. Test full auth flow: login -> dashboard -> accounts -> assistant -> creative
3. Test account upload: .session + .json pair
4. Test proxy binding
5. Test assistant brief -> context -> creative generate -> approve flow
6. Test channel map, parser, campaigns pages
7. Fix any broken flows found during testing
8. Add missing error states and loading indicators
9. Verify all API endpoints return correct data
10. Run DartVPN bot through the full workflow as a real customer

Acceptance criteria:
- Login with Telegram works
- Upload DartVPN account, bind proxy, run through assistant flow
- All pages render without errors
- All API calls succeed

### Sprint 7: Production Hardening
**Goal**: Make the product robust enough for real users

Tasks:
1. Sentry integration for error monitoring
2. Structured logging (JSON format)
3. Automated DB backup (daily cron)
4. CI/CD: run all 11 test suites on push
5. Redis-backed rate limiting (replace in-memory)
6. Health check dashboard
7. SQL pagination everywhere (replace in-memory slicing)
8. Connection pool tuning for asyncpg
9. Real domain setup (if available)

### Sprint 8: Campaigns + Parser Dashboard
**Goal**: First real product loop beyond the assistant

Tasks:
1. Campaign CRUD (create, edit, delete, status)
2. Parser UI with saved searches
3. Channel discovery integration
4. Draft queue with approve/reject flow
5. Campaign run execution
6. Results and statistics per campaign

### Sprint 9: Analytics + Usage Dashboards
**Goal**: Prove value to customers with data

Tasks:
1. Analytics events pipeline
2. Customer analytics dashboard
3. Usage meters per tenant
4. Internal activation report
5. ROI calculations

### Sprint 10: Billing & Subscriptions (LAST)
**Goal**: Enable payment only after product is proven

Tasks:
1. Plans table and subscription model
2. 14-day free trial
3. YooKassa integration (RU/CIS)
4. Stripe integration (international)
5. Plan enforcement middleware
6. Subscription management UI
7. Invoice/receipt generation

## Required Keys & Tokens

### Currently configured (verify in .env on VPS):
- `OPS_API_TOKEN` — internal auth
- `JWT_ACCESS_SECRET` / `JWT_REFRESH_SECRET` — JWT signing
- `GEMINI_API_KEY` — AI (Gemini direct)
- `OPENROUTER_API_KEY` — AI (OpenRouter fallback)
- `DATABASE_URL` — PostgreSQL
- `REDIS_URL` — Redis
- `ADMIN_BOT_TOKEN` — Admin Telegram bot
- `ADMIN_TELEGRAM_ID` — Admin user ID
- `DB_PASSWORD` / `POSTGRES_SUPERUSER_PASSWORD` — DB credentials

### Need to verify:
- `DIGEST_BOT_TOKEN` / `DIGEST_CHAT_ID` — Telegram digest
- `GOOGLE_SHEETS_CREDENTIALS_FILE` — Sheets integration
- `CHANNELS_SPREADSHEET_ID` — Sheets mirror

### Needed for Sprint 10 (billing):
- YooKassa merchant ID and secret key
- Stripe API keys (optional, for international)

### Needed for production:
- Sentry DSN
- Custom domain (optional, sslip.io works for now)

## Architecture Quick Reference

- Backend: Python 3.11, FastAPI, SQLAlchemy, Alembic, PostgreSQL 16, Redis 7
- Frontend: React 18, Vite 6, TypeScript, framer-motion, lucide-react
- AI: Gemini (direct) + OpenRouter (fallback), boss/manager/worker tiers
- Deploy: Docker Compose on Aeza VPS, nginx + Let's Encrypt
- Auth: Telegram Login Widget + JWT + refresh token cookies

## Key Files

- `ops_api.py` — main API (4700+ lines, should be split eventually)
- `core/web_auth.py` — auth logic
- `core/ai_router.py` — AI routing
- `core/assistant_service.py` — assistant flows
- `storage/models.py` — SQLAlchemy models
- `frontend/src/` — React app
- `alembic/versions/` — 13 migrations + RLS hardening

## Testing Strategy

- `tests/test_tenant_foundation.py` — Sprint 1 tenant basics
- `tests/test_marketing_site.py` — Sprint 2 landing + leads
- `tests/test_lead_funnel.py` — Lead side effects
- `tests/test_web_auth.py` — Sprint 3 auth
- `tests/test_web_accounts.py` — Account management
- `tests/test_web_assistant.py` — Sprint 4 assistant
- `tests/test_ai_router.py` — AI routing + budget

## Claude Code Session Recovery

To restore context in a new session, read these files in order:
1. `CLAUDE.md`
2. `knowledge/project_context/sprint_master_plan.md` (this file)
3. `knowledge/project_context/change_register.md`
4. `README.md`

Then check: `git log --oneline -5`, `git status`, VPS container status.
