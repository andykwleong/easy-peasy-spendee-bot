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

from getrichbot.categories import ALL_CATEGORIES, CATEGORY_ALIASES, FIXED_CATEGORIES, VARIABLE_CATEGORIES, configure_category_config
from getrichbot.config import Settings
from getrichbot.image_utils import prepare_image_for_vision
from getrichbot.models import ExpenseDraft, ExpenseRecord, ExpenseRow
from getrichbot.parser import categorize_description, extract_date_phrase, extract_standalone_date, parse_expense, parse_expenses
from getrichbot.sheets import SheetsClient
from getrichbot.summary import build_spending_summary, format_spending_summary, parse_summary_period
from getrichbot.summary import build_monthly_summary_table

LOGGER = logging.getLogger(__name__)
SINGAPORE_TZ = ZoneInfo("Asia/Singapore")
RECENT_DUPLICATE_WINDOW = timedelta(minutes=1)
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
/categories or /category - show categories
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
    batch_id: str | None = None


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


class FinanceBot:
    def __init__(self, settings: Settings, sheets: SheetsClient):
        self.settings = settings
        self.sheets = sheets
        self.pending: dict[str, PendingExpense] = {}
        self.pending_deletes: dict[tuple[int, int], PendingDelete] = {}
        self.pending_duplicates: dict[tuple[int, int], PendingDuplicate] = {}
        self.pending_edits: dict[tuple[int, int], PendingEdit] = {}
        self.latest_pending_batch: dict[tuple[int, int], str] = {}
        self.recent_logged: list[RecentLoggedExpense] = []
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
            pending_id = self._add_pending(draft, logged_by, update, "category")
            await update.message.reply_text(
                f"I found ${draft.amount:.2f}, but need a category.\n"
                f"Pending ID: {pending_id}\n"
                f"Reply: /confirm {pending_id} Food"
            )
            return

        row = self._expense_row(draft, logged_by, update, draft.category, "Confirmed", "Text")
        logged_line = await self._append_or_hold_duplicate(update, row)
        if logged_line:
            await update.message.reply_text(logged_line)

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
        logged_line = await self._append_or_hold_duplicate(update, row)
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

        if await self._handle_pending_edit_reply(update, lowered):
            return True

        if await self._handle_pending_delete_reply(update, lowered):
            return True

        if await self._handle_pending_duplicate_reply(update, lowered):
            return True

        if lowered in {"help", "what can you do", "commands"}:
            await update.message.reply_text(HELP_TEXT)
            return True

        if re.search(r"\bsummary\b", lowered):
            await self._reply_with_summary(update, text)
            return True

        if lowered in {"confirm fixed", "confirm fixed expenses", "log fixed", "log fixed expenses"}:
            await self._confirm_fixed_for_month(update, datetime.now(SINGAPORE_TZ).date())
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
        logged_line = await self._append_or_hold_duplicate(update, row)
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

        if lowered in {"yes", "ok", "okay", "looks good", "correct", "log it", "confirm", "confirm them", "log them"}:
            await self._confirm_all_pending(update, latest_batch_only=bool(self._latest_pending_batch_id(update)))
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
            changed = self._update_unclear_pending_categories(update, category_reply)
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

        record = self.sheets.get_last_matching_record(self.settings.raw_expenses_sheet, logged_by)
        if record is None:
            await update.message.reply_text("I could not find an expense to delete for you.")
            return
        await self._ask_delete_confirmation(update, record, logged_by_restriction=logged_by)

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

    async def _append_or_hold_duplicate(self, update: Update, row: ExpenseRow) -> str | None:
        if update.message is None or update.effective_user is None:
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
        )
        await update.message.reply_text(self._duplicate_prompt(row, duplicate))
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
            await update.message.reply_text("Duplicate not logged.")
            return True

        if lowered != "confirm":
            return False

        self.pending_duplicates.pop(key, None)
        self._append_expense(pending.row)
        self._remember_logged(pending.row)
        await update.message.reply_text(self._logged_line(pending.row))
        return True

    def _find_duplicate(self, row: ExpenseRow) -> ExpenseRecord | None:
        if row.input_type.lower() == "fixed":
            return None
        recent_duplicate = self._find_recent_duplicate(row)
        if recent_duplicate is not None:
            return recent_duplicate
        records = self.sheets.get_expense_records(self.settings.raw_expenses_sheet)
        row_date = row.timestamp.strftime("%Y-%m-%d")
        for record in reversed(records):
            if record.status.lower() != "confirmed" or record.input_type.lower() == "fixed":
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
                )
        return None

    def _duplicate_prompt(self, row: ExpenseRow, existing: ExpenseRecord) -> str:
        return (
            "Possible duplicate found:\n\n"
            f"Existing: {self._delete_candidate_line(existing)}\n\n"
            f"New: ${row.amount:.2f} to {row.category} - {self._human_date(row.timestamp.date())} - {row.description}\n\n"
            'Reply: "confirm" to log anyway or "cancel" to delete'
        )

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
            pending_id = self._add_pending(draft, logged_by, update, reason, batch_id=batch_id)
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
            lines.append("Or: confirm 2 as Food")
        return "\n\n".join(lines)

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
            )

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
            )
            changed = True
        return changed

    async def _confirm_pending_positions(
        self,
        update: Update,
        positions: list[int],
        category_override: str | None = None,
        latest_batch_only: bool = False,
    ) -> list[str]:
        matching = self._matching_pending(update, latest_batch_only=latest_batch_only)
        logged_lines = []
        for position in sorted(set(positions), reverse=True):
            if position < 1 or position > len(matching):
                continue
            pending_id, pending = matching[position - 1]
            category = category_override or pending.draft.category or self._infer_pending_category(pending)
            if category not in VARIABLE_CATEGORIES:
                continue
            self.pending.pop(pending_id, None)
            row = self._expense_row_from_pending(pending, category, "Confirmed", pending.reason.title(), None)
            logged_line = await self._append_or_hold_duplicate(update, row)
            if logged_line:
                logged_lines.append(logged_line)
        return list(reversed(logged_lines))

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

        logged_lines = []
        for pending_id, pending in matching:
            if pending.draft.category not in VARIABLE_CATEGORIES:
                continue
            self.pending.pop(pending_id, None)
            row = self._expense_row_from_pending(pending, pending.draft.category, "Confirmed", pending.reason.title(), None)
            logged_line = await self._append_or_hold_duplicate(update, row)
            if logged_line:
                logged_lines.append(logged_line)

        if logged_lines:
            await update.message.reply_text("\n\n".join(logged_lines))
        else:
            await update.message.reply_text("Pending expenses still need categories. Use: confirm abc123 Food")

    def _add_pending(self, draft: ExpenseDraft, logged_by: str, update: Update, reason: str, batch_id: str | None = None) -> str:
        pending_id = uuid.uuid4().hex[:6]
        self.pending[pending_id] = PendingExpense(
            draft=draft,
            logged_by=logged_by,
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            created_at=datetime.now(SINGAPORE_TZ),
            reason=reason,
            batch_id=batch_id,
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
    if lowered in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[lowered]
    for category in ALL_CATEGORIES:
        if category.lower() == lowered:
            return category
    for category in ALL_CATEGORIES:
        if category.lower().startswith(lowered):
            return category
    return raw


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
    sheet_category_config = sheets.get_category_config(settings.categories_sheet, settings.category_keywords_sheet)
    if sheet_category_config.get("variable_categories"):
        configure_category_config(sheet_category_config)
        LOGGER.info(
            "Loaded %d variable and %d fixed categories from Google Sheets.",
            len(VARIABLE_CATEGORIES),
            len(FIXED_CATEGORIES),
        )
    else:
        LOGGER.warning("No categories found in Google Sheets. Falling back to JSON/default category config.")
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
    application.add_handler(CommandHandler(["categories", "category"], finance_bot.categories))
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
