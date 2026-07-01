import unittest
from datetime import date, datetime
from decimal import Decimal

from getrichbot.categories import FIXED_CATEGORIES
from getrichbot.bot import FinanceBot
from getrichbot.models import ExpenseRecord
from getrichbot.sheets import _parse_sheet_amount


class FakeSettings:
    raw_expenses_sheet = "Raw Expenses"
    fixed_expenses_sheet = "Fixed Expenses"
    monthly_summary_sheet = "Monthly Summary"
    bot_state_sheet = "Bot State"
    telegram_chat_id = -100
    me_label = "Me"
    wife_label = "My wife"

    def label_for_user(self, telegram_user_id):
        return "Me" if telegram_user_id == 123 else None


class FakeSheets:
    def __init__(self):
        fixed_categories = list(FIXED_CATEGORIES[:3])
        self.fixed = [
            {"category": fixed_categories[0], "amount": Decimal("920"), "notes": ""},
            {"category": fixed_categories[1], "amount": Decimal("1000"), "notes": ""},
            {"category": fixed_categories[2], "amount": Decimal("96.83"), "notes": ""},
        ]
        self.records = []
        self.rows = []
        self.state = {}
        self.summary_updates = []
        self.hide_appended_records = False

    def get_fixed_expenses(self, sheet_name):
        return [dict(item) for item in self.fixed]

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
                transaction_type=row.transaction_type,
            )
        )

    def get_expense_records(self, sheet_name):
        if self.hide_appended_records:
            return []
        return self.records

    def delete_fixed_expenses_for_month(self, sheet_name, month):
        before = len(self.records)
        self.records = [
            record
            for record in self.records
            if not (
                record.month == month
                and record.status.lower() == "confirmed"
                and (record.transaction_type.lower() == "fixed" or record.input_type.lower() == "fixed")
            )
        ]
        return before - len(self.records)

    def update_monthly_summary(self, sheet_name, rows):
        self.summary_updates.append(rows)

    def get_state_value(self, sheet_name, key):
        return self.state.get(key)

    def set_state_value(self, sheet_name, key, value):
        self.state[key] = value


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


class FakeTelegramBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


class TestFixedReview(unittest.IsolatedAsyncioTestCase):
    def test_fixed_amount_parser_accepts_currency_formatting(self):
        examples = {
            "$56.92": Decimal("56.92"),
            "S$1,227.84": Decimal("1227.84"),
            "1,139.00": Decimal("1139.00"),
            "96.83": Decimal("96.83"),
        }

        for raw, expected in examples.items():
            with self.subTest(raw=raw):
                self.assertEqual(_parse_sheet_amount(raw), expected)

    async def test_confirm_fixed_month_starts_review_then_edits_and_confirms(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("confirm fixed May 2026")

        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 0)
        self.assertIn("Confirm fixed expenses for May 2026:", update.message.replies[0])
        self.assertIn(f"{sheets.fixed[0]['category']}: $920.00", update.message.replies[0])

        update.message = FakeMessage(f"{sheets.fixed[0]['category']} change to 930 and {sheets.fixed[2]['category']} change to 100")
        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertIn(f"{sheets.fixed[0]['category']}: $930.00", update.message.replies[0])
        self.assertIn(f"{sheets.fixed[2]['category']}: $100.00", update.message.replies[0])

        update.message = FakeMessage("confirm fixed")
        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 3)
        self.assertEqual(sheets.rows[0].timestamp.date().isoformat(), "2026-05-31")
        self.assertEqual(sheets.rows[0].amount, Decimal("930"))
        self.assertEqual(sheets.rows[2].amount, Decimal("100"))
        self.assertTrue(sheets.summary_updates)
        self.assertIn("Added 3 fixed expenses for May 2026.", update.message.replies[0])

    async def test_fixed_review_accepts_change_first_and_unique_short_name(self):
        sheets = FakeSheets()
        sheets.fixed = [
            {"category": "Bills (Example Provider)", "amount": Decimal("56.92"), "notes": ""},
            {"category": "Loan repayment", "amount": Decimal("1000"), "notes": ""},
        ]
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("confirm fixed June 2026")

        await bot.handle_plain_language_command(update)
        update.message = FakeMessage("change example provider to 64.02")
        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertIn("Bills (Example Provider): $64.02", update.message.replies[0])
        self.assertIn("Loan repayment: $1,000.00", update.message.replies[0])

    async def test_fixed_review_rejects_ambiguous_short_name(self):
        sheets = FakeSheets()
        sheets.fixed = [
            {"category": "Mortgage - Home A", "amount": Decimal("900"), "notes": ""},
            {"category": "Mortgage - Home B", "amount": Decimal("1200"), "notes": ""},
        ]
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("confirm fixed June 2026")

        await bot.handle_plain_language_command(update)
        update.message = FakeMessage("change mortgage to 1000")
        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertIn("I could not match these fixed expense names:", update.message.replies[0])
        self.assertIn("- mortgage", update.message.replies[0])
        self.assertEqual(bot.pending_fixed_reviews[-100].items[0]["amount"], Decimal("900"))
        self.assertEqual(bot.pending_fixed_reviews[-100].items[1]["amount"], Decimal("1200"))

    async def test_fixed_review_logs_every_reviewed_item_even_if_not_in_category_config(self):
        sheets = FakeSheets()
        sheets.fixed = [
            {"category": f"Custom Fixed {index}", "amount": Decimal(str(index)), "notes": ""}
            for index in range(1, 11)
        ]
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("confirm fixed May 2026")

        handled = await bot.handle_plain_language_command(update)
        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 0)
        self.assertIn("Custom Fixed 10: $10.00", update.message.replies[0])

        update.message = FakeMessage("confirmed fixed")
        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 10)
        self.assertEqual({row.category for row in sheets.rows}, {f"Custom Fixed {index}" for index in range(1, 11)})
        self.assertIn("Added 10 fixed expenses for May 2026.", update.message.replies[0])

    async def test_fixed_review_does_not_skip_existing_fixed_categories(self):
        sheets = FakeSheets()
        existing_category = sheets.fixed[0]["category"]
        sheets.records.append(
            ExpenseRecord(
                row_number=2,
                entry_id="abc123",
                timestamp="09:00:00",
                expense_date="2026-05-31",
                month="2026-05",
                logged_by="Me",
                raw_input="Fixed expense confirmation",
                amount=Decimal("920"),
                category=existing_category,
                description=existing_category,
                input_type="Fixed",
                status="Confirmed",
            )
        )
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("confirm fixed May 2026")

        await bot.handle_plain_language_command(update)
        update.message = FakeMessage("confirm")
        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 3)
        self.assertIn(existing_category, {row.category for row in sheets.rows})
        self.assertIn("Added 3 fixed expenses for May 2026.", update.message.replies[0])
        self.assertIn("Monthly Summary updated.", update.message.replies[0])

    async def test_fixed_review_writes_reviewed_values_to_monthly_summary_directly(self):
        sheets = FakeSheets()
        sheets.hide_appended_records = True
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("confirm fixed May 2026")

        await bot.handle_plain_language_command(update)
        update.message = FakeMessage("confirm")
        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 3)
        self.assertTrue(sheets.summary_updates)
        latest_summary = sheets.summary_updates[-1]
        self.assertIn([sheets.fixed[0]["category"], "920.00"], latest_summary)
        self.assertIn([sheets.fixed[1]["category"], "1000.00"], latest_summary)
        self.assertIn([sheets.fixed[2]["category"], "96.83"], latest_summary)

    async def test_month_end_reminder_creates_review_and_state(self):
        sheets = FakeSheets()
        finance_bot = FinanceBot(FakeSettings(), sheets)
        telegram_bot = FakeTelegramBot()

        await finance_bot._send_fixed_expense_reminder(telegram_bot, date(2026, 5, 31))

        self.assertEqual(sheets.state["fixed_reminder_sent:2026-05"], "yes")
        self.assertIn(-100, finance_bot.pending_fixed_reviews)
        self.assertEqual(len(telegram_bot.messages), 1)
        self.assertIn("Confirm fixed expenses for May 2026:", telegram_bot.messages[0][1])
        self.assertIn("Reply: confirm fixed", telegram_bot.messages[0][1])


if __name__ == "__main__":
    unittest.main()
