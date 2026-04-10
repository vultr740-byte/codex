from __future__ import annotations

import pytest

from codex_gateway.config import load_config


def test_load_config_requires_openai_key(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("CHANNEL", "telegram")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_WORKING_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        load_config()


def test_load_config_requires_channel(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.delenv("CHANNEL", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("CODEX_WORKING_DIR", str(tmp_path / "workspace"))
    with pytest.raises(ValueError, match="CHANNEL"):
        load_config()


def test_load_config_defaults_approval_policy_to_never(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("CHANNEL", "telegram")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("CODEX_WORKING_DIR", str(tmp_path / "workspace"))
    config = load_config()
    assert config.codex_approval_policy == "never"
    assert config.codex_sandbox_mode == "danger-full-access"
    assert config.sqlite_path == tmp_path / "gateway.sqlite"
    assert config.codex_home == tmp_path / "codex-home"
    assert config.codex_working_dir == (tmp_path / "workspace").resolve()


def test_load_config_supports_openai_base_url(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CHANNEL", "telegram")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("CODEX_WORKING_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("CODEX_BIN", "/usr/local/bin/codex")
    config = load_config()
    app_server_config = config.app_server_config(openai_base_url="http://127.0.0.1:8765/v1")
    assert app_server_config.launch_args_override[0] == "/usr/local/bin/codex"
    assert 'openai_base_url="http://127.0.0.1:8765/v1"' in app_server_config.launch_args_override


def test_load_config_creates_working_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("CHANNEL", "telegram")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    working_dir = tmp_path / "missing"
    monkeypatch.setenv("CODEX_WORKING_DIR", str(working_dir))
    config = load_config()
    assert config.codex_working_dir == working_dir.resolve()
    assert working_dir.exists()
