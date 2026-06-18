import WebSocket from "ws";
import type { InboundAttachment } from "../../types.js";

type JsonObject = Record<string, unknown>;
type CodexTurnInput =
  | {
      type: "text";
      text: string;
      text_elements: [];
    }
  | {
      type: "localImage";
      path: string;
    };

type PendingRequest = {
  method: string;
  reject: (error: Error) => void;
  resolve: (value: unknown) => void;
  timeout: ReturnType<typeof setTimeout>;
};

const WEIXIN_DEVELOPER_INSTRUCTIONS = [
  "You are replying through a Weixin bridge.",
  "When the user asks you to send a generated local file as an attachment, do not use a markdown link as the only delivery mechanism.",
  "Create the file on disk, then include a fenced JSON block exactly like:",
  "```codex-weixin-attachments",
  "{\"attachments\":[{\"path\":\"/absolute/path/to/file.zip\",\"caption\":\"optional short caption\"}]}",
  "```",
  "The bridge will upload those paths and send them as native Weixin attachment payloads. Keep normal user-facing text outside the fenced block.",
].join("\n");

export type CodexTurnResult = {
  assistantText: string;
  threadId: string;
  turnId: string | null;
};

export class CodexAppServerClient {
  private readonly websocketUrl: string;
  private readonly token: string;
  private readonly defaultCwd: string | null;
  private readonly requestTimeoutMs: number;
  private ws: WebSocket | null = null;
  private initialized = false;
  private nextId = 1;
  private pending = new Map<number, PendingRequest>();
  private connectPromise: Promise<void> | null = null;
  private activeTurns = new Map<string, {
    finalText: string;
    resolve: (result: CodexTurnResult) => void;
    reject: (error: Error) => void;
    threadId: string;
    turnId: string | null;
    timeout: ReturnType<typeof setTimeout>;
  }>();

  constructor(params: {
    websocketUrl: string;
    token: string;
    defaultCwd?: string | null;
    requestTimeoutMs?: number;
  }) {
    this.websocketUrl = normalizeWebSocketUrl(params.websocketUrl);
    this.token = params.token;
    this.defaultCwd = params.defaultCwd ?? null;
    this.requestTimeoutMs = params.requestTimeoutMs ?? 30_000;
  }

  async ensureConnected(): Promise<void> {
    if (this.ws?.readyState === WebSocket.OPEN && this.initialized) {
      return;
    }

    if (this.connectPromise) {
      return this.connectPromise;
    }

    this.connectPromise = this.connect();
    try {
      await this.connectPromise;
    } finally {
      this.connectPromise = null;
    }
  }

  async startThread(): Promise<string> {
    await this.ensureConnected();
    const result = await this.send("thread/start", {
      ephemeral: false,
      ...(this.defaultCwd ? { cwd: this.defaultCwd } : {}),
      approvalPolicy: "never",
      sandbox: "danger-full-access",
      developerInstructions: WEIXIN_DEVELOPER_INSTRUCTIONS,
    }) as { thread?: { id?: unknown } };
    const threadId = normalizeText(result.thread?.id);
    if (!threadId) {
      throw new Error("Codex app-server did not return a thread id.");
    }
    return threadId;
  }

  async sendTurn(params: {
    threadId: string;
    text: string;
    attachments?: InboundAttachment[];
    clientUserMessageId?: string | null;
    timeoutMs?: number;
  }): Promise<CodexTurnResult> {
    await this.ensureConnected();
    const turn = await this.send("turn/start", {
      threadId: params.threadId,
      ...(params.clientUserMessageId ? { clientUserMessageId: params.clientUserMessageId } : {}),
      input: buildCodexTurnInput(params.text, params.attachments ?? []),
    }) as { turn?: { id?: unknown } };
    const turnId = normalizeText(turn.turn?.id);
    if (!turnId) {
      throw new Error("Codex app-server did not return a turn id.");
    }

    return await new Promise<CodexTurnResult>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.activeTurns.delete(turnId);
        reject(new Error("Timed out waiting for Codex turn completion."));
      }, params.timeoutMs ?? 180_000);

      this.activeTurns.set(turnId, {
        finalText: "",
        resolve,
        reject,
        threadId: params.threadId,
        turnId,
        timeout,
      });
    });
  }

  async close(): Promise<void> {
    this.initialized = false;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timeout);
      pending.reject(new Error("Codex WebSocket closed."));
    }
    this.pending.clear();
    for (const turn of this.activeTurns.values()) {
      clearTimeout(turn.timeout);
      turn.reject(new Error("Codex WebSocket closed before turn completion."));
    }
    this.activeTurns.clear();
    this.ws?.close();
    this.ws = null;
  }

  private async connect(): Promise<void> {
    await this.close();

    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(this.websocketUrl, {
        headers: {
          Authorization: `Bearer ${this.token}`,
        },
      });
      this.ws = ws;

      const cleanup = () => {
        ws.off("open", onOpen);
        ws.off("error", onError);
      };
      const onOpen = () => {
        cleanup();
        resolve();
      };
      const onError = (error: Error) => {
        cleanup();
        reject(error);
      };

      ws.on("open", onOpen);
      ws.on("error", onError);
      ws.on("message", (data) => this.handleMessage(data.toString()));
      ws.on("close", () => {
        this.initialized = false;
        for (const pending of this.pending.values()) {
          clearTimeout(pending.timeout);
          pending.reject(new Error("Codex WebSocket closed."));
        }
        this.pending.clear();
      });
    });

    await this.send("initialize", {
      clientInfo: {
        name: "codex_weixin_bridge",
        title: "Codex Weixin Bridge",
        version: "0.1.0",
      },
    });
    this.notify("initialized", {});
    this.initialized = true;
  }

  private send(method: string, params: JsonObject): Promise<unknown> {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("Codex WebSocket is not open."));
    }
    const id = this.nextId++;
    ws.send(JSON.stringify({ id, method, params }));

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Timed out waiting for ${method}.`));
      }, this.requestTimeoutMs);

      this.pending.set(id, { method, reject, resolve, timeout });
    });
  }

  private notify(method: string, params: JsonObject): void {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      throw new Error("Codex WebSocket is not open.");
    }
    ws.send(JSON.stringify({ method, params }));
  }

  private handleMessage(raw: string): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return;
    }

    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return;
    }

    const message = parsed as JsonObject;
    if (typeof message.id === "number") {
      const pending = this.pending.get(message.id);
      if (!pending) {
        return;
      }
      this.pending.delete(message.id);
      clearTimeout(pending.timeout);
      if (message.error) {
        pending.reject(new Error(`${pending.method}: ${JSON.stringify(message.error)}`));
      } else {
        pending.resolve(message.result ?? {});
      }
      return;
    }

    if (message.method === "item/agentMessage/delta") {
      const params = message.params as JsonObject | undefined;
      const turnId = normalizeText(params?.turnId) ?? this.singleActiveTurnId();
      const delta = normalizeText(params?.delta) ?? "";
      if (turnId && this.activeTurns.has(turnId)) {
        const turn = this.activeTurns.get(turnId)!;
        turn.finalText += delta;
      }
    }

    if (message.method === "item/completed") {
      const params = message.params as JsonObject | undefined;
      const item = params?.item;
      const turnId = normalizeText(params?.turnId) ?? this.singleActiveTurnId();
      if (
        turnId &&
        this.activeTurns.has(turnId) &&
        item &&
        typeof item === "object" &&
        !Array.isArray(item)
      ) {
        const record = item as JsonObject;
        if (record.type === "agentMessage") {
          const text = normalizeText(record.text);
          if (text !== null) {
            this.activeTurns.get(turnId)!.finalText = text;
          }
        }
      }
    }

    if (message.method === "turn/completed") {
      const params = message.params as JsonObject | undefined;
      const turn = params?.turn;
      if (!turn || typeof turn !== "object" || Array.isArray(turn)) {
        return;
      }
      const turnRecord = turn as JsonObject;
      const turnId = normalizeText(turnRecord.id);
      if (!turnId) {
        return;
      }
      const active = this.activeTurns.get(turnId);
      if (!active) {
        return;
      }
      this.activeTurns.delete(turnId);
      clearTimeout(active.timeout);
      const status = normalizeText(turnRecord.status);
      if (status === "failed") {
        active.reject(new Error(`Codex turn failed: ${JSON.stringify(turnRecord.error ?? {})}`));
        return;
      }
      active.resolve({
        assistantText: active.finalText.trim(),
        threadId: active.threadId,
        turnId,
      });
    }
  }

  private singleActiveTurnId(): string | null {
    return this.activeTurns.size === 1 ? [...this.activeTurns.keys()][0] : null;
  }
}

function buildCodexTurnInput(text: string, attachments: InboundAttachment[]): CodexTurnInput[] {
  const prompt = attachments.length === 0 ? text : buildAttachmentPrompt(text, attachments);
  const input: CodexTurnInput[] = [
    {
      type: "text",
      text: prompt,
      text_elements: [],
    },
  ];
  for (const attachment of attachments) {
    if (attachment.kind === "image") {
      input.push({
        type: "localImage",
        path: attachment.localPath,
      });
    }
  }
  return input;
}

function buildAttachmentPrompt(text: string, attachments: InboundAttachment[]): string {
  const lines: string[] = [];
  const normalizedText = text.trim();
  if (normalizedText) {
    lines.push(normalizedText, "");
  } else {
    lines.push("User sent Weixin attachments without additional text.", "");
  }
  lines.push("Weixin attachments:");
  attachments.forEach((attachment, index) => {
    lines.push(`${index + 1}. ${describeAttachment(attachment)}`);
    lines.push(`   path: ${attachment.localPath}`);
    if (attachment.fileName) {
      lines.push(`   filename: ${attachment.fileName}`);
    }
    if (attachment.mimeType) {
      lines.push(`   mime: ${attachment.mimeType}`);
    }
    if (typeof attachment.durationSeconds === "number" && Number.isFinite(attachment.durationSeconds)) {
      lines.push(`   duration_seconds: ${attachment.durationSeconds}`);
    }
    if (attachment.transcriptText) {
      lines.push(`   transcript_hint: ${attachment.transcriptText}`);
    }
    if (attachment.kind === "image") {
      lines.push("   attached_as: localImage");
    }
  });
  lines.push("", "Use the local file paths above when you inspect these attachments.");
  return lines.join("\n");
}

function describeAttachment(attachment: InboundAttachment): string {
  switch (attachment.kind) {
    case "image":
      return "image";
    case "voice":
      return "voice";
    case "file":
      return "file";
    case "video":
      return "video";
  }
}

function normalizeText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function normalizeWebSocketUrl(value: string): string {
  const trimmed = value.trim();
  if (/^wss?:\/\//i.test(trimmed)) {
    return trimmed.replace(/\/+$/, "");
  }
  if (/^https?:\/\//i.test(trimmed)) {
    const url = new URL(trimmed);
    url.protocol = url.protocol === "http:" ? "ws:" : "wss:";
    return url.toString().replace(/\/+$/, "");
  }
  return `wss://${trimmed}`;
}
