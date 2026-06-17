export const MessageItemType = {
  TEXT: 1,
  IMAGE: 2,
  VOICE: 3,
  FILE: 4,
  VIDEO: 5,
} as const;

export const MessageType = {
  USER: 1,
  BOT: 2,
} as const;

export const MessageState = {
  NEW: 0,
  GENERATING: 1,
  FINISH: 2,
} as const;

export type MessageItem = {
  type?: number;
  text_item?: {
    text?: string;
  };
  voice_item?: {
    text?: string;
  };
  ref_msg?: {
    title?: string;
    message_item?: MessageItem;
  };
};

export type WeixinMessage = {
  seq?: number;
  message_id?: number | string;
  from_user_id?: string;
  to_user_id?: string;
  client_id?: string;
  create_time_ms?: number;
  context_token?: string;
  item_list?: MessageItem[];
};

export type GetUpdatesResponse = {
  ret?: number;
  errcode?: number;
  errmsg?: string;
  msgs?: WeixinMessage[];
  get_updates_buf?: string;
  longpolling_timeout_ms?: number;
};

export type WeixinApiResponse = {
  ret?: number;
  errcode?: number;
  errmsg?: string;
};
