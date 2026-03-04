# Telegram Anti-Fraud Detection

## How Telegram Detects Automation

### Signal Categories

**1. API-Level Signals**
- API ID reputation: flagged IDs (e.g., ID 4 — old Android) get extra scrutiny
- Request patterns: uniform timing between API calls = bot signature
- Error rate: too many failed requests indicate scraping/automation
- Unusual API combinations: e.g., GetDialogs + GetMessages in rapid sequence without user interaction

**2. Behavioral Signals**
- Message timing: exact intervals (every 60s) vs human-like variance
- Activity hours: 24/7 activity = bot; humans have sleep windows
- Content patterns: identical/similar messages across groups
- Interaction depth: bots typically only post, never read or react
- Join-then-post pattern: joining a group and immediately commenting is a red flag
- Engagement asymmetry: only posting without reading, reacting, or scrolling

**3. Network Signals**
- IP reputation: datacenter IPs flagged more than residential/mobile
- Geo-jumps: account appearing from Moscow then NYC in 5 minutes
- Multiple accounts same IP: strong automation indicator
- VPN/proxy fingerprints: known proxy provider IP ranges

**4. Account Signals**
- Account age: <3 months = highest risk tier
- Profile completeness: no avatar, no bio = suspicious
- Phone number type: VoIP numbers flagged more than real SIMs
- Registration pattern: bulk registrations from same IP range

### SpamBan Mechanics

**How SpamBot Works:**
- Telegram's @SpamBot is the official spam reporting interface
- Users report spam → Telegram ML model evaluates
- Automated bans based on pattern matching + user reports
- SpamBot `/start` to check own account status
- Restrictions: temporary (1-7 days) or permanent

**Trigger Thresholds (Approximate):**
- 5+ reports from unique users in 24h → review triggered
- 10+ reports → likely automatic restriction
- Identical message sent to 20+ groups → automatic flag
- Joining 50+ groups in one day → temporary restriction

**Types of Restrictions:**
1. **Spam flag** — messages marked as spam, reduced reach
2. **Temporary restriction** — cannot send messages to groups/channels for N days
3. **Permanent restriction** — account flagged as spam permanently
4. **Account termination** — session key revoked (`AuthKeyUnregisteredError`)

### Risk Factor Matrix

| Factor | Low Risk | Medium Risk | High Risk |
|--------|----------|-------------|-----------|
| API ID | Desktop (2040) | AndroidX (21724) | Old Android (4) |
| Account age | >1 year | 3-12 months | <3 months |
| Proxy type | Mobile/Residential | ISP | Datacenter |
| Activity pattern | Gaussian delays | Fixed delays | No delays |
| Content | Unique per post | Templated with variation | Copy-paste |
| Profile | Complete (avatar, bio, username) | Partial | Empty |
| Warm-up | 30+ days | 7-14 days | None |

## Anti-Detection Strategies

### DO:
- Use safe API IDs (Desktop=2040 or AndroidX=21724)
- Maintain consistent device fingerprint per account (never randomize per restart)
- Add Gaussian-distributed delays between all operations
- Implement per-account sleep windows (e.g., 23:00-07:00)
- Warm up accounts for 30+ days before automation
- Mix automated actions with passive actions (read, react, scroll)
- Use residential/mobile proxies with geo-consistency
- Keep 1 IP = 1 account ratio strictly
- Monitor session health proactively (check every 4h)
- Build complete profiles (avatar, bio, username)

### DON'T:
- Use API ID 4 (old Android, actively monitored)
- Send identical messages across groups
- Post immediately after joining a group
- Run accounts 24/7 without sleep periods
- Use datacenter proxies
- Share IPs between accounts
- Ignore FloodWait errors (respect + add 10-30% buffer)
- Skip warm-up for new accounts
- Use burst patterns (many messages in quick succession)
- Mix API ID families with wrong device fingerprints

## Telegram's ML Detection Model

Telegram uses a multi-layered ML system:

1. **Real-time layer**: Pattern matching on message content, frequency, and targets
2. **Behavioral layer**: Analyzes activity patterns over hours/days
3. **Network layer**: Correlates accounts by IP, device fingerprint, API ID
4. **Report layer**: User reports weighted by reporter reputation
5. **Account layer**: New accounts get higher scrutiny multiplier

The key insight: Telegram doesn't just look at individual signals — it correlates them. An account with API ID 4 + datacenter proxy + no warm-up + copy-paste messages will trigger ALL layers simultaneously.
