import unittest
from datetime import date
from datetime import datetime as real_datetime
from decimal import Decimal
from unittest.mock import patch

from getrichbot.bot import FinanceBot
from getrichbot.models import ExpenseRecord


class FakeSettings:
    raw_expenses_sheet = "Raw Expenses"
    monthly_summary_sheet = "Monthly Summary"
    me_label = "Me"
    wife_label = "My wife"

    def label_for_user(self, telegram_user_id):
        return "Me" if telegram_user_id == 123 else None


class FakeSheets:
    def __init__(self, records=None):
        self.rows = []
        self.records = records or []

    def append_expense(self, sheet_name, row):
        self.rows.append(row)
        self.records.append(
            ExpenseRecord(
                row_number=len(self.records) + 2,
                entry_id=row.entry_id,
                timestamp=row.timestamp.strftime("%H:%M:%S"),
                expense_date=row.timestamp.strftime("%Y-%m-%d"),
                month=row.timestamp.strftime("%Y-%m"),
                logged_by=row.logged_by,
                raw_input=row.raw_input,
                amount=row.amount,
                category=row.category,
                description=row.description,
                input_type=row.input_type,
                status=row.status,
            )
        )

    def get_expense_records(self, sheet_name):
        return self.records

    def update_monthly_summary(self, sheet_name, rows):
        pass


class FakeUser:
    id = 123


class FakeChat:
    id = -100


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.message_id = 456
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, text):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser()
        self.effective_chat = FakeChat()


class TestMultiline(unittest.IsolatedAsyncioTestCase):
    async def test_logs_one_dated_expense_per_line(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate(
            "19th may 2026 food 20.62\n"
            "17th may 2026 food 22.54\n"
            "16th may 2026 25.18 food\n"
            "14th may 2026 24.1 food"
        )

        handled = await bot.handle_multiline_text(update, context=None)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 4)
        self.assertEqual([str(row.amount) for row in sheets.rows], ["20.62", "22.54", "25.18", "24.1"])
        self.assertEqual([row.timestamp.date().isoformat() for row in sheets.rows], [
            "2026-05-19",
            "2026-05-17",
            "2026-05-16",
            "2026-05-14",
        ])
        self.assertTrue(all(row.category == "Food" for row in sheets.rows))

    async def test_logs_new_lines_and_holds_duplicate_line(self):
        existing = ExpenseRecord(
            row_number=2,
            entry_id="abc123",
            timestamp="12:00:00",
            expense_date="2026-05-15",
            month="2026-05",
            logged_by="Me",
            raw_input="15th may dinner 20",
            amount=Decimal("20"),
            category="Food",
            description="dinner",
            input_type="Text",
            status="Confirmed",
        )
        sheets = FakeSheets(records=[existing])
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate(
            "15th may 2026 dinner 20\n"
            "16th may 2026 groceries 50"
        )

        handled = await bot.handle_multiline_text(update, context=None)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 1)
        self.assertEqual(sheets.rows[0].category, "Groceries")
        self.assertIn("Possible duplicate found:", update.message.replies[0])
        self.assertIn('Reply: "confirm" to log anyway or "cancel" to delete', update.message.replies[0])
        self.assertIn("Logged $50.00 to Groceries", update.message.replies[-1])

    async def test_logs_multiple_undated_lines_for_today(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("Dinner 83.93\nDessert 22.54")

        with patch("getrichbot.bot.datetime") as fake_datetime:
            fake_datetime.now.return_value = real_datetime(2026, 5, 24, 12, 0, 0)
            fake_datetime.combine.side_effect = real_datetime.combine
            handled = await bot.handle_multiline_text(update, context=None)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 2)
        self.assertEqual([str(row.amount) for row in sheets.rows], ["83.93", "22.54"])
        self.assertTrue(all(row.category == "Food" for row in sheets.rows))
        self.assertEqual([row.timestamp.date().isoformat() for row in sheets.rows], ["2026-05-24", "2026-05-24"])


if __name__ == "__main__":
    unittest.main()
