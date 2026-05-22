from __future__ import annotations

import asyncio
import calendar
import logging
import traceback
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import time
from datetime import datetime
from datetime import timedelta
from time import perf_counter
from zoneinfo import ZoneInfo

from getrichbot.categories import ALL_CATEGORIES, FIXED_CATEGORIES, VARIABLE_CATEGORIES
from getrichbot.config import Settings
from getrichbot.image_utils import prepare_image_for_vision
from getrichbot.models import ExpenseDraft, ExpenseRow
from getrichbot.parser import extract_date_phrase, extract_standalone_date, parse_expense
from getrichbot.sheets import SheetsClient
from getrichbot.summary import build_spending_summary, format_spending_summary, parse_summary_period
from getrichbot.summary import build_monthly_summary_table

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
/summary - show this month's checkpoint
/fixed - preview fixed expenses
/confirmfixed - add fixed expenses
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


class FinanceBot:
    def __init__(self, settings: Settings, sheets: SheetsClient):
        self.settings = settings
        self.sheets = sheets
        self.pending: dict[str, PendingExpense] = {}
        self.ai = None

    def _ai(self):
        if not self.settings.openai_api_key:
            return None
        if self.ai is None:
            from getrichbot.ai import AIInterpreter

            self.ai = AIInterpreter(self.settings.openai_api_key, self.settings.openai_model)
        return self.ai

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
        self._append_expense(row)
        await update.message.reply_text(self._logged_line(row))

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
            await update.message.reply_text("Usage: /confirm <pending_id> [category] [YYYY-MM-DD]")
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
        pending = self.pending.pop(pending_id, None)
        if pending is None:
            await update.message.reply_text("Pending ID not found.")
            return
        category = _normalize_category(category) if category else pending.draft.category or ""
        if category not in VARIABLE_CATEGORIES:
            await update.message.reply_text("Unknown category. Use /categories to see valid names.")
            self.pending[pending_id] = pending
            return

        row = self._expense_row_from_pending(pending, category, "Confirmed", "Text", date_override)
        self._append_expense(row)
        await update.message.reply_text(self._logged_line(row))

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
            await update.message.reply_text(intent.clarification_question or "Which expense should I use?")
            return True

        if intent.action == "answer":
            await update.message.reply_text(intent.answer or "I could not find a matching expense.")
            return True

        if intent.action == "delete":
            deleted = []
            for entry_id in intent.entry_ids:
                if entry_id not in by_id:
                    continue
                if self.sheets.delete_entry_by_id(self.settings.raw_expenses_sheet, entry_id):
                    deleted.append(entry_id)
            if deleted:
                self._refresh_monthly_summary()
                await update.message.reply_text("Deleted: " + ", ".join(deleted))
            else:
                await update.message.reply_text("I could not find a matching entry to delete.")
            return True

        if intent.action == "edit":
            changed = []
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
                self.sheets.update_expense_record(
                    self.settings.raw_expenses_sheet,
                    record.row_number,
                    amount=amount,
                    category=category,
                    description=update_item.description,
                    expense_date=update_item.date,
                )
                changed.append(update_item.entry_id)
            if changed:
                self._refresh_monthly_summary()
                await update.message.reply_text("Updated: " + ", ".join(changed))
            else:
                await update.message.reply_text("I could not find a matching entry to update.")
            return True

        return False

    async def handle_plain_language_command(self, update: Update) -> bool:
        if update.message is None or update.effective_user is None:
            return False
        text = (update.message.text or "").strip()
        lowered = text.lower()

        if lowered in {"help", "what can you do", "commands"}:
            await update.message.reply_text(HELP_TEXT)
            return True

        if re.search(r"\bsummary\b", lowered):
            await self._reply_with_summary(update, text)
            return True

        if lowered in {"confirm fixed", "confirm fixed expenses", "log fixed", "log fixed expenses"}:
            await self._confirm_fixed_for_month(update, datetime.now(SINGAPORE_TZ).date())
            return True

        if lowered in {"undo", "undo last", "delete last", "remove last"}:
            logged_by = self.settings.label_for_user(update.effective_user.id)
            if logged_by is None:
                await update.message.reply_text("I do not recognize this Telegram user ID yet.")
                return True
            deleted = self.sheets.delete_last_matching_row(self.settings.raw_expenses_sheet, logged_by)
            if deleted:
                self._refresh_monthly_summary()
            await update.message.reply_text("Deleted your latest logged expense." if deleted else "I could not find an expense to delete for you.")
            return True

        delete_match = re.match(r"^(?:delete|remove)\s+([a-z0-9]{6})$", lowered, re.IGNORECASE)
        if delete_match is not None:
            logged_by = self.settings.label_for_user(update.effective_user.id)
            if logged_by is None:
                await update.message.reply_text("I do not recognize this Telegram user ID yet.")
                return True
            deleted = self.sheets.delete_entry_by_id(
                self.settings.raw_expenses_sheet,
                delete_match.group(1),
                logged_by=logged_by,
            )
            if deleted:
                self._refresh_monthly_summary()
            await update.message.reply_text("Deleted that expense." if deleted else "I could not find that expense under your entries.")
            return True

        if lowered in {"confirm all", "log all", "extract all"}:
            await self._confirm_all_pending(update)
            return True

        match = re.match(r"^confirm\s+([a-f0-9]{6})(?:\s+(?:as\s+)?(.+?))?(?:\s+on\s+(\d{4}-\d{1,2}-\d{1,2}))?$", lowered, re.IGNORECASE)
        if match is None:
            return False

        pending_id = match.group(1)
        pending = self.pending.pop(pending_id, None)
        if pending is None:
            await update.message.reply_text("I could not find that pending item.")
            return True

        category_raw = (match.group(2) or "").strip()
        category = _normalize_category(category_raw) if category_raw else pending.draft.category or ""
        date_override = datetime.fromisoformat(match.group(3)).date() if match.group(3) else None
        if category not in VARIABLE_CATEGORIES:
            await update.message.reply_text("I do not recognize that category. Send 'categories' to see the list.")
            self.pending[pending_id] = pending
            return True

        row = self._expense_row_from_pending(pending, category, "Confirmed", "Text", date_override)
        self._append_expense(row)
        await update.message.reply_text(self._logged_line(row))
        return True

    async def handle_pending_update(self, update: Update) -> bool:
        if update.message is None or update.effective_user is None:
            return False

        return await self._handle_pending_text(update, update.message.text or "")

    async def _handle_pending_text(self, update: Update, text: str) -> bool:
        if update.message is None or update.effective_user is None:
            return False

        matching = self._matching_pending(update)
        if not matching:
            return False

        text = text.strip()
        lowered = text.lower()
        today = datetime.now(SINGAPORE_TZ).date()

        if lowered in {"yes", "ok", "okay", "looks good", "correct", "log it", "confirm", "confirm them", "log them"}:
            await self._confirm_all_pending(update)
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

        return False

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
            await update.message.reply_text("Extracting expenses from the screenshot...")

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

        deleted = self.sheets.delete_last_matching_row(self.settings.raw_expenses_sheet, logged_by)
        if deleted:
            self._refresh_monthly_summary()
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

        await self._confirm_fixed_for_month(update, datetime.now(SINGAPORE_TZ).date())

    async def _confirm_fixed_for_month(self, update: Update, month_date) -> None:
        if update.message is None or update.effective_user is None:
            return

        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return

        added_count = self._add_fixed_expenses_for_month(
            month_date=month_date,
            logged_by=logged_by,
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
        target_month = _month_label(month_date)
        if added_count:
            self._refresh_monthly_summary(month_date.strftime("%Y-%m"))
            await update.message.reply_text(f"Added {added_count} fixed expenses for {target_month}.")
        else:
            await update.message.reply_text(f"Fixed expenses for {target_month} were already added, or none are active.")

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

        logged_lines = []
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
            row = self._expense_row(draft, logged_by, update, draft.category, "Confirmed", "Text")
            self._append_expense(row)
            logged_lines.append(self._logged_line(row))

        message = "\n\n".join(logged_lines) if logged_lines else f"No expenses logged for {self._human_date(shared_date)}."
        if pending_ids:
            message += "\n\nPending IDs: " + ", ".join(pending_ids)
        await update.message.reply_text(message)
        return True

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
        records = self.sheets.get_expense_records(self.settings.raw_expenses_sheet)
        summary = build_spending_summary(records, period)
        await update.message.reply_text(format_spending_summary(summary))

    def _add_fixed_expenses_for_month(self, month_date, logged_by: str, chat_id: int | str, message_id: int | str) -> int:
        fixed = self.sheets.get_fixed_expenses(self.settings.fixed_expenses_sheet)
        if not fixed:
            return 0

        records = self.sheets.get_expense_records(self.settings.raw_expenses_sheet)
        target_month = month_date.strftime("%Y-%m")
        existing_fixed_categories = {
            record.category
            for record in records
            if record.month == target_month and record.input_type.lower() == "fixed" and record.status.lower() == "confirmed"
        }

        expense_date = _last_day_of_month(month_date)
        added_count = 0
        for item in fixed:
            category = str(item["category"])
            if category not in FIXED_CATEGORIES or category in existing_fixed_categories:
                continue
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
            )
            self.sheets.append_expense(self.settings.raw_expenses_sheet, row)
            added_count += 1
        return added_count

    def _append_expense(self, row: ExpenseRow) -> None:
        self.sheets.append_expense(self.settings.raw_expenses_sheet, row)
        self._refresh_monthly_summary(row.timestamp.strftime("%Y-%m"))

    def _refresh_monthly_summary(self, include_month: str | None = None) -> None:
        records = self.sheets.get_expense_records(self.settings.raw_expenses_sheet)
        table = build_monthly_summary_table(records, include_month=include_month)
        self.sheets.update_monthly_summary(self.settings.monthly_summary_sheet, table)

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

        fixed = self.sheets.get_fixed_expenses(self.settings.fixed_expenses_sheet)
        if not fixed:
            message = f"No active fixed expenses found for {_month_label(today)}."
        else:
            lines = [f"Month-end fixed expenses check for {_month_label(today)}:"]
            lines.extend(f"{item['category']}: ${item['amount']:.2f}" for item in fixed)
            lines.append("Reply: confirm fixed")
            message = "\n\n".join(lines)

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
            pending_id = self._add_pending(draft, logged_by, update, reason)
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

    def _matching_pending(self, update: Update) -> list[tuple[str, PendingExpense]]:
        if update.effective_user is None:
            return []
        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            return []
        return [
            (pending_id, pending)
            for pending_id, pending in self.pending.items()
            if pending.logged_by == logged_by and pending.chat_id == update.effective_chat.id
        ]

    def _update_pending_dates(self, update: Update, parsed_date) -> bool:
        changed = False
        for pending_id, pending in self._matching_pending(update):
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
            )
            changed = True
        return changed

    def _pending_summary(self, update: Update, title: str) -> str:
        lines = [title]
        for index, (pending_id, pending) in enumerate(self._matching_pending(update), start=1):
            date_value = pending.draft.expense_date or datetime.now(SINGAPORE_TZ).date()
            category = pending.draft.category or "category unclear"
            lines.append(
                f"{index}. ${pending.draft.amount:.2f} to {category} - "
                f"{self._human_date(date_value)} - {pending.draft.description} [{pending_id}]"
            )
        lines.append("Reply: confirm all")
        return "\n\n".join(lines)

    async def _handle_ai_pending_instruction(self, update: Update, text: str) -> bool:
        ai = self._ai()
        if ai is None or update.message is None:
            return False

        matching = self._matching_pending(update)
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
            logged_lines = self._confirm_pending_positions(update, instruction.confirm_positions)

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
        matching = self._matching_pending(update)
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
            )

    def _confirm_pending_positions(self, update: Update, positions: list[int]) -> list[str]:
        matching = self._matching_pending(update)
        logged_lines = []
        for position in sorted(set(positions), reverse=True):
            if position < 1 or position > len(matching):
                continue
            pending_id, pending = matching[position - 1]
            if pending.draft.category not in VARIABLE_CATEGORIES:
                continue
            self.pending.pop(pending_id, None)
            row = self._expense_row_from_pending(pending, pending.draft.category, "Confirmed", pending.reason.title(), None)
            self._append_expense(row)
            logged_lines.append(self._logged_line(row))
        return list(reversed(logged_lines))

    async def _confirm_all_pending(self, update: Update) -> None:
        if update.message is None or update.effective_user is None:
            return
        logged_by = self.settings.label_for_user(update.effective_user.id)
        if logged_by is None:
            await update.message.reply_text("I do not recognize this Telegram user ID yet.")
            return

        matching = self._matching_pending(update)
        if not matching:
            await update.message.reply_text("No pending expenses to confirm.")
            return

        logged_lines = []
        for pending_id, pending in matching:
            if pending.draft.category not in VARIABLE_CATEGORIES:
                continue
            self.pending.pop(pending_id, None)
            row = self._expense_row_from_pending(pending, pending.draft.category, "Confirmed", pending.reason.title(), None)
            self._append_expense(row)
            logged_lines.append(self._logged_line(row))

        if logged_lines:
            await update.message.reply_text("\n\n".join(logged_lines))
        else:
            await update.message.reply_text("Pending expenses still need categories. Use: confirm abc123 Food")

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
        )

    def _logged_line(self, row: ExpenseRow) -> str:
        return f"Logged ${row.amount:.2f} to {row.category} - {self._human_date(row.timestamp.date())} [{row.entry_id}]"

    def _human_date(self, value) -> str:
        return value.strftime("%-d %B %Y")


def _normalize_category(raw: str) -> str:
    lowered = raw.strip().lower()
    aliases = {
        "grocery": "Groceries",
        "groceries": "Groceries",
    }
    if lowered in aliases:
        return aliases[lowered]
    for category in ALL_CATEGORIES:
        if category.lower() == lowered:
            return category
    for category in ALL_CATEGORIES:
        if category.lower().startswith(lowered):
            return category
    return raw


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


def _last_day_of_month(value) -> object:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def _month_label(value) -> str:
    return value.strftime("%B %Y")


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
    finance_bot = FinanceBot(settings, sheets)

    print("Loading Telegram library...", flush=True)
    from telegram import Update
    from telegram.error import TelegramError
    from telegram.ext import Application, CommandHandler, MessageHandler, filters
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
    application.add_handler(CommandHandler("categories", finance_bot.categories))
    application.add_handler(CommandHandler("pending", finance_bot.pending_command))
    application.add_handler(CommandHandler("summary", finance_bot.summary_command))
    application.add_handler(CommandHandler("confirm", finance_bot.confirm_command))
    application.add_handler(CommandHandler("undo", finance_bot.undo_command))
    application.add_handler(CommandHandler("fixed", finance_bot.fixed_command))
    application.add_handler(CommandHandler("confirmfixed", finance_bot.confirm_fixed_command))
    application.add_handler(MessageHandler(filters.PHOTO, finance_bot.handle_photo))
    application.add_handler(MessageHandler(filters.Document.IMAGE, finance_bot.handle_image_document))
    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, finance_bot.handle_voice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, finance_bot.handle_text))

    LOGGER.info("Bot is starting polling. Keep this Terminal window open.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
