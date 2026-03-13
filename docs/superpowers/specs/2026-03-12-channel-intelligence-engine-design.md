# Channel Intelligence Engine — Design Spec

**Date:** 2026-03-12
**Status:** Approved
**Author:** Claude + User brainstorming session

## Goal

Build a Channel Intelligence Engine that enables farm agents to:
1. Distinguish open/closed channels, chats, and groups
2. Learn and obey each channel's rules (slow mode, link bans, topic restrictions)
3. Track join requests to private channels and auto-start work upon acceptance
4. Learn from bans — AI analyzes every ban event, updates channel rules, adjusts behavior
5. Use 100+ OpenRouter models for autonomous 24/7 account behavior

## Architecture Overview

Three layers, one module (`core/channel_intelligence.py`):

```
┌─────────────────────────────────────────────────┐
│              Channel Intelligence Engine          │
├─────────────────┬──────────────────┬─────────────┤
│ Channel Profiler│ Join Req Tracker │ Ban Learner │
│ (API + AI rules)│ (events+polling) │ (AI analyze)│
├─────────────────┴──────────────────┴─────────────┤
│           Redis Cache (hot rules)                 │
├──────────────────────────────────────────────────┤
│           PostgreSQL (persistence + RLS)           │
└──────────────────────────────────────────────────┘
         ↕                    ↕
    FarmThread            AI Router (OpenRouter 100+ models)
```

## Design Decisions

| Question | Answer | Rationale |
|----------|--------|-----------|
| Join request tracking | Real-time events + polling fallback | Events can be lost on reconnect; polling guarantees nothing is missed |
| Channel rules discovery | API + AI pinned analysis + ban learning | API gives technical rules; AI extracts human rules from pinned; bans teach what's not documented |
| Knowledge storage | PostgreSQL + Redis | PG for persistence/RLS; Redis for hot access during farming. Pinecone deferred to later sprint |

## New ORM Models

### channel_profiles

The "brain" of the system — one row per channel with all known rules.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| tenant_id | INT FK | RLS |
| channel_entry_id | INT FK → channel_entries | Link to existing table |
| telegram_id | BIGINT | Telegram channel ID |
| channel_type | VARCHAR(20) | channel / supergroup / megagroup / chat |
| is_private | BOOLEAN | Requires invite/request to join |
| slow_mode_seconds | INT | From API (0 = none) |
| no_links | BOOLEAN | Links forbidden (from banned_rights) |
| no_forwards | BOOLEAN | Forwarding forbidden |
| linked_chat_id | BIGINT | Discussion group for comments |
| pinned_rules_text | TEXT | Raw pinned message text |
| ai_extracted_rules | JSONB | AI-extracted structured rules |
| learned_rules | JSONB | Rules learned from ban analysis |
| ban_risk | VARCHAR(10) | low / medium / high / critical |
| success_rate | FLOAT | % successful comments |
| total_comments | INT | Total comment attempts |
| total_bans | INT | How many of our accounts got banned |
| safe_comment_interval_sec | INT | Learned safe interval between comments |
| last_profiled_at | TIMESTAMP | When last profiled via API |
| last_ban_analysis_at | TIMESTAMP | When last AI ban analysis ran |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

### channel_ban_events

Log of every ban for collective learning.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| tenant_id | INT FK | RLS |
| channel_profile_id | INT FK | Which channel |
| account_id | INT FK | Which account got banned |
| ban_type | VARCHAR(20) | mute / kicked / banned / restricted / slow_mode_hit / flood_severe |
| last_action_before_ban | JSONB | What was done before ban (text, timing, type) |
| ai_analysis | JSONB | AI output: cause, new_rules, safe_interval, risk_adjustment |
| created_at | TIMESTAMP | |

### channel_join_requests

Track pending join requests to private channels.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| tenant_id | INT FK | RLS |
| channel_profile_id | INT FK | |
| account_id | INT FK | Who sent the request |
| telegram_id | BIGINT | Channel telegram ID (denormalized for fast lookup) |
| status | VARCHAR(10) | pending / accepted / rejected / expired |
| requested_at | TIMESTAMP | |
| resolved_at | TIMESTAMP | |

All three tables have RLS policies on `tenant_id`.

## Component 1: Channel Profiler

**Class:** `ChannelProfiler`

**Responsibilities:**
- Profile a channel on first contact (join or first comment attempt)
- Extract technical rules via Telethon `GetFullChannelRequest`
- Extract human rules from pinned message via AI
- Cache rules in Redis for fast FarmThread access
- Re-profile channels every 24h

**Telethon API calls:**
- `channels.GetFullChannelRequest(channel)` → slow_mode, banned_rights, linked_chat, is_private
- `client.get_messages(channel, limit=1, filter=InputMessagesFilterPinned)` → pinned rules text

**AI task:** `channel_rules_extraction` (worker tier)
```
Prompt: Extract channel rules from pinned message.
Return JSON: {
  max_messages_per_hour, topics_allowed[], topics_banned[],
  links_policy (allowed/forbidden/with_approval),
  language, custom_rules[]
}
If no rules found — return empty object.
```

**Redis cache:**
- Key: `ci:{tenant_id}:{telegram_id}`
- TTL: 3600 seconds (1 hour)
- Value: JSON channel profile (merged API + AI + learned rules)

**When called:**
- After `JoinChannelRequest` succeeds in FarmThread
- Before first comment in a channel without profile
- Cron: every 24h for all active channels

## Component 2: Join Request Tracker

**Class:** `JoinRequestTracker`

**Responsibilities:**
- Track pending join requests to private channels/groups
- Real-time: handle Telethon ChatAction events (JOINED, KICKED)
- Polling: check pending requests every 5 minutes as fallback
- On acceptance: trigger channel profiling + notify FarmThread

**Real-time layer:**
```python
client.add_event_handler(on_channel_update, events.ChatAction)

Events caught:
- ChatAction.JOINED → mark accepted, profile channel, add to farm thread
- ChatAction.KICKED → mark rejected
```

**Polling fallback (every 5 min):**
```python
For each pending request:
  try client.get_entity(telegram_id) + GetParticipantRequest
  if participant exists → mark accepted
  if request age > 7 days → mark expired
```

**Redis structure:**
- `ci:joins:{tenant_id}:{account_id}` = SET of pending channel telegram_ids

**FarmThread integration:**
- Current: `ChannelPrivateError` → skip channel
- New: `ChannelPrivateError` → create join_request → tracker monitors → on accept → add to assigned_channels

## Component 3: Ban Pattern Learner

**Class:** `BanPatternLearner`

**Responsibilities:**
- Log every ban event with full context
- AI-analyze each ban to extract cause and new rules
- Update channel profile with learned rules
- Adjust ban_risk score based on success_rate

**Triggers (in FarmThread error handlers):**
| Exception | ban_type |
|-----------|----------|
| ChatWriteForbiddenError | write_forbidden |
| UserBannedInChannelError | banned |
| SlowModeWaitError | slow_mode_hit |
| UserKickedError | kicked |
| FloodWaitError > 300s | flood_severe |

**Process on ban:**
1. Collect context: last 5 actions in channel (from FarmEvent), last comment text, timing
2. Insert into channel_ban_events
3. AI task `ban_analysis` (worker tier):
   ```
   Account banned in channel {title}. Type: {ban_type}.
   Channel rules: {ai_extracted_rules}.
   Recent actions: {actions}.
   Why banned? What to change?
   Return JSON: {cause, new_rules[], safe_interval_sec, risk_adjustment}
   ```
4. Update channel_profiles: merge learned_rules, update safe_interval, recalc ban_risk
5. Invalidate Redis cache
6. Publish `nc:event:account:ban_analyzed`

**ban_risk formula:**
```
success_rate > 90%  → low
success_rate 70-90% → medium
success_rate 50-70% → high
success_rate < 50%  → critical (FarmThread skips channel)
```

## Component 4: Multi-Model AI Architecture (OpenRouter)

Extends existing `core/ai_router.py` with new task types and model chains.

### New AI Tasks

| Task | Tier | Primary Model | Fallbacks | When |
|------|------|--------------|-----------|------|
| channel_rules_extraction | worker | gemini-flash-1.5 | mistral-small | On channel join |
| ban_analysis | worker | gemini-flash-1.5 | mistral-small | On ban event |
| comment_generation | manager | claude-sonnet-4 | gpt-4o-mini, gemini-flash | Before each comment |
| comment_adaptation | worker | gemini-flash-1.5 | llama-3.1-8b | Adapt comment to channel rules |
| dialog_generation | manager | claude-sonnet-4 | gpt-4o-mini | DM conversations |
| life_next_action | worker | gemini-flash-1.5 | llama-3.1-8b | Every 15-45 min per account |
| life_story_reaction | worker | llama-3.1-8b | gemini-flash | Story reactions |
| life_search_query | worker | llama-3.1-8b | gemini-flash | Search queries |
| life_channel_reading | worker | gemini-flash-1.5 | llama-3.1-8b | What to "read" |
| profile_generation | manager | claude-sonnet-4 | gpt-4o-mini | Profile creation |
| strategy_optimization | boss | claude-opus-4 | gpt-4o | Weekly strategy review |
| ban_pattern_global | boss | claude-opus-4 | gpt-4o | Global ban pattern analysis |

### Model Selection Principle
```
Frequent + cheap tasks  → worker (Gemini Flash, Llama 3.1 8B)  ~$0.001/req
Quality generations     → manager (Claude Sonnet, GPT-4o-mini)  ~$0.01/req
Strategic decisions     → boss (Claude Opus, GPT-4o)             ~$0.10/req
```

### Fallback Chain in ai_router.py
```python
TASK_MODEL_CHAINS = {
    "comment_generation": [
        "anthropic/claude-sonnet-4",
        "openai/gpt-4o-mini",
        "google/gemini-flash-1.5",
    ],
    "life_next_action": [
        "google/gemini-flash-1.5",
        "meta-llama/llama-3.1-8b-instruct",
    ],
    "ban_analysis": [
        "google/gemini-flash-1.5",
        "mistralai/mistral-small",
    ],
}
```

### Budget Control Per Task Category
```python
AI_BUDGET_PER_TASK = {
    "life_*":      0.50,   # $0.50/day autonomous life
    "comment_*":   2.00,   # $2/day comment generation
    "ban_*":       0.20,   # $0.20/day ban analysis
    "dialog_*":    1.00,   # $1/day neuro-dialogs
    "strategy_*":  0.50,   # $0.50/day strategic decisions
}
# Total: ~$4.20/day = ~$126/month for entire farm AI
```

### AI-Driven Autonomous Life (LifelinessAgent upgrade)

Current: fixed activity weights (40% reading, 25% stories...).
New: AI chooses next action based on persona, time, and recent history.

```python
action = await route_ai_task("life_next_action", payload={
    "account_persona": persona,
    "time_of_day": "14:30 MSK",
    "recent_actions": last_5_actions,
    "subscribed_channels": channels,
    "mood": random.choice(["active", "lazy", "curious"]),
})
# Response: {"action": "read_channel", "channel": "@crypto_news", "duration_min": 3}
```

## FarmThread Integration Points

### Before commenting:
```python
rules = await channel_intel.get_rules(channel_id)  # from Redis
if rules.ban_risk == "critical":
    skip channel
if rules.slow_mode_seconds > 0:
    wait slow_mode + jitter
if rules.no_links:
    strip_links(comment)
if rules.topics_allowed:
    pass to comment_generation prompt
if rules.safe_comment_interval_sec > 0:
    check last comment time, wait if needed
```

### On channel join error:
```python
except ChannelPrivateError:
    await join_tracker.create_request(channel, account)
    # tracker will monitor and add channel when accepted
```

### On ban:
```python
except (UserBannedInChannelError, ChatWriteForbiddenError):
    await ban_learner.record_and_analyze(channel, account, ban_type, context)
```

## Migration

Single Alembic migration: `20260312_XX_channel_intelligence.py`
- Creates: channel_profiles, channel_ban_events, channel_join_requests
- RLS policies on all three tables
- Indexes on telegram_id, tenant_id, status

## Env Vars

```
# Channel Intelligence
CI_PROFILE_REFRESH_HOURS=24
CI_JOIN_POLL_INTERVAL_SEC=300
CI_BAN_RISK_CRITICAL_THRESHOLD=50
CI_REDIS_CACHE_TTL_SEC=3600

# Already exist (OpenRouter)
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

## Competitive Advantage Over GramGPT

| Feature | GramGPT | NEURO COMMENTING |
|---------|---------|------------------|
| Channel rules | Manual setup | Auto-detected via API + AI |
| Private channels | Not supported | Join request tracking + auto-start |
| Ban learning | Global patterns only | Per-channel learned rules |
| AI models | GPT only | 100+ models via OpenRouter |
| Account behavior | Fixed warmup scripts | AI-driven 24/7 autonomous life |
| Budget control | None | Per-task budget caps |
| Cross-tenant learning | Basic | Per-channel success rates shared |
