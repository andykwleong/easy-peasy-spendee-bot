from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from getrichbot.categories import ALL_CATEGORIES, FIXED_CATEGORIES, VARIABLE_CATEGORIES
from getrichbot.config import Settings
from getrichbot.models import ExpenseDraft, ExpenseRow
from getrichbot.parser import extract_standalone_date, parse_expense
from getrichbot.sheets import SheetsClient

LOGGER = logging.getLogger(__name__)
SINGAPORE_TZ = ZoneInfo("Asia/Singapore")
HELP_TEXT = """Send expenses naturally:
dinner 60
ntuc 82.30
food 60 yesterday

Multiple entries:
19th May
Food 60
groceries 50
personal care 20

Useful commands:
/help - show this guide
/whoami - show your Telegram ID
/pending - show items needing review
/categories - show categories
/fixed - preview fixed expenses
/confirmfixed - add fixed expenses
/undo - delete your latest logged expense

Plain replies also work:
confirm abc123 as Food
delete last
undo last
"""


@dataclass
class PendingExpense:
    draft: ExpenseDraft
    logged_by: str
    chat_id: int
    message_id: int
    created_at: datetime
    reason: str


class FinanceBot:
    def __init__(self, settings: Settings, sheets: SheetsClient):
        self.settings = settings
        self.sheets = sheets
        self.pending: dict[str, PendingExpense] = {}

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(HELP_TEXT)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        await update.message.reply_text(HELP_TEXT)

    async def whoami(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return
        label = self.settings.label_for_user(update.effective_user.id) or "Not configured"
        await update.message.reply_text(f"Telegram user ID: {update.effective_user.id}\nConfigured as: {label}")

    async def categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Categories:\n" + "\n".join(f"- {category}" for category in ALL_CATEGORIES))

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return

        if await self.handle_plain_language_command(update):
            return

        if await self.handle_multiline_text(update, context):
            return

        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return

        draft = parse_expense(
            update.message.text or "",
            logged_by=logged_by,
            me_label=self.settings.me_label,
            wife_label=self.settings.wife_label,
            today=datetime.now(SINGAPORE_TZ).date(),
        )
        if draft is None:
            return

        if draft.needs_date_confirmation:
            pending_id = self._add_pending(draft, logged_by, update, "date")
            await update.message.reply_text(
                f"I found ${draft.amount:.2f}, but the date is ambiguous.\n"
                f"Pending ID: {pending_id}\n"
                f"Use an exact date: /confirm {pending_id} Food 2026-05-19"
            )
            return

        if draft.category is None or draft.confidence < 0.7:
            pending_id = self._add_pending(draft, logged_by, update, "category")
            await update.message.reply_text(
                f"I found ${draft.amount:.2f}, but need a category.\n"
                f"Pending ID: {pending_id}\n"
                f"Reply: /confirm {pending_id} Food"
            )
            return

        row = self._expense_row(draft, logged_by, update, draft.category, "Confirmed", "Text")
        self.sheets.append_expense(self.settings.raw_expenses_sheet, row)
        await update.message.reply_text(f"Logged ${draft.amount:.2f} to {draft.category}.")

    async def pending_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        if not self.pending:
            await update.message.reply_text("No pending expenses.")
            return

        lines = []
        for pending_id, pending in self.pending.items():
            lines.append(
                f"{pending_id}: ${pending.draft.amount:.2f} | {pending.logged_by} | {pending.draft.description}"
            )
        await update.message.reply_text("Pending expenses:\n" + "\n".join(lines))

    async def confirm_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /confirm <pending_id> <category> [YYYY-MM-DD]")
            return

        pending_id = context.args[0]
        date_override = None
        category_args = context.args[1:]
        if category_args and re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", category_args[-1]):
            date_override = datetime.fromisoformat(category_args[-1]).date()
            category_args = category_args[:-1]
        category = " ".join(category_args).strip()
        category = _normalize_category(category)
        if category not in VARIABLE_CATEGORIES:
            await update.message.reply_text("Unknown category. Use /categories to see valid names.")
            return

        pending = self.pending.pop(pending_id, None)
        if pending is None:
            await update.message.reply_text("Pending ID not found.")
            return

        row = self._expense_row_from_pending(pending, category, "Confirmed", "Text", date_override)
        self.sheets.append_expense(self.settings.raw_expenses_sheet, row)
        await update.message.reply_text(f"Confirmed ${row.amount:.2f} to {category}.")

    async def handle_plain_language_command(self, update: Update) -> bool:
        if update.message is None or update.effective_user is None:
            return False
        text = (update.message.text or "").strip()
        lowered = text.lower()

        if lowered in {"help", "what can you do", "commands"}:
            await update.message.reply_text(HELP_TEXT)
            return True

        if lowered in {"undo", "undo last", "delete last", "remove last"}:
            logged_by = self.settings.label_for_user(update.effective_user.id)
            if logged_by is None:
                await update.message.reply_text("I do not recognize this Telegram user ID yet.")
                return True
            deleted = self.sheets.delete_last_matching_row(self.settings.raw_expenses_sheet, logged_by)
            await update.message.reply_text("Deleted your latest logged expense." if deleted else "I could not find an expense to delete for you.")
            return True

        match = re.match(r"^confirm\s+([a-f0-9]{6})\s+(?:as\s+)?(.+?)(?:\s+on\s+(\d{4}-\d{1,2}-\d{1,2}))?$", lowered, re.IGNORECASE)
        if match is None:
            return False

        pending_id = match.group(1)
        category = _normalize_category(match.group(2).strip())
        date_override = datetime.fromisoformat(match.group(3)).date() if match.group(3) else None
        if category not in VARIABLE_CATEGORIES:
            await update.message.reply_text("I do not recognize that category. Send 'categories' to see the list.")
            return True

        pending = self.pending.pop(pending_id, None)
        if pending is None:
            await update.message.reply_text("I could not find that pending item.")
            return True

        row = self._expense_row_from_pending(pending, category, "Confirmed", "Text", date_override)
        self.sheets.append_expense(self.settings.raw_expenses_sheet, row)
        await update.message.reply_text(f"Confirmed ${row.amount:.2f} to {category}.")
        return True

    async def undo_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return

        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return

        deleted = self.sheets.delete_last_matching_row(self.settings.raw_expenses_sheet, logged_by)
        if deleted:
            await update.message.reply_text("Deleted your latest logged expense.")
        else:
            await update.message.reply_text("I could not find an expense to delete for you.")

    async def fixed_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        fixed = self.sheets.get_fixed_expenses(self.settings.fixed_expenses_sheet)
        if not fixed:
            await update.message.reply_text("No active fixed expenses found in Google Sheets.")
            return

        lines = [f"{item['category']}: ${item['amount']:.2f}" for item in fixed]
        await update.message.reply_text("Active fixed expenses:\n" + "\n".join(lines))

    async def confirm_fixed_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return

        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return

        fixed = self.sheets.get_fixed_expenses(self.settings.fixed_expenses_sheet)
        if not fixed:
            await update.message.reply_text("No active fixed expenses found in Google Sheets.")
            return

        now = datetime.now(SINGAPORE_TZ)
        for item in fixed:
            category = str(item["category"])
            if category not in FIXED_CATEGORIES:
                continue
            row = ExpenseRow(
                timestamp=now,
                logged_by=logged_by,
                raw_input=f"Fixed expense confirmation: {category}",
                amount=item["amount"],
                category=category,
                description=str(item.get("notes") or category),
                input_type="Fixed",
                status="Confirmed",
                telegram_chat_id=update.effective_chat.id,
                telegram_message_id=update.message.message_id,
            )
            self.sheets.append_expense(self.settings.raw_expenses_sheet, row)

        await update.message.reply_text(f"Added {len(fixed)} fixed expenses to raw data.")

    async def handle_multiline_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        if update.message is None or update.effective_user is None:
            return False
        text = update.message.text or ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return False

        shared_date, ambiguous = extract_standalone_date(lines[0], datetime.now(SINGAPORE_TZ).date())
        if shared_date is None and not ambiguous:
            return False
        if ambiguous:
            await update.message.reply_text("The first line looks like an ambiguous date. Please use a clear date like 2026-05-19 or 19 May.")
            return True

        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return True

        logged_count = 0
        pending_ids = []
        for line in lines[1:]:
            draft = parse_expense(
                line,
                logged_by=logged_by,
                me_label=self.settings.me_label,
                wife_label=self.settings.wife_label,
                today=shared_date,
            )
            if draft is None:
                continue
            draft = ExpenseDraft(
                raw_input=f"{lines[0]} | {draft.raw_input}",
                amount=draft.amount,
                category=draft.category,
                description=draft.description,
                confidence=draft.confidence,
                expense_date=shared_date,
                needs_date_confirmation=draft.needs_date_confirmation,
            )
            if draft.category is None or draft.confidence < 0.7 or draft.needs_date_confirmation:
                pending_ids.append(self._add_pending(draft, logged_by, update, "category"))
                continue
            self.sheets.append_expense(
                self.settings.raw_expenses_sheet,
                self._expense_row(draft, logged_by, update, draft.category, "Confirmed", "Text"),
            )
            logged_count += 1

        message = f"Logged {logged_count} expenses for {shared_date.isoformat()}."
        if pending_ids:
            message += "\nPending IDs: " + ", ".join(pending_ids)
        await update.message.reply_text(message)
        return True

    def _add_pending(self, draft: ExpenseDraft, logged_by: str, update: Update, reason: str) -> str:
        pending_id = uuid.uuid4().hex[:6]
        self.pending[pending_id] = PendingExpense(
            draft=draft,
            logged_by=logged_by,
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            created_at=datetime.now(SINGAPORE_TZ),
            reason=reason,
        )
        return pending_id

    def _expense_row(
        self,
        draft: ExpenseDraft,
        logged_by: str,
        update: Update,
        category: str,
        status: str,
        input_type: str,
    ) -> ExpenseRow:
        return ExpenseRow(
            timestamp=self._row_timestamp(draft),
            logged_by=logged_by,
            raw_input=draft.raw_input,
            amount=draft.amount,
            category=category,
            description=draft.description,
            input_type=input_type,
            status=status,
            telegram_chat_id=update.effective_chat.id,
            telegram_message_id=update.message.message_id,
        )

    def _row_timestamp(self, draft: ExpenseDraft) -> datetime:
        now = datetime.now(SINGAPORE_TZ)
        if draft.expense_date is None:
            return now
        return datetime.combine(draft.expense_date, time(hour=now.hour, minute=now.minute, second=now.second), SINGAPORE_TZ)

    def _expense_row_from_pending(
        self,
        pending: PendingExpense,
        category: str,
        status: str,
        input_type: str,
        date_override,
    ) -> ExpenseRow:
        draft = pending.draft
        if date_override is not None:
            draft = ExpenseDraft(
                raw_input=draft.raw_input,
                amount=draft.amount,
                category=draft.category,
                description=draft.description,
                confidence=draft.confidence,
                expense_date=date_override,
                needs_date_confirmation=False,
            )
        return ExpenseRow(
            timestamp=self._row_timestamp(draft),
            logged_by=pending.logged_by,
            raw_input=pending.draft.raw_input,
            amount=pending.draft.amount,
            category=category,
            description=pending.draft.description,
            input_type=input_type,
            status=status,
            telegram_chat_id=pending.chat_id,
            telegram_message_id=pending.message_id,
        )


def _normalize_category(raw: str) -> str:
    lowered = raw.strip().lower()
    for category in ALL_CATEGORIES:
        if category.lower() == lowered:
            return category
    for category in ALL_CATEGORIES:
        if category.lower().startswith(lowered):
            return category
    return raw


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.load()
    LOGGER.info("Starting GetRichBot. Raw sheet: %s. Fixed sheet: %s.", settings.raw_expenses_sheet, settings.fixed_expenses_sheet)
    sheets = SheetsClient(settings.google_sheet_id, settings.service_account_file)
    finance_bot = FinanceBot(settings, sheets)

    application = Application.builder().token(settings.telegram_bot_token).build()
    application.add_handler(CommandHandler("start", finance_bot.start))
    application.add_handler(CommandHandler("help", finance_bot.help_command))
    application.add_handler(CommandHandler("whoami", finance_bot.whoami))
    application.add_handler(CommandHandler("categories", finance_bot.categories))
    application.add_handler(CommandHandler("pending", finance_bot.pending_command))
    application.add_handler(CommandHandler("confirm", finance_bot.confirm_command))
    application.add_handler(CommandHandler("undo", finance_bot.undo_command))
    application.add_handler(CommandHandler("fixed", finance_bot.fixed_command))
    application.add_handler(CommandHandler("confirmfixed", finance_bot.confirm_fixed_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, finance_bot.handle_text))

    LOGGER.info("Bot is ready. Open Telegram and send /whoami in the group chat.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
