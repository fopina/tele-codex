#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from contextlib import suppress

import classyclick
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
POLL_TIMEOUT_SECONDS = int(os.getenv('POLL_TIMEOUT_SECONDS', '30'))

CODEX_APP_SERVER_CMD = os.getenv('CODEX_APP_SERVER_CMD', 'codex app-server')
CODEX_MODEL = os.getenv('CODEX_MODEL', 'gpt-5')
CODEX_CWD = os.getenv('CODEX_CWD', os.getcwd())
CODEX_APPROVAL_POLICY = os.getenv('CODEX_APPROVAL_POLICY', 'never')


class CodexStdioClient:
    def __init__(self, command: str) -> None:
        self.command = command
        self.proc: subprocess.Popen[str] | None = None
        self.next_id = 1
        self.lock = threading.Lock()
        self.thread_id: str | None = None

    def start(self) -> None:
        argv = shlex.split(self.command)
        if not argv:
            raise RuntimeError('CODEX_APP_SERVER_CMD is empty')

        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self._request(
            'initialize',
            {
                'clientInfo': {
                    'name': 'telegram-codex-bot',
                    'version': '0.1.0',
                },
            },
        )
        self._notify('initialized', {})

        start_result = self._request(
            'thread/start',
            {
                'cwd': CODEX_CWD,
                'model': CODEX_MODEL,
                'approvalPolicy': CODEX_APPROVAL_POLICY,
            },
        )

        thread = start_result.get('thread') if isinstance(start_result, dict) else None
        thread_id = thread.get('id') if isinstance(thread, dict) else None
        if not thread_id:
            raise RuntimeError(f'thread/start did not return thread id: {start_result}')
        self.thread_id = thread_id

    def _ensure_running(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            err = ''
            if self.proc and self.proc.stderr:
                try:
                    err = self.proc.stderr.read()
                except Exception:
                    err = ''
            raise RuntimeError(f'app-server not running. stderr: {err[:2000]}')

    def _send(self, obj: dict) -> None:
        self._ensure_running()
        assert self.proc is not None and self.proc.stdin is not None
        line = json.dumps(obj, ensure_ascii=False)
        self.proc.stdin.write(line + '\n')
        self.proc.stdin.flush()

    def _read_message(self) -> dict:
        self._ensure_running()
        assert self.proc is not None and self.proc.stdout is not None

        while True:
            line = self.proc.stdout.readline()
            if line == '':
                self._ensure_running()
                raise RuntimeError('Unexpected EOF from app-server stdout')
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                return msg

    def _request(self, method: str, params: dict) -> dict:
        req_id = self.next_id
        self.next_id += 1

        self._send({'id': req_id, 'method': method, 'params': params})

        while True:
            msg = self._read_message()
            if msg.get('id') == req_id:
                if 'error' in msg:
                    raise RuntimeError(f'{method} failed: {msg["error"]}')
                return msg.get('result', {})

    def _notify(self, method: str, params: dict) -> None:
        self._send({'method': method, 'params': params})

    def ask(self, text: str) -> str:
        with self.lock:
            self._ensure_running()
            if not self.thread_id:
                raise RuntimeError('No thread initialized')

            turn_result = self._request(
                'turn/start',
                {
                    'threadId': self.thread_id,
                    'input': [{'type': 'text', 'text': text}],
                },
            )
            turn = turn_result.get('turn') if isinstance(turn_result, dict) else None
            turn_id = turn.get('id') if isinstance(turn, dict) else None
            if not turn_id:
                raise RuntimeError(f'turn/start did not return turn id: {turn_result}')

            chunks: list[str] = []
            fallback_final: str | None = None

            while True:
                msg = self._read_message()

                method = msg.get('method')
                params = msg.get('params')
                if not method or not isinstance(params, dict):
                    continue

                if method == 'item/agentMessage/delta' and params.get('turnId') == turn_id:
                    delta = params.get('delta')
                    if isinstance(delta, str):
                        chunks.append(delta)
                    continue

                if method == 'turn/completed':
                    completed_turn = params.get('turn')
                    completed_turn_id = completed_turn.get('id') if isinstance(completed_turn, dict) else None
                    if completed_turn_id != turn_id:
                        continue

                    agent_state = completed_turn.get('agentState') if isinstance(completed_turn, dict) else None
                    message = agent_state.get('message') if isinstance(agent_state, dict) else None
                    if isinstance(message, str) and message.strip():
                        fallback_final = message
                    break

            final = ''.join(chunks).strip()
            if final:
                return final
            if fallback_final:
                return fallback_final
            return 'No text response returned by app-server.'

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is not None:
            return

        self.proc.terminate()
        with suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=5)

        if self.proc.poll() is None:
            self.proc.kill()
            with suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=5)


def require_env() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print('Missing TELEGRAM_BOT_TOKEN', file=sys.stderr)
        sys.exit(1)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    text = (message.text or '').strip()
    if not text:
        return

    codex = context.application.bot_data['codex']
    assert isinstance(codex, CodexStdioClient)

    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)

    try:
        reply = await asyncio.to_thread(codex.ask, text)
    except Exception as exc:  # noqa: BLE001
        reply = f'app-server error: {exc}'

    reply = reply[:4096]
    try:
        await message.reply_text(
            reply,
            reply_to_message_id=message.message_id,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except BadRequest:
        await message.reply_text(
            reply,
            reply_to_message_id=message.message_id,
            disable_web_page_preview=True,
        )


async def handle_error(_: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f'Loop error: {context.error}', file=sys.stderr)


def run_bot() -> None:
    require_env()

    codex = CodexStdioClient(CODEX_APP_SERVER_CMD)
    while True:
        try:
            codex.start()
            app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
            app.bot_data['codex'] = codex
            app.add_handler(MessageHandler(filters.TEXT, handle_message))
            app.add_error_handler(handle_error)

            print('Bot is running (Telegram <-> codex app-server over stdio).')
            app.run_polling(
                allowed_updates=['message'],
                timeout=POLL_TIMEOUT_SECONDS,
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

    telegram_bot_token: str = classyclick.Option(
        envvar='TELEGRAM_BOT_TOKEN',
        default='',
        type=str,
        show_envvar=True,
        help='Telegram bot token.',
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
        default=CODEX_CWD,
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
        global TELEGRAM_BOT_TOKEN, POLL_TIMEOUT_SECONDS
        global CODEX_APP_SERVER_CMD, CODEX_MODEL, CODEX_CWD, CODEX_APPROVAL_POLICY

        TELEGRAM_BOT_TOKEN = self.telegram_bot_token
        POLL_TIMEOUT_SECONDS = self.poll_timeout_seconds
        CODEX_APP_SERVER_CMD = self.codex_app_server_cmd
        CODEX_MODEL = self.codex_model
        CODEX_CWD = self.codex_cwd
        CODEX_APPROVAL_POLICY = self.codex_approval_policy

        run_bot()


def main() -> None:
    Telecodex.click()


if __name__ == '__main__':
    main()
