import { CodexAppServerClient } from "../providers/codex/app-server-client.js";
import { getUpdates, sendTextMessage } from "../platforms/weixin/api.js";
import type { BridgeConfig } from "../types.js";
import { DedupStore } from "../store/dedup-store.js";
import { ThreadBindingStore } from "../store/thread-binding-store.js";
import { WeixinAccountStore } from "../store/weixin-account-store.js";
import fs from "node:fs";
import path from "node:path";

const SESSION_EXPIRED_ERRCODE = -14;

export class Bridge {
  private readonly config: BridgeConfig;
  private readonly codex: CodexAppServerClient;
  private readonly threadBindings: ThreadBindingStore;
  private readonly dedup: DedupStore;
  private readonly accountStore: WeixinAccountStore;
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

          const binding = this.threadBindings.getByWeixinUserId(fromUserId);
          const threadId = binding?.codexThreadId ?? (await this.codex.startThread());
          if (!binding) {
            this.threadBindings.save({
              weixinUserId: fromUserId,
              codexThreadId: threadId,
              updatedAt: Date.now(),
            });
          }

          const result = await this.codex.sendTurn({
            threadId,
            text,
            clientUserMessageId: messageId,
          });

          this.threadBindings.updateWeixinUserId(fromUserId, { updatedAt: Date.now() });

          if (result.assistantText) {
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

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
