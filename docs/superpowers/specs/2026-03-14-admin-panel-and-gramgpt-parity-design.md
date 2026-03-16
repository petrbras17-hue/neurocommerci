# Admin Panel + GramGPT Feature Parity — Design Spec

Date: 2026-03-14
Status: Approved by founder

## 1. Goal

Build an admin panel with full account/proxy lifecycle management and close all 23 feature gaps vs GramGPT, while maintaining our 7 unique advantages (3D globe, AI A/B, self-healing, analytics, multi-tenant, health scoring, Discovery/Farm/Intelligence modes).

## 2. Architecture Decisions

- **Admin Mode**: Toggle ADMIN/CLIENT in sidebar (is_admin flag on auth_users)
- **Storage**: `storage/accounts/{workspace_id}/{phone}/` on filesystem, PostgreSQL as index
- **Session files**: .session + metadata.json per account, gitignored
- **Onboarding**: Step-by-step wizard for new accounts + command center dashboard for existing
- **AI**: Text via OpenRouter (route_ai_task), images via Gemini API, video via Gemini API
- **Real-time**: WebSocket for live operation logs
- **All tables**: workspace_id from day 1 (tenant-ready)

## 3. Current State

- 47 features fully implemented
- 18 features have backend but no UI
- 23 features missing entirely
- Account +77076294082 (KZ) secured and ready for warmup
- KZ SOCKS5 proxy verified (DUAL: HTTP + HTTPS CONNECT)

## 4. Sprint Plan (Sprints 17-24)

### Sprint 17: Admin Panel Foundation + Account Onboarding Wizard

**Goal**: Admin can upload accounts, bind proxies, verify, and harden through web UI.

**Backend**:
- Migration: `is_admin` column on `auth_users`
- `POST /v1/admin/accounts/upload` — accept tdata ZIP, .session+.json, bulk ZIP
- tdata→session conversion via opentele server-side
- `POST /v1/admin/accounts/{id}/verify` — connect, check auth, get_me
- `POST /v1/admin/accounts/{id}/harden` — kill sessions, 2FA, privacy (with delays)
- `POST /v1/admin/proxies/import` — bulk proxy import (host:port:user:pass)
- `POST /v1/admin/proxies/{id}/test` — HTTP + HTTPS CONNECT test
- `POST /v1/admin/accounts/{id}/bind-proxy` — 1:1 binding
- `GET /v1/admin/dashboard` — account/proxy stats summary
- `GET /v1/admin/operations-log` — recent operations

**Frontend**:
- Admin/Client mode toggle in AppShell sidebar
- Admin nav groups: Onboarding, Operations, Monitoring, System
- `AdminDashboardPage` — command center with stats + quick actions
- `AccountOnboardingWizard` — 6-step wizard (Upload → Proxy → Connect → Secure → Warmup → Ready)
- `AdminProxyManagerPage` — import, test, bind UI
- `AdminOperationsLogPage` — scrollable log of all operations

**Tests**: Admin auth middleware, upload endpoint, proxy test endpoint

---

### Sprint 18: Account Packaging (AI Profiles + Channel Creation)

**Goal**: Full AI-powered account packaging — profiles, avatars, channels, posts.

**Backend**:
- `POST /v1/admin/accounts/{id}/edit-profile` — change name/bio/username (with 48h guard)
- `POST /v1/admin/accounts/{id}/generate-profile` — AI profile (gender, country, age, profession)
- `POST /v1/admin/accounts/mass-generate-profiles` — bulk AI profiles
- `POST /v1/admin/accounts/{id}/generate-avatar` — Gemini image API
- `POST /v1/admin/accounts/{id}/upload-avatar` — manual upload
- Avatar library: `storage/avatars/` with curated safe avatars
- `POST /v1/admin/accounts/{id}/create-channel` — create TG channel + pin + first post
- `POST /v1/admin/accounts/{id}/create-channel` extended: AI-generated post text + image
- Gemini image generation integration in ai_router.py
- Video generation via Gemini API (for channel posts)

**Frontend**:
- `AccountPackagingPage` — single account packaging wizard
- `MassPackagingPage` — select multiple accounts, set params, generate all
- Profile preview card (before/after)
- Avatar gallery + AI generate button
- Channel creation form (name, description, avatar, first post text + media)

**Tests**: Profile generation, avatar generation, channel creation

---

### Sprint 19: Warmup Engine v2 + Real-Time Logs

**Goal**: Production-grade warmup with all GramGPT features + WebSocket live logs.

**Backend**:
- Warmup engine additions: story viewing, channel joining (with delays), inter-account dialogs
- Warmup scheduling: time ranges, duration per session, sessions per day
- Warmup progress tracking: actions done, channels visited, days warmed
- WebSocket endpoint: `WS /v1/ws/logs` — real-time operation logs
- Operation log model: `operation_logs` table (timestamp, account_id, module, action, status, detail)
- All modules emit to operation_logs: warmup, farm, parser, reactions, chatting, dialogs

**Frontend**:
- `WarmupControlPage` redesign — drag accounts, set schedule, choose intensity
- Real-time log panel (WebSocket) — filterable by account/module
- Warmup progress indicators per account
- Background operation indicator in topbar

**Tests**: Warmup story viewing, channel joining, WebSocket connection

---

### Sprint 20: Neurocommenting v2 — Full GramGPT Parity

**Goal**: Close all commenting gaps — blacklist/whitelist, channel comments, auto-DM, language auto-detect.

**Backend**:
- `channel_blacklist` / `channel_whitelist` tables (per workspace, auto-populated)
- Auto-blacklist on account ban in channel
- Auto-whitelist on successful comment
- Comment-as-channel: send comment from account's pinned channel (not personal)
- Auto-responder for DMs: `POST /v1/farm/{id}/auto-dm` config
- Auto-DM: single pre-set message on first incoming DM
- Post targeting modes: all / random N% / keyword match
- Language auto-detection per channel (from channel_indexer)
- Telegram folder integration: import channels from folder for farm
- Preset system: save/load farm configs as named presets
- `farm_presets` table

**Frontend**:
- Farm setup redesign with targeting mode selector
- Blacklist/Whitelist management panel
- Auto-DM config section
- Preset save/load buttons
- Language selector (manual / auto)

**Tests**: Blacklist auto-population, comment-as-channel, auto-DM trigger

---

### Sprint 21: Neurochatting v2 + Neurodialogs v2

**Goal**: Close all chatting/dialog gaps — unified DM inbox, product promotion, semantic matching.

**Backend**:
- Neurochatting modes: interval (every N%), keyword trigger, AI semantic matching
- Semantic matching: AI classifies if message relates to target topics
- Product promotion config: product name, description, problems solved, mention frequency
- Context depth setting: N previous messages for AI context
- Neurodialogs unified inbox: `GET /v1/dialogs/inbox` — all DMs from all accounts
- `POST /v1/dialogs/inbox/{account_id}/{peer_id}/send` — send from unified inbox
- AI auto-responder for DMs with product knowledge
- Preset system for chatting/dialog configs

**Frontend**:
- `ChattingPage` redesign — mode selector, product promotion panel, context depth
- `DialogsPage` redesign — unified inbox (left: account list, middle: conversations, right: chat)
- AI auto-responder config with product info fields
- Preset save/load

**Tests**: Semantic matching accuracy, unified inbox loading, auto-responder

---

### Sprint 22: Parsing v2 — Groups + Message-Based + AI Keywords

**Goal**: Full parsing parity — group parser, message parser, AI keyword suggestions, templates.

**Backend**:
- Group/chat parser: `core/group_parser_service.py` — search groups by keywords
- Group filters: active only, member count range, spam rating
- Message-based user parser: parse users who sent messages (for hidden member lists)
- Message parser filters: keyword match, date range, limit
- AI keyword suggestions: `POST /v1/parser/suggest-keywords` — expand seed keywords via AI
- Parsing templates: crypto, lead-gen, programming, SMM, etc.
- `parsing_templates` table with pre-built keyword sets

**Frontend**:
- Parser page tabs: Channels / Groups / Users / By Messages
- AI keyword suggestion button (seed word → expanded list)
- Template selector dropdown
- Advanced filters panel per parser type
- Enhanced export: JSON/TXT/CSV with selectable fields

**Tests**: Group parsing, message-based parsing, AI keyword suggestion

---

### Sprint 23: Mass Reactions v2 + Real-Time Monitoring Enhancement

**Goal**: Full reaction features + monitoring mode + enhanced real-time dashboard.

**Backend**:
- Mass reactions monitoring mode: react to new comments within N seconds
- Reaction-as-channel (Premium accounts)
- Enhanced blacklist/whitelist for reactions
- Real-time monitoring dashboard: all active modules, all accounts, all actions
- Account busy status in real-time (free/warmup/farm/chatting/parsing)
- WebSocket push for status changes

**Frontend**:
- `ReactionsPage` redesign — monitoring mode toggle, reaction type picker
- Enhanced `AdminDashboardPage` — real-time module status, active accounts, throughput
- Account cards with live status badge
- Module activity feed

**Tests**: Monitoring mode reaction timing, status broadcasting

---

### Sprint 24: Farm Launch Orchestration + Anti-Fraud Intelligence

**Goal**: Full production farm launch with gradual scaling, anti-fraud AI analysis at every step.

**Backend**:
- Farm launch orchestrator: gradual power ramp (day 1: 2 comments, day 3: 5, day 7: 10, day 14: full)
- Anti-fraud scoring per action: AI evaluates risk before each comment/reaction/message
- Per-account action history analysis: detect patterns before Telegram does
- Configurable scaling curves: linear / exponential / custom
- Health-gated actions: if health < threshold, auto-reduce activity
- Cross-account pattern detection: ensure accounts don't behave identically
- Random Gaussian delays (not uniform) for all inter-action intervals
- Active hours with jitter (not exact same time every day)
- Weekly behavior variation (less active Mon, more active Thu-Sat)

**Frontend**:
- Farm launch wizard with scaling curve selector
- Anti-fraud risk indicator per account
- Pattern detection alerts
- Scaling schedule visualization (graph)

**Tests**: Scaling curve math, anti-fraud scoring, pattern detection

---

## 5. Priority Order

Sprint 17 → 18 → 19 → 20 are the critical path (admin panel → packaging → warmup → farm).
Sprints 21-24 can be parallelized or reordered based on what the founder needs first.

## 6. What We Already Beat GramGPT On

| Our Advantage | Detail |
|---|---|
| 3D Globe Channel Map | react-globe.gl with H3 clustering vs their 2D map |
| Discovery/Farm/Intelligence modes | 3 specialized map modes |
| 10 AI comment styles + A/B | vs their ~5 styles |
| Self-Healing Engine | Auto-appeal, auto-replace, auto-purchase |
| Analytics & ROI dashboards | They have zero analytics |
| Multi-tenant SaaS | They are desktop-only |
| Health scoring + survivability | Quantified account health |
| Account lifecycle FSM | 10-stage state machine |
| Session topology | Visual session management |

## 7. New Advantages We're Adding

| Feature | Sprint | Detail |
|---|---|---|
| AI video generation for channels | 18 | Gemini video API — they don't have this |
| Semantic topic matching | 21 | AI determines if chat message relates to topics |
| Anti-fraud AI scoring per action | 24 | Risk-score every action before executing |
| Gradual power ramp with curves | 24 | Scientific scaling, not flat limits |
| Weekly behavior variation | 24 | Different activity on different days |
| Cross-account pattern detection | 24 | Ensure no identical behavior between accounts |
