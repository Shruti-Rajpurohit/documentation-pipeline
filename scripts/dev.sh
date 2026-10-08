#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"

PYTHON=${PYTHON:-python3}
API_PORT=${API_PORT:-8000}
FRONTEND_PORT=${FRONTEND_PORT:-3000}

mkdir -p "$ROOT_DIR/data"
export DATABASE_URL=${DATABASE_URL:-"sqlite+aiosqlite:///$ROOT_DIR/data/documentation_pipeline.db"}
export AUTH_PROXY_SECRET=${AUTH_PROXY_SECRET:-"$(openssl rand -hex 32)"}
export REPOSITORY_ROOTS=${REPOSITORY_ROOTS:-"$ROOT_DIR"}
export REVIEW_USER_ID=${REVIEW_USER_ID:-"$("$PYTHON" -m app.db.provision_user --ensure documentation-dev-writer documentation-dev-writer@example.test WRITER)"}
export FASTAPI_BASE_URL=${FASTAPI_BASE_URL:-"http://127.0.0.1:$API_PORT"}

"$PYTHON" -m app.main --host 127.0.0.1 --port "$API_PORT" &
API_PID=$!

cleanup() {
	kill "$API_PID" 2>/dev/null || true
	wait "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

printf 'FastAPI: http://127.0.0.1:%s\n' "$API_PORT"
printf 'Next.js: http://127.0.0.1:%s\n' "$FRONTEND_PORT"
printf 'Local review identity: user ID %s (WRITER)\n' "$REVIEW_USER_ID"

cd "$ROOT_DIR/frontend"
npm run dev -- --hostname 127.0.0.1 --port "$FRONTEND_PORT"