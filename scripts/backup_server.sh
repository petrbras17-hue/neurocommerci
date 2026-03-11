#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/neuro-commenting}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/neuro-backups}"
DB_SERVICE="${DB_SERVICE:-db}"
DB_NAME="${DB_NAME:-neurocomment}"
DB_USER="${DB_USER:-nc}"

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "ERROR: PROJECT_DIR not found: $PROJECT_DIR" >&2
  exit 1
fi

TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
TARGET_DIR="$BACKUP_ROOT/$TIMESTAMP"
mkdir -p "$TARGET_DIR"

cd "$PROJECT_DIR"

echo "[1/4] PostgreSQL dump..."
docker compose exec -T "$DB_SERVICE" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$TARGET_DIR/postgres_${DB_NAME}.sql.gz"

echo "[2/4] Sessions archive..."
if [[ -d data/sessions ]]; then
  tar -czf "$TARGET_DIR/sessions.tar.gz" data/sessions
else
  echo "WARN: data/sessions not found, skipping"
fi

echo "[3/4] Session backups archive..."
if [[ -d data/session_backups ]]; then
  tar -czf "$TARGET_DIR/session_backups.tar.gz" data/session_backups
else
  echo "WARN: data/session_backups not found, skipping"
fi

echo "[4/4] Config payload..."
if [[ -f data/proxies.txt ]]; then
  cp data/proxies.txt "$TARGET_DIR/proxies.txt"
fi
if [[ -f data/product_posts.json ]]; then
  cp data/product_posts.json "$TARGET_DIR/product_posts.json"
fi

cat > "$TARGET_DIR/manifest.txt" <<MANIFEST
created_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
project_dir=$PROJECT_DIR
db_service=$DB_SERVICE
db_name=$DB_NAME
db_user=$DB_USER
MANIFEST

ln -sfn "$TARGET_DIR" "$BACKUP_ROOT/latest"

echo "Backup complete: $TARGET_DIR"
