import unittest
from datetime import date, datetime
from decimal import Decimal

from getrichbot.bot import SINGAPORE_TZ, FinanceBot
from getrichbot.cards import parse_payment_config
from getrichbot.models import CardUsageRecord, ExpenseRecord


class ReminderSettings:
    telegram_chat_id = -100
    bot_state_sheet = "Bot State"
    raw_expenses_sheet = "Raw Expenses"
    fixed_expenses_sheet = "Fixed Expenses"
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
    def __init__(self, config, records=None, usage=None):
        self.config = config
        self.records = records or []
        self.usage = usage or []
        self.state = {}
        self.fixed = []

    def get_payment_config(self, methods_sheet, limits_sheet):
        if isinstance(self.config, Exception):
            raise self.config
        return self.config

    def get_expense_records(self, sheet_name):
        return self.records

    def get_card_usage_records(self, sheet_name):
        return self.usage

    def get_fixed_expenses(self, sheet_name):
        return self.fixed

    def get_state_value(self, sheet_name, key):
        return self.state.get(key)

    def set_state_value(self, sheet_name, key, value):
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
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, do_quote=False):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self):
        self.message = FakeMessage()
        self.effective_user = FakeUser()
        self.effective_chat = FakeChat()


def at_singapore(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 5, tzinfo=SINGAPORE_TZ)


def expense(amount: str, method: str = "Sample Visa", logged_by: str = "Me", expense_date: str = "2026-07-10") -> ExpenseRecord:
    return ExpenseRecord(
        row_number=2,
        entry_id="abc123",
        timestamp="12:00:00",
        expense_date=expense_date,
        month=expense_date[:7],
        logged_by=logged_by,
        raw_input="sample",
        amount=Decimal(amount),
        category="Food",
        description="Sample shop",
        input_type="Text",
        status="Confirmed",
        payment_method=method,
        payment_owner=logged_by,
    )


def capped_config(*limit_rows: list[str]):
    methods = [
        ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
        ["Sample Visa", "Me", "Credit Card", "Calendar", "1", "TRUE"],
        ["Sample Card", "My wife", "Credit Card", "Calendar", "1", "TRUE"],
    ]
    limits = [["Payment Method", "Owner", "Category", "Limit Amount", "Active"], *limit_rows]
    return parse_payment_config(methods, limits)


class TestCardLimitReminderJob(unittest.IsolatedAsyncioTestCase):
    def _bot(self, sheets, telegram=None, settings=None):
        return FinanceBot(settings or ReminderSettings(), sheets), telegram or RecordingBot()

    async def test_nine_am_tags_the_owner_once_per_cycle(self):
        sheets = ReminderSheets(
            capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]),
            [expense("90")],
        )
        finance, telegram = self._bot(sheets)
        today = date(2026, 7, 10)

        await finance._run_monthly_checks(telegram, at_singapore(today, 9))
        await finance._run_monthly_checks(telegram, at_singapore(today, 9))
        restarted = FinanceBot(ReminderSettings(), sheets)
        await restarted._run_monthly_checks(telegram, at_singapore(date(2026, 7, 11), 9))

        self.assertEqual(len(telegram.messages), 1)
        message = telegram.messages[0]
        self.assertEqual(message["chat_id"], -100)
        self.assertEqual(message["parse_mode"], "HTML")
        self.assertEqual(
            message["text"],
            '<a href="tg://user?id=111">Me</a> Sample Visa is at 90% of its limit. $90.00 of $100.00 is used this cycle.',
        )
        self.assertEqual(sheets.state["card_limit_90:2026-07-01:Me:Sample Visa"], "yes")
        self.assertEqual(sheets.state["card_limit_90_checked:2026-07-10"], "yes")
        self.assertEqual(sheets.state["card_limit_90_checked:2026-07-11"], "yes")

    async def test_next_cycle_sends_again(self):
        sheets = ReminderSheets(
            capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]),
            [expense("90"), expense("90", expense_date="2026-08-02")],
        )
        finance, telegram = self._bot(sheets)
        sheets.state["card_limit_90:2026-07-01:Me:Sample Visa"] = "yes"

        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 8, 2), 9))

        self.assertEqual(len(telegram.messages), 1)
        self.assertEqual(sheets.state["card_limit_90:2026-08-01:Me:Sample Visa"], "yes")

    async def test_under_ninety_uncapped_and_blank_limits_stay_quiet(self):
        under = ReminderSheets(
            capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]),
            [expense("89.99")],
        )
        uncapped = ReminderSheets(capped_config(), [expense("500")])
        blank = ReminderSheets(
            parse_payment_config(
                [
                    ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                    ["Sample Visa", "Me", "Credit Card", "Calendar", "1", "TRUE"],
                ],
                [
                    ["Payment Method", "Owner", "Category", "Limit Amount", "Active"],
                    ["Sample Visa", "Me", "All", "", "TRUE"],
                ],
            ),
            [expense("500")],
        )
        for sheets in (under, uncapped, blank):
            finance, telegram = self._bot(sheets)
            await finance._run_monthly_checks(telegram, at_singapore(date(2026, 7, 10), 9))
            self.assertEqual(telegram.messages, [])
            self.assertEqual(sheets.state, {"card_limit_90_checked:2026-07-10": "yes"})

    async def test_outside_the_nine_am_hour_or_without_a_chat_does_not_send(self):
        sheets = ReminderSheets(
            capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]),
            [expense("95")],
        )
        finance, telegram = self._bot(sheets)
        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 7, 10), 8))
        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 7, 10), 10))
        finance.settings.telegram_chat_id = None
        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 7, 10), 9))

        self.assertEqual(telegram.messages, [])
        self.assertEqual(sheets.state, {})

    async def test_each_owner_is_tagged_for_their_own_card(self):
        sheets = ReminderSheets(
            capped_config(
                ["Sample Visa", "Me", "All", "100", "TRUE"],
                ["Sample Card", "My wife", "All", "200", "TRUE"],
            ),
            [expense("90"), expense("180", method="Sample Card", logged_by="My wife")],
        )
        finance, telegram = self._bot(sheets)

        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 7, 10), 9))

        self.assertEqual(
            [message["text"] for message in telegram.messages],
            [
                '<a href="tg://user?id=111">Me</a> Sample Visa is at 90% of its limit. $90.00 of $100.00 is used this cycle.',
                '<a href="tg://user?id=222">My wife</a> Sample Card is at 90% of its limit. $180.00 of $200.00 is used this cycle.',
            ],
        )

    async def test_every_configured_id_for_that_owner_is_tagged(self):
        settings = ReminderSettings()
        settings.me_telegram_ids = {333, 111}
        sheets = ReminderSheets(
            capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]),
            [expense("90")],
        )
        finance, telegram = self._bot(sheets, settings=settings)

        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 7, 10), 9))

        self.assertIn(
            '<a href="tg://user?id=111">Me</a> <a href="tg://user?id=333">Me</a> ',
            telegram.messages[0]["text"],
        )

    async def test_a_missing_telegram_id_still_names_the_owner(self):
        settings = ReminderSettings()
        settings.me_telegram_ids = set()
        settings.me_label = "A & B"
        sheets = ReminderSheets(
            parse_payment_config(
                [
                    ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                    ["Sample Visa", "A & B", "Credit Card", "Calendar", "1", "TRUE"],
                ],
                [
                    ["Payment Method", "Owner", "Category", "Limit Amount", "Active"],
                    ["Sample Visa", "A & B", "All", "100", "TRUE"],
                ],
            ),
            [expense("90", logged_by="A & B")],
        )
        finance, telegram = self._bot(sheets, settings=settings)

        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 7, 10), 9))

        self.assertEqual(
            telegram.messages[0]["text"],
            "A &amp; B Sample Visa is at 90% of its limit. $90.00 of $100.00 is used this cycle.",
        )

    async def test_two_limits_at_ninety_send_one_message(self):
        config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["Sample Visa", "Me", "Credit Card", "Calendar", "1", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
                ["Sample Visa", "Me", "Food", "All", "100", "TRUE"],
                ["Sample Visa", "Me", "All", "Online", "100", "TRUE"],
            ],
        )
        food = expense("90")
        online = ExpenseRecord(
            row_number=3,
            entry_id="onl123",
            timestamp="13:00:00",
            expense_date="2026-07-10",
            month="2026-07",
            logged_by="Me",
            raw_input="sample",
            amount=Decimal("90"),
            category="Transport",
            description="Sample shop",
            input_type="Text",
            status="Confirmed",
            payment_method="Sample Visa",
            payment_owner="Me",
            payment_channel="Online",
        )
        sheets = ReminderSheets(config, [food, online])
        finance, telegram = self._bot(sheets)

        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 7, 10), 9))

        self.assertEqual(len(telegram.messages), 1)
        self.assertIn("these limits this cycle", telegram.messages[0]["text"])
        self.assertIn("Food - $90.00 of $100.00 (90%)", telegram.messages[0]["text"])
        self.assertIn("Online - $90.00 of $100.00 (90%)", telegram.messages[0]["text"])
        self.assertEqual(sheets.state["card_limit_90:2026-07-01:Me:Sample Visa"], "yes")

    async def test_failed_send_is_retried_once_and_then_remembered(self):
        sheets = ReminderSheets(
            capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]),
            [expense("90")],
        )
        telegram = RecordingBot(fail_times=1)
        finance = FinanceBot(ReminderSettings(), sheets)
        today = date(2026, 7, 10)

        await finance._run_monthly_checks(telegram, at_singapore(today, 9))
        self.assertEqual(telegram.messages, [])
        self.assertEqual(sheets.state, {})

        await finance._run_monthly_checks(telegram, at_singapore(today, 9))
        self.assertEqual(len(telegram.messages), 1)
        self.assertEqual(sheets.state["card_limit_90:2026-07-01:Me:Sample Visa"], "yes")
        self.assertEqual(sheets.state["card_limit_90_checked:2026-07-10"], "yes")

    async def test_card_summary_reply_does_not_send_the_household_reminder(self):
        sheets = ReminderSheets(
            capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]),
            [expense("95")],
        )
        finance, telegram = self._bot(sheets)
        update = FakeUpdate()

        await finance._reply_with_card_summary(update)

        self.assertEqual(telegram.messages, [])
        self.assertEqual(sheets.state, {})
        self.assertIn("Sample Visa", update.message.replies[0])

    async def test_month_end_still_sends_the_fixed_reminder(self):
        sheets = ReminderSheets(
            capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]),
            [expense("90", expense_date="2026-05-31")],
        )
        finance, telegram = self._bot(sheets)

        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 5, 31), 9))

        self.assertEqual(len(telegram.messages), 2)
        self.assertIn("No active fixed expenses", telegram.messages[0]["text"])
        self.assertIn("tg://user?id=111", telegram.messages[1]["text"])
        self.assertEqual(sheets.state["fixed_reminder_sent:2026-05"], "yes")

    async def test_bad_payment_setup_does_not_mark_the_day_checked(self):
        sheets = ReminderSheets(ValueError("Card Limits is missing these header(s): Limit Amount."))
        finance, telegram = self._bot(sheets)

        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 7, 10), 9))

        self.assertEqual(telegram.messages, [])
        self.assertEqual(sheets.state, {})

    async def test_card_usage_counts_toward_the_reminder(self):
        usage = CardUsageRecord(
            row_number=2,
            entry_id="claim1",
            timestamp="12:00:00",
            usage_date="2026-07-10",
            month="2026-07",
            logged_by="Me",
            raw_input="sample claim",
            amount=Decimal("50"),
            payment_method="Sample Visa",
            description="Sample clinic",
            usage_type="Claimable",
            status="Confirmed",
            payment_owner="Me",
        )
        sheets = ReminderSheets(
            capped_config(["Sample Visa", "Me", "All", "100", "TRUE"]),
            [expense("40")],
            [usage],
        )
        finance, telegram = self._bot(sheets)

        await finance._run_monthly_checks(telegram, at_singapore(date(2026, 7, 10), 9))

        self.assertIn("$90.00 of $100.00", telegram.messages[0]["text"])
