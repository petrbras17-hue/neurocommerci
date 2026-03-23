# Telegram Account Warmup Best Practices

Last updated: 2026-03-23

Sources: IPFoxy, nReach, Telegram Growth Studio, BlackHatWorld, MoreLogin, Telethon docs, GramGPT

---

## Why Warmup Is Critical

New Telegram accounts start with **zero trust**. Launching business/commenting activity immediately triggers Telegram's risk control system, leading to:
- **Low survival rate**: Accounts banned within hours/days
- **Low message delivery**: Messages folded, hidden, or no notification to recipients
- **Low conversion**: Empty profiles with no history appear suspicious
- Telegram has become **significantly more aggressive** with anti-spam since 2023

---

## 7-Day Standard Operating Procedure (SOP)

### Day 1: Registration + Profile Setup
- Complete profile: realistic avatar, name, username, bio
- Enable 2FA (two-factor authentication)
- Join 1-2 public channels or interest groups
- Browse content passively
- **Do NOT**: Post anything, send messages, add contacts

### Day 2: Increase Browsing
- Continue browsing channels and groups
- Read messages, click links, follow 1-2 new channels
- Occasionally share a simple opinion in a group
- **Do NOT**: Post promotional content or links

### Day 3: Light Interaction
- Reply to 2-3 messages in groups
- Start 1-2 conversations with contacts/friends
- Keep conversations natural, ask questions
- **Do NOT**: Send marketing content, bulk messages

### Days 4-5: Gradual Activity Increase
- Browse more channels, participate in discussions
- Maintain ongoing conversations
- Join 2-3 groups per day maximum
- React to posts (emoji reactions)
- **Do NOT**: Join more than 3 groups/day, send identical messages

### Day 6: Function Testing
- Add more contacts
- Send 5-10 normal messages
- Participate in group chats
- Test if any restrictions appear
- Check account with @SpamBot

### Day 7: Soft Operational Start
- Begin light promotional activity
- Post content in own channel (if applicable)
- Add new contacts gradually
- Start with 5-10 comments maximum
- **Keep frequency moderate** -- no sudden large-scale activity

### Days 8-14: Gradual Ramp-Up
- Increase commenting volume by 10-20% per day
- Start: 10 comments/day -> End: 50-100 comments/day
- Continue regular non-commercial activity alongside
- Monitor for any restrictions

---

## Advanced Warmup Techniques

### Network Environment

| Practice | Priority | Details |
|----------|----------|---------|
| **Unique IP per account** | CRITICAL | Multiple accounts on same IP get cross-flagged |
| **Residential proxy** | HIGH | Closer to typical household network, natural geographic attribution |
| **Stable IP** | HIGH | Frequent IP changes trigger risk detection |
| **Avoid shared proxies** | HIGH | Multiple users on same IP = high risk |
| **Match timezone** | MEDIUM | Account phone number timezone should match proxy location |
| **Mobile proxy** | BEST | Most natural, hardest to detect, but expensive |

### Phone Number Quality

| Number Type | Risk Level | Notes |
|-------------|------------|-------|
| Real SIM (personal) | LOW | Best option, highest trust |
| Real SIM (new prepaid) | LOW-MEDIUM | Good, but needs warmup |
| Long-term virtual | MEDIUM | If used for months, acceptable |
| SMS verification services | HIGH | Often flagged, short lifespan |
| VoIP numbers | HIGH | Telegram actively blocks these |
| Previously-recycled numbers | VERY HIGH | May inherit spam history |

### Device Identity

- Use realistic device_model that matches API ID family
- **API ID 2040** (Telegram Desktop) or **21724** (Android X) are safer
- **API ID 4** is FLAGGED (default Telethon) -- DO NOT USE
- Unique device fingerprint per account
- Never reuse device identifiers across accounts

---

## Activity Simulation During Warmup

### Daily Activity Schedule (Simulated)

| Time | Activity | Duration |
|------|----------|----------|
| 09:00-10:00 | Browse channels, read messages | 15-30 min |
| 12:00-13:00 | React to posts, reply in groups | 10-20 min |
| 15:00-16:00 | Light chatting, join 1 channel | 10-15 min |
| 19:00-21:00 | Active engagement, conversations | 20-30 min |
| 22:00+ | Minimal activity, occasional reads | 5 min |

### Activity Types (In Order of Safety)

1. **Reading messages** (safest -- looks like passive user)
2. **Emoji reactions** (low risk, shows engagement)
3. **Reading profile pages** (browsing behavior)
4. **Joining channels** (1-2/day max)
5. **Replying in groups** (moderate risk, shows genuine interest)
6. **Starting conversations** (higher risk for new accounts)
7. **Commenting on posts** (highest risk, save for after warmup)

### Gaussian Timing Model

Instead of fixed delays, use Gaussian (normal) distribution:
- Mean delay: 30 seconds
- Standard deviation: 10 seconds
- This produces human-like timing variation
- Example: delays of 18s, 33s, 27s, 41s, 29s (not 30s, 30s, 30s)

---

## What Triggers Bans

### Immediate Ban Triggers (DO NOT DO)
- Sending identical messages to many users
- Mass joining groups (>5-10 per day)
- Posting links immediately after registration
- Bulk contact syncing on first login
- Using known-blacklisted phone numbers
- Running multiple accounts on same IP without proxies

### Gradual Risk Accumulation
- Frequent profile edits (>2/week)
- Rapid channel join/leave cycles
- High volume of unreciprocated messages
- Messages that get reported by recipients
- Commenting on too many channels too quickly
- Inconsistent activity patterns (nothing for days, then burst)

### Technical Red Flags
- Using API ID 4 (default Telethon -- immediately flagged)
- Frequently changing IP addresses
- Using datacenter IPs (not residential)
- Multiple accounts sharing same device_model + api_id
- Connecting/disconnecting rapidly
- AuthKeyUnregisteredError = session invalidated, account potentially dead

---

## Account Health Monitoring

### Automated Checks
- `get_me()` every 4 hours to verify session is alive
- Check @SpamBot for restriction status
- Monitor for PeerFloodError (= rate limited)
- Track successful vs failed message sends
- Alert on AuthKeyUnregisteredError

### Health Status Classification

| Status | Meaning | Action |
|--------|---------|--------|
| HEALTHY | All checks pass | Continue normal operation |
| LIMITED | PeerFloodError received | Reduce activity 50%, wait 24h |
| WARNED | @SpamBot shows restriction | Stop commenting, maintain passive use, wait 7 days |
| BANNED | Account inaccessible | Remove from pool, don't reconnect |
| DEAD | AuthKeyUnregistered | Session invalidated, account lost |

---

## GramGPT's Account Warming Module

GramGPT ($30/month) offers automated warming with:
- AI rewrite for natural message variation
- Trust percentage visualization (65% -> 78% -> 94%)
- Automatic dialog creation
- Gradual activity increase
- Integration with their commenting pipeline

This shows there's **paid demand** ($30/month) for automated warmup alone.

---

## Recommended Warmup Configuration for Our System

### Phase 1: Initial (Days 1-3)
```
actions_per_day: 5-10
action_types: [read_messages, react_to_posts]
channels_to_join: 2
delay_between_actions: gaussian(mean=120s, std=30s)
active_hours: 10:00-22:00
```

### Phase 2: Building (Days 4-7)
```
actions_per_day: 15-25
action_types: [read_messages, react_to_posts, reply_in_groups]
channels_to_join: 3
delay_between_actions: gaussian(mean=60s, std=20s)
active_hours: 09:00-23:00
```

### Phase 3: Activation (Days 8-14)
```
actions_per_day: 30-50
action_types: [read_messages, react_to_posts, reply_in_groups, comment_on_posts]
comments_per_day: 10 (increasing by 10 daily)
delay_between_actions: gaussian(mean=45s, std=15s)
active_hours: 08:00-23:00
```

### Phase 4: Full Operation (Day 15+)
```
actions_per_day: 100-200
action_types: [all]
comments_per_day: 100-200
delay_between_comments: gaussian(mean=30s, std=10s)
active_hours: 07:00-00:00
max_channels: 160 (safe) / 500 (aggressive)
```

---

## Key Takeaways

1. **Patience is essential**: 7-14 days minimum before commenting
2. **Residential proxies are non-negotiable** for account survival
3. **API ID 4 (default Telethon) is a death sentence** -- always use Desktop (2040) or AndroidX (21724)
4. **Gaussian timing** is more human-like than fixed delays
5. **Activity simulation** during warmup significantly improves trust
6. **Health monitoring** with `get_me()` catches dead sessions early
7. **Gradual ramp-up** (10% daily increase) is safer than sudden volume
8. **Night reduction** matches real human behavior patterns
9. **GramGPT charges $30/mo just for warming** -- shows market value
10. **The warmup phase IS the product** -- accounts that survive warmup are the real asset
