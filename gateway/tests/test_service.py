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
        self.commands: list[list[dict[str, str]]] = []
        self.get_me_calls = 0

    def send_message(self, *, chat_id: int, text: str) -> int:
        self.sent.append((chat_id, text))
        return len(self.sent)

    def send_chat_action(self, *, chat_id: int, action: str) -> None:
        self.actions.append((chat_id, action))

    def edit_message_text(self, *, chat_id: int, message_id: int, text: str) -> None:
        self.edited.append((chat_id, message_id, text))

    def get_me(self) -> dict[str, str]:
        self.get_me_calls += 1
        return {"username": "xiasou_pico_bot"}

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        self.commands.append(commands)


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str]] = []
        self.new_thread_calls = 0
        self.compact_calls: list[str] = []
        self.fork_calls: list[str] = []
        self.rename_calls: list[tuple[str, str]] = []
        self.read_calls: list[str] = []
        self.list_threads_calls = 0
        self.list_models_calls = 0

    def run_turn(self, *, thread_id: str | None, prompt: str, on_delta):  # type: ignore[no-untyped-def]
        self.calls.append((thread_id, prompt))
        on_delta("hello ")
        on_delta("world")
        from codex_gateway.codex_runner import CodexTurnResult

        return CodexTurnResult(thread_id="thr_1", final_text="hello world")

    def new_thread(self):  # type: ignore[no-untyped-def]
        from codex_gateway.codex_runner import CodexThreadInfo

        self.new_thread_calls += 1
        return CodexThreadInfo(thread_id="thr_new", name=None, preview="", cwd="/tmp")

    def compact_thread(self, *, thread_id: str) -> None:
        self.compact_calls.append(thread_id)

    def fork_thread(self, *, thread_id: str):  # type: ignore[no-untyped-def]
        from codex_gateway.codex_runner import CodexThreadInfo

        self.fork_calls.append(thread_id)
        return CodexThreadInfo(thread_id="thr_fork", name=None, preview="forked", cwd="/tmp")

    def rename_thread(self, *, thread_id: str, name: str) -> None:
        self.rename_calls.append((thread_id, name))

    def read_thread(self, *, thread_id: str):  # type: ignore[no-untyped-def]
        from codex_gateway.codex_runner import CodexThreadInfo

        self.read_calls.append(thread_id)
        return CodexThreadInfo(thread_id=thread_id, name="demo", preview="hello", cwd="/tmp")

    def list_threads(self, *, limit: int = 10):  # type: ignore[no-untyped-def]
        from codex_gateway.codex_runner import CodexThreadInfo

        self.list_threads_calls += 1
        return [
            CodexThreadInfo(thread_id="thr_resume", name="Resume Demo", preview="cloudflare", cwd="/tmp"),
            CodexThreadInfo(thread_id="thr_other", name="Other", preview="other", cwd="/tmp"),
        ]

    def list_models(self) -> list[str]:
        self.list_models_calls += 1
        return ["gpt-5.2-codex", "gpt-5.4"]


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
        codex_sandbox_mode="danger-full-access",
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


def test_run_forever_registers_telegram_commands_before_polling(tmp_path: Path) -> None:
    service = GatewayService(_config(tmp_path))
    bridge = FakeBridge()
    telegram = FakeTelegram()
    service._bridge = bridge  # type: ignore[attr-defined]
    service._telegram = telegram  # type: ignore[attr-defined]

    def stop_after_first_poll(*, offset, timeout=20):  # type: ignore[no-untyped-def]
        raise KeyboardInterrupt()

    telegram.get_updates = stop_after_first_poll  # type: ignore[method-assign]

    try:
        service.run_forever()
    except KeyboardInterrupt:
        pass

    assert bridge.started is True
    assert telegram.get_me_calls == 1
    assert telegram.commands
    assert any(command["command"] == "new" for command in telegram.commands[0])


def test_service_processes_new_command_without_running_turn(tmp_path: Path) -> None:
    service = GatewayService(_config(tmp_path))
    service._bridge = FakeBridge()  # type: ignore[attr-defined]
    telegram = FakeTelegram()
    runner = FakeRunner()
    service._telegram = telegram  # type: ignore[attr-defined]
    service._codex_runner = runner  # type: ignore[attr-defined]

    service.process_message_for_test(
        InboundMessage(
            channel="telegram",
            chat_id=1,
            message_id=101,
            user_id=200,
            text="/new",
            is_group=False,
            command="new",
        ),
    )

    session = service._db.get_session(channel="telegram", external_chat_id="1")  # type: ignore[attr-defined]
    assert session is not None
    assert session.codex_thread_id == "thr_new"
    assert runner.new_thread_calls == 1
    assert runner.calls == []
    assert telegram.sent == [(1, "Started a new thread.\nthread_id: thr_new")]


def test_service_processes_status_command(tmp_path: Path) -> None:
    service = GatewayService(_config(tmp_path))
    service._bridge = FakeBridge()  # type: ignore[attr-defined]
    telegram = FakeTelegram()
    runner = FakeRunner()
    service._telegram = telegram  # type: ignore[attr-defined]
    service._codex_runner = runner  # type: ignore[attr-defined]
    service._db.save_session(channel="telegram", external_chat_id="1", codex_thread_id="thr_1")  # type: ignore[attr-defined]

    service.process_message_for_test(
        InboundMessage(
            channel="telegram",
            chat_id=1,
            message_id=102,
            user_id=200,
            text="/status",
            is_group=False,
            command="status",
        ),
    )

    assert runner.read_calls == ["thr_1"]
    assert "thread_id: thr_1" in telegram.sent[0][1]


def test_service_returns_unsupported_message_for_tui_only_command(tmp_path: Path) -> None:
    service = GatewayService(_config(tmp_path))
    service._bridge = FakeBridge()  # type: ignore[attr-defined]
    telegram = FakeTelegram()
    runner = FakeRunner()
    service._telegram = telegram  # type: ignore[attr-defined]
    service._codex_runner = runner  # type: ignore[attr-defined]

    service.process_message_for_test(
        InboundMessage(
            channel="telegram",
            chat_id=1,
            message_id=103,
            user_id=200,
            text="/theme",
            is_group=False,
            command="theme",
        ),
    )

    assert runner.calls == []
    assert telegram.sent == [
        (
            1,
            "/theme is registered for parity with Codex slash commands,\n"
            "but this command is currently TUI-specific or not yet implemented for Telegram.",
        )
    ]


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
