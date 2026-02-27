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
2. Environment variables (`TELEGRAM_BOT_TOKEN`, `CODEX_*`, `POLL_TIMEOUT_SECONDS`)
3. TOML config values
4. Built-in defaults

## Run

```bash
# optional: create config.toml at the default platform-specific location shown above
python3 -m telecodex
```

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
