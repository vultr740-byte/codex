from __future__ import annotations

from codex_gateway.slash_commands import parse_telegram_command, registered_telegram_commands


def test_registered_telegram_commands_include_new() -> None:
    commands = registered_telegram_commands()
    assert any(command["command"] == "new" for command in commands)
    assert any(command["command"] == "sandbox_add_read_dir" for command in commands)


def test_parse_telegram_command_parses_command_and_args() -> None:
    parsed = parse_telegram_command("/rename hello world")
    assert parsed is not None
    assert parsed.key == "rename"
    assert parsed.args == "hello world"


def test_parse_telegram_command_supports_bot_mentions() -> None:
    parsed = parse_telegram_command("/new@xiasou_pico_bot", bot_username="xiasou_pico_bot")
    assert parsed is not None
    assert parsed.key == "new"


def test_parse_telegram_command_ignores_other_bot_mentions() -> None:
    assert parse_telegram_command("/new@other_bot", bot_username="xiasou_pico_bot") is None
