import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import test from "node:test";

import { Bridge } from "../src/core/bridge.js";

const require = createRequire(import.meta.url);
const { Server: WebSocketServer } = require("ws") as typeof import("ws");

test("bridge sends and cancels Weixin typing while a Codex turn is running", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "codex-weixin-bridge-"));
  const receivedWeixinRequests: Array<{ endpoint: string; body: Record<string, unknown> }> = [];
  let getUpdatesCount = 0;

  const weixinServer = http.createServer((req, res) => {
    const endpoint = new URL(req.url ?? "/", "http://localhost").pathname.replace(/^\/+/, "");
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk.toString("utf8");
    });
    req.on("end", () => {
      const body = raw ? JSON.parse(raw) as Record<string, unknown> : {};
      receivedWeixinRequests.push({ endpoint, body });
      res.setHeader("content-type", "application/json");

      if (endpoint === "ilink/bot/getupdates") {
        getUpdatesCount += 1;
        if (getUpdatesCount === 1) {
          res.end(JSON.stringify({
            ret: 0,
            get_updates_buf: "buf-1",
            msgs: [
              {
                message_id: "msg-1",
                from_user_id: "user-1",
                context_token: "ctx-1",
                item_list: [{ type: 1, text_item: { text: "hello" } }],
              },
            ],
          }));
          return;
        }
        res.end(JSON.stringify({ ret: 0, get_updates_buf: "buf-1", msgs: [] }));
        return;
      }

      if (endpoint === "ilink/bot/getconfig") {
        res.end(JSON.stringify({ ret: 0, typing_ticket: "ticket-1" }));
        return;
      }

      res.end(JSON.stringify({ ret: 0 }));
    });
  });

  const weixinBaseUrl = await listen(weixinServer);
  const appServer = new WebSocketServer({ port: 0 });
  await new Promise<void>((resolve) => {
    appServer.once("listening", resolve);
  });
  appServer.on("connection", (ws) => {
    ws.on("message", (data) => {
      const message = JSON.parse(data.toString()) as {
        id?: number;
        method?: string;
        params?: { threadId?: string };
      };

      if (message.id !== undefined && message.method === "initialize") {
        ws.send(JSON.stringify({ id: message.id, result: {} }));
        return;
      }
      if (message.id !== undefined && message.method === "thread/start") {
        ws.send(JSON.stringify({ id: message.id, result: { thread: { id: "thread-1" } } }));
        return;
      }
      if (message.id !== undefined && message.method === "turn/start") {
        ws.send(JSON.stringify({ id: message.id, result: { turn: { id: "turn-1" } } }));
        setTimeout(() => {
          ws.send(JSON.stringify({
            method: "item/agentMessage/delta",
            params: { turnId: "turn-1", delta: "hi" },
          }));
          ws.send(JSON.stringify({
            method: "turn/completed",
            params: { turn: { id: "turn-1", status: "completed" } },
          }));
        }, 50);
      }
    });
  });

  const appServerAddress = appServer.address();
  assert.ok(appServerAddress && typeof appServerAddress !== "string");
  const appServerUrl = `ws://127.0.0.1:${appServerAddress.port}`;
  const bridge = new Bridge({
    appServerUrl,
    appServerToken: "codex-token",
    weixinBaseUrl,
    weixinToken: "weixin-token",
    controlApiToken: null,
    stateDir,
    codexThreadMode: "per_user",
    defaultCwd: null,
  });

  const bridgeTask = bridge.start();

  try {
    await waitFor(() =>
      receivedWeixinRequests.filter((request) => request.endpoint === "ilink/bot/sendtyping").length >= 2 &&
      receivedWeixinRequests.some((request) => request.endpoint === "ilink/bot/sendmessage")
    );
  } finally {
    await bridge.stop();
    appServer.close();
    weixinServer.close();
    await bridgeTask;
  }

  const typingStatuses = receivedWeixinRequests
    .filter((request) => request.endpoint === "ilink/bot/sendtyping")
    .map((request) => request.body.status);
  assert.deepEqual(typingStatuses, [1, 2]);

  const configRequest = receivedWeixinRequests.find((request) => request.endpoint === "ilink/bot/getconfig");
  assert.equal(configRequest?.body.ilink_user_id, "user-1");
  assert.equal(configRequest?.body.context_token, "ctx-1");

  const sendMessageRequest = receivedWeixinRequests.find((request) => request.endpoint === "ilink/bot/sendmessage");
  const sentMessage = sendMessageRequest?.body.msg as { to_user_id?: string; item_list?: Array<{ text_item?: { text?: string } }> } | undefined;
  assert.equal(sentMessage?.to_user_id, "user-1");
  assert.equal(sentMessage?.item_list?.[0]?.text_item?.text, "hi");
});

test("bridge sends a billing failure message when a Codex turn fails with payment error", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "codex-weixin-bridge-"));
  const receivedWeixinRequests: Array<{ endpoint: string; body: Record<string, unknown> }> = [];
  let getUpdatesCount = 0;

  const weixinServer = http.createServer((req, res) => {
    const endpoint = new URL(req.url ?? "/", "http://localhost").pathname.replace(/^\/+/, "");
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk.toString("utf8");
    });
    req.on("end", () => {
      const body = raw ? JSON.parse(raw) as Record<string, unknown> : {};
      receivedWeixinRequests.push({ endpoint, body });
      res.setHeader("content-type", "application/json");

      if (endpoint === "ilink/bot/getupdates") {
        getUpdatesCount += 1;
        if (getUpdatesCount === 1) {
          res.end(JSON.stringify({
            ret: 0,
            get_updates_buf: "buf-1",
            msgs: [
              {
                message_id: "msg-1",
                from_user_id: "user-1",
                context_token: "ctx-1",
                item_list: [{ type: 1, text_item: { text: "hello" } }],
              },
            ],
          }));
          return;
        }
        res.end(JSON.stringify({ ret: 0, get_updates_buf: "buf-1", msgs: [] }));
        return;
      }

      if (endpoint === "ilink/bot/getconfig") {
        res.end(JSON.stringify({ ret: 0, typing_ticket: "ticket-1" }));
        return;
      }

      res.end(JSON.stringify({ ret: 0 }));
    });
  });

  const weixinBaseUrl = await listen(weixinServer);
  const appServer = new WebSocketServer({ port: 0 });
  await new Promise<void>((resolve) => {
    appServer.once("listening", resolve);
  });
  appServer.on("connection", (ws) => {
    ws.on("message", (data) => {
      const message = JSON.parse(data.toString()) as {
        id?: number;
        method?: string;
      };

      if (message.id !== undefined && message.method === "initialize") {
        ws.send(JSON.stringify({ id: message.id, result: {} }));
        return;
      }
      if (message.id !== undefined && message.method === "thread/start") {
        ws.send(JSON.stringify({ id: message.id, result: { thread: { id: "thread-1" } } }));
        return;
      }
      if (message.id !== undefined && message.method === "turn/start") {
        ws.send(JSON.stringify({ id: message.id, result: { turn: { id: "turn-1" } } }));
        setTimeout(() => {
          ws.send(JSON.stringify({
            method: "turn/completed",
            params: {
              turn: {
                id: "turn-1",
                status: "failed",
                error: {
                  message: "unexpected status 402 Payment Required",
                  additionalDetails: "{\"detail\":\"insufficient balance\"}",
                },
              },
            },
          }));
        }, 50);
      }
    });
  });

  const appServerAddress = appServer.address();
  assert.ok(appServerAddress && typeof appServerAddress !== "string");
  const appServerUrl = `ws://127.0.0.1:${appServerAddress.port}`;
  const previousRechargeTarget = process.env.RECHARGE_TARGET;
  process.env.RECHARGE_TARGET = "clawfather";
  const bridge = new Bridge({
    appServerUrl,
    appServerToken: "codex-token",
    weixinBaseUrl,
    weixinToken: "weixin-token",
    controlApiToken: null,
    stateDir,
    codexThreadMode: "per_user",
    defaultCwd: null,
  });

  const bridgeTask = bridge.start();

  try {
    await waitFor(() =>
      receivedWeixinRequests.some((request) => request.endpoint === "ilink/bot/sendmessage")
    );
  } finally {
    if (previousRechargeTarget === undefined) {
      delete process.env.RECHARGE_TARGET;
    } else {
      process.env.RECHARGE_TARGET = previousRechargeTarget;
    }
    await bridge.stop();
    appServer.close();
    weixinServer.close();
    await bridgeTask;
  }

  const sendMessageRequest = receivedWeixinRequests.find((request) => request.endpoint === "ilink/bot/sendmessage");
  const sentMessage = sendMessageRequest?.body.msg as { to_user_id?: string; item_list?: Array<{ text_item?: { text?: string } }> } | undefined;
  assert.equal(sentMessage?.to_user_id, "user-1");
  assert.match(sentMessage?.item_list?.[0]?.text_item?.text ?? "", /模型余额不足/);
  assert.match(sentMessage?.item_list?.[0]?.text_item?.text ?? "", /https:\/\/www\.xialiao\.app\/recharge\/clawfather/);
});

function listen(server: http.Server): Promise<string> {
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address() as { port: number };
      resolve(`http://127.0.0.1:${address.port}`);
    });
  });
}

async function waitFor(predicate: () => boolean): Promise<void> {
  const deadline = Date.now() + 2_000;
  while (Date.now() < deadline) {
    if (predicate()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  assert.fail("Timed out waiting for bridge side effects.");
}
