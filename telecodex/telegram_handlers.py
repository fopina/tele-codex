from __future__ import annotations

import asyncio
import html
import json
import sys
from typing import Any

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from telecodex.codex_client import AskResult, CodexStdioClient, ModelOption
from telecodex.status_formatting import (
    format_limit_name,
    format_rate_limit_bucket,
    format_token_usage,
    should_render_rate_limit,
)

PENDING_MODEL_INPUT_KEY = 'pending_model_input'


def is_allowed_sender(update: Update, allowed_sender_id: Any) -> bool:
    user = update.effective_user
    if user is None:
        return False
    return user.id == allowed_sender_id


def register_handlers(application: Any) -> None:
    application.add_handler(CommandHandler('start', handle_start_command))
    application.add_handler(CommandHandler('verbose', handle_verbose_command))
    application.add_handler(CommandHandler('status', handle_status_command))
    application.add_handler(CommandHandler('model', handle_model_command))
    application.add_handler(CallbackQueryHandler(handle_model_callback, pattern=r'^model:(set:|free_text$|cancel$)'))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(handle_error)


def format_raw_json_markdown(raw_message: str) -> str:
    safe_raw = raw_message.replace('```', '``\\`')
    return f'```json\n{safe_raw}\n```'


def format_raw_json_expandable_blockquote(raw_message: str) -> str:
    escaped = html.escape(raw_message)
    return f'<blockquote expandable>{escaped}</blockquote>'


def is_delta_message(raw_message: str) -> bool:
    try:
        msg = json.loads(raw_message)
    except json.JSONDecodeError:
        return False
    if not isinstance(msg, dict):
        return False

    method = msg.get('method')
    if isinstance(method, str) and 'delta' in method.lower():
        return True

    params = msg.get('params')
    if isinstance(params, dict):
        nested_msg = params.get('msg')
        if isinstance(nested_msg, dict):
            msg_type = nested_msg.get('type')
            if isinstance(msg_type, str) and 'delta' in msg_type.lower():
                return True

    return False


async def reply_markdown(message: Any, text: str, reply_to_message_id: int) -> None:
    text = text[:4096]
    try:
        await message.reply_text(
            text,
            reply_to_message_id=reply_to_message_id,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except BadRequest:
        await message.reply_text(
            text,
            reply_to_message_id=reply_to_message_id,
            disable_web_page_preview=True,
        )


async def reply_expandable_blockquote(message: Any, text: str, reply_to_message_id: int) -> None:
    max_payload = 4000
    payload = text[:max_payload]
    try:
        await message.reply_text(
            format_raw_json_expandable_blockquote(payload),
            reply_to_message_id=reply_to_message_id,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest:
        await reply_markdown(
            message,
            format_raw_json_markdown(payload),
            reply_to_message_id=reply_to_message_id,
        )


async def process_user_input(message: Any, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    codex = context.application.bot_data['codex']
    assert isinstance(codex, CodexStdioClient)

    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)

    try:
        ask_result = await asyncio.to_thread(codex.ask, text)
        assert isinstance(ask_result, AskResult)
    except Exception as exc:  # noqa: BLE001
        await reply_markdown(message, f'app-server error: {exc}', reply_to_message_id=message.message_id)
        return

    await reply_markdown(message, ask_result.reply, reply_to_message_id=message.message_id)

    if context.application.bot_data.get('verbose'):
        for raw_message in ask_result.unprocessed_messages:
            if is_delta_message(raw_message):
                continue
            await reply_expandable_blockquote(message, raw_message, reply_to_message_id=message.message_id)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    text = (message.text or '').strip()
    if not text:
        return

    allowed_sender_id = context.application.bot_data.get('allowed_chat_id')
    if not is_allowed_sender(update, allowed_sender_id):
        return

    if context.application.bot_data.get(PENDING_MODEL_INPUT_KEY):
        codex = context.application.bot_data['codex']
        assert isinstance(codex, CodexStdioClient)
        await asyncio.to_thread(codex.set_model, text)
        context.application.bot_data[PENDING_MODEL_INPUT_KEY] = False
        await reply_markdown(
            message,
            f'Model updated to `{text}` for next turns.',
            reply_to_message_id=message.message_id,
        )
        return

    await process_user_input(message, context, text)


async def handle_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    allowed_sender_id = context.application.bot_data.get('allowed_chat_id')
    if not is_allowed_sender(update, allowed_sender_id):
        return
    await process_user_input(message, context, 'hello')


async def handle_verbose_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    allowed_sender_id = context.application.bot_data.get('allowed_chat_id')
    if not is_allowed_sender(update, allowed_sender_id):
        return
    verbose = bool(context.application.bot_data.get('verbose'))
    verbose = not verbose
    context.application.bot_data['verbose'] = verbose
    status = 'ON' if verbose else 'OFF'
    await reply_markdown(message, f'Verbose mode is now `{status}`.', reply_to_message_id=message.message_id)


async def handle_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    allowed_sender_id = context.application.bot_data.get('allowed_chat_id')
    if not is_allowed_sender(update, allowed_sender_id):
        return

    codex = context.application.bot_data['codex']
    assert isinstance(codex, CodexStdioClient)
    snapshot = await asyncio.to_thread(codex.read_rate_limits)
    token_usage = await asyncio.to_thread(codex.get_latest_token_usage)
    if not snapshot and not token_usage:
        await reply_markdown(
            message,
            'No rate limits or token usage received yet.',
            reply_to_message_id=message.message_id,
        )
        return

    lines: list[str] = []
    if snapshot:
        sorted_limits = sorted(snapshot.items(), key=lambda item: str(item[0]))
        visible_limits = [(limit_id, values) for limit_id, values in sorted_limits if should_render_rate_limit(values)]
        hidden_limit_names = [
            format_limit_name(limit_id) for limit_id, values in sorted_limits if not should_render_rate_limit(values)
        ]
        if visible_limits or hidden_limit_names:
            lines.append('')
            lines.append('*Rate Limits*')
            for limit_id, values in visible_limits:
                limit_name = format_limit_name(limit_id)
                lines.append('')
                lines.append(f'*Limit:* `{limit_name}`')

                primary = values.get('primary')
                secondary = values.get('secondary')
                lines.append(f'*Primary:* {format_rate_limit_bucket(primary)}')
                lines.append(f'*Secondary:* {format_rate_limit_bucket(secondary)}')
            if hidden_limit_names:
                lines.append('')
                lines.append(f'*Unused limits:* `{", ".join(hidden_limit_names)}`')

    if token_usage:
        lines.append('')
        lines.append('*Token Usage*')
        total = token_usage.get('total_token_usage')
        last = token_usage.get('last_token_usage')
        model_context_window = token_usage.get('model_context_window')
        lines.append(f'*Total:* {format_token_usage(total)}')
        lines.append(f'*Last:* {format_token_usage(last)}')
        lines.append(f'*Model Context Window:* `{model_context_window}`')

    await reply_markdown(message, '\n'.join(lines), reply_to_message_id=message.message_id)


def build_model_menu(models: list[ModelOption], selected_model: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for model in models:
        marker = '✅ ' if model.model_id == selected_model else ''
        label = f'{marker}{model.display_name}'
        rows.append([InlineKeyboardButton(text=label, callback_data=f'model:set:{model.model_id}')])
    rows.append([InlineKeyboardButton(text='Free text', callback_data='model:free_text')])
    rows.append([InlineKeyboardButton(text='Cancel', callback_data='model:cancel')])
    return InlineKeyboardMarkup(rows)


async def setup_bot_commands(application: Any) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand(command='start', description='Start conversation with Codex'),
            BotCommand(command='verbose', description='Toggle verbose ACP debug messages'),
            BotCommand(command='status', description='Show latest ACP rate-limit updates'),
            BotCommand(command='model', description='Choose the Codex model'),
        ]
    )


async def handle_error(_: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f'Loop error: {context.error}', file=sys.stderr)


async def handle_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    allowed_sender_id = context.application.bot_data.get('allowed_chat_id')
    if not is_allowed_sender(update, allowed_sender_id):
        return

    codex = context.application.bot_data['codex']
    assert isinstance(codex, CodexStdioClient)

    try:
        models = await asyncio.to_thread(codex.list_models)
        selected_model = await asyncio.to_thread(codex.get_model)
    except Exception as exc:  # noqa: BLE001
        await reply_markdown(message, f'app-server error: {exc}', reply_to_message_id=message.message_id)
        return

    if not models:
        await reply_markdown(message, 'No models available from app-server.', reply_to_message_id=message.message_id)
        return

    keyboard = build_model_menu(models, selected_model=selected_model)
    await message.reply_text(
        f'Select model for next turns (current: `{selected_model}`):',
        reply_to_message_id=message.message_id,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def handle_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    message = query.message
    if message is None:
        await query.answer()
        return

    allowed_sender_id = context.application.bot_data.get('allowed_chat_id')
    if not is_allowed_sender(update, allowed_sender_id):
        await query.answer()
        return

    data = query.data or ''
    if data == 'model:cancel':
        context.application.bot_data[PENDING_MODEL_INPUT_KEY] = False
        await query.answer('Canceled')
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if data == 'model:free_text':
        context.application.bot_data[PENDING_MODEL_INPUT_KEY] = True
        await query.answer()
        await query.edit_message_text('Send the model id as a text message. It will be used for next turns.')
        return

    if not data.startswith('model:set:'):
        await query.answer()
        return

    model_id = data.removeprefix('model:set:')
    codex = context.application.bot_data['codex']
    assert isinstance(codex, CodexStdioClient)

    try:
        models = await asyncio.to_thread(codex.list_models)
    except Exception as exc:  # noqa: BLE001
        await query.answer('Failed to load models')
        await query.edit_message_text(f'Could not load models: {exc}')
        return

    available_ids = {model.model_id for model in models}
    if model_id not in available_ids:
        await query.answer('Model unavailable')
        selected_model = await asyncio.to_thread(codex.get_model)
        await query.edit_message_text(
            f'Model `{model_id}` is not available. Current model remains `{selected_model}`.',
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await asyncio.to_thread(codex.set_model, model_id)
    context.application.bot_data[PENDING_MODEL_INPUT_KEY] = False
    await query.answer(f'Model set to {model_id}')
    await query.edit_message_text(f'Model updated to `{model_id}` for next turns.', parse_mode=ParseMode.MARKDOWN)
