# Warmup Schedule — Detailed Protocol

## Overview

New accounts must be warmed up gradually to establish a legitimate activity pattern before automation begins. Skipping or rushing warm-up is the #1 cause of early account bans.

## Multi-Week Schedule

### Week 1: Establishing Presence

**Days 0-2: Readonly**
| Activity | Count/Day | Timing |
|----------|-----------|--------|
| Read channel messages | 10-20 | Spread over 8h active window |
| Scroll through feeds | 3-5 sessions | 2-5 min each |
| Join channels | 2 | Morning + evening |
| Set profile info | 1 (avatar) | Day 0 |
| Comments | 0 | — |
| Reactions | 0 | — |

**Days 3-4: First Interactions**
| Activity | Count/Day | Timing |
|----------|-----------|--------|
| Read channel messages | 15-25 | Spread over 10h |
| React to posts | 3-5 | Space 2-4h apart |
| Join channels | 3 | Morning, midday, evening |
| Set username | 1 | Day 3 |
| Comments | 0 | — |

**Days 5-7: Light Commenting**
| Activity | Count/Day | Timing |
|----------|-----------|--------|
| Read channel messages | 20-30 | Throughout day |
| React to posts | 8-10 | Space 1-2h apart |
| Post comments | 2-3 | Only in groups joined 24h+ ago |
| Join channels | 3 | Spread out |
| Mark messages as read | 50+ | Natural browsing |

### Week 2: Building Activity

**Days 8-14: Moderate Activity**
| Activity | Count/Day | Timing |
|----------|-----------|--------|
| Read channel messages | 30+ | Throughout day |
| React to posts | 12-15 | Space 1h apart |
| Post comments | 5-8 | Space 45min-2h apart |
| Join channels | 4-5 | Spread out |
| Add bio | 1 | Day 10 |

### Weeks 3-4: Approaching Full

**Days 15-21: Ramping Up**
| Activity | Count/Day | Timing |
|----------|-----------|--------|
| Post comments | 10-15 | Space 30min-1.5h apart |
| React to posts | 15-20 | Natural pace |
| Join channels | 5 | |
| Channel browsing | Active | Mix of subscribed + discover |

**Days 22-30: Near Full**
| Activity | Count/Day | Timing |
|----------|-----------|--------|
| Post comments | 15-25 | Space 20min-1h apart |
| React to posts | 20-25 | |
| Channel management | As needed | Create redirect channel |

### Month 2+: Full Operation

| Activity | Count/Day | Timing |
|----------|-----------|--------|
| Post comments | 25-35 | Per rate limiter settings |
| React to posts | 25-30 | |
| Channel management | Full | |
| All operations | Enabled | Per rate limiter |

## Activity Rules During Warm-Up

### Content Quality
- During warm-up, comments must be EXTRA natural
- No product mentions (Scenario A only) during first 14 days
- Scenario B (with hidden link) only after day 15
- Comment length: 20-150 chars (short, casual)
- Match channel topic (don't post tech comments in cooking group)

### Timing Rules
- Never post within 60 seconds of joining a channel
- Wait at least 24 hours after joining before first comment
- Never post more than 2 comments in same channel per day during warm-up
- Add 2x longer delays during warm-up vs. full operation

### Channel Selection During Warm-Up
- Join popular, mainstream channels first (news, entertainment)
- Avoid: crypto, gambling, adult content channels
- Mix: 60% topic-relevant, 40% general interest
- Never join channels that were just created or have few members

## Warm-Up Monitoring

### Health Indicators (Good)
- No FloodWait errors
- All messages delivered
- @SpamBot shows "no restrictions"
- Normal `get_me()` responses

### Warning Signs
- FloodWait during warm-up → extend current phase by 3 days
- SpamBot restriction → immediately pause, restart warm-up from Phase 2
- `PEER_FLOOD` error → account is already flagged, reduce all limits by 50%
- Cannot send to some groups → partial restriction, check @SpamBot

## Account Age Impact on Warm-Up

| Account Age | Warm-Up Duration | Max Initial Comments |
|-------------|-----------------|---------------------|
| Fresh (0-7 days) | 30 days minimum | Start at 0, end at 10 |
| Young (1-4 weeks) | 21 days | Start at 0, end at 15 |
| Medium (1-3 months) | 14 days | Start at 2, end at 20 |
| Aged (3-6 months) | 7 days | Start at 5, end at 25 |
| Old (6+ months) | 3 days | Start at 10, end at 35 |

**Note:** These are guidelines. If an account was previously restricted or has other risk factors, use the longer warm-up regardless of age.

## Implementation in Code

Current implementation in `utils/anti_ban.py`:
```python
# get_warmup_phase(days_active) returns WarmupPhase
# Rate limiter uses: get_daily_limit(days_active) -> int
```

Enhanced implementation should:
1. Accept both `days_active` and `account_age_days`
2. Return full `WarmupConfig` with all activity types
3. Apply age factor: `min(1.0, account_age_days / 90)`
4. Track warm-up progress in DB per account
