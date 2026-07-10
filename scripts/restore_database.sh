#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/restore_database.sh <backup-file>" >&2
  exit 1
fi

BACKUP_FILE="$1"
DATABASE_URL="${SOCIALEASE_DATABASE_URL:-}"
SQLITE_PATH="${SOCIALEASE_DB_PATH:-backend/socialease.db}"

if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

if [[ "$DATABASE_URL" == postgresql* || "$DATABASE_URL" == postgres* ]]; then
  if ! command -v pg_restore >/dev/null 2>&1; then
    echo "pg_restore is required for PostgreSQL restores." >&2
    exit 1
  fi
  echo "Restoring PostgreSQL backup into configured SOCIALEASE_DATABASE_URL."
  pg_restore "$BACKUP_FILE" --dbname="$DATABASE_URL" --clean --if-exists --no-owner
  echo "PostgreSQL restore completed."
  exit 0
fi

if [[ "$DATABASE_URL" == sqlite:///* ]]; then
  SQLITE_PATH="${DATABASE_URL#sqlite:///}"
fi

mkdir -p "$(dirname "$SQLITE_PATH")"
cp "$BACKUP_FILE" "$SQLITE_PATH"
echo "SQLite restore completed at $SQLITE_PATH"
