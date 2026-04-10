from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

from codex_gateway.config import GatewayConfig, TelegramConfig
from codex_gateway.messages import InboundMessage
from codex_gateway.service import GatewayService


class FakeBridge:
    def __init__(self) -> None:
        self.local_base_url = "http://127.0.0.1:8765/v1"
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.edited: list[tuple[int, int, str]] = []
        self.actions: list[tuple[int, str]] = []

    def send_message(self, *, chat_id: int, text: str) -> int:
        self.sent.append((chat_id, text))
        return len(self.sent)

    def send_chat_action(self, *, chat_id: int, action: str) -> None:
        self.actions.append((chat_id, action))

    def edit_message_text(self, *, chat_id: int, message_id: int, text: str) -> None:
        self.edited.append((chat_id, message_id, text))


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str]] = []

    def run_turn(self, *, thread_id: str | None, prompt: str, on_delta):  # type: ignore[no-untyped-def]
        self.calls.append((thread_id, prompt))
        on_delta("hello ")
        on_delta("world")
        from codex_gateway.codex_runner import CodexTurnResult

        return CodexTurnResult(thread_id="thr_1", final_text="hello world")


def _config(tmp_path: Path) -> GatewayConfig:
    return GatewayConfig(
        data_dir=tmp_path,
        sqlite_path=tmp_path / "gateway.sqlite",
        codex_home=tmp_path / "codex-home",
        codex_working_dir=tmp_path,
        openai_api_key="key",
        openai_base_url="https://example.test/v1",
        codex_model="gpt-5.2-codex",
        codex_approval_policy="never",
        codex_sandbox_mode="workspace-write",
        codex_bin=None,
        telegram=TelegramConfig(
            bot_token="token",
            poll_interval_ms=1000,
            allowed_chat_ids=set(),
            allowed_user_ids=set(),
            require_mention=False,
            streaming_enabled=True,
        ),
    )


def test_service_processes_message_and_persists_thread(tmp_path: Path) -> None:
    service = GatewayService(_config(tmp_path))
    service._bridge = FakeBridge()  # type: ignore[attr-defined]
    service._telegram = FakeTelegram()  # type: ignore[attr-defined]
    service._codex_runner = FakeRunner()  # type: ignore[attr-defined]

    service.process_message_for_test(
        InboundMessage(
            channel="telegram",
            chat_id=1,
            message_id=100,
            user_id=200,
            text="fix the bug",
            is_group=False,
        ),
    )

    session = service._db.get_session(channel="telegram", external_chat_id="1")  # type: ignore[attr-defined]
    assert session is not None
    assert session.codex_thread_id == "thr_1"
    assert service._telegram.actions  # type: ignore[attr-defined]
    assert service._telegram.actions[0] == (1, "typing")  # type: ignore[attr-defined]
    assert service._telegram.sent  # type: ignore[attr-defined]


def test_service_honors_allowlist(tmp_path: Path) -> None:
    config = replace(
        _config(tmp_path),
        telegram=replace(_config(tmp_path).telegram, allowed_chat_ids={999}),
    )
    service = GatewayService(config)
    assert service._is_allowed(  # type: ignore[attr-defined]
        InboundMessage(
            channel="telegram",
            chat_id=1,
            message_id=100,
            user_id=200,
            text="hello",
            is_group=False,
        )
    ) is False


def test_service_stop_shuts_down_bridge(tmp_path: Path) -> None:
    service = GatewayService(_config(tmp_path))
    service._bridge = FakeBridge()  # type: ignore[attr-defined]
    service.stop()
    assert service._bridge.stopped is True  # type: ignore[attr-defined]


def test_finalize_stream_does_not_send_duplicate_message_when_text_is_unchanged(tmp_path: Path) -> None:
    service = GatewayService(_config(tmp_path))
    telegram = FakeTelegram()
    service._telegram = telegram  # type: ignore[attr-defined]

    from codex_gateway.service import TelegramStreamLoop, _new_stream_state

    state = _new_stream_state()
    state.sent_message_id = 1
    state.content = "pong"
    state.rendered_text = "pong"
    loop = TelegramStreamLoop(telegram, chat_id=1, state=state)
    loop.start()
    service._finalize_stream("pong", state, loop)  # type: ignore[attr-defined]

    assert telegram.sent == []
    assert telegram.edited == []


def test_stream_loop_sends_initial_token_immediately(tmp_path: Path) -> None:
    service = GatewayService(_config(tmp_path))
    telegram = FakeTelegram()
    service._telegram = telegram  # type: ignore[attr-defined]

    from codex_gateway.service import TelegramStreamLoop, _new_stream_state

    state = _new_stream_state()
    loop = TelegramStreamLoop(telegram, chat_id=1, state=state)
    loop.start()
    service._flush_stream_delta("你", state, loop)  # type: ignore[attr-defined]
    loop.stop(final_text="你")

    assert telegram.sent == [(1, "你")]


def test_stream_loop_coalesces_small_updates_before_edit(tmp_path: Path) -> None:
    service = GatewayService(_config(tmp_path))
    telegram = FakeTelegram()
    service._telegram = telegram  # type: ignore[attr-defined]

    from codex_gateway.service import TelegramStreamLoop, _new_stream_state

    state = _new_stream_state()
    loop = TelegramStreamLoop(telegram, chat_id=1, state=state)
    loop.start()

    service._flush_stream_delta("h", state, loop)  # type: ignore[attr-defined]
    time.sleep(0.02)
    service._flush_stream_delta("e", state, loop)  # type: ignore[attr-defined]
    time.sleep(0.02)
    service._flush_stream_delta("l", state, loop)  # type: ignore[attr-defined]
    time.sleep(0.02)
    service._flush_stream_delta("l", state, loop)  # type: ignore[attr-defined]
    time.sleep(0.02)
    service._flush_stream_delta("o", state, loop)  # type: ignore[attr-defined]
    time.sleep(0.05)
    service._flush_stream_delta("!", state, loop)  # type: ignore[attr-defined]
    loop.stop(final_text="hello!")

    assert telegram.sent
    assert telegram.sent[0] == (1, "h")
    assert telegram.edited[-1] == (1, 1, "hello!")


def test_service_sends_one_final_message_when_streaming_disabled(tmp_path: Path) -> None:
    config = replace(
        _config(tmp_path),
        telegram=replace(_config(tmp_path).telegram, streaming_enabled=False),
    )
    service = GatewayService(config)
    service._bridge = FakeBridge()  # type: ignore[attr-defined]
    telegram = FakeTelegram()
    service._telegram = telegram  # type: ignore[attr-defined]
    service._codex_runner = FakeRunner()  # type: ignore[attr-defined]

    service.process_message_for_test(
        InboundMessage(
            channel="telegram",
            chat_id=1,
            message_id=100,
            user_id=200,
            text="fix the bug",
            is_group=False,
        ),
    )

    assert telegram.actions
    assert telegram.sent == [(1, "hello world")]
    assert telegram.edited == []
