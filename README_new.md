# Telegram bot for codex app-server (`stdio`)

This bot receives Telegram messages, sends them to `codex app-server` over stdio JSON-RPC, and replies back to Telegram.

## Requirements

- Python 3.10+
- A Telegram bot token (`@BotFather`)
- `codex` CLI installed and `codex app-server` available

## Environment variables

- `TELEGRAM_BOT_TOKEN` (required): Telegram bot token
- `CODEX_APP_SERVER_CMD` (optional): command to launch app-server. Default: `codex app-server`
- `CODEX_MODEL` (optional): model for `thread/start`. Default: `gpt-5`
- `CODEX_CWD` (optional): working dir for app-server thread. Default: current working directory
- `CODEX_APPROVAL_POLICY` (optional): `untrusted|on-failure|on-request|never`. Default: `never`
- `POLL_TIMEOUT_SECONDS` (optional): Telegram long-poll timeout. Default: `30`

## Run

```bash
export TELEGRAM_BOT_TOKEN='123456:ABC...'
export CODEX_APP_SERVER_CMD='codex app-server'
export CODEX_MODEL='gpt-5'
python3 bot.py
```

## Protocol used

The bot uses v2 JSON-RPC methods from your schema in `/Users/fopina/Documents/telegram-codex/x`:

1. `initialize`
2. `initialized` (notification)
3. `thread/start`
4. `turn/start` for each Telegram message
5. collect `item/agentMessage/delta` until `turn/completed`
