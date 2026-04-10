from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .messages import InboundMessage, normalize_user_text


@dataclass(frozen=True)
class TelegramUpdate:
    update_id: int
    message: InboundMessage


class TelegramApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, description: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.description = description


class TelegramApi:
    def __init__(self, token: str) -> None:
        self._base = f"https://api.telegram.org/bot{token}"

    def get_updates(self, *, offset: int | None, timeout: int = 20) -> list[TelegramUpdate]:
        query = {"timeout": str(timeout)}
        if offset is not None:
            query["offset"] = str(offset)
        payload = self._request_json("getUpdates", query=query)
        updates: list[TelegramUpdate] = []
        for item in payload:
            parsed = self._parse_update(item)
            if parsed is not None:
                updates.append(parsed)
        return updates

    def send_message(self, *, chat_id: int, text: str) -> int:
        payload = self._request_json(
            "sendMessage",
            data={"chat_id": chat_id, "text": text},
        )
        return int(payload["message_id"])

    def send_chat_action(self, *, chat_id: int, action: str) -> None:
        self._request_json(
            "sendChatAction",
            data={"chat_id": chat_id, "action": action},
        )

    def edit_message_text(self, *, chat_id: int, message_id: int, text: str) -> None:
        self._request_json(
            "editMessageText",
            data={"chat_id": chat_id, "message_id": message_id, "text": text},
        )

    def _request_json(
        self,
        method: str,
        *,
        query: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base}/{method}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        request_data = None
        headers: dict[str, str] = {}
        if data is not None:
            request_data = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=request_data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = None
            try:
                raw = exc.read().decode("utf-8")
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    detail = parsed.get("description")
            except Exception:
                detail = None
            message = f"telegram {method} failed with status {exc.code}"
            if detail:
                message = f"{message}: {detail}"
            raise TelegramApiError(message, status_code=exc.code, description=detail) from exc
        except urllib.error.URLError as exc:
            raise TelegramApiError(f"telegram {method} failed: {exc.reason}") from exc

        if not body.get("ok"):
            description = body.get("description") if isinstance(body, dict) else None
            message = f"telegram {method} failed: {body}"
            raise TelegramApiError(message, description=description)
        return body["result"]

    def _parse_update(self, item: dict[str, Any]) -> TelegramUpdate | None:
        message = item.get("message")
        if not isinstance(message, dict):
            return None
        text = message.get("text")
        if not isinstance(text, str):
            return None
        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        if not isinstance(chat_id, int) or not isinstance(message_id, int):
            return None
        inbound = InboundMessage(
            channel="telegram",
            chat_id=chat_id,
            message_id=message_id,
            user_id=from_user.get("id") if isinstance(from_user.get("id"), int) else None,
            text=normalize_user_text(text),
            is_group=chat.get("type") in {"group", "supergroup"},
        )
        return TelegramUpdate(update_id=int(item["update_id"]), message=inbound)
