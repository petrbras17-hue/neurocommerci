# Telegram Session Management

## Session Architecture

### What is a Session?

A Telegram session consists of:
1. **Auth key** — 256-byte key negotiated during authorization (DH key exchange)
2. **Server salt** — rotated periodically by Telegram servers
3. **Session ID** — random 64-bit number, changes on reconnect
4. **DC ID** — which Telegram datacenter the account is assigned to

The `.session` file (SQLite database) stores the auth key + DC info. This is the critical secret — whoever has the auth key can act as the account.

### Session Lifecycle

```
Registration/Login
    ↓
Auth Key Created (stored in .session file)
    ↓
Active Session (connected, sending/receiving)
    ↓ (disconnect)
Dormant Session (auth key valid, not connected)
    ↓ (180/365 days inactivity OR server-side revocation)
Dead Session (auth key no longer recognized)
```

### Session Expiry Rules

| Platform | Inactivity Timeout |
|----------|-------------------|
| Mobile (Android/iOS) | 180 days |
| Desktop | 365 days |
| Web | 180 days |

**Important:** "Inactivity" means no API calls at all. A single `client.get_me()` call resets the timer.

### Server-Side Revocation

Telegram can revoke auth keys for:
- Spam/abuse detection → `AuthKeyUnregisteredError`
- User-initiated "terminate all sessions" → `AuthKeyUnregisteredError`
- Phone number ban → `UserDeactivatedBanError`
- Account deletion → `UserDeactivatedError`
- Suspicious API ID usage → `ApiIdPublishedFloodError` (prevents new auth, may trigger existing session revocation)

**Once revoked, the auth key is permanently invalid.** The `.session` file cannot be "fixed" — a new auth key requires SMS verification.

## Health Checking

### Recommended Health Check Strategy

```python
# Cheapest possible check — 1 API call, no flood risk
try:
    me = await client.get_me()
    # Session is alive
except AuthKeyUnregisteredError:
    # Session terminated server-side — CANNOT recover without re-auth
except UserDeactivatedBanError:
    # Phone number banned
except UserDeactivatedError:
    # Account deleted
except ConnectionError:
    # Network issue — retry later
```

**Check frequency:** Every 4-8 hours for connected accounts. More frequent = unnecessary API load. Less frequent = delayed detection.

**Health check cost:** ~1 API call. Safe to call frequently (no flood risk for `get_me()`).

### Keep-Alive Strategy

To prevent session expiry from inactivity:
1. Call `client.get_me()` every 4-8 hours (cheapest)
2. Read recent messages from subscribed channels every 8-16 hours
3. Update online status periodically
4. React to posts every 24-48 hours

**Timing:** Use Gaussian distribution, not fixed intervals. Per-account sleep windows (23:00-07:00 local time).

## StringSession Backup

### What is StringSession?

A base64-encoded export of the auth key + DC info. Can be used to reconstruct a session without the `.session` file.

```python
from telethon.sessions import StringSession

# Export
string = StringSession.save(client.session)

# Import (create client from backup)
client = TelegramClient(StringSession(string), api_id, api_hash)
```

### Backup Strategy

1. **On connect**: After successful `get_me()`, export StringSession
2. **Daily**: Re-export all connected accounts
3. **Storage**: Encrypt with Fernet (AES-128-CBC), store in `data/session_backups/`
4. **Retention**: Keep last 3 backups per account

### Limitations

- If Telegram revoked the auth key server-side, the backup is ALSO useless
- Backup protects against: file corruption, accidental deletion, server migration, disk failure
- Backup does NOT protect against: Telegram bans, spam restrictions, auth key revocation

## Multi-Device Considerations

- Each session is a separate "device" in Telegram's view
- Multiple sessions for the same account can coexist
- Telegram shows all active sessions in Settings → Devices
- Too many simultaneous sessions for one account = suspicious
- Recommended: 1 session per account for automation

## Common Errors

| Error | Meaning | Recovery |
|-------|---------|----------|
| `AuthKeyUnregisteredError` | Auth key revoked server-side | Cannot recover — needs new SMS auth |
| `UserDeactivatedBanError` | Phone number banned | Appeal via @SpamBot or email |
| `UserDeactivatedError` | Account deleted | Cannot recover |
| `SessionPasswordNeededError` | 2FA enabled, password required | Provide password from account metadata |
| `PhoneNumberBannedError` | Phone cannot be used | Need new phone number |
| `FloodWaitError` | Too many requests | Wait specified seconds × 1.5 |
| `ApiIdPublishedFloodError` | API ID flagged/leaked | Cannot use this API ID for new auth |
| `SessionRevokedError` | Session explicitly terminated | Re-auth required |
