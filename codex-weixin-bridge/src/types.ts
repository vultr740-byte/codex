export type BridgeConfig = {
  appServerUrl: string;
  appServerToken: string;
  weixinBaseUrl: string;
  weixinToken: string | null;
  controlApiToken: string | null;
  stateDir: string;
  codexThreadMode: "per_user" | "single_thread";
  defaultCwd: string | null;
};

export type WeixinInboundMessage = {
  messageId: string;
  fromUserId: string;
  toUserId: string | null;
  text: string;
  contextToken: string | null;
  createTimeMs: number | null;
};

export type CodexConnectionInfo = {
  websocketUrl: string;
  authorizationHeader: string;
  token: string;
};

export type ThreadBinding = {
  weixinUserId: string;
  codexThreadId: string;
  updatedAt: number;
};
