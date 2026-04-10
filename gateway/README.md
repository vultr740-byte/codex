# Codex Telegram Gateway

Docker-friendly Telegram gateway for `codex app-server`.

This service is designed for single-instance container deployment. It runs
Telegram long polling, persists session state in SQLite, and launches Codex
locally via the Python app-server SDK.

By default the gateway starts Codex with `approval_policy=never` and
`sandbox_mode=danger-full-access`, so the agent has full permissions unless you
override those settings with environment variables.

At runtime the gateway also starts a local Responses bridge. Codex connects to
that bridge over `http://127.0.0.1:<port>/v1`, and the bridge forwards HTTP/SSE
traffic to the configured upstream `OPENAI_BASE_URL`. This keeps the deployment
on the standard `codex app-server` architecture while compensating for upstream
providers that support `/v1/responses` over HTTP/SSE but not the websocket
transport Codex expects.
