---
name: proxy-management
description: "Proxy lifecycle management for Telegram neurocommenting — purchasing, setup, health checking, auto-cleanup, 1:1 account binding, CAPTCHA bridge proxies, and low-proxy alerts. Use this skill whenever working with proxies: importing, testing, binding to accounts, checking health, troubleshooting dead proxies, buying new proxies, or configuring SOCKS5/HTTP CONNECT for Telegram. Also triggers when user mentions proxy, прокси, Webshare, IPRoyal, Proxyverse, CAPTCHA bridge, or proxy health."
---

# Proxy Management — Full Lifecycle for Telegram Neurocommenting

This skill contains everything about proxies for keeping Telegram accounts alive and working. Every rule here was learned from real bans and dead proxies.

## The Golden Rules

1. **1 IP = 1 account** — NEVER share a proxy between accounts. Breaking this rule killed 10 accounts in 5 seconds.
2. **Proxy geo must match account region** — +7/+77 numbers need CIS proxies (KZ, RU, UZ), not Brazil or Africa.
3. **Never change a proxy** on a bound account — Telegram flags IP changes as suspicious.
4. **Static residential (ISP) proxies only** for account binding — datacenter proxies get detected by ASN.
5. **SOCKS5 preferred over HTTP** — more universal, works for both Telethon and CAPTCHA.

## Proxy Types

| Type | Price/IP/mo | Telegram Trust | Use Case |
|------|------------|----------------|----------|
| Datacenter | $0.50-2 | Low | **NEVER** for accounts. OK for web scraping only |
| Rotating residential | $0.65-4/GB | Medium | OK for channel parsing, NOT for accounts |
| **Static residential (ISP)** | $2-6.50 | **High** | **OPTIMAL** for account binding |
| Mobile | $10-30 | Maximum | For high-value accounts only |

## Recommended Providers

### Tier 1 (Best price/quality for scale)

**Webshare** (webshare.io)
- Static residential: $0.30/IP (minimum 20 IP = $6/mo)
- 100 proxies: ~$20/mo, 500: ~$95/mo, 1000: ~$180/mo
- Good API for programmatic management
- Up to 10,000 concurrent connections
- Best for 100+ accounts scale

**IPRoyal** (iproyal.com)
- Static residential: from $2.00/IP/mo
- 20 IP for $6/mo with 250GB traffic
- Good geo coverage including RU/CIS
- Supports SOCKS5 and HTTPS

### Tier 2 (Premium)

**Smartproxy/Decodo** — $4.70/IP, city/ASN targeting
**Bright Data** — largest IP pool, most expensive, highest reliability

### DO NOT USE

- **922Proxy** — shut down by Google in Jan 2026, IPs blacklisted
- **Proxyverse** (current) — ~80% dead proxies, unreliable for HTTPS CONNECT

## Proxy File Format

Standard format in `data/proxies.txt`:
```
host:port:user:password
```

Telethon tuple format:
```python
proxy = (3, host, int(port), True, user, password)
# Type 3 = HTTP with CONNECT support
```

SOCKS5 format (preferred):
```python
import socks
proxy = (socks.SOCKS5, host, int(port), True, user, password)
```

## Proxy Health Checking

### Quick HTTP Check
```python
import subprocess
def test_proxy_http(host, port, user, password, timeout=8):
    cmd = f'curl -x "http://{user}:{password}@{host}:{port}" -s -o /dev/null -w "%{{http_code}}" --connect-timeout {timeout} https://api.ipify.org'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip() == "200"
```

### HTTPS CONNECT Check (for CAPTCHA bridge)
```python
import requests
def test_proxy_https(host, port, user, password, timeout=10):
    proxy_url = f"http://{user}:{password}@{host}:{port}"
    try:
        r = requests.get("https://telegram.org/",
                        proxies={"https": proxy_url},
                        timeout=timeout, allow_redirects=False)
        return r.status_code in (200, 301, 302)
    except Exception:
        return False
```

### Telethon Connection Check
```python
async def test_proxy_telethon(phone, data, proxy, timeout=30):
    client = build_client(phone, data, proxy)
    try:
        await asyncio.wait_for(client.connect(), timeout=timeout)
        authorized = await client.is_user_authorized()
        return authorized
    except Exception:
        return False
    finally:
        await client.disconnect()
```

### DUAL Proxy Check (for appeal/CAPTCHA)
A DUAL proxy works for BOTH Telethon AND HTTPS CONNECT:
```python
def find_dual_proxy(phone, max_search=60):
    """Find proxy that works for both Telethon MTProto and Chrome CAPTCHA."""
    for proxy_line in proxy_pool:
        host, port, user, password = parse(proxy_line)
        if not test_proxy_http(host, port, user, password):
            continue
        if test_proxy_https(host, port, user, password):
            return (3, host, int(port), True, user, password)
    return None
```

## Health Check Schedule

| Check Type | Frequency | Action on Failure |
|-----------|-----------|-------------------|
| HTTP alive | Every 30 min | Mark dead, notify if bound |
| HTTPS CONNECT | On demand (appeal) | Find alternative DUAL proxy |
| Telethon connect | Every 6 hours | Re-test, mark dead if 3 failures |
| IP reputation | Weekly | Check against blacklists |

## Binding Rules

### Auto-Assignment
When importing accounts without proxies:
1. Find first unbound alive proxy matching account's geo region
2. Bind and record in `account_proxies` table
3. Test Telethon connection through this proxy
4. If fails — unbind, try next proxy
5. If no proxies available — alert admin

### Rebinding (emergency only)
When a bound proxy dies permanently:
1. Disconnect account from Telegram cleanly
2. Wait 30 minutes
3. Bind new proxy from same geo region
4. Reconnect and verify
5. Note: this is suspicious to Telegram — avoid if possible

## CAPTCHA Bridge

For SpamBot CAPTCHA, Chrome must use the SAME proxy as the Telethon client:

1. Find DUAL proxy (HTTP + HTTPS CONNECT)
2. Create Chrome MV2 extension for proxy auth:
```javascript
// background.js — Manifest V2 only (MV3 doesn't support asyncBlocking)
chrome.webRequest.onAuthRequired.addListener(
  function(details, callback) {
    callback({
      authCredentials: { username: "USER", password: "PASS" }
    });
  },
  {urls: ["<all_urls>"]},
  ["asyncBlocking"]
);
```
3. Launch Chrome: `--proxy-server=http://host:port --load-extension=/tmp/proxy_ext_PHONE`
4. User solves Cloudflare Turnstile
5. Click Done in SpamBot via Telethon (same proxy)

**Critical**: Turnstile is domain-locked to telegram.org — will NOT work on localhost tunnel.

## Low Proxy Alerts

When alive proxies drop below thresholds:
| Threshold | Alert Level | Action |
|-----------|------------|--------|
| < 20% alive | WARNING | Send to Нейросводка |
| < 10% alive | CRITICAL | Auto-purchase if enabled |
| 0 free proxies | EMERGENCY | Stop new account onboarding |

Alert message format:
```
⚠️ ПРОКСИ ALERT
━━━━━━━━━━━━━━━━
Живых: 15/100 (15%)
Мёртвых: 85
Привязанных: 12
Свободных: 3

🔴 Рекомендация: закупить 80+ прокси
```

## Purchasing Guide

### Where to Buy

**Static Residential Proxies:**
- Webshare: webshare.io — best for 100+ IP, API available
- IPRoyal: iproyal.com — good RU/CIS geo
- Smartproxy: smartproxy.com — city-level targeting

**Proxy Geo for Account Regions:**
| Account Number | Proxy Geo | Reason |
|---------------|-----------|--------|
| +7 (Russia) | RU, KZ | Matches region |
| +77 (Kazakhstan) | KZ, RU | Matches region |
| +998 (Uzbekistan) | UZ, KZ, RU | CIS region |
| +996 (Kyrgyzstan) | KG, KZ, RU | CIS region |
| +1 (USA) | US | Must match |

### Budget Calculator
```
Accounts needed: N
Proxies needed: N (1:1)
Monthly proxy cost: N × $2-3 (Webshare/IPRoyal)
Monthly replacement: N × 15% × $2-3

Example for 100 accounts:
100 × $2.50 = $250/mo proxies
15 replacements × $2.50 = $37.50/mo
Total: ~$287/mo
```

## Project Integration

| Component | File | Purpose |
|-----------|------|---------|
| Proxy import/test | `utils/standalone_helpers.py` | `_test_proxy()`, `load_proxies()` |
| Proxy file | `data/proxies.txt` | Main proxy list |
| Appeal w/ proxy | `scripts/appeal_unified.py` | DUAL proxy for CAPTCHA |
| Account monitor | `scripts/account_monitor.py` | Uses bound proxy per account |
| Farm thread | `core/farm_thread.py` | Uses bound proxy for commenting |

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| 504 Gateway Timeout | Proxy dead or overloaded | Test proxy, replace if dead |
| ERR_TUNNEL_CONNECTION_FAILED | No HTTPS CONNECT support | Use DUAL proxy or switch provider |
| Connection timed out: 30 | Proxy slow or Telegram blocked it | Try different proxy |
| Chrome can't load page | Proxy auth failed | Check MV2 extension credentials |
| CAPTCHA rejected | IP mismatch Telethon vs Chrome | Use appeal_unified.py with DUAL proxy |
| All proxies dead | Provider outage | Switch to backup provider |
