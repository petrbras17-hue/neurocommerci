---
name: check-status
description: Check NEURO COMMENTING system status — accounts, proxy, sessions, database, config
allowed-tools: Bash, Read, Grep, Glob
---

# System Status Check

Run a comprehensive status check of the NEURO COMMENTING system.

## Steps

1. **Check sessions**:
```bash
ls -la data/sessions/*.session 2>/dev/null | wc -l
```

2. **Check proxy file**:
```bash
wc -l data/proxies.txt 2>/dev/null
```

3. **Check database**:
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('data/neuro_commenting.db')
c = conn.cursor()
c.execute('SELECT status, COUNT(*) FROM accounts GROUP BY status')
print('Accounts:', dict(c.fetchall()))
c.execute('SELECT COUNT(*) FROM channels WHERE is_active=1')
print('Active channels:', c.fetchone()[0])
c.execute('SELECT COUNT(*) FROM comments WHERE date(created_at)=date(\"now\")')
print('Comments today:', c.fetchone()[0])
conn.close()
"
```

4. **Check config** — verify critical settings:
```bash
python3 -c "
from config import settings
warnings = settings.validate_critical()
if warnings:
    for w in warnings: print(f'WARNING: {w}')
else:
    print('Config OK')
print(f'Gemini: {settings.GEMINI_MODEL}')
print(f'Claude: {settings.CLAUDE_MODEL}')
print(f'API ID: {settings.TELEGRAM_API_ID}')
print(f'Anthropic key: {\"SET\" if settings.ANTHROPIC_API_KEY else \"NOT SET\"}')
"
```

5. **Check frozen accounts**:
```bash
python3 check_frozen.py 2>/dev/null || echo "check_frozen.py not available"
```

Present findings as a clear status report.
