import assert from "node:assert/strict";
import test from "node:test";

import { loadConfigFromEnv } from "../src/util/env.js";

const ENV_KEYS = [
  "CODEX_APP_SERVER_URL",
  "CODEX_APP_SERVER_TOKEN",
  "WEIXIN_BASE_URL",
  "WEIXIN_CDN_BASE_URL",
  "WEIXIN_TOKEN",
  "CONTROL_API_TOKEN",
  "CODEX_WEIXIN_BRIDGE_CONTROL_API_TOKEN",
  "CODEX_WEIXIN_STATE_DIR",
  "CODEX_WEIXIN_UPLOAD_DIR",
  "CODEX_THREAD_MODE",
  "CODEX_DEFAULT_CWD",
  "CODEX_TURN_TIMEOUT_MS",
] as const;

test("loadConfigFromEnv defaults Codex turn timeout to 30 minutes", () => {
  withEnv({
    CODEX_APP_SERVER_URL: "ws://127.0.0.1:1234",
    CODEX_APP_SERVER_TOKEN: "codex-token",
    WEIXIN_BASE_URL: "https://ilink.example",
  }, () => {
    const config = loadConfigFromEnv();

    assert.equal(config.codexTurnTimeoutMs, 30 * 60 * 1000);
  });
});

test("loadConfigFromEnv accepts custom Codex turn timeout", () => {
  withEnv({
    CODEX_APP_SERVER_URL: "ws://127.0.0.1:1234",
    CODEX_APP_SERVER_TOKEN: "codex-token",
    WEIXIN_BASE_URL: "https://ilink.example",
    CODEX_TURN_TIMEOUT_MS: "600000",
  }, () => {
    const config = loadConfigFromEnv();

    assert.equal(config.codexTurnTimeoutMs, 600_000);
  });
});

test("loadConfigFromEnv rejects invalid Codex turn timeout", () => {
  withEnv({
    CODEX_APP_SERVER_URL: "ws://127.0.0.1:1234",
    CODEX_APP_SERVER_TOKEN: "codex-token",
    WEIXIN_BASE_URL: "https://ilink.example",
    CODEX_TURN_TIMEOUT_MS: "0",
  }, () => {
    assert.throws(() => loadConfigFromEnv(), /CODEX_TURN_TIMEOUT_MS must be a positive integer/u);
  });
});

function withEnv(values: Partial<Record<(typeof ENV_KEYS)[number], string>>, fn: () => void): void {
  const previous = new Map<string, string | undefined>();
  for (const key of ENV_KEYS) {
    previous.set(key, process.env[key]);
    delete process.env[key];
  }
  for (const [key, value] of Object.entries(values)) {
    process.env[key] = value;
  }

  try {
    fn();
  } finally {
    for (const key of ENV_KEYS) {
      const value = previous.get(key);
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
}
