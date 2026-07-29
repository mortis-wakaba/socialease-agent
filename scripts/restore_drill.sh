#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/restore_drill.sh <backup-file>" >&2
  exit 1
fi

BACKUP_FILE="$1"
TARGET_URL="${SOCIALEASE_RESTORE_TEST_DATABASE_URL:-}"
PG_TARGET_URL="${TARGET_URL/postgresql+psycopg:/postgresql:}"

if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

if [[ -z "$TARGET_URL" ]]; then
  echo "SOCIALEASE_RESTORE_TEST_DATABASE_URL is required for PostgreSQL restore drills." >&2
  exit 1
fi

if [[ "$TARGET_URL" != postgresql* && "$TARGET_URL" != postgres* ]]; then
  echo "SOCIALEASE_RESTORE_TEST_DATABASE_URL must be a PostgreSQL URL." >&2
  exit 1
fi

if ! command -v pg_restore >/dev/null 2>&1; then
  echo "pg_restore is required for PostgreSQL restore drills." >&2
  exit 1
fi

echo "Running PostgreSQL restore drill into SOCIALEASE_RESTORE_TEST_DATABASE_URL."
pg_restore "$BACKUP_FILE" --dbname="$PG_TARGET_URL" --clean --if-exists --no-owner
echo "PostgreSQL restore drill completed."
