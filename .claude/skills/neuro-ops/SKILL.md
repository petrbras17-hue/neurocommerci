---
name: neuro-ops
description: NEURO COMMENTING platform operations — deploy, monitor, troubleshoot accounts, manage farms, check health, query database, view logs, restart services. Use when the user asks about VPS status, account health, farm monitoring, deployment, warmup status, proxy health, billing status, or any operational task on the production server.
disable-model-invocation: true
allowed-tools: Bash, Read, Grep
---

# NEURO COMMENTING Operations Skill

Comprehensive operations toolkit for the NEURO COMMENTING platform.

## Environment

- **VPS Host**: `176.124.221.253`
- **SSH User**: `deploy`
- **Project Path**: `/opt/neuro-commenting`
- **Public URL**: `https://neurocommenting.com/`
- **App URL**: `https://neurocommenting.com/app`
- **Services**: `db`, `redis`, `ops_api`, `bot`
- **Branch**: `main`
- **DB User**: `nc` (NOSUPERUSER, NOBYPASSRLS)
- **DB Name**: `neuro_commenting`

## Quick Commands

### 1. VPS Health Check

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose ps && echo '---' && curl -sk https://neurocommenting.com/health"
```

### 2. Full Service Status

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose ps -a && echo '--- DISK ---' && df -h / && echo '--- MEMORY ---' && free -h && echo '--- UPTIME ---' && uptime"
```

### 3. Account Status (All Accounts)

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT id, phone, status, lifecycle_stage, health_status, proxy_id, created_at FROM accounts ORDER BY id;\""
```

### 4. Account Health Scores

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT ahs.account_id, a.phone, ahs.health_score, ahs.survivability_score, ahs.risk_factors, ahs.updated_at FROM account_health_scores ahs JOIN accounts a ON ahs.account_id = a.id ORDER BY ahs.health_score ASC;\""
```

### 5. Warmup Status (Active Configs)

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT wc.id, a.phone, wc.mode, wc.current_phase, wc.warmup_day, wc.is_active, wc.next_session_at, wc.active_hours_start, wc.active_hours_end FROM warmup_configs wc JOIN accounts a ON wc.account_id = a.id ORDER BY wc.id;\""
```

### 6. Warmup Sessions (Recent)

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT ws.id, a.phone, ws.session_type, ws.actions_count, ws.started_at, ws.ended_at, ws.error FROM warmup_sessions ws JOIN accounts a ON ws.account_id = a.id ORDER BY ws.started_at DESC LIMIT 20;\""
```

### 7. Proxy Status

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT id, host, port, protocol, status, is_healthy, last_checked_at, rotation_strategy, auto_rotation FROM proxies ORDER BY id;\""
```

### 8. Farm Status

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT id, name, status, total_threads, active_threads, comments_sent, comments_failed, created_at FROM farms ORDER BY id;\""
```

### 9. Farm Threads

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT ft.id, ft.farm_id, a.phone, ft.status, ft.comments_sent, ft.comments_failed, ft.last_comment_at, ft.error FROM farm_threads ft JOIN accounts a ON ft.account_id = a.id ORDER BY ft.farm_id, ft.id;\""
```

### 10. Channel Map Stats

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SELECT COUNT(*) as total_channels, COUNT(DISTINCT category) as categories, AVG(subscribers_count) as avg_subscribers, MAX(subscribers_count) as max_subscribers FROM channel_map_entries;\""
```

### 11. Billing Status

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT s.id, s.plan_id, p.name as plan_name, s.status, s.trial_ends_at, s.current_period_start, s.current_period_end FROM subscriptions s JOIN plans p ON s.plan_id = p.id ORDER BY s.id;\""
```

### 12. Recent Payments

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT id, amount, currency, provider, status, created_at FROM billing_payments ORDER BY created_at DESC LIMIT 20;\""
```

### 13. AI Request Log

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT id, task_type, model_tier, provider, model, status, tokens_in, tokens_out, cost_usd, created_at FROM ai_requests ORDER BY created_at DESC LIMIT 20;\""
```

### 14. Job Queue Status

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT job_type, status, COUNT(*) as count FROM app_jobs GROUP BY job_type, status ORDER BY job_type, status;\""
```

### 15. Auth Users

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SELECT id, email, telegram_id, display_name, tenant_id, created_at FROM auth_users ORDER BY id;\""
```

## Deployment

### Standard Deploy (Pull + Migrate + Restart)

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && git pull origin main && docker compose exec -T ops_api alembic upgrade head && docker compose restart ops_api && echo '--- HEALTH ---' && sleep 3 && curl -sk https://neurocommenting.com/health"
```

### Frontend Rebuild + Deploy

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting/frontend && npm install && npm run build && cd .. && docker compose restart ops_api"
```

### Full Restart (All Services)

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose down && docker compose up -d db redis && sleep 5 && docker compose up -d ops_api bot && sleep 3 && docker compose ps && curl -sk https://neurocommenting.com/health"
```

### Rollback to Specific Commit

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && git log --oneline -5 && echo '--- Current HEAD ---' && git rev-parse HEAD"
# Then manually: git checkout <commit> && docker compose restart ops_api
```

## Logs

### API Logs (Recent)

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose logs --tail=100 ops_api"
```

### API Errors Only

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose logs --tail=500 ops_api 2>&1 | grep -i 'error\|traceback\|exception'"
```

### Bot Logs

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose logs --tail=100 bot"
```

### Database Logs

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose logs --tail=50 db"
```

### Redis Logs

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose logs --tail=50 redis"
```

### Follow Logs Live

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose logs -f --tail=20 ops_api"
```

## Database Administration

### Check Alembic Migration Status

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T ops_api alembic current && echo '---' && docker compose exec -T ops_api alembic history --last 5"
```

### List All Tables

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"\\dt\""
```

### Check RLS Policies

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SELECT tablename, policyname, cmd, qual FROM pg_policies ORDER BY tablename;\""
```

### Check Table Row Counts

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SELECT schemaname, relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 30;\""
```

### Check DB Size

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SELECT pg_size_pretty(pg_database_size('neuro_commenting')) as db_size;\""
```

### Vacuum Analyze

```bash
ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"VACUUM ANALYZE;\""
```

## Troubleshooting

### Problem: Health endpoint returns error

1. Check if ops_api container is running:
   ```bash
   ssh deploy@176.124.221.253 "docker compose -f /opt/neuro-commenting/docker-compose.yml ps ops_api"
   ```
2. Check recent logs for crashes:
   ```bash
   ssh deploy@176.124.221.253 "docker compose -f /opt/neuro-commenting/docker-compose.yml logs --tail=50 ops_api"
   ```
3. Restart ops_api:
   ```bash
   ssh deploy@176.124.221.253 "docker compose -f /opt/neuro-commenting/docker-compose.yml restart ops_api"
   ```

### Problem: Warmup not executing sessions

1. Check WarmupScheduler is running (health endpoint should show `warmup_scheduler: running`).
2. Check if active warmup configs exist and `next_session_at` is in the past.
3. Check warmup_sessions for recent errors.
4. Check active_hours — sessions only run within the configured window.

### Problem: Account stuck in quarantine

1. Check quarantine reason:
   ```bash
   ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT id, phone, status, quarantine_reason, quarantine_until FROM accounts WHERE status = 'quarantined';\""
   ```
2. Lift quarantine via API:
   ```
   POST /v1/quarantine/{account_id}/lift
   ```

### Problem: Farm threads failing

1. Check thread errors:
   ```bash
   ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT ft.id, a.phone, ft.status, ft.error, ft.last_comment_at FROM farm_threads ft JOIN accounts a ON ft.account_id = a.id WHERE ft.error IS NOT NULL ORDER BY ft.id;\""
   ```
2. Common causes: FloodWait (too many requests), frozen session, bad proxy, channel banned.

### Problem: Proxy not working

1. Check proxy health:
   ```bash
   ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SET app.tenant_id = '1'; SELECT id, host, port, protocol, status, is_healthy, last_checked_at, error FROM proxies WHERE is_healthy = false;\""
   ```
2. Trigger health check via API:
   ```
   POST /v1/proxies/{id}/check
   ```

### Problem: Database connection issues

1. Check if DB container is healthy:
   ```bash
   ssh deploy@176.124.221.253 "docker compose -f /opt/neuro-commenting/docker-compose.yml ps db"
   ```
2. Check connection count:
   ```bash
   ssh deploy@176.124.221.253 "cd /opt/neuro-commenting && docker compose exec -T db psql -U nc -d neuro_commenting -c \"SELECT count(*) FROM pg_stat_activity;\""
   ```

### Problem: Redis connection issues

1. Check Redis container:
   ```bash
   ssh deploy@176.124.221.253 "docker compose -f /opt/neuro-commenting/docker-compose.yml ps redis"
   ```
2. Check Redis info:
   ```bash
   ssh deploy@176.124.221.253 "docker compose -f /opt/neuro-commenting/docker-compose.yml exec -T redis redis-cli info server | head -20"
   ```

## API Smoke Tests

### Quick Smoke (Core Endpoints)

```bash
VPS="https://neurocommenting.com"
echo "=== Health ===" && curl -sk "$VPS/health" | python3 -m json.tool
echo "=== Landing ===" && curl -sk -o /dev/null -w "%{http_code}" "$VPS/"
echo "=== App ===" && curl -sk -o /dev/null -w "%{http_code}" "$VPS/app"
echo "=== Auth Me ===" && curl -sk -o /dev/null -w "%{http_code}" "$VPS/auth/me"
echo "=== Channel Map ===" && curl -sk -o /dev/null -w "%{http_code}" "$VPS/v1/channel-map?limit=5"
```

### Authenticated Smoke (Requires Token)

```bash
# First get a token via /auth/login, then:
TOKEN="<your-jwt>"
VPS="https://neurocommenting.com"
curl -sk -H "Authorization: Bearer $TOKEN" "$VPS/auth/me" | python3 -m json.tool
curl -sk -H "Authorization: Bearer $TOKEN" "$VPS/v1/web/accounts" | python3 -m json.tool
curl -sk -H "Authorization: Bearer $TOKEN" "$VPS/v1/warmup/configs" | python3 -m json.tool
curl -sk -H "Authorization: Bearer $TOKEN" "$VPS/v1/farm" | python3 -m json.tool
```

## Monitoring Checklist (Daily)

1. Run VPS Health Check — confirm all 4 services are `running`.
2. Check warmup status — confirm active configs have `next_session_at` in the future.
3. Check recent warmup sessions — confirm no persistent errors.
4. Check account health scores — flag any score below 50.
5. Check API error logs — look for repeating errors.
6. Check disk space — flag if usage exceeds 80%.
7. Check DB size — flag if growth is abnormal.
