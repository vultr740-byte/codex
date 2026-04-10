from __future__ import annotations

from pathlib import Path

from codex_gateway.db import GatewayDb


def test_seen_message_deduplicates(tmp_path: Path) -> None:
    db = GatewayDb(tmp_path / "gateway.sqlite")
    assert db.seen_message(channel="telegram", external_chat_id="1", external_message_id="2") is False
    assert db.seen_message(channel="telegram", external_chat_id="1", external_message_id="2") is True


def test_session_round_trip(tmp_path: Path) -> None:
    db = GatewayDb(tmp_path / "gateway.sqlite")
    db.save_session(channel="telegram", external_chat_id="42", codex_thread_id="thr_123")
    session = db.get_session(channel="telegram", external_chat_id="42")
    assert session is not None
    assert session.codex_thread_id == "thr_123"
