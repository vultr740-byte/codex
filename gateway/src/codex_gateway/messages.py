from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    chat_id: int
    message_id: int
    user_id: int | None
    text: str
    is_group: bool
    command: str | None = None
    command_args: str = ""


def normalize_user_text(text: str) -> str:
    return text.strip()
