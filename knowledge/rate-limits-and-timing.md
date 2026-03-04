# Telegram Rate Limits and Timing

## Official Rate Limits

### Message Sending
| Context | Limit |
|---------|-------|
| Per chat (1-on-1) | 1 msg/sec |
| Per group/channel | 1 msg/sec (20 msg/min aggregate) |
| Broadcast to many chats | 30 msg/sec total, but groups limited to 20/min |
| Inline bot results | 10 results/sec |

### API Calls
| Operation | Approximate Limit |
|-----------|-------------------|
| Total API calls/day | ~5000 (soft limit, varies) |
| GetMessages | ~200/15min |
| GetDialogs | ~50/15min |
| JoinChannel | ~20/day |
| SendMessage to new chats | ~50/day for new accounts, ~200/day for aged |
| GetParticipants | ~200/15min |

### Group/Channel Operations
| Operation | Limit |
|-----------|-------|
| Create groups/channels | 10/day |
| Join groups/channels | 20/day (stricter for new accounts) |
| Add members | 50/day |
| Change group info | ~10/hour |

## FloodWait Handling

### What is FloodWait?

Telegram responds with `FloodWaitError(seconds=N)` when rate limits are exceeded. The `seconds` value is how long to wait before retrying.

### Handling Strategy

```python
except FloodWaitError as e:
    # NEVER ignore FloodWait
    # Add 10-30% random buffer to avoid exact-same-time retry
    buffer = random.uniform(1.1, 1.3)
    wait_time = int(e.seconds * buffer)
    await asyncio.sleep(wait_time)
```

### FloodWait Escalation

Consecutive FloodWait errors for the same operation get progressively longer:
- 1st: ~30s
- 2nd: ~120s
- 3rd: ~300s
- 4th+: ~3600s or more

**Reset:** ~15 minutes of no requests for that operation type.

## Timing Patterns for Anti-Detection

### Human-Like Delay Distribution

Humans don't operate at fixed intervals. Use Gaussian (normal) distribution:

```python
import random

def human_delay(min_sec: float, max_sec: float) -> float:
    """Gaussian-distributed delay between min and max."""
    mean = (min_sec + max_sec) / 2
    std = (max_sec - min_sec) / 4  # 95% within [min, max]
    delay = random.gauss(mean, std)
    return max(min_sec, min(delay, max_sec))
```

### Recommended Delays

| Operation | Min Delay | Max Delay | Notes |
|-----------|-----------|-----------|-------|
| Between comments (same account) | 60s | 300s | Gaussian distribution |
| Between comments (different accounts) | 10s | 30s | Stagger accounts |
| After joining a group → first post | 24h | 72h | CRITICAL — never post immediately |
| Between group joins | 300s | 600s | Max 20/day |
| Between message reads | 2s | 5s | Simulates scrolling |
| Between reactions | 5s | 15s | Natural pace |
| Daily session start | ±30min | ±2h | Vary "wake up" time |

### Session Rest Patterns

After 8-10 comments in a row, take a "rest":
- Duration: 30-90 minutes (Gaussian)
- During rest: only passive actions (read, scroll)
- After rest: resume with new random threshold (8-10 comments)

### Per-Account Sleep Windows

Each account should have a "sleep" period (no activity):
- Duration: 6-9 hours
- Time: e.g., 23:00-07:00 in account's local timezone
- Variation: ±30 minutes daily to avoid exact patterns

### Activity Distribution Over Day

Mimic human activity curve:
```
Morning (07:00-10:00): Light activity, 20% of daily quota
Midday (10:00-14:00): Peak activity, 35% of daily quota
Afternoon (14:00-18:00): Moderate activity, 25% of daily quota
Evening (18:00-22:00): Light activity, 20% of daily quota
Night (22:00-07:00): No activity (sleep window)
```

## Daily Quotas by Account Phase

| Phase | Days Active | Max Comments/Day | Max Joins/Day | Max Reactions/Day |
|-------|-------------|-----------------|---------------|-------------------|
| Readonly | 0-2 | 0 | 2 | 0 |
| Reactions | 3-4 | 0 | 3 | 5 |
| Light | 5-7 | 3 | 3 | 10 |
| Moderate | 8-14 | 8 | 5 | 15 |
| Full | 15-30 | 20 | 5 | 20 |
| Veteran | 30+ | 35 | 10 | 30 |

**Account age factor:** Multiply limits by `min(1.0, account_age_days / 90)` for accounts younger than 90 days.

## Monitoring and Alerts

### Metrics to Track
- Comments sent per account per day
- FloodWait errors per account (count + total wait time)
- Success rate (comments sent / attempts)
- Average delay between comments
- API calls per account per day

### Alert Thresholds
- FloodWait > 300s → pause account for 1 hour
- 3+ FloodWait in 1 hour → pause for 4 hours
- Daily limit reached before 14:00 → account may be over-used
- Success rate < 80% → investigate
