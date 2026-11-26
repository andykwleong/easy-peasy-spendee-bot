from __future__ import annotations

import asyncio
from typing import Any

from getrichbot.bot import (
    INCOME_CATEGORY_CALLBACK,
    PAYMENT_CHANNEL_CALLBACK,
    PAYMENT_METHOD_CALLBACK,
)
from getrichbot.shop_category import SHOP_CATEGORY_CALLBACK

_CALLBACK_HANDLERS = {
    PAYMENT_METHOD_CALLBACK: "handle_payment_method_callback",
    PAYMENT_CHANNEL_CALLBACK: "handle_payment_channel_callback",
    INCOME_CATEGORY_CALLBACK: "handle_income_category_callback",
    SHOP_CATEGORY_CALLBACK: "handle_shop_category_callback",
}
_MAX_TEXT_CHARS = 4000
_MAX_CALLBACK_CHARS = 64
COMMAND_REPLY = (
    "Send commands in Telegram. This box takes the same expense text you would type in the chat."
)
UNAVAILABLE_BUTTON_REPLY = "That button is no longer available."


def command_reply() -> list[dict[str, Any]]:
    return [_reply(COMMAND_REPLY, None)]


def submit_text(bot: Any, user_id: int, text: str, message_id: int) -> list[dict[str, Any]]:
    update = _TextUpdate(user_id, text, message_id)
    asyncio.run(bot.handle_text(update, None))
    return list(update.message.replies)


def submit_callback(bot: Any, user_id: int, callback: str, message_id: int) -> list[dict[str, Any]]:
    handler_name = _handler_name(callback)
    if handler_name is None:
        return [_reply(UNAVAILABLE_BUTTON_REPLY, None)]
    update = _CallbackUpdate(user_id, callback, message_id)
    asyncio.run(getattr(bot, handler_name)(update, None))
    return list(update.callback_query.message.replies)


def text_is_command(text: str) -> bool:
    return text.lstrip().startswith("/")


def text_is_sendable(text: str) -> bool:
    cleaned = text.strip()
    return bool(cleaned) and len(cleaned) <= _MAX_TEXT_CHARS


def _handler_name(callback: str) -> str | None:
    if not isinstance(callback, str) or not callback or len(callback) > _MAX_CALLBACK_CHARS:
        return None
    prefix, separator, rest = callback.partition("|")
    if not separator or prefix not in _CALLBACK_HANDLERS:
        return None
    pending_id, separator, index = rest.partition("|")
    if not separator or not pending_id or not index.isdigit() or "|" in index:
        return None
    return _CALLBACK_HANDLERS[prefix]


def _private_chat_id(user_id: int) -> int:
    return user_id


def _buttons(markup: Any) -> list[dict[str, str]]:
    rows = getattr(markup, "inline_keyboard", None)
    if not rows:
        return []
    buttons: list[dict[str, str]] = []
    for row in rows:
        for button in row:
            label = getattr(button, "text", None)
            callback = getattr(button, "callback_data", None)
            if isinstance(label, str) and label and isinstance(callback, str) and callback:
                buttons.append({"label": label, "callback": callback})
    return buttons


def _reply(text: str, markup: Any) -> dict[str, Any]:
    return {"text": str(text), "buttons": _buttons(markup)}


class _Actor:
    def __init__(self, actor_id: int):
        self.id = actor_id


class _CapturedMessage:
    def __init__(self, text: str, chat_id: int, message_id: int):
        self.text = text
        self.chat_id = chat_id
        self.message_id = message_id
        self.replies: list[dict[str, Any]] = []

    async def reply_text(self, text: str, **kwargs: Any) -> None:
        self.replies.append(_reply(text, kwargs.get("reply_markup")))

    async def edit_message_text(self, text: str, **kwargs: Any) -> None:
        self.replies.append(_reply(text, kwargs.get("reply_markup")))

    async def edit_message_reply_markup(self, **kwargs: Any) -> None:
        return None


class _CapturedQuery:
    def __init__(self, data: str, message: _CapturedMessage):
        self.data = data
        self.message = message

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        if kwargs.get("show_alert") and text:
            self.message.replies.append(_reply(text, None))

    async def edit_message_text(self, text: str, **kwargs: Any) -> None:
        await self.message.edit_message_text(text, **kwargs)

    async def edit_message_reply_markup(self, **kwargs: Any) -> None:
        await self.message.edit_message_reply_markup(**kwargs)


class _TextUpdate:
    def __init__(self, user_id: int, text: str, message_id: int):
        chat_id = _private_chat_id(user_id)
        self.effective_user = _Actor(user_id)
        self.effective_chat = _Actor(chat_id)
        self.message = _CapturedMessage(text, chat_id, message_id)
        self.callback_query = None


class _CallbackUpdate:
    def __init__(self, user_id: int, callback: str, message_id: int):
        chat_id = _private_chat_id(user_id)
        message = _CapturedMessage("", chat_id, message_id)
        self.effective_user = _Actor(user_id)
        self.effective_chat = _Actor(chat_id)
        self.message = None
        self.callback_query = _CapturedQuery(callback, message)
