#!/usr/bin/env bash
set -euo pipefail

normalized_channel="$(printf '%s' "${CODEX_ENABLED_CHANNEL:-codex}" | tr '[:upper:]-' '[:lower:]_')"

if [[ "$normalized_channel" == "weixin" || "$normalized_channel" == "wechat" ]]; then
  exec /usr/local/bin/app-server-with-weixin-start
fi

exec /usr/local/bin/app-server-run
