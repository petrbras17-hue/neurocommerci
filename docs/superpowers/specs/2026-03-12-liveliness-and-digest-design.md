# Unified Liveliness Service + Нейросводка Digest

**Date:** 2026-03-12
**Status:** Approved
**Approach:** #3 — Unified Agent (one Docker Compose service, two internal modules)

## Problem

1. Telegram bans idle or bot-like accounts. Accounts need continuous human-like activity to survive.
2. The team has no real-time visibility into what accounts, farms, parsers, and deploys are doing. All events should flow to the existing Telegram digest chat.

## Design Decisions

- **Full simulation level (C):** online/offline by timezone, story viewing, channel reading, search behavior, profile evolution, inter-account dialogs, timezone sleep patterns
- **Digest target:** Existing `DIGEST_CHAT_ID` via `DIGEST_BOT_TOKEN` (already configured in .env)
- **Architecture:** Standalone `liveliness_service.py` in Docker Compose, two async modules sharing one event loop

## Architecture

```
liveliness_service.py (Docker Compose service)
├── LifelinessAgent (core/liveliness_agent.py)
│   └── Per-account AccountLifeLoop
│       ├── online_presence (continuous, UpdateStatus by timezone)
│       ├── story_viewing (2-5x/day)
│       ├── channel_reading (3-8x/day, scroll subscribed channels)
│       ├── search_behavior (1-3x/day, random keyword searches)
│       ├── profile_evolution (1x/week, AI-generated bio/photo/status)
│       ├── inter_dialog (1-2x/day, forward posts between own accounts)
│       └── timezone_sleep (offline 23:00-07:00 ± 30min jitter)
├── DigestReporter (core/digest_reporter.py)
│   └── Redis PubSub listener → format → Telegram send
└── EventBus (core/event_bus.py)
    └── Redis pub/sub wrapper (publish_event / subscribe)
```

## New Files

| File | Purpose |
|---|---|
| `core/event_bus.py` | Redis pub/sub wrapper: `publish_event(channel, data)`, `subscribe(patterns, callback)` |
| `core/liveliness_agent.py` | `LifelinessAgent` class, `AccountLifeLoop` per-account coroutine |
| `core/digest_reporter.py` | `DigestReporter` class, event formatting, Telegram delivery |
| `liveliness_service.py` | Entry point: starts both modules in one asyncio loop |

## LifelinessAgent Detail

### AccountLifeLoop

Each account runs its own async loop with a personal schedule derived from:
- Account timezone (from proxy geo or manual setting)
- Account "personality seed" (deterministic jitter from account ID)
- Current health score (reduces activity if health < 50)

### Activity Schedule

| Activity | Frequency | Jitter | Telethon Methods |
|---|---|---|---|
| online_presence | continuous | ±15min transitions | `UpdateStatusRequest(offline=bool)` |
| story_viewing | 2-5x/day | ±30% interval | `stories.GetPeerStoriesRequest`, `stories.ReadStoriesRequest` |
| channel_reading | 3-8x/day | ±25% interval | `messages.GetHistoryRequest`, `messages.ReadHistoryRequest` |
| search_behavior | 1-3x/day | ±40% interval | `contacts.SearchRequest` |
| profile_evolution | 1x/week | ±1 day | `account.UpdateProfileRequest`, `photos.UploadProfilePhotoRequest` |
| inter_dialog | 1-2x/day | ±30% interval | `messages.ForwardMessagesRequest` |
| timezone_sleep | 23:00-07:00 | ±30min | `UpdateStatusRequest(offline=True)`, pause all activities |

### Safety Rules

- Health check via `HealthScorer` before each activity cycle
- If health_score < 30: pause all write activities, read-only mode
- If frozen detected: stop loop, publish `nc:event:account` with status=frozen
- If FloodWait: respect wait time + 50% extra, publish warning event
- 1 proxy per account (existing rule)
- Never call `send_code_request` on purchased sessions
- Use existing `AntiDetection` class for typing/reading delays

### Connection Management

- One Telethon client per account, lazy connect on first activity
- Disconnect after timezone_sleep starts
- Reconnect on wake with fresh session validation
- Connection pool limit: configurable via `LIVELINESS_MAX_CONCURRENT` (default 10)

## DigestReporter Detail

### Redis PubSub Channels

| Channel Pattern | Source | Events |
|---|---|---|
| `nc:event:deploy` | Manual or CI | deploy start/complete/fail |
| `nc:event:account` | LifelinessAgent, health checks | status change, frozen, banned, unfrozen |
| `nc:event:health` | HealthScorer | hourly health summary, threshold alerts |
| `nc:event:parsing` | Parser jobs | job start/complete, channel counts |
| `nc:event:farm` | FarmOrchestrator | farm start/stop/pause, thread errors |
| `nc:event:error` | Any service | critical errors, unhandled exceptions |
| `nc:event:system` | Liveliness service | service start/stop, config changes |

### Message Format

```
{emoji} {CATEGORY} | {HH:MM}
{summary line 1}
{summary line 2}
{optional metrics}
```

Emojis by category:
- deploy: rocket or green/red circle
- account: person emoji
- health: chart emoji
- parsing: magnifying glass
- farm: tractor
- error: red alert
- system: gear

### Delivery Rules

- Batch messages within 2-second window to avoid Telegram rate limits
- Max 30 messages/minute to digest chat
- Critical errors (account banned, service crash) send immediately
- Hourly health summary is a single aggregated message
- Include inline keyboard "Details" button linking to `/app/health` or `/app/farm` where applicable

## Docker Compose Addition

```yaml
liveliness:
  build: .
  command: python liveliness_service.py
  depends_on: [db, redis]
  restart: unless-stopped
  env_file: .env
  deploy:
    resources:
      limits:
        memory: 512M
```

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `LIVELINESS_ENABLED` | `true` | Master switch |
| `LIVELINESS_MAX_CONCURRENT` | `10` | Max simultaneous account connections |
| `LIVELINESS_TIMEZONE` | `Europe/Moscow` | Default timezone for accounts |
| `LIVELINESS_HEALTH_THRESHOLD` | `30` | Below this = read-only mode |
| `LIVELINESS_POLL_INTERVAL_SEC` | `60` | Account list refresh interval |
| `DIGEST_ENABLED` | `true` | Master switch for digest |
| `DIGEST_BATCH_WINDOW_SEC` | `2` | Batch window before sending |
| `DIGEST_MAX_PER_MINUTE` | `30` | Rate limit |

## Integration Points

- **EventBus** should be imported by existing services (farm_orchestrator, channel_parser_service, ops_api) to publish events. This is additive — existing code works without it, events are optional.
- **HealthScorer** already exists — LifelinessAgent calls it, doesn't replace it.
- **AntiDetection** already exists — LifelinessAgent uses it for delay simulation.
- **Telethon sessions** from `data/sessions/` — same session files used by farm threads.

## Out of Scope

- SpamBot auto-appeal (requires CAPTCHA solving, separate feature)
- AI-generated comments in liveliness (that's farm_thread's job)
- Proxy rotation (accounts keep their assigned proxy)
- Account purchasing or registration
