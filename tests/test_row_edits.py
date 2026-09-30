import unittest
from decimal import Decimal

from getrichbot.bot import FinanceBot
from getrichbot.cards import parse_payment_config
from getrichbot.categories import configure_category_config
from getrichbot.models import CardUsageRecord, ExpenseRecord
from getrichbot.row_edits import (
    channel_after_card_change,
    find_payment_method,
    format_saved_row,
    parse_finished_amount,
    parse_immediate_edit,
    plan_edit,
)


def payment_config():
    return parse_payment_config(
        [
            ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
            ["Sample Visa", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
            ["Sample Shop Card", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
            ["Sample Mastercard", "Sam", "Credit Card", "Calendar", "1", "TRUE"],
            ["Sample Cash", "Alex", "Cash", "Calendar", "1", "TRUE"],
        ],
        [
            ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
            ["Sample Visa", "Alex", "All", "All", "100", "TRUE"],
            ["Sample Shop Card", "Alex", "All", "Online", "100", "TRUE"],
            ["Sample Shop Card", "Alex", "All", "PayWave", "100", "TRUE"],
            ["Sample Mastercard", "Sam", "All", "PayWave", "200", "TRUE"],
            ["Sample Cash", "Alex", "All", "All", "", "TRUE"],
        ],
    )


def expense(**overrides) -> ExpenseRecord:
    values = dict(
        row_number=4,
        entry_id="aa1001",
        timestamp="12:00:00",
        expense_date="2026-09-20",
        month="2026-09",
        logged_by="Alex",
        raw_input="sample cafe",
        amount=Decimal("18.50"),
        category="Food",
        description="Sample cafe",
        input_type="text",
        status="Confirmed",
        transaction_type="Expense",
        payment_method="Sample Shop Card",
        payment_owner="Alex",
        payment_channel="PayWave",
    )
    values.update(overrides)
    return ExpenseRecord(**values)


class ParseImmediateEditTests(unittest.TestCase):
    def test_category_amount_card_and_channel_phrases(self):
        cases = {
            "change to Travel": ("category", "Travel"),
            "change it to Travel": ("category", "Travel"),
            "change category to Travel": ("category", "Travel"),
            "change the category to Groceries": ("category", "Groceries"),
            "change amount to 23.20": ("amount", "23.20"),
            "change the amount to $18": ("amount", "$18"),
            "change it to 23.20": ("amount", "23.20"),
            "change to 23.20": ("amount", "23.20"),
            "change card to Sample Visa": ("card", "Sample Visa"),
            "change payment method to Sample Cash": ("card", "Sample Cash"),
            "change channel to Online": ("channel", "Online"),
            "change payment channel to PayWave": ("channel", "PayWave"),
        }
        for text, expected in cases.items():
            parsed = parse_immediate_edit(text)
            self.assertIsNotNone(parsed, text)
            self.assertEqual((parsed.field, parsed.value), expected, text)

    def test_other_phrases_stay_on_todays_rules(self):
        for text in [
            "change spend date to 21 May",
            "delete last",
            "change 2 to Food",
            "change the second one to Groceries",
            "confirm",
            "yes",
            "shopping 20th may 23.20",
            "groceries 63 and 15.20",
        ]:
            self.assertIsNone(parse_immediate_edit(text), text)

    def test_finished_amount_does_not_treat_a_partial_number_as_ready(self):
        self.assertEqual(parse_finished_amount("23.20"), Decimal("23.20"))
        self.assertEqual(parse_finished_amount("$18"), Decimal("18.00"))
        self.assertIsNone(parse_finished_amount("12."))
        self.assertIsNone(parse_finished_amount(""))
        self.assertIsNone(parse_finished_amount("0"))


class PlanEditTests(unittest.TestCase):
    def test_payment_owner_follows_the_card_and_one_channel_is_kept(self):
        config = payment_config()
        planned = plan_edit(
            kind="expense",
            field="card",
            value="Sample Mastercard",
            current_method="Sample Shop Card",
            current_owner="Alex",
            current_channel="Online",
            payment_config=config,
        )

        self.assertTrue(planned.ok)
        self.assertEqual(planned.payment_method, "Sample Mastercard")
        self.assertEqual(planned.payment_owner, "Sam")
        self.assertEqual(planned.payment_channel, "PayWave")
        self.assertEqual(find_payment_method(config, "Sample Visa")[1].owner, "Alex")

    def test_two_channels_are_not_guessed(self):
        config = payment_config()
        method = find_payment_method(config, "Sample Shop Card", "Alex")[1]
        self.assertEqual(channel_after_card_change(config, method, "Online"), "Online")
        self.assertEqual(channel_after_card_change(config, method, "PayWave"), "PayWave")
        self.assertEqual(channel_after_card_change(config, method, ""), "")
        self.assertEqual(channel_after_card_change(config, method, "All"), "")

        cleared = plan_edit(
            kind="expense",
            field="card",
            value="Sample Shop Card",
            owner="Alex",
            current_channel="Something else",
            payment_config=config,
        )
        self.assertEqual(cleared.payment_channel, "")
        self.assertEqual(cleared.payment_owner, "Alex")

    def test_a_card_with_no_channel_choice_does_not_invent_one(self):
        config = payment_config()
        planned = plan_edit(
            kind="expense",
            field="card",
            value="Sample Cash",
            payment_config=config,
        )
        self.assertEqual(planned.payment_channel, "")

        rejected = plan_edit(
            kind="expense",
            field="channel",
            value="Online",
            current_method="Sample Cash",
            current_owner="Alex",
            payment_config=config,
        )
        self.assertFalse(rejected.ok)
        self.assertIn("no channel", rejected.message)

    def test_income_has_no_card_and_card_only_has_no_category(self):
        config = payment_config()
        income = plan_edit(kind="income", field="card", value="Sample Visa", payment_config=config)
        self.assertEqual(income.message, "Income has no card.")
        card_only = plan_edit(kind="card_only", field="category", value="Food", payment_config=config)
        self.assertEqual(card_only.message, "Card-only spend has no expense category.")
        fixed = plan_edit(kind="fixed", field="channel", value="Online", payment_config=config)
        self.assertEqual(fixed.message, "Fixed expenses have no card.")

    def test_ambiguous_card_name_is_not_guessed(self):
        config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["Sample Visa", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
                ["Sample Visa", "Sam", "Credit Card", "Calendar", "1", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Limit Amount", "Active"],
                ["Sample Visa", "Alex", "All", "100", "TRUE"],
                ["Sample Visa", "Sam", "All", "100", "TRUE"],
            ],
        )
        planned = plan_edit(kind="expense", field="card", value="Sample Visa", payment_config=config)
        self.assertEqual(planned.error, "ambiguous")
        chosen = plan_edit(kind="expense", field="card", value="Sample Visa", owner="Sam", payment_config=config)
        self.assertEqual(chosen.payment_owner, "Sam")

    def test_category_line_stays_the_same_shape(self):
        self.assertEqual(
            format_saved_row(
                amount=Decimal("40.79"),
                category="Travel",
                expense_date="2026-07-24",
                entry_id="38218a",
                kind="expense",
            ),
            "Updated $40.79 to Travel - 24 July 2026 [38218a]",
        )


class TelegramImmediateEditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original = configure_snapshot()
        configure_category_config(
            {
                "variable_categories": ["Food", "Groceries", "Travel", "Income - Sample Pay"],
                "fixed_categories": ["Sample Bills"],
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

    async def test_amount_and_card_update_the_latest_row_without_a_confirm_screen(self):
        sheets = BotSheets([expense()])
        bot = FinanceBot(BotSettings(), sheets)
        update = FakeUpdate("change amount to 23.20")

        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertEqual(sheets.updated[0]["amount"], Decimal("23.20"))
        self.assertNotIn("yes to update", update.message.replies[0])
        self.assertIn("Updated $23.20 to Food", update.message.replies[0])

        update.message = FakeMessage("change card to Sample Mastercard")
        await bot.handle_plain_language_command(update)

        card_update = sheets.updated[-1]
        self.assertEqual(card_update["payment_method"], "Sample Mastercard")
        self.assertEqual(card_update["payment_owner"], "Sam")
        self.assertEqual(card_update["payment_channel"], "PayWave")
        self.assertNotIn("logged_by", card_update)
        self.assertNotIn("Change this expense?", update.message.replies[0])

    async def test_a_later_card_only_row_can_change_amount_but_not_category(self):
        sheets = BotSheets([expense(timestamp="08:00:00")])
        sheets.usage = [
            CardUsageRecord(
                row_number=2,
                entry_id="cc1001",
                timestamp="18:00:00",
                usage_date="2026-09-21",
                month="2026-09",
                logged_by="Alex",
                raw_input="card only",
                amount=Decimal("12.00"),
                payment_method="Sample Visa",
                description="Sample clinic",
                usage_type="Claimable",
                status="Confirmed",
                payment_owner="Alex",
                payment_channel="",
            )
        ]
        bot = FinanceBot(BotSettings(), sheets)
        update = FakeUpdate("change category to Groceries")

        await bot.handle_plain_language_command(update)

        self.assertEqual(update.message.replies[0], "Card-only spend has no expense category.")
        self.assertEqual(sheets.updated, [])

        update.message = FakeMessage("change amount to 9.50")
        await bot.handle_plain_language_command(update)

        self.assertEqual(sheets.updated[-1]["amount"], Decimal("9.50"))

    async def test_delete_still_asks_before_removing_anything(self):
        sheets = BotSheets([expense()])
        bot = FinanceBot(BotSettings(), sheets)
        update = FakeUpdate("delete last")

        handled = await bot.handle_plain_language_command(update)

        self.assertTrue(handled)
        self.assertIn("yes to delete", update.message.replies[0].lower())
        self.assertEqual(sheets.updated, [])


def configure_snapshot():
    from getrichbot import categories

    return {
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


class BotSettings:
    raw_expenses_sheet = "Raw Expenses"
    monthly_summary_sheet = "Monthly Summary"
    card_usage_sheet = "Card Usage"
    payment_methods_sheet = "Payment Methods"
    card_limits_sheet = "Card Limits"
    categories_sheet = "Categories"
    category_keywords_sheet = "Category Keywords"
    me_label = "Alex"
    wife_label = "Sam"
    openai_api_key = None
    openai_model = "test-model"

    def label_for_user(self, telegram_user_id):
        if telegram_user_id == 456:
            return "Alex"
        return None


class BotSheets:
    def __init__(self, records):
        self.records = records
        self.updated = []
        self.usage = []

    def get_expense_records(self, sheet_name):
        return self.records

    def get_card_usage_records(self, sheet_name):
        return self.usage

    def get_payment_config(self, payment_methods_sheet, card_limits_sheet):
        return payment_config()

    def update_expense_record(self, sheet_name, row_number, **kwargs):
        self.updated.append(kwargs)

    def update_card_usage_record(self, sheet_name, row_number, **kwargs):
        self.updated.append(kwargs)

    def update_monthly_summary(self, sheet_name, rows):
        return None

    def get_last_matching_record(self, sheet_name, logged_by):
        for record in reversed(self.records):
            if record.logged_by == logged_by:
                return record
        return None


class FakeUser:
    id = 456


class FakeChat:
    id = -100


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, text):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser()
        self.effective_chat = FakeChat()


if __name__ == "__main__":
    unittest.main()
