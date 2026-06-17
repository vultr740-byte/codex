#!/usr/bin/env bash
set -euo pipefail

: "${WEIXIN_BASE_URL:?WEIXIN_BASE_URL must be set when CODEX_ENABLED_CHANNEL=weixin}"
: "${CODEX_WS_TOKEN:?CODEX_WS_TOKEN must be set}"

CODEX_APP_SERVER_PORT="${CODEX_APP_SERVER_PORT:-8787}"
CODEX_APP_SERVER_LISTEN="${CODEX_APP_SERVER_LISTEN:-ws://127.0.0.1:${CODEX_APP_SERVER_PORT}}"
CODEX_APP_SERVER_URL="${CODEX_APP_SERVER_URL:-ws://127.0.0.1:${CODEX_APP_SERVER_PORT}}"
CODEX_APP_SERVER_TOKEN="${CODEX_APP_SERVER_TOKEN:-${CODEX_WS_TOKEN}}"
CODEX_WEIXIN_STATE_DIR="${CODEX_WEIXIN_STATE_DIR:-/data/weixin}"
CODEX_THREAD_MODE="${CODEX_THREAD_MODE:-${CODEX_WEIXIN_THREAD_MODE:-per_user}}"
CODEX_DEFAULT_CWD="${CODEX_DEFAULT_CWD:-${CODEX_WEIXIN_DEFAULT_CWD:-${WORKSPACE_DIR:-/data/workspaces/default}}}"
CONTROL_API_TOKEN="${CONTROL_API_TOKEN:-${CODEX_WEIXIN_BRIDGE_CONTROL_API_TOKEN:-}}"

export CODEX_APP_SERVER_LISTEN
export CODEX_APP_SERVER_URL
export CODEX_APP_SERVER_TOKEN
export CODEX_WEIXIN_STATE_DIR
export CODEX_THREAD_MODE
export CODEX_DEFAULT_CWD
export CONTROL_API_TOKEN

app_pid=''
bridge_pid=''

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$bridge_pid" ]]; then
    kill "$bridge_pid" >/dev/null 2>&1 || true
  fi
  if [[ -n "$app_pid" ]]; then
    kill "$app_pid" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

/usr/local/bin/app-server-run &
app_pid="$!"

for _ in $(seq 1 "${CODEX_APP_SERVER_READY_ATTEMPTS:-60}"); do
  if curl -fsS "http://127.0.0.1:${CODEX_APP_SERVER_PORT}/readyz" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$app_pid" >/dev/null 2>&1; then
    wait "$app_pid"
  fi
  sleep 1
done

if ! curl -fsS "http://127.0.0.1:${CODEX_APP_SERVER_PORT}/readyz" >/dev/null 2>&1; then
  echo "Codex app-server did not become ready on port ${CODEX_APP_SERVER_PORT}" >&2
  exit 1
fi

cd /app/codex-weixin-bridge
node dist/index.js &
bridge_pid="$!"

wait -n "$app_pid" "$bridge_pid"
