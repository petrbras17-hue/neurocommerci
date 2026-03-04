# Account Lifecycle

## Account Acquisition

### Account Sources

1. **Self-registered** — registered with own SIM card
   - Highest trust, best longevity
   - Most expensive (SIM cost + time)
   - Recommended for critical/long-term accounts

2. **Purchased from supplier** — bulk accounts from panels
   - Cheaper, faster to acquire
   - Risk: unknown registration quality, may have prior flags
   - Common issues: API ID 4, VoIP numbers, datacenter registration IPs
   - Always request: session file + JSON metadata + 2FA password

3. **Farmed** — registered via automation, then aged
   - Medium cost, decent quality if done right
   - Requires: clean IPs, real SIMs, proper API IDs
   - Aging period: 30+ days of manual-like activity

### Account Quality Checklist

When receiving/purchasing accounts, verify:
- [ ] API ID is NOT 4 (use 2040 or 21724)
- [ ] Phone number is real SIM (not VoIP)
- [ ] Registration IP was residential/mobile (not datacenter)
- [ ] 2FA password is provided
- [ ] Session file is valid (test connect + get_me)
- [ ] Account is not already flagged (check via @SpamBot)
- [ ] Device fingerprint is realistic for the API ID

## Account States

```
New → Warm-up → Active → (Restricted) → (Dead/Banned)
                  ↓                         ↓
               Cooling Down           Appeal Process
                  ↓                         ↓
               Active                 Recovery / Replace
```

### State Definitions

| State | Description | Automated Actions |
|-------|-------------|-------------------|
| **New** | Just imported, not yet warm-up | None (readonly) |
| **Warmup** | In progressive warm-up schedule | Passive actions only (read, react) |
| **Active** | Fully operational | Comments, reactions, channel management |
| **Restricted** | SpamBot flagged, limited messaging | Pause all automated actions, appeal |
| **Cooling** | After FloodWait or restriction | Minimal activity, wait for cooldown |
| **Dead** | Session terminated (AuthKeyUnregisteredError) | Remove from pool, notify admin |
| **Banned** | Phone number banned | Appeal via email, replace if permanent |

## Warm-Up Protocol

### Phase 1: Readonly (Days 0-2)
- Join 2 channels per day
- Read messages (scroll through feeds)
- No posting, no reactions
- Purpose: establish baseline activity pattern

### Phase 2: Reactions (Days 3-4)
- Join 3 channels per day
- React to 5 posts per day (thumbs up, heart, etc.)
- Read messages actively
- No comments yet

### Phase 3: Light Commenting (Days 5-7)
- Max 3 comments per day
- Comments must be high quality (AI-generated, natural)
- Continue joining channels (3/day)
- Continue reactions (10/day)
- Wait 24-72h after joining before first comment

### Phase 4: Moderate (Days 8-14)
- Max 8 comments per day
- Diversify comment topics and styles
- Join 5 channels per day
- Reactions: 15/day

### Phase 5: Full (Days 15-30)
- Max 20 comments per day
- Full automation enabled
- Channel management operations allowed
- Join 5 channels per day
- Reactions: 20/day

### Phase 6: Veteran (Day 30+)
- Max 35 comments per day
- All operations enabled
- Lower risk profile due to account age
- Reactions: 30/day

### Account Age Factor

For accounts younger than 90 days, apply a multiplier:
```
effective_limit = base_limit * min(1.0, account_age_days / 90)
```

Example: A 30-day-old account in "Full" phase:
- Base limit: 20 comments/day
- Age factor: 30/90 = 0.33
- Effective limit: 20 * 0.33 = 6 comments/day

## Profile Building

### Required Profile Elements

1. **Avatar** — realistic photo (NOT stock photos, NOT AI-generated faces that are too perfect)
2. **First name** — natural name matching language/region
3. **Last name** — optional but adds trust
4. **Username** — natural-looking (not bot-like patterns)
5. **Bio** — short, natural (optional but recommended)

### Profile Building Timeline

- Day 0: Set avatar + name (during import)
- Day 3: Add username
- Day 7: Add bio
- Day 14: Consider personal channel with redirect post

## Ban Recovery

### Types of Bans

1. **Spam restriction** (temporary, 1-7 days)
   - Action: Pause all automation, wait out the timer
   - After: Reduce daily limits by 50% for 2 weeks

2. **SpamBot flag** (persistent)
   - Check: `/start` with @SpamBot
   - Appeal: Message @SpamBot with appeal text
   - Wait: 24-48h for automated review
   - After: If lifted, warm-up again from Phase 3

3. **Account restriction** (permanent-ish)
   - Appeal: Email `recover@telegram.org` with:
     - Phone number
     - Explanation (human-written, NOT template)
     - Promise to follow rules
   - Wait: 3-7 days
   - Success rate: ~30-50% for first offense

4. **Phone ban** (permanent)
   - The phone number itself is blacklisted
   - Cannot register new account with this number
   - No recovery — need new phone number

### Appeal Best Practices

- Write in the same language as account registration region
- Be polite, acknowledge potential violation
- Mention legitimate use case
- Don't use template/copy-paste appeals
- Don't appeal more than once per week
- Don't use automation for appeal process

## Account Rotation Strategy

### Active Pool Management

For a pool of 50 accounts:
- Active at any time: 30-35 (70%)
- Warming up: 5-10 (10-20%)
- Cooling down: 3-5 (6-10%)
- Reserve (backup): 5-10 (10-20%)

### Rotation Triggers

- FloodWait > 5 minutes → rotate to next account
- 3 FloodWaits in 1 hour → put account in cooling for 4 hours
- Daily limit reached → rotate
- SpamBot restriction → immediately pause, move to cooling
- No rotation mid-conversation (if account started a thread, finish it)

### Retirement Criteria

- Account consistently hitting FloodWait daily → reduce usage or retire
- Account restricted 3+ times → retire
- Account age > 2 years with no issues → promote to "veteran" with higher limits
