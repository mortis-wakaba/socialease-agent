#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${SOCIALEASE_SMOKE_BACKEND_PORT:-18080}"
FRONTEND_PORT="${SOCIALEASE_SMOKE_FRONTEND_PORT:-13000}"
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"
TMP_DIR="$(mktemp -d)"
BACKEND_LOG="${TMP_DIR}/backend.log"
FRONTEND_LOG="${TMP_DIR}/frontend.log"
DB_PATH="${TMP_DIR}/socialease-smoke.db"
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  if [[ -n "${FRONTEND_PID}" ]]; then
    kill "${FRONTEND_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${BACKEND_PID}" ]]; then
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

wait_for_url() {
  local url="$1"
  local label="$2"
  python - "$url" "$label" <<'PY'
import sys
import time
from urllib.request import urlopen

url = sys.argv[1]
label = sys.argv[2]
deadline = time.time() + 60
last_error = ""
while time.time() < deadline:
    try:
        with urlopen(url, timeout=2) as response:
            if response.status < 500:
                print(f"{label} ready: {url}")
                raise SystemExit(0)
    except Exception as exc:
        last_error = repr(exc)
    time.sleep(0.5)
print(f"{label} did not become ready at {url}: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
}

echo "Starting SocialEase backend smoke server on ${BACKEND_URL}"
(
  cd "${ROOT_DIR}/backend"
  SOCIALEASE_DATABASE_URL="sqlite:///${DB_PATH}" \
  SOCIALEASE_AUTH_MODE=production \
  SOCIALEASE_AUTH_TOKEN_SECRET="smoke-test-secret-change-me" \
  SOCIALEASE_ENABLE_SIGNUP=true \
  SOCIALEASE_AUTH_COOKIE_ENABLED=true \
  SOCIALEASE_AUTH_COOKIE_SECURE=false \
  SOCIALEASE_AUTH_RATE_LIMIT_PER_MINUTE=1000 \
  SOCIALEASE_ENFORCE_DIRECT_ACTION_CONSENT=true \
  SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS=true \
  SOCIALEASE_CORS_ORIGINS="${FRONTEND_URL}" \
  LLM_ENABLED=false \
  python -m uvicorn app.main:app --host 127.0.0.1 --port "${BACKEND_PORT}"
) >"${BACKEND_LOG}" 2>&1 &
BACKEND_PID="$!"
wait_for_url "${BACKEND_URL}/health" "backend"

echo "Starting SocialEase frontend smoke server on ${FRONTEND_URL}"
(
  cd "${ROOT_DIR}/frontend"
  NEXT_PUBLIC_API_BASE_URL="${BACKEND_URL}" \
  NEXT_PUBLIC_SOCIALEASE_AUTH_MODE=production \
  NEXT_PUBLIC_SOCIALEASE_ENABLE_SIGNUP=true \
  NEXT_PUBLIC_SOCIALEASE_TOKEN_STORAGE=cookie \
  NEXT_PUBLIC_SOCIALEASE_SHOW_TRACE=false \
  npm run dev -- --hostname 127.0.0.1 --port "${FRONTEND_PORT}"
) >"${FRONTEND_LOG}" 2>&1 &
FRONTEND_PID="$!"
wait_for_url "${FRONTEND_URL}" "frontend"

echo "Running real frontend/backend smoke flow"
(
  cd "${ROOT_DIR}/frontend"
  SOCIALEASE_SMOKE_FRONTEND_URL="${FRONTEND_URL}" npm run test:e2e:smoke
)
