import unittest
from datetime import date, datetime
from decimal import Decimal

from getrichbot.bot import SINGAPORE_TZ, FinanceBot
from getrichbot.cards import parse_payment_config
from getrichbot.models import CardUsageRow, ExpenseRecord, ExpenseRow


class ReminderSettings:
    telegram_chat_id = -100
    bot_state_sheet = "Bot State"
    raw_expenses_sheet = "Raw Expenses"
    fixed_expenses_sheet = "Fixed Expenses"
    monthly_summary_sheet = "Monthly Summary"
    card_usage_sheet = "Card Usage"
    payment_methods_sheet = "Payment Methods"
    card_limits_sheet = "Card Limits"
    me_label = "Me"
    wife_label = "My wife"
    me_telegram_ids = {111}
    wife_telegram_ids = {222}

    def label_for_user(self, telegram_user_id):
        if telegram_user_id == 111:
            return self.me_label
        if telegram_user_id == 222:
            return self.wife_label
        return None


class ReminderSheets:
    def __init__(self, config):
        self.config = config
        self.records = []
        self.usage = []
        self.state = {}
        self.fixed = []
        self.summary_updates = []

    def get_payment_config(self, methods_sheet, limits_sheet):
        if isinstance(self.config, Exception):
            raise self.config
        return self.config

    def get_expense_records(self, sheet_name):
        return list(self.records)

    def get_card_usage_records(self, sheet_name):
        return list(self.usage)

    def append_expense(self, sheet_name, row):
        self.records.append(_saved_expense(row))

    def update_monthly_summary(self, sheet_name, rows):
        self.summary_updates.append(rows)

    def get_fixed_expenses(self, sheet_name):
        return self.fixed

    def get_state_value(self, sheet_name, key):
        return self.state.get(key)

    def set_state_value(self, sheet_name, key, value):
        if value == "":
            self.state.pop(key, None)
            return
        self.state[key] = value


class RecordingBot:
    def __init__(self, fail_times=0):
        self.messages = []
        self.fail_times = fail_times

    async def send_message(self, chat_id, text, parse_mode=None):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("telegram down")
        self.messages.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})


class FakeUser:
    id = 111


class FakeChat:
    id = -100


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, do_quote=False):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, text=""):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser()
        self.effective_chat = FakeChat()


def at_singapore(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 5, tzinfo=SINGAPORE_TZ)


def expense_row(entry_id: str, amount: str, method: str = "Sample Visa", owner: str = "Me", category: str = "Food") -> ExpenseRow:
    return ExpenseRow(
        entry_id=entry_id,
        timestamp=datetime(2026, 7, 10, 12, 0, tzinfo=SINGAPORE_TZ),
        logged_by=owner,
        raw_input="sample",
        amount=Decimal(amount),
        category=category,
        description="Sample shop",
        input_type="Text",
        status="Confirmed",
        telegram_chat_id=-100,
        telegram_message_id=1,
        payment_method=method,
        payment_owner=owner,
    )


def capped_config(*limit_rows: list[str]):
    methods = [
        ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
        ["Sample Visa", "Me", "Credit Card", "Calendar", "1", "TRUE"],
        ["Sample Card", "My wife", "Credit Card", "Calendar", "1", "TRUE"],
        ["Sample Cash", "Me", "Cash", "Calendar", "1", "TRUE"],
    ]
    limits = [["Payment Method", "Owner", "Category", "Limit Amount", "Active"], *limit_rows]
    return parse_payment_config(methods, limits)


def _saved_expense(row: ExpenseRow):
    return ExpenseRecord(
        row_number=len([]) + 2,
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
        payment_method=row.payment_method,
        payment_owner=row.payment_owner,
        payment_channel=row.payment_channel,
    )


class TestCardUseWarningSend(unittest.IsolatedAsyncioTestCase):
    def _ready(self, config):
        sheets = ReminderSheets(config)
        settings = ReminderSettings()
        finance = FinanceBot(settings, sheets)
        telegram = RecordingBot()
        finance.household_bot = telegram
        return finance, sheets, telegram

    async def test_the_purchase_that_reaches_ninety_sends_one_tagged_note(self):
        finance, sheets, telegram = self._ready(capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]))

        await finance._append_or_hold_duplicate(FakeUpdate(), expense_row("use001", "90"))
        await finance._send_card_use_warning(expense_row("use001", "90"))
        restarted = FinanceBot(finance.settings, sheets)
        restarted.household_bot = telegram
        await restarted._send_card_use_warning(expense_row("use001", "90"))

        self.assertEqual(
            telegram.messages,
            [
                {
                    "chat_id": -100,
                    "parse_mode": "HTML",
                    "text": '<a href="tg://user?id=111">Me</a> Sample Visa is at 90% of its $100.00 limit. $90.00 is used in this cycle.',
                }
            ],
        )
        self.assertEqual(sheets.state["card_limit_note:use001"], "yes")
        self.assertEqual(sheets.state["card_limit_90_opened:2026-07-01:Me:Sample Visa"], "yes")

    async def test_later_uses_repeat_until_the_limit_and_then_keep_warning(self):
        finance, sheets, telegram = self._ready(capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]))

        await finance._append_or_hold_duplicate(FakeUpdate(), expense_row("use001", "90"))
        await finance._append_or_hold_duplicate(FakeUpdate(), expense_row("use002", "5", category="Transport"))
        await finance._append_or_hold_duplicate(FakeUpdate(), expense_row("use003", "6", category="Shopping"))
        await finance._append_or_hold_duplicate(FakeUpdate(), expense_row("use004", "7", category="Gifts"))

        self.assertEqual(
            [message["text"] for message in telegram.messages],
            [
                '<a href="tg://user?id=111">Me</a> Sample Visa is at 90% of its $100.00 limit. $90.00 is used in this cycle.',
                '<a href="tg://user?id=111">Me</a> Sample Visa is at 95% of its limit. $95.00 of $100.00 is used in this cycle.',
                '<a href="tg://user?id=111">Me</a> Sample Visa has reached its limit. Use another card for your next purchase.',
                '<a href="tg://user?id=111">Me</a> Sample Visa has reached its limit. Use another card for your next purchase.',
            ],
        )

    async def test_a_purchase_that_lands_on_one_hundred_skips_the_ninety_note(self):
        finance, sheets, telegram = self._ready(capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]))

        await finance._append_or_hold_duplicate(FakeUpdate(), expense_row("use001", "100"))

        self.assertEqual(len(telegram.messages), 1)
        self.assertIn("has reached its limit", telegram.messages[0]["text"])
        self.assertNotIn("90%", telegram.messages[0]["text"])
        self.assertNotIn("card_limit_90_opened:2026-07-01:Me:Sample Visa", sheets.state)

    async def test_under_ninety_uncapped_blank_and_cash_stay_quiet(self):
        under, under_sheets, under_bot = self._ready(capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]))
        await under._append_or_hold_duplicate(FakeUpdate(), expense_row("use001", "89.99"))
        self.assertEqual(under_bot.messages, [])
        self.assertEqual(under_sheets.state, {})

        uncapped, _, uncapped_bot = self._ready(capped_config())
        await uncapped._append_or_hold_duplicate(FakeUpdate(), expense_row("use002", "500"))
        self.assertEqual(uncapped_bot.messages, [])

        blank, _, blank_bot = self._ready(
            parse_payment_config(
                [
                    ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                    ["Sample Visa", "Me", "Credit Card", "Calendar", "1", "TRUE"],
                ],
                [
                    ["Payment Method", "Owner", "Category", "Limit Amount", "Active"],
                    ["Sample Visa", "Me", "All", "", "TRUE"],
                ],
            )
        )
        await blank._append_or_hold_duplicate(FakeUpdate(), expense_row("use003", "500"))
        self.assertEqual(blank_bot.messages, [])

        cash, _, cash_bot = self._ready(capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]))
        await cash._append_or_hold_duplicate(FakeUpdate(), expense_row("use004", "90", method="Sample Cash"))
        self.assertEqual(cash_bot.messages, [])

    async def test_the_nine_am_check_does_not_send_a_card_warning(self):
        finance, sheets, telegram = self._ready(capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]))
        sheets.records.append(_saved_expense(expense_row("use001", "95")))

        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 7, 10), 9))

        self.assertEqual(telegram.messages, [])
        self.assertEqual(sheets.state, {})

    async def test_card_summary_does_not_send_the_warning(self):
        finance, sheets, telegram = self._ready(capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]))
        sheets.records.append(_saved_expense(expense_row("use001", "95")))

        update = FakeUpdate("card summary")
        await finance._reply_with_card_summary(update)

        self.assertEqual(telegram.messages, [])
        self.assertIn("Sample Visa", update.message.replies[0])

    async def test_a_failed_send_can_be_retried_once(self):
        finance, sheets, telegram = self._ready(capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]))
        telegram.fail_times = 1
        row = expense_row("use001", "90")
        sheets.records.append(_saved_expense(row))

        await finance._send_card_use_warning(row)
        self.assertEqual(telegram.messages, [])
        self.assertEqual(sheets.state, {})

        await finance._send_card_use_warning(row)
        self.assertEqual(len(telegram.messages), 1)
        self.assertEqual(sheets.state["card_limit_note:use001"], "yes")

    async def test_the_owner_tag_uses_the_configured_ids(self):
        finance, _, telegram = self._ready(capped_config(["Sample Card", "My wife", "All", "200", "TRUE"]))
        finance.settings.me_telegram_ids = {333, 111}

        await finance._append_or_hold_duplicate(
            FakeUpdate(),
            expense_row("use001", "180", method="Sample Card", owner="My wife"),
        )

        self.assertIn('<a href="tg://user?id=222">My wife</a> Sample Card is at 90% of its $200.00 limit.', telegram.messages[0]["text"])

    async def test_card_only_spend_can_send_the_warning(self):
        finance, _, telegram = self._ready(capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]))
        row = CardUsageRow(
            entry_id="claim1",
            timestamp=datetime(2026, 7, 10, 12, 0, tzinfo=SINGAPORE_TZ),
            logged_by="Me",
            raw_input="sample claim",
            amount=Decimal("90"),
            payment_method="Sample Visa",
            description="Sample clinic",
            usage_type="Claimable",
            status="Confirmed",
            telegram_chat_id=-100,
            telegram_message_id=1,
            payment_owner="Me",
        )

        await finance._send_card_use_warning(row)

        self.assertIn("$90.00 is used in this cycle.", telegram.messages[0]["text"])

    async def test_month_end_still_sends_only_the_fixed_reminder(self):
        finance, sheets, telegram = self._ready(capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]))
        sheets.records.append(_saved_expense(expense_row("use001", "95")))

        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 5, 31), 9))

        self.assertEqual(len(telegram.messages), 1)
        self.assertIn("No active fixed expenses", telegram.messages[0]["text"])
        self.assertNotIn("tg://user", telegram.messages[0]["text"])
