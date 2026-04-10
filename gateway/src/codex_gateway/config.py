from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from codex_app_server.client import AppServerConfig
from .responses_bridge import BridgeConfig


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv_ints(name: str) -> set[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set()
    values: set[int] = set()
    for item in raw.split(","):
        stripped = item.strip()
        if not stripped:
            continue
        values.add(int(stripped))
    return values


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    poll_interval_ms: int
    allowed_chat_ids: set[int]
    allowed_user_ids: set[int]
    require_mention: bool
    streaming_enabled: bool


@dataclass(frozen=True)
class GatewayConfig:
    data_dir: Path
    sqlite_path: Path
    codex_home: Path
    codex_working_dir: Path
    openai_api_key: str
    openai_base_url: str | None
    codex_model: str
    codex_approval_policy: str
    codex_sandbox_mode: str
    codex_bin: str | None
    telegram: TelegramConfig

    def bridge_config(self) -> BridgeConfig:
        return BridgeConfig(
            upstream_base_url=self.openai_base_url,
            upstream_api_key=self.openai_api_key,
        )

    def app_server_config(self, *, openai_base_url: str | None = None) -> AppServerConfig:
        codex_bin = self.codex_bin or shutil.which("codex")
        if not codex_bin:
            raise ValueError("Could not find Codex binary. Set CODEX_BIN or install `codex` in PATH.")

        launch_args = [codex_bin]
        overrides = [f'approval_policy="{self.codex_approval_policy}"']
        effective_openai_base_url = openai_base_url or self.openai_base_url
        if effective_openai_base_url:
            overrides.append(f'openai_base_url="{effective_openai_base_url}"')
        for override in overrides:
            launch_args.extend(["--config", override])
        launch_args.extend(["app-server", "--listen", "stdio://"])

        env = {
            "OPENAI_API_KEY": self.openai_api_key,
            "CODEX_HOME": str(self.codex_home),
        }

        return AppServerConfig(
            codex_bin=codex_bin,
            launch_args_override=tuple(launch_args),
            cwd=str(self.codex_working_dir),
            env=env,
            client_name="codex_gateway",
            client_title="Codex Telegram Gateway",
            client_version="0.1.0",
            experimental_api=True,
        )


def load_config() -> GatewayConfig:
    data_dir = Path(os.getenv("DATA_DIR", "/data")).expanduser()
    codex_home = Path(os.getenv("CODEX_HOME", str(data_dir / "codex-home"))).expanduser()
    sqlite_path = Path(os.getenv("GATEWAY_SQLITE_PATH", str(data_dir / "gateway.sqlite"))).expanduser()
    codex_working_dir = Path(os.getenv("CODEX_WORKING_DIR", str(data_dir / "workspace"))).expanduser().resolve()
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_api_key:
        raise ValueError("OPENAI_API_KEY is required")
    openai_base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    if not openai_base_url:
        raise ValueError("OPENAI_BASE_URL is required")
    channel = os.getenv("CHANNEL", "").strip().lower()
    if not channel:
        raise ValueError("CHANNEL is required")
    if channel != "telegram":
        raise ValueError(f"Unsupported CHANNEL: {channel}")

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not telegram_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required when CHANNEL=telegram")

    data_dir.mkdir(parents=True, exist_ok=True)
    codex_home.mkdir(parents=True, exist_ok=True)
    codex_working_dir.mkdir(parents=True, exist_ok=True)

    return GatewayConfig(
        data_dir=data_dir,
        sqlite_path=sqlite_path,
        codex_home=codex_home,
        codex_working_dir=codex_working_dir,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        codex_model=os.getenv("CODEX_MODEL", "gpt-5.2-codex").strip(),
        codex_approval_policy=os.getenv("CODEX_APPROVAL_POLICY", "never").strip(),
        codex_sandbox_mode=os.getenv("CODEX_SANDBOX_MODE", "workspace-write").strip(),
        codex_bin=os.getenv("CODEX_BIN", "").strip() or None,
        telegram=TelegramConfig(
            bot_token=telegram_token,
            poll_interval_ms=int(os.getenv("TELEGRAM_POLL_INTERVAL_MS", "1000")),
            allowed_chat_ids=_env_csv_ints("TELEGRAM_ALLOWED_CHAT_IDS"),
            allowed_user_ids=_env_csv_ints("TELEGRAM_ALLOWED_USER_IDS"),
            require_mention=_env_bool("TELEGRAM_REQUIRE_MENTION", False),
            streaming_enabled=_env_bool("TELEGRAM_STREAMING_ENABLED", True),
        ),
    )
