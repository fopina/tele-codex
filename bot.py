#!/usr/bin/env python3
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import urllib.request

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
POLL_TIMEOUT_SECONDS = int(os.getenv("POLL_TIMEOUT_SECONDS", "30"))

CODEX_APP_SERVER_CMD = os.getenv("CODEX_APP_SERVER_CMD", "codex app-server")
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5")
CODEX_CWD = os.getenv("CODEX_CWD", os.getcwd())
CODEX_APPROVAL_POLICY = os.getenv("CODEX_APPROVAL_POLICY", "never")


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
            raise RuntimeError("CODEX_APP_SERVER_CMD is empty")

        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "telegram-codex-bot",
                    "version": "0.1.0",
                },
            },
        )
        self._notify("initialized", {})

        start_result = self._request(
            "thread/start",
            {
                "cwd": CODEX_CWD,
                "model": CODEX_MODEL,
                "approvalPolicy": CODEX_APPROVAL_POLICY,
            },
        )

        thread = start_result.get("thread") if isinstance(start_result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not thread_id:
            raise RuntimeError(f"thread/start did not return thread id: {start_result}")
        self.thread_id = thread_id

    def _ensure_running(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            err = ""
            if self.proc and self.proc.stderr:
                try:
                    err = self.proc.stderr.read()
                except Exception:
                    err = ""
            raise RuntimeError(f"app-server not running. stderr: {err[:2000]}")

    def _send(self, obj: dict) -> None:
        self._ensure_running()
        assert self.proc is not None and self.proc.stdin is not None
        line = json.dumps(obj, ensure_ascii=False)
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def _read_message(self) -> dict:
        self._ensure_running()
        assert self.proc is not None and self.proc.stdout is not None

        while True:
            line = self.proc.stdout.readline()
            if line == "":
                self._ensure_running()
                raise RuntimeError("Unexpected EOF from app-server stdout")
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

        self._send({"id": req_id, "method": method, "params": params})

        while True:
            msg = self._read_message()
            if msg.get("id") == req_id:
                if "error" in msg:
                    raise RuntimeError(f"{method} failed: {msg['error']}")
                return msg.get("result", {})

    def _notify(self, method: str, params: dict) -> None:
        self._send({"method": method, "params": params})

    def ask(self, text: str) -> str:
        with self.lock:
            self._ensure_running()
            if not self.thread_id:
                raise RuntimeError("No thread initialized")

            turn_result = self._request(
                "turn/start",
                {
                    "threadId": self.thread_id,
                    "input": [{"type": "text", "text": text}],
                },
            )
            turn = turn_result.get("turn") if isinstance(turn_result, dict) else None
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not turn_id:
                raise RuntimeError(f"turn/start did not return turn id: {turn_result}")

            chunks: list[str] = []
            fallback_final: str | None = None

            while True:
                msg = self._read_message()

                method = msg.get("method")
                params = msg.get("params")
                if not method or not isinstance(params, dict):
                    continue

                if method == "item/agentMessage/delta" and params.get("turnId") == turn_id:
                    delta = params.get("delta")
                    if isinstance(delta, str):
                        chunks.append(delta)
                    continue

                if method == "turn/completed":
                    completed_turn = params.get("turn")
                    completed_turn_id = completed_turn.get("id") if isinstance(completed_turn, dict) else None
                    if completed_turn_id != turn_id:
                        continue

                    agent_state = completed_turn.get("agentState") if isinstance(completed_turn, dict) else None
                    message = agent_state.get("message") if isinstance(agent_state, dict) else None
                    if isinstance(message, str) and message.strip():
                        fallback_final = message
                    break

            final = "".join(chunks).strip()
            if final:
                return final
            if fallback_final:
                return fallback_final
            return "No text response returned by app-server."


def require_env() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("Missing TELEGRAM_BOT_TOKEN", file=sys.stderr)
        sys.exit(1)


def telegram_api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


def post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


def get_updates(offset: int | None = None) -> list[dict]:
    payload = {"timeout": POLL_TIMEOUT_SECONDS, "allowed_updates": ["message"]}
    if offset is not None:
        payload["offset"] = offset
    response = post_json(telegram_api_url("getUpdates"), payload, timeout=POLL_TIMEOUT_SECONDS + 10)
    if not response.get("ok"):
        raise RuntimeError(f"Telegram getUpdates failed: {response}")
    return response.get("result", [])


def send_message(chat_id: int, text: str, reply_to_message_id: int | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id is not None:
        payload["reply_parameters"] = {"message_id": reply_to_message_id}
    response = post_json(telegram_api_url("sendMessage"), payload)
    if not response.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {response}")


def handle_message(update: dict, codex: CodexStdioClient) -> None:
    message = update.get("message", {})
    text = (message.get("text") or "").strip()
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    msg_id = message.get("message_id")

    if not chat_id or not text:
        return

    try:
        reply = codex.ask(text)
    except Exception as exc:  # noqa: BLE001
        reply = f"app-server error: {exc}"

    send_message(chat_id, reply[:4096], reply_to_message_id=msg_id)


def main() -> None:
    require_env()

    codex = CodexStdioClient(CODEX_APP_SERVER_CMD)
    codex.start()

    print("Bot is running (Telegram <-> codex app-server over stdio).")
    next_offset = None

    while True:
        try:
            updates = get_updates(next_offset)
            for update in updates:
                next_offset = update["update_id"] + 1
                handle_message(update, codex)
        except KeyboardInterrupt:
            print("Stopped by user")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"Loop error: {exc}", file=sys.stderr)
            time.sleep(3)


if __name__ == "__main__":
    main()
