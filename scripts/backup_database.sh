#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${SOCIALEASE_BACKUP_DIR:-./backups}"
DATABASE_URL="${SOCIALEASE_DATABASE_URL:-}"
PG_DATABASE_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_DIR"

if [[ "$DATABASE_URL" != postgresql* && "$DATABASE_URL" != postgres* ]]; then
  echo "SOCIALEASE_DATABASE_URL must be a PostgreSQL URL." >&2
  exit 1
fi

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "pg_dump is required for PostgreSQL backups." >&2
  exit 1
fi

OUTPUT="$BACKUP_DIR/socialease-postgres-$TIMESTAMP.dump"
pg_dump "$PG_DATABASE_URL" --format=custom --no-owner --file="$OUTPUT"
echo "PostgreSQL backup written to $OUTPUT"
