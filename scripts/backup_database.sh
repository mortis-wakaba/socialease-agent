#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${SOCIALEASE_BACKUP_DIR:-./backups}"
DATABASE_URL="${SOCIALEASE_DATABASE_URL:-}"
SQLITE_PATH="${SOCIALEASE_DB_PATH:-backend/socialease.db}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_DIR"

if [[ "$DATABASE_URL" == postgresql* || "$DATABASE_URL" == postgres* ]]; then
  if ! command -v pg_dump >/dev/null 2>&1; then
    echo "pg_dump is required for PostgreSQL backups." >&2
    exit 1
  fi
  OUTPUT="$BACKUP_DIR/socialease-postgres-$TIMESTAMP.dump"
  pg_dump "$DATABASE_URL" --format=custom --no-owner --file="$OUTPUT"
  echo "PostgreSQL backup written to $OUTPUT"
  exit 0
fi

if [[ "$DATABASE_URL" == sqlite:///* ]]; then
  SQLITE_PATH="${DATABASE_URL#sqlite:///}"
fi

if [[ ! -f "$SQLITE_PATH" ]]; then
  echo "SQLite database not found at $SQLITE_PATH" >&2
  exit 1
fi

OUTPUT="$BACKUP_DIR/socialease-sqlite-$TIMESTAMP.db"
cp "$SQLITE_PATH" "$OUTPUT"
echo "SQLite backup written to $OUTPUT"
