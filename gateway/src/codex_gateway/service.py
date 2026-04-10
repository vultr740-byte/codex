from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
import textwrap

from .codex_runner import ApprovalNotAllowedError, CodexRunner
from .config import GatewayConfig
from .db import GatewayDb
from .messages import InboundMessage
from .responses_bridge import ResponsesBridge
from .slash_commands import registered_telegram_commands
from .telegram_api import TelegramApi, TelegramApiError

logger = logging.getLogger(__name__)

_STREAM_INITIAL_FLUSH_CHARS = 1
_STREAM_MIN_FLUSH_INTERVAL_SECONDS = 0.25
_STREAM_MIN_CHARS_BETWEEN_FLUSHES = 12


@dataclass
class StreamState:
    sent_message_id: int | None = None
    content: str = ""
    rendered_text: str = ""
    pending_text: str = ""
    final_text: str | None = None
    stop_requested: bool = False
    in_flight: bool = False
    last_sent_monotonic: float = 0.0
    lock: threading.Lock | None = None
    wake_event: threading.Event | None = None
    worker: threading.Thread | None = None


def _new_stream_state() -> StreamState:
    return StreamState(
        lock=threading.Lock(),
        wake_event=threading.Event(),
    )


class TypingIndicator:
    def __init__(self, telegram: TelegramApi, *, chat_id: int, interval_seconds: float = 4.0) -> None:
        self._telegram = telegram
        self._chat_id = chat_id
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=f"typing-{self._chat_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._telegram.send_chat_action(chat_id=self._chat_id, action="typing")
            except TelegramApiError:
                logger.exception("failed to send telegram typing action")
            if self._stop_event.wait(self._interval_seconds):
                return


class TelegramStreamLoop:
    def __init__(self, telegram: TelegramApi, *, chat_id: int, state: StreamState) -> None:
        self._telegram = telegram
        self._chat_id = chat_id
        self._state = state
        if state.lock is None or state.wake_event is None:
            raise ValueError("stream state must be initialized with lock and wake_event")

    def start(self) -> None:
        if self._state.worker is not None:
            return
        self._state.worker = threading.Thread(
            target=self._run,
            name=f"tg-stream-{self._chat_id}",
            daemon=True,
        )
        self._state.worker.start()

    def wake(self) -> None:
        self._state.wake_event.set()  # type: ignore[union-attr]

    def stop(self, *, final_text: str | None = None) -> None:
        with self._state.lock:  # type: ignore[arg-type]
            self._state.final_text = final_text
            self._state.stop_requested = True
        self.wake()
        if self._state.worker is not None:
            self._state.worker.join(timeout=10)
            self._state.worker = None

    def _run(self) -> None:
        while True:
            now = time.monotonic()
            with self._state.lock:  # type: ignore[arg-type]
                target_text = self._compute_target_text_locked()
                should_stop = self._state.stop_requested and not self._has_unsent_text_locked(target_text)
                time_since_last = now - self._state.last_sent_monotonic
                chars_since_last = len(target_text) - len(self._state.rendered_text)
                should_send = self._should_send_locked(
                    target_text=target_text,
                    time_since_last=time_since_last,
                    chars_since_last=chars_since_last,
                )
                if should_send:
                    self._state.in_flight = True
                wait_timeout = None
                if not should_send and not should_stop and target_text:
                    wait_timeout = max(0.0, _STREAM_MIN_FLUSH_INTERVAL_SECONDS - time_since_last)

            if should_stop:
                return

            if not should_send:
                self._state.wake_event.wait(wait_timeout)  # type: ignore[union-attr]
                self._state.wake_event.clear()  # type: ignore[union-attr]
                continue

            try:
                self._send_text(target_text)
                sent_ok = True
            except TelegramApiError:
                logger.exception("failed to send telegram stream update")
                sent_ok = False

            with self._state.lock:  # type: ignore[arg-type]
                self._state.in_flight = False
                if sent_ok:
                    self._state.rendered_text = target_text
                    self._state.last_sent_monotonic = time.monotonic()
                if self._state.stop_requested and not self._has_unsent_text_locked(self._compute_target_text_locked()):
                    return

    def _compute_target_text_locked(self) -> str:
        if self._state.final_text is not None:
            return self._state.final_text.strip() or self._state.content.strip() or "[no assistant text]"
        return self._state.pending_text

    def _has_unsent_text_locked(self, target_text: str) -> bool:
        return bool(target_text) and target_text != self._state.rendered_text

    def _should_send_locked(self, *, target_text: str, time_since_last: float, chars_since_last: int) -> bool:
        if self._state.in_flight:
            return False
        if not target_text:
            return False
        if target_text == self._state.rendered_text:
            return False
        if self._state.final_text is not None:
            return True
        if self._state.sent_message_id is None:
            return len(target_text) >= _STREAM_INITIAL_FLUSH_CHARS
        return (
            time_since_last >= _STREAM_MIN_FLUSH_INTERVAL_SECONDS
            or chars_since_last >= _STREAM_MIN_CHARS_BETWEEN_FLUSHES
        )

    def _send_text(self, text: str) -> None:
        if self._state.sent_message_id is None:
            self._state.sent_message_id = self._telegram.send_message(chat_id=self._chat_id, text=text)
            return
        self._telegram.edit_message_text(
            chat_id=self._chat_id,
            message_id=self._state.sent_message_id,
            text=text,
        )


class GatewayService:
    def __init__(self, config: GatewayConfig) -> None:
        self._config = config
        self._db = GatewayDb(config.sqlite_path)
        self._locks: dict[int, threading.Lock] = {}
        self._bridge = ResponsesBridge(config.bridge_config())
        self._codex_runner = CodexRunner(
            config.app_server_config(openai_base_url=self._bridge.local_base_url),
            model=config.codex_model,
            approval_policy=config.codex_approval_policy,
            sandbox_mode=config.codex_sandbox_mode,
        )
        self._telegram = TelegramApi(config.telegram.bot_token)

    def run_forever(self) -> None:
        update_offset: int | None = None
        self._bridge.start()
        logger.info("responses bridge started base_url=%s upstream=%s", self._bridge.local_base_url, self._config.openai_base_url)
        self._register_telegram_commands()
        logger.info("telegram polling started")
        while True:
            try:
                updates = self._telegram.get_updates(offset=update_offset, timeout=20)
                for update in updates:
                    update_offset = update.update_id + 1
                    self._handle_telegram_message(update.message)
            except TelegramApiError:
                logger.exception("telegram polling failed")
                time.sleep(3)
            except Exception:
                logger.exception("gateway loop failed")
                time.sleep(3)

    def _handle_telegram_message(self, message: InboundMessage) -> None:
        if not message.text:
            return
        if not self._is_allowed(message):
            logger.info("skipping unauthorized telegram message chat_id=%s user_id=%s", message.chat_id, message.user_id)
            return
        if self._config.telegram.require_mention and message.is_group and "@codex" not in message.text.lower():
            return
        if self._db.seen_message(
            channel="telegram",
            external_chat_id=str(message.chat_id),
            external_message_id=str(message.message_id),
        ):
            return

        lock = self._locks.setdefault(message.chat_id, threading.Lock())
        thread = threading.Thread(target=self._process_message, args=(lock, message), daemon=True)
        thread.start()

    def _process_message(self, lock: threading.Lock, message: InboundMessage) -> None:
        with lock:
            if message.command:
                self._process_command_message(message)
                return
            started_at = time.monotonic()
            first_delta_at: float | None = None
            delta_count = 0
            delta_chars = 0
            typing_indicator = TypingIndicator(self._telegram, chat_id=message.chat_id)
            typing_indicator.start()
            stream_state: StreamState | None = None
            stream_loop: TelegramStreamLoop | None = None
            if self._config.telegram.streaming_enabled:
                stream_state = _new_stream_state()
                stream_loop = TelegramStreamLoop(self._telegram, chat_id=message.chat_id, state=stream_state)
                stream_loop.start()
            try:
                session = self._db.get_session(channel="telegram", external_chat_id=str(message.chat_id))

                def on_delta(delta: str) -> None:
                    nonlocal delta_chars, delta_count, first_delta_at
                    delta_count += 1
                    delta_chars += len(delta)
                    if first_delta_at is None:
                        first_delta_at = time.monotonic()
                        logger.info(
                            "first assistant delta chat_id=%s elapsed=%.3fs streaming=%s",
                            message.chat_id,
                            first_delta_at - started_at,
                            self._config.telegram.streaming_enabled,
                        )
                    if stream_state is not None and stream_loop is not None:
                        self._flush_stream_delta(delta, stream_state, stream_loop)

                result = self._codex_runner.run_turn(
                    thread_id=session.codex_thread_id if session else None,
                    prompt=message.text,
                    on_delta=on_delta,
                )
                completed_at = time.monotonic()
                logger.info(
                    "codex turn completed chat_id=%s elapsed=%.3fs delta_count=%s delta_chars=%s streaming=%s",
                    message.chat_id,
                    completed_at - started_at,
                    delta_count,
                    delta_chars,
                    self._config.telegram.streaming_enabled,
                )
                self._db.save_session(
                    channel="telegram",
                    external_chat_id=str(message.chat_id),
                    codex_thread_id=result.thread_id,
                )
                if stream_state is not None and stream_loop is not None:
                    self._finalize_stream(result.final_text, stream_state, stream_loop)
                else:
                    self._send_final_message(chat_id=message.chat_id, text=result.final_text)
                    logger.info(
                        "non-streaming reply sent chat_id=%s elapsed=%.3fs",
                        message.chat_id,
                        time.monotonic() - started_at,
                    )
            except ApprovalNotAllowedError:
                logger.exception("unexpected approval request")
                if stream_loop is not None:
                    stream_loop.stop()
                self._telegram.send_message(
                    chat_id=message.chat_id,
                    text="This deployment is configured for non-interactive execution only. The task requested approval and was stopped.",
                )
            except Exception:
                logger.exception("failed to process telegram message")
                if stream_loop is not None:
                    stream_loop.stop()
                self._telegram.send_message(
                    chat_id=message.chat_id,
                    text="Codex failed to process this message.",
                )
            finally:
                if stream_state is not None and stream_state.worker is not None and stream_loop is not None:
                    stream_loop.stop()
                typing_indicator.stop()

    def process_message_for_test(self, message: InboundMessage) -> None:
        lock = self._locks.setdefault(message.chat_id, threading.Lock())
        self._process_message(lock, message)

    def stop(self) -> None:
        self._bridge.stop()

    def _flush_stream_delta(self, delta: str, state: StreamState, stream_loop: TelegramStreamLoop) -> None:
        with state.lock:  # type: ignore[arg-type]
            state.content += delta
            state.pending_text = state.content.strip() or "..."
        stream_loop.wake()

    def _finalize_stream(self, final_text: str, state: StreamState, stream_loop: TelegramStreamLoop) -> None:
        stream_loop.stop(final_text=final_text)

    def _send_final_message(self, *, chat_id: int, text: str) -> None:
        final_text = text.strip() or "[no assistant text]"
        self._telegram.send_message(chat_id=chat_id, text=final_text)

    def _register_telegram_commands(self) -> None:
        try:
            self._telegram.get_me()
            self._telegram.set_my_commands(registered_telegram_commands())
        except TelegramApiError:
            logger.exception("failed to register telegram commands")

    def _process_command_message(self, message: InboundMessage) -> None:
        command = message.command or ""
        args = message.command_args.strip()
        try:
            if command == "new":
                thread = self._codex_runner.new_thread()
                self._db.save_session(
                    channel="telegram",
                    external_chat_id=str(message.chat_id),
                    codex_thread_id=thread.thread_id,
                )
                self._telegram.send_message(
                    chat_id=message.chat_id,
                    text=f"Started a new thread.\nthread_id: {thread.thread_id}",
                )
                return

            session = self._db.get_session(channel="telegram", external_chat_id=str(message.chat_id))
            if command in {"compact", "fork", "rename", "status"} and session is None:
                self._telegram.send_message(
                    chat_id=message.chat_id,
                    text="No active thread for this chat yet. Send /new or a normal message first.",
                )
                return

            if command == "compact":
                self._codex_runner.compact_thread(thread_id=session.codex_thread_id)  # type: ignore[union-attr]
                self._telegram.send_message(chat_id=message.chat_id, text="Started compaction for the current thread.")
                return

            if command == "fork":
                forked = self._codex_runner.fork_thread(thread_id=session.codex_thread_id)  # type: ignore[union-attr]
                self._db.save_session(
                    channel="telegram",
                    external_chat_id=str(message.chat_id),
                    codex_thread_id=forked.thread_id,
                )
                self._telegram.send_message(
                    chat_id=message.chat_id,
                    text=f"Forked the current thread.\nthread_id: {forked.thread_id}",
                )
                return

            if command == "rename":
                if not args:
                    self._telegram.send_message(chat_id=message.chat_id, text="Usage: /rename <new thread name>")
                    return
                self._codex_runner.rename_thread(thread_id=session.codex_thread_id, name=args)  # type: ignore[union-attr]
                self._telegram.send_message(chat_id=message.chat_id, text=f"Renamed the current thread to: {args}")
                return

            if command == "resume":
                threads = self._codex_runner.list_threads(limit=20)
                if not args:
                    if not threads:
                        self._telegram.send_message(chat_id=message.chat_id, text="No saved threads found for the current working directory.")
                        return
                    lines = ["Usage: /resume <thread_id or preview substring>", "", "Recent threads:"]
                    for thread in threads[:10]:
                        title = thread.name or thread.preview or "(untitled)"
                        lines.append(f"- {thread.thread_id}  {title[:80]}")
                    self._telegram.send_message(chat_id=message.chat_id, text="\n".join(lines))
                    return

                target = next((thread for thread in threads if thread.thread_id == args), None)
                if target is None:
                    lowered = args.lower()
                    target = next(
                        (
                            thread
                            for thread in threads
                            if lowered in (thread.name or "").lower() or lowered in thread.preview.lower()
                        ),
                        None,
                    )
                if target is None:
                    self._telegram.send_message(chat_id=message.chat_id, text=f"No matching thread found for: {args}")
                    return
                self._db.save_session(
                    channel="telegram",
                    external_chat_id=str(message.chat_id),
                    codex_thread_id=target.thread_id,
                )
                self._telegram.send_message(
                    chat_id=message.chat_id,
                    text=f"Resumed thread.\nthread_id: {target.thread_id}",
                )
                return

            if command == "status":
                info = self._codex_runner.read_thread(thread_id=session.codex_thread_id)  # type: ignore[union-attr]
                lines = [
                    f"thread_id: {info.thread_id}",
                    f"name: {info.name or '(untitled)'}",
                    f"preview: {info.preview or '(empty)'}",
                    f"cwd: {info.cwd}",
                    f"model: {self._config.codex_model}",
                    f"approval_policy: {self._config.codex_approval_policy}",
                    f"sandbox_mode: {self._config.codex_sandbox_mode}",
                ]
                self._telegram.send_message(chat_id=message.chat_id, text="\n".join(lines))
                return

            if command == "model":
                models = self._codex_runner.list_models()
                self._telegram.send_message(
                    chat_id=message.chat_id,
                    text="Available models:\n" + "\n".join(f"- {model}" for model in models[:40]),
                )
                return

            unsupported = self._unsupported_command_message(command)
            self._telegram.send_message(chat_id=message.chat_id, text=unsupported)
        except ApprovalNotAllowedError:
            logger.exception("unexpected approval request while processing command")
            self._telegram.send_message(
                chat_id=message.chat_id,
                text="This deployment is configured for non-interactive execution only. The command requested approval and was stopped.",
            )
        except Exception:
            logger.exception("failed to process telegram command")
            self._telegram.send_message(
                chat_id=message.chat_id,
                text="Codex failed to process this command.",
            )

    def _unsupported_command_message(self, command: str) -> str:
        if command == "clear":
            return "Telegram does not have a terminal UI to clear. Use /new to start a fresh thread."
        if command in {"quit", "exit"}:
            return "Telegram bot commands cannot terminate the deployed service."
        if command in {"copy", "mention", "theme", "title", "statusline", "feedback", "apps", "plugins", "realtime", "settings", "agent", "subagents", "collab", "plan", "skills", "approvals", "permissions", "setup_default_sandbox", "sandbox_add_read_dir", "experimental", "fast", "personality", "ps", "stop", "rollout", "debug_config", "debug_m_drop", "debug_m_update", "test_approval", "logout"}:
            return textwrap.dedent(
                f"""\
                /{command} is registered for parity with Codex slash commands,
                but this command is currently TUI-specific or not yet implemented for Telegram.
                """
            ).strip()
        return f"/{command} is not implemented for Telegram yet."

    def _is_allowed(self, message: InboundMessage) -> bool:
        allowed_chat_ids = self._config.telegram.allowed_chat_ids
        allowed_user_ids = self._config.telegram.allowed_user_ids
        if not allowed_chat_ids and not allowed_user_ids:
            return True
        if allowed_chat_ids and message.chat_id in allowed_chat_ids:
            return True
        if allowed_user_ids and message.user_id is not None and message.user_id in allowed_user_ids:
            return True
        return False
