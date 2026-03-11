# Farm Orchestrator SaaS — Design Spec

Date: 2026-03-11
Status: Approved
Approach: A (Modular Combine)

## 1. Product Vision

Build a multi-tenant Telegram Growth OS that surpasses GramGPT.io in every dimension:
- Multi-threaded neurocommenting farm (50+ threads)
- AI-powered account warmup and profile generation
- Channel/user parsing with smart targeting
- Mass reactions, neurochatting, neurodialogs
- Real-time monitoring, health scoring, quarantine management
- Campaign orchestration with ROI analytics
- International scalability (i18n, multi-geo)

## 2. Architecture

```
Frontend (React/Vite)
  /app/farm — Farm control panel with real-time logs
  /app/warmup — Warmup configuration and monitoring
  /app/parser — Channel/user parser UI
  /app/health — Account health dashboard
  /app/campaigns — Campaign builder
  /app/reactions — Mass reactions control
  /app/chatting — Neurochatting control
  /app/dialogs — Neurodialogs (auto-reply) control

FastAPI API Layer (ops_api.py)
  /v1/farm/* — Farm CRUD, thread management, start/stop
  /v1/warmup/* — Warmup profiles, schedules, status
  /v1/parser/* — Channel/user parsing jobs
  /v1/health/* — Health scores, quarantine actions
  /v1/campaigns/* — Campaign CRUD, scheduling
  /v1/reactions/* — Reaction job management
  /v1/chatting/* — Chat commenting management
  /v1/dialogs/* — Auto-reply configuration
  /v1/profiles/* — AI profile generation

Farm Orchestrator (core/farm_orchestrator.py)
  - Manages N FarmThread instances
  - Each thread: 1 account + 1 proxy + assigned channels
  - State machine: idle → subscribing → monitoring → commenting → cooldown
  - Redis pub/sub for real-time log streaming

Workers (extend existing worker.py pattern)
  - FarmWorker — runs farm threads (comment + monitor)
  - WarmupWorker — runs warmup sessions
  - ParserWorker — runs channel/user parsing jobs
  - ReactionWorker — runs mass reaction jobs
  - ChattingWorker — runs neurochatting sessions

Telethon Session Pool (core/session_manager.py — existing)
  - 1 account = 1 TelethonClient = 1 proxy
  - Connection pooling with LRU eviction
  - Already implemented, extend for concurrent multi-module access

Storage
  - PostgreSQL with RLS (all new tables tenant-scoped)
  - Redis for task queues, real-time state, pub/sub logs
  - Alembic migrations for all schema changes
```

## 3. Sprint 5: Farm Core (2 weeks)

### 3.1 Database Tables (migration 20260311_09)

```sql
-- Farm configurations
farm_configs (
  id SERIAL PRIMARY KEY,
  tenant_id INT NOT NULL REFERENCES tenants(id),
  workspace_id INT NOT NULL REFERENCES workspaces(id),
  name VARCHAR(200) NOT NULL,
  status VARCHAR(20) DEFAULT 'stopped',  -- stopped, running, paused
  mode VARCHAR(20) DEFAULT 'multithread', -- multithread, standard
  max_threads INT DEFAULT 50,
  comment_prompt TEXT,
  comment_tone VARCHAR(50) DEFAULT 'neutral',  -- neutral, hater, flirt, native, custom
  comment_language VARCHAR(10) DEFAULT 'auto',
  comment_all_posts BOOLEAN DEFAULT true,
  comment_percentage INT DEFAULT 100,
  delay_before_comment_min INT DEFAULT 30,
  delay_before_comment_max INT DEFAULT 120,
  delay_before_join_min INT DEFAULT 60,
  delay_before_join_max INT DEFAULT 300,
  ai_protection_mode VARCHAR(20) DEFAULT 'aggressive', -- off, aggressive, conservative
  auto_responder_enabled BOOLEAN DEFAULT false,
  auto_responder_prompt TEXT,
  auto_responder_redirect_url TEXT,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

-- Farm threads (1 thread = 1 account)
farm_threads (
  id SERIAL PRIMARY KEY,
  tenant_id INT NOT NULL REFERENCES tenants(id),
  farm_id INT NOT NULL REFERENCES farm_configs(id),
  account_id INT NOT NULL REFERENCES accounts(id),
  thread_index INT NOT NULL,
  status VARCHAR(30) DEFAULT 'idle',
  -- idle, subscribing, monitoring, commenting, cooldown, quarantine, error, stopped
  assigned_channels JSONB DEFAULT '[]',
  folder_invite_link TEXT,
  stats_comments_sent INT DEFAULT 0,
  stats_comments_failed INT DEFAULT 0,
  stats_reactions_sent INT DEFAULT 0,
  stats_last_comment_at TIMESTAMP,
  stats_last_error TEXT,
  health_score INT DEFAULT 100,
  quarantine_until TIMESTAMP,
  started_at TIMESTAMP,
  updated_at TIMESTAMP DEFAULT NOW()
);

-- Channel databases for targeting
channel_databases (
  id SERIAL PRIMARY KEY,
  tenant_id INT NOT NULL REFERENCES tenants(id),
  workspace_id INT NOT NULL REFERENCES workspaces(id),
  name VARCHAR(200) NOT NULL,
  source VARCHAR(20) DEFAULT 'manual', -- manual, parsed, map
  status VARCHAR(20) DEFAULT 'active',
  created_at TIMESTAMP DEFAULT NOW()
);

-- Channels in databases
channel_entries (
  id SERIAL PRIMARY KEY,
  tenant_id INT NOT NULL REFERENCES tenants(id),
  database_id INT NOT NULL REFERENCES channel_databases(id),
  telegram_id BIGINT,
  username VARCHAR(100),
  title VARCHAR(300),
  member_count INT,
  has_comments BOOLEAN DEFAULT true,
  language VARCHAR(10),
  category VARCHAR(100),
  last_post_at TIMESTAMP,
  blacklisted BOOLEAN DEFAULT false,
  success_rate FLOAT,  -- % successful comments
  created_at TIMESTAMP DEFAULT NOW()
);

-- Parsing jobs
parsing_jobs (
  id SERIAL PRIMARY KEY,
  tenant_id INT NOT NULL REFERENCES tenants(id),
  workspace_id INT NOT NULL REFERENCES workspaces(id),
  account_id INT REFERENCES accounts(id),
  job_type VARCHAR(20) NOT NULL, -- channels, users
  status VARCHAR(20) DEFAULT 'pending',
  keywords JSONB DEFAULT '[]',
  filters JSONB DEFAULT '{}',
  -- filters: min_members, max_members, language, has_comments, active_only
  max_results INT DEFAULT 50,
  results_count INT DEFAULT 0,
  target_database_id INT REFERENCES channel_databases(id),
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  error TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);

-- Profile templates for AI generation
profile_templates (
  id SERIAL PRIMARY KEY,
  tenant_id INT NOT NULL REFERENCES tenants(id),
  workspace_id INT NOT NULL REFERENCES workspaces(id),
  name VARCHAR(200),
  gender VARCHAR(10), -- male, female, any
  geo VARCHAR(50),
  bio_template TEXT,
  channel_name_template TEXT,
  channel_description_template TEXT,
  channel_first_post_template TEXT,
  avatar_style VARCHAR(50), -- ai_generated, library, custom
  avatar_url TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);

-- Farm event log (real-time streaming)
farm_events (
  id SERIAL PRIMARY KEY,
  tenant_id INT NOT NULL REFERENCES tenants(id),
  farm_id INT NOT NULL REFERENCES farm_configs(id),
  thread_id INT REFERENCES farm_threads(id),
  event_type VARCHAR(50) NOT NULL,
  -- thread_started, thread_stopped, comment_sent, comment_failed,
  -- channel_joined, channel_left, quarantine_entered, quarantine_lifted,
  -- mute_detected, flood_wait, error, health_change
  severity VARCHAR(10) DEFAULT 'info', -- info, warn, error
  message TEXT,
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMP DEFAULT NOW()
);

-- All tables get RLS policies for tenant_id
```

### 3.2 Core Modules

**core/farm_orchestrator.py**
- `FarmOrchestrator` class
- `start_farm(farm_id)` — create threads, assign channels, start monitoring
- `stop_farm(farm_id)` — gracefully stop all threads
- `pause_farm(farm_id)` / `resume_farm(farm_id)`
- `get_farm_status(farm_id)` — aggregate thread statuses
- `distribute_channels(farm_id)` — evenly split channels across threads
- `redistribute_on_thread_failure(farm_id, failed_thread_id)`
- Redis pub/sub for `farm:{farm_id}:events` real-time log stream

**core/farm_thread.py**
- `FarmThread` class — state machine for single account
- States: idle → subscribing → monitoring → commenting → cooldown → (quarantine)
- `subscribe_to_channels()` — join assigned channels (with delays)
- `subscribe_via_folder(invite_link)` — instant join via folder invite
- `monitor_new_posts()` — poll channels for new posts
- `generate_and_post_comment(post)` — AI comment generation + posting
- `handle_flood_wait(seconds)` — enter cooldown/quarantine
- `handle_mute()` — detect mute, enter quarantine, attempt recovery
- Anti-detection: random delays, typing simulation, read simulation

**core/channel_parser_service.py**
- `ChannelParserService` class
- `parse_channels(keywords, filters, account_id)` — search Telegram for channels
- `validate_channel(channel)` — check has_comments, active, member_count
- `save_to_database(channels, database_id)` — save parsed channels
- Uses Telethon `client.get_dialogs()` and `client(SearchRequest(...))`

**core/profile_factory.py**
- `ProfileFactory` class
- `generate_profile(template_id, account_id)` — AI-generate name, bio, avatar
- `apply_profile(account_id, profile)` — set name, bio, avatar via Telethon
- `create_and_pin_channel(account_id, template)` — create personal channel + first post
- `mass_generate_profiles(account_ids, template_id)` — batch generation
- Uses AI router for text generation, external image APIs for avatars

### 3.3 API Endpoints

```
POST   /v1/farm                    — Create farm config
GET    /v1/farm                    — List farms
GET    /v1/farm/{id}               — Get farm with thread statuses
PUT    /v1/farm/{id}               — Update farm config
POST   /v1/farm/{id}/start         — Start farm
POST   /v1/farm/{id}/stop          — Stop farm
POST   /v1/farm/{id}/pause         — Pause farm
POST   /v1/farm/{id}/resume        — Resume farm
GET    /v1/farm/{id}/threads       — List threads with stats
GET    /v1/farm/{id}/events        — Recent events (paginated)
WS     /v1/farm/{id}/logs          — WebSocket real-time logs

POST   /v1/channel-db              — Create channel database
GET    /v1/channel-db              — List databases
GET    /v1/channel-db/{id}         — Get database with channels
POST   /v1/channel-db/{id}/import  — Import channels (manual paste)
DELETE /v1/channel-db/{id}/channels/{cid} — Remove channel

POST   /v1/parser/channels         — Start channel parsing job
POST   /v1/parser/users            — Start user parsing job
GET    /v1/parser/jobs              — List parsing jobs
GET    /v1/parser/jobs/{id}        — Get job status + results

POST   /v1/profiles/generate       — Generate AI profile for account
POST   /v1/profiles/mass-generate  — Batch generate profiles
POST   /v1/profiles/apply/{account_id} — Apply profile to account
POST   /v1/profiles/create-channel/{account_id} — Create + pin channel
GET    /v1/profiles/templates      — List profile templates
POST   /v1/profiles/templates      — Create template
```

### 3.4 Frontend Pages

**/app/farm** — Farm Control Panel
- Farm list with status badges (running/stopped/paused)
- Create farm wizard: select accounts → select channel DB → configure prompt/tone → start
- Thread grid: each thread shows account name, status, channels count, comments sent, health score
- Real-time log panel (WebSocket) with per-thread filtering
- Quick actions: start/stop/pause, redistribute channels

**/app/parser** — Channel & User Parser
- Keyword input + filters (min members, language, has_comments)
- Live results as channels are found
- Save to channel database
- Channel database manager with blacklist/whitelist

**/app/profiles** — Profile Factory
- Template builder (gender, geo, bio pattern, avatar style)
- Mass generate preview
- Apply to selected accounts
- Channel creation wizard

## 4. Sprint 6: Anti-Detection & Warmup

### Tables (migration 20260311_10)
```sql
warmup_configs (
  id, tenant_id, workspace_id, name, status,
  mode VARCHAR(20) DEFAULT 'conservative', -- conservative, moderate, aggressive
  safety_limit_actions_per_hour INT DEFAULT 5,
  active_hours_start INT DEFAULT 9,
  active_hours_end INT DEFAULT 23,
  warmup_duration_minutes INT DEFAULT 30,
  interval_between_sessions_hours INT DEFAULT 6,
  enable_reactions BOOLEAN DEFAULT true,
  enable_read_channels BOOLEAN DEFAULT true,
  enable_dialogs_between_accounts BOOLEAN DEFAULT true,
  target_channels JSONB DEFAULT '[]',
  created_at, updated_at
);

warmup_sessions (
  id, tenant_id, warmup_id, account_id, status,
  actions_performed INT DEFAULT 0,
  started_at, completed_at, next_session_at
);

account_health_scores (
  id, tenant_id, account_id,
  health_score INT DEFAULT 100,  -- 0-100
  survivability_score INT DEFAULT 100,  -- 0-100
  flood_wait_count INT DEFAULT 0,
  spam_block_count INT DEFAULT 0,
  successful_actions INT DEFAULT 0,
  hours_without_error INT DEFAULT 0,
  profile_completeness INT DEFAULT 0,
  account_age_days INT DEFAULT 0,
  last_calculated_at TIMESTAMP,
  factors JSONB DEFAULT '{}'
);
```

### Modules
- `core/warmup_engine.py` — WarmupEngine with session scheduling
- `core/anti_detection.py` — AntiDetection (typing simulation, random delays, read simulation)
- `core/health_scorer.py` — HealthScorer (calculate health + survivability from factors)
- `core/quarantine_manager.py` — QuarantineManager (detect mute, auto-lift, cooldown scheduling)

## 5. Sprint 7: Advanced Modules

### Tables (migration 20260311_11)
```sql
reaction_jobs (id, tenant_id, workspace_id, farm_id, status, target_channels, reaction_emoji, ...);
chatting_configs (id, tenant_id, workspace_id, name, status, target_chats, prompt, tone, ...);
dialog_configs (id, tenant_id, workspace_id, name, status, prompt, redirect_url, ...);
user_parsing_results (id, tenant_id, job_id, telegram_user_id, username, first_name, ...);
telegram_folders (id, tenant_id, account_id, folder_name, invite_link, channels JSONB, ...);
```

### Modules
- `core/mass_reactions.py` — MassReactionEngine
- `core/neuro_chatting.py` — NeuroChatEngine (comment in group chats)
- `core/neuro_dialogs.py` — NeuroDialogEngine (auto-reply to DMs)
- `core/user_parser.py` — UserParser (extract users from chats/groups)
- `core/folder_manager.py` — FolderManager (create/manage Telegram folders)

## 6. Sprint 8: Intelligence & Scale

### Modules
- `core/channel_map.py` — ChannelMap (index and categorize 400K+ channels)
- `core/campaign_manager.py` — CampaignManager (bundle: channels + accounts + prompts + schedule)
- WebSocket real-time dashboard per thread
- Analytics: ROI, conversions, reach, A/B prompt testing

## 7. Testing Strategy

Each sprint includes:
- Unit tests for all core modules (pytest)
- Integration tests for API endpoints
- Tenant isolation tests (cross-tenant access denied)
- Worker tests with mocked Telethon clients
- Frontend component tests

## 8. Non-Functional Requirements

- All new tables must have RLS policies
- All AI calls route through core/ai_router.py
- All async jobs use enqueue_app_job() pattern
- All config via config.py env vars
- Max 50 concurrent Telethon connections per worker process
- Health scoring recalculated every 5 minutes
- Farm events retained for 30 days
- WebSocket connections authenticated via JWT
