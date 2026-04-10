from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from codex_app_server.client import AppServerClient, AppServerConfig
from codex_app_server.generated.v2_all import (
    AgentMessageDeltaNotification,
    AskForApproval,
    ItemCompletedNotification,
    SandboxMode,
    TurnCompletedNotification,
)

logger = logging.getLogger(__name__)


class ApprovalNotAllowedError(RuntimeError):
    pass


def _deny_approval(method: str, _params: dict[str, object] | None) -> dict[str, str]:
    if method.endswith("/requestApproval"):
        raise ApprovalNotAllowedError(f"unexpected approval request: {method}")
    return {}


def _assistant_text_from_turn(turn: object | None) -> str:
    if turn is None:
        return ""
    chunks: list[str] = []
    for item in getattr(turn, "items", []) or []:
        raw_item = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if isinstance(raw_item, dict) and "root" in raw_item and isinstance(raw_item["root"], dict):
            raw_item = raw_item["root"]
        if not isinstance(raw_item, dict):
            continue
        if raw_item.get("type") == "agentMessage":
            text = raw_item.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)
            continue
        if raw_item.get("type") != "message" or raw_item.get("role") != "assistant":
            continue
        for content in raw_item.get("content") or []:
            raw_content = content
            if isinstance(raw_content, dict) and "root" in raw_content and isinstance(raw_content["root"], dict):
                raw_content = raw_content["root"]
            if isinstance(raw_content, dict) and raw_content.get("type") == "output_text":
                text = raw_content.get("text")
                if isinstance(text, str) and text:
                    chunks.append(text)
    return "".join(chunks)


def _assistant_text_from_thread_item(item: object | None) -> str:
    if item is None:
        return ""
    raw_item = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    if isinstance(raw_item, dict) and "root" in raw_item and isinstance(raw_item["root"], dict):
        raw_item = raw_item["root"]
    if not isinstance(raw_item, dict):
        return ""
    if raw_item.get("type") == "agentMessage":
        text = raw_item.get("text")
        return text if isinstance(text, str) else ""
    if raw_item.get("type") == "message" and raw_item.get("role") == "assistant":
        chunks: list[str] = []
        for content in raw_item.get("content") or []:
            raw_content = content
            if isinstance(raw_content, dict) and "root" in raw_content and isinstance(raw_content["root"], dict):
                raw_content = raw_content["root"]
            if isinstance(raw_content, dict) and raw_content.get("type") == "output_text":
                text = raw_content.get("text")
                if isinstance(text, str) and text:
                    chunks.append(text)
        return "".join(chunks)
    return ""


@dataclass(frozen=True)
class CodexTurnResult:
    thread_id: str
    final_text: str


class CodexRunner:
    def __init__(
        self,
        app_server_config: AppServerConfig,
        *,
        model: str,
        approval_policy: str,
        sandbox_mode: str,
    ) -> None:
        self._app_server_config = app_server_config
        self._model = model
        self._approval_policy = AskForApproval.model_validate(approval_policy)
        self._sandbox_mode = SandboxMode(sandbox_mode)

    def run_turn(
        self,
        *,
        thread_id: str | None,
        prompt: str,
        on_delta: Callable[[str], None],
    ) -> CodexTurnResult:
        client = AppServerClient(
            config=self._app_server_config,
            approval_handler=_deny_approval,
        )
        try:
            client.start()
            client.initialize()
            if thread_id is None:
                started = client.thread_start(
                    {
                        "model": self._model,
                        "cwd": self._app_server_config.cwd,
                        "approvalPolicy": self._approval_policy.root.value,
                        "sandbox": self._sandbox_mode.value,
                    }
                )
                active_thread_id = started.thread.id
            else:
                resumed = client.thread_resume(
                    thread_id,
                    {
                        "threadId": thread_id,
                        "model": self._model,
                        "cwd": self._app_server_config.cwd,
                        "approvalPolicy": self._approval_policy.root.value,
                        "sandbox": self._sandbox_mode.value,
                    },
                )
                active_thread_id = resumed.thread.id

            started_turn = client.turn_start(
                active_thread_id,
                [{"type": "text", "text": prompt}],
                params={
                    "threadId": active_thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "approvalPolicy": self._approval_policy.root.value,
                    "cwd": self._app_server_config.cwd,
                    "model": self._model,
                },
            )

            turn_id = started_turn.turn.id
            final_chunks: list[str] = []
            saw_completed = False
            while True:
                event = client.next_notification()
                payload = event.payload
                if isinstance(payload, AgentMessageDeltaNotification) and payload.turn_id == turn_id:
                    delta = payload.delta or ""
                    if delta:
                        final_chunks.append(delta)
                        on_delta(delta)
                    continue
                if isinstance(payload, ItemCompletedNotification) and payload.turn_id == turn_id:
                    item_text = _assistant_text_from_thread_item(payload.item).strip()
                    if item_text and not final_chunks:
                        final_chunks.append(item_text)
                    continue
                if isinstance(payload, TurnCompletedNotification) and payload.turn.id == turn_id:
                    saw_completed = True
                    break

            if not saw_completed:
                raise RuntimeError("turn did not complete")

            final_text = "".join(final_chunks).strip()
            if not final_text:
                persisted = client.thread_read(active_thread_id, include_turns=True)
                matched_turn = next(
                    (turn for turn in persisted.thread.turns or [] if getattr(turn, "id", None) == turn_id),
                    None,
                )
                final_text = _assistant_text_from_turn(matched_turn).strip()
                if not final_text and matched_turn is not None:
                    logger.info(
                        "empty assistant text fallback thread_id=%s turn_id=%s items=%s",
                        active_thread_id,
                        turn_id,
                        [
                            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                            for item in getattr(matched_turn, "items", []) or []
                        ],
                    )

            return CodexTurnResult(
                thread_id=active_thread_id,
                final_text=final_text or "[no assistant text]",
            )
        finally:
            client.close()
