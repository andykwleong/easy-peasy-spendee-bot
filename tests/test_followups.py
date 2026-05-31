import unittest
from decimal import Decimal

from getrichbot.bot import FinanceBot
from getrichbot.ai import EntryUpdate, ExpenseIntent
from getrichbot.models import ExpenseDraft, ExpenseRecord


class FakeSettings:
    raw_expenses_sheet = "Raw Expenses"
    monthly_summary_sheet = "Monthly Summary"
    me_label = "Me"
    wife_label = "My wife"
    openai_api_key = None
    openai_model = "test-model"

    def label_for_user(self, telegram_user_id):
        return "My wife" if telegram_user_id == 456 else None


class FakeSheets:
    def __init__(self, records=None):
        self.records = records or []
        self.rows = []
        self.updated = []

    def append_expense(self, sheet_name, row):
        self.rows.append(row)

    def get_expense_records(self, sheet_name):
        return self.records

    def get_last_matching_record(self, sheet_name, logged_by):
        for record in reversed(self.records):
            if record.logged_by == logged_by:
                return record
        return None

    def update_expense_record(self, sheet_name, row_number, amount=None, category=None, description=None, expense_date=None):
        self.updated.append(
            {
                "row_number": row_number,
                "amount": amount,
                "category": category,
                "description": description,
                "expense_date": expense_date,
            }
        )

    def update_monthly_summary(self, sheet_name, rows):
        pass


class FakeUser:
    id = 456


class FakeChat:
    id = -100


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.message_id = 789
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, text):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser()
        self.effective_chat = FakeChat()


def record() -> ExpenseRecord:
    return ExpenseRecord(
        row_number=5,
        entry_id="4bea7c",
        timestamp="22:13:00",
        expense_date="2026-05-24",
        month="2026-05",
        logged_by="My wife",
        raw_input="30 gift",
        amount=Decimal("30"),
        category="Gifts",
        description="gift",
        input_type="Text",
        status="Confirmed",
    )


class TestFollowups(unittest.IsolatedAsyncioTestCase):
    async def test_change_multiple_pending_categories_keeps_items_pending(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("Change 3 to groceries and change 5 to food")
        bot.latest_pending_batch[(-100, 456)] = "screenshot"
        bot.pending = {
            "one111": bot_pending("2", "Old Chang Kee", "Food", batch_id="screenshot"),
            "two222": bot_pending("57.20", "CS Fresh", "Groceries", batch_id="screenshot"),
            "three3": bot_pending("60.70", "Nai Nai Flavour", "Food", batch_id="screenshot"),
            "four44": bot_pending("15.82", "Sheng Siong", "Groceries", batch_id="screenshot"),
            "five55": bot_pending("30.81", "Paradise Classic", "Groceries", batch_id="screenshot"),
        }

        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(sheets.rows, [])
        self.assertEqual(bot.pending["three3"].draft.category, "Groceries")
        self.assertEqual(bot.pending["five55"].draft.category, "Food")
        self.assertIn("Updated pending entries:", update.message.replies[0])
        self.assertIn("3. $60.70 to Groceries", update.message.replies[0])
        self.assertIn("5. $30.81 to Food", update.message.replies[0])

    async def test_confirm_targets_latest_pending_batch_only(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("confirm")
        bot.latest_pending_batch[(-100, 456)] = "voice"
        bot.pending = {
            "oldone": bot_pending("30.81", "Paradise Classic", "Food", batch_id="screenshot"),
            "voice1": bot_pending("100", "shoes", "Shopping - My wife", batch_id="voice"),
        }

        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 1)
        self.assertEqual(sheets.rows[0].amount, Decimal("100"))
        self.assertEqual(sheets.rows[0].description, "shoes")
        self.assertIn("oldone", bot.pending)
        self.assertNotIn("voice1", bot.pending)

    async def test_recent_logged_memory_catches_duplicate_before_sheet_readback(self):
        sheets = FakeSheets(records=[])
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("confirm")
        bot.latest_pending_batch[(-100, 456)] = "screenshot"
        first = bot_pending("30.81", "Paradise Classic", "Food", batch_id="screenshot")
        second = bot_pending("30.81", "Paradise Classic", "Food", batch_id="voice")
        bot.pending = {"first1": first}

        await bot.handle_pending_update(update)

        self.assertEqual(len(sheets.rows), 1)

        bot.latest_pending_batch[(-100, 456)] = "voice"
        bot.pending = {"voice1": second}
        update.message = FakeMessage("confirm")
        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 1)
        self.assertIn("Possible duplicate found:", update.message.replies[0])

    async def test_confirm_number_confirms_pending_position(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("confirm 2")
        bot.pending = {
            "first1": bot_pending("21", "gifts spent on"),
            "second": bot_pending("30", ", gifts"),
        }

        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 1)
        self.assertEqual(sheets.rows[0].amount, Decimal("30"))
        self.assertEqual(sheets.rows[0].category, "Gifts")
        self.assertNotIn("second", bot.pending)
        self.assertIn("first1", bot.pending)

    async def test_category_reply_updates_unclear_pending_entries(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("gift")
        bot.pending = {"abc123": bot_pending("30", "unknown thing")}

        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(bot.pending["abc123"].draft.category, "Gifts")
        self.assertIn("Updated pending category to Gifts:", update.message.replies[0])

    async def test_confirm_after_single_text_pending_category_update_logs_item(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("Food")
        bot.pending = {"abc123": bot_pending("31.90", "Pizza")}

        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(bot.pending["abc123"].draft.category, "Food")
        self.assertIn("Reply: confirm all", update.message.replies[0])
        self.assertNotIn("confirm 2 as Food", update.message.replies[0])

        update.message = FakeMessage("Confirm")
        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 1)
        self.assertEqual(sheets.rows[0].amount, Decimal("31.90"))
        self.assertEqual(sheets.rows[0].category, "Food")
        self.assertNotIn("abc123", bot.pending)

    async def test_change_spend_date_updates_latest_logged_expense(self):
        sheets = FakeSheets(records=[record()])
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("change spend date to 21 may 2026")

        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(sheets.updated, [])
        self.assertIn("Change this expense?", update.message.replies[0])
        self.assertIn("After: $30.00 logged as Gifts - 21 May 2026 [4bea7c]", update.message.replies[0])

        update.message = FakeMessage("yes")
        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(sheets.updated[0]["row_number"], 5)
        self.assertEqual(sheets.updated[0]["expense_date"], "2026-05-21")
        self.assertIn("Updated $30.00 logged as Gifts", update.message.replies[0])

    async def test_ai_edit_requires_confirmation_before_updating_sheet(self):
        class AISettings(FakeSettings):
            openai_api_key = "test-key"

        class FakeAI:
            def interpret(self, message, records, today, logged_by):
                return ExpenseIntent(
                    action="edit",
                    updates=[
                        EntryUpdate(entry_id="4bea7c", category="Food"),
                    ],
                )

        sheets = FakeSheets(records=[record()])
        bot = FinanceBot(AISettings(), sheets)
        bot.ai = FakeAI()
        update = FakeUpdate("change the category to food")

        handled = await bot.handle_ai_command(update)

        self.assertTrue(handled)
        self.assertEqual(sheets.updated, [])
        self.assertIn("Change this expense?", update.message.replies[0])
        self.assertIn("After: $30.00 logged as Food", update.message.replies[0])

        update.message = FakeMessage("yes")
        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(sheets.updated[0]["category"], "Food")


def bot_pending(amount: str, description: str, category: str | None = None, batch_id: str | None = None):
    from datetime import datetime

    from getrichbot.bot import PendingExpense

    return PendingExpense(
        draft=ExpenseDraft(
            raw_input=description,
            amount=Decimal(amount),
            category=category,
            description=description,
            confidence=0,
        ),
        logged_by="My wife",
        chat_id=-100,
        message_id=789,
        created_at=datetime(2026, 5, 24, 22, 14, 0),
        reason="category",
        batch_id=batch_id,
    )


if __name__ == "__main__":
    unittest.main()
