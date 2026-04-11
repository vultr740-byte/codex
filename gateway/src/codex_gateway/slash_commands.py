from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramCommandSpec:
    key: str
    telegram_command: str
    codex_command: str
    description: str
    supports_args: bool = False
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedTelegramCommand:
    name: str
    args: str
    spec: TelegramCommandSpec | None

    @property
    def key(self) -> str | None:
        if self.spec is None:
            return None
        return self.spec.key


_COMMAND_SPECS = (
    TelegramCommandSpec("model", "model", "model", "choose what model and reasoning effort to use"),
    TelegramCommandSpec("reasoning", "reasoning", "reasoning", "choose the reasoning effort for the current model"),
    TelegramCommandSpec("fast", "fast", "fast", "toggle Fast mode to enable fastest inference at 2X plan usage", True),
    TelegramCommandSpec("approvals", "approvals", "approvals", "choose what Codex is allowed to do"),
    TelegramCommandSpec("permissions", "permissions", "permissions", "choose what Codex is allowed to do"),
    TelegramCommandSpec(
        "setup_default_sandbox",
        "setup_default_sandbox",
        "setup-default-sandbox",
        "set up elevated agent sandbox",
    ),
    TelegramCommandSpec(
        "sandbox_add_read_dir",
        "sandbox_add_read_dir",
        "sandbox-add-read-dir",
        "let sandbox read a directory",
        True,
    ),
    TelegramCommandSpec("experimental", "experimental", "experimental", "toggle experimental features"),
    TelegramCommandSpec("skills", "skills", "skills", "use skills to improve how Codex performs specific tasks"),
    TelegramCommandSpec("review", "review", "review", "review my current changes and find issues", True),
    TelegramCommandSpec("rename", "rename", "rename", "rename the current thread", True),
    TelegramCommandSpec("new", "new", "new", "start a new chat during a conversation"),
    TelegramCommandSpec("resume", "resume", "resume", "resume a saved chat", True),
    TelegramCommandSpec("fork", "fork", "fork", "fork the current chat"),
    TelegramCommandSpec("init", "init", "init", "create an AGENTS.md file with instructions for Codex"),
    TelegramCommandSpec("compact", "compact", "compact", "summarize conversation to prevent hitting the context limit"),
    TelegramCommandSpec("plan", "plan", "plan", "switch to Plan mode", True),
    TelegramCommandSpec("collab", "collab", "collab", "change collaboration mode"),
    TelegramCommandSpec("agent", "agent", "agent", "switch the active agent thread"),
    TelegramCommandSpec("diff", "diff", "diff", "show git diff"),
    TelegramCommandSpec("copy", "copy", "copy", "copy the latest Codex output to your clipboard"),
    TelegramCommandSpec("mention", "mention", "mention", "mention a file"),
    TelegramCommandSpec("status", "status", "status", "show current session configuration and token usage"),
    TelegramCommandSpec("debug_config", "debug_config", "debug-config", "show config layers and requirement sources for debugging"),
    TelegramCommandSpec("title", "title", "title", "configure which items appear in the terminal title"),
    TelegramCommandSpec("statusline", "statusline", "statusline", "configure which items appear in the status line"),
    TelegramCommandSpec("theme", "theme", "theme", "choose a syntax highlighting theme"),
    TelegramCommandSpec("mcp", "mcp", "mcp", "list configured MCP tools"),
    TelegramCommandSpec("apps", "apps", "apps", "manage apps"),
    TelegramCommandSpec("plugins", "plugins", "plugins", "browse plugins"),
    TelegramCommandSpec("logout", "logout", "logout", "log out of Codex"),
    TelegramCommandSpec("quit", "quit", "quit", "exit Codex"),
    TelegramCommandSpec("exit", "exit", "exit", "exit Codex"),
    TelegramCommandSpec("feedback", "feedback", "feedback", "send logs to maintainers"),
    TelegramCommandSpec("rollout", "rollout", "rollout", "print the rollout file path"),
    TelegramCommandSpec("ps", "ps", "ps", "list background terminals"),
    TelegramCommandSpec("stop", "stop", "stop", "stop all background terminals", aliases=("clean",)),
    TelegramCommandSpec("clear", "clear", "clear", "clear the terminal and start a new chat"),
    TelegramCommandSpec("personality", "personality", "personality", "choose a communication style for Codex"),
    TelegramCommandSpec("realtime", "realtime", "realtime", "toggle realtime voice mode"),
    TelegramCommandSpec("settings", "settings", "settings", "configure realtime microphone/speaker"),
    TelegramCommandSpec("test_approval", "test_approval", "test-approval", "test approval request"),
    TelegramCommandSpec("subagents", "subagents", "subagents", "switch the active agent thread"),
    TelegramCommandSpec("debug_m_drop", "debug_m_drop", "debug-m-drop", "debug memory maintenance"),
    TelegramCommandSpec("debug_m_update", "debug_m_update", "debug-m-update", "debug memory maintenance"),
)

_COMMAND_BY_NAME = {
    name: spec
    for spec in _COMMAND_SPECS
    for name in (spec.telegram_command, *spec.aliases, spec.codex_command.replace("-", "_"))
}


def registered_telegram_commands() -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for spec in _COMMAND_SPECS:
        commands.append(
            {
                "command": spec.telegram_command,
                "description": spec.description[:256],
            }
        )
        for alias in spec.aliases:
            commands.append(
                {
                    "command": alias,
                    "description": f"alias for /{spec.telegram_command}"[:256],
                }
            )
    return commands


def parse_telegram_command(text: str, *, bot_username: str | None = None) -> ParsedTelegramCommand | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    first_token, _, remainder = stripped.partition(" ")
    raw_name = first_token[1:]
    mention_target: str | None = None
    if "@" in raw_name:
        raw_name, mention_target = raw_name.split("@", 1)
    if mention_target and bot_username and mention_target.lower() != bot_username.lower():
        return None

    normalized_name = raw_name.lower().replace("-", "_")
    return ParsedTelegramCommand(
        name=normalized_name,
        args=remainder.strip(),
        spec=_COMMAND_BY_NAME.get(normalized_name),
    )
