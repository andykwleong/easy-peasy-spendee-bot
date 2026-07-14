import unittest
from datetime import date
from decimal import Decimal

from getrichbot.bot import FinanceBot
from getrichbot.ai import EntryUpdate, ExpenseIntent
from getrichbot import categories
from getrichbot.categories import configure_category_config
from getrichbot.models import ExpenseDraft, ExpenseRecord


class FakeSettings:
    raw_expenses_sheet = "Raw Expenses"
    monthly_summary_sheet = "Monthly Summary"
    categories_sheet = "Categories"
    category_keywords_sheet = "Category Keywords"
    me_label = "Me"
    wife_label = "My wife"
    openai_api_key = None
    openai_model = "test-model"

    def label_for_user(self, telegram_user_id):
        if telegram_user_id == 456:
            return "My wife"
        if telegram_user_id == 123:
            return "Me"
        return None


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

    def update_expense_record(self, sheet_name, row_number, amount=None, category=None, description=None, expense_date=None, transaction_type=None):
        self.updated.append(
            {
                "row_number": row_number,
                "amount": amount,
                "category": category,
                "description": description,
                "expense_date": expense_date,
                "transaction_type": transaction_type,
            }
        )

    def update_monthly_summary(self, sheet_name, rows):
        pass

    def get_category_config(self, categories_sheet, keywords_sheet):
        return {
            "source": "google_sheets",
            "categories_sheet_loaded": categories_sheet,
            "keywords_sheet_loaded": keywords_sheet,
            "variable_categories": ["Food", "Gifts", "Shopping - My wife"],
            "fixed_categories": [],
            "category_keywords": {"Food": ["durian"], "Gifts": ["gift", "gifts"], "Shopping - My wife": ["shoes"]},
            "priority_keywords": [],
            "shopping_keywords": [],
            "shopping_categories": {"wife": "Shopping - My wife"},
            "category_aliases": {"food": "Food", "gift": "Gifts", "gifts": "Gifts", "durian": "Food"},
        }


class FakeUser:
    id = 456


class FakeChat:
    id = -100


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.message_id = 789
        self.replies = []
        self.reply_markups = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


class FakeUpdate:
    def __init__(self, text):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser()
        self.effective_chat = FakeChat()


class FakeContext:
    def __init__(self, args=None):
        self.args = args or []


class FakeCallbackMessage(FakeMessage):
    chat_id = -100


class FakeCallbackQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeCallbackMessage("")
        self.answers = []
        self.edited_text = None
        self.reply_markup_removed = False

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_reply_markup(self, reply_markup=None):
        self.reply_markup_removed = reply_markup is None

    async def edit_message_text(self, text):
        self.edited_text = text


class FakeCallbackUpdate:
    def __init__(self, data, user_id=456):
        self.message = None
        self.callback_query = FakeCallbackQuery(data)
        self.effective_user = type("CallbackUser", (), {"id": user_id})()
        self.effective_chat = FakeChat()


class FakeInlineButton:
    def __init__(self, text, callback_data):
        self.text = text
        self.callback_data = callback_data


class FakeInlineMarkup:
    def __init__(self, rows):
        self.inline_keyboard = rows


def fake_income_keyboard(pending_id, income_categories):
    buttons = [
        FakeInlineButton(category, f"income_category|{pending_id}|{index}")
        for index, category in enumerate(income_categories)
    ]
    return FakeInlineMarkup([buttons[index:index + 2] for index in range(0, len(buttons), 2)])


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
    @classmethod
    def setUpClass(cls):
        cls.original_config = {
            "variable_categories": list(categories.VARIABLE_CATEGORIES),
            "fixed_categories": list(categories.FIXED_CATEGORIES),
            "category_keywords": {key: list(value) for key, value in categories.CATEGORY_KEYWORDS.items()},
            "priority_keywords": [
                {"category": category, "keywords": list(keywords)}
                for category, keywords in categories.BILL_PRIORITY_KEYWORDS
            ],
            "shopping_keywords": list(categories.SHOPPING_KEYWORDS),
            "shopping_categories": dict(categories.SHOPPING_CATEGORIES),
            "category_aliases": dict(categories.CATEGORY_ALIASES),
        }

    async def asyncSetUp(self):
        income_categories = ["Income - A", "Income - FX", "Income - Misc"]
        configure_category_config(
            {
                **self.original_config,
                "variable_categories": [
                    *self.original_config["variable_categories"],
                    *[
                        category
                        for category in income_categories
                        if category not in self.original_config["variable_categories"]
                    ],
                ],
            }
        )

    async def asyncTearDown(self):
        configure_category_config(self.original_config)

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

    async def test_date_edit_keeps_matched_entry_for_a_follow_up_date(self):
        class DateClarifyingAI:
            def interpret(self, *args, **kwargs):
                return ExpenseIntent(
                    action="clarify",
                    clarification_question="Which date should I change entry 4bea7c to?",
                )

        settings = FakeSettings()
        settings.openai_api_key = "test-key"
        sheets = FakeSheets(records=[record()])
        bot = FinanceBot(settings, sheets)
        bot.ai = DateClarifyingAI()
        update = FakeUpdate("change date gifts")

        await bot.handle_text(update, FakeContext())

        self.assertIn("Which date should I change", update.message.replies[0])
        self.assertIn((-100, 456), bot.pending_edit_dates)

        update.message = FakeMessage("June 30th")
        await bot.handle_text(update, FakeContext())

        self.assertIn("Change this expense?", update.message.replies[0])
        self.assertIn("30 June 2026", update.message.replies[0])
        self.assertNotIn((-100, 456), bot.pending_edit_dates)
        self.assertEqual(sheets.updated, [])

        update.message = FakeMessage("yes")
        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(sheets.updated[0]["expense_date"], "2026-06-30")

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
        self.assertEqual(update.message.replies[0], "Logging expenses...")
        self.assertIn("Possible duplicate found:", update.message.replies[1])

    async def test_confirm_number_confirms_position_and_cancels_the_rest(self):
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
        self.assertNotIn("first1", bot.pending)
        self.assertEqual(update.message.replies[0], "Logging expenses...")

    async def test_yes_does_not_confirm_pending_batch(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("yes")
        bot.latest_pending_batch[(-100, 456)] = "screenshot"
        bot.pending = {
            "one111": bot_pending("21.01", "Food Panda", "Food", batch_id="screenshot"),
        }

        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(sheets.rows, [])
        self.assertIn("one111", bot.pending)
        self.assertIn("Please reply with confirm all or cancel", update.message.replies[0])

    async def test_cancel_clears_visible_pending_batch(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("cancel")
        bot.latest_pending_batch[(-100, 456)] = "screenshot"
        bot.pending = {
            "one111": bot_pending("21.01", "Food Panda", "Food", batch_id="screenshot", expense_date=date(2026, 7, 7)),
            "two222": bot_pending("43.15", "Cut Butchery", "Groceries", batch_id="screenshot", expense_date=date(2026, 7, 7)),
        }

        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(sheets.rows, [])
        self.assertEqual(bot.pending, {})
        self.assertEqual(update.message.replies[0], "Pending expenses cancelled.")

    async def test_confirm_all_stops_at_first_duplicate_and_keeps_pending_items(self):
        duplicate_record = ExpenseRecord(
            row_number=2,
            entry_id="old123",
            timestamp="16:46:00",
            expense_date="2026-07-07",
            month="2026-07",
            logged_by="My wife",
            raw_input="screenshot: Food Panda",
            amount=Decimal("21.01"),
            category="Food",
            description="Food Panda",
            input_type="Screenshot",
            status="Confirmed",
        )
        sheets = FakeSheets(records=[duplicate_record])
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("confirm all")
        bot.latest_pending_batch[(-100, 456)] = "screenshot"
        bot.pending = {
            "one111": bot_pending("21.01", "Food Panda", "Food", batch_id="screenshot", expense_date=date(2026, 7, 7)),
            "two222": bot_pending("43.15", "Cut Butchery", "Groceries", batch_id="screenshot", expense_date=date(2026, 7, 7)),
        }

        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(sheets.rows, [])
        self.assertIn("one111", bot.pending)
        self.assertIn("two222", bot.pending)
        self.assertEqual(update.message.replies[0], "Logging expenses...")
        self.assertIn("Possible duplicate found:", update.message.replies[1])

    async def test_mixed_confirm_and_change_text_does_not_confirm_selected_items(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("confirm the first entry, change the second one to groceries")
        bot.latest_pending_batch[(-100, 456)] = "screenshot"
        bot.pending = {
            "one111": bot_pending("21.01", "Food Panda", "Food", batch_id="screenshot"),
            "two222": bot_pending("43.15", "Cut Butchery", "Food", batch_id="screenshot"),
        }

        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(sheets.rows, [])
        self.assertIn("one111", bot.pending)
        self.assertIn("two222", bot.pending)
        self.assertEqual(bot.pending["two222"].draft.category, "Groceries")
        self.assertIn("Updated pending entries:", update.message.replies[0])

    async def test_category_reply_updates_unclear_pending_entries(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("gift")
        bot.pending = {"abc123": bot_pending("30", "unknown thing")}

        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 1)
        self.assertEqual(sheets.rows[0].category, "Gifts")
        self.assertNotIn("abc123", bot.pending)

    async def test_category_reply_logs_single_text_pending_item_immediately(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("Food")
        bot.pending = {"abc123": bot_pending("31.90", "Pizza")}

        handled = await bot.handle_pending_update(update)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 1)
        self.assertEqual(sheets.rows[0].amount, Decimal("31.90"))
        self.assertEqual(sheets.rows[0].category, "Food")
        self.assertNotIn("abc123", bot.pending)

    async def test_confirm_command_without_args_confirms_single_pending_item(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("/confirm")
        bot.pending = {"abc123": bot_pending("31.90", "Pizza", "Food")}

        await bot.confirm_command(update, FakeContext())

        self.assertEqual(len(sheets.rows), 1)
        self.assertEqual(sheets.rows[0].category, "Food")
        self.assertNotIn("abc123", bot.pending)

    async def test_refresh_categories_loads_sheet_keywords(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate("/refreshcategories")

        await bot.refresh_categories(update, FakeContext())

        self.assertIn("Categories refreshed from Google Sheets.", update.message.replies[0])
        self.assertEqual(categories.CATEGORY_ALIASES["durian"], "Food")

    async def test_generic_income_shows_buttons_and_selection_logs_immediately(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        bot._income_category_keyboard = fake_income_keyboard
        update = FakeUpdate("income june 15th 2026 15020.33")

        await bot.handle_text(update, FakeContext())

        self.assertEqual(len(bot.pending), 1)
        pending_id, pending = next(iter(bot.pending.items()))
        self.assertEqual(pending.reason, "income_category")
        self.assertEqual(
            pending.category_options,
            ("Income - A", "Income - FX", "Income - Misc"),
        )
        self.assertIn("Which income category?", update.message.replies[0])
        keyboard = update.message.reply_markups[0]
        self.assertEqual(
            [button.text for row in keyboard.inline_keyboard for button in row],
            ["Income - A", "Income - FX", "Income - Misc"],
        )

        callback_update = FakeCallbackUpdate(f"income_category|{pending_id}|0")
        await bot.handle_income_category_callback(callback_update, FakeContext())

        self.assertEqual(len(sheets.rows), 1)
        self.assertEqual(sheets.rows[0].amount, Decimal("15020.33"))
        self.assertEqual(sheets.rows[0].category, "Income - A")
        self.assertEqual(sheets.rows[0].timestamp.date().isoformat(), "2026-06-15")
        self.assertEqual(sheets.rows[0].transaction_type, "Income")
        self.assertNotIn(pending_id, bot.pending)
        self.assertTrue(callback_update.callback_query.reply_markup_removed)
        self.assertIn("Logged income $15020.33 to Income - A", callback_update.callback_query.edited_text)

    async def test_other_user_cannot_choose_pending_income_category(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        bot._income_category_keyboard = fake_income_keyboard
        update = FakeUpdate("income june 15th 2026 15020.33")
        await bot.handle_text(update, FakeContext())
        pending_id = next(iter(bot.pending))

        callback_update = FakeCallbackUpdate(f"income_category|{pending_id}|0", user_id=123)
        await bot.handle_income_category_callback(callback_update, FakeContext())

        self.assertEqual(sheets.rows, [])
        self.assertIn(pending_id, bot.pending)
        self.assertEqual(
            callback_update.callback_query.answers[-1],
            ("Only the person who submitted this income can choose its category.", True),
        )

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


def bot_pending(
    amount: str,
    description: str,
    category: str | None = None,
    batch_id: str | None = None,
    expense_date=None,
):
    from datetime import datetime

    from getrichbot.bot import PendingExpense

    return PendingExpense(
        draft=ExpenseDraft(
            raw_input=description,
            amount=Decimal(amount),
            category=category,
            description=description,
            confidence=0,
            expense_date=expense_date,
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
