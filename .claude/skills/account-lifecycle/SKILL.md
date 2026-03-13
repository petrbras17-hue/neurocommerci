---
name: account-lifecycle
description: "Telegram account anti-fraud lifecycle management — safe onboarding, warmup, human-like behavior, health monitoring, auto-appeal of frozen accounts, and CAPTCHA handling. Use this skill whenever working with Telegram accounts via Telethon: connecting, warming up, commenting, checking status, appealing bans, handling FloodWait, configuring proxies, or any account safety question. Also triggers when user mentions SpamBot, frozen accounts, account bans, warmup, anti-detection, or proxy setup."
---

# Account Lifecycle — Anti-Fraud & Human-Like Behavior

This skill contains the complete knowledge base for keeping Telegram accounts alive and productive. Every rule here was learned from real bans and lost accounts.

## The Golden Rules (break any = instant ban)

1. **NEVER call `send_code_request()`** on purchased sessions — instant session revocation
2. **NEVER share IP between accounts** — 1 IP = 1 account, always. 10 accounts were banned in 5 seconds from one IP
3. **NEVER change profile within 48 hours** of first connection — triggers FrozenMethodInvalidError
4. **NEVER use empty `lang_pack`** — marks session as unofficial client
5. **NEVER send /start to @SpamBot** if an appeal is already pending — resets the appeal

## Account Lifecycle Phases

```
┌─────────┐    ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌──────────┐
│ UPLOAD  │───▶│  BIND   │───▶│  WARMUP  │───▶│  READY  │───▶│  FARM    │
│ session │    │  proxy  │    │  24-48h  │    │         │    │          │
└─────────┘    └─────────┘    └──────────┘    └─────────┘    └──────────┘
                                   │                              │
                                   ▼                              ▼
                              ┌──────────┐                  ┌──────────┐
                              │  FROZEN  │◀─────────────────│ COOLDOWN │
                              │          │                  │          │
                              └────┬─────┘                  └──────────┘
                                   │
                              ┌────▼─────┐    ┌──────────┐
                              │  APPEAL  │───▶│  FREE    │──▶ back to WARMUP
                              │          │    │          │
                              └────┬─────┘    └──────────┘
                                   │
                              ┌────▼─────┐
                              │  BANNED  │──▶ move to _banned/
                              └──────────┘
```

### Phase 1: Upload & Bind (Day 0)

Upload `.session` + `.json` pair. The JSON must contain device fingerprint:

```json
{
  "app_id": 12345,
  "app_hash": "abc123...",
  "device": "Samsung Galaxy S23",
  "sdk": "SDK 29",
  "app_version": "12.4.3",
  "lang_pack": "ru",
  "system_lang_pack": "ru-ru"
}
```

Bind a unique proxy (NEVER reuse):
```python
proxy = (3, "proxy.proxyverse.io", 9200, True, "country-ru-session-XXX", "password")
# Type 3 = HTTP with CONNECT
```

Test proxy before binding:
```bash
curl -x "http://user:pass@host:port" https://api.ipify.org
# Must return 200 with an IP address
```

### Phase 2: First Connection (Day 0)

```python
client = TelegramClient(
    session_path,
    api_id=data["app_id"],
    api_hash=data["app_hash"],
    proxy=proxy,
    device_model=data["device"],      # Match fingerprint exactly
    system_version=data["sdk"],
    app_version=data["app_version"],
    lang_code=data["lang_pack"],       # NEVER empty
    system_lang_code=data["system_lang_pack"],
    timeout=30,
    connection_retries=5,
    retry_delay=5,
)
```

First connection checklist:
- [ ] `await client.connect()`
- [ ] `await client.is_user_authorized()` — if False, session is dead
- [ ] `me = await client.get_me()` — verify identity
- [ ] **DO NOT** change profile, photo, username, or bio
- [ ] **DO NOT** join channels or send messages
- [ ] Disconnect cleanly in `finally` block

### Phase 3: Security Hardening (Day 0, after successful connection)

Wait 30-60 seconds after first connection, then:

1. **Terminate foreign sessions:**
```python
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest
auths = await client(GetAuthorizationsRequest())
for s in auths.authorizations:
    if not s.current:
        await client(ResetAuthorizationRequest(hash=s.hash))
        await asyncio.sleep(random.uniform(2, 5))  # delay between terminations
```

2. **Set 2FA password:**
```python
import secrets
password = secrets.token_hex(8)  # 16 char hex
await client.edit_2fa(new_password=password, hint=f"nc-{phone[-4:]}")
# Save password in account JSON under "twoFA" key
```

3. **Configure privacy:**
```python
from telethon.tl.functions.account import SetPrivacyRequest
from telethon.tl.types import (
    InputPrivacyKeyPhoneNumber, InputPrivacyKeyStatusTimestamp,
    InputPrivacyKeyProfilePhoto, InputPrivacyKeyForwards,
    InputPrivacyValueDisallowAll, InputPrivacyValueAllowContacts,
)
# Hide phone from everyone
await client(SetPrivacyRequest(InputPrivacyKeyPhoneNumber(), [InputPrivacyValueDisallowAll()]))
await asyncio.sleep(2)
# Last seen — contacts only
await client(SetPrivacyRequest(InputPrivacyKeyStatusTimestamp(), [InputPrivacyValueAllowContacts()]))
await asyncio.sleep(2)
# Photo — contacts only
await client(SetPrivacyRequest(InputPrivacyKeyProfilePhoto(), [InputPrivacyValueAllowContacts()]))
await asyncio.sleep(2)
# Forwards — nobody
await client(SetPrivacyRequest(InputPrivacyKeyForwards(), [InputPrivacyValueDisallowAll()]))
```

4. **Backup session files** before and after security ops.

### Phase 4: Warmup (Days 1-3)

Warmup teaches Telegram that the account is a real user. Run 2-4 sessions per day.

**Conservative mode** (first 48 hours — ALWAYS start here):
| Parameter | Value |
|-----------|-------|
| Delay multiplier | 2.0x |
| Actions per hour | max 5 |
| Session duration | 30 min |
| Between sessions | 6 hours |
| Typing speed | 0.10 sec/char |
| Pre-join delay | 120-300 sec |
| Action skip chance | 20% |

**Warmup actions (weighted random):**
- Read channels (weight 2): fetch 3-8 messages, simulate reading at 15 chars/sec
- React to posts: pick random emoji from safe pool
- Browse dialogs: fetch 10 recent, read one conversation

**Safe emoji pool:**
```python
["👍", "❤️", "🔥", "🎉", "😂", "😮", "😢", "🤔", "👎", "🤩", "💯", "😎", "🙏", "👀"]
```

**Fallback channels** (if none configured): `durov`, `telegram`, `tginfo`

**Active hours:** Only run during configured hours (e.g., 9-22 UTC). Outside = sleep.

### Phase 5: Ready → Farm (Day 3+)

After warmup, switch to moderate mode:
| Parameter | Value |
|-----------|-------|
| Delay multiplier | 1.3x |
| Typing speed | 0.07 sec/char |
| Pre-comment delay | 45-90 sec |
| Action skip chance | 10% |

For aged accounts (30+ days active), can use aggressive mode:
| Parameter | Value |
|-----------|-------|
| Delay multiplier | 1.0x |
| Typing speed | 0.04 sec/char |
| Pre-comment delay | 15-45 sec |
| Action skip chance | 5% |

### Phase 6: Health Monitoring (Continuous)

**Health Score (0-100):**
```
100
  - flood_wait_count × 8        (max -40)
  - spam_block_count × 15       (max -60)
  + successful_actions × 0.1    (max +20)
  + hours_without_error × 0.5   (max +15)
  + profile_completeness × 0.1  (max +10)
  - quarantine_penalty          (-20 if quarantined)
  → clamp [0, 100]
```

**Survivability Score (0-100):**
```
100
  - spam_block_count × 20       (max -80)
  - flood_wait_count × 5        (max -30)
  + account_age_days × 0.3      (max +20)
  + successful_actions × 0.05   (max +10)
  → clamp [0, 100]
```

**Decision thresholds:**
| Health | Action |
|--------|--------|
| >70 | Normal operations |
| 40-70 | Reduce activity, switch to conservative |
| <40 | Stop farm, warmup only |
| Survivability <40 | Expect ban, prepare replacement |

### Phase 7: Quarantine (On FloodWait)

When Telegram sends `FloodWaitError(seconds=N)`:
```python
quarantine_until = now + timedelta(seconds=N * 1.5)  # 50% padding
```

- Set account status to `quarantine`
- Stop all actions for this account
- Auto-lift when `quarantine_until` passes
- Manual lift available via API

### Phase 8: Freeze Detection & Auto-Appeal

**Monitoring script:** `scripts/account_monitor.py`

```bash
# One-time check
python scripts/account_monitor.py --once

# Daemon (every 30 min)
python scripts/account_monitor.py --daemon --interval 30

# Specific account
python scripts/account_monitor.py --once --phone 79637428613
```

**Status detection (read-only, NO /start):**
Read last 5 SpamBot messages and classify:
- `"no limits"` / `"free"` → status = `free`
- `"limited"` / `"restricted"` → status = `frozen`
- `"submitted"` / `"on review"` → status = `appeal_submitted`
- `"banned"` / `"deleted"` → status = `banned`

**Notifications:** Sends to ADMIN_BOT_TOKEN + DIGEST_CHAT_ID on any status change.

### Phase 9: SpamBot Appeal

**Auto-appeal script:** `scripts/appeal_with_tunnel.py`

```bash
# Run appeal for specific account
python scripts/appeal_with_tunnel.py --phone 79637428613 --base-port 18080

# Click Done after CAPTCHA solved
python scripts/appeal_with_tunnel.py --phone 79637428613 --phase done
```

**Appeal flow:**
1. Connect via Telethon through account's proxy
2. Send `/start` to @SpamBot
3. Wait for response (60s timeout per step, max 30 steps)
4. Auto-detect question type and answer from pool:
   - `reason` → "I believe this is a mistake..."
   - `usage` → "I use Telegram daily for personal chats..."
   - `full_name` → from profile
   - `email` → from config
   - `reg_year` → always send CURRENT year (2026), not actual registration year. SpamBot validates it as "valid year" — old years get rejected
   - `source` → "I heard from friends"
5. Click confirmation buttons automatically
6. When CAPTCHA detected → start proxy tunnel

**Appeal cooldown:** 24 hours between attempts per account.

### Phase 10: CAPTCHA Solving

CAPTCHA is Cloudflare Turnstile, tied to the IP that Telegram sees.

**Approach: Chrome with account's proxy**

1. Create Chrome extension for proxy auth:
```javascript
// background.js (Manifest V2)
chrome.webRequest.onAuthRequired.addListener(
  function(details, callback) {
    callback({
      authCredentials: {
        username: "PROXY_USERNAME",
        password: "PROXY_PASSWORD"
      }
    });
  },
  {urls: ["<all_urls>"]},
  ["asyncBlocking"]
);
```

2. Launch Chrome with proxy:
```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --proxy-server="http://proxy.proxyverse.io:9200" \
  --load-extension="/tmp/proxy_ext_PHONE" \
  --user-data-dir="/tmp/chrome_proxy_PHONE" \
  --no-first-run \
  "https://telegram.org/captcha?scope=sbot_frozen&actor=TOKEN"
```

3. User solves Turnstile CAPTCHA manually in Chrome
4. Run `--phase done` to click "Done" in SpamBot
5. Check SpamBot response for success

**Critical:** The proxy used for Chrome MUST support HTTPS CONNECT tunneling. Test first:
```python
import requests
proxy_url = f"http://{user}:{password}@{host}:{port}"
r = requests.get("https://telegram.org/", proxies={"https": proxy_url}, timeout=10)
# Must return 200/301/302
```

Not all Proxyverse proxies support HTTPS CONNECT — test multiple until one works.

**Fallback:** Reverse proxy tunnel on localhost (but Turnstile may fail on localhost domain).

## Error Handling Reference

| Error | Cause | Action |
|-------|-------|--------|
| `FrozenMethodInvalidError` | Profile change too soon | Quarantine 48h, no retry |
| `FloodWaitError(N)` | Rate limited | Quarantine N×1.5 seconds |
| `SessionRevokedError` | send_code_request or killed | Mark dead, no recovery |
| `UserDeactivatedBanError` | Permanent ban | Mark banned, move to _banned/ |
| `PhoneNumberBannedError` | Phone blocked | Mark banned, no recovery |
| `AuthKeyUnregisteredError` | Invalid session | Mark dead, no recovery |
| `ChannelPrivateError` | Can't access channel | Skip channel, not account's fault |
| `ConnectionError` | Proxy or network | Retry with different proxy |

## Project Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/account_monitor.py` | Status monitoring daemon with notifications |
| `scripts/appeal_with_tunnel.py` | SpamBot appeal + CAPTCHA tunnel |
| `scripts/account_security.py` | 2FA, session termination, privacy |
| `scripts/warmup_launcher.py` | Warmup session launcher |

## Project Modules Reference

| Module | Purpose |
|--------|---------|
| `core/anti_detection.py` | Typing/reading/delay simulation (3 modes) |
| `core/warmup_engine.py` | Warmup session management |
| `core/health_scorer.py` | Health + survivability scoring |
| `core/quarantine_manager.py` | FloodWait quarantine logic |
| `core/spambot_auto_appeal.py` | Automated SpamBot appeal |
| `core/farm_thread.py` | Farm thread state machine |
| `utils/standalone_helpers.py` | Proxy loading, client building |
