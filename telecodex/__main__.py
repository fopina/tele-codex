#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import classyclick
import click
from platformdirs import user_config_dir
from telegram.ext import ApplicationBuilder

from telecodex.codex_client import CodexStdioClient
from telecodex.telegram_handlers import PENDING_MODEL_INPUT_KEY, register_handlers, setup_bot_commands

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


@dataclass(slots=True)
class Settings:
    telegram_bot_token: str
    allowed_chat_id: int | None
    acp_log_file: str | None
    poll_timeout_seconds: int
    codex_app_server_cmd: str
    codex_model: str
    codex_cwd: str
    codex_approval_policy: str


DEFAULT_CONFIG_PATH = str(Path(user_config_dir('telecodex')) / 'config.toml')
CONFIG_SECTION = 'telecodex'
CONFIG_KEYS = {
    'telegram_bot_token',
    'allowed_chat_id',
    'acp_log_file',
    'poll_timeout_seconds',
    'codex_app_server_cmd',
    'codex_model',
    'codex_cwd',
    'codex_approval_policy',
}


def load_settings_from_toml(config_path: str) -> dict[str, Any]:
    path = Path(config_path).expanduser()
    if not path.exists():
        return {}

    with path.open('rb') as fh:
        data = tomllib.load(fh)

    if not isinstance(data, dict):
        raise ValueError(f'Config file {path} must contain a TOML table at the root.')

    section = data.get(CONFIG_SECTION)
    if section is None:
        candidate = data
    elif isinstance(section, dict):
        candidate = section
    else:
        raise ValueError(f'[{CONFIG_SECTION}] in {path} must be a TOML table.')

    values: dict[str, Any] = {}
    for key in CONFIG_KEYS:
        value = candidate.get(key)
        if value is not None:
            values[key] = value
    return values


def config_callback(ctx: click.Context, _: click.Parameter, value: str) -> str:
    try:
        config_values = load_settings_from_toml(value)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise click.BadParameter(str(exc)) from exc
    if config_values:
        existing = dict(ctx.default_map or {})
        existing.update(config_values)
        ctx.default_map = existing
    return value


def require_env(settings: Settings) -> None:
    if not settings.telegram_bot_token:
        print('Missing TELEGRAM_BOT_TOKEN', file=sys.stderr)
        sys.exit(1)
    if settings.allowed_chat_id is None:
        print(
            'Missing allowed sender id (--allowed-chat-id / TELEGRAM_ALLOWED_CHAT_ID / config allowed_chat_id)',
            file=sys.stderr,
        )
        sys.exit(1)


def run_bot(settings: Settings) -> None:
    require_env(settings)

    codex = CodexStdioClient(
        settings.codex_app_server_cmd,
        settings.codex_model,
        settings.codex_cwd,
        settings.codex_approval_policy,
        settings.acp_log_file,
    )
    while True:
        try:
            codex.start()
            app = ApplicationBuilder().token(settings.telegram_bot_token).post_init(setup_bot_commands).build()
            app.bot_data['codex'] = codex
            app.bot_data['allowed_chat_id'] = settings.allowed_chat_id
            app.bot_data['verbose'] = False
            app.bot_data[PENDING_MODEL_INPUT_KEY] = False
            register_handlers(app)

            print('Bot is running (Telegram <-> codex app-server over stdio).')
            app.run_polling(
                allowed_updates=['message', 'callback_query'],
                timeout=settings.poll_timeout_seconds,
                close_loop=False,
            )
            return
        except KeyboardInterrupt:
            print('Stopped by user')
            return
        except Exception as exc:  # noqa: BLE001
            print(f'Loop error: {exc}', file=sys.stderr)
            time.sleep(3)
        finally:
            codex.stop()


@classyclick.command()
class Telecodex:
    """Telegram bot bridge for codex app-server over stdio."""

    config: str = classyclick.Option(
        '--config',
        default=DEFAULT_CONFIG_PATH,
        type=str,
        show_default=True,
        is_eager=True,
        expose_value=False,
        callback=config_callback,
        help='TOML config file path (uses [telecodex] table or top-level keys).',
    )
    telegram_bot_token: str = classyclick.Option(
        envvar='TELEGRAM_BOT_TOKEN',
        default='',
        type=str,
        show_envvar=True,
        help='Telegram bot token.',
    )
    allowed_chat_id: int | None = classyclick.Option(
        envvar='TELEGRAM_ALLOWED_CHAT_ID',
        default=None,
        type=int,
        show_envvar=True,
        help='Only this Telegram sender user id will be accepted.',
    )
    acp_log_file: str | None = classyclick.Option(
        envvar='TELECODEX_ACP_LOG_FILE',
        default=None,
        type=str,
        show_envvar=True,
        help='File path to append every ACP/app-server message received (disabled by default).',
    )
    poll_timeout_seconds: int = classyclick.Option(
        envvar='POLL_TIMEOUT_SECONDS',
        default=30,
        type=int,
        show_envvar=True,
        help='Telegram polling timeout in seconds.',
    )
    codex_app_server_cmd: str = classyclick.Option(
        envvar='CODEX_APP_SERVER_CMD',
        default='codex app-server',
        type=str,
        show_envvar=True,
        help='Command used to launch codex app-server.',
    )
    codex_model: str = classyclick.Option(
        envvar='CODEX_MODEL',
        default='gpt-5',
        type=str,
        show_envvar=True,
        help='Codex model passed to thread/start.',
    )
    codex_cwd: str = classyclick.Option(
        envvar='CODEX_CWD',
        default=os.getcwd(),
        type=str,
        show_envvar=True,
        help='Working directory for the codex app-server thread.',
    )
    codex_approval_policy: str = classyclick.Option(
        envvar='CODEX_APPROVAL_POLICY',
        default='never',
        type=str,
        show_envvar=True,
        help='Approval policy passed to thread/start.',
    )

    def __call__(self) -> None:
        settings = Settings(
            telegram_bot_token=self.telegram_bot_token,
            allowed_chat_id=self.allowed_chat_id,
            acp_log_file=self.acp_log_file,
            poll_timeout_seconds=self.poll_timeout_seconds,
            codex_app_server_cmd=self.codex_app_server_cmd,
            codex_model=self.codex_model,
            codex_cwd=self.codex_cwd,
            codex_approval_policy=self.codex_approval_policy,
        )
        run_bot(settings)


def main() -> None:
    Telecodex.click()


if __name__ == '__main__':
    main()
