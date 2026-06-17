import http from "node:http";
import net from "node:net";
import tls from "node:tls";
import type { Duplex } from "node:stream";

import { Bridge } from "./core/bridge.js";
import { WeixinLoginManager } from "./platforms/weixin/login.js";
import { WeixinAccountStore } from "./store/weixin-account-store.js";
import { loadConfigFromEnv } from "./util/env.js";

type JsonRecord = Record<string, unknown>;

async function main(): Promise<void> {
  const config = loadConfigFromEnv();
  const bridge = new Bridge(config);
  const accountStore = new WeixinAccountStore(config.stateDir);
  const login = new WeixinLoginManager({ accountStore });
  let ready = false;

  const server = http.createServer((req, res) => {
    void handleRequest(req, res, {
      isReady: () => ready,
      config,
      login,
      accountStore,
    });
  });
  server.on("upgrade", (req, socket, head) => {
    proxyWebSocketUpgrade(req, socket, head, config.appServerUrl);
  });

  const port = Number(process.env.PORT ?? 3000);
  server.listen(port, () => {
    ready = true;
    process.stdout.write(`codex-weixin-bridge listening on ${port}\n`);
  });

  const shutdown = async () => {
    ready = false;
    await bridge.stop();
    server.close();
    process.exit(0);
  };

  process.on("SIGINT", () => void shutdown());
  process.on("SIGTERM", () => void shutdown());

  await bridge.start();
}

function proxyWebSocketUpgrade(
  req: http.IncomingMessage,
  socket: Duplex,
  head: Buffer,
  appServerUrl: string,
): void {
  const upstreamUrl = new URL(appServerUrl);
  const port = Number(upstreamUrl.port || (upstreamUrl.protocol === "wss:" ? 443 : 80));
  const host = upstreamUrl.hostname;
  const onConnected = (upstream: net.Socket | tls.TLSSocket) => {
    const path = `${upstreamUrl.pathname === "/" ? "" : upstreamUrl.pathname}${req.url ?? "/"}`;
    const headers = [
      `${req.method ?? "GET"} ${path} HTTP/${req.httpVersion}`,
      `Host: ${upstreamUrl.host}`,
      ...Object.entries(req.headers)
        .filter(([name]) => name.toLowerCase() !== "host")
        .map(([name, value]) => `${name}: ${Array.isArray(value) ? value.join(", ") : value ?? ""}`),
      "",
      "",
    ];
    upstream.write(headers.join("\r\n"));
    if (head.length > 0) {
      upstream.write(head);
    }
    upstream.pipe(socket);
    socket.pipe(upstream);
  };
  const connectOptions = { host, port };
  const upstream =
    upstreamUrl.protocol === "wss:"
      ? tls.connect({ ...connectOptions, servername: host })
      : net.connect(connectOptions);

  const connectedEvent = upstreamUrl.protocol === "wss:" ? "secureConnect" : "connect";
  upstream.once(connectedEvent, () => onConnected(upstream));

  upstream.on("error", (error) => {
    console.error(error);
    socket.end("HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n");
  });
  socket.on("error", () => upstream.destroy());
}

async function handleRequest(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  ctx: {
    isReady: () => boolean;
    config: ReturnType<typeof loadConfigFromEnv>;
    login: WeixinLoginManager;
    accountStore: WeixinAccountStore;
  },
): Promise<void> {
  try {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (req.method === "GET" && url.pathname === "/healthz") {
      writeJson(res, 200, { ok: true });
      return;
    }
    if (req.method === "GET" && url.pathname === "/readyz") {
      writeJson(res, ctx.isReady() ? 200 : 503, { ok: ctx.isReady() });
      return;
    }

    if (!isAuthorized(req, ctx.config.controlApiToken)) {
      writeJson(res, 401, { error: "unauthorized" });
      return;
    }

    if (req.method === "GET" && url.pathname === "/api/weixin/account") {
      const account = ctx.accountStore.load();
      writeJson(res, 200, {
        ok: true,
        connected: Boolean(account),
        account: account
          ? {
              accountId: account.accountId,
              baseUrl: account.baseUrl,
              userId: account.userId,
              savedAt: account.savedAt,
            }
          : null,
      });
      return;
    }

    if (req.method === "GET" && url.pathname === "/api/weixin/login/status") {
      const sessionKey = url.searchParams.get("sessionKey")?.trim();
      if (sessionKey) {
        writeJson(res, 200, { ok: true, login: await ctx.login.pollLogin({ sessionKey }) });
        return;
      }
      writeJson(res, 200, { ok: true, login: ctx.login.getState() });
      return;
    }

    if (req.method === "POST" && url.pathname === "/api/weixin/login/start") {
      const body = await readJsonBody(req);
      const force = Boolean(body.force);
      const botType = typeof body.botType === "string" ? body.botType : null;
      const baseUrl = typeof body.baseUrl === "string" && body.baseUrl.trim()
        ? body.baseUrl.trim()
        : ctx.config.weixinBaseUrl;
      const state = await ctx.login.startLogin({ baseUrl, botType, force });
      writeJson(res, 200, { ok: true, login: state });
      return;
    }

    if (req.method === "POST" && url.pathname === "/api/weixin/login/status") {
      const body = await readJsonBody(req);
      const sessionKey = requiredString(body.sessionKey, "sessionKey");
      writeJson(res, 200, { ok: true, login: await ctx.login.pollLogin({ sessionKey }) });
      return;
    }

    if (req.method === "POST" && url.pathname === "/api/weixin/login/verify") {
      const body = await readJsonBody(req);
      const sessionKey = requiredString(body.sessionKey, "sessionKey");
      const verifyCode = requiredString(body.verifyCode, "verifyCode");
      writeJson(res, 200, { ok: true, login: ctx.login.submitVerifyCode({ sessionKey, verifyCode }) });
      return;
    }

    writeJson(res, 404, { error: "not found" });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    writeJson(res, 400, { error: message });
  }
}

function isAuthorized(req: http.IncomingMessage, token: string | null): boolean {
  if (!token) {
    return true;
  }
  const authorization = req.headers.authorization;
  return authorization === `Bearer ${token}`;
}

function writeJson(res: http.ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

async function readJsonBody(req: http.IncomingMessage): Promise<JsonRecord> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) {
    return {};
  }
  const parsed = JSON.parse(raw) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON body must be an object.");
  }
  return parsed as JsonRecord;
}

function requiredString(value: unknown, name: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${name} is required.`);
  }
  return value.trim();
}

void main().catch((error) => {
  console.error(error);
  process.exit(1);
});
