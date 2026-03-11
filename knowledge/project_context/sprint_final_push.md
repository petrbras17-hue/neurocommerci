# NEURO COMMENTING — Final Push to Production

Last updated: 2026-03-11
Author: Claude Opus 4.6 (CEO/PM mode)

## Current State Assessment

### Completed
- Sprint 1: Tenant foundation + RLS (deployed on VPS)
- Sprint 2: Landing + lead capture (deployed)
- Sprint 3: Telegram auth + web shell (deployed)
- Sprint 4: AI orchestrator + assistant/context/creative (deployed)
- Sprint 5: Security hardening (deployed)
- Sprint 6: Landing redesign + email/password auth + bot auth (deployed)

### HEAD: `ab9eea7` on branch `sprint/3-telegram-first-auth-shell`

### Frontend Pages Status (21 pages)
Pages with real functionality (>200 lines):
- ChannelMapPage (1311 lines) — needs upgrade to GramGPT level
- CampaignsPage (929 lines) — needs backend wiring check
- AccountsPage (788 lines) — functional
- FarmPage (677 lines) — functional
- AssistantPage (596 lines) — functional
- ParserPage (553 lines) — needs channel discovery upgrade
- WarmupPage (539 lines) — needs backend wiring check
- AnalyticsPage (537 lines) — needs real data pipeline
- ProfilesPage (532 lines) — needs backend check
- DashboardPage (475 lines) — functional
- CreativePage (423 lines) — functional
- ContextPage (419 lines) — functional
- HealthPage (418 lines) — needs backend check
- DialogsPage (313 lines) — needs backend check
- LoginPage (292 lines) — functional (3 auth methods)
- FoldersPage (276 lines) — needs backend check
- ChattingPage (274 lines) — needs backend check
- ReactionsPage (247 lines) — needs backend check
- UserParserPage (217 lines) — needs backend check
- ProfileCompletionPage (55 lines) — functional
- PlaceholderPage (55 lines) — should not be used

## Priority Tasks

### P0: Channel Map Upgrade (GramGPT Level)
- Full interactive map with clickable channels
- Advanced search: by category, subscribers, comments activity, language
- Filter panel: sidebar with checkboxes, sliders, tags
- Channel cards: avatar, name, subscribers, comments/day, category, link
- Pre-parsed CIS channels with 1000+ subs and open comments
- Real-time search with debounce
- Pagination / infinite scroll

### P1: Backend API Wiring Audit
- Every frontend page must have working backend endpoints
- Check all API contracts match frontend expectations
- Fix any broken/missing endpoints

### P2: Channel Discovery & Parsing
- Build channel scraping pipeline for CIS Telegram channels
- Filter: open comments, 1000+ subscribers
- Store in PostgreSQL with full metadata
- Expose via API for ChannelMapPage

### P3: Frontend Polish
- All pages must render without errors
- Navigation must work
- Loading states, error states, empty states
- Mobile responsive

### P4: Sprint 7-9 Feature Completion
- Sprint 7: Campaigns + Parser UI
- Sprint 8: Analytics + Usage
- Sprint 9: Infrastructure hardening

### P5: Billing Architecture (no payment keys yet)
- Build subscription model, plans table, trial logic
- UI for plan selection
- Middleware for plan enforcement
- Leave payment gateway integration for final sprint

## Agent Assignments
1. saas-code-reviewer: Full backend audit
2. qa-tenant-auditor: Tenant isolation + API contract verification
3. sprint-product-manager: Sprint completion check
4. Explore agent: Frontend page analysis
5. saas-backend-implementer: Fix broken endpoints
6. e2e-tester: Browser-based smoke test
