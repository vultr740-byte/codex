import assert from "node:assert/strict";
import crypto from "node:crypto";
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
    weixinCdnBaseUrl: weixinBaseUrl,
    weixinToken: "weixin-token",
    controlApiToken: null,
    stateDir,
    uploadDir: path.join(stateDir, "uploads"),
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

test("bridge refreshes a stale Codex thread binding when the app-server no longer has the thread", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "codex-weixin-bridge-"));
  fs.writeFileSync(
    path.join(stateDir, "thread-bindings.json"),
    JSON.stringify({
      bindings: [
        {
          weixinUserId: "user-1",
          codexThreadId: "old-thread",
          updatedAt: 1,
        },
      ],
    }),
    "utf8",
  );
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

  const turnStartThreadIds: string[] = [];
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
        ws.send(JSON.stringify({ id: message.id, result: { thread: { id: "new-thread" } } }));
        return;
      }
      if (message.id !== undefined && message.method === "turn/start") {
        turnStartThreadIds.push(message.params?.threadId ?? "");
        if (message.params?.threadId === "old-thread") {
          ws.send(JSON.stringify({
            id: message.id,
            error: { code: -32600, message: "thread not found: old-thread" },
          }));
          return;
        }
        ws.send(JSON.stringify({ id: message.id, result: { turn: { id: "turn-1" } } }));
        setTimeout(() => {
          ws.send(JSON.stringify({
            method: "item/agentMessage/delta",
            params: { turnId: "turn-1", delta: "fresh reply" },
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
    weixinCdnBaseUrl: weixinBaseUrl,
    weixinToken: "weixin-token",
    controlApiToken: null,
    stateDir,
    uploadDir: path.join(stateDir, "uploads"),
    codexThreadMode: "per_user",
    defaultCwd: null,
  });

  const bridgeTask = bridge.start();

  try {
    await waitFor(() =>
      receivedWeixinRequests.some((request) => request.endpoint === "ilink/bot/sendmessage")
    );
  } finally {
    await bridge.stop();
    appServer.close();
    weixinServer.close();
    await bridgeTask;
  }

  assert.deepEqual(turnStartThreadIds, ["old-thread", "new-thread"]);

  const sendMessageRequests = receivedWeixinRequests.filter((request) => request.endpoint === "ilink/bot/sendmessage");
  assert.equal(sendMessageRequests.length, 1);
  const sentMessage = sendMessageRequests[0]?.body.msg as { to_user_id?: string; item_list?: Array<{ text_item?: { text?: string } }> } | undefined;
  const sentText = sentMessage?.item_list?.[0]?.text_item?.text ?? "";
  assert.equal(sentMessage?.to_user_id, "user-1");
  assert.equal(sentText, "fresh reply");
  assert.doesNotMatch(sentText, /thread not found/);

  const bindingFile = JSON.parse(fs.readFileSync(path.join(stateDir, "thread-bindings.json"), "utf8")) as {
    bindings: Array<{ weixinUserId: string; codexThreadId: string; updatedAt: number }>;
  };
  assert.equal(bindingFile.bindings.length, 1);
  assert.equal(bindingFile.bindings[0]?.weixinUserId, "user-1");
  assert.equal(bindingFile.bindings[0]?.codexThreadId, "new-thread");
  assert.ok(bindingFile.bindings[0]?.updatedAt > 1);
});

test("bridge downloads Weixin file attachments and forwards local paths to Codex", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "codex-weixin-bridge-"));
  const uploadDir = path.join(stateDir, "uploads");
  const plaintext = Buffer.from("hello from attachment", "utf8");
  const imageBytes = Buffer.from([0xff, 0xd8, 0xff, 0xd9]);
  const aesKey = Buffer.from("00112233445566778899aabbccddeeff", "hex");
  const encrypted = encryptAesEcb(plaintext, aesKey);
  const aesKeyBase64 = Buffer.from(aesKey.toString("hex"), "utf8").toString("base64");
  const receivedWeixinRequests: Array<{ endpoint: string; body: Record<string, unknown> }> = [];
  let getUpdatesCount = 0;

  const cdnServer = http.createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (url.pathname === "/download" && url.searchParams.get("encrypted_query_param") === "file-param") {
      res.writeHead(200, { "content-type": "application/octet-stream" });
      res.end(encrypted);
      return;
    }
    if (url.pathname === "/download" && url.searchParams.get("encrypted_query_param") === "image-param") {
      res.writeHead(200, { "content-type": "image/jpeg" });
      res.end(imageBytes);
      return;
    }
    res.writeHead(404);
    res.end("not found");
  });
  const cdnBaseUrl = await listen(cdnServer);

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
                item_list: [
                  { type: 1, text_item: { text: "please inspect this" } },
                  {
                    type: 2,
                    image_item: {
                      media: {
                        encrypt_query_param: "image-param",
                      },
                    },
                  },
                  {
                    type: 4,
                    file_item: {
                      file_name: "report.txt",
                      media: {
                        encrypt_query_param: "file-param",
                        aes_key: aesKeyBase64,
                      },
                    },
                  },
                ],
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

  let seenTurnInput: unknown = null;
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
        params?: { input?: unknown };
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
        seenTurnInput = message.params?.input;
        ws.send(JSON.stringify({ id: message.id, result: { turn: { id: "turn-1" } } }));
        setTimeout(() => {
          ws.send(JSON.stringify({
            method: "item/agentMessage/delta",
            params: { turnId: "turn-1", delta: "read attachment" },
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
    weixinCdnBaseUrl: cdnBaseUrl,
    weixinToken: "weixin-token",
    controlApiToken: null,
    stateDir,
    uploadDir,
    codexThreadMode: "per_user",
    defaultCwd: null,
  });

  const bridgeTask = bridge.start();

  try {
    await waitFor(() =>
      receivedWeixinRequests.some((request) => request.endpoint === "ilink/bot/sendmessage")
    );
  } finally {
    await bridge.stop();
    appServer.close();
    weixinServer.close();
    cdnServer.close();
    await bridgeTask;
  }

  assert.ok(Array.isArray(seenTurnInput));
  const input = seenTurnInput as Array<{ type?: string; text?: string; path?: string }>;
  assert.equal(input.length, 2);
  assert.equal(input[0]?.type, "text");
  assert.match(input[0]?.text ?? "", /please inspect this/);
  assert.match(input[0]?.text ?? "", /Weixin attachments:/);
  assert.match(input[0]?.text ?? "", /report\.txt/);
  const savedPath = input[0]?.text?.match(/path: (.+report-[^\n]+\.txt)/)?.[1];
  assert.ok(savedPath);
  assert.equal(fs.readFileSync(savedPath, "utf8"), "hello from attachment");
  assert.equal(input[1]?.type, "localImage");
  assert.match(input[1]?.path ?? "", /image-[^\n]+\.jpg/);
  assert.equal(fs.readFileSync(input[1]?.path ?? "").equals(imageBytes), true);
});

test("bridge sends Codex-declared output files as native Weixin attachments", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "codex-weixin-bridge-"));
  const outputFile = path.join(stateDir, "answer.zip");
  fs.writeFileSync(outputFile, "zip bytes", "utf8");
  const receivedWeixinRequests: Array<{ endpoint: string; body: Record<string, unknown> }> = [];
  const uploadedCdnBodies: Buffer[] = [];
  let getUpdatesCount = 0;
  let sawDeveloperInstructions = false;

  const cdnServer = http.createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (req.method === "POST" && url.pathname === "/upload") {
      const chunks: Buffer[] = [];
      req.on("data", (chunk) => {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      });
      req.on("end", () => {
        uploadedCdnBodies.push(Buffer.concat(chunks));
        res.setHeader("x-encrypted-param", "uploaded-param");
        res.end("ok");
      });
      return;
    }
    res.writeHead(404);
    res.end("not found");
  });
  const cdnBaseUrl = await listen(cdnServer);

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
                item_list: [{ type: 1, text_item: { text: "send me the zip" } }],
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

      if (endpoint === "ilink/bot/getuploadurl") {
        res.end(JSON.stringify({
          ret: 0,
          upload_param: "upload-param",
        }));
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
        params?: { developerInstructions?: string };
      };

      if (message.id !== undefined && message.method === "initialize") {
        ws.send(JSON.stringify({ id: message.id, result: {} }));
        return;
      }
      if (message.id !== undefined && message.method === "thread/start") {
        sawDeveloperInstructions = /codex-weixin-attachments/.test(message.params?.developerInstructions ?? "");
        ws.send(JSON.stringify({ id: message.id, result: { thread: { id: "thread-1" } } }));
        return;
      }
      if (message.id !== undefined && message.method === "turn/start") {
        ws.send(JSON.stringify({ id: message.id, result: { turn: { id: "turn-1" } } }));
        setTimeout(() => {
          ws.send(JSON.stringify({
            method: "item/agentMessage/delta",
            params: {
              turnId: "turn-1",
              delta: [
                "Here is the zip.",
                "",
                "```codex-weixin-attachments",
                JSON.stringify({ attachments: [{ path: outputFile, caption: "zip file" }] }),
                "```",
              ].join("\n"),
            },
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
    weixinCdnBaseUrl: cdnBaseUrl,
    weixinToken: "weixin-token",
    controlApiToken: null,
    stateDir,
    uploadDir: path.join(stateDir, "uploads"),
    codexThreadMode: "per_user",
    defaultCwd: null,
  });

  const bridgeTask = bridge.start();

  try {
    await waitFor(() =>
      receivedWeixinRequests.filter((request) => request.endpoint === "ilink/bot/sendmessage").length >= 2
    );
  } finally {
    await bridge.stop();
    appServer.close();
    weixinServer.close();
    cdnServer.close();
    await bridgeTask;
  }

  assert.equal(sawDeveloperInstructions, true);
  assert.equal(uploadedCdnBodies.length, 1);

  const sendMessages = receivedWeixinRequests.filter((request) => request.endpoint === "ilink/bot/sendmessage");
  const textMessage = sendMessages[0]?.body.msg as { item_list?: Array<{ text_item?: { text?: string } }> } | undefined;
  assert.equal(textMessage?.item_list?.[0]?.text_item?.text, "Here is the zip.");
  assert.doesNotMatch(textMessage?.item_list?.[0]?.text_item?.text ?? "", /codex-weixin-attachments/);

  const fileMessage = sendMessages[1]?.body.msg as {
    item_list?: Array<{
      text_item?: { text?: string };
      file_item?: {
        file_name?: string;
        len?: string;
        media?: { encrypt_query_param?: string; aes_key?: string };
      };
    }>;
  } | undefined;
  assert.equal(fileMessage?.item_list?.[0]?.text_item?.text, "zip file");
  assert.equal(fileMessage?.item_list?.[1]?.file_item?.file_name, "answer.zip");
  assert.equal(fileMessage?.item_list?.[1]?.file_item?.len, String(Buffer.byteLength("zip bytes")));
  assert.equal(fileMessage?.item_list?.[1]?.file_item?.media?.encrypt_query_param, "uploaded-param");
  assert.ok(fileMessage?.item_list?.[1]?.file_item?.media?.aes_key);
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
    weixinCdnBaseUrl: weixinBaseUrl,
    weixinToken: "weixin-token",
    controlApiToken: null,
    stateDir,
    uploadDir: path.join(stateDir, "uploads"),
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

function encryptAesEcb(plaintext: Buffer, key: Buffer): Buffer {
  const cipher = crypto.createCipheriv("aes-128-ecb", key, null);
  return Buffer.concat([cipher.update(plaintext), cipher.final()]);
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
