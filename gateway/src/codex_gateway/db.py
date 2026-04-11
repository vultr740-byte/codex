from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionBinding:
    channel: str
    external_chat_id: str
    codex_thread_id: str


@dataclass(frozen=True)
class ChatPreferences:
    channel: str
    external_chat_id: str
    model: str | None
    reasoning_effort: str | None


class GatewayDb:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_sessions (
                    channel TEXT NOT NULL,
                    external_chat_id TEXT NOT NULL,
                    codex_thread_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (channel, external_chat_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inbound_messages (
                    channel TEXT NOT NULL,
                    external_chat_id TEXT NOT NULL,
                    external_message_id TEXT NOT NULL,
                    received_at INTEGER NOT NULL,
                    PRIMARY KEY (channel, external_chat_id, external_message_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_preferences (
                    channel TEXT NOT NULL,
                    external_chat_id TEXT NOT NULL,
                    model TEXT,
                    reasoning_effort TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (channel, external_chat_id)
                )
                """
            )
            self._ensure_column("channel_preferences", "reasoning_effort", "TEXT")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        existing_columns = {
            row["name"]
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column in existing_columns:
            return
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def seen_message(self, *, channel: str, external_chat_id: str, external_message_id: str) -> bool:
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT 1
                FROM inbound_messages
                WHERE channel = ? AND external_chat_id = ? AND external_message_id = ?
                """,
                (channel, external_chat_id, external_message_id),
            ).fetchone()
            if row is not None:
                return True

            self._conn.execute(
                """
                INSERT INTO inbound_messages (
                    channel,
                    external_chat_id,
                    external_message_id,
                    received_at
                ) VALUES (?, ?, ?, ?)
                """,
                (channel, external_chat_id, external_message_id, int(time.time())),
            )
            return False

    def get_session(self, *, channel: str, external_chat_id: str) -> SessionBinding | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT channel, external_chat_id, codex_thread_id
                FROM channel_sessions
                WHERE channel = ? AND external_chat_id = ?
                """,
                (channel, external_chat_id),
            ).fetchone()
        if row is None:
            return None
        return SessionBinding(
            channel=row["channel"],
            external_chat_id=row["external_chat_id"],
            codex_thread_id=row["codex_thread_id"],
        )

    def save_session(self, *, channel: str, external_chat_id: str, codex_thread_id: str) -> None:
        now = int(time.time())
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO channel_sessions (
                    channel,
                    external_chat_id,
                    codex_thread_id,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel, external_chat_id)
                DO UPDATE SET
                    codex_thread_id = excluded.codex_thread_id,
                    updated_at = excluded.updated_at
                """,
                (channel, external_chat_id, codex_thread_id, now, now),
            )

    def get_preferences(self, *, channel: str, external_chat_id: str) -> ChatPreferences | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT channel, external_chat_id, model
                    , reasoning_effort
                FROM channel_preferences
                WHERE channel = ? AND external_chat_id = ?
                """,
                (channel, external_chat_id),
            ).fetchone()
        if row is None:
            return None
        return ChatPreferences(
            channel=row["channel"],
            external_chat_id=row["external_chat_id"],
            model=row["model"],
            reasoning_effort=row["reasoning_effort"],
        )

    def save_preferences(
        self,
        *,
        channel: str,
        external_chat_id: str,
        model: str | None,
        reasoning_effort: str | None,
    ) -> None:
        now = int(time.time())
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO channel_preferences (
                    channel,
                    external_chat_id,
                    model,
                    reasoning_effort,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, external_chat_id)
                DO UPDATE SET
                    model = excluded.model,
                    reasoning_effort = excluded.reasoning_effort,
                    updated_at = excluded.updated_at
                """,
                (channel, external_chat_id, model, reasoning_effort, now, now),
            )
