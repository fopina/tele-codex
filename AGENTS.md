# AGENTS.md

Guidance for agents working in this repository.

## Project context

- This repository is a Telegram bot bridge for `codex app-server` over stdio (`telecodex/__main__.py`).
- The repo still contains Python package template leftovers (`example/`, `tests/test_demo.py`, template metadata in `pyproject.toml`/`README.md`).
- Prefer changes that improve the Telegram bot behavior without expanding scope into template cleanup unless requested.

## Working rules

- Keep changes focused and minimal for the requested task.
- Preserve existing runtime behavior unless the task explicitly asks for behavior changes.
- Do not log or print secrets (especially `TELEGRAM_BOT_TOKEN`).
- Keep the bot compatible with Python `>=3.10` (see `pyproject.toml`).
- Prefer Python standard library solutions unless a dependency is already present and justified.

## Bot-specific constraints (`telecodex/__main__.py`)

- Maintain Telegram reply safety:
  - Telegram messages are capped at 4096 chars; keep truncation behavior or improve it safely.
- Preserve long-polling resilience:
  - Loop should continue after transient errors.
  - Backoff/sleep behavior should remain reasonable.
- Preserve JSON-RPC sequencing with `codex app-server` unless intentionally refactoring protocol handling:
  - `initialize` -> `initialized` -> `thread/start` -> `turn/start` / stream -> `turn/completed`.
- Be careful with concurrency changes:
  - `CodexStdioClient.ask()` currently serializes access with a lock; do not remove serialization without replacing it with a correct multi-turn strategy.

## Validation

- Use `uv` for local commands.
- First-time setup (or after dependency changes): `uv sync --group dev`
- Run formatting/linting before finishing code changes:
  - `make lint` (auto-format + ruff fixes)
  - `make lint-check` for verification-only (`ruff format --diff` + `ruff check`)
- Run tests before finishing changes when feasible:
  - `make test` (uses `uv run python -m pytest --cov` locally)
- If tests are template-only or unrelated, say so explicitly in your summary.

## Docs and config hygiene

- Keep `README.md` and `telecodex/__main__.py` in sync when changing runtime env vars or execution flow.
- If modifying packaging metadata (`pyproject.toml`), note whether changes are for the bot project or template cleanup.
- Avoid deleting template files unless the task asks for repository cleanup/migration.
