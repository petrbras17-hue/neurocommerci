# NEURO COMMENTING Engine — Deep Technical Audit

**Date:** 2026-03-23
**Scope:** 10 core files of the commenting engine
**Type:** Research only — no code modifications

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [File-by-File Analysis](#2-file-by-file-analysis)
3. [Critical Questions — Answers](#3-critical-questions--answers)
4. [Anti-Detection Measures Summary](#4-anti-detection-measures-summary)
5. [AI Model Usage](#5-ai-model-usage)
6. [Timing & Delay Architecture](#6-timing--delay-architecture)
7. [Error Handling & Resilience](#7-error-handling--resilience)
8. [Potential Issues & Ban Risks](#8-potential-issues--ban-risks)
9. [Strengths](#9-strengths)
10. [Recommendations](#10-recommendations)

---

## 1. Architecture Overview

The system has **TWO parallel commenting engines** that evolved over time:

### Engine 1: Legacy Worker-Based (worker.py + comments/*)
- **Entry point:** `worker.py` → `AccountWorker`
- **Queue:** Redis task queue (`comment_tasks` stream)
- **Generator:** `comments/generator.py` (Gemini-only, google-genai SDK)
- **Poster:** `comments/poster.py` (CommentPoster)
- **Anti-ban:** `utils/anti_ban.py` (AntibanManager)
- **Scenario system:** A (no link, 70%) / B (hidden product link, 30%)
- **Orchestrator layer:** `core/ai_orchestrator.py` (Gemini decision/review)

### Engine 2: Farm-Based SaaS (farm_thread.py + smart_commenter.py + anti_detection.py)
- **Entry point:** `core/farm_thread.py` → `FarmThread` state machine
- **Strategy:** `core/smart_commenter.py` (CommentOrchestrator)
- **Generator:** `core/smart_commenter.py` → `CommentGenerator` (via `ai_router.py`)
- **Anti-detection:** `core/anti_detection.py` (AntiDetection class)
- **AI routing:** `core/ai_router.py` (Gemini + OpenRouter, boss/manager/worker tiers)
- **Supporting:** `core/neurocommenting_v2.py` (blacklists, whitelists, targeting, auto-DM, presets)

The Farm-based engine is the newer, more sophisticated system intended for SaaS use. The legacy worker-based engine is still operational for the standalone deployment.

---

## 2. File-by-File Analysis

### 2.1. comments/generator.py (271 lines) — Legacy AI Comment Generation

**Current approach:**
- Uses Google Gemini API exclusively (google-genai SDK)
- Lazy client initialization — creates `genai.Client` on first call
- Two scenario types: A (no product mention) and B (with `@BotUsername` mention)
- Model fallback chain: primary model → flash model (configurable via `GEMINI_MODEL` / `GEMINI_FLASH_MODEL`)

**AI model usage:**
- Temperature: 0.9 (high creativity)
- Top-p: 0.95
- Max output tokens: 300
- 30-second timeout per API call
- Runs in `asyncio.to_thread()` (blocking SDK call offloaded to thread pool)

**Quality controls:**
- Comment validation: 5-350 chars, max 35 words
- Scenario A: rejects if contains `@BotUsername` or product name
- Scenario B: rejects if does NOT contain `@BotUsername`
- Forbidden patterns: "как бот", "я бот", "сгенерирован", "artificial", "нейросет", "prompt", "gpt"
- Deduplication: last 100 comments tracked; Jaccard word similarity > 0.8 = duplicate
- Text cleaning: removes quotes, "Комментарий:" prefixes, extra whitespace

**Fallback:**
- 18 hardcoded Russian fallback comments for Scenario A
- 14 template fallbacks for Scenario B (with `{m}` placeholder for bot mention)

**Issues:**
- Deduplication is in-memory only — resets on restart
- Similarity check is word-overlap (Jaccard), not semantic — two comments with synonyms pass
- Fallback pool is small (18 + 14) — repeated fallbacks could flag accounts
- No post context analysis beyond raw text truncation

### 2.2. comments/poster.py (754 lines) — Legacy Comment Posting

**Current approach:**
- Pops posts from `ChannelMonitor.queue`
- Multi-layer decision: AI orchestrator → keyword analysis → account selection → rate limiting → policy engine
- Two posting modes: direct send and emoji-swap (send emoji first, edit to text after 60s)

**Timing and delays:**
- Pre-comment passive action: 25% chance → random action + 2-5s delay
- Typing simulation via `AntibanManager.send_typing()` before every message
- Rate limiter controls inter-comment delays
- Rest periods: 15-45 minutes after 8-10 comments in a row

**Does it wait for other comments?**
- **YES** — configurable via `MIN_EXISTING_COMMENTS_BEFORE_COMMENT` setting
- Fetches real-time reply count from Telegram (`msg.replies.replies`)
- If below threshold: requeue up to `MIN_COMMENTS_RECHECK_MAX_ATTEMPTS` times
- After max attempts: skip the post entirely

**Emoji-swap mechanism:**
- 60% of Scenario B comments use emoji-swap (when enabled)
- Step 1: Send random emoji from pool of 10 (with typing simulation)
- Step 2: `asyncio.sleep(60)` then edit message to real comment text
- Scenario B text gets hidden link: `@BotUsername` replaced with `<a href="bot_link">один VPN-бот</a>`
- 7 different link word variants per product category (VPN, AI, Bot, Service)

**Error handling:**
- `FloodWaitError`: notify admin, set account cooldown, retry
- `UserBannedInChannelError`: save as failed, notify admin
- `ChatWriteForbiddenError`: disable comments for channel in DB
- `ChannelPrivateError`: skip
- `MsgIdInvalidError`: skip
- Generic exceptions: retry with account error recording
- Graceful shutdown: waits for all pending swap tasks

**Human-gated mode:**
- When `HUMAN_GATED_COMMENTS=true`: only posts pre-approved text (no AI generation)
- Emoji swap disabled in human-gated mode

### 2.3. comments/templates.py (181 lines) — Prompt Templates

**System prompt:**
- Category-aware: VPN, AI, Bot, Service — different topic descriptions
- Russian language, conversational style
- Hard 30-word limit in prompt
- Prohibits: praise reviews, ads, bot-like comments, templates, >2 emojis
- 5 persona styles: casual, formal, slang, tech, skeptic

**Scenario A prompt:**
- Context: post text (truncated to 1500 chars)
- Includes 5 example comments
- Rules: no products/services/bots/links
- "Write ONLY comment text, no quotes or explanations"

**Scenario B prompt:**
- Dynamic — includes `@BotUsername` from current settings
- "Naturally mention that you use {mention}"
- "Link should look like casual mention, not advertising"
- Max 30 words, 15-25 preferred

**Product cache:**
- `SettingsCache` invalidates when product category/bot mention changes
- Template B fallbacks regenerated on product change

### 2.4. comments/scenarios.py (64 lines) — Scenario Selection

- Binary choice: Scenario A (no link) vs Scenario B (with link)
- Default ratio: `SCENARIO_B_RATIO` (0.3 = 30% B)
- Anti-pattern protection: max 2 consecutive B scenarios in last 5
- History tracked in memory (last 50 entries)

### 2.5. core/smart_commenter.py (~1200 lines) — Farm Comment Strategy

**This is the most sophisticated commenting engine in the system.**

**Architecture:**
- `PostAnalyzer` — AI-powered post analysis (topic, sentiment, key points, language, promotional detection)
- `CommentGenerator` — AI comment generation with tone/style/gap-filling
- `CommentStrategy` — rule-based decision engine (rate limits, never-first rule, frequency filters)
- `PromptImprover` — two-stage generation (improve prompt → generate comment)
- `CommentOrchestrator` — integrates all four into a single pipeline

**10 comment styles for A/B testing:**
1. question — asks clarifying question
2. agree — sincere agreement
3. supplement — new fact/argument
4. joke — witty/ironic observation
5. expert — domain expert insight
6. personal — first-person experience
7. quote — quotes striking phrase from post
8. emoji — emotional emoji reaction
9. controversial — polite challenge
10. gratitude — thanks the author

**Style rotation:** cycles through styles, never repeats same style twice in a row.

**5 tone types:** positive, hater, emotional, expert, witty — each with specific AI instructions.

**"Never first commenter" rule (CRITICAL):**
- `min_existing_comments` default = 1 (configurable per farm)
- If fewer existing comments than threshold → skip post entirely
- `_MIN_WAIT_BEFORE_FIRST_COMMENT_SEC = 120` (2 min)
- `_MAX_WAIT_BEFORE_FIRST_COMMENT_SEC = 600` (10 min)

**Rate limiting:**
- `COMMENT_INTERVAL_MIN_SEC = 350` (~6 min)
- `COMMENT_INTERVAL_MAX_SEC = 400` (~6.7 min)
- `MAX_COMMENTS_PER_HOUR_SAFE = 12`
- Default `max_comments_per_day = 50`
- Account rotation every N comments (default 5)
- Hourly bucket and daily counter tracked per orchestrator instance

**Frequency strategies:**
- `all` — comment on every post
- `30pct` — random 30% of posts
- `by_keywords` — only posts matching configured keywords

**Emoji-first trick:**
- Step 1: send random emoji from `_SAFE_EMOJIS` pool of 10
- Step 2: wait 40-55 seconds (`_EMOJI_TRICK_EDIT_DELAY_MIN/MAX`)
- Step 3: edit message to real comment text
- Interruptible: respects stop_event during wait

**Two-stage prompt improvement (PromptImprover):**
- Enabled via `AI_PROMPT_IMPROVEMENT_ENABLED="true"`
- Stage 1: AI analyzes post + context → creates improved prompt (task_type: `prompt_improvement`)
- Stage 2: CommentGenerator uses improved prompt for final comment
- Fallback: if stage 1 fails → direct generation without improvement
- This is the "Vels (n8n Nano Banana Bot)" pattern

**Existing comments analysis:**
- Fetches up to 20 existing comments
- AI analyzes: top themes, gaps in discussion, opportunity_score (0.0-1.0)
- If `opportunity_score < 0.2` → skip generation entirely (saves AI budget)
- Comment generator receives gaps as instruction: "fill one of these gaps"

**A/B result tracking:**
- `record_ab_result()` saves style, tone, reactions, replies, was_deleted to DB
- Used for style performance analysis over time

### 2.6. core/neurocommenting_v2.py (762 lines) — Advanced Comment Logic

**This file is NOT a commenting engine itself — it provides supporting SaaS features:**

- **Blacklist/Whitelist:** per-workspace channel blacklist/whitelist management
  - Auto-blacklist on ban (`auto_blacklist_on_ban`)
  - Auto-whitelist on successful comment (`auto_whitelist_on_success`)
  - Tracked with `successful_comments` counter
- **Comment as channel:** posts comments from account's pinned channel identity (not personal account)
  - Uses `SendMessageRequest` with `send_as` parameter
  - 2-6 second human delay before sending
- **Auto-DM:** automatic replies to incoming DMs
  - Per-farm configuration with daily limits
  - Dedup: in-memory set `(farm_id, sender_id, date)`
  - 5-15 second delay before responding
- **Targeting:** `filter_posts_by_targeting()` with 3 modes
  - `all` — no filter
  - `random_pct` — random percentage
  - `keyword_match` — regex keyword matching
- **Language detection:** AI-based with DB cache fallback
- **Farm presets:** save/load/delete farm configurations
- **Channel folder import:** import channel IDs from Telegram folders

### 2.7. core/ai_router.py (~400+ lines) — AI Model Routing

**Hybrid provider system:**
- `gemini_direct` — Google Gemini via google-genai SDK
- `openrouter` — OpenRouter API (httpx) for boss/manager/fallback

**Three model tiers:**
- `boss` — deepest reasoning (strategy, global analysis). Approval required.
- `manager` — complex tasks (creative, assistant, expert comments)
- `worker` — fast/cheap (most commenting tasks, parsing, profiles)

**Comment-related task types:**
- `farm_comment` → worker tier
- `farm_comment_hater` → worker tier
- `farm_comment_expert` → manager tier (higher quality for expert comments!)
- `farm_auto_reply` → worker tier
- `farm_dm_sales` → manager tier
- `prompt_improvement` → worker tier

**Budget controls:**
- Daily and monthly budget limits (USD)
- Boss-tier has separate daily budget
- `AI_HARD_STOP_ENABLED` — blocks all AI calls when budget exceeded
- Outcomes: `executed_as_requested`, `downgraded_by_budget_policy`, `blocked_by_budget_policy`

**Output contract:**
- All comment tasks use `json_object` output contract
- Forces structured `{"text": "..."}` responses

**Telemetry:**
- Every AI call recorded: model, provider, tokens, cost, latency
- Quality flags tracking

### 2.8. core/anti_detection.py (389 lines) — Anti-Ban Measures

**Three modes:**
- `conservative` — 2x delays, 20% skip, 0.10s/char typing (new accounts, <3 days)
- `moderate` — 1.3x delays, 10% skip, 0.07s/char typing (3-30 days)
- `aggressive` — 1x delays, 5% skip, 0.04s/char typing (>30 days)

**Typing simulation:**
- Sends `client.action(peer, "typing")` for 1-5 seconds (scaled by mode)
- Capped at 10 seconds maximum

**Reading simulation:**
- Reads messages at ~15 chars/sec (scaled by mode)
- Random jitter +/-30%
- Capped at 15 seconds per message

**Pre-comment delay (CRITICAL):**
- Conservative: 90-180 seconds (1.5 - 3 minutes)
- Moderate: 45-90 seconds
- Aggressive: 15-45 seconds

**Pre-join delay:**
- Conservative: 120-300 seconds (2 - 5 minutes)
- Moderate: 60-180 seconds
- Aggressive: 30-90 seconds

**Inter-action delay:**
- Conservative: 10-30 seconds
- Moderate: 5-15 seconds
- Aggressive: 2-8 seconds

**Online status toggling (Sprint 8):**
- Simulates online/offline cycles via `UpdateStatusRequest`
- Multiple on/off cycles with mode-scaled delays

**Channel browsing simulation (Sprint 8):**
- Reads N recent posts with realistic speed-based pauses
- 200-300 chars/sec reading speed + 0.5-2s random padding

**Random reactions (Sprint 8):**
- 40% chance to react to a random post when visiting a channel
- 14 different reaction emojis
- Uses `SendReactionRequest`

**Per-account interval jitter (Sprint 8):**
- Deterministic per-account offset based on `account_id % 100`
- +/-15% shift of timing range per account — reduces timing fingerprint

**Night awareness:**
- 00:00-07:00 local time → activity multiplier 0.2 (80% reduction)
- Configurable UTC offset (default: Moscow UTC+3)

### 2.9. worker.py (668 lines) — Legacy Comment Execution Worker

**Distributed worker architecture:**
- Each worker claims `MAX_ACCOUNTS_PER_WORKER` accounts via Redis
- Supports pinned phone mode (single-account worker) or dynamic claiming
- Claim TTL: 5 minutes, renewed every 2 minutes

**Comment loop:**
- Checks `AntibanManager.is_active_hours()` — only 8:00-23:00 MSK
- Filters claimed phones by lifecycle stage: `active_commenting` or `execution_ready`
- Filters out `dead`, `restricted`, `frozen`, `expired` health statuses
- Dequeues from Redis `comment_tasks` stream
- Burst detection: >6 actions in 60 seconds → 10s pause + policy check
- Max 3 retries before dead-lettering a task

**Health loop:**
- Periodic `client.get_me()` checks
- Detects `AuthKeyUnregisteredError`, `UserDeactivatedBanError` → marks account as dead
- Staggered start: 60-300s random delay
- Check interval: `SESSION_HEALTH_CHECK_HOURS * 3600`

**Keepalive loop:**
- `client.get_me()` + 30% chance to read 3 dialogs
- Staggered start: 300-900s
- Interval: `KEEP_ALIVE_INTERVAL_HOURS * 3600`

**Account connection:**
- Batch connection with configurable batch size
- Per-account proxy binding (strict proxy mode supported)
- 3-8s delay between batches

### 2.10. core/farm_thread.py (~1400+ lines) — Farm Thread State Machine

**States:** idle → subscribing → monitoring → commenting → cooldown → quarantine → stopped | error

**Channel subscription:**
- Pre-join delay (mode-aware: 30-300s depending on account age)
- Browses 3 posts before joining (simulate interest)
- Inter-action delay after each join
- Handles: FloodWaitError, ChannelPrivateError, UserAlreadyParticipantError
- Alternative: folder invite bulk-join via `JoinChatlistInviteRequest`

**Post monitoring:**
- Polls each assigned channel for last 5 messages
- Tracks `last_seen_id` per (thread, channel) in Redis (7-day TTL)
- Only considers posts with reply groups (discussion enabled)
- `comment_percentage` filter (configurable)
- Simulates reading messages before deciding (anti-detection)
- 30% chance to send random reaction while browsing (passive engagement)

**Smart comment pipeline:**
- Full CommentOrchestrator integration
- Fetches up to 10 existing comments from post thread
- Never-first-commenter rule: if `replies_count == 0` → no existing comments fetched → orchestrator skips

**QualityGate (DO-Framework):**
- 4-level quality check on generated comments
- If fails: auto-retry up to 3 times with style degradation (original → casual → emoji_first)
- Returns best result or original if all retries fail
- Controlled by `FARM_QUALITY_GATE_ENABLED` flag

**Comment posting:**
- Strategy delay respected (from CommentDecision, capped at 600s)
- Fallback to mode-aware pre-comment delay if no strategy delay
- Typing simulation before posting
- Two paths: emoji-first trick (via orchestrator) or direct send
- Post-comment inter-action delay

**FloodWait handling:**
- < 300s → cooldown state (wait × 1.5 safety margin)
- >= 300s → quarantine state (persisted to DB)
- AccountLifecycle transition recorded

**Session death handling:**
- Detects: `AuthKeyUnregisteredError`, `SessionRevokedError`, `AuthKeyDuplicatedError`
- Marks account dead via AccountLifecycle
- Evicts from SessionPool
- Terminates thread immediately

**Mute handling:**
- `ChatWriteForbiddenError`, `UserBannedInChannelError` → 24h quarantine
- Recorded via Channel Intelligence `BanPatternLearner`

**Night mode:**
- Night factor < 1.0 → monitoring delay increased proportionally
- Combined with per-account interval jitter

---

## 3. Critical Questions — Answers

### Q1: Does the bot comment IMMEDIATELY on new posts or wait?

**NO — it never comments immediately.** Multiple delay layers exist:

| Layer | Delay | Where |
|-------|-------|-------|
| SmartCommenter strategy delay | 350-400 sec (default) | `smart_commenter.py` line 745-748 |
| AntiDetection pre-comment delay | 15-180 sec (mode-dependent) | `anti_detection.py` line 140-153 |
| Per-account interval jitter | Variable (account-seed based) | `anti_detection.py` line 327-355 |
| Legacy poster rate limiter | Configurable per account | `poster.py` line 271-273 |
| Night hours multiplier | 5x longer delays at night | `anti_detection.py` line 379-388 |

**Total delay before commenting: typically 6-10+ minutes** in the Farm engine.
The legacy engine uses shorter delays but still has rate limiting + pre-comment passive actions.

### Q2: Does it check if other users commented first before commenting?

**YES — this is a core safety rule.**

- **Farm engine (smart_commenter.py):** `min_existing_comments = 1` (default). Strategy refuses to comment if existing comments < threshold. Checks `replies_count` from post metadata AND fetches actual comment texts for context.
- **Legacy engine (poster.py):** `MIN_EXISTING_COMMENTS_BEFORE_COMMENT` setting. Fetches real reply count from Telegram. Requeues up to `MIN_COMMENTS_RECHECK_MAX_ATTEMPTS` if not enough comments yet.

### Q3: Does it read the post content before generating a comment?

**YES — extensively in the Farm engine, minimally in the Legacy engine.**

- **Farm engine:** Full AI-powered `PostAnalyzer`: extracts topic, sentiment, key_points, suggested_angle, language, is_promotional, has_questions. Also analyzes existing comments for themes, gaps, and opportunity_score.
- **Legacy engine:** Passes raw post text (truncated to 1500 chars) directly to Gemini prompt. Also has keyword-based `PostAnalyzer` for should-comment decision.

### Q4: Does it simulate typing before sending?

**YES — in both engines.**

- **Legacy engine:** `AntibanManager.send_typing()` — sends `SetTypingRequest` + waits `text_len * 0.08` seconds (min 2s, max 25s)
- **Farm engine:** `AntiDetection.simulate_typing()` — sends `client.action(peer, "typing")` for 1-5 seconds (scaled by mode × multiplier)

### Q5: Does it vary comment length and style?

**YES — significantly in the Farm engine.**

- **Farm engine:** 10 distinct styles rotated sequentially, 5 tones, style instructions per comment, post-analysis-driven angle selection, gap-filling from existing comment analysis. Style never repeats consecutively.
- **Legacy engine:** 5 persona styles (casual, formal, slang, tech, skeptic) assigned per account. High temperature (0.9) for variety. But style is per-account, not per-comment.

### Q6: Does it use different AI models for different comment types?

**YES — in the Farm engine via ai_router.py.**

- `farm_comment` → worker tier (cheapest/fastest models)
- `farm_comment_expert` → manager tier (higher quality models)
- `farm_comment_hater` → worker tier
- `prompt_improvement` → worker tier (meta-prompt generation)
- Post analysis and comment context analysis also use worker tier

The Legacy engine uses only Gemini (primary + flash fallback).

### Q7: How does it handle FloodWait errors?

**Comprehensively in both engines.**

- **Farm engine:**
  - < 300s: cooldown state, wait `seconds × 1.5` safety margin
  - >= 300s: quarantine state, persist to DB, notify via events
  - AccountLifecycle stage transition recorded
  - Channel intelligence: ban pattern learning
- **Legacy engine:**
  - `FloodWaitError`: log warning, notify admin via Telegram, set account cooldown via AccountManager, return "retry" for task requeue
  - Policy engine check on every FloodWait

### Q8: Does it rotate accounts for commenting?

**YES — in both engines, differently.**

- **Farm engine:** `account_rotate_every_n = 5` (default). After every 5 comments, orchestrator signals rotation. Each FarmThread is bound to one account, but the FarmOrchestrator manages multi-thread distribution.
- **Legacy engine:** `AccountManager.get_next_available()` rotates through available accounts. Rate limiter tracks per-account cooldowns. Rest periods (15-45 min) force rotation.

---

## 4. Anti-Detection Measures Summary

| Measure | Legacy Engine | Farm Engine |
|---------|:---:|:---:|
| Typing simulation (SetTypingRequest) | Yes | Yes |
| Read simulation (time-based per message) | No | Yes |
| Pre-comment delay | Via rate limiter | 15-180s (mode) |
| Inter-action delays | 2-5s passive action | 2-30s (mode) |
| Online/offline toggling | No | Yes |
| Channel browsing before actions | No | Yes (3-5 posts) |
| Random reactions (passive engagement) | 25% before comments | 30% while monitoring |
| Per-account timing jitter | No | Yes (account_id seed) |
| Night hours reduction | 8-23 MSK only | 0.2x multiplier at night |
| Account age mode selection | Warmup phases | conservative/moderate/aggressive |
| Emoji-first trick | Yes (60s edit delay) | Yes (40-55s edit delay) |
| Lazy day (reduced activity) | Yes (20% chance) | No (uses night multiplier) |
| Never-first-commenter rule | Configurable | Yes (default min=1) |
| Forbidden word filtering | Yes | N/A (AI-generated) |
| Deduplication | In-memory Jaccard | Via style rotation |
| Hidden links (not plain @mentions) | Yes (HTML `<a href>`) | No (farm doesn't do product links) |
| Active hours enforcement | 8-23 MSK | Via night_activity_multiplier |
| Burst detection | No | Yes (6 actions/60s) |

---

## 5. AI Model Usage

### Legacy Engine (comments/generator.py)
- **Provider:** Google Gemini only
- **Models:** `GEMINI_MODEL` (primary) → `GEMINI_FLASH_MODEL` (fallback)
- **Temperature:** 0.9
- **Top-p:** 0.95
- **Max tokens:** 300
- **Timeout:** 30s per attempt
- **System prompt:** Category-aware Russian commenter persona (30-word limit)
- **Review layer:** Optional AI orchestrator review/rewrite after generation

### Farm Engine (smart_commenter.py + ai_router.py)
- **Providers:** Gemini (direct) + OpenRouter (fallback)
- **Tier:** worker (fast/cheap)
- **Temperature:** 0.85 (generation), 0.3 (analysis), 0.6 (prompt improvement)
- **Max tokens:** 150 (generation), 300 (analysis/improvement), 250 (context)
- **System prompt:** Fixed English base + tone instructions + language directive + gap hints
- **Two-stage generation:** Optional PromptImprover (meta-prompt → improved prompt → final comment)
- **Output contract:** Strict JSON `{"text": "..."}`
- **Post analysis AI call:** Extracts topic, sentiment, key_points, suggested_angle, language, is_promotional, has_questions
- **Comment context AI call:** Analyzes existing comments for themes, gaps, opportunity_score, dominant_sentiment

### AI Calls Per Comment (Farm Engine, worst case)
1. Post analysis (worker tier)
2. Existing comments analysis (worker tier)
3. Prompt improvement — stage 1 (worker tier) [optional]
4. Comment generation (worker tier)
5. QualityGate retry generation × 2 [if initial fails]

**Total: 2-6 AI calls per comment.** Cost-optimized by using worker tier for all farm commenting tasks.

---

## 6. Timing & Delay Architecture

### Between discovering a post and commenting (Farm Engine):

```
Post discovered in monitor_new_posts()
  │
  ├─ simulate_reading() — 0.5-15s per message
  ├─ random_reaction() — 30% chance, adds delay
  ├─ inter_action_delay() — 2-30s between channels
  │
  ▼ Strategy check (CommentDecision)
  │
  ├─ delay_seconds from strategy — 350-400s (default)
  │  OR
  ├─ pre_comment_delay() — 15-180s (if no strategy delay)
  │
  ├─ simulate_typing() — 1-5s
  │
  ├─ [IF emoji trick] send emoji → wait 40-55s → edit
  │  OR
  ├─ [IF direct] send_message()
  │
  ├─ inter_action_delay() — 2-30s after posting
  │
  ▼ per_account_interval() — variable between posts
  │
  ▼ monitoring iteration delay — 30-120s × night_factor
```

### Between discovering a post and commenting (Legacy Engine):

```
Post popped from queue
  │
  ├─ AI analysis / keyword analysis
  ├─ Rate limiter check
  ├─ MIN_EXISTING_COMMENTS check (may requeue)
  ├─ 25% passive action + 2-5s delay
  ├─ AI comment generation
  ├─ AI review/rewrite (optional)
  │
  ├─ send_typing() — text_len × 0.08s (2-25s)
  │
  ├─ [IF emoji swap] send emoji → sleep(60s) → edit
  │  OR
  ├─ [IF direct] send_message()
  │
  ▼ rate_limiter.get_next_delay()
```

---

## 7. Error Handling & Resilience

### Covered Telegram Errors:
| Error | Legacy | Farm | Action |
|-------|:---:|:---:|--------|
| `FloodWaitError` | Yes | Yes | Cooldown/quarantine, retry task |
| `UserBannedInChannelError` | Yes | Yes | Record ban, quarantine |
| `ChatWriteForbiddenError` | Yes | Yes | Disable channel / quarantine |
| `ChannelPrivateError` | Yes | Yes | Skip channel |
| `MsgIdInvalidError` | Yes | Yes | Skip post |
| `SlowModeWaitError` | No | Yes | Wait and retry |
| `MessageNotModifiedError` | Yes | No | Treat as success (swap) |
| `AuthKeyUnregisteredError` | Yes | Yes | Mark dead, evict |
| `SessionRevokedError` | No | Yes | Mark dead, evict |
| `AuthKeyDuplicatedError` | No | Yes | Mark dead, evict |
| `UserAlreadyParticipantError` | No | Yes | Skip (already member) |

### Uncovered Scenarios (potential issues):
- **No explicit handling for `PeerFloodError`** — different from FloodWaitError, indicates account flagged for spam
- **No handling for `UserRestricted` errors** — account-level restrictions
- **No handling for `ChatForbiddenError`** (different from `ChatWriteForbiddenError`) — cannot even read
- **Auto-DM dedup is in-memory set** — resets on restart, could send duplicate DMs

---

## 8. Potential Issues & Ban Risks

### HIGH RISK

1. **Legacy fallback comments are too few and too generic.**
   - Only 18 Scenario A fallbacks. If AI goes down for extended period, same comments will repeat.
   - Fallbacks are not contextual — "интересно, спасибо за инфо" on a tragedy post = instant flag.
   - **Recommendation:** Expand to 100+ fallbacks with category awareness, or refuse to comment when AI is unavailable.

2. **In-memory deduplication resets on restart.**
   - `_recent_comments` deque in `CommentGenerator` is lost when process restarts.
   - Worker restarts could lead to duplicate comments across sessions.
   - **Recommendation:** Move dedup to Redis with TTL.

3. **Reading simulation speed is unrealistic in anti_detection.py.**
   - `simulate_channel_browse()` uses 200-300 chars/sec reading speed — this is extremely fast (real humans read ~25-40 chars/sec in Russian).
   - Result: browse simulation takes only 0.5-2s per post instead of realistic 5-15s.
   - **Recommendation:** Fix to 20-40 chars/sec.

4. **No detection of channel admin actions.**
   - If a channel admin deletes the bot's comment, there is no callback/check to detect this.
   - Repeated deleted comments in the same channel = high ban risk.
   - **Recommendation:** Periodically check if recent comments still exist; auto-blacklist channels that delete comments.

5. **Emoji-first trick edit is detectable.**
   - Telegram shows "edited" label on messages. A pattern of emoji-then-edit is detectable by moderators and automated tools.
   - The 40-60s delay helps but the pattern is still visible.
   - **Recommendation:** Consider varying the trick more (sometimes don't edit, sometimes use different initial content).

### MEDIUM RISK

6. **Per-account persona style is static in legacy engine.**
   - Each account always uses the same persona (casual, formal, etc.). Over time, this creates a fingerprint.
   - The Farm engine handles this much better with 10-style rotation.

7. **Legacy engine does not browse channels before commenting.**
   - No `simulate_channel_browse()` or `simulate_reading()` in the poster.py path.
   - Comment appears without the account having "read" the post — detectable via Telegram's "read" indicators.

8. **Farm engine's post analysis sends full post text to AI.**
   - Post text is truncated to 2000 chars but still sent to external AI providers.
   - If the channel content is sensitive/private, this is a data leak.

9. **No comment deletion/cleanup mechanism.**
   - If a comment gets heavily downvoted or reported, there's no automatic cleanup.
   - Old comments remain forever, even if the product/bot changes.

10. **Rate limiter state not shared between workers.**
    - Each worker has its own `RateLimiter` instance. If multiple workers claim the same account (shouldn't happen due to Redis claims, but edge case), rate limits won't aggregate.

### LOW RISK

11. **`_dm_sent_today` set in neurocommenting_v2.py is never cleaned up.**
    - In-memory set grows indefinitely (no daily reset logic visible).
    - Production note says "replace with Redis SET" — not yet done.

12. **QualityGate retry creates new CommentOrchestrator.process_post() calls.**
    - These may trigger additional AI calls (post analysis + context analysis again).
    - Could multiply AI cost if quality gate frequently fails.

---

## 9. Strengths

1. **Never-first-commenter rule** is well-implemented in both engines — critical for avoiding detection.

2. **Multi-layer anti-detection** in the Farm engine is comprehensive: typing, reading, browsing, reactions, jitter, night mode, account age awareness.

3. **AI-powered post analysis** in smart_commenter.py produces contextually relevant comments by analyzing topic, sentiment, existing comment gaps.

4. **Two-stage prompt improvement** (PromptImprover) is a sophisticated technique that can significantly improve comment quality.

5. **10-style A/B testing rotation** provides excellent variety and avoids pattern detection.

6. **Emoji-first trick** is a clever anti-spam bypass — initial emoji passes through filters, then edited to real text.

7. **Channel Intelligence integration** in farm_thread.py — learns from bans, adapts behavior per channel.

8. **QualityGate with auto-retry** ensures minimum comment quality with style degradation fallback.

9. **Budget controls** prevent runaway AI costs with daily/monthly limits and tier-based routing.

10. **Graceful degradation everywhere** — AI failure → heuristic fallback, generation failure → skip, swap failure → direct send.

---

## 10. Recommendations

### Immediate (ban risk reduction)

1. **Move deduplication to Redis** — survive worker restarts, share across workers.
2. **Fix reading simulation speed** — 200-300 chars/sec is superhuman; use 20-40 chars/sec.
3. **Expand fallback pool** to 100+ category-aware comments or disable commenting when AI is down.
4. **Add comment existence check** — verify recent comments weren't deleted by admins.
5. **Add `PeerFloodError` handling** — this error means the account is flagged for spam by Telegram.

### Short-term (quality improvement)

6. **Unify the two engines** — maintaining legacy + farm engines doubles maintenance burden.
7. **Add semantic deduplication** — use embedding similarity instead of word overlap (Jaccard).
8. **Track comment deletion rate per channel** — auto-blacklist channels that frequently delete.
9. **Add comment warmth scoring** — detect and avoid comments that are "too perfect" or too AI-like.
10. **Replace in-memory `_dm_sent_today`** with Redis SET with TTL.

### Long-term (competitive advantage)

11. **Add post image/media analysis** — many posts are image-only; current engine only reads text.
12. **Add thread-aware commenting** — reply to existing comments (not just to the post) for more natural engagement.
13. **Add channel personality profiling** — learn each channel's typical comment style and match it.
14. **Add comment performance feedback loop** — track which comments get reactions/replies and use that data to train better generation.
15. **Add multi-language support** — current prompts and fallbacks are Russian-only; expand for CIS markets.

---

## Appendix: File Dependency Map

```
worker.py
  ├── comments/poster.py (CommentPoster)
  │     ├── comments/generator.py (CommentGenerator)
  │     │     ├── comments/templates.py (prompts, styles, fallbacks)
  │     │     └── comments/scenarios.py (A/B selection)
  │     ├── core/ai_orchestrator.py (review layer)
  │     └── utils/anti_ban.py (AntibanManager)
  └── channels/monitor.py (ChannelMonitor)

core/farm_thread.py (FarmThread state machine)
  ├── core/smart_commenter.py (CommentOrchestrator)
  │     ├── PostAnalyzer (AI post analysis)
  │     ├── CommentGenerator (AI comment generation)
  │     ├── CommentStrategy (rule-based decisions)
  │     └── PromptImprover (two-stage generation)
  ├── core/anti_detection.py (AntiDetection)
  ├── core/ai_router.py (route_ai_task)
  ├── core/quality_gate.py (QualityGate)
  └── core/neurocommenting_v2.py (blacklists, targeting, auto-DM)
```
