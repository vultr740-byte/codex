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


def test_preferences_round_trip_and_reset(tmp_path: Path) -> None:
    db = GatewayDb(tmp_path / "gateway.sqlite")
    db.save_preferences(channel="telegram", external_chat_id="42", model="gpt-5.4")

    preferences = db.get_preferences(channel="telegram", external_chat_id="42")
    assert preferences is not None
    assert preferences.model == "gpt-5.4"

    db.save_preferences(channel="telegram", external_chat_id="42", model=None)
    reset_preferences = db.get_preferences(channel="telegram", external_chat_id="42")
    assert reset_preferences is not None
    assert reset_preferences.model is None
