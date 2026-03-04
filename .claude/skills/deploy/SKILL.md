---
name: deploy
description: Deploy NEURO COMMENTING to Railway for 24/7 operation
disable-model-invocation: true
allowed-tools: Bash, Read, Grep, Glob
---

# Deploy to Railway

Deploy the NEURO COMMENTING bot to Railway.

## Pre-deploy checklist

1. Verify all Python files compile:
```bash
find . -name "*.py" -not -path "./venv/*" | xargs -I {} python -m py_compile {}
```

2. Check required env vars are set:
```bash
echo "Checking .env..."
grep -c "TELEGRAM_API_ID\|TELEGRAM_API_HASH\|ADMIN_BOT_TOKEN\|GEMINI_API_KEY" .env
```

3. Check Dockerfile exists and is valid:
```bash
cat Dockerfile
cat railway.json
```

## Deploy steps

1. Login to Railway: `railway login`
2. Link/init project: `railway link` or `railway init`
3. Set environment variables via Railway Dashboard
4. Deploy: `railway up`
5. Check logs: `railway logs`

## Post-deploy verification

1. Check bot responds in Telegram
2. Verify `/status` command works
3. Check Railway Dashboard for resource usage

## Environment variables for Railway

These must be set in Railway Dashboard (Settings > Variables):
- TELEGRAM_API_ID
- TELEGRAM_API_HASH
- ADMIN_BOT_TOKEN
- ADMIN_TELEGRAM_ID
- GEMINI_API_KEY
- ANTHROPIC_API_KEY (optional, for Claude orchestrator)
- PROXY_DATA (format: host:port:user:pass;host2:port2:user2:pass2)
- SESSIONS_DATA (base64 tar.gz of sessions)

## Volume

Railway persistent volume must be mounted at `/app/data` for sessions and DB.
