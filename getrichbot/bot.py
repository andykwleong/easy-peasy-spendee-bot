from __future__ import annotations

import asyncio
import calendar
import logging
import traceback
import re
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from datetime import time
from datetime import datetime
from datetime import timedelta
from time import perf_counter
from zoneinfo import ZoneInfo

from getrichbot.categories import ALL_CATEGORIES, CATEGORY_ALIASES, FIXED_CATEGORIES, VARIABLE_CATEGORIES, category_config_status, configure_category_config
from getrichbot.config import Settings
from getrichbot.cards import PaymentConfig, build_card_summary, format_card_summary
from getrichbot.image_utils import prepare_image_for_vision
from getrichbot.models import ExpenseDraft, ExpenseRecord, ExpenseRow
from getrichbot.parser import categorize_description, extract_date_phrase, extract_standalone_date, parse_expense, parse_expenses
from getrichbot.sheets import SheetsClient
from getrichbot.summary import (
    build_personal_expense_history,
    build_spending_summary,
    expense_history_clarification,
    format_personal_expense_history,
    format_spending_summary,
    looks_like_expense_history_request,
    parse_expense_history_period,
    parse_summary_period,
)
from getrichbot.summary import build_monthly_summary_table

LOGGER = logging.getLogger(__name__)
SINGAPORE_TZ = ZoneInfo("Asia/Singapore")
RECENT_DUPLICATE_WINDOW = timedelta(minutes=1)
INCOME_CATEGORY_CALLBACK = "income_category"
PAYMENT_METHOD_CALLBACK = "payment_method"
PAYMENT_CONFIG_CACHE_WINDOW = timedelta(minutes=1)
HELP_TEXT = """Send expenses naturally:
dinner 60
ntuc 82.30
food 60 yesterday
income 15 June 2026 15020.33

Multiple entries:
19th May
Food 60
groceries 50
personal care 20

Useful commands:
/help - show this guide
/whoami - show your Telegram ID
/pending - show items needing review
/categories or /category - show categories
/refreshcategories - reload categories from Google Sheets
/refreshpayments - reload payment methods and card limits from Google Sheets
/summary - show this month's checkpoint
/cards - show your card spending against current limits
/fixed - preview fixed expenses
/confirmfixed - review fixed expenses
/undo - delete your latest logged expense

Plain replies also work:
confirm abc123 as Food
confirm abc123
delete last
undo last
delete e1a2b3

Screenshots and voice notes work too when OpenAI is configured. I will ask before logging them.
"""


@dataclass
class PendingExpense:
    draft: ExpenseDraft
    logged_by: str
    chat_id: int
    message_id: int
    created_at: datetime
    reason: str
    batch_id: str | None = None
    category_options: tuple[str, ...] = ()
    input_type: str = "Text"
    payment_options: tuple[str, ...] = ()


@dataclass
class PendingDelete:
    record: ExpenseRecord
    chat_id: int
    requested_by_user_id: int
    created_at: datetime
    logged_by_restriction: str | None = None


@dataclass
class PendingDuplicate:
    row: ExpenseRow
    existing_record: ExpenseRecord
    chat_id: int
    requested_by_user_id: int
    created_at: datetime
    pending_id: str | None = None


@dataclass
class RecentLoggedExpense:
    row: ExpenseRow
    logged_at: datetime


@dataclass
class PendingEditChange:
    record: ExpenseRecord
    amount: Decimal | None = None
    category: str | None = None
    description: str | None = None
    expense_date: str | None = None


@dataclass
class PendingEdit:
    changes: list[PendingEditChange]
    chat_id: int
    requested_by_user_id: int
    created_at: datetime


@dataclass
class PendingEditDate:
    record: ExpenseRecord
    chat_id: int
    requested_by_user_id: int
    created_at: datetime


@dataclass
class FixedReview:
    month_date: object
    items: list[dict[str, str | Decimal]]
    chat_id: int | str
    created_at: datetime


@dataclass
class FixedAddResult:
    added_count: int = 0


@dataclass
class PendingPaymentBatch:
    pending_ids: list[str]
    chat_id: int
    requested_by_user_id: int


@dataclass
class CachedPaymentConfig:
    config: PaymentConfig
    loaded_at: datetime


class FinanceBot:
    def __init__(self, settings: Settings, sheets: SheetsClient):
        self.settings = settings
        self.sheets = sheets
        self.pending: dict[str, PendingExpense] = {}
        self.pending_deletes: dict[tuple[int, int], PendingDelete] = {}
        self.pending_duplicates: dict[tuple[int, int], PendingDuplicate] = {}
        self.pending_edits: dict[tuple[int, int], PendingEdit] = {}
        self.pending_edit_dates: dict[tuple[int, int], PendingEditDate] = {}
        self.pending_fixed_reviews: dict[int | str, FixedReview] = {}
        self.latest_pending_batch: dict[tuple[int, int], str] = {}
        self.pending_payment_batches: dict[tuple[int, int], PendingPaymentBatch] = {}
        self.payment_config_cache: CachedPaymentConfig | None = None
        self.recent_logged: list[RecentLoggedExpense] = []
        self.ai = None

    def _ai(self):
        if not self.settings.openai_api_key:
            return None
        if self.ai is None:
            from getrichbot.ai import AIInterpreter

            self.ai = AIInterpreter(self.settings.openai_api_key, self.settings.openai_model)
        return self.ai

    def _load_category_config_from_sheets(self) -> dict:
        return load_category_config_from_sheets(self.settings, self.sheets)

    def _payment_tracking_enabled(self) -> bool:
        return hasattr(self.settings, "payment_methods_sheet") and hasattr(self.sheets, "get_payment_config")

    def _load_payment_config(self, force: bool = False) -> PaymentConfig:
        if not self._payment_tracking_enabled():
            raise RuntimeError("Payment tracking is not configured.")
        now = datetime.now(SINGAPORE_TZ)
        if (
            not force
            and self.payment_config_cache is not None
            and now - self.payment_config_cache.loaded_at < PAYMENT_CONFIG_CACHE_WINDOW
        ):
            return self.payment_config_cache.config
        config = self.sheets.get_payment_config(
            self.settings.payment_methods_sheet,
            self.settings.card_limits_sheet,
        )
        self.payment_config_cache = CachedPaymentConfig(config=config, loaded_at=now)
        return config

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
        await update.message.reply_text(
            f"Telegram user ID: {update.effective_user.id}\n"
            f"Telegram chat ID: {update.effective_chat.id}\n"
            f"Configured as: {label}"
        )

    async def categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Categories:\n" + "\n".join(f"- {category}" for category in ALL_CATEGORIES))

    async def category_debug(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return
        if self.settings.label_for_user(update.effective_user.id) is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return
        status = category_config_status()
        preview = "\n".join(f"- {category}" for category in status["categories"][:15])
        await update.message.reply_text(
            "Category config debug:\n\n"
            f"Source: {status['source']}\n"
            f"Variable categories: {status['variable_count']}\n"
            f"Fixed categories: {status['fixed_count']}\n\n"
            "First categories:\n"
            f"{preview}"
        )

    async def refresh_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return
        if self.settings.label_for_user(update.effective_user.id) is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return

        config = self._load_category_config_from_sheets()
        configure_category_config(config)
        LOGGER.info(
            "Refreshed %d variable and %d fixed categories from Google Sheets tab %s. Keywords tab: %s.",
            len(VARIABLE_CATEGORIES),
            len(FIXED_CATEGORIES),
            config.get("categories_sheet_loaded"),
            config.get("keywords_sheet_loaded"),
        )
        await update.message.reply_text(
            "Categories refreshed from Google Sheets.\n\n"
            f"Variable categories: {len(VARIABLE_CATEGORIES)}\n"
            f"Fixed categories: {len(FIXED_CATEGORIES)}"
        )

    async def refresh_payments(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return
        if self.settings.label_for_user(update.effective_user.id) is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return
        try:
            config = self._load_payment_config(force=True)
        except (RuntimeError, ValueError) as exc:
            await update.message.reply_text(f"Payment setup could not be loaded: {exc}")
            return
        await update.message.reply_text(
            "Payment methods refreshed from Google Sheets.\n\n"
            f"Active payment methods: {len(config.payment_methods)}\n"
            f"Active card limits: {len(config.card_limits)}"
        )

    async def cards_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply_with_card_summary(update)

    async def summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        text = "summary " + " ".join(context.args)
        await self._reply_with_summary(update, text)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return

        if await self.handle_plain_language_command(update):
            return

        if await self.handle_pending_update(update):
            return

        if await self.handle_ai_command(update):
            return

        if await self.handle_multiline_text(update, context):
            return

        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return

        drafts = parse_expenses(
            update.message.text or "",
            logged_by=logged_by,
            me_label=self.settings.me_label,
            wife_label=self.settings.wife_label,
            today=datetime.now(SINGAPORE_TZ).date(),
        )
        if len(drafts) > 1:
            await self._log_multiline_drafts(update, logged_by, drafts)
            return

        draft = drafts[0] if drafts else None
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
            income_categories = self._income_category_options(draft)
            if income_categories:
                pending_id = self._add_pending(
                    draft,
                    logged_by,
                    update,
                    "income_category",
                    category_options=income_categories,
                )
                expense_date = draft.expense_date or datetime.now(SINGAPORE_TZ).date()
                await update.message.reply_text(
                    f"I found income of {_format_money(draft.amount)} for {self._human_date(expense_date)}.\n\n"
                    "Which income category?",
                    reply_markup=self._income_category_keyboard(pending_id, income_categories),
                )
                return

            pending_id = self._add_pending(draft, logged_by, update, "category")
            await update.message.reply_text(
                f"I found ${draft.amount:.2f}, but need a category.\n"
                f"Pending ID: {pending_id}\n"
                "Reply with the category, for example: Food"
            )
            return

        logged_line = await self._record_or_request_payment(update, draft, logged_by, draft.category, "Text")
        if logged_line:
            await update.message.reply_text(logged_line)

    def _income_category_options(self, draft: ExpenseDraft) -> tuple[str, ...]:
        if re.search(r"\bincome\b", draft.raw_input, flags=re.IGNORECASE) is None:
            return ()
        return tuple(category for category in VARIABLE_CATEGORIES if category.lower().startswith("income -"))

    def _income_category_keyboard(self, pending_id: str, categories: tuple[str, ...]):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        buttons = [
            InlineKeyboardButton(
                category,
                callback_data=f"{INCOME_CATEGORY_CALLBACK}|{pending_id}|{index}",
            )
            for index, category in enumerate(categories)
        ]
        rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
        return InlineKeyboardMarkup(rows)

    async def handle_income_category_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or update.effective_user is None:
            return

        parts = (query.data or "").split("|")
        if len(parts) != 3 or parts[0] != INCOME_CATEGORY_CALLBACK:
            return

        pending_id = parts[1]
        try:
            option_index = int(parts[2])
        except ValueError:
            await query.answer("That income option is invalid.", show_alert=True)
            return

        pending = self.pending.get(pending_id)
        if pending is None:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer("This pending income is no longer available.", show_alert=True)
            return

        logged_by = self.settings.label_for_user(update.effective_user.id)
        message_chat_id = query.message.chat_id if query.message is not None else None
        if logged_by is None or logged_by != pending.logged_by or message_chat_id != pending.chat_id:
            await query.answer("Only the person who submitted this income can choose its category.", show_alert=True)
            return

        if option_index < 0 or option_index >= len(pending.category_options):
            await query.answer("That income option is no longer valid.", show_alert=True)
            return

        category = pending.category_options[option_index]
        if category not in VARIABLE_CATEGORIES or not category.lower().startswith("income -"):
            await query.answer("That income category is no longer active.", show_alert=True)
            return

        await query.answer()
        self.pending.pop(pending_id, None)
        await query.edit_message_reply_markup(reply_markup=None)
        row = self._expense_row_from_pending(pending, category, "Confirmed", "Text", None)
        logged_line = await self._append_or_hold_duplicate(update, row)
        if logged_line:
            await query.edit_message_text(logged_line)

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
        if len(context.args) < 1:
            latest_matching = self._matching_pending(update, latest_batch_only=True)
            if latest_matching or self._matching_pending(update):
                await self._confirm_all_pending(update, latest_batch_only=bool(latest_matching))
                return
            await update.message.reply_text("No pending expenses to confirm.")
            return

        if context.args[0].lower() == "all":
            await self._confirm_all_pending(update)
            return

        pending_id = context.args[0]
        date_override = None
        category_args = context.args[1:]
        if category_args and re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", category_args[-1]):
            date_override = datetime.fromisoformat(category_args[-1]).date()
            category_args = category_args[:-1]
        category = " ".join(category_args).strip()
        pending = self.pending.get(pending_id)
        if pending is None:
            await update.message.reply_text("Pending ID not found.")
            return
        category = _normalize_category(category) if category else pending.draft.category or ""
        if category not in VARIABLE_CATEGORIES:
            await update.message.reply_text("Unknown category. Use /categories to see valid names.")
            return

        logged_line = await self._record_pending_or_request_payment(update, pending_id, pending, category, date_override)
        if logged_line:
            await update.message.reply_text(logged_line)

    async def handle_ai_command(self, update: Update) -> bool:
        ai = self._ai()
        if ai is None or update.message is None or update.effective_user is None:
            return False

        text = (update.message.text or "").strip()
        if not _looks_like_ai_request(text):
            return False

        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return True

        records = self.sheets.get_expense_records(self.settings.raw_expenses_sheet)
        intent = ai.interpret(text, records, datetime.now(SINGAPORE_TZ).date(), logged_by)
        by_id = {record.entry_id: record for record in records}

        if intent.action == "clarify":
            record = self._clarification_record(intent, by_id)
            if record is not None and (
                intent.clarification_field == "date" or _is_date_change_request(text)
            ):
                await self._ask_for_edit_date(update, record)
                return True
            await update.message.reply_text(intent.clarification_question or "Which expense should I use?")
            return True

        if intent.action == "answer":
            await update.message.reply_text(intent.answer or "I could not find a matching expense.")
            return True

        if intent.action == "delete":
            matches = []
            for entry_id in intent.entry_ids:
                record = by_id.get(entry_id)
                if record is not None:
                    matches.append(record)
            if not matches:
                await update.message.reply_text("I could not find a matching entry to delete.")
            elif len(matches) > 1:
                lines = ["I found multiple possible deletes. Please delete one at a time:"]
                lines.extend(self._delete_candidate_line(record) for record in matches)
                await update.message.reply_text("\n\n".join(lines))
            else:
                await self._ask_delete_confirmation(update, matches[0])
            return True

        if intent.action == "edit":
            changes = []
            for update_item in intent.updates:
                record = by_id.get(update_item.entry_id)
                if record is None:
                    continue
                category = _normalize_category(update_item.category) if update_item.category else None
                if category is not None and category not in ALL_CATEGORIES:
                    await update.message.reply_text(f"I do not recognize this category: {update_item.category}")
                    return True
                amount = None
                if update_item.amount is not None:
                    try:
                        amount = Decimal(str(update_item.amount))
                    except InvalidOperation:
                        await update.message.reply_text(f"I could not read this amount: {update_item.amount}")
                        return True
                changes.append(PendingEditChange(
                    record=record,
                    amount=amount,
                    category=category,
                    description=update_item.description,
                    expense_date=update_item.date,
                ))
            if changes:
                if _is_date_change_request(text) and len(changes) == 1 and changes[0].expense_date is None:
                    await self._ask_for_edit_date(update, changes[0].record)
                    return True
                await self._ask_edit_confirmation(update, changes)
            else:
                await update.message.reply_text("I could not find a matching entry to update.")
            return True

        return False

    async def handle_plain_language_command(self, update: Update) -> bool:
        if update.message is None or update.effective_user is None:
            return False
        text = (update.message.text or "").strip()
        lowered = text.lower()

        if await self._handle_pending_edit_date_reply(update, text, lowered):
            return True

        if await self._handle_pending_edit_reply(update, lowered):
            return True

        if await self._handle_pending_delete_reply(update, lowered):
            return True

        if await self._handle_pending_duplicate_reply(update, lowered):
            return True

        if lowered in {"help", "what can you do", "commands"}:
            await update.message.reply_text(HELP_TEXT)
            return True

        if re.search(r"\b(card|cards)\s+summary\b", lowered) or lowered in {"cards", "my cards"}:
            await self._reply_with_card_summary(update)
            return True

        if await self._reply_with_personal_expense_history(update, text):
            return True

        if re.search(r"\bsummary\b", lowered):
            await self._reply_with_summary(update, text)
            return True

        if await self._handle_pending_fixed_review(update, text):
            return True

        if lowered.startswith(("confirm fixed", "confirmfixed", "fixed expenses")) or lowered in {"log fixed", "log fixed expenses"}:
            await self._start_fixed_review(update, text)
            return True

        if any(phrase in lowered for phrase in ["change spend date", "change spending date", "change logged date", "change expense date"]):
            await self._change_latest_logged_date(update, text)
            return True

        if lowered in {"undo", "undo last", "delete last", "remove last"}:
            logged_by = self.settings.label_for_user(update.effective_user.id)
            if logged_by is None:
                await update.message.reply_text("I do not recognize this Telegram user ID yet.")
                return True
            record = self.sheets.get_last_matching_record(self.settings.raw_expenses_sheet, logged_by)
            if record is None:
                await update.message.reply_text("I could not find an expense to delete for you.")
                return True
            await self._ask_delete_confirmation(update, record, logged_by_restriction=logged_by)
            return True

        delete_match = re.match(r"^(?:delete|remove)\s+([a-z0-9]{6})$", lowered, re.IGNORECASE)
        if delete_match is not None:
            logged_by = self.settings.label_for_user(update.effective_user.id)
            if logged_by is None:
                await update.message.reply_text("I do not recognize this Telegram user ID yet.")
                return True
            record = self.sheets.get_record_by_id(
                self.settings.raw_expenses_sheet,
                delete_match.group(1),
                logged_by=logged_by,
            )
            if record is None:
                await update.message.reply_text("I could not find that expense under your entries.")
                return True
            await self._ask_delete_confirmation(update, record, logged_by_restriction=logged_by)
            return True

        bare_entry_id_match = re.fullmatch(r"[a-f0-9]{6}", lowered, re.IGNORECASE)
        if bare_entry_id_match is not None:
            logged_by = self.settings.label_for_user(update.effective_user.id)
            if logged_by is None:
                await update.message.reply_text("I do not recognize this Telegram user ID yet.")
                return True
            record = self.sheets.get_record_by_id(
                self.settings.raw_expenses_sheet,
                bare_entry_id_match.group(0),
                logged_by=logged_by,
            )
            if record is None:
                await update.message.reply_text("I could not find that expense under your entries.")
                return True
            await self._ask_delete_confirmation(update, record, logged_by_restriction=logged_by)
            return True

        if lowered in {"confirm all", "log all", "extract all"}:
            if self._payment_choice_is_active(update):
                await update.message.reply_text("Please choose a payment method using the buttons above, or reply cancel to discard these pending expenses.")
                return True
            await self._confirm_all_pending(update, latest_batch_only=bool(self._matching_pending(update, latest_batch_only=True)))
            return True

        match = re.match(r"^confirm\s+([a-f0-9]{6})(?:\s+(?:as\s+)?(.+?))?(?:\s+on\s+(\d{4}-\d{1,2}-\d{1,2}))?$", lowered, re.IGNORECASE)
        if match is None:
            return False

        pending_id = match.group(1)
        pending = self.pending.get(pending_id)
        if pending is None:
            await update.message.reply_text("I could not find that pending item.")
            return True

        category_raw = (match.group(2) or "").strip()
        category = _normalize_category(category_raw) if category_raw else pending.draft.category or ""
        date_override = datetime.fromisoformat(match.group(3)).date() if match.group(3) else None
        if category not in VARIABLE_CATEGORIES:
            await update.message.reply_text("I do not recognize that category. Send 'categories' to see the list.")
            return True

        logged_line = await self._record_pending_or_request_payment(update, pending_id, pending, category, date_override)
        if logged_line:
            await update.message.reply_text(logged_line)
        return True

    async def handle_pending_update(self, update: Update) -> bool:
        if update.message is None or update.effective_user is None:
            return False

        return await self._handle_pending_text(update, update.message.text or "")

    async def _handle_pending_text(self, update: Update, text: str) -> bool:
        if update.message is None or update.effective_user is None:
            return False

        matching = self._matching_pending(update, latest_batch_only=True)
        if not matching:
            matching = self._matching_pending(update)
            if not matching:
                return False

        text = text.strip()
        lowered = text.lower()
        today = datetime.now(SINGAPORE_TZ).date()

        if lowered in {"cancel", "no", "stop", "never mind", "nevermind"}:
            cancelled = self._cancel_visible_pending(update)
            self._clear_payment_batch(update)
            if cancelled:
                await update.message.reply_text("Pending expenses cancelled.")
            else:
                await update.message.reply_text("No pending expenses to cancel.")
            return True

        if self._payment_choice_is_active(update):
            await update.message.reply_text("Please choose a payment method using the buttons above, or reply cancel to discard these pending expenses.")
            return True

        if lowered in {"confirm all", "confirmed all", "log all", "extract all"}:
            await self._confirm_all_pending(update, latest_batch_only=bool(self._matching_pending(update, latest_batch_only=True)))
            return True

        if lowered in {"confirm", "confirmed", "confirm them", "confirmed them", "log it", "log them"}:
            await self._confirm_all_pending(update, latest_batch_only=bool(self._matching_pending(update, latest_batch_only=True)))
            return True

        if lowered in {"yes", "ok", "okay", "looks good", "correct"}:
            await update.message.reply_text(self._pending_summary(update, "You still have pending expenses. Please reply with confirm all or cancel:"))
            return True

        selected_positions = _parse_confirm_positions(lowered, len(matching))
        if selected_positions:
            logged_lines = await self._confirm_pending_positions(
                update,
                selected_positions,
                latest_batch_only=bool(self._latest_pending_batch_id(update)),
                cancel_unselected=True,
            )
            if logged_lines:
                await update.message.reply_text("\n\n".join(logged_lines))
            return True

        category_updates, invalid_categories = self._parse_pending_category_changes(lowered, len(matching))
        if invalid_categories:
            await update.message.reply_text(
                "I do not recognize this category: "
                + ", ".join(invalid_categories)
                + "\n\nSend: categories"
            )
            return True
        if category_updates:
            self._update_pending_position_categories(update, category_updates)
            await update.message.reply_text(self._pending_summary(update, "Updated pending entries:"))
            return True

        position_match = re.fullmatch(r"confirm\s+(\d+|first|second|third|fourth|fifth)(?:\s+(?:as\s+)?(.+))?", lowered)
        if position_match is not None:
            position = _position_from_text(position_match.group(1))
            category = _normalize_category(position_match.group(2)) if position_match.group(2) else None
            logged_lines = await self._confirm_pending_positions(
                update,
                [position],
                category,
                latest_batch_only=bool(self._latest_pending_batch_id(update)),
            )
            if logged_lines:
                await update.message.reply_text("\n\n".join(logged_lines))
            else:
                await update.message.reply_text(self._pending_summary(update, "That pending entry still needs a category:"))
            return True

        category_reply = _normalize_category(lowered)
        if category_reply in VARIABLE_CATEGORIES:
            logged_lines, changed = await self._apply_category_reply(update, category_reply)
            if logged_lines:
                await update.message.reply_text("\n\n".join(logged_lines))
                return True
            if changed:
                await update.message.reply_text(self._pending_summary(update, f"Updated pending category to {category_reply}:"))
                return True

        if any(phrase in lowered for phrase in ["change date", "date to", "make it", "make them", "both", "all", "were yesterday", "was yesterday"]):
            parsed_date, ambiguous = extract_date_phrase(text, today)
            if ambiguous:
                await update.message.reply_text("That date is ambiguous. Try: change date to 2026-05-19 or change date to 19 May.")
                return True
            if parsed_date is not None:
                changed = self._update_pending_dates(update, parsed_date)
                if changed:
                    await update.message.reply_text(self._pending_summary(update, f"Updated pending date to {self._human_date(parsed_date)}:"))
                    return True

        if self.settings.openai_api_key and any(word in lowered for word in ["first", "second", "third", "fourth", "entry", "one", "confirm", "change", "update"]):
            handled = await self._handle_ai_pending_instruction(update, text)
            if handled:
                return True

        if _looks_like_pending_position_request(lowered):
            await update.message.reply_text(
                self._pending_summary(update, "I could not apply that change. Current pending entries:")
            )
            return True

        await update.message.reply_text(self._pending_summary(update, "You still have pending expenses. Please confirm or cancel them first:"))
        return True

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return
        if not update.message.photo:
            return
        photo = update.message.photo[-1]
        await self._handle_image_file(update, context, photo.file_id, "image/jpeg")

    async def handle_image_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None or update.message.document is None:
            return
        mime_type = update.message.document.mime_type or ""
        if not mime_type.startswith("image/"):
            return
        await self._handle_image_file(update, context, update.message.document.file_id, mime_type)

    async def _handle_image_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE, file_id: str, mime_type: str) -> None:
        started_at = perf_counter()
        try:
            logged_by = self.settings.label_for_user(update.effective_user.id)
            if logged_by is None:
                await update.message.reply_text("I do not recognize this Telegram user ID yet.")
                return
            if self._matching_pending(update):
                await update.message.reply_text(self._pending_summary(update, "You still have pending expenses. Please confirm or cancel them first:"))
                return

            await update.message.reply_text("Reading screenshot...")
            download_started_at = perf_counter()
            file = await context.bot.get_file(file_id)
            image_bytes = bytes(await file.download_as_bytearray())
            LOGGER.info(
                "Screenshot downloaded in %.2fs (%d bytes).",
                perf_counter() - download_started_at,
                len(image_bytes),
            )

            prepare_started_at = perf_counter()
            prepared_image = prepare_image_for_vision(image_bytes, mime_type)
            LOGGER.info(
                "Screenshot prepared in %.2fs: %d -> %d bytes, size %s -> %s, mime %s -> %s%s.",
                perf_counter() - prepare_started_at,
                prepared_image.original_bytes,
                prepared_image.prepared_bytes,
                prepared_image.original_size,
                prepared_image.prepared_size,
                mime_type,
                prepared_image.mime_type,
                f" ({prepared_image.note})" if prepared_image.note else "",
            )
            ai = self._ai()
            if ai is None:
                await update.message.reply_text("Screenshot parsing needs OPENAI_API_KEY in .env first.")
                return

            extraction_started_at = perf_counter()
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    ai.extract_from_image,
                    image_bytes=prepared_image.content,
                    mime_type=prepared_image.mime_type,
                    today=datetime.now(SINGAPORE_TZ).date(),
                    logged_by=logged_by,
                ),
                timeout=75,
            )
            LOGGER.info("OpenAI screenshot extraction finished in %.2fs.", perf_counter() - extraction_started_at)

            if not result.found_expenses or not result.expenses:
                await update.message.reply_text(result.clarification_question or "I could not find a clear expense in that screenshot.")
                return

            await update.message.reply_text(self._pending_extractions_message(result.expenses, logged_by, update, "screenshot", "I found these screenshot expenses:"))
        except Exception as exc:
            LOGGER.exception("Screenshot handling failed after %.2fs", perf_counter() - started_at)
            short_error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            await update.message.reply_text(f"Screenshot parsing failed: {short_error}")

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return
        ai = self._ai()
        if ai is None:
            await update.message.reply_text("Voice parsing needs OPENAI_API_KEY in .env first.")
            return

        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return
        if self._matching_pending(update):
            await update.message.reply_text(self._pending_summary(update, "You still have pending expenses. Please confirm or cancel them first:"))
            return

        media = update.message.voice or update.message.audio
        if media is None:
            return
        file = await context.bot.get_file(media.file_id)
        audio_bytes = bytes(await file.download_as_bytearray())
        filename = "voice.ogg" if update.message.voice else "audio.m4a"
        await update.message.reply_text("Transcribing voice note...")
        transcript = await asyncio.wait_for(
            asyncio.to_thread(ai.transcribe_audio, audio_bytes, filename=filename),
            timeout=75,
        )
        if not transcript:
            await update.message.reply_text("I could not transcribe that voice note.")
            return

        if await self._handle_pending_text(update, transcript):
            return

        result = await asyncio.wait_for(
            asyncio.to_thread(ai.extract_from_text, transcript, datetime.now(SINGAPORE_TZ).date(), logged_by),
            timeout=75,
        )
        if not result.found_expenses or not result.expenses:
            await update.message.reply_text(f"I heard: {transcript}\n{result.clarification_question or 'But I could not find a clear expense.'}")
            return

        message = self._pending_extractions_message(
            result.expenses,
            logged_by,
            update,
            "voice",
            f'I heard: "{transcript}"\nHere are the expenses:',
        )
        await update.message.reply_text(message)

    async def undo_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return

        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return

        record = self.sheets.get_last_matching_record(self.settings.raw_expenses_sheet, logged_by)
        if record is None:
            await update.message.reply_text("I could not find an expense to delete for you.")
            return
        await self._ask_delete_confirmation(update, record, logged_by_restriction=logged_by)

    async def fixed_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        try:
            fixed = self.sheets.get_fixed_expenses(self.settings.fixed_expenses_sheet)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        if not fixed:
            await update.message.reply_text("No active fixed expenses found in Google Sheets.")
            return

        lines = [f"{item['category']}: ${item['amount']:.2f}" for item in fixed]
        await update.message.reply_text("Active fixed expenses:\n" + "\n".join(lines))

    async def confirm_fixed_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None:
            return

        text = "confirm fixed " + " ".join(context.args)
        if await self._handle_pending_fixed_review(update, text.strip()):
            return
        await self._start_fixed_review(update, text.strip())

    async def _start_fixed_review(self, update: Update, text: str) -> None:
        if update.message is None or update.effective_user is None:
            return
        if self.settings.label_for_user(update.effective_user.id) is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return

        month_date = _parse_fixed_month(text, datetime.now(SINGAPORE_TZ).date())
        try:
            fixed = self.sheets.get_fixed_expenses(self.settings.fixed_expenses_sheet)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        if not fixed:
            await update.message.reply_text(f"No active fixed expenses found for {_month_label(month_date)}.")
            return

        review = FixedReview(
            month_date=month_date,
            items=[dict(item) for item in fixed],
            chat_id=update.effective_chat.id,
            created_at=datetime.now(SINGAPORE_TZ),
        )
        self.pending_fixed_reviews[update.effective_chat.id] = review
        await update.message.reply_text(self._fixed_review_message(review))

    async def _handle_pending_fixed_review(self, update: Update, text: str) -> bool:
        if update.message is None or update.effective_user is None:
            return False
        review = self.pending_fixed_reviews.get(update.effective_chat.id)
        if review is None:
            return False
        lowered = text.strip().lower()

        if lowered in {"cancel", "cancel fixed", "no", "stop", "never mind", "nevermind"}:
            self.pending_fixed_reviews.pop(update.effective_chat.id, None)
            await update.message.reply_text("Fixed expenses confirmation cancelled.")
            return True

        if lowered in {
            "confirm",
            "confirmed",
            "confirm fixed",
            "confirmed fixed",
            "confirm fixed expenses",
            "confirmed fixed expenses",
            "yes",
            "y",
            "ok",
            "okay",
            "looks good",
            "correct",
            "yes confirm fixed",
        }:
            await self._confirm_fixed_review(update, review)
            return True

        updates, unknown = self._parse_fixed_review_updates(text, review)
        if updates:
            for index, amount in updates.items():
                review.items[index]["amount"] = amount
            await update.message.reply_text(self._fixed_review_message(review))
            return True
        if unknown:
            await update.message.reply_text(
                "I could not match these fixed expense names:\n"
                + "\n".join(f"- {item}" for item in unknown)
                + "\n\nPlease use the category name shown in the fixed expenses list."
            )
            return True
        return False

    async def _confirm_fixed_review(self, update: Update, review: FixedReview) -> None:
        if update.message is None or update.effective_user is None:
            return
        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return
        result = self._add_fixed_expenses_for_month(
            month_date=review.month_date,
            logged_by=logged_by,
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            fixed_items=review.items,
        )
        self.pending_fixed_reviews.pop(update.effective_chat.id, None)
        target_month = _month_label(review.month_date)
        self._refresh_monthly_summary(
            review.month_date.strftime("%Y-%m"),
            fixed_month_date=review.month_date,
            fixed_items=review.items,
        )
        await update.message.reply_text(self._fixed_add_result_message(result, target_month))

    def _fixed_review_message(self, review: FixedReview) -> str:
        lines = [f"Confirm fixed expenses for {_month_label(review.month_date)}:"]
        lines.extend(f"{item['category']}: {_format_money(item['amount'])}" for item in review.items)
        lines.append("Reply: confirm fixed")
        lines.append("Or say: change Income Tax Andy to 30")
        return "\n\n".join(lines)

    def _parse_fixed_review_updates(self, text: str, review: FixedReview) -> tuple[dict[int, Decimal], list[str]]:
        updates: dict[int, Decimal] = {}
        unknown: list[str] = []
        parts = re.split(r"\s+(?:and|,)\s+", text, flags=re.IGNORECASE)
        patterns = [
            re.compile(
                r"^\s*(?P<name>.+?)\s+(?:change|changed|update|set|make)\s+(?:(?:to|as)\s+)?"
                r"\$?(?P<amount>\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*$",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"^\s*(?:change|changed|update|set|make)\s+(?P<name>.+?)\s+(?:to|as)\s+"
                r"\$?(?P<amount>\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*$",
                flags=re.IGNORECASE,
            ),
        ]
        for part in parts:
            match = None
            for pattern in patterns:
                match = pattern.match(part)
                if match is not None:
                    break
            if match is None:
                continue
            amount = Decimal(match.group("amount").replace(",", ""))
            name = match.group("name").strip()
            index = self._match_fixed_review_item(name, review)
            if index is None:
                unknown.append(name)
                continue
            updates[index] = amount
        return updates, unknown

    def _match_fixed_review_item(self, raw_name: str, review: FixedReview) -> int | None:
        wanted = _normalize_lookup_text(raw_name)
        if not wanted:
            return None
        matches = [
            index
            for index, item in enumerate(review.items)
            if wanted == _normalize_lookup_text(str(item["category"]))
        ]
        if len(matches) == 1:
            return matches[0]
        matches = [
            index
            for index, item in enumerate(review.items)
            if wanted in _normalize_lookup_text(str(item["category"]))
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    async def _confirm_fixed_for_month(self, update: Update, month_date) -> None:
        if update.message is None or update.effective_user is None:
            return

        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return

        result = self._add_fixed_expenses_for_month(
            month_date=month_date,
            logged_by=logged_by,
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
        target_month = _month_label(month_date)
        fixed_items = self.sheets.get_fixed_expenses(self.settings.fixed_expenses_sheet)
        self._refresh_monthly_summary(
            month_date.strftime("%Y-%m"),
            fixed_month_date=month_date,
            fixed_items=fixed_items,
        )
        await update.message.reply_text(self._fixed_add_result_message(result, target_month))

    def _fixed_add_result_message(self, result: FixedAddResult, target_month: str) -> str:
        if result.added_count:
            lines = [f"Added {result.added_count} fixed expenses for {target_month}."]
        else:
            lines = [f"No active fixed expenses found for {target_month}."]
        lines.append("Monthly Summary updated.")
        return "\n\n".join(lines)

    async def handle_multiline_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        if update.message is None or update.effective_user is None:
            return False
        text = update.message.text or ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return False

        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return True

        today = datetime.now(SINGAPORE_TZ).date()
        shared_date, ambiguous = extract_standalone_date(lines[0], today)
        if ambiguous:
            await update.message.reply_text("The first line looks like an ambiguous date. Please use a clear date like 2026-05-19 or 19 May.")
            return True
        if shared_date is not None:
            shared_date_drafts = []
            skipped_lines = []
            for line in lines[1:]:
                draft = parse_expense(
                    line,
                    logged_by=logged_by,
                    me_label=self.settings.me_label,
                    wife_label=self.settings.wife_label,
                    today=shared_date,
                )
                if draft is None:
                    skipped_lines.append(line)
                    continue
                shared_date_drafts.append(ExpenseDraft(
                    raw_input=f"{lines[0]} | {draft.raw_input}",
                    amount=draft.amount,
                    category=draft.category,
                    description=draft.description,
                    confidence=draft.confidence,
                    expense_date=shared_date,
                    needs_date_confirmation=draft.needs_date_confirmation,
                ))
            await self._log_multiline_drafts(update, logged_by, shared_date_drafts, skipped_lines)
            return True

        line_drafts = [
            parse_expense(
                line,
                logged_by=logged_by,
                me_label=self.settings.me_label,
                wife_label=self.settings.wife_label,
                today=today,
            )
            for line in lines
        ]
        dated_line_drafts = [draft for draft in line_drafts if draft is not None and draft.expense_date is not None]
        if dated_line_drafts:
            skipped_lines = [
                line
                for line, draft in zip(lines, line_drafts)
                if draft is None or draft.expense_date is None
            ]
            await self._log_multiline_drafts(update, logged_by, dated_line_drafts, skipped_lines)
            return True

        if line_drafts and all(draft is not None for draft in line_drafts):
            today_line_drafts = [
                ExpenseDraft(
                    raw_input=draft.raw_input,
                    amount=draft.amount,
                    category=draft.category,
                    description=draft.description,
                    confidence=draft.confidence,
                    expense_date=today,
                    needs_date_confirmation=draft.needs_date_confirmation,
                )
                for draft in line_drafts
                if draft is not None
            ]
            await self._log_multiline_drafts(update, logged_by, today_line_drafts)
            return True

        return False

    async def _log_multiline_drafts(
        self,
        update: Update,
        logged_by: str,
        drafts: list[ExpenseDraft],
        skipped_lines: list[str] | None = None,
    ) -> None:
        if self._payment_tracking_enabled():
            payment_ids: list[str] = []
            pending_ids: list[str] = []
            for draft in drafts:
                if draft.category is None or draft.confidence < 0.7 or draft.needs_date_confirmation:
                    pending_ids.append(self._add_pending(draft, logged_by, update, "category", input_type="Text"))
                    continue
                payment_ids.append(self._add_pending(draft, logged_by, update, "payment", input_type="Text"))
            if payment_ids:
                await self._begin_payment_batch(update, payment_ids)
            if pending_ids:
                await update.message.reply_text(
                    "These entries still need a category:\n\n"
                    + "\n".join(pending_ids)
                )
            if not payment_ids and not pending_ids:
                await update.message.reply_text("No new expenses found.")
            if skipped_lines:
                await update.message.reply_text("Skipped lines:\n" + "\n".join(f"- {line}" for line in skipped_lines))
            return

        logged_lines = []
        pending_ids = []
        for draft in drafts:
            if draft.category is None or draft.confidence < 0.7 or draft.needs_date_confirmation:
                pending_ids.append(self._add_pending(draft, logged_by, update, "category"))
                continue
            row = self._expense_row(draft, logged_by, update, draft.category, "Confirmed", "Text")
            logged_line = await self._append_or_hold_duplicate(update, row)
            if logged_line:
                logged_lines.append(logged_line)
        message = "\n\n".join(logged_lines) if logged_lines else "No new expenses logged."
        if pending_ids:
            message += "\n\nPending IDs: " + ", ".join(pending_ids)
        if skipped_lines:
            message += "\n\nSkipped lines:\n" + "\n".join(f"- {line}" for line in skipped_lines)
        await update.message.reply_text(message)

    async def _reply_with_summary(self, update: Update, text: str) -> None:
        if update.message is None or update.effective_user is None:
            return
        if self.settings.label_for_user(update.effective_user.id) is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return
        period = parse_summary_period(text, datetime.now(SINGAPORE_TZ).date())
        if period is None:
            await update.message.reply_text("Try: summary, summary this month, or summary last month.")
            return
        self._refresh_monthly_summary(period.start.strftime("%Y-%m"))
        records = self.sheets.get_expense_records(self.settings.raw_expenses_sheet)
        summary = build_spending_summary(records, period)
        await update.message.reply_text(format_spending_summary(summary))

    async def _reply_with_card_summary(self, update: Update) -> None:
        if update.message is None or update.effective_user is None:
            return
        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return
        try:
            config = self._load_payment_config()
        except (RuntimeError, ValueError) as exc:
            await update.message.reply_text(f"Payment setup could not be loaded: {exc}")
            return
        records = self.sheets.get_expense_records(self.settings.raw_expenses_sheet)
        items = build_card_summary(config, records, logged_by, datetime.now(SINGAPORE_TZ).date())
        await update.message.reply_text(format_card_summary(items), do_quote=False)

    async def _reply_with_personal_expense_history(self, update: Update, text: str) -> bool:
        if update.message is None or update.effective_user is None:
            return False
        period = parse_expense_history_period(text, datetime.now(SINGAPORE_TZ).date())
        if period is None:
            if looks_like_expense_history_request(text):
                await update.message.reply_text(expense_history_clarification())
                return True
            return False
        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return True
        records = self.sheets.get_expense_records(self.settings.raw_expenses_sheet)
        history = build_personal_expense_history(records, period, logged_by)
        await update.message.reply_text(format_personal_expense_history(history))
        return True

    async def _change_latest_logged_date(self, update: Update, text: str) -> None:
        if update.message is None or update.effective_user is None:
            return
        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return

        parsed_date, ambiguous = extract_date_phrase(text, datetime.now(SINGAPORE_TZ).date())
        if ambiguous:
            await update.message.reply_text("That date is ambiguous. Try: change spend date to 2026-05-21 or 21 May.")
            return
        if parsed_date is None:
            await update.message.reply_text("I could not read that date. Try: change spend date to 21 May.")
            return

        record = self.sheets.get_last_matching_record(self.settings.raw_expenses_sheet, logged_by)
        if record is None:
            await update.message.reply_text("I could not find a recent logged expense to update.")
            return

        await self._ask_edit_confirmation(
            update,
            [PendingEditChange(record=record, expense_date=parsed_date.isoformat())],
        )

    async def _ask_edit_confirmation(self, update: Update, changes: list[PendingEditChange]) -> None:
        if update.message is None or update.effective_user is None:
            return
        key = (update.effective_chat.id, update.effective_user.id)
        self.pending_edits[key] = PendingEdit(
            changes=changes,
            chat_id=update.effective_chat.id,
            requested_by_user_id=update.effective_user.id,
            created_at=datetime.now(SINGAPORE_TZ),
        )
        title = "Change this expense?" if len(changes) == 1 else "Change these expenses?"
        lines = [title]
        for index, change in enumerate(changes, start=1):
            prefix = f"{index}. " if len(changes) > 1 else ""
            lines.append(f"{prefix}Before: {self._delete_candidate_line(change.record)}")
            lines.append(f"{prefix}After: {self._edit_after_line(change)}")
        lines.append("Reply: yes to update, or cancel.")
        await update.message.reply_text("\n\n".join(lines))

    def _clarification_record(self, intent: ExpenseIntent, by_id: dict[str, ExpenseRecord]) -> ExpenseRecord | None:
        candidate_ids = [intent.clarification_entry_id, *intent.entry_ids]
        candidate_ids.extend(
            re.findall(r"\b[a-f0-9]{6}\b", intent.clarification_question or "", flags=re.IGNORECASE)
        )
        records = {by_id[entry_id] for entry_id in candidate_ids if entry_id in by_id}
        return next(iter(records)) if len(records) == 1 else None

    async def _ask_for_edit_date(self, update: Update, record: ExpenseRecord) -> None:
        if update.message is None or update.effective_user is None:
            return
        key = (update.effective_chat.id, update.effective_user.id)
        self.pending_edit_dates[key] = PendingEditDate(
            record=record,
            chat_id=update.effective_chat.id,
            requested_by_user_id=update.effective_user.id,
            created_at=datetime.now(SINGAPORE_TZ),
        )
        await update.message.reply_text(
            f"Which date should I change {self._delete_candidate_line(record)} to?\n\n"
            "Reply with a date such as 30 June 2026, or cancel."
        )

    async def _handle_pending_edit_date_reply(self, update: Update, text: str, lowered: str) -> bool:
        if update.message is None or update.effective_user is None:
            return False
        key = (update.effective_chat.id, update.effective_user.id)
        pending = self.pending_edit_dates.get(key)
        if pending is None:
            return False

        if lowered in {"cancel", "no", "stop", "never mind", "nevermind"}:
            self.pending_edit_dates.pop(key, None)
            await update.message.reply_text("Date change cancelled.")
            return True

        parsed_date, ambiguous = extract_date_phrase(text, datetime.now(SINGAPORE_TZ).date())
        if ambiguous:
            await update.message.reply_text("That date is ambiguous. Try 30 June 2026, or cancel.")
            return True
        if parsed_date is None:
            await update.message.reply_text("I am waiting for the new date. Reply with a date such as 30 June 2026, or cancel.")
            return True

        self.pending_edit_dates.pop(key, None)
        await self._ask_edit_confirmation(
            update,
            [PendingEditChange(record=pending.record, expense_date=parsed_date.isoformat())],
        )
        return True

    async def _handle_pending_edit_reply(self, update: Update, lowered: str) -> bool:
        if update.message is None or update.effective_user is None:
            return False
        key = (update.effective_chat.id, update.effective_user.id)
        pending = self.pending_edits.get(key)
        if pending is None:
            return False

        if lowered in {"cancel", "no", "stop", "never mind", "nevermind"}:
            self.pending_edits.pop(key, None)
            await update.message.reply_text("Update cancelled.")
            return True

        if lowered not in {"yes", "y", "confirm", "update", "update it", "confirm update"}:
            return False

        self.pending_edits.pop(key, None)
        updated_lines = []
        for change in pending.changes:
            self.sheets.update_expense_record(
                self.settings.raw_expenses_sheet,
                change.record.row_number,
                amount=change.amount,
                category=change.category,
                description=change.description,
                expense_date=change.expense_date,
                transaction_type=_transaction_type_for_category(change.category, change.record.input_type) if change.category else None,
            )
            updated_lines.append(f"Updated {self._edit_after_line(change)}")
        self._refresh_monthly_summary()
        await update.message.reply_text("\n\n".join(updated_lines))
        return True

    def _edit_after_line(self, change: PendingEditChange) -> str:
        amount = change.amount if change.amount is not None else change.record.amount
        category = change.category or change.record.category
        date_value = change.expense_date or change.record.expense_date
        try:
            date_text = datetime.fromisoformat(date_value).strftime("%-d %B %Y")
        except ValueError:
            date_text = date_value
        return f"${amount:.2f} logged as {category} - {date_text} [{change.record.entry_id}]"

    async def _ask_delete_confirmation(
        self,
        update: Update,
        record: ExpenseRecord,
        logged_by_restriction: str | None = None,
    ) -> None:
        if update.message is None or update.effective_user is None:
            return
        key = (update.effective_chat.id, update.effective_user.id)
        self.pending_deletes[key] = PendingDelete(
            record=record,
            chat_id=update.effective_chat.id,
            requested_by_user_id=update.effective_user.id,
            created_at=datetime.now(SINGAPORE_TZ),
            logged_by_restriction=logged_by_restriction,
        )
        await update.message.reply_text(
            f"Delete {self._delete_candidate_line(record)}?\n\n"
            "Reply: yes to delete, or cancel."
        )

    async def _handle_pending_delete_reply(self, update: Update, lowered: str) -> bool:
        if update.message is None or update.effective_user is None:
            return False
        key = (update.effective_chat.id, update.effective_user.id)
        pending = self.pending_deletes.get(key)
        if pending is None:
            return False

        if lowered in {"cancel", "no", "stop", "never mind", "nevermind"}:
            self.pending_deletes.pop(key, None)
            await update.message.reply_text("Delete cancelled.")
            return True

        if lowered not in {"yes", "y", "confirm", "delete it", "yes delete", "confirm delete"}:
            return False

        deleted = self.sheets.delete_entry_by_id(
            self.settings.raw_expenses_sheet,
            pending.record.entry_id,
            logged_by=pending.logged_by_restriction,
        )
        self.pending_deletes.pop(key, None)
        if deleted:
            self._refresh_monthly_summary()
            await update.message.reply_text(f"Deleted {self._delete_candidate_line(pending.record)}.")
        else:
            await update.message.reply_text("I could not find that expense anymore. It may already be deleted.")
        return True

    def _delete_candidate_line(self, record: ExpenseRecord) -> str:
        try:
            date_text = datetime.fromisoformat(record.expense_date).strftime("%-d %B %Y")
        except ValueError:
            date_text = record.expense_date
        return f"${record.amount:.2f} logged as {record.category} - {date_text} [{record.entry_id}]"

    async def _append_or_hold_duplicate(self, update: Update, row: ExpenseRow, pending_id: str | None = None) -> str | None:
        message = update.message
        if message is None and update.callback_query is not None:
            message = update.callback_query.message
        if message is None or update.effective_user is None:
            return None
        duplicate = self._find_duplicate(row)
        if duplicate is None:
            self._append_expense(row)
            self._remember_logged(row)
            return self._logged_line(row)

        key = (update.effective_chat.id, update.effective_user.id)
        self.pending_duplicates[key] = PendingDuplicate(
            row=row,
            existing_record=duplicate,
            chat_id=update.effective_chat.id,
            requested_by_user_id=update.effective_user.id,
            created_at=datetime.now(SINGAPORE_TZ),
            pending_id=pending_id,
        )
        await message.reply_text(self._duplicate_prompt(row, duplicate))
        return None

    async def _handle_pending_duplicate_reply(self, update: Update, lowered: str) -> bool:
        if update.message is None or update.effective_user is None:
            return False
        key = (update.effective_chat.id, update.effective_user.id)
        pending = self.pending_duplicates.get(key)
        if pending is None:
            return False

        if lowered in {"cancel", "no", "discard", "delete", "delete duplicate"}:
            self.pending_duplicates.pop(key, None)
            if pending.pending_id is not None:
                self.pending.pop(pending.pending_id, None)
            if key in self.pending_payment_batches:
                await update.message.reply_text("Duplicate not logged.")
                await self._continue_payment_batch(update)
                return True
            remaining = self._matching_pending(update, latest_batch_only=True) or self._matching_pending(update)
            if remaining:
                await update.message.reply_text(
                    "Duplicate not logged.\n\n"
                    + self._pending_summary(update, "Pending expenses still need your decision:")
                )
            else:
                await update.message.reply_text("Duplicate not logged.")
            return True

        if lowered != "confirm":
            await update.message.reply_text(self._duplicate_prompt(pending.row, pending.existing_record))
            return True

        self.pending_duplicates.pop(key, None)
        self._append_expense(pending.row)
        self._remember_logged(pending.row)
        if pending.pending_id is not None:
            self.pending.pop(pending.pending_id, None)
        if key in self.pending_payment_batches:
            await update.message.reply_text(self._logged_line(pending.row))
            await self._continue_payment_batch(update)
            return True
        remaining = self._matching_pending(update, latest_batch_only=True) or self._matching_pending(update)
        if remaining:
            await update.message.reply_text(
                self._logged_line(pending.row)
                + "\n\n"
                + self._pending_summary(update, "Pending expenses still need your decision:")
            )
        else:
            await update.message.reply_text(self._logged_line(pending.row))
        return True

    def _find_duplicate(self, row: ExpenseRow) -> ExpenseRecord | None:
        if row.transaction_type.lower() == "fixed" or row.input_type.lower() == "fixed":
            return None
        recent_duplicate = self._find_recent_duplicate(row)
        if recent_duplicate is not None:
            return recent_duplicate
        records = self.sheets.get_expense_records(self.settings.raw_expenses_sheet)
        row_date = row.timestamp.strftime("%Y-%m-%d")
        for record in reversed(records):
            if (
                record.status.lower() != "confirmed"
                or record.transaction_type.lower() == "fixed"
                or record.input_type.lower() == "fixed"
            ):
                continue
            if record.expense_date == row_date and record.amount == row.amount and record.category == row.category:
                return record
        return None

    def _remember_logged(self, row: ExpenseRow) -> None:
        now = datetime.now(SINGAPORE_TZ)
        cutoff = now - RECENT_DUPLICATE_WINDOW
        self.recent_logged = [item for item in self.recent_logged if item.logged_at >= cutoff]
        self.recent_logged.append(RecentLoggedExpense(row=row, logged_at=now))

    def _find_recent_duplicate(self, row: ExpenseRow) -> ExpenseRecord | None:
        now = datetime.now(SINGAPORE_TZ)
        cutoff = now - RECENT_DUPLICATE_WINDOW
        self.recent_logged = [item for item in self.recent_logged if item.logged_at >= cutoff]
        row_date = row.timestamp.strftime("%Y-%m-%d")
        for item in reversed(self.recent_logged):
            existing = item.row
            if (
                existing.status.lower() == "confirmed"
                and existing.transaction_type.lower() != "fixed"
                and existing.input_type.lower() != "fixed"
                and existing.timestamp.strftime("%Y-%m-%d") == row_date
                and existing.amount == row.amount
                and existing.category == row.category
            ):
                return ExpenseRecord(
                    row_number=0,
                    entry_id=existing.entry_id,
                    timestamp=existing.timestamp.strftime("%H:%M:%S"),
                    expense_date=existing.timestamp.strftime("%Y-%m-%d"),
                    month=existing.timestamp.strftime("%Y-%m"),
                    logged_by=existing.logged_by,
                    raw_input=existing.raw_input,
                    amount=existing.amount,
                    category=existing.category,
                    description=existing.description,
                    input_type=existing.input_type,
                    status=existing.status,
                    transaction_type=existing.transaction_type,
                )
        return None

    def _duplicate_prompt(self, row: ExpenseRow, existing: ExpenseRecord) -> str:
        return (
            "Possible duplicate found:\n\n"
            f"Existing: {self._delete_candidate_line(existing)}\n\n"
            f"New: ${row.amount:.2f} to {row.category} - {self._human_date(row.timestamp.date())} - {row.description}\n\n"
            'Reply: "confirm" to log anyway or "cancel" to delete'
        )

    def _add_fixed_expenses_for_month(
        self,
        month_date,
        logged_by: str,
        chat_id: int | str,
        message_id: int | str,
        fixed_items: list[dict[str, str | Decimal]] | None = None,
    ) -> FixedAddResult:
        fixed = fixed_items if fixed_items is not None else self.sheets.get_fixed_expenses(self.settings.fixed_expenses_sheet)
        if not fixed:
            return FixedAddResult(0)

        expense_date = _last_day_of_month(month_date)
        self.sheets.delete_fixed_expenses_for_month(self.settings.raw_expenses_sheet, month_date.strftime("%Y-%m"))
        added_count = 0
        for item in fixed:
            category = str(item["category"])
            row = ExpenseRow(
                entry_id=uuid.uuid4().hex[:6],
                timestamp=datetime.combine(expense_date, time(hour=9), SINGAPORE_TZ),
                logged_by=logged_by,
                raw_input=f"Fixed expense confirmation: {category}",
                amount=item["amount"],
                category=category,
                description=str(item.get("notes") or category),
                input_type="Fixed",
                status="Confirmed",
                telegram_chat_id=chat_id,
                telegram_message_id=message_id,
                transaction_type="Fixed",
            )
            self.sheets.append_expense(self.settings.raw_expenses_sheet, row)
            added_count += 1
        return FixedAddResult(added_count)

    def _append_expense(self, row: ExpenseRow) -> None:
        self.sheets.append_expense(self.settings.raw_expenses_sheet, row)
        self._refresh_monthly_summary(row.timestamp.strftime("%Y-%m"))

    def _refresh_monthly_summary(
        self,
        include_month: str | None = None,
        fixed_month_date=None,
        fixed_items: list[dict[str, str | Decimal]] | None = None,
    ) -> None:
        records = self.sheets.get_expense_records(self.settings.raw_expenses_sheet)
        fixed_overrides = self._fixed_summary_overrides(fixed_month_date, fixed_items)
        table = build_monthly_summary_table(records, include_month=include_month, fixed_overrides=fixed_overrides)
        self.sheets.update_monthly_summary(self.settings.monthly_summary_sheet, table)

    def _fixed_summary_overrides(
        self,
        month_date,
        fixed_items: list[dict[str, str | Decimal]] | None,
    ) -> dict[str, dict[str, Decimal]]:
        if month_date is None or not fixed_items:
            return {}
        month = month_date.strftime("%Y-%m")
        return {
            month: {
                str(item["category"]): Decimal(str(item["amount"]))
                for item in fixed_items
            }
        }

    async def run_monthly_scheduler(self, bot) -> None:
        while True:
            try:
                await self._run_monthly_checks(bot, datetime.now(SINGAPORE_TZ))
            except Exception:
                LOGGER.exception("Monthly scheduler check failed")
            await asyncio.sleep(60)

    async def _run_monthly_checks(self, bot, now: datetime) -> None:
        if self.settings.telegram_chat_id is None:
            return
        if now.hour != 9:
            return

        today = now.date()
        if today == _last_day_of_month(today):
            await self._send_fixed_expense_reminder(bot, today)
        if today.day == 1:
            await self._send_previous_month_summary(bot, today)

    async def _send_fixed_expense_reminder(self, bot, today) -> None:
        target_month = today.strftime("%Y-%m")
        state_key = f"fixed_reminder_sent:{target_month}"
        if self.sheets.get_state_value(self.settings.bot_state_sheet, state_key) == "yes":
            return

        try:
            fixed = self.sheets.get_fixed_expenses(self.settings.fixed_expenses_sheet)
        except ValueError as exc:
            await bot.send_message(chat_id=self.settings.telegram_chat_id, text=str(exc))
            self.sheets.set_state_value(self.settings.bot_state_sheet, state_key, "yes")
            return
        if not fixed:
            message = f"No active fixed expenses found for {_month_label(today)}."
        else:
            review = FixedReview(
                month_date=today,
                items=[dict(item) for item in fixed],
                chat_id=self.settings.telegram_chat_id,
                created_at=datetime.now(SINGAPORE_TZ),
            )
            self.pending_fixed_reviews[self.settings.telegram_chat_id] = review
            message = self._fixed_review_message(review)

        await bot.send_message(chat_id=self.settings.telegram_chat_id, text=message)
        self.sheets.set_state_value(self.settings.bot_state_sheet, state_key, "yes")

    async def _send_previous_month_summary(self, bot, today) -> None:
        previous_month_end = today.replace(day=1) - timedelta(days=1)
        target_month = previous_month_end.strftime("%Y-%m")
        state_key = f"final_summary_sent:{target_month}"
        if self.sheets.get_state_value(self.settings.bot_state_sheet, state_key) == "yes":
            return

        self._refresh_monthly_summary(target_month)
        period = parse_summary_period("summary last month", today)
        if period is None:
            return
        records = self.sheets.get_expense_records(self.settings.raw_expenses_sheet)
        summary = build_spending_summary(records, period)
        await bot.send_message(chat_id=self.settings.telegram_chat_id, text=format_spending_summary(summary))
        self.sheets.set_state_value(self.settings.bot_state_sheet, state_key, "yes")

    def _pending_extractions_message(self, expenses, logged_by: str, update: Update, reason: str, title: str) -> str:
        lines = [title]
        pending_ids = []
        batch_id = self._new_pending_batch(update)
        for expense in expenses:
            if expense.amount is None:
                continue
            category = _normalize_category(expense.category or "")
            if category not in VARIABLE_CATEGORIES:
                category = None
            expense_date = None
            if expense.date:
                try:
                    expense_date = datetime.fromisoformat(expense.date).date()
                except ValueError:
                    expense_date = None
            draft = ExpenseDraft(
                raw_input=f"{reason}: {expense.description or 'expense'}",
                amount=Decimal(str(expense.amount)),
                category=category,
                description=expense.description or f"{reason} expense",
                confidence=expense.confidence,
                expense_date=expense_date,
                needs_date_confirmation=False,
            )
            pending_id = self._add_pending(
                draft,
                logged_by,
                update,
                reason,
                batch_id=batch_id,
                input_type=reason.title(),
            )
            pending_ids.append(pending_id)
            date_text = self._human_date(expense_date or datetime.now(SINGAPORE_TZ).date())
            category_text = category or "category unclear"
            lines.append(
                f"{len(pending_ids)}. ${draft.amount:.2f} to {category_text} - "
                f"{date_text} - {draft.description} [{pending_id}]"
            )

        if not pending_ids:
            return "I could not find a clear expense to log."
        lines.append("Reply: confirm all")
        lines.append("Or say: confirm the first entry, change the second one to Groceries")
        return "\n\n".join(lines)

    def _matching_pending(self, update: Update, latest_batch_only: bool = False) -> list[tuple[str, PendingExpense]]:
        if update.effective_user is None:
            return []
        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            return []
        latest_batch_id = self._latest_pending_batch_id(update)
        return [
            (pending_id, pending)
            for pending_id, pending in self.pending.items()
            if pending.logged_by == logged_by and pending.chat_id == update.effective_chat.id
            and (not latest_batch_only or (latest_batch_id is not None and pending.batch_id == latest_batch_id))
        ]

    def _latest_pending_batch_id(self, update: Update) -> str | None:
        if update.effective_user is None:
            return None
        return self.latest_pending_batch.get((update.effective_chat.id, update.effective_user.id))

    def _new_pending_batch(self, update: Update) -> str:
        batch_id = uuid.uuid4().hex[:8]
        if update.effective_user is not None:
            self.latest_pending_batch[(update.effective_chat.id, update.effective_user.id)] = batch_id
        return batch_id

    def _update_pending_dates(self, update: Update, parsed_date) -> bool:
        changed = False
        matching = self._matching_pending(update, latest_batch_only=True) or self._matching_pending(update)
        for pending_id, pending in matching:
            draft = pending.draft
            self.pending[pending_id] = PendingExpense(
                draft=ExpenseDraft(
                    raw_input=draft.raw_input,
                    amount=draft.amount,
                    category=draft.category,
                    description=draft.description,
                    confidence=draft.confidence,
                    expense_date=parsed_date,
                    needs_date_confirmation=False,
                ),
                logged_by=pending.logged_by,
                chat_id=pending.chat_id,
                message_id=pending.message_id,
                created_at=pending.created_at,
                reason=pending.reason,
                batch_id=pending.batch_id,
                category_options=pending.category_options,
                input_type=pending.input_type,
                payment_options=pending.payment_options,
            )
            changed = True
        return changed

    def _pending_summary(self, update: Update, title: str) -> str:
        lines = [title]
        matching = self._matching_pending(update, latest_batch_only=True) or self._matching_pending(update)
        for index, (pending_id, pending) in enumerate(matching, start=1):
            date_value = pending.draft.expense_date or datetime.now(SINGAPORE_TZ).date()
            category = pending.draft.category or "category unclear"
            lines.append(
                f"{index}. ${pending.draft.amount:.2f} to {category} - "
                f"{self._human_date(date_value)} - {pending.draft.description} [{pending_id}]"
            )
        lines.append("Reply: confirm all")
        matching_count = len(self._matching_pending(update, latest_batch_only=True) or self._matching_pending(update))
        if matching_count > 1:
            lines.append("Or: confirm 1 and 3")
        lines.append("Or: cancel")
        return "\n\n".join(lines)

    def _cancel_visible_pending(self, update: Update) -> int:
        matching = self._matching_pending(update, latest_batch_only=True) or self._matching_pending(update)
        for pending_id, _pending in matching:
            self.pending.pop(pending_id, None)
        if update.effective_user is not None:
            latest_key = (update.effective_chat.id, update.effective_user.id)
            latest_batch_id = self.latest_pending_batch.get(latest_key)
            logged_by = self.settings.label_for_user(update.effective_user.id)
            if latest_batch_id is not None and not any(
                pending.batch_id == latest_batch_id
                and pending.chat_id == update.effective_chat.id
                and pending.logged_by == logged_by
                for pending in self.pending.values()
            ):
                self.latest_pending_batch.pop(latest_key, None)
        return len(matching)

    async def _handle_ai_pending_instruction(self, update: Update, text: str) -> bool:
        ai = self._ai()
        if ai is None or update.message is None:
            return False

        matching = self._matching_pending(update, latest_batch_only=True) or self._matching_pending(update)
        if not matching:
            return False

        pending_lines = [
            f"{index}. {pending_id}: ${pending.draft.amount:.2f} | {pending.draft.category or 'category unclear'} | "
            f"{self._human_date(pending.draft.expense_date or datetime.now(SINGAPORE_TZ).date())} | {pending.draft.description}"
            for index, (pending_id, pending) in enumerate(matching, start=1)
        ]
        instruction = ai.interpret_pending_instruction(text, pending_lines, datetime.now(SINGAPORE_TZ).date())

        if instruction.action == "clarify":
            await update.message.reply_text(instruction.clarification_question or "Which pending entry should I change?")
            return True

        if instruction.action == "ignore":
            return False

        if instruction.update_positions:
            category = _normalize_category(instruction.category) if instruction.category else None
            if category is not None and category not in VARIABLE_CATEGORIES:
                await update.message.reply_text("I do not recognize that category. Send: categories")
                return True
            parsed_date = None
            if instruction.date:
                try:
                    parsed_date = datetime.fromisoformat(instruction.date).date()
                except ValueError:
                    await update.message.reply_text("I could not read that date. Try: 19 May or 2026-05-19.")
                    return True
            self._update_pending_positions(update, instruction.update_positions, category, parsed_date)

        logged_lines = []
        if instruction.confirm_positions:
            logged_lines = await self._confirm_pending_positions(update, instruction.confirm_positions)

        response_parts = []
        if instruction.update_positions:
            response_parts.append(self._pending_summary(update, "Updated pending entries:"))
        if logged_lines:
            response_parts.append("\n\n".join(logged_lines))
        if not response_parts:
            response_parts.append(self._pending_summary(update, "Current pending entries:"))
        await update.message.reply_text("\n\n".join(response_parts))
        return True

    def _update_pending_positions(self, update: Update, positions: list[int], category: str | None, parsed_date) -> None:
        matching = self._matching_pending(update, latest_batch_only=True) or self._matching_pending(update)
        for position in positions:
            if position < 1 or position > len(matching):
                continue
            pending_id, pending = matching[position - 1]
            draft = pending.draft
            self.pending[pending_id] = PendingExpense(
                draft=ExpenseDraft(
                    raw_input=draft.raw_input,
                    amount=draft.amount,
                    category=category or draft.category,
                    description=draft.description,
                    confidence=draft.confidence,
                    expense_date=parsed_date or draft.expense_date,
                    needs_date_confirmation=False,
                ),
                logged_by=pending.logged_by,
                chat_id=pending.chat_id,
                message_id=pending.message_id,
                created_at=pending.created_at,
                reason=pending.reason,
                batch_id=pending.batch_id,
                category_options=pending.category_options,
                input_type=pending.input_type,
                payment_options=pending.payment_options,
            )

    def _parse_pending_category_changes(self, text: str, pending_count: int) -> tuple[dict[int, str], list[str]]:
        if not any(word in text for word in ["change", "update", "make"]):
            return {}, []

        position_words = "first|second|third|fourth|fifth|last|\\d+"
        pattern = re.compile(
            rf"(?:\b(?:change|update|make)\s+)?"
            rf"(?P<position>{position_words})"
            rf"(?:\s*(?:entry|one))?\s+"
            rf"(?:to|as)\s+"
            rf"(?P<category>[a-z][a-z0-9 /&().+-]*?)"
            rf"(?=(?:\s*(?:,|and)\s*(?:(?:change|update|make)\s+)?(?:{position_words})"
            rf"(?:\s*(?:entry|one))?\s+(?:to|as)\s+)|$)"
        )
        updates: dict[int, str] = {}
        invalid_categories: list[str] = []
        for match in pattern.finditer(text):
            position = _position_from_text(match.group("position"), pending_count)
            if position < 1 or position > pending_count:
                continue
            raw_category = match.group("category").strip(" .,")
            category = _normalize_category(raw_category)
            if category not in VARIABLE_CATEGORIES:
                invalid_categories.append(raw_category)
                continue
            updates[position] = category
        return updates, invalid_categories

    def _update_pending_position_categories(self, update: Update, updates: dict[int, str]) -> None:
        matching = self._matching_pending(update, latest_batch_only=True) or self._matching_pending(update)
        for position, category in updates.items():
            if position < 1 or position > len(matching):
                continue
            pending_id, pending = matching[position - 1]
            draft = pending.draft
            self.pending[pending_id] = PendingExpense(
                draft=ExpenseDraft(
                    raw_input=draft.raw_input,
                    amount=draft.amount,
                    category=category,
                    description=draft.description,
                    confidence=max(draft.confidence, 0.95),
                    expense_date=draft.expense_date,
                    needs_date_confirmation=draft.needs_date_confirmation,
                ),
                logged_by=pending.logged_by,
                chat_id=pending.chat_id,
                message_id=pending.message_id,
                created_at=pending.created_at,
                reason=pending.reason,
                batch_id=pending.batch_id,
                category_options=pending.category_options,
                input_type=pending.input_type,
                payment_options=pending.payment_options,
            )

    async def _apply_category_reply(self, update: Update, category: str) -> tuple[list[str], bool]:
        matching = self._matching_pending(update, latest_batch_only=True) or self._matching_pending(update)
        logged_lines: list[str] = []
        payment_ids: list[str] = []
        changed = False

        for pending_id, pending in matching:
            if pending.draft.category in VARIABLE_CATEGORIES:
                continue

            draft = pending.draft
            updated_pending = PendingExpense(
                draft=ExpenseDraft(
                    raw_input=draft.raw_input,
                    amount=draft.amount,
                    category=category,
                    description=draft.description,
                    confidence=max(draft.confidence, 0.95),
                    expense_date=draft.expense_date,
                    needs_date_confirmation=draft.needs_date_confirmation,
                ),
                logged_by=pending.logged_by,
                chat_id=pending.chat_id,
                message_id=pending.message_id,
                created_at=pending.created_at,
                reason=pending.reason,
                batch_id=pending.batch_id,
                category_options=pending.category_options,
                input_type=pending.input_type,
                payment_options=pending.payment_options,
            )

            if pending.reason == "category":
                if self._needs_payment_method(category, pending.input_type):
                    self.pending[pending_id] = updated_pending
                    payment_ids.append(pending_id)
                    changed = True
                    continue
                self.pending.pop(pending_id, None)
                row = self._expense_row_from_pending(updated_pending, category, "Confirmed", updated_pending.input_type, None)
                logged_line = await self._append_or_hold_duplicate(update, row, pending_id=pending_id)
                if logged_line:
                    logged_lines.append(logged_line)
                changed = True
                continue

            self.pending[pending_id] = updated_pending
            changed = True

        if payment_ids:
            if len(payment_ids) == 1:
                await self._request_payment_method(update, payment_ids[0])
            else:
                await self._begin_payment_batch(update, payment_ids)
        return logged_lines, changed

    def _update_unclear_pending_categories(self, update: Update, category: str) -> bool:
        changed = False
        for pending_id, pending in self._matching_pending(update):
            if pending.draft.category in VARIABLE_CATEGORIES:
                continue
            draft = pending.draft
            self.pending[pending_id] = PendingExpense(
                draft=ExpenseDraft(
                    raw_input=draft.raw_input,
                    amount=draft.amount,
                    category=category,
                    description=draft.description,
                    confidence=max(draft.confidence, 0.95),
                    expense_date=draft.expense_date,
                    needs_date_confirmation=draft.needs_date_confirmation,
                ),
                logged_by=pending.logged_by,
                chat_id=pending.chat_id,
                message_id=pending.message_id,
                created_at=pending.created_at,
                reason=pending.reason,
                batch_id=pending.batch_id,
                category_options=pending.category_options,
                input_type=pending.input_type,
                payment_options=pending.payment_options,
            )
            changed = True
        return changed

    async def _confirm_pending_positions(
        self,
        update: Update,
        positions: list[int],
        category_override: str | None = None,
        latest_batch_only: bool = False,
        cancel_unselected: bool = False,
    ) -> list[str]:
        matching = self._matching_pending(update, latest_batch_only=latest_batch_only)
        selected_positions = {position for position in positions if 1 <= position <= len(matching)}
        selected_rows = []
        for position in sorted(selected_positions):
            pending_id, pending = matching[position - 1]
            category = category_override or pending.draft.category or self._infer_pending_category(pending)
            if category not in VARIABLE_CATEGORIES:
                continue
            selected_rows.append((position, pending_id, pending, category))
        if not selected_rows:
            return []

        if self._payment_tracking_enabled() and all(
            _transaction_type_for_category(category, pending.input_type).casefold() == "expense"
            for _position, _pending_id, pending, category in selected_rows
        ):
            selected_ids = []
            for _position, pending_id, pending, category in selected_rows:
                self.pending[pending_id] = replace(
                    pending,
                    draft=replace(pending.draft, category=category, confidence=max(pending.draft.confidence, 0.95)),
                    payment_options=(),
                )
                selected_ids.append(pending_id)
            if cancel_unselected:
                for position, (pending_id, _pending) in enumerate(matching, start=1):
                    if position not in selected_positions:
                        self.pending.pop(pending_id, None)
            await self._begin_payment_batch(update, selected_ids)
            return []

        await update.message.reply_text("Logging expenses...")
        logged_lines = []
        duplicate_seen = False
        for _position, pending_id, pending, category in selected_rows:
            row = self._expense_row_from_pending(pending, category, "Confirmed", pending.reason.title(), None)
            logged_line = await self._append_or_hold_duplicate(update, row, pending_id=pending_id)
            if logged_line:
                self.pending.pop(pending_id, None)
                logged_lines.append(logged_line)
            else:
                duplicate_seen = True
                break
        if cancel_unselected and not duplicate_seen:
            for position, (pending_id, _pending) in enumerate(matching, start=1):
                if position not in selected_positions:
                    self.pending.pop(pending_id, None)
        return logged_lines

    def _infer_pending_category(self, pending: PendingExpense) -> str | None:
        category, confidence = categorize_description(
            pending.draft.description,
            pending.logged_by,
            self.settings.me_label,
            self.settings.wife_label,
        )
        if confidence >= 0.7:
            return category
        return None

    async def _confirm_all_pending(self, update: Update, latest_batch_only: bool = False) -> None:
        if update.message is None or update.effective_user is None:
            return
        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return

        matching = self._matching_pending(update, latest_batch_only=latest_batch_only)
        if not matching:
            await update.message.reply_text("No pending expenses to confirm.")
            return

        prepared_rows = []
        for pending_id, pending in matching:
            category = pending.draft.category or self._infer_pending_category(pending)
            if category not in VARIABLE_CATEGORIES:
                await update.message.reply_text("Pending expenses still need categories. Use: confirm abc123 Food")
                return
            row = self._expense_row_from_pending(pending, category, "Confirmed", pending.reason.title(), None)
            prepared_rows.append((pending_id, row))

        if self._payment_tracking_enabled() and all(row.transaction_type.casefold() == "expense" for _pending_id, row in prepared_rows):
            for pending_id, row in prepared_rows:
                pending = self.pending[pending_id]
                self.pending[pending_id] = replace(
                    pending,
                    draft=replace(pending.draft, category=row.category, confidence=max(pending.draft.confidence, 0.95)),
                    payment_options=(),
                )
            await self._begin_payment_batch(update, [pending_id for pending_id, _row in prepared_rows])
            return

        await update.message.reply_text("Logging expenses...")
        logged_lines = []
        duplicate_seen = False
        for pending_id, row in prepared_rows:
            logged_line = await self._append_or_hold_duplicate(update, row, pending_id=pending_id)
            if logged_line:
                self.pending.pop(pending_id, None)
                logged_lines.append(logged_line)
            else:
                duplicate_seen = True
                break

        if logged_lines:
            await update.message.reply_text("\n\n".join(logged_lines))
        elif not duplicate_seen:
            await update.message.reply_text("Pending expenses still need categories. Use: confirm abc123 Food")

    def _add_pending(
        self,
        draft: ExpenseDraft,
        logged_by: str,
        update: Update,
        reason: str,
        batch_id: str | None = None,
        category_options: tuple[str, ...] = (),
        input_type: str = "Text",
    ) -> str:
        pending_id = uuid.uuid4().hex[:6]
        self.pending[pending_id] = PendingExpense(
            draft=draft,
            logged_by=logged_by,
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            created_at=datetime.now(SINGAPORE_TZ),
            reason=reason,
            batch_id=batch_id,
            category_options=category_options,
            input_type=input_type,
        )
        return pending_id

    def _needs_payment_method(self, category: str, input_type: str) -> bool:
        return self._payment_tracking_enabled() and _transaction_type_for_category(category, input_type).casefold() == "expense"

    async def _record_or_request_payment(
        self,
        update: Update,
        draft: ExpenseDraft,
        logged_by: str,
        category: str,
        input_type: str,
    ) -> str | None:
        if not self._needs_payment_method(category, input_type):
            row = self._expense_row(draft, logged_by, update, category, "Confirmed", input_type)
            return await self._append_or_hold_duplicate(update, row)

        pending_id = self._add_pending(draft, logged_by, update, "payment", input_type=input_type)
        await self._request_payment_method(update, pending_id)
        return None

    async def _record_pending_or_request_payment(
        self,
        update: Update,
        pending_id: str,
        pending: PendingExpense,
        category: str,
        date_override=None,
    ) -> str | None:
        draft = pending.draft
        if date_override is not None:
            draft = replace(draft, expense_date=date_override, needs_date_confirmation=False)
            pending = replace(pending, draft=draft)
            self.pending[pending_id] = pending
        if self._needs_payment_method(category, pending.input_type):
            self.pending[pending_id] = replace(pending, draft=replace(draft, category=category, confidence=max(draft.confidence, 0.95)))
            await self._request_payment_method(update, pending_id)
            return None
        row = self._expense_row_from_pending(pending, category, "Confirmed", pending.input_type, date_override)
        logged_line = await self._append_or_hold_duplicate(update, row, pending_id=pending_id)
        if logged_line:
            self.pending.pop(pending_id, None)
        return logged_line

    async def _request_payment_method(self, update: Update, pending_id: str, position: tuple[int, int] | None = None) -> bool:
        pending = self.pending.get(pending_id)
        if pending is None:
            return False
        try:
            config = self._load_payment_config()
        except (RuntimeError, ValueError) as exc:
            await self._reply_to_update(update, f"Payment setup could not be loaded: {exc}")
            return False
        methods = config.methods_for_owner(pending.logged_by)
        if not methods:
            await self._reply_to_update(
                update,
                f"No active payment methods are configured for {pending.logged_by}. "
                "Check the Payment Methods tab, then use /refreshpayments.",
            )
            return False
        options = tuple(method.name for method in methods)
        self.pending[pending_id] = replace(pending, payment_options=options)
        date_value = pending.draft.expense_date or datetime.now(SINGAPORE_TZ).date()
        position_text = f"Payment method for {position[0]} of {position[1]}:\n\n" if position else "Which payment method?\n\n"
        await self._reply_to_update(
            update,
            position_text
            + f"${pending.draft.amount:,.2f} to {pending.draft.category} - "
            + f"{self._human_date(date_value)} - {pending.draft.description}",
            reply_markup=self._payment_method_keyboard(pending_id, options),
        )
        return True

    def _payment_method_keyboard(self, pending_id: str, options: tuple[str, ...]):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        buttons = [
            InlineKeyboardButton(name, callback_data=f"{PAYMENT_METHOD_CALLBACK}|{pending_id}|{index}")
            for index, name in enumerate(options)
        ]
        rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
        return InlineKeyboardMarkup(rows)

    async def handle_payment_method_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or update.effective_user is None:
            return
        parts = (query.data or "").split("|")
        if len(parts) != 3 or parts[0] != PAYMENT_METHOD_CALLBACK:
            return
        pending_id = parts[1]
        try:
            option_index = int(parts[2])
        except ValueError:
            await query.answer("That payment method is invalid.", show_alert=True)
            return
        pending = self.pending.get(pending_id)
        if pending is None:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer("This payment choice is no longer available.", show_alert=True)
            return
        logged_by = self.settings.label_for_user(update.effective_user.id)
        message_chat_id = query.message.chat_id if query.message is not None else None
        if logged_by is None or logged_by != pending.logged_by or message_chat_id != pending.chat_id:
            await query.answer("Only the person who submitted this expense can choose its payment method.", show_alert=True)
            return
        if option_index < 0 or option_index >= len(pending.payment_options):
            await query.answer("That payment method is no longer available.", show_alert=True)
            return
        payment_method = pending.payment_options[option_index]
        try:
            config = self._load_payment_config()
        except (RuntimeError, ValueError) as exc:
            await query.answer("Payment setup needs attention.", show_alert=True)
            await query.message.reply_text(f"Payment setup could not be loaded: {exc}")
            return
        if config.method_for(pending.logged_by, payment_method) is None:
            await query.answer("That payment method is no longer active.", show_alert=True)
            return

        await query.answer()
        category = pending.draft.category or self._infer_pending_category(pending)
        if category not in VARIABLE_CATEGORIES:
            await query.answer("This expense still needs a category.", show_alert=True)
            return
        row = self._expense_row_from_pending(
            pending,
            category,
            "Confirmed",
            pending.input_type,
            None,
            payment_method=payment_method,
        )
        logged_line = await self._append_or_hold_duplicate(update, row, pending_id=pending_id)
        await query.edit_message_reply_markup(reply_markup=None)
        if logged_line:
            self.pending.pop(pending_id, None)
            await query.edit_message_text(logged_line)
            await self._continue_payment_batch(update)

    async def _begin_payment_batch(self, update: Update, pending_ids: list[str]) -> None:
        if update.effective_user is None:
            return
        key = (update.effective_chat.id, update.effective_user.id)
        self.pending_payment_batches[key] = PendingPaymentBatch(
            pending_ids=pending_ids,
            chat_id=update.effective_chat.id,
            requested_by_user_id=update.effective_user.id,
        )
        await self._continue_payment_batch(update)

    async def _continue_payment_batch(self, update: Update) -> None:
        if update.effective_user is None:
            return
        key = (update.effective_chat.id, update.effective_user.id)
        batch = self.pending_payment_batches.get(key)
        if batch is None:
            return
        active_ids = [pending_id for pending_id in batch.pending_ids if pending_id in self.pending]
        if not active_ids:
            self.pending_payment_batches.pop(key, None)
            return
        batch.pending_ids = active_ids
        pending_id = active_ids[0]
        await self._request_payment_method(update, pending_id, position=(1, len(active_ids)))

    async def _reply_to_update(self, update: Update, text: str, reply_markup=None) -> None:
        if update.message is not None:
            await update.message.reply_text(text, reply_markup=reply_markup)
            return
        if update.callback_query is not None and update.callback_query.message is not None:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup)

    def _payment_choice_is_active(self, update: Update) -> bool:
        matching = self._matching_pending(update, latest_batch_only=True) or self._matching_pending(update)
        return any(pending.payment_options for _pending_id, pending in matching)

    def _clear_payment_batch(self, update: Update) -> None:
        if update.effective_user is not None:
            self.pending_payment_batches.pop((update.effective_chat.id, update.effective_user.id), None)

    def _expense_row(
        self,
        draft: ExpenseDraft,
        logged_by: str,
        update: Update,
        category: str,
        status: str,
        input_type: str,
        payment_method: str = "",
    ) -> ExpenseRow:
        return ExpenseRow(
            entry_id=uuid.uuid4().hex[:6],
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
            transaction_type=_transaction_type_for_category(category, input_type),
            payment_method=payment_method,
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
        payment_method: str = "",
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
            entry_id=uuid.uuid4().hex[:6],
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
            transaction_type=_transaction_type_for_category(category, input_type),
            payment_method=payment_method,
        )

    def _logged_line(self, row: ExpenseRow) -> str:
        if row.transaction_type.lower() == "income":
            return f"Logged income ${row.amount:.2f} to {row.category} - {self._human_date(row.timestamp.date())} [{row.entry_id}]"
        payment = f" via {row.payment_method}" if row.payment_method else ""
        return f"Logged ${row.amount:.2f} to {row.category} - {self._human_date(row.timestamp.date())}{payment} [{row.entry_id}]"

    def _human_date(self, value) -> str:
        return value.strftime("%-d %B %Y")


def _normalize_category(raw: str) -> str:
    lowered = raw.strip().lower()
    if lowered in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[lowered]
    for category in ALL_CATEGORIES:
        if category.lower() == lowered:
            return category
    for category in ALL_CATEGORIES:
        if category.lower().startswith(lowered):
            return category
    return raw


def _transaction_type_for_category(category: str, input_type: str) -> str:
    if input_type.lower() == "fixed":
        return "Fixed"
    if category.lower().startswith("income -"):
        return "Income"
    return "Expense"


def _position_from_text(raw: str, pending_count: int = 0) -> int:
    positions = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
    }
    lowered = raw.strip().lower()
    if lowered == "last":
        return pending_count
    if lowered in positions:
        return positions[lowered]
    return int(lowered)


def _parse_confirm_positions(text: str, pending_count: int) -> list[int]:
    match = re.fullmatch(r"confirm(?:ed)?\s+(.+)", text.strip().lower())
    if match is None:
        return []
    raw_positions = match.group(1).strip()
    if raw_positions in {"all", "them", "everything"} or " as " in raw_positions:
        return []
    tokens = re.findall(r"\b(\d+|first|second|third|fourth|fifth|last)\b", raw_positions)
    if not tokens:
        return []
    remaining = re.sub(r"\b(\d+|first|second|third|fourth|fifth|last|and|the|entry|entries)\b|[,/&+]", " ", raw_positions)
    if remaining.strip():
        return []
    positions = []
    for token in tokens:
        try:
            position = _position_from_text(token, pending_count)
        except ValueError:
            return []
        if 1 <= position <= pending_count:
            positions.append(position)
    return positions


def _looks_like_pending_position_request(text: str) -> bool:
    return bool(re.search(r"\b(change|update|make|confirm)\b.*\b(\d+|first|second|third|fourth|fifth|last|entry|one)\b", text))


def _looks_like_ai_request(text: str) -> bool:
    lowered = text.lower()
    triggers = [
        "delete",
        "remove",
        "change",
        "edit",
        "update",
        "correct",
        "how much",
        "how many",
        "what was",
        "what did",
        "last friday",
        "last week",
    ]
    return any(trigger in lowered for trigger in triggers)


def _is_date_change_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:change|edit|update|correct)\b.*\bdate\b|\bdate\b.*\b(?:change|edit|update|correct)\b",
            text,
            re.IGNORECASE,
        )
    )


def _last_day_of_month(value) -> object:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def _month_label(value) -> str:
    return value.strftime("%B %Y")


def _parse_fixed_month(text: str, today) -> object:
    lowered = text.lower()
    if "last month" in lowered or "previous month" in lowered:
        return today.replace(day=1) - timedelta(days=1)

    iso_match = re.search(r"\b(\d{4})-(\d{1,2})\b", lowered)
    if iso_match is not None:
        year = int(iso_match.group(1))
        month = int(iso_match.group(2))
        if 1 <= month <= 12:
            return today.replace(year=year, month=month, day=1)

    months = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    month_match = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{4})\b",
        lowered,
    )
    if month_match is not None:
        month = months[month_match.group(1)]
        year = int(month_match.group(2))
        return today.replace(year=year, month=month, day=1)

    return today


def _format_money(value) -> str:
    amount = Decimal(str(value))
    return f"${amount:,.2f}"


def _normalize_lookup_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def load_category_config_from_sheets(settings: Settings, sheets: SheetsClient) -> dict:
    sheet_category_config = sheets.get_category_config(settings.categories_sheet, settings.category_keywords_sheet)
    if not sheet_category_config.get("variable_categories"):
        raise RuntimeError(
            "No active categories found in Google Sheets. "
            f"Check sheet tabs '{settings.categories_sheet}' and '{settings.category_keywords_sheet}'."
        )
    return sheet_category_config


def main() -> None:
    logging.basicConfig(level=logging.INFO, force=True)
    print("GetRichBot startup reached.", flush=True)
    settings = Settings.load()
    LOGGER.info("Starting GetRichBot. Raw sheet: %s. Fixed sheet: %s.", settings.raw_expenses_sheet, settings.fixed_expenses_sheet)
    sheets = SheetsClient(
        settings.google_sheet_id,
        service_account_file=settings.service_account_file,
        service_account_json=settings.service_account_json,
    )
    sheet_category_config = load_category_config_from_sheets(settings, sheets)
    configure_category_config(sheet_category_config)
    LOGGER.info(
        "Loaded %d variable and %d fixed categories from Google Sheets tab %s. Keywords tab: %s.",
        len(VARIABLE_CATEGORIES),
        len(FIXED_CATEGORIES),
        sheet_category_config.get("categories_sheet_loaded"),
        sheet_category_config.get("keywords_sheet_loaded"),
    )
    finance_bot = FinanceBot(settings, sheets)

    print("Loading Telegram library...", flush=True)
    from telegram import Update
    from telegram.error import TelegramError
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
    print("Telegram library loaded.", flush=True)

    async def post_init(application: Application) -> None:
        try:
            bot_user = await application.bot.get_me()
        except TelegramError:
            LOGGER.exception("Telegram startup check failed. Check TELEGRAM_BOT_TOKEN and internet access.")
            raise
        LOGGER.info("Connected to Telegram as @%s. Send /whoami in the group chat.", bot_user.username)
        if settings.telegram_chat_id is None:
            LOGGER.warning("TELEGRAM_CHAT_ID is not set. Monthly reminder messages will not be sent.")
        else:
            application.create_task(finance_bot.run_monthly_scheduler(application.bot))

    application = Application.builder().token(settings.telegram_bot_token).post_init(post_init).build()
    application.add_handler(CommandHandler("start", finance_bot.start))
    application.add_handler(CommandHandler("help", finance_bot.help_command))
    application.add_handler(CommandHandler("whoami", finance_bot.whoami))
    application.add_handler(CommandHandler(["categories", "category"], finance_bot.categories))
    application.add_handler(CommandHandler("categorydebug", finance_bot.category_debug))
    application.add_handler(CommandHandler("refreshcategories", finance_bot.refresh_categories))
    application.add_handler(CommandHandler("refreshpayments", finance_bot.refresh_payments))
    application.add_handler(CommandHandler("pending", finance_bot.pending_command))
    application.add_handler(CommandHandler("summary", finance_bot.summary_command))
    application.add_handler(CommandHandler(["cards", "cardsummary"], finance_bot.cards_command))
    application.add_handler(CommandHandler("confirm", finance_bot.confirm_command))
    application.add_handler(CommandHandler("undo", finance_bot.undo_command))
    application.add_handler(CommandHandler("fixed", finance_bot.fixed_command))
    application.add_handler(CommandHandler("confirmfixed", finance_bot.confirm_fixed_command))
    application.add_handler(
        CallbackQueryHandler(
            finance_bot.handle_income_category_callback,
            pattern=rf"^{INCOME_CATEGORY_CALLBACK}\|",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            finance_bot.handle_payment_method_callback,
            pattern=rf"^{PAYMENT_METHOD_CALLBACK}\|",
        )
    )
    application.add_handler(MessageHandler(filters.PHOTO, finance_bot.handle_photo))
    application.add_handler(MessageHandler(filters.Document.IMAGE, finance_bot.handle_image_document))
    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, finance_bot.handle_voice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, finance_bot.handle_text))

    LOGGER.info("Bot is starting polling. Keep this Terminal window open.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
