#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f /usr/local/lib/codex-app-server-common.sh ]]; then
  # shellcheck source=/usr/local/lib/codex-app-server-common.sh
  source /usr/local/lib/codex-app-server-common.sh
else
  # shellcheck source=./codex-app-server-common.sh
  source "${script_dir}/codex-app-server-common.sh"
fi

prepare_codex_runtime

listen_url="${CODEX_APP_SERVER_LISTEN:-ws://0.0.0.0:${PORT:-3000}}"
token_sha256="$(codex_ws_token_sha256)"

exec codex app-server \
  --listen "$listen_url" \
  --ws-auth capability-token \
  --ws-token-sha256 "$token_sha256"
