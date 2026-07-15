from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal

from getrichbot.bot import FinanceBot, PendingExpense
from getrichbot.cards import parse_payment_config
from getrichbot.models import ExpenseDraft


class Settings:
    raw_expenses_sheet = "Raw Expenses"
    monthly_summary_sheet = "Monthly Summary"
    payment_methods_sheet = "Payment Methods"
    card_limits_sheet = "Card Limits"
    me_label = "Me"
    wife_label = "My wife"
    openai_api_key = None
    openai_model = "test-model"

    def label_for_user(self, telegram_user_id):
        return "Me" if telegram_user_id == 123 else "My wife" if telegram_user_id == 456 else None


class Sheets:
    def __init__(self):
        self.rows = []
        self.config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["Citi Rewards", "Me", "Credit Card", "Calendar", "1", "TRUE"],
                ["Cash", "Me", "Cash", "Calendar", "1", "TRUE"],
                ["Wife Card", "My wife", "Credit Card", "Calendar", "1", "TRUE"],
            ],
            [["Payment Method", "Owner", "Category", "Limit Amount", "Active"]],
        )

    def get_payment_config(self, payment_methods_sheet, card_limits_sheet):
        return self.config

    def append_expense(self, sheet_name, row):
        self.rows.append(row)

    def get_expense_records(self, sheet_name):
        return []

    def update_monthly_summary(self, sheet_name, rows):
        pass


class Message:
    def __init__(self, text):
        self.text = text
        self.message_id = 55
        self.replies = []
        self.markups = []
        self.reply_kwargs = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        self.reply_kwargs.append(kwargs)


class Update:
    def __init__(self, text="food 20", user_id=123):
        self.message = Message(text)
        self.effective_user = type("User", (), {"id": user_id})()
        self.effective_chat = type("Chat", (), {"id": -100})()
        self.callback_query = None


class CallbackQuery:
    def __init__(self, data):
        self.data = data
        self.message = Message("")
        self.message.chat_id = -100
        self.edited_text = None

    async def answer(self, *args, **kwargs):
        pass

    async def edit_message_reply_markup(self, **kwargs):
        pass

    async def edit_message_text(self, text):
        self.edited_text = text


class CallbackUpdate(Update):
    def __init__(self, data):
        super().__init__(user_id=123)
        self.message = None
        self.callback_query = CallbackQuery(data)


class FakeButton:
    def __init__(self, text, callback_data):
        self.text = text
        self.callback_data = callback_data


class FakeMarkup:
    def __init__(self, rows):
        self.inline_keyboard = rows


class TestFinanceBot(FinanceBot):
    def _payment_method_keyboard(self, pending_id, options):
        buttons = [
            FakeButton(name, callback_data=f"payment_method|{pending_id}|{index}")
            for index, name in enumerate(options)
        ]
        return FakeMarkup([buttons])


class TestPaymentFlow(unittest.IsolatedAsyncioTestCase):
    async def test_expense_waits_for_the_sender_payment_button(self):
        sheets = Sheets()
        bot = TestFinanceBot(Settings(), sheets)
        update = Update()

        await bot.handle_text(update, None)

        self.assertEqual(sheets.rows, [])
        self.assertEqual(len(bot.pending), 1)
        self.assertIn("Which payment method?", update.message.replies[0])
        button_names = [button.text for row in update.message.markups[0].inline_keyboard for button in row]
        self.assertEqual(button_names, ["Citi Rewards", "Cash"])

        callback_data = update.message.markups[0].inline_keyboard[0][0].callback_data
        callback_update = CallbackUpdate(callback_data)
        await bot.handle_payment_method_callback(callback_update, None)

        self.assertEqual(len(sheets.rows), 1)
        self.assertEqual(sheets.rows[0].payment_method, "Citi Rewards")
        self.assertEqual(callback_update.callback_query.message.replies, [])
        self.assertIn("via Citi Rewards", callback_update.callback_query.edited_text)

    async def test_card_summary_is_sent_without_a_quote(self):
        sheets = Sheets()
        bot = TestFinanceBot(Settings(), sheets)
        update = Update("card summary")

        await bot._reply_with_card_summary(update)

        self.assertEqual(update.message.replies[0], "Card summary\n\nUncapped:\nCiti Rewards - $0.00")
        self.assertFalse(update.message.reply_kwargs[0]["do_quote"])

    async def test_confirmed_screenshot_batch_asks_for_cards_before_logging(self):
        sheets = Sheets()
        bot = TestFinanceBot(Settings(), sheets)
        update = Update("confirm all")
        bot.latest_pending_batch[(-100, 123)] = "photo01"
        bot.pending = {
            "first01": PendingExpense(
                ExpenseDraft("photo", Decimal("20"), "Food", "dinner", 0.95),
                "Me", -100, 55, datetime.now(), "screenshot", "photo01", input_type="Screenshot",
            ),
            "second1": PendingExpense(
                ExpenseDraft("photo", Decimal("30"), "Groceries", "ntuc", 0.95),
                "Me", -100, 55, datetime.now(), "screenshot", "photo01", input_type="Screenshot",
            ),
        }

        await bot.handle_pending_update(update)

        self.assertEqual(sheets.rows, [])
        self.assertEqual(len(bot.pending_payment_batches[(-100, 123)].pending_ids), 2)
        self.assertIn("Payment method for 1 of 2", update.message.replies[0])
