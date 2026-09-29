from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Update, WebAppInfo
from telegram.error import TelegramError
from telegram.ext import ContextTypes

LOGGER = logging.getLogger(__name__)


def dashboard_prompt(public_url: str | None) -> tuple[str, str | None]:
    url = (public_url or "").strip()
    if not url.lower().startswith("https://"):
        return (
            "The dashboard button is not ready yet.\n\n"
            "It needs a public https address saved in Railway as DASHBOARD_PUBLIC_URL. "
            "Until that is set, this button does nothing useful. "
            "Expenses in this chat are unchanged.",
            None,
        )
    return (
        "Open the household dashboard.\n\n"
        "On a computer, open this link in a browser and tap Log in with Telegram:\n"
        f"{url}\n\n"
        "On your phone, tap Dashboard in the bot menu. It opens inside Telegram and already knows who you are.\n\n"
        "After you confirm, that browser remembers you for 30 days so you do not confirm every time. "
        "Use Log out on a shared computer. Log out forgets it immediately.",
        url,
    )


def _is_private_chat(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and getattr(chat, "type", None) == "private"


async def reply_with_dashboard(update: Update, settings) -> None:
    if update.message is None or update.effective_user is None:
        return
    if settings.label_for_user(update.effective_user.id) is None:
        await update.message.reply_text("I do not recognize this Telegram user ID yet.")
        return
    text, url = dashboard_prompt(getattr(settings, "dashboard_public_url", None))
    # Telegram only accepts a web_app button in a private chat. In the household
    # group that button rejects the whole message, so the link is sent as text.
    reply_markup = None
    if url is not None and _is_private_chat(update):
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Open dashboard", web_app=WebAppInfo(url=url))]]
        )
    try:
        await update.message.reply_text(text, reply_markup=reply_markup)
    except TelegramError:
        if reply_markup is None:
            LOGGER.exception("Could not send the dashboard link. Expense chat is unchanged.")
            return
        LOGGER.exception("Telegram rejected the dashboard button. Sending the link as text.")
        try:
            await update.message.reply_text(text)
        except TelegramError:
            LOGGER.exception("Could not send the dashboard link. Expense chat is unchanged.")


async def configure_dashboard_menu(application, settings, dashboard_server, username: str) -> None:
    if dashboard_server is not None and username:
        dashboard_server.dashboard_app.set_bot_username(username)
    url = (getattr(settings, "dashboard_public_url", None) or "").strip()
    if dashboard_server is None or not url.lower().startswith("https://"):
        if dashboard_server is None:
            LOGGER.info("Dashboard page is not running. The Telegram dashboard button stays off.")
        elif not url:
            LOGGER.info("DASHBOARD_PUBLIC_URL is not set. The Telegram dashboard button stays off.")
        else:
            LOGGER.warning("DASHBOARD_PUBLIC_URL must start with https. The Telegram dashboard button stays off.")
        return
    try:
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Dashboard", web_app=WebAppInfo(url=url))
        )
    except TelegramError:
        LOGGER.exception("Could not set the Telegram dashboard button. Expense chat is unchanged.")
    else:
        LOGGER.info("Telegram dashboard menu button is on.")


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data.get("dashboard_settings")
    if settings is None:
        if update.message is not None:
            await update.message.reply_text("The dashboard is not available right now. Expenses in this chat are unchanged.")
        return
    await reply_with_dashboard(update, settings)
