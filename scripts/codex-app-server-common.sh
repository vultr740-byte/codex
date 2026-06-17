#!/usr/bin/env bash
set -euo pipefail

: "${CODEX_WS_TOKEN:?CODEX_WS_TOKEN must be set}"

OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://clawfather.up.railway.app/v1}"
OPENAI_MODEL="${OPENAI_MODEL:-gpt-5.5}"
OPENAI_ACTIVATE_EXTERNAL_ID="${OPENAI_ACTIVATE_EXTERNAL_ID:-codex_railway_${RAILWAY_SERVICE_ID:-api}}"
OPENAI_ACTIVATE_USERNAME="${OPENAI_ACTIVATE_USERNAME:-railway-${RAILWAY_SERVICE_NAME:-api}}"
CODEX_HOME="${CODEX_HOME:-/data/.codex}"
WORKSPACE_DIR="${WORKSPACE_DIR:-/data/workspaces/default}"
RAILWAY_CODEX_SANDBOX_MODE="${RAILWAY_CODEX_SANDBOX_MODE:-danger-full-access}"

sanitize_activation_value() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_' | sed 's/^_*//; s/_*$//'
}

json_escape() {
  jq -Rn --arg value "$1" '$value'
}

activation_endpoint() {
  local normalized_base
  normalized_base="${OPENAI_BASE_URL%/}"

  if [[ "$normalized_base" == */v1 ]]; then
    printf '%s/keys/activate' "$normalized_base"
  else
    printf '%s/v1/keys/activate' "$normalized_base"
  fi
}

activate_api_key() {
  local endpoint request_body curl_output curl_status http_code response api_key

  OPENAI_ACTIVATE_EXTERNAL_ID="$(sanitize_activation_value "$OPENAI_ACTIVATE_EXTERNAL_ID")"
  OPENAI_ACTIVATE_USERNAME="$(sanitize_activation_value "$OPENAI_ACTIVATE_USERNAME")"

  endpoint="$(activation_endpoint)"
  request_body="$(jq -cn \
    --arg external_id "$OPENAI_ACTIVATE_EXTERNAL_ID" \
    --arg username "$OPENAI_ACTIVATE_USERNAME" \
    '{external_id: $external_id, username: $username}')"

  echo "Requesting OpenAI-compatible API key from ${endpoint}" >&2
  if curl_output="$(
    curl -sS \
      -w '\n%{http_code}' \
      -X POST \
      -H 'Content-Type: application/json' \
      -d "$request_body" \
      "$endpoint" \
      2>&1
  )"; then
    curl_status=0
  else
    curl_status=$?
  fi

  http_code="$(printf '%s\n' "$curl_output" | tail -n 1)"
  response="$(printf '%s\n' "$curl_output" | sed '$d')"

  if [[ $curl_status -ne 0 ]]; then
    echo "Failed to activate API key from ${endpoint}; curl exit code ${curl_status}" >&2
    printf '%s\n' "$response" | head -c 500 >&2
    echo >&2
    exit 1
  fi

  if [[ ! "$http_code" =~ ^2[0-9][0-9]$ ]]; then
    echo "Activation service returned HTTP ${http_code} from ${endpoint}" >&2
    printf '%s\n' "$response" | head -c 500 >&2
    echo >&2
    exit 1
  fi

  api_key="$(printf '%s' "$response" | jq -r '.api_key // empty')"
  if [[ -z "$api_key" ]]; then
    echo "Activation service did not return api_key" >&2
    printf '%s\n' "$response" | head -c 500 >&2
    echo >&2
    exit 1
  fi

  OPENAI_API_KEY="$api_key"
  export OPENAI_API_KEY
  echo "Activated API key for external_id=${OPENAI_ACTIVATE_EXTERNAL_ID} username=${OPENAI_ACTIVATE_USERNAME}" >&2
}

write_codex_config() {
  local escaped_base escaped_model escaped_sandbox_mode
  escaped_base="$(json_escape "$OPENAI_BASE_URL")"
  escaped_model="$(json_escape "$OPENAI_MODEL")"
  escaped_sandbox_mode="$(json_escape "$RAILWAY_CODEX_SANDBOX_MODE")"

  mkdir -p "$CODEX_HOME" "$WORKSPACE_DIR"

  cat >"$CODEX_HOME/config.toml" <<EOF
model_provider = "custom"
model = ${escaped_model}
model_reasoning_effort = "high"
sandbox_mode = ${escaped_sandbox_mode}

[model_providers.custom]
name = "custom"
base_url = ${escaped_base}
wire_api = "responses"
requires_openai_auth = true

[sandbox_workspace_write]
network_access = true
EOF
}

write_auth_json() {
  jq -n --arg api_key "$OPENAI_API_KEY" '{
    auth_mode: "apikey",
    OPENAI_API_KEY: $api_key,
    tokens: null,
    last_refresh: null
  }' >"$CODEX_HOME/auth.json"
  chmod 600 "$CODEX_HOME/auth.json"
}

prepare_codex_runtime() {
  if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    activate_api_key
  fi

  write_codex_config
  write_auth_json
}

codex_ws_token_sha256() {
  printf '%s' "$CODEX_WS_TOKEN" | sha256sum | cut -d ' ' -f 1
}
