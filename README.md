# telecodex

[![ci](https://github.com/fopina/telecodex/actions/workflows/publish-main.yml/badge.svg)](https://github.com/fopina/telecodex/actions/workflows/publish-main.yml)
[![test](https://github.com/fopina/telecodex/actions/workflows/test.yml/badge.svg)](https://github.com/fopina/telecodex/actions/workflows/test.yml)
[![codecov](https://codecov.io/github/fopina/telecodex/graph/badge.svg)](https://codecov.io/github/fopina/telecodex)
[![PyPI pyversions](https://img.shields.io/pypi/pyversions/telecodex.svg)](https://pypi.org/project/telecodex/)
[![Current version on PyPI](https://img.shields.io/pypi/v/telecodex)](https://pypi.org/project/telecodex/)

Telegram bot bridge for `codex app-server` over stdio JSON-RPC.

The bot receives Telegram messages, sends them to `codex app-server`, and replies back to Telegram chats.

## Requirements

- Python 3.10+
- A Telegram bot token (from `@BotFather`)
- `codex` CLI installed with `codex app-server` available

## Install

```bash
uv sync --group dev
```

## Configuration

By default, `telecodex` reads a platform-specific config path:

- macOS: `~/Library/Application Support/telecodex/config.toml`
- Linux: `~/.config/telecodex/config.toml`
- Windows: `%APPDATA%\\telecodex\\config.toml`

Example:

```toml
[telecodex]
telegram_bot_token = "123456:ABC..."
allowed_chat_id = 123456789
# optional: enable ACP message logging
# acp_log_file = "/path/to/acp-messages.log"
codex_app_server_cmd = "codex app-server"
codex_model = "gpt-5"
codex_cwd = "."
codex_approval_policy = "never"
poll_timeout_seconds = 30
```

You can choose another config file with:

```bash
python3 -m telecodex --config /path/to/config.toml
```

Option precedence is:

1. CLI flags
2. Environment variables (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`, `TELECODEX_ACP_LOG_FILE`, `CODEX_*`, `POLL_TIMEOUT_SECONDS`)
3. TOML config values
4. Built-in defaults

`allowed_chat_id` (or `TELEGRAM_ALLOWED_CHAT_ID`) is mandatory, and the bot only accepts messages from that sender user id.
ACP/app-server message logging is disabled by default and is enabled only when `acp_log_file` (or `TELECODEX_ACP_LOG_FILE`) is set.

## Run

```bash
# optional: create config.toml at the default platform-specific location shown above
python3 -m telecodex
```

## Telegram commands

On startup, the bot registers Telegram commands:

- `/start`: sends `hello` to Codex to initialize/start the conversation turn
- `/verbose`: toggles an internal verbose flag (starts `false`)
- `/status`: fetches current ACP rate-limit values on demand (`account/rateLimits/read`) and shows latest token usage
- `/model`: shows an inline menu of available models, marks the current one with `✅`, includes `Free text` for manual model ids, and applies the selection to next turns (`Cancel` closes without changes)

When verbose mode is enabled, every ACP/app-server message that is not normally processed by the bridge is sent back to the requesting message as raw JSON in Markdown (still restricted to the allowed sender user id).

## Development

- Lint/format: `make lint`
- Lint check only: `make lint-check`
- Tests: `make test`

## Protocol flow

The bot uses the `codex app-server` JSON-RPC flow:

1. `initialize`
2. `initialized` (notification)
3. `thread/start`
4. `turn/start` for each Telegram message
5. stream `item/agentMessage/delta`
6. wait for `turn/completed`
