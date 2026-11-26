from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from getrichbot import categories
from getrichbot.bot import FinanceBot
from getrichbot.cards import parse_payment_config
from getrichbot.categories import DEFAULT_CATEGORY_CONFIG, configure_category_config
from getrichbot.dashboard_http import DashboardApp, DashboardContext

TOKEN = "test-bot-token"
NOW = int(time.time())


def sign_webapp(user_id: int) -> str:
    user = json.dumps({"id": user_id, "first_name": "Alex"}, separators=(",", ":"))
    fields = {"auth_date": str(NOW), "user": user}
    data_check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


class Settings:
    raw_expenses_sheet = "Raw Expenses"
    monthly_summary_sheet = "Monthly Summary"
    payment_methods_sheet = "Payment Methods"
    card_limits_sheet = "Card Limits"
    card_usage_sheet = "Card Usage"
    me_label = "Alex"
    wife_label = "Sam"
    openai_api_key = None
    openai_model = "test-model"
    telegram_chat_id = -100

    def label_for_user(self, user_id):
        if user_id == 111:
            return "Alex"
        if user_id == 222:
            return "Sam"
        return None


class Sheets:
    def __init__(self):
        self.rows = []
        self.summary = None
        self.config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["Sample Visa", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
                ["Sample Visa", "Alex", "All", "All", "100", "TRUE"],
            ],
        )

    def get_payment_config(self, payment_methods_sheet, card_limits_sheet):
        return self.config

    def get_expense_records(self, sheet_name, timeout_seconds=None):
        return []

    def append_expense(self, sheet_name, row):
        self.rows.append(row)

    def update_monthly_summary(self, sheet_name, rows, timeout_seconds=None):
        self.summary = rows


def labels(user_id: int):
    return Settings().label_for_user(user_id)


def app_for(sheets: Sheets | None = None) -> tuple[DashboardApp, Sheets, FinanceBot]:
    sheets = sheets or Sheets()
    bot = FinanceBot(Settings(), sheets)
    context = DashboardContext(
        bot_token=TOKEN,
        allowed_user_ids={111, 222},
        label_for_user=labels,
        raw_sheet="Raw Expenses",
        card_usage_sheet="Card Usage",
        payment_methods_sheet="Payment Methods",
        card_limits_sheet="Card Limits",
        sheets=sheets,
        today=lambda: date(2026, 9, 26),
    )
    app = DashboardApp(context)
    app.attach_expense_bot(bot)
    return app, sheets, bot


def post(app: DashboardApp, user_id: int | None, payload: dict):
    headers = {}
    if user_id is not None:
        headers["X-Telegram-Init-Data"] = sign_webapp(user_id)
    status, _, body = app.handle(
        "POST",
        "/api/dashboard/expense",
        headers,
        json.dumps(payload).encode(),
        secure=False,
    )
    return status, json.loads(body.decode())


def _category_snapshot() -> dict:
    return {
        "source": categories.CONFIG_SOURCE,
        "variable_categories": list(categories.VARIABLE_CATEGORIES),
        "fixed_categories": list(categories.FIXED_CATEGORIES),
        "category_keywords": {name: list(words) for name, words in categories.CATEGORY_KEYWORDS.items()},
        "priority_keywords": [
            {"category": name, "keywords": list(words)} for name, words in categories.BILL_PRIORITY_KEYWORDS
        ],
        "shopping_keywords": list(categories.SHOPPING_KEYWORDS),
        "shopping_categories": dict(categories.SHOPPING_CATEGORIES),
        "category_aliases": dict(categories.CATEGORY_ALIASES),
    }


class DashboardExpenseTests(unittest.TestCase):
    def setUp(self):
        self._categories = _category_snapshot()
        configure_category_config({**DEFAULT_CATEGORY_CONFIG, "source": "test"})

    def tearDown(self):
        configure_category_config(self._categories)

    def test_text_asks_for_payment_and_the_button_writes_the_row(self):
        app, sheets, _bot = app_for()

        status, body = post(app, 111, {"text": "lunch 23.40"})

        self.assertEqual(status, 200)
        self.assertEqual(sheets.rows, [])
        self.assertIn("Which payment method?", body["replies"][0]["text"])
        buttons = body["replies"][0]["buttons"]
        self.assertEqual([button["label"] for button in buttons], ["Sample Visa"])

        chosen = post(app, 111, {"callback": buttons[0]["callback"]})

        self.assertEqual(chosen[0], 200)
        self.assertEqual(len(sheets.rows), 1)
        row = sheets.rows[0]
        self.assertEqual(row.amount, Decimal("23.40"))
        self.assertEqual(row.category, "Food")
        self.assertEqual(row.description, "lunch")
        self.assertEqual(row.logged_by, "Alex")
        self.assertEqual(row.payment_method, "Sample Visa")
        self.assertEqual(row.payment_owner, "Alex")
        self.assertEqual(row.telegram_chat_id, 111)
        self.assertNotEqual(row.telegram_chat_id, Settings.telegram_chat_id)
        self.assertIn("Logged $23.40", chosen[1]["replies"][0]["text"])

    def test_someone_else_cannot_choose_the_payment_method(self):
        app, sheets, _bot = app_for()
        _status, body = post(app, 111, {"text": "lunch 23.40"})
        callback = body["replies"][0]["buttons"][0]["callback"]

        status, reply = post(app, 222, {"callback": callback})

        self.assertEqual(status, 200)
        self.assertEqual(sheets.rows, [])
        self.assertIn("Only the person who submitted", reply["replies"][0]["text"])

    def test_a_locked_visit_does_not_write(self):
        app, sheets, _bot = app_for()

        status, body = post(app, None, {"text": "lunch 23.40"})

        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "locked")
        self.assertEqual(sheets.rows, [])

    def test_a_command_stays_out_of_the_expense_handler(self):
        app, sheets, _bot = app_for()

        status, body = post(app, 111, {"text": "/undo"})

        self.assertEqual(status, 200)
        self.assertIn("Send commands in Telegram", body["replies"][0]["text"])
        self.assertEqual(sheets.rows, [])

    def test_income_category_button_uses_the_same_handler(self):
        app, sheets, _bot = app_for()

        status, body = post(app, 111, {"text": "income 100"})

        self.assertEqual(status, 200)
        self.assertEqual(sheets.rows, [])
        labels = [button["label"] for button in body["replies"][0]["buttons"]]
        self.assertIn("Income - Misc", labels)
        chosen = next(button for button in body["replies"][0]["buttons"] if button["label"] == "Income - Misc")

        status, reply = post(app, 111, {"callback": chosen["callback"]})

        self.assertEqual(status, 200)
        self.assertEqual(len(sheets.rows), 1)
        self.assertEqual(sheets.rows[0].category, "Income - Misc")
        self.assertEqual(sheets.rows[0].transaction_type, "Income")
        self.assertEqual(sheets.rows[0].payment_method, "")
        self.assertIn("Logged income", reply["replies"][0]["text"])

    def test_the_page_offers_the_expense_box(self):
        app, _sheets, _bot = app_for()

        status, _, payload = app.handle("GET", "/dashboard.js", {}, b"", secure=False)

        self.assertEqual(status, 200)
        script = payload.decode()
        self.assertIn('aria-label", "Expense text"', script)
        self.assertIn('aria-label", "Send expense"', script)
        self.assertIn("Add an expense", script)
