import unittest
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from getrichbot.bot import DUPLICATE_NOT_LOGGED_TEXT
from getrichbot.bot import REFRESH_PAYMENT_COMMANDS
from getrichbot.bot import FinanceBot, PendingDuplicate, _normalize_category
from getrichbot.cards import parse_payment_config
from getrichbot.categories import CATEGORY_ALIASES
from getrichbot.models import ExpenseRecord
from getrichbot.models import ExpenseRow


class TestDeleteConfirmation(unittest.TestCase):
    def test_delete_candidate_line_matches_logged_format(self):
        record = ExpenseRecord(
            row_number=2,
            entry_id="d5cf6d",
            timestamp="12:00:00",
            expense_date="2026-05-16",
            month="2026-05",
            logged_by="Me",
            raw_input="dinner",
            amount=Decimal("25.18"),
            category="Food",
            description="Dinner",
            input_type="Text",
            status="Confirmed",
        )

        bot = FinanceBot.__new__(FinanceBot)

        self.assertEqual(
            bot._delete_candidate_line(record),
            "$25.18 logged as Food - 16 May 2026 [d5cf6d]",
        )

    def test_category_aliases_for_bills(self):
        aliases = {
            "baby": CATEGORY_ALIASES["baby"],
            "electricity bills": CATEGORY_ALIASES["electricity"],
            "insurance": CATEGORY_ALIASES["insurance"],
        }
        for optional_alias in ["bills baby", "sp bills", "singtel", "ar;yn", "misc bills"]:
            if optional_alias in CATEGORY_ALIASES:
                aliases[optional_alias] = CATEGORY_ALIASES[optional_alias]

        for raw, expected in aliases.items():
            with self.subTest(raw=raw):
                self.assertEqual(_normalize_category(raw), expected)


class FakeSettings:
    raw_expenses_sheet = "Raw Expenses"
    monthly_summary_sheet = "Monthly Summary"
    payment_methods_sheet = "Payment Methods"
    card_limits_sheet = "Card Limits"

    def label_for_user(self, telegram_user_id):
        return "My wife" if telegram_user_id == 456 else None


class FakeSheets:
    def __init__(self, record):
        self.record = record

    def get_record_by_id(self, sheet_name, entry_id, logged_by=None):
        if entry_id.lower() != self.record.entry_id.lower():
            return None
        if logged_by is not None and logged_by != self.record.logged_by:
            return None
        return self.record


class FakeUser:
    id = 456


class FakeChat:
    id = -100


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []
        self.reply_to_message = None

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeReply:
    def __init__(self, text):
        self.text = text


class FakeUpdate:
    def __init__(self, text, reply_text=None):
        self.message = FakeMessage(text)
        if reply_text is not None:
            self.message.reply_to_message = FakeReply(reply_text)
        self.effective_user = FakeUser()
        self.effective_chat = FakeChat()


class TestBareEntryIdDelete(unittest.IsolatedAsyncioTestCase):
    async def test_bare_entry_id_opens_delete_confirmation(self):
        record = ExpenseRecord(
            row_number=2,
            entry_id="1d9c9a",
            timestamp="12:00:00",
            expense_date="2026-05-23",
            month="2026-05",
            logged_by="My wife",
            raw_input="$80 baby shoes",
            amount=Decimal("80"),
            category=CATEGORY_ALIASES["baby"],
            description="baby shoes",
            input_type="Text",
            status="Confirmed",
        )
        bot = FinanceBot(FakeSettings(), FakeSheets(record))
        update = FakeUpdate("1d9c9a")

        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertIn(f"Delete $80.00 logged as {CATEGORY_ALIASES['baby']} - 23 May 2026 [1d9c9a]?", update.message.replies[0])


def sample_expense(entry_id="ab12cd"):
    return ExpenseRecord(
        row_number=2,
        entry_id=entry_id,
        timestamp="09:00:00",
        expense_date="2026-10-12",
        month="2026-10",
        logged_by="My wife",
        raw_input="sample shop",
        amount=Decimal("165.00"),
        category="Food",
        description="SAMPLE SHOP",
        input_type="Text",
        status="Confirmed",
        transaction_type="Expense",
    )


class MemorySheets:
    def __init__(self, records):
        self.records = list(records)

    def get_record_by_id(self, sheet_name, entry_id, logged_by=None):
        for record in self.records:
            if record.entry_id.lower() != entry_id.lower():
                continue
            if logged_by is not None and record.logged_by != logged_by:
                return None
            return record
        return None

    def delete_entry_by_id(self, sheet_name, entry_id, logged_by=None):
        for record in list(self.records):
            if record.entry_id.lower() != entry_id.lower():
                continue
            if logged_by is not None and record.logged_by != logged_by:
                return False
            self.records.remove(record)
            return True
        return False

    def get_expense_records(self, sheet_name):
        return list(self.records)

    def update_monthly_summary(self, sheet_name, rows):
        return None

    def get_payment_config(self, methods_sheet, limits_sheet):
        return parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active", "Last 4"],
                ["Sample Visa", "My wife", "Credit Card", "Calendar", "1", "TRUE", "1111"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
                ["Sample Visa", "My wife", "All", "All", "100", "TRUE"],
            ],
        )


class TestReplyDelete(unittest.IsolatedAsyncioTestCase):
    async def test_reply_delete_removes_the_row_without_asking_again(self):
        sheets = MemorySheets([sample_expense()])
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("delete", reply_text="Logged $165.00 to Food - 12 October 2026 [ab12cd]")

        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(sheets.records, [])
        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("Deleted $165.00 logged as Food - 12 October 2026 [ab12cd]", update.message.replies[0])
        self.assertNotIn("yes to delete", update.message.replies[0].lower())
        self.assertNotIn("entry code", update.message.replies[0].lower())

    async def test_reply_without_an_entry_id_asks_for_the_logged_message(self):
        sheets = MemorySheets([sample_expense()])
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("delete", reply_text="Please choose a payment method.")

        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.records), 1)
        self.assertIn("logged message", update.message.replies[0].lower())
        self.assertNotIn("yes to delete", update.message.replies[0].lower())

    async def test_delete_that_is_not_a_reply_still_asks(self):
        sheets = MemorySheets([sample_expense()])
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("delete ab12cd")

        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.records), 1)
        self.assertIn("yes to delete", update.message.replies[0].lower())

    async def test_plain_cancel_does_not_log_a_pending_duplicate(self):
        sheets = MemorySheets([sample_expense()])
        bot = FinanceBot(FakeSettings(), sheets)
        row = ExpenseRow(
            entry_id="cd34ef",
            timestamp=datetime(2026, 10, 12, 9, 0, tzinfo=ZoneInfo("Asia/Singapore")),
            logged_by="My wife",
            raw_input="sample shop",
            amount=Decimal("165.00"),
            category="Food",
            description="SAMPLE SHOP",
            input_type="Text",
            status="Confirmed",
            telegram_chat_id=-100,
            telegram_message_id="1",
        )
        bot.pending_duplicates[(-100, 456)] = PendingDuplicate(
            row=row,
            existing_record=sample_expense(),
            chat_id=-100,
            requested_by_user_id=456,
            created_at=datetime(2026, 10, 12, 9, 1, tzinfo=ZoneInfo("Asia/Singapore")),
        )
        update = FakeUpdate("cancel")

        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(update.message.replies, [DUPLICATE_NOT_LOGGED_TEXT])
        self.assertNotIn((-100, 456), bot.pending_duplicates)
        self.assertEqual(len(sheets.records), 1)

    async def test_refreshpayment_uses_the_same_reload_as_refreshpayments(self):
        self.assertEqual(REFRESH_PAYMENT_COMMANDS, ("refreshpayments", "refreshpayment"))
        sheets = MemorySheets([])
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("/refreshpayment")

        await bot.refresh_payments(update, None)

        self.assertIn("Payment methods refreshed from Google Sheets.", update.message.replies[0])
        self.assertIn("Active payment methods: 1", update.message.replies[0])


if __name__ == "__main__":
    unittest.main()
