# Knowledge Base — NEURO COMMENTING

Categorized reference files for Telegram userbot automation.
Load these files as context for AI assistants working on the project.

## Files

| File | Topic |
|------|-------|
| [telegram-antifraud.md](telegram-antifraud.md) | Telegram anti-fraud detection, spamban mechanics, risk factors |
| [session-management.md](session-management.md) | Session lifecycle, expiry, health checking, StringSession backup |
| [api-ids-and-fingerprints.md](api-ids-and-fingerprints.md) | API ID families, device fingerprint consistency, opentele |
| [rate-limits-and-timing.md](rate-limits-and-timing.md) | Rate limits, delay patterns, FloodWait handling |
| [proxy-and-geo.md](proxy-and-geo.md) | Proxy rules, geo-consistency, IP-account binding |
| [account-lifecycle.md](account-lifecycle.md) | Registration, warm-up phases, aging, ban recovery |
| [warmup-schedule.md](warmup-schedule.md) | Multi-week warmup protocol with activity types |

## Usage

When starting a new Claude/AI session on this project, load relevant knowledge files as context:
```
# For session issues:
knowledge/session-management.md + knowledge/api-ids-and-fingerprints.md

# For ban/spam issues:
knowledge/telegram-antifraud.md + knowledge/account-lifecycle.md

# For rate/timing issues:
knowledge/rate-limits-and-timing.md + knowledge/warmup-schedule.md

# For proxy issues:
knowledge/proxy-and-geo.md
```

## Sources

Research compiled from:
- Telegram official documentation (core.telegram.org)
- Telethon library documentation and source code
- opentele library (GitHub: AXE-Me)
- Community research (grammyjs, pyrogram forums)
- Production experience from NEURO COMMENTING project (50+ accounts)
