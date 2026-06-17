# codex-weixin-bridge

Bridge that connects Weixin long-poll messages to a Codex app-server WebSocket thread.

The Codex app-server owns the WebSocket conversation runtime. This bridge owns
Weixin login, polling, message ingestion, forwarding messages into the app-server,
and proxying external WebSocket upgrades to the app-server when both processes run
inside the same container.

## Responsibilities

- Poll Weixin `getupdates`
- Keep one Codex thread per Weixin user
- Send user text to Codex app-server
- Send Codex replies back to Weixin
- Expose a small control API for QR login
- Proxy WebSocket clients to the configured Codex app-server URL

## Environment

- `CODEX_APP_SERVER_URL`
- `CODEX_APP_SERVER_TOKEN`
- `WEIXIN_BASE_URL`
- `WEIXIN_TOKEN` optional fallback for existing installs
- `CONTROL_API_TOKEN` optional bearer token for control endpoints
- `CODEX_WEIXIN_STATE_DIR` optional, defaults to `./state`
- `CODEX_THREAD_MODE` optional, defaults to `per_user`
- `CODEX_DEFAULT_CWD` optional
- `PORT` optional, defaults to `3000`

## Run

```bash
npm install
npm run start
```

## Container Mode

In the Codex Railway image this package is built into the same container as
`codex app-server`. The bridge listens on `$PORT`, while app-server listens on
an internal loopback port configured by `scripts/app-server-with-weixin-start.sh`.

Required runtime variables for Weixin mode:

- `CODEX_ENABLED_CHANNEL=weixin`
- `CODEX_WS_TOKEN`
- `CODEX_APP_SERVER_TOKEN`
- `CODEX_APP_SERVER_URL`
- `WEIXIN_BASE_URL`
- `CONTROL_API_TOKEN` recommended

## Control API

Health checks:

- `GET /healthz`
- `GET /readyz`

Weixin login:

- `POST /api/weixin/login/start`
- `GET /api/weixin/login/status?sessionKey=...`
- `POST /api/weixin/login/status`
- `POST /api/weixin/login/verify`

Account status:

- `GET /api/weixin/account`

If `CONTROL_API_TOKEN` is set, send `Authorization: Bearer <token>` to the control endpoints.

## Login flow

1. Call `POST /api/weixin/login/start`.
2. Scan the returned QR code.
3. Poll `GET /api/weixin/login/status?sessionKey=...` or `POST /api/weixin/login/status`.
4. If the server asks for a verify code, submit it with `POST /api/weixin/login/verify`.
5. When login succeeds, the bridge saves `state/weixin-account.json`.
6. The main bridge loop picks up the saved token automatically.

## Notes

- The bridge is text-first in this version.
- WebSocket conversation still happens through the Codex app-server. The bridge
  only proxies the external WebSocket connection when used as the public entrypoint.
