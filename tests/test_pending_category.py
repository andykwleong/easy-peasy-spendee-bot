from __future__ import annotations

import json
import unittest
from decimal import Decimal

from getrichbot import categories
from getrichbot.bot import FinanceBot
from getrichbot.categories import configure_category_config
from getrichbot.email_mail import MIXED_SHOP_ASK
from getrichbot.models import ExpenseRecord
from getrichbot.shop_category import shop_choice_state_key


class PendingCategoryReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original = {
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
            "source": "test",
        }
        configure_category_config(
            {
                "variable_categories": ["Food", "Groceries"],
                "fixed_categories": [],
                "category_keywords": {},
                "priority_keywords": [],
                "shopping_keywords": [],
                "shopping_categories": {},
                "category_aliases": {},
                "source": "test",
            }
        )

    def tearDown(self):
        configure_category_config(self.original)

    async def test_reply_saves_an_offered_category_and_does_not_pin_the_shop(self):
        record = expense_row()
        sheets = ReplySheets(record)
        key = shop_choice_state_key(record.entry_id)
        sheets.state[key] = json.dumps(["Food", "Groceries"])
        bot = FinanceBot(ReplySettings(), sheets)
        notice = (
            "Logged $9.99 at Sample stall - 5 October 2026 via Sample Visa (PayWave) [abc123]\n"
            + MIXED_SHOP_ASK
        )

        update = ReplyUpdate("Groceries", user_id=456, reply_text=notice)
        await bot.handle_text(update, None)

        self.assertEqual(sheets.updates, [{"category": "Groceries", "transaction_type": "Expense"}])
        self.assertEqual(sheets.keywords, [])
        self.assertEqual(sheets.state[key], "")
        self.assertIn("Groceries", update.message.replies[0])
        self.assertIn("abc123", update.message.replies[0])

        sheets.state[key] = json.dumps(["Food", "Groceries"])
        other = ReplyUpdate("Food", user_id=789, reply_text=notice)
        await bot.handle_text(other, None)

        self.assertEqual(sheets.updates, [{"category": "Groceries", "transaction_type": "Expense"}])
        self.assertEqual(sheets.keywords, [])
        self.assertIn("Only the person who submitted this mail", other.message.replies[0])


def expense_row() -> ExpenseRecord:
    return ExpenseRecord(
        row_number=4,
        entry_id="abc123",
        timestamp="09:00:00",
        expense_date="2026-10-05",
        month="2026-10",
        logged_by="Alex",
        raw_input="email: Sample stall",
        amount=Decimal("9.99"),
        category="",
        description="Sample stall",
        input_type="Email",
        status="Confirmed",
        transaction_type="Expense",
        payment_method="Sample Visa",
        payment_owner="Alex",
        payment_channel="PayWave",
    )


class ReplySettings:
    raw_expenses_sheet = "Raw Expenses"
    monthly_summary_sheet = "Monthly Summary"
    bot_state_sheet = "Bot State"
    card_usage_sheet = "Card Usage"
    me_label = "Alex"
    wife_label = "Sam"
    openai_api_key = None

    def label_for_user(self, telegram_user_id):
        if telegram_user_id == 456:
            return "Alex"
        if telegram_user_id == 789:
            return "Sam"
        return None


class ReplySheets:
    def __init__(self, record):
        self.record = record
        self.updates = []
        self.keywords = []
        self.state = {}
        self.summaries = []

    def get_state_value(self, sheet_name, key):
        del sheet_name
        return self.state.get(key, "")

    def set_state_value(self, sheet_name, key, value):
        del sheet_name
        self.state[key] = value

    def get_record_by_id(self, sheet_name, entry_id, logged_by=None):
        del sheet_name
        if self.record.entry_id.lower() != entry_id.lower():
            return None
        if logged_by is not None and self.record.logged_by != logged_by:
            return None
        return self.record

    def get_expense_records(self, sheet_name):
        del sheet_name
        return [self.record]

    def update_expense_record(self, sheet_name, row_number, **updates):
        del sheet_name, row_number
        self.updates.append(updates)

    def update_monthly_summary(self, sheet_name, rows):
        self.summaries.append(sheet_name)
        del rows

    def upsert_category_keyword(self, sheet_name, keyword, category):
        self.keywords.append((sheet_name, keyword, category))

    def deactivate_category_keyword(self, sheet_name, keyword):
        self.keywords.append((sheet_name, keyword, "off"))


class FakeReply:
    def __init__(self, text):
        self.text = text
        self.message_id = 77


class FakeMessage:
    def __init__(self, text, reply_text):
        self.text = text
        self.reply_to_message = FakeReply(reply_text) if reply_text else None
        self.replies = []

    async def reply_text(self, text, **kwargs):
        del kwargs
        self.replies.append(text)


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeChat:
    id = -100


class ReplyUpdate:
    def __init__(self, text, user_id, reply_text):
        self.message = FakeMessage(text, reply_text)
        self.effective_user = FakeUser(user_id)
        self.effective_chat = FakeChat()


if __name__ == "__main__":
    unittest.main()
