import { CodexAppServerClient } from "../providers/codex/app-server-client.js";
import { getConfig, getUpdates, sendTextMessage, sendTyping } from "../platforms/weixin/api.js";
import type { BridgeConfig } from "../types.js";
import { DedupStore } from "../store/dedup-store.js";
import { ThreadBindingStore } from "../store/thread-binding-store.js";
import { WeixinAccountStore } from "../store/weixin-account-store.js";
import fs from "node:fs";
import path from "node:path";

const SESSION_EXPIRED_ERRCODE = -14;
const TYPING_KEEPALIVE_INTERVAL_MS = 5_000;
const DEFAULT_RECHARGE_BASE_URL = "https://www.xialiao.app/recharge/";
const BILLING_ERROR_MESSAGE = "⚠️ 模型余额不足，请充值后重试。";

export class Bridge {
  private readonly config: BridgeConfig;
  private readonly codex: CodexAppServerClient;
  private readonly threadBindings: ThreadBindingStore;
  private readonly dedup: DedupStore;
  private readonly accountStore: WeixinAccountStore;
  private readonly typingTickets = new TypingTicketCache();
  private readonly syncBufPath: string;
  private running = false;

  constructor(config: BridgeConfig) {
    this.config = config;
    this.codex = new CodexAppServerClient({
      websocketUrl: config.appServerUrl,
      token: config.appServerToken,
      defaultCwd: config.defaultCwd,
    });
    this.threadBindings = new ThreadBindingStore(config.stateDir);
    this.dedup = new DedupStore(config.stateDir);
    this.accountStore = new WeixinAccountStore(config.stateDir);
    this.syncBufPath = path.join(config.stateDir, "weixin-sync-buf.json");
  }

  async start(): Promise<void> {
    this.running = true;
    let backoffMs = 1_000;
    while (this.running) {
      try {
        const account = this.accountStore.load();
        const token = account?.token ?? this.config.weixinToken;
        const baseUrl = account?.baseUrl ?? this.config.weixinBaseUrl;
        if (!token) {
          await sleep(5_000);
          continue;
        }

        const resp = await getUpdates({
          baseUrl,
          token,
          getUpdatesBuf: this.loadSyncBuf(),
        });
        if (isApiError(resp)) {
          if (resp.ret === SESSION_EXPIRED_ERRCODE || resp.errcode === SESSION_EXPIRED_ERRCODE) {
            this.accountStore.clear();
          }
          throw new Error(`Weixin getUpdates failed: ret=${resp.ret} errcode=${resp.errcode} errmsg=${resp.errmsg ?? ""}`);
        }
        if (resp.get_updates_buf) {
          this.saveSyncBuf(resp.get_updates_buf);
        }
        backoffMs = 1_000;

        const msgs = resp.msgs ?? [];
        for (const msg of msgs) {
          const messageId = String(msg.message_id ?? msg.seq ?? "");
          const fromUserId = msg.from_user_id?.trim();
          if (!messageId || !fromUserId) {
            continue;
          }
          if (this.dedup.has(messageId)) {
            continue;
          }
          this.dedup.add(messageId);

          const text = extractText(msg.item_list);
          if (!text) {
            continue;
          }

          const typingTicket = await this.getTypingTicket({
            baseUrl,
            token,
            userId: fromUserId,
            contextToken: msg.context_token ?? null,
          });

          const binding = this.threadBindings.getByWeixinUserId(fromUserId);
          const threadId = binding?.codexThreadId ?? (await this.codex.startThread());
          if (!binding) {
            this.threadBindings.save({
              weixinUserId: fromUserId,
              codexThreadId: threadId,
              updatedAt: Date.now(),
            });
          }

          const typing = this.startTypingIndicator({
            baseUrl,
            token,
            toUserId: fromUserId,
            typingTicket,
          });
          let result: { assistantText: string } | null = null;
          try {
            result = await this.codex.sendTurn({
              threadId,
              text,
              clientUserMessageId: messageId,
            });
          } catch (error) {
            console.error(error);
            await sendTextMessage({
              baseUrl,
              token,
              toUserId: fromUserId,
              text: formatCodexFailureMessage(error),
              contextToken: msg.context_token ?? null,
            });
          } finally {
            await typing.stop();
          }

          this.threadBindings.updateWeixinUserId(fromUserId, { updatedAt: Date.now() });

          if (result?.assistantText) {
            await sendTextMessage({
              baseUrl,
              token,
              toUserId: fromUserId,
              text: result.assistantText,
              contextToken: msg.context_token ?? null,
            });
          }
        }
      } catch (error) {
        if (!this.running) {
          break;
        }
        await sleep(backoffMs);
        backoffMs = Math.min(backoffMs * 2, 30_000);
        console.error(error);
      }
    }
  }

  async stop(): Promise<void> {
    this.running = false;
    await this.codex.close();
  }

  private async getTypingTicket(params: {
    baseUrl: string;
    token: string;
    userId: string;
    contextToken: string | null;
  }): Promise<string | null> {
    const cached = this.typingTickets.get(params.userId);
    if (cached) {
      return cached;
    }

    try {
      const config = await getConfig({
        baseUrl: params.baseUrl,
        token: params.token,
        ilinkUserId: params.userId,
        contextToken: params.contextToken,
      });
      const ticket = config.typing_ticket?.trim();
      if (ticket) {
        this.typingTickets.set(params.userId, ticket);
        return ticket;
      }
    } catch (error) {
      console.error(`Weixin getconfig failed for ${params.userId}:`, error);
    }

    return null;
  }

  private startTypingIndicator(params: {
    baseUrl: string;
    token: string;
    toUserId: string;
    typingTicket: string | null;
  }): { stop: () => Promise<void> } {
    if (!params.typingTicket) {
      return { stop: async () => {} };
    }

    let stopped = false;
    let sendQueue = Promise.resolve();
    const send = async (status: 1 | 2) => {
      try {
        await sendTyping({
          baseUrl: params.baseUrl,
          token: params.token,
          toUserId: params.toUserId,
          typingTicket: params.typingTicket!,
          status,
        });
      } catch (error) {
        console.error(`Weixin sendtyping failed for ${params.toUserId}:`, error);
      }
    };

    const enqueue = (status: 1 | 2) => {
      sendQueue = sendQueue.then(() => send(status));
      return sendQueue;
    };

    void enqueue(1);
    const interval = setInterval(() => {
      if (!stopped) {
        void enqueue(1);
      }
    }, TYPING_KEEPALIVE_INTERVAL_MS);

    return {
      stop: async () => {
        if (stopped) {
          return;
        }
        stopped = true;
        clearInterval(interval);
        await enqueue(2);
      },
    };
  }

  private loadSyncBuf(): string {
    try {
      if (!fs.existsSync(this.syncBufPath)) {
        return "";
      }
      const raw = fs.readFileSync(this.syncBufPath, "utf8");
      const parsed = JSON.parse(raw) as { getUpdatesBuf?: string };
      return typeof parsed.getUpdatesBuf === "string" ? parsed.getUpdatesBuf : "";
    } catch {
      return "";
    }
  }

  private saveSyncBuf(getUpdatesBuf: string): void {
    try {
      fs.writeFileSync(this.syncBufPath, JSON.stringify({ getUpdatesBuf }, null, 2), "utf8");
    } catch (error) {
      console.error(error);
    }
  }
}

function extractText(items: Array<{ type?: number; text_item?: { text?: string }; voice_item?: { text?: string } } | undefined> | undefined): string {
  for (const item of items ?? []) {
    if (item?.type === 1 && typeof item.text_item?.text === "string") {
      return item.text_item.text.trim();
    }
    if (item?.type === 3 && typeof item.voice_item?.text === "string") {
      return item.voice_item.text.trim();
    }
  }
  return "";
}

function isApiError(resp: { ret?: number; errcode?: number }): boolean {
  return (resp.ret !== undefined && resp.ret !== 0) || (resp.errcode !== undefined && resp.errcode !== 0);
}

function formatCodexFailureMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  if (isBillingFailure(message)) {
    const rechargeUrl = resolveRechargeUrl();
    return rechargeUrl ? `${BILLING_ERROR_MESSAGE}\n${rechargeUrl}` : BILLING_ERROR_MESSAGE;
  }
  return `⚠️ Codex 对话失败：${message.slice(0, 500)}`;
}

function isBillingFailure(message: string): boolean {
  const normalized = message.toLowerCase();
  return (
    normalized.includes("402") ||
    normalized.includes("payment required") ||
    normalized.includes("insufficient balance") ||
    normalized.includes("insufficient funds") ||
    normalized.includes("credits") ||
    normalized.includes("billing") ||
    normalized.includes("quota")
  );
}

function resolveRechargeUrl(): string | null {
  const target = process.env.RECHARGE_TARGET?.trim();
  if (!target) {
    return null;
  }
  const baseUrl = process.env.RECHARGE_BASE_URL?.trim() || DEFAULT_RECHARGE_BASE_URL;
  const normalizedBaseUrl = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  return `${normalizedBaseUrl}${encodeURIComponent(target)}`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

class TypingTicketCache {
  private readonly tickets = new Map<string, string>();

  get(userId: string): string | null {
    return this.tickets.get(userId) ?? null;
  }

  set(userId: string, ticket: string): void {
    this.tickets.set(userId, ticket);
  }
}
