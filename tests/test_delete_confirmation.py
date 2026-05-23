import unittest
from decimal import Decimal

from getrichbot.bot import FinanceBot, _normalize_category
from getrichbot.categories import CATEGORY_ALIASES
from getrichbot.models import ExpenseRecord


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

    async def reply_text(self, text):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, text):
        self.message = FakeMessage(text)
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


if __name__ == "__main__":
    unittest.main()
