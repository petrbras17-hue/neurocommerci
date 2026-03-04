# API IDs and Device Fingerprints

## Official Telegram API IDs

These are the API IDs used by official Telegram applications:

| App | API ID | API Hash | Status |
|-----|--------|----------|--------|
| Android (old) | 4 | 014b35b6184100b085b0d0572f9b5103 | **FLAGGED — DO NOT USE** |
| Android | 6 | eb06d4abfb49dc3eeb1aeb98ae0f581e | Safe but monitored |
| iOS | 10840 | 33c45224029f7de15cec1bfc18076588 | Safe |
| Desktop | 2040 | b18441a1ff607e10a989891a5462e627 | **RECOMMENDED** |
| macOS | 2834 | 68875f756c9b437a8b916ca3de215571 | Safe |
| Web A | 2496 | 8da85b0d5bfe62527e5b244c209159c3 | Safe |
| Web K | 2496 | 8da85b0d5bfe62527e5b244c209159c3 | Safe |
| AndroidX | 21724 | 3e0cb5efcd52300aec5994fdfc5bdc16 | **RECOMMENDED** |

### Why API ID 4 is Dangerous

1. **Published/leaked** — Telegram knows this ID is widely used by automation tools
2. **`ApiIdPublishedFloodError`** — Telegram blocks new authorization attempts with this ID
3. **Active monitoring** — Sessions using ID 4 get extra scrutiny from anti-fraud
4. **Session revocation** — Telegram may proactively revoke auth keys for sessions with flagged IDs

### API ID Migration Rules

**CRITICAL: Once a session is created with an API ID, it CANNOT be changed.**

The API ID is part of the initial key exchange during authorization. To use a different API ID, you must:
1. Create a new session (requires SMS verification code)
2. Re-authorize the account with the new API ID

For existing accounts with API ID 4:
- **Cannot migrate** the session
- Keep them alive as long as possible
- Use them conservatively (lower limits, more delays)
- Gradually replace with accounts purchased using safe API IDs

For new accounts:
- Use API ID 2040 (Desktop) or 21724 (AndroidX)
- NEVER use API ID 4

## Device Fingerprint Consistency

### What Telegram Sees

When a Telethon client connects, it sends:
- `api_id` — identifies the "app"
- `device_model` — e.g., "Samsung Galaxy S23"
- `system_version` — e.g., "SDK 29" (Android 10)
- `app_version` — e.g., "10.8.3"
- `lang_code` — e.g., "ru"
- `system_lang_code` — e.g., "ru-ru"

### Consistency Rules

**Rule 1: Fingerprint must match API ID family**
- API ID 4/6/21724 (Android) → device_model should be Android device, system_version should be "SDK XX"
- API ID 2040 (Desktop) → device_model should be "Desktop" or OS name, system_version should be OS version
- API ID 10840 (iOS) → device_model should be iPhone/iPad model

**Rule 2: Fingerprint must be stable per account**
- NEVER randomize device_model/system_version between restarts
- Store fingerprint in account JSON and always reuse
- Changing fingerprint = Telegram sees a "new device" = suspicious

**Rule 3: Fingerprint should be realistic**
- `device_model: "Samsung Galaxy S23"` + `system_version: "SDK 29"` is plausible
- `device_model: "iPhone 15"` + `system_version: "SDK 29"` is NOT plausible
- `device_model: "Desktop"` + `api_id: 4` is NOT plausible

### opentele Library

The `opentele` library (GitHub: AXE-Me/opentele) provides:
- Pre-built fingerprint templates matching official Telegram apps
- `TDesktop` class — generates Desktop-consistent fingerprints
- `TAndroid` class — generates Android-consistent fingerprints
- Session conversion between Telethon, Pyrogram, and tdata formats

**Recommended for new accounts:**
```python
from opentele.td import TDesktop
from opentele.api import UseCurrentSession

# Generate consistent Desktop fingerprint
api = TDesktop.TelegramDesktop(api_id=2040)
# api.device_model = "Desktop"
# api.system_version = "Windows 10"
# api.app_version = "4.8.1 x64"
```

### Current Project State

All accounts in `data/sessions/*.json` use:
- `app_id: 4` (FLAGGED)
- Various budget phone models (Lava X3, Danew Dslide714, Sony Xperia E4)
- `sdk: "SDK 29"` (Android 10)
- `app_version: "12.4.3 (65272)"`

These are from a bulk account supplier. The fingerprints are internally consistent (Android device + Android SDK + Android app version) but the API ID 4 association makes them risky.

### Migration Path for New Accounts

When purchasing new accounts, request or configure:
1. `api_id: 2040` (Desktop)
2. `api_hash: b18441a1ff607e10a989891a5462e627`
3. `device_model: "Desktop"` or specific OS
4. `system_version: "Windows 10"` or `"macOS 14.0"`
5. `app_version: "4.16.8 x64"` (current Telegram Desktop version)
6. `lang_code: "ru"`, `system_lang_code: "ru"`

Or use opentele to generate consistent fingerprints automatically.
