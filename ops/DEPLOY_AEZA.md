# AEZA Deployment Runbook (NEURO COMMENTING)

This runbook executes the migration plan to Aeza VPS with minimal risk and clear rollback.

## 0. Assumptions

- VPS: Aeza Shared 2 vCPU
- OS: Ubuntu 24.04 LTS
- Runtime: Docker Compose (5 services)
- Cutover mode: parallel validation first, then switch production
- API policy: keep `TELEGRAM_API_ID=4` temporarily for current sessions

## 1. Local freeze checkpoint

```bash
cd "/Users/braslavskii/NEURO COMMENTING"
git checkout migration/aeza-prod
git tag -f pre-aeza-migration-20260305
```

Optional inventory/export bundle:

```bash
python3 scripts/export_local_state.py --include-raw
```

## 2. Prepare server access (one-time)

### 2.1 Create deploy user and secure SSH

Run on server as root (console panel):

```bash
apt update && apt -y upgrade
apt -y install ufw fail2ban ca-certificates curl gnupg

adduser deploy
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys || true
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
```

Edit `/etc/ssh/sshd_config`:

- `PasswordAuthentication no`
- `PermitRootLogin prohibit-password`

Then:

```bash
systemctl restart ssh
ufw allow 22/tcp
ufw --force enable
systemctl enable fail2ban --now
```

## 3. Install Docker Engine + Compose plugin

Run as `deploy` with sudo:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
newgrp docker

docker --version
docker compose version
```

## 4. Deploy project to `/opt/neuro-commenting`

```bash
sudo mkdir -p /opt/neuro-commenting
sudo chown -R deploy:deploy /opt/neuro-commenting
cd /opt/neuro-commenting
git clone <YOUR_REPO_URL> .
git checkout migration/aeza-prod
```

## 5. Server `.env` creation

```bash
cp .env.example .env
chmod 600 .env
```

Set required values in `.env`:

- Telegram/Gemini/Anthropic keys
- `PRODUCT_*`
- `DATABASE_URL=postgresql+asyncpg://nc:<DB_PASSWORD>@db:5432/neurocomment`
- `REDIS_URL=redis://redis:6379/0`
- `DISTRIBUTED_QUEUE_MODE=true`
- `MAX_ACCOUNTS_PER_WORKER=20` (canary stage-specific)
- `MAX_CONNECTED_CLIENTS_PER_WORKER=50`
- `WORKER_CONNECT_BATCH_SIZE=5`
- `WORKER_DEQUEUE_TIMEOUT_SEC=5`
- `STRICT_PROXY_PER_ACCOUNT=true`
- `PINNED_PHONE_REQUIRED=true`
- `PACKAGING_DELAY_SCALE=0.65`
- `PACKAGING_ALLOW_BIO_FALLBACK=false`
- `ENABLE_LEGACY_COMMENTING=false`
- `ENABLE_EMOJI_SWAP=false`
- `COMPLIANCE_MODE=strict`
- `POLICY_RULES_PATH=policy/rules.yaml`
- `NEW_ACCOUNT_LAUNCH_MODE=faster_1d`
- `STRICT_PARSER_ONLY=true`
- `FROZEN_PROBE_ON_CONNECT=true`
- `FROZEN_PROBE_BEFORE_PACKAGING=true`
- `FROZEN_PROBE_BEFORE_PARSER=true`
- `MANUAL_GATE_REQUIRED=true`
- `ENABLE_CLIENT_WIZARD=true`
- `ENABLE_ADMIN_LEGACY_TOOLS=true`
- `STRICT_SLO_WINDOW_DAYS=30`
- `PARSER_ONLY_PHONE=+79XXXXXXXXX`
- `WORKER_A_PINNED_PHONE=+79637411890`
- `WORKER_B_PINNED_PHONE=+79637421804`

## 6. Transfer data payload

From local machine:

```bash
# sessions/proxies/backups/product posts
scp -r "data/sessions" deploy@<SERVER_IP>:/opt/neuro-commenting/data/
scp "data/proxies.txt" deploy@<SERVER_IP>:/opt/neuro-commenting/data/
scp -r "data/session_backups" deploy@<SERVER_IP>:/opt/neuro-commenting/data/
scp "data/product_posts.json" deploy@<SERVER_IP>:/opt/neuro-commenting/data/
```

Optional export bundle transfer:

```bash
scp -r artifacts/local_export_* deploy@<SERVER_IP>:/opt/neuro-commenting/artifacts/
```

## 7. Start stack in distributed safe mode

```bash
cd /opt/neuro-commenting
docker compose pull || true
docker compose build --pull
docker compose up -d
```

Check health:

```bash
docker compose ps
docker compose logs --tail=200 db redis bot worker_a worker_b packager
python3 scripts/preflight_runtime_env.py --json
```

Pinned workers mode (recommended for anti-fraud-safe rollout):

```bash
docker compose up -d bot packager worker_a worker_b
```

Single-account packaging enqueue (manual gate):

```bash
python3 scripts/enqueue_packaging_phone.py --phone +79637411890
```

Gate check command (before moving to next stage):

```bash
python3 scripts/runtime_status.py
```

Note: avoid posting `docker compose config` output in shared logs/chats, because it can expose secrets.

Parser diagnostics:

```bash
python3 scripts/parser_diagnose.py --keywords "vpn, впн" --json
```

## 8. Import local DB snapshot into PostgreSQL (if needed)

```bash
cd /opt/neuro-commenting
python3 scripts/import_to_postgres.py \
  --export-dir artifacts/local_export_<TIMESTAMP> \
  --pg-dsn "postgresql://nc:<DB_PASSWORD>@localhost:5432/neurocomment"
```

## 9. Smoke validation checklist

- Bot responds to `/start`
- Accounts list opens
- `Подключить все` works without mass failures
- Packaging test on 1 account works
- Wizard flow works: `profile -> channel -> content -> warmup -> gate_review`
- Gate approve transitions account to `active_commenting`
- Channel parser and test comment generation work
- `docker compose restart` keeps state and recovers cleanly

## 10. Cutover

1. Stop local production process.
2. Keep only server bot active.
3. Re-run smoke checks.
4. Watch logs and stability for 24h.

## 11. Backups (server)

Create backup root and schedule nightly run:

```bash
sudo mkdir -p /opt/neuro-backups
sudo chown -R deploy:deploy /opt/neuro-backups
```

Manual backup:

```bash
cd /opt/neuro-commenting
PROJECT_DIR=/opt/neuro-commenting BACKUP_ROOT=/opt/neuro-backups ./scripts/backup_server.sh
```

Daily cron (01:40 UTC):

```bash
( crontab -l 2>/dev/null; echo "40 1 * * * PROJECT_DIR=/opt/neuro-commenting BACKUP_ROOT=/opt/neuro-backups /opt/neuro-commenting/scripts/backup_server.sh >/opt/neuro-backups/cron.log 2>&1" ) | crontab -
```

Export backups to local PC:

```bash
scp -r deploy@<SERVER_IP>:/opt/neuro-backups/latest ./aeza-backup-latest
```

## 12. Docker log rotation (host-level)

Configure `/etc/docker/daemon.json`:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "5"
  }
}
```

Apply in maintenance window:

```bash
sudo systemctl restart docker
cd /opt/neuro-commenting
docker compose up -d
```

## 13. Rollback

```bash
# on server
cd /opt/neuro-commenting
docker compose down
```

Then restart local production bot from the pre-migration setup.

If needed, restore from local export bundle + `pre-aeza-migration-20260305` tag.

## 14. Canary rollout gates (A-H)

Use stages: `1 -> 5 -> 10 -> 20 -> 35 -> 50 -> 75 -> 100 connected`.
At each stage keep observation window and continue only if all gates pass:

- No crash-loop containers
- Worker heartbeat fresh
- DB/Redis healthy
- No mass flood-wait escalation
- CPU < 85% avg
- RAM < 85% avg

Quick rollback to previous stage:

```bash
cd /opt/neuro-commenting
# example rollback from stage E to stage D
export MAX_ACCOUNTS_PER_WORKER=20
docker compose up -d --scale worker=2
python3 scripts/runtime_status.py
```
