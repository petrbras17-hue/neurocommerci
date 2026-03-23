# Account Registration & Login Flow Audit

Last updated: 2026-03-23

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [SMS Provider Deep Dive](#2-sms-provider-deep-dive)
3. [Temporary Email Service](#3-temporary-email-service)
4. [Registration Flow (AccountFactory)](#4-registration-flow-accountfactory)
5. [Session Lifecycle (SessionManager)](#5-session-lifecycle-sessionmanager)
6. [Session Pool (SessionPool)](#6-session-pool-sessionpool)
7. [Proxy Management](#7-proxy-management)
8. [Configuration](#8-configuration)
9. [Web Research: Market Intelligence](#9-web-research-market-intelligence)
10. [Recommendations](#10-recommendations)

---

## 1. Executive Summary

NEURO COMMENTING implements a multi-layered account registration and session management system for Telegram accounts. The system consists of:

- **AccountFactory** (`core/account_factory.py`) -- full registration pipeline: buy number, send code, handle email verification, receive SMS, register account, save session
- **MultiSmsProvider** (`core/sms_provider.py`) -- multi-provider SMS fallback system with 5 providers
- **TempEmailService** (`core/temp_email.py`) -- automated temporary email for Telegram's email-before-SMS requirement (2025-2026)
- **SessionManager** (`core/session_manager.py`) -- legacy phone-keyed LRU pool for TelegramClient instances
- **SessionPool** (`core/session_pool.py`) -- new account_id-keyed centralized pool for SaaS control plane
- **ProxyManager** (`core/proxy_manager.py`) -- proxy assignment (static round-robin or rotating sticky sessions)

### Critical Constraint

Telegram blocked SMS delivery for third-party API apps in February 2023. `auth.sendCode` via Telethon returns `SentCodeTypeApp` (push notification to the Telegram app), NOT SMS. Since virtual numbers have no Telegram app installed, the code never arrives. The codebase handles this with:
1. `auth.resendCode` escalation (up to 2 attempts) to reach an SMS-capable delivery type
2. Email verification flow (`SentCodeTypeSetUpEmailRequired`) -- new requirement since 2025-2026
3. Fragment SMS detection and early abort (unsupported automatically)

---

## 2. SMS Provider Deep Dive

All providers implement `BaseSmsProvider` (abstract base class) with these methods:
- `get_balance()` -- check account balance
- `buy_number(country)` -- purchase a virtual number for Telegram
- `wait_for_code(request_id, timeout_sec, poll_interval)` -- poll for incoming SMS code
- `cancel_number(request_id)` -- cancel/release a number
- `mark_bad(request_id)` -- mark number as bad (defaults to cancel)
- `request_another_sms(request_id)` -- request resend (not all providers support)
- `prolong_number(phone)` -- extend rental (not all providers support)
- `get_available_count(country)` -- check available numbers

### 2.1 SMS-Man (RECOMMENDED PRIMARY)

| Field | Value |
|---|---|
| Class | `SmsManProvider` |
| API Endpoint | `https://api.sms-man.com/control` |
| Auth | `token` query parameter |
| Telegram App ID | `6` |
| Buy Number | `GET /get-number?country_id={id}&application_id=6&token={key}` |
| Wait for Code | `GET /get-sms?request_id={id}&token={key}` (poll until `sms_code` is non-null) |
| Cancel | `GET /set-status?request_id={id}&status=reject&token={key}` |
| Resend | `GET /set-status?request_id={id}&status=retrysms&token={key}` |
| Default Poll Interval | 3.0s |
| Default Timeout | 120s |
| Config Key | `SMSMAN_API_KEY` |
| Supported Countries | KZ (2), RU (1), UZ (58), KG (65), IN (14), ID (4), TR (62) |
| Cost per Number | From ~$0.05/activation (varies by country) |
| Market Status (2026) | Active. 195 countries, 1500+ services. Became top alternative after SMS-Activate shutdown. |

### 2.2 SmsPVA (REAL SIM, 99% Delivery)

| Field | Value |
|---|---|
| Class | `SmsPvaProvider` |
| API Endpoint | `https://smspva.com/priemnik.php` |
| Auth | `apikey` query parameter |
| Telegram Service | `opt29` |
| Buy Number | `GET ?metod=get_number&service=opt29&country={code}&apikey={key}` |
| Wait for Code | `GET ?metod=get_sms&service=opt29&id={id}&apikey={key}` (poll until `sms` is non-null) |
| Cancel | `GET ?metod=denial&service=opt29&id={id}&apikey={key}` |
| Default Poll Interval | 5.0s (SmsPVA recommends 4-5s) |
| Default Timeout | 120s |
| Config Key | `SMSPVA_API_KEY` |
| Supported Countries | KZ, RU, UZ, KG, IN, ID, TR, US, UK |
| Error Handling | 5 consecutive error max before abort; code extraction via regex `\b(\d{4,6})\b` |
| API Quirks | `response=2` means no numbers; `response=5` means rate limit |

### 2.3 5SIM (CHEAP, JWT Auth)

| Field | Value |
|---|---|
| Class | `FiveSimProvider` |
| API Endpoint | `https://5sim.net/v1` |
| Auth | `Authorization: Bearer {api_key}` header |
| Telegram Product | `telegram` |
| Buy Number | `GET /user/buy/activation/{country}/any/telegram` |
| Wait for Code | `GET /user/check/{request_id}` (poll until `status == "RECEIVED"` and `sms` list populated) |
| Finalize | `GET /user/finish/{request_id}` (after receiving code) |
| Cancel | `GET /user/cancel/{request_id}` |
| Default Poll Interval | 3.0s |
| Default Timeout | 120s |
| Config Key | `FIVESIM_API_KEY` |
| Supported Countries | KZ (kazakhstan), RU (russia), UZ (uzbekistan), KG (kyrgyzstan), IN (india), ID (indonesia), TR (turkey), US (usa), UK (england) |
| Cost per Number | From ~$0.10 for Telegram (10x higher than basic services) |
| Price Check | `GET /guest/prices?country={name}&product=telegram` (no auth needed) |
| Market Status (2026) | Active. 500K+ numbers, 180+ countries. Pay-as-you-go, crypto payments accepted. |

### 2.4 Grizzly SMS (TELEGRAM-FOCUSED)

| Field | Value |
|---|---|
| Class | `GrizzlySmsProvider` |
| API Endpoint | `https://api.grizzlysms.com/stubs/handler_api.php` |
| Auth | `api_key` query parameter |
| Telegram Service | `go` |
| Buy Number | `GET ?action=getNumberV2&service=go&country={code}&maxPrice=5&api_key={key}` |
| Wait for Code | `GET ?action=getStatus&id={id}&api_key={key}` (poll until `STATUS_OK:{code}`) |
| Set Waiting | `GET ?action=setStatus&id={id}&status=1` (must call before polling) |
| Finalize | `GET ?action=setStatus&id={id}&status=6` (done) |
| Cancel | `GET ?action=setStatus&id={id}&status=8` |
| Resend | `GET ?action=setStatus&id={id}&status=3` |
| Default Poll Interval | 3.0s |
| Default Timeout | 120s |
| Config Key | `GRIZZLY_API_KEY` |
| Supported Countries | KZ (2), RU (0), UZ (40), KG (68), IN (22), ID (6), TR (62), US (187), UK (16) |
| Max Price | $5 per number (hardcoded in `maxPrice` param) |
| Response Format | JSON (V2) or legacy `ACCESS_NUMBER:id:phone` |
| Market Status (2026) | Active. 150+ countries, 2300+ services. From $0.04/number. Also sells pre-made tdata accounts. |

### 2.5 VAK-SMS (LEGACY FALLBACK)

| Field | Value |
|---|---|
| Class | `VakSmsProvider` |
| API Endpoint | `https://vak-sms.com/api` |
| Auth | `apiKey` query parameter |
| Buy Number | `GET /getNumber/?service=tg&country={code}&apiKey={key}` |
| Wait for Code | `GET /getSmsCode/?idNum={id}&apiKey={key}` (poll until `smsCode` is non-null) |
| Cancel | `GET /setStatus/?status=end&idNum={id}&apiKey={key}` |
| Mark Bad | `GET /setStatus/?status=bad&idNum={id}&apiKey={key}` |
| Resend | `GET /setStatus/?status=send&idNum={id}&apiKey={key}` |
| Prolong | `GET /prolongNumber/?service=tg&tel={phone}&apiKey={key}` (unique to VAK-SMS) |
| Available Count | `GET /getCountNumber/?service=tg&country={code}&price=true&apiKey={key}` |
| Default Poll Interval | 3.0s |
| Default Timeout | 120s |
| Config Key | `VAKSMS_API_KEY` |
| Unique Feature | Number prolongation (extend rental period) |
| Market Status (2026) | Operational but noted as having delivery problems in the codebase comments. Last in fallback order. |

### 2.6 MultiSmsProvider (Fallback Manager)

The `MultiSmsProvider` class wraps all configured providers and implements automatic fallback:

- **Default order**: sms-man -> smspva -> 5sim -> grizzly -> vak-sms
- **buy_number()**: Tries each provider in order until one succeeds. Tracks which provider is "current" for subsequent `wait_for_code` calls.
- **buy_number_with_code()**: Full cycle -- buy + wait_for_code with automatic fallback. If code times out on one provider, cancels the number and tries the next provider.
- **test_all_providers()**: Diagnostic method that checks balance + available numbers for all configured providers.
- **get_all_balances()**: Returns balance for every configured provider.

---

## 3. Temporary Email Service

File: `core/temp_email.py`

### Why It Exists

Since 2025-2026, Telegram requires email verification BEFORE sending SMS codes for new registrations. `auth.sendCode` returns `SentCodeTypeSetUpEmailRequired`. The flow becomes:
1. `auth.sendCode` returns email setup required
2. Create temporary email via mail.tm or GuerrillaMail
3. Call `account.sendVerifyEmailCode` with the temp email
4. Wait for Telegram's verification email, extract 5-6 digit code
5. Call `account.verifyEmail` with the code
6. Telegram then sends the actual SMS code

### Provider: mail.tm (RECOMMENDED)

| Field | Value |
|---|---|
| Class | `MailTmProvider` |
| API Base | `https://api.mail.tm` |
| Auth | JWT (create account, then get token) |
| Rate Limit | 8 QPS per IP |
| Delivery Speed | 1-5 seconds typical |
| Cost | Free |

**Flow:**
1. `GET /domains` -- fetch available domains
2. `POST /accounts` -- create `tg{random10}@{domain}` with random password
3. `POST /token` -- get JWT for inbox access
4. `GET /messages` + `GET /messages/{id}` -- poll inbox for Telegram emails

### Provider: GuerrillaMail (BACKUP)

| Field | Value |
|---|---|
| Class | `GuerrillaMailProvider` |
| API Base | `https://api.guerrillamail.com/ajax.php` |
| Auth | Session token (`sid_token`) |
| Rate Limit | Unknown, generous |
| Delivery Speed | 1-10 seconds |
| Cost | Free |
| Caveat | Domains sometimes blocked by major services |

**Flow:**
1. `GET ?f=get_email_address&lang=en` -- get email + session token
2. `GET ?f=check_email&sid_token={sid}&seq=0` -- poll inbox

### Code Extraction

Telegram code extraction uses 3 regex patterns applied in order:
1. `(?:login|verification|verify)\s*code[:\s]+(\d{5,6})` -- "Login code: 12345"
2. `code[:\s]+(\d{5,6})` -- "Code: 12345"
3. `\b(\d{5,6})\b` -- standalone 5-6 digit number

The service checks sender for keywords: "telegram", "t.me", "noreply", "no-reply" to filter Telegram emails.

---

## 4. Registration Flow (AccountFactory)

File: `core/account_factory.py`

### Safe API IDs

Only two API IDs are permitted (API ID 4 is FLAGGED by Telegram):

| API ID | Name | API Hash | Device Profiles |
|---|---|---|---|
| 2040 | Telegram Desktop | `b18441a1ff607e10a989891a5462e627` | Windows 10/11, macOS 14.3, Ubuntu 22.04 |
| 21724 | AndroidX | `3e0cb5efcd52300aec5994fdfc5bdc16` | Samsung S24/S23, Pixel 8, Xiaomi 14, OnePlus 12 |

### Step-by-Step Registration Process

**Step 1: Buy Number**
- Call `sms.buy_number(country)` via the configured SMS provider (single or multi)
- Default country: Kazakhstan ("kz")
- Human-like delay: 2-5 seconds after purchase

**Step 2: Create TelegramClient**
- Generate random device fingerprint from `SAFE_PROFILES[api_id]`
- Create `TelegramClient` with the fingerprint (device_model, system_version, app_version)
- Apply proxy if provided (socks5/http tuple format)
- Session saved to `data/sessions/{user_id}/{phone}.session`
- Connect to Telegram

**Step 3a: Send Code (Initial)**
- Call `client.send_code_request("+{phone}")`
- Log the `type`, `next_type`, `timeout`, and `phone_code_hash`

**Step 3b: Handle Email Setup Required (NEW 2025-2026)**
If `SentCodeTypeSetUpEmailRequired` is returned:
1. Create temporary email via `TempEmailService.create_email()`
2. Wait 2-4 seconds (human-like)
3. Call `SendVerifyEmailCodeRequest` with `EmailVerifyPurposeLoginSetup`
4. Wait for Telegram verification email (120s timeout, 3s poll interval)
5. Extract 5-6 digit code from email body/HTML
6. Call `VerifyEmailRequest` with the extracted code
7. After verification, Telegram returns `EmailVerifiedLogin` with a new `sent_code` containing SMS delivery info
8. If no `sent_code` in response, re-call `auth.sendCode` to trigger SMS delivery

On email timeout or failure: cancel number, return `FactoryResult(success=False)`

**Step 3c: Handle Non-SMS Delivery Types**
If code was sent via App Push (not SMS-receivable):
1. Check for Fragment SMS -- **dead end**, abort immediately (requires fragment.com manual access)
2. If `next_type` exists, wait for timeout (up to 120s), then call `ResendCodeRequest`
3. If resend returns non-SMS type and another `next_type` exists, wait and resend again (2nd attempt)
4. If all resend attempts fail to reach SMS: abort with `sms_blocked_by_telegram` error
5. Error message suggests: contact `sms@telegram.org` with `#enableSMS`, or buy pre-made accounts

**Step 4: Wait for SMS Code**
- For `SentCodeTypeSmsWord` or `SentCodeTypeSmsPhrase`: extract secret word/phrase from SMS text using `beginning` hint
- For standard SMS: wait for numeric code via `sms.wait_for_code(request_id, timeout=120)`
- Human-like pause: 3-7 seconds after receiving code

**Step 5: Register Account**
- Generate random Russian name (male/female 50/50 split, from pools of 20 first names + 15 last names)
- Try `client.sign_in()` first (existing account)
  - If `SessionPasswordNeededError`: abort (2FA on existing account)
  - If other error: call `client.sign_up()` with first_name, last_name (new account)
- Human-like pause: 2-5 seconds

**Step 6: Update Profile**
- Call `UpdateProfileRequest` with first_name + last_name
- Non-fatal on failure (logged as warning)

**Step 7: Save Files**
- Write metadata JSON to `data/sessions/{user_id}/{phone}.json` containing:
  - phone, first_name, last_name, device params, app_id, app_hash, twoFA, country, created_at, sms_provider, sms_request_id, cost_rub
- Create `StringSession` backup to `data/session_backups/{phone}.backup`
- Disconnect client (NEVER call `client.log_out()`)

### Batch Creation

`create_batch(count, country, api_id, delay_between)`:
- Creates `count` accounts sequentially
- Default delay between registrations: 300-900 seconds (5-15 minutes)
- Logs success/failure for each account

### DB Registration

`register_in_db(result)`:
- Creates `Account` ORM record with:
  - `status="active"`, `health_status="alive"`, `lifecycle_stage="warming_up"`
  - `warmup_phase="STEALTH"`, `warmup_day=0`, `warmup_mode="conservative"`

### Error Handling

| Error Type | Handling |
|---|---|
| `SendCodeUnavailableError` | Cancel number, return error. SMS blocked for third-party apps. |
| `PhoneNumberBannedError` | Mark number as bad via `sms.mark_bad()`. |
| `PhoneNumberInvalidError` | Cancel number. |
| `PhoneCodeExpiredError` / `PhoneCodeInvalidError` | Return error with cost (number already used). |
| `SmsTimeoutError` | Cancel number, return error. |
| `SmsApiError` | Return error (SMS provider API issue). |
| `FloodWaitError` | Return error with wait time. Number cost may be lost. |
| `email_code_timeout` | Cancel number (email verification step failed). |
| `fragment_sms_unsupported` | Cancel number (Fragment SMS requires manual access). |

---

## 5. Session Lifecycle (SessionManager)

File: `core/session_manager.py`

### Architecture

Legacy phone-keyed LRU pool for TelegramClient instances. Used by FarmThread, WarmupEngine, and AccountManager.

### Key Features

- **LRU Eviction**: When `MAX_CONNECTED_CLIENTS_PER_WORKER` is reached, disconnects the least recently used client
- **Device Fingerprint**: Loads per-account device params from JSON metadata files (device, sdk, app_version, lang_pack, api_id, api_hash)
- **Per-User Isolation**: Session paths scoped to `data/sessions/{user_id}/{phone}.session`
- **Proxy Resolution**: Uses `get_live_proxy_config()` to resolve unique live proxy per account
- **STRICT_PROXY_PER_ACCOUNT**: When enabled (default=True), blocks connections without a unique proxy
- **Duplicate Detection**: Prevents two phones from using the same session name via `_session_owner` dict
- **Device Cache**: LRU cache of 2000 entries for device parameters

### Connection Flow

1. Check if client already connected and alive -> return
2. Evict LRU client if at capacity
3. Resolve live proxy (DB-backed, with health check)
4. If `STRICT_PROXY_PER_ACCOUNT=True` and no proxy available -> block connection
5. Check session name not already owned by another phone
6. Create `TelegramClient` with per-account api_id/api_hash from JSON metadata
7. Connect and verify `is_user_authorized()`
8. If `FROZEN_PROBE_ON_CONNECT=True`: run capability probe (search probe) to detect frozen/restricted accounts
9. Cache client in pool

### Important Safety Rules

- api_id/api_hash MUST come from JSON metadata (not global config) -- `ValueError` if missing
- API ID 4 triggers a warning (flagged by Telegram)
- `AuthKeyUnregisteredError` marks account as dead (irrecoverable without re-registration)
- FloodWaitError uses 1.5x backoff
- Never calls `send_code_request` -- if not authorized, returns None

---

## 6. Session Pool (SessionPool)

File: `core/session_pool.py`

### Architecture

New centralized TelegramClient pool keyed by `account_id` (not phone). Designed for SaaS control plane. Coexists with legacy `SessionManager`.

### Key Design Principles

- **One client per account_id**, never more
- **Thread-safe**: one `asyncio.Lock` per account_id prevents concurrent connect races
- **NEVER calls `send_code_request`** -- if session is not authorized, raises `SessionDeadError`
- **Proxy from DB**: `Account.proxy_id` -> `Proxy` ORM row
- **Device fingerprint from JSON metadata**
- **Idle eviction**: clients disconnected after `idle_timeout_sec` (default 10 minutes)
- **Hard ceiling**: `max_concurrent` (default 20)
- **Lock pruning**: max 500 account locks, stale locks pruned

### Pool Miss Flow

1. Load `Account` + `Proxy` from DB (tenant-scoped with `tenant_id` filter)
2. Resolve `.session` file path (supports absolute, tenant-scoped, or flat layout)
3. Load metadata JSON for api_id/api_hash/device fingerprint
4. Build `TelegramClient` with all parameters
5. Connect and verify authorization
6. On auth error -> `SessionDeadError` (callers MUST mark account dead in DB)

### Custom Exceptions

- `SessionDeadError` -- session revoked, .session missing, metadata invalid, or unauthorized. NEVER retry.
- `PoolCapacityError` -- pool full, release a client first.

---

## 7. Proxy Management

### ProxyManager (`core/proxy_manager.py`)

Two operating modes:
- **Static** (`PROXY_ROTATING=False`): N proxies -> N accounts, round-robin assignment
- **Rotating** (`PROXY_ROTATING=True`): 1 proxy endpoint -> N accounts, each gets a deterministic sticky session ID based on phone MD5 hash

Supported proxy formats:
- `type://user:pass@host:port`
- `host:port:user:pass`
- `host:port`

Proxy types: `socks5` (default), `socks4`, `http`

Telethon format: `(type_int, host, port, True, username, password)` where type_int: 1=SOCKS4, 2=SOCKS5, 3=HTTP

### Proxy Bindings (`utils/proxy_bindings.py`)

DB-backed proxy lifecycle:
- **Sync from file**: `sync_proxies_from_file()` -- imports proxies from `data/proxies.txt` into DB, handles deduplication and reactivation
- **Health probing**: `_probe_proxy()` validates proxy via HTTP or SOCKS connection to `api.ipify.org`
- **Failure tracking**: `consecutive_failures` counter with configurable threshold (`PROXY_FAILURES_BEFORE_DISABLE`, default=2)
- **Health states**: `unknown` -> `alive` -> `failing` -> `dead`
- **Auto-disable**: After N consecutive failures, proxy is marked `is_active=False` with `invalidated_at` timestamp
- **Recheck cooldown**: `PROXY_RECHECK_COOLDOWN_SEC` (default 900s / 15 min)

### Proxy Health Config

| Setting | Default | Purpose |
|---|---|---|
| `PROXY_HEALTH_TIMEOUT_SEC` | 8 | Timeout for proxy health check |
| `PROXY_RECHECK_COOLDOWN_SEC` | 900 | Min seconds between rechecks |
| `PROXY_FAILURES_BEFORE_DISABLE` | 2 | Consecutive failures to disable |
| `PROXY_MIN_FREE_POOL` | 20 | Min unassigned proxies to maintain |
| `PROXY_DELETE_INVALID_AFTER_DAYS` | 7 | Days before removing dead proxies |
| `STRICT_PROXY_PER_ACCOUNT` | True | Block connection without unique proxy |

---

## 8. Configuration

File: `config.py`

### SMS Provider Config

| Setting | Default | Description |
|---|---|---|
| `SMS_PROVIDER_ORDER` | `sms-man,smspva,5sim,grizzly,vak-sms` | Fallback order |
| `SMSMAN_API_KEY` | `""` | SMS-Man API key |
| `SMSPVA_API_KEY` | `""` | SmsPVA API key |
| `FIVESIM_API_KEY` | `""` | 5SIM API key |
| `GRIZZLY_API_KEY` | `""` | Grizzly SMS API key |
| `VAKSMS_API_KEY` | `""` | VAK-SMS API key (legacy) |

The `sms_provider_keys` property builds a dict of configured providers (only those with non-empty keys).

### Telegram API Config

| Setting | Default | Description |
|---|---|---|
| `TELEGRAM_API_ID` | `0` | Global default API ID (not used for factory accounts) |
| `TELEGRAM_API_HASH` | `""` | Global default API hash |

Note: AccountFactory uses hardcoded safe API IDs (2040, 21724) with their own api_hash values, NOT the global config.

### Session & Account Config

| Setting | Default | Description |
|---|---|---|
| `SESSIONS_DIR` | `data/sessions` | Root session directory |
| `MAX_CONNECTED_CLIENTS_PER_WORKER` | 50 | Max concurrent TelegramClient connections |
| `STRICT_PROXY_PER_ACCOUNT` | True | Require unique proxy per account |
| `FROZEN_PROBE_ON_CONNECT` | True | Run capability probe on connect |
| `API_ID_4_STRICT_MODE` | True | Stricter limits for flagged API ID 4 |

---

## 9. Web Research: Market Intelligence

### 9.1 SMS Provider Market Status (March 2026)

**SMS-Activate**: SHUT DOWN in 2025. Was the largest provider. Its closure pushed users to SMS-Man, 5SIM, and Grizzly.

**SMS-Man**: Active and thriving. Positioned as the primary replacement for SMS-Activate. 195 countries, 1500+ services, mature API, desktop app. Pricing from ~$0.05/activation. 24/7 support, Telegram bot for receiving SMS directly.

**5SIM**: Active. 500K+ numbers across 180+ countries. Telegram numbers cost ~$0.10/number (10x higher than basic services). Pay-as-you-go, cryptocurrency payments accepted. JWT-based API.

**Grizzly SMS**: Active. Strong Telegram focus. 150+ countries, 2300+ services. From $0.04/number. Also sells pre-made tdata accounts (bypasses SMS entirely). MaxPrice parameter prevents overpaying.

**SmsPVA**: Operational. Real SIM option for higher delivery rates. Supports 9 countries in the codebase.

**VAK-SMS**: Operational but has known delivery issues. Noted as "fallback" in the codebase. API is functional but less reliable than alternatives.

### 9.2 Telegram Registration Challenges (2026)

1. **SMS blocked for third-party apps** (since Feb 2023): `auth.sendCode` returns `SentCodeTypeApp` (push notification), not SMS. Mitigation: `auth.resendCode` escalation.

2. **Email verification required** (since 2025-2026): `SentCodeTypeSetUpEmailRequired` -- must verify an email before Telegram will send SMS. The codebase handles this via `TempEmailService`.

3. **Fragment SMS**: Some numbers receive codes via Fragment.com instead of SMS. This cannot be automated and is a dead end for the factory.

4. **IP fraud scoring**: Telegram checks IP reputation. Datacenter IPs are flagged more aggressively than residential IPs. Fraud score should be under 10.

5. **Country-IP matching**: Registration success improves when the phone number country matches the proxy IP country.

### 9.3 Country Success Rates

Based on web research and codebase configuration:

| Country | Code | Notes |
|---|---|---|
| Kazakhstan (KZ) | Default in codebase | Good availability, lower fraud detection |
| Uzbekistan (UZ) | Supported | Good availability |
| Kyrgyzstan (KG) | Supported | Good availability |
| India (IN) | Supported | Largest Telegram user base (104M), high availability but high scrutiny |
| Russia (RU) | Supported | 34.4M users, moderate scrutiny |
| Indonesia (ID) | Supported | 27.2M users |
| Turkey (TR) | Supported | Moderate availability |
| US/UK | Some providers | Higher prices, more scrutiny |

CIS countries (KZ, UZ, KG) remain the best choices for registration -- lower fraud detection, good number availability, and lower prices.

### 9.4 Alternative: Pre-Made Accounts

Instead of registering via API, buying pre-made accounts is increasingly viable:

- **tdata format**: Desktop client data folder. Place `tdata/` next to `telegram-portable.exe`.
- **session+json format**: Telethon session file with metadata. Directly compatible with NEURO COMMENTING.
- **Suppliers**: Grizzly SMS (also offers accounts), CryptoCartel, TeleMember, TelegramGrowthStudio, hstock.org
- **Pricing**: Varies by age, country, quality. Autoreg (fresh) accounts are cheapest but most fragile. Aged accounts cost more but survive longer.
- **Risk**: Bought accounts may already be flagged or have previous spam history. Quality varies significantly.

### 9.5 Proxy Recommendations

| Proxy Type | Risk Level | Cost | Recommendation |
|---|---|---|---|
| Datacenter | HIGH | $1-3/mo | Avoid for registration. Telegram actively flags datacenter IPs. |
| Residential | LOW | $5-15/mo | RECOMMENDED. Look like real home connections. |
| ISP Static | LOW | $3-8/mo | Good balance of reliability and cost. |
| Mobile 4G/5G | LOWEST | $10-30/mo | Most legitimate-looking. Best for high-value accounts. |
| Rotating Residential | MEDIUM | $5-10/GB | Good for registration, but IP changes can trigger detection. Use sticky sessions. |

Key: Every IP should be checked against fraud databases (CyberYozh Fraud Score tool) before use. Score must be under 10.

---

## 10. Recommendations

### 10.1 SMS Provider Strategy

1. **Keep SMS-Man as primary**: Most reliable since SMS-Activate shutdown. Mature API, good Telegram support, 195 countries.
2. **5SIM as secondary**: Good API, cheap, wide coverage. JWT auth is cleaner.
3. **Grizzly as tertiary**: Telegram-focused, maxPrice prevents overspend, offers accounts directly.
4. **Demote VAK-SMS**: Known delivery issues. Keep as last resort only.
5. **SmsPVA for high-value registrations**: Real SIM cards have highest delivery rate (99%).

Recommended `SMS_PROVIDER_ORDER`: `sms-man,5sim,grizzly,smspva,vak-sms`

### 10.2 Country Strategy

1. **Primary: Kazakhstan (KZ)** -- lowest fraud detection, good availability, cheap
2. **Secondary: Uzbekistan (UZ), Kyrgyzstan (KG)** -- CIS countries with low scrutiny
3. **Avoid: Russia (RU)** for bulk registration -- higher scrutiny since 2024
4. **Avoid: US/UK** -- highest prices, most aggressive fraud detection
5. **Consider: Indonesia (ID), India (IN)** -- high availability but watch for IP-country mismatch

### 10.3 Alternative Registration Approaches

1. **Pre-made session+json accounts**: Buy directly from suppliers like Grizzly SMS or specialized sellers. Eliminates the SMS/email verification problem entirely. Compatible with the existing session import flow.

2. **ReDroid emulator farm** (already in progress): Run official Telegram app in Android containers. Receives push codes natively. No SMS limitation. Progress tracked in `knowledge/redroid_progress.md`.

3. **Fragment.com numbers**: Buy Telegram phone numbers from Fragment (blockchain-based). Numbers are purpose-built for Telegram and bypass SMS issues.

4. **Email `sms@telegram.org` with `#enableSMS`**: Request SMS delivery enablement for specific API IDs. Low success rate but worth trying for custom API IDs.

### 10.4 Proxy Strategy

1. **Use residential proxies for registration**: Datacenter IPs are actively flagged. Residential IPs have near-zero fraud scores.
2. **IP-country matching**: Kazakhstan number -> Kazakhstan/CIS proxy IP. Mismatch increases ban risk.
3. **Fraud score check**: Validate every proxy IP against fraud databases before use. Target score < 10.
4. **Sticky sessions for rotating proxies**: Each account must consistently use the same IP. The existing `PROXY_STICKY_FORMAT` implementation handles this correctly.
5. **1:1 account-proxy binding**: Keep `STRICT_PROXY_PER_ACCOUNT=True`. Multiple accounts sharing an IP is the fastest path to mass bans.

### 10.5 Code Quality Observations

1. **AccountFactory is well-structured**: The SentCodeType handling with up to 2 resend attempts and Fragment SMS detection is thorough.
2. **Email verification flow is complete**: mail.tm + GuerrillaMail fallback with code extraction regex covers the 2025-2026 requirement.
3. **Never calls `log_out()`**: Correctly preserves session auth keys.
4. **Human-like delays**: Random delays between 2-7s between steps, 5-15 min between batch registrations.
5. **Error handling is comprehensive**: All Telethon error types are caught and handled with appropriate cleanup.

### 10.6 Potential Improvements (Not Implemented -- Research Only)

1. **Add HeroSMS as a 6th provider**: New entrant in 2026, good reviews.
2. **Fragment.com integration**: Automate Fragment number purchase for accounts that need it.
3. **IP fraud score pre-check**: Validate proxy IPs via CyberYozh or similar API before assigning to accounts.
4. **SmsWord/SmsPhrase test coverage**: The word/phrase extraction logic is complex and would benefit from unit tests with real Telegram SMS samples.
5. **Email provider diversification**: mail.tm is the only reliable option; GuerrillaMail domains are often blocked. Consider adding Mailnesia, TempMail.org, or YOPmail.

---

## Sources

### Web Research (March 2026)
- [VAK-SMS API](https://vak-sms.com/api/vak/)
- [Telegram Account Farming in 2026](https://mobileproxy.space/en/pages/telegram-account-farming-in-2026-a-step-by-step-guide-using-telegram-expert.html)
- [Best SMS Verification Services in 2026](https://hero-sms.com/blog/best-sms-verification-services/)
- [5SIM Prices](https://5sim.net/prices)
- [5SIM Review 2026](https://hero-sms.com/blog/5sim-review-and-alternatives/)
- [SMS-Activate Closed: Alternatives 2026](https://sms-man.com/blog/sms-activate-closed-cheap-sms-verification-alternatives-2026/)
- [How to Create Anonymous Telegram Account 2026](https://sms-man.com/blog/how-to-create-an-anonymous-telegram-account/)
- [How to Get Virtual Number for Telegram 2026](https://sms-man.com/blog/how-to-get-a-virtual-number-for-telegram-in-2026/)
- [SMS-Activate vs SMS-Man Comparison 2026](https://hero-sms.com/blog/sms-activate-vs-sms-man/)
- [CryptoCartel Telegram Accounts](https://cryptocartel.cc/telegram_accounts/)
- [TelegramGrowthStudio: Buy Accounts](https://telegramgrowthstudio.com/blog/buy-telegram-accounts.html)
- [Grizzly SMS](https://grizzlysms.com/)
- [Grizzly SMS Telegram Numbers](https://grizzlysms.com/telegram)
- [Grizzly SMS Reviews 2026](https://slashdot.org/software/p/Grizzly-SMS/)
- [Telegram User Authorization API](https://core.telegram.org/api/auth)
- [Telegram account.verifyEmail API](https://core.telegram.org/method/account.verifyEmail)
- [Telegram Registration Guide 2025](https://www.socialecho.net/en/blog/docs/telegram-registration-guide-2025)
- [Fix Telegram Not Sending Code 2026](https://www.adspower.com/blog/fix-telegram-not-sending-verification-code-11-ways)
- [Telegram Users by Country 2026](https://worldpopulationreview.com/country-rankings/telegram-users-by-country)
- [Best Proxy for Telegram 2026](https://multilogin.com/blog/proxies-for-telegram/)
- [Telegram Proxy in 2026: Which One to Choose](https://gramgpt.io/en/blog/telegram-proxy-automation)
- [Best Telegram Proxies 2026](https://pixelscan.net/blog/best-telegram-proxies/)
- [Best Virtual Numbers for Telegram](https://pixelscan.net/blog/best-virtual-numbers-for-telegram/)

### Codebase Files Audited
- `core/account_factory.py` -- 1028 lines, full registration pipeline
- `core/sms_provider.py` -- 1015 lines, 5 SMS providers + MultiSmsProvider
- `core/temp_email.py` -- 557 lines, mail.tm + GuerrillaMail
- `core/session_manager.py` -- 434 lines, legacy LRU session pool
- `core/session_pool.py` -- 731 lines, new centralized session pool
- `core/proxy_manager.py` -- 312 lines, proxy assignment and validation
- `config.py` -- 509 lines, all configuration settings
- `utils/proxy_bindings.py` -- 200 lines (audited), DB-backed proxy lifecycle
