# Proxy and Geo-Consistency

## The Golden Rule: 1 IP = 1 Account

**NEVER share a proxy/IP between multiple Telegram accounts.**

Telegram correlates accounts by:
- Source IP address
- IP subnet (accounts from same /24 subnet get extra scrutiny)
- TLS fingerprint (some proxy types leak this)
- Connection timing patterns from same IP

If two accounts share an IP, Telegram can link them. If one gets banned, the other may follow.

## Proxy Types

### Residential Proxies (BEST)
- Real ISP-assigned IPs from actual homes
- Lowest detection rate
- More expensive ($5-15/GB)
- Providers: Bright Data, SmartProxy, Oxylabs

### Mobile Proxies (BEST)
- IPs from mobile carriers (4G/5G)
- Telegram trusts mobile IPs (most real users are on mobile)
- Shared IP is normal for mobile (carrier-grade NAT)
- Most expensive but safest
- Providers: various, often regional

### ISP Proxies (GOOD)
- Static residential IPs
- Assigned by real ISPs but hosted in datacenters
- Good balance of speed and safety
- Providers: Bright Data (ISP product), IPRoyal

### Datacenter Proxies (RISKY)
- IPs from hosting providers (AWS, GCP, OVH, etc.)
- Telegram flags known datacenter IP ranges
- Cheapest but highest detection rate
- AVOID for Telegram automation

## Proxy Configuration for Telethon

```python
# SOCKS5 (recommended)
proxy = (2, 'host', port, True, 'username', 'password')
# Type: 2 = SOCKS5

# HTTP (also works)
proxy = (3, 'host', port, True, 'username', 'password')
# Type: 3 = HTTP

# SOCKS4
proxy = (1, 'host', port)
# Type: 1 = SOCKS4
```

**Note:** The project currently uses type 3 (HTTP) for Proxyverse proxies.

## Geo-Consistency Rules

### Why It Matters

Telegram tracks the geographic location of account activity. Sudden location changes trigger fraud alerts.

### Geo-Jump Detection Thresholds

| Distance | Time | Risk Level |
|----------|------|------------|
| <100 km | Any | Safe (normal movement) |
| 100-500 km | >1 hour | Low risk |
| 100-500 km | <1 hour | Medium risk (amber flag) |
| 500-2000 km | >4 hours | Low risk |
| 500-2000 km | <4 hours | High risk |
| >2000 km | <24 hours | Very high risk (red flag) |
| Cross-continent | <8 hours | Almost certain flag |
| Known Tor/VPN exit | Any | Elevated scrutiny |

### Rules for Account-Proxy Binding

1. **Assign proxy at account creation/import** — never rotate
2. **Same country always** — if account was registered with Russian number, use Russian proxy
3. **Same city ideal** — Moscow account should always appear from Moscow
4. **Never change proxy for a connected account** — if proxy fails, wait for it to recover or pause the account
5. **Fallback proxy must be geo-consistent** — backup proxy should be in the same region

### What Happens on Geo-Jump

1. Telegram may flag the session for review
2. May trigger 2FA verification request
3. May add the session to a "suspicious" list with lower trust
4. Repeated geo-jumps → session termination

## Proxy Assignment Strategy

### Current Implementation (`proxy_manager.py`)

```
Account → Sorted by phone number → Assigned proxy by index
If index > proxy count → index % proxy count (DANGEROUS — reuse!)
```

### Recommended Implementation

```
Account → Persistent binding in DB (proxy_id column on Account)
On connect → Load assigned proxy from DB
If proxy unavailable → REFUSE to connect (don't fallback to shared)
Alert admin if proxy is down → Admin manually assigns new geo-consistent proxy
```

### Proxy Pool Sizing

- **Minimum:** 1 proxy per active account
- **Recommended:** 1.2x active accounts (20% buffer for rotation/failure)
- **Budget:** $3-5/month per residential proxy (static)

## Proxy Monitoring

### Health Checks (Every 30 min)

For each assigned proxy:
1. TCP connectivity test
2. HTTP/SOCKS5 handshake
3. Optional: IP geolocation check (verify still in expected region)

### Metrics to Track

- Proxy uptime per provider
- Average latency per proxy
- IP change detection (for rotating residential proxies — these should NOT be used)
- Accounts paused due to proxy failure

## Common Proxy Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `ConnectionError` | Proxy down | Wait + retry, alert admin |
| `ProxyConnectionError` | Auth failed | Check credentials |
| Slow connection | Proxy overloaded | Switch to different proxy (same region) |
| IP changed | Rotating proxy | Use STATIC proxies only |
| Account banned after proxy change | Geo-jump detected | Never change proxy for active account |
| Multiple accounts banned simultaneously | Shared IP | Enforce 1:1 binding strictly |
