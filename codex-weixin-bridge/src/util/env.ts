import path from "node:path";

import type { BridgeConfig } from "../types.js";

function required(value: string | undefined, name: string): string {
  const trimmed = value?.trim();
  if (!trimmed) {
    throw new Error(`${name} is required.`);
  }
  return trimmed;
}

function optional(value: string | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

export function loadConfigFromEnv(): BridgeConfig {
  const stateDir = optional(process.env.CODEX_WEIXIN_STATE_DIR) ?? path.join(process.cwd(), "state");

  return {
    appServerUrl: required(process.env.CODEX_APP_SERVER_URL, "CODEX_APP_SERVER_URL"),
    appServerToken: required(process.env.CODEX_APP_SERVER_TOKEN, "CODEX_APP_SERVER_TOKEN"),
    weixinBaseUrl: required(process.env.WEIXIN_BASE_URL, "WEIXIN_BASE_URL"),
    weixinToken: optional(process.env.WEIXIN_TOKEN),
    controlApiToken:
      optional(process.env.CONTROL_API_TOKEN) ??
      optional(process.env.CODEX_WEIXIN_BRIDGE_CONTROL_API_TOKEN),
    stateDir,
    codexThreadMode: process.env.CODEX_THREAD_MODE === "single_thread" ? "single_thread" : "per_user",
    defaultCwd: optional(process.env.CODEX_DEFAULT_CWD),
  };
}
