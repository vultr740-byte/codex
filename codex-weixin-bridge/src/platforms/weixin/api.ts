import crypto from "node:crypto";

import type { GetUpdatesResponse } from "./types.js";

export type WeixinApiOptions = {
  baseUrl: string;
  token?: string | null;
  timeoutMs?: number;
};

const DEFAULT_TIMEOUT_MS = 35_000;
const ILINK_APP_ID = "bot";
const DEFAULT_BOT_AGENT = "CodexWeixinBridge/0.1.0";
const ILINK_APP_CLIENT_VERSION = "132099";
const CHANNEL_VERSION = "0.1.0";

export async function getUpdates(params: WeixinApiOptions & {
  getUpdatesBuf: string;
  signal?: AbortSignal;
}): Promise<GetUpdatesResponse> {
  try {
    const response = await postJson(params, "ilink/bot/getupdates", {
      get_updates_buf: params.getUpdatesBuf,
      base_info: buildBaseInfo(),
    });
    return response as GetUpdatesResponse;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      return { ret: 0, msgs: [], get_updates_buf: params.getUpdatesBuf };
    }
    throw error;
  }
}

export async function getConfig(params: WeixinApiOptions & {
  ilinkUserId: string;
  contextToken?: string | null;
}): Promise<{ ret?: number; errmsg?: string; typing_ticket?: string }> {
  const response = await postJson(params, "ilink/bot/getconfig", {
    ilink_user_id: params.ilinkUserId,
    ...(params.contextToken ? { context_token: params.contextToken } : {}),
  });
  return response as { ret?: number; errmsg?: string; typing_ticket?: string };
}

export async function sendTextMessage(params: WeixinApiOptions & {
  toUserId: string;
  text: string;
  contextToken?: string | null;
}): Promise<void> {
  await postJson(params, "ilink/bot/sendmessage", {
    msg: {
      from_user_id: "",
      to_user_id: params.toUserId,
      client_id: `codex-weixin-${crypto.randomUUID()}`,
      message_type: 2,
      message_state: 2,
      item_list: [
        {
          type: 1,
          text_item: {
            text: params.text,
          },
        },
      ],
      ...(params.contextToken ? { context_token: params.contextToken } : {}),
    },
    base_info: buildBaseInfo(),
  });
}

export async function sendTyping(params: WeixinApiOptions & {
  toUserId: string;
  typingTicket: string;
  status: 1 | 2;
}): Promise<void> {
  await postJson(params, "ilink/bot/sendtyping", {
    ilink_user_id: params.toUserId,
    typing_ticket: params.typingTicket,
    status: params.status,
  });
}

export async function getQrCode(params: {
  baseUrl: string;
  botType?: string;
  localTokenList?: string[];
}): Promise<{ qrcode: string; qrcode_img_content: string }> {
  const response = await postJson(
    { baseUrl: params.baseUrl, token: "", timeoutMs: DEFAULT_TIMEOUT_MS },
    `ilink/bot/get_bot_qrcode?bot_type=${encodeURIComponent(params.botType ?? "3")}`,
    { local_token_list: params.localTokenList ?? [] },
    { auth: false, includeBaseInfo: false },
  );
  assertNoApiError(response, "get_bot_qrcode");
  return response as { qrcode: string; qrcode_img_content: string };
}

export async function getQrCodeStatus(params: {
  baseUrl: string;
  qrcode: string;
  timeoutMs?: number;
  verifyCode?: string | null;
}): Promise<{
  status: "wait" | "scaned" | "confirmed" | "expired" | "scaned_but_redirect" | "need_verifycode" | "verify_code_blocked" | "binded_redirect";
  bot_token?: string;
  ilink_bot_id?: string;
  baseurl?: string;
  ilink_user_id?: string;
  redirect_host?: string;
}> {
  let endpoint = `ilink/bot/get_qrcode_status?qrcode=${encodeURIComponent(params.qrcode)}`;
  if (params.verifyCode) {
    endpoint += `&verify_code=${encodeURIComponent(params.verifyCode)}`;
  }
  const response = await getJson({
    baseUrl: params.baseUrl,
    endpoint,
    timeoutMs: params.timeoutMs ?? DEFAULT_TIMEOUT_MS,
  });
  assertNoApiError(response, "get_qrcode_status");
  return response as Awaited<ReturnType<typeof getQrCodeStatus>>;
}

async function postJson(
  opts: WeixinApiOptions & { signal?: AbortSignal },
  endpoint: string,
  body: unknown,
  options: { auth?: boolean; includeBaseInfo?: boolean } = {},
): Promise<unknown> {
  const url = new URL(endpoint, ensureTrailingSlash(opts.baseUrl));
  const controller = opts.signal ? null : new AbortController();
  const timeout = controller
    ? setTimeout(() => controller.abort(), opts.timeoutMs ?? DEFAULT_TIMEOUT_MS)
    : null;

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        ...(options.auth === false || !opts.token?.trim() ? {} : { Authorization: `Bearer ${opts.token.trim()}` }),
        AuthorizationType: "ilink_bot_token",
        "Content-Type": "application/json",
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
        "X-WECHAT-UIN": randomWechatUin(),
      },
      body: JSON.stringify(options.includeBaseInfo === false ? body : { ...(isRecord(body) ? body : { value: body }), base_info: buildBaseInfo() }),
      signal: opts.signal ?? controller?.signal,
    });

    const text = await response.text();
    if (!response.ok) {
      throw new Error(`Weixin ${endpoint} returned HTTP ${response.status}: ${text.slice(0, 500)}`);
    }
    const json = text.trim() ? JSON.parse(text) : {};
    assertNoApiError(json, endpoint);
    return json;
  } finally {
    if (timeout) {
      clearTimeout(timeout);
    }
  }
}

async function getJson(params: {
  baseUrl: string;
  endpoint: string;
  timeoutMs?: number;
}): Promise<unknown> {
  const url = new URL(params.endpoint, ensureTrailingSlash(params.baseUrl));
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), params.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      method: "GET",
      headers: {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
      },
      signal: controller.signal,
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`Weixin ${params.endpoint} returned HTTP ${response.status}: ${text.slice(0, 500)}`);
    }
    const json = text.trim() ? JSON.parse(text) : {};
    assertNoApiError(json, params.endpoint);
    return json;
  } finally {
    clearTimeout(timeout);
  }
}

function ensureTrailingSlash(value: string): string {
  return value.endsWith("/") ? value : `${value}/`;
}

function randomWechatUin(): string {
  const uint32 = crypto.randomBytes(4).readUInt32BE(0);
  return Buffer.from(String(uint32), "utf8").toString("base64");
}

function buildBaseInfo(): { channel_version: string; bot_agent: string } {
  return {
    channel_version: CHANNEL_VERSION,
    bot_agent: DEFAULT_BOT_AGENT,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function buildClientVersion(version: string): number {
  const parts = version.split(".").map((part) => Number.parseInt(part, 10));
  const major = parts[0] ?? 0;
  const minor = parts[1] ?? 0;
  const patch = parts[2] ?? 0;
  return ((major & 0xff) << 16) | ((minor & 0xff) << 8) | (patch & 0xff);
}

function assertNoApiError(value: unknown, label: string): void {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return;
  }
  const record = value as { ret?: unknown; errcode?: unknown; errmsg?: unknown };
  const ret = typeof record.ret === "number" ? record.ret : 0;
  const errcode = typeof record.errcode === "number" ? record.errcode : 0;
  if (ret !== 0 || errcode !== 0) {
    const error = new Error(`Weixin ${label} failed: ret=${ret} errcode=${errcode} errmsg=${String(record.errmsg ?? "")}`);
    error.name = "WeixinApiError";
    throw error;
  }
}
