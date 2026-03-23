# GramGPT Anti-Ban Technical Details (Blog Posts)

**URLs:**
- https://gramgpt.io/en/blog/telegram-proxy-automation (Proxy Strategy)
- https://gramgpt.io/en/blog/what-is-flood-wait-in-telegram-and-how-to-fix-it (Flood Wait & Quarantine)
**Type:** Technical Blog Posts
**Scraped:** 2026-03-23

## Proxy Strategy for Telegram Automation

### Three Proxy Types Compared:

| Type | Trust Level | Cost | Best For |
|------|-------------|------|----------|
| Datacenter (SOCKS5 IPv4) | Low | $1-3/IP | Scale, testing, moderate load |
| Residential | Medium | Higher | Account warming, long-term stability |
| Mobile (4G/5G) | High | Most expensive | New accounts, high-risk actions |

### Key Rules:
- SOCKS5 is the most stable format for Telegram
- Never reuse same IP across dozens of accounts simultaneously
- Proxy pool with automatic distribution is essential for scale
- Supported formats: IP:PORT, IP:PORT:LOGIN:PASSWORD, socks5://user:pass@ip:port
- 80% of Telegram automation relies on SOCKS5 proxies

### GramGPT Proxy Features:
- Proxy pool for automatic distribution (sequential or random)
- Bulk proxy import and validation
- Per-account proxy assignment
- Both SOCKS5 and HTTP supported

## Flood Wait & Quarantine System

### What Triggers Flood Wait:
1. Joining channels too frequently
2. Repetitive mass actions
3. Activity speed too high
4. Repeated actions without pauses

### Flood Wait Duration Escalation:
- First: 60 seconds
- Second: few minutes
- Third: few hours
- Eventually: 24 hours+

### GramGPT Quarantine Approach:
- **ANY Flood Wait (even 60s) = 24h quarantine** -- aggressive safety
- Account completely stops all active operations
- Protected from longer restriction periods
- Automatic -- no manual intervention needed

### Account Scoring System:
- **Account Health** -- overall health percentage
- **Survivability Score** -- long-term survival likelihood
- **Risk Factors** -- identified risk elements

### Anti-Ban Best Practices (from GramGPT):
1. Avoid repetitive mass actions consecutively
2. Limit channel joins; increase delays between joins
3. Use account warming strategies
4. Work with high-quality proxies
5. Allow accounts to rest periodically
6. Use AI Protection across all modules

## Key Technical Takeaways for Our Project

1. **Quarantine on ANY flood = 24h rest** -- we should implement this exact strategy
2. **Account health scoring** -- already partially have this; need to formalize
3. **Proxy diversity** -- our system should support all three types
4. **Flood Wait escalation** -- our watchdog should track escalation patterns
5. **"Don't push through"** -- our retry logic must respect cooldown periods
6. **Proxy pool** -- automatic proxy rotation and distribution is essential
7. **SOCKS5 as default** -- ensure our proxy implementation prioritizes SOCKS5

## Implementation Ideas

```
# Account Health Score Components (inspired by GramGPT):
- Days since last flood: 0-100 points
- Successful comments ratio: 0-100 points
- Account age: 0-100 points
- Session health: healthy/degraded/dead
- Current quarantine status: active/inactive
- Risk level: low/medium/high
```
