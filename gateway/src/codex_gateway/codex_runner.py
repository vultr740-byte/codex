from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from codex_app_server.client import AppServerClient, AppServerConfig
from codex_app_server.generated.v2_all import (
    AgentMessageDeltaNotification,
    AskForApproval,
    ItemCompletedNotification,
    ReasoningEffort,
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


@dataclass(frozen=True)
class CodexThreadInfo:
    thread_id: str
    name: str | None
    preview: str
    cwd: str
    model: str | None = None


@dataclass(frozen=True)
class CodexModelInfo:
    id: str
    default_reasoning_effort: str
    supported_reasoning_efforts: list[str]


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
        model: str | None = None,
        effort: str | None = None,
    ) -> CodexTurnResult:
        effective_model = model or self._model
        effective_effort = ReasoningEffort(effort) if effort else None
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
                        "model": effective_model,
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
                        "model": effective_model,
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
                    "model": effective_model,
                    "effort": effective_effort.value if effective_effort else None,
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

    def new_thread(self, *, model: str | None = None) -> CodexThreadInfo:
        effective_model = model or self._model
        client = AppServerClient(
            config=self._app_server_config,
            approval_handler=_deny_approval,
        )
        try:
            client.start()
            client.initialize()
            started = client.thread_start(
                {
                    "model": effective_model,
                    "cwd": self._app_server_config.cwd,
                    "approvalPolicy": self._approval_policy.root.value,
                    "sandbox": self._sandbox_mode.value,
                }
            )
            return CodexThreadInfo(
                thread_id=started.thread.id,
                name=started.thread.name,
                preview=started.thread.preview,
                cwd=started.thread.cwd,
                model=started.model,
            )
        finally:
            client.close()

    def compact_thread(self, *, thread_id: str) -> None:
        client = AppServerClient(
            config=self._app_server_config,
            approval_handler=_deny_approval,
        )
        try:
            client.start()
            client.initialize()
            client.thread_compact(thread_id)
        finally:
            client.close()

    def fork_thread(self, *, thread_id: str, model: str | None = None) -> CodexThreadInfo:
        effective_model = model or self._model
        client = AppServerClient(
            config=self._app_server_config,
            approval_handler=_deny_approval,
        )
        try:
            client.start()
            client.initialize()
            forked = client.thread_fork(
                thread_id,
                {
                    "threadId": thread_id,
                    "model": effective_model,
                    "cwd": self._app_server_config.cwd,
                    "approvalPolicy": self._approval_policy.root.value,
                    "sandbox": self._sandbox_mode.value,
                },
            )
            return CodexThreadInfo(
                thread_id=forked.thread.id,
                name=forked.thread.name,
                preview=forked.thread.preview,
                cwd=forked.thread.cwd,
                model=forked.model,
            )
        finally:
            client.close()

    def rename_thread(self, *, thread_id: str, name: str) -> None:
        client = AppServerClient(
            config=self._app_server_config,
            approval_handler=_deny_approval,
        )
        try:
            client.start()
            client.initialize()
            client.thread_set_name(thread_id, name)
        finally:
            client.close()

    def read_thread(self, *, thread_id: str) -> CodexThreadInfo:
        client = AppServerClient(
            config=self._app_server_config,
            approval_handler=_deny_approval,
        )
        try:
            client.start()
            client.initialize()
            thread = client.thread_read(thread_id, include_turns=False).thread
            return CodexThreadInfo(
                thread_id=thread.id,
                name=thread.name,
                preview=thread.preview,
                cwd=thread.cwd,
            )
        finally:
            client.close()

    def list_threads(self, *, limit: int = 10) -> list[CodexThreadInfo]:
        client = AppServerClient(
            config=self._app_server_config,
            approval_handler=_deny_approval,
        )
        try:
            client.start()
            client.initialize()
            response = client.thread_list({"limit": limit, "cwd": self._app_server_config.cwd})
            return [
                CodexThreadInfo(
                    thread_id=thread.id,
                    name=thread.name,
                    preview=thread.preview,
                    cwd=thread.cwd,
                )
                for thread in response.data
            ]
        finally:
            client.close()

    def list_models(self) -> list[CodexModelInfo]:
        client = AppServerClient(
            config=self._app_server_config,
            approval_handler=_deny_approval,
        )
        try:
            client.start()
            client.initialize()
            models = client.model_list(include_hidden=False).data
            return [
                CodexModelInfo(
                    id=model.id,
                    default_reasoning_effort=model.default_reasoning_effort.value,
                    supported_reasoning_efforts=[
                        option.reasoning_effort.value for option in model.supported_reasoning_efforts
                    ],
                )
                for model in models
            ]
        finally:
            client.close()
