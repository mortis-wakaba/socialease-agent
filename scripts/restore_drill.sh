#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/restore_drill.sh <backup-file>" >&2
  exit 1
fi

BACKUP_FILE="$1"
TARGET_URL="${SOCIALEASE_RESTORE_TEST_DATABASE_URL:-}"

if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

if [[ -z "$TARGET_URL" ]]; then
  if [[ "$BACKUP_FILE" == *.db ]]; then
    TMP_DB="$(mktemp /tmp/socialease-restore-drill-XXXXXX.db)"
    cp "$BACKUP_FILE" "$TMP_DB"
    python - "$TMP_DB" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
with sqlite3.connect(db_path) as connection:
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise SystemExit(f"SQLite integrity_check failed: {result}")
print(f"SQLite restore drill passed for {db_path}")
PY
    rm -f "$TMP_DB"
    exit 0
  fi
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
pg_restore "$BACKUP_FILE" --dbname="$TARGET_URL" --clean --if-exists --no-owner
echo "PostgreSQL restore drill completed."
