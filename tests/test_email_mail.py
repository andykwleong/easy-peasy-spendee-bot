from __future__ import annotations

import base64
import json
import time
import unittest
from datetime import date
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from getrichbot.cards import build_card_summary
from getrichbot.cards import parse_payment_config
from getrichbot.dashboard_http import DashboardApp
from getrichbot.dashboard_http import DashboardContext
from getrichbot.categories import configure_category_config
from getrichbot.email_mail import INCOME_MISC
from getrichbot.email_mail import MIXED_SHOP_ASK
from getrichbot.email_mail import REVERSAL_MISSING_TEXT
from getrichbot.email_mail import build_expense_row
from getrichbot.email_mail import category_from_keywords
from getrichbot.email_mail import decide_mail
from getrichbot.email_mail import email_logged_text
from getrichbot.email_mail import logged_notice_entry_id
from getrichbot.row_edits import category_for_linked_message
from getrichbot.row_edits import record_for_message_id
from getrichbot.sheets import expense_message_column
from getrichbot.shop_category import categorize_email_shop
from getrichbot.shop_category import shop_choice_state_key
from getrichbot.gmail_watch import BACKLOG_KEY
from getrichbot.gmail_watch import HISTORY_KEY
from getrichbot.gmail_watch import BotEmailContext
from getrichbot.gmail_watch import EmailService
from getrichbot.gmail_watch import EmailSettings
from getrichbot.gmail_watch import WriteOutcome
from getrichbot.gmail_watch import decode_pubsub_notification
from getrichbot.models import ExpenseRecord
from getrichbot.parser import categorize_description

PURCHASE_SUBJECT = "UOB - Transaction Alert"
FOODPANDA_BODY = (
    "A transaction of SGD 18.50 was made with your UOB Card ending 1234 "
    "on 05/10/2026 at fp*Food Panda.\n"
)
REFUND_SUBJECT = "Your transaction has been refunded"
REFUND_BODY = "A refund of SGD 18.50 was credited on 05/10/2026 for the card ending 1234.\n"
STATEMENT_SUBJECT = "Your eStatement/eAdvice is ready for viewing"
PAYNOW_SUBJECT = "UOB-PayNow transfer received"
PAYNOW_BODY = "You received SGD 40.00 by PayNow on 05/10/2026.\n"
TODAY = date(2026, 10, 5)
SINGAPORE = ZoneInfo("Asia/Singapore")


def payment_rows(include_last4: bool, last4: str = "1234") -> list[list[str]]:
    if include_last4:
        return [
            ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active", "Last 4"],
            ["UOB Sample Visa", "Me", "Credit Card", "Calendar", "1", "TRUE", last4],
            ["Cash", "Me", "Cash", "Calendar", "1", "TRUE", ""],
        ]
    return [
        ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
        ["UOB Sample Visa", "Me", "Credit Card", "Calendar", "1", "TRUE"],
        ["Cash", "Me", "Cash", "Calendar", "1", "TRUE"],
    ]


def limit_rows(*channels: str) -> list[list[str]]:
    rows = [["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"]]
    for channel in channels:
        rows.append(["UOB Sample Visa", "Me", "All", channel, "500", "TRUE"])
    return rows


def config_with(channels: tuple[str, ...] = ("Online", "PayWave"), include_last4: bool = True, last4: str = "1234"):
    return parse_payment_config(payment_rows(include_last4, last4), limit_rows(*channels))


class Context:
    def __init__(self, config, categories: tuple[str, ...]):
        self.config = config
        self._categories = categories

    def methods(self):
        return self.config.payment_methods

    def categories(self):
        return self._categories

    def has_income_misc(self):
        return INCOME_MISC in self._categories

    def channels_for(self, owner, payment_method):
        return self.config.channel_options_for(owner, payment_method)

    def category_for(self, shop, logged_by, body=""):
        del body
        return category_from_keywords(shop, logged_by, "Me", "My wife")


class MemoryState:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value


class FakeMailbox:
    def __init__(self):
        self.watch_calls = 0
        self.stop_calls = 0
        self.history_calls = 0
        self.fetch_calls = 0
        self.recent_calls = 0
        self.recent: list[str] = []
        self.added: list[str] = []
        self.next_history = "200"
        self.messages: dict[str, tuple[str, str, tuple[str, ...]]] = {}

    def watch(self, topic: str) -> str:
        self.watch_calls += 1
        self.topic = topic
        return self.next_history

    def stop(self) -> None:
        self.stop_calls += 1

    def added_since(self, start_history_id: str):
        self.history_calls += 1
        self.start_history = start_history_id
        return list(self.added), self.next_history

    def recent_ids(self, limit: int = 20):
        self.recent_calls += 1
        return list(self.recent)

    def fetch(self, message_id: str):
        self.fetch_calls += 1
        return self.messages[message_id]


class FakeWriter:
    def __init__(self):
        self.rows = []
        self.updates = []

    def write(self, row):
        self.rows.append(row)
        return WriteOutcome(logged=True, text=f"logged {row.category}")


def active_settings() -> EmailSettings:
    return EmailSettings(
        enabled=True,
        client_id="your-client-id.apps.googleusercontent.com",
        client_secret="your-client-secret",
        refresh_token="your-refresh-token",
        topic="projects/your-project-id/topics/your-topic-name",
        audience="https://your-app.example.com/gmail/push",
        push_service_account="",
        forwarders={"person-a@example.com": "Me"},
        chat_id=-100,
        me_label="Me",
        wife_label="My wife",
    )


def service_for(config, categories: tuple[str, ...] = ("Food", "Groceries", INCOME_MISC)):
    mailbox = FakeMailbox()
    state = MemoryState()
    writer = FakeWriter()
    service = EmailService(
        active_settings(),
        mailbox,
        state,
        Context(config, categories),
        writer.write,
        authorize=lambda _headers: True,
        now=lambda: datetime(2026, 10, 5, 9, 0, tzinfo=SINGAPORE),
    )
    return service, mailbox, state, writer


def decide(subject: str, body: str, config, categories: tuple[str, ...] = ("Food", "Groceries", INCOME_MISC)):
    return decide_mail(
        subject,
        body,
        ("person-a@example.com",),
        forwarders={"person-a@example.com": "Me"},
        methods=config.payment_methods,
        categories=categories,
        channels_for=config.channel_options_for,
        category_for=lambda shop, logged_by, body="": category_from_keywords(shop, logged_by, "Me", "My wife"),
        today=TODAY,
    )


class EmailMailTests(unittest.TestCase):
    def test_foodpanda_purchase_uses_keyword_category_and_last4(self):
        category, confidence = categorize_description("fp*Food Panda", "Me", "Me", "My wife")
        self.assertEqual(category, "Food")
        self.assertGreaterEqual(confidence, 0.7)

        decision = decide(PURCHASE_SUBJECT, FOODPANDA_BODY, config_with())
        self.assertEqual(decision.action, "log")
        self.assertEqual(decision.category, "Food")
        self.assertEqual(decision.shop, "fp*Food Panda")
        self.assertEqual(decision.amount, Decimal("18.50"))
        self.assertEqual(decision.last4, "1234")
        self.assertEqual(decision.payment_method, "UOB Sample Visa")
        self.assertEqual(decision.payment_owner, "Me")
        self.assertEqual(decision.logged_by, "Me")
        self.assertEqual(decision.payment_channel, "Online")

    def test_two_digit_uob_year_is_2026_and_keeps_the_shop(self):
        body = (
            "A transaction of SGD 5.99 was made with your UOB Card ending 1234 "
            "on 05/10/26 at fp*Food Panda."
        )
        decision = decide(PURCHASE_SUBJECT, body, config_with())
        self.assertEqual(decision.action, "log")
        self.assertEqual(decision.expense_date, date(2026, 10, 5))
        self.assertEqual(decision.shop, "fp*Food Panda")
        self.assertEqual(decision.amount, Decimal("5.99"))
        self.assertEqual(decision.last4, "1234")
        self.assertEqual(decision.category, "Food")
        self.assertEqual(decision.payment_channel, "Online")

    def test_clock_time_before_the_shop_is_still_one_purchase(self):
        bodies = (
            "A transaction of SGD 5.99 was made with your UOB Card ending 1234 on 05/10/26 at fp*Food Panda.",
            "A transaction of SGD 5.99 was made with your UOB Card ending 1234 on 05/10/26 14:22 at fp*Food Panda.",
            "A transaction of SGD 5.99 was made with your UOB Card ending 1234 on 05/10/26 2:22PM at fp*Food Panda.",
        )
        for body in bodies:
            decision = decide(PURCHASE_SUBJECT, body, config_with())
            self.assertEqual(decision.action, "log")
            self.assertEqual(decision.shop, "fp*Food Panda")
            self.assertEqual(decision.expense_date, date(2026, 10, 5))
            self.assertEqual(decision.amount, Decimal("5.99"))
            self.assertEqual(decision.last4, "1234")

    def test_extra_amount_and_card_outside_the_purchase_sentence_are_ignored(self):
        body = (
            "A transaction of SGD 5.99 was made with your UOB Card ending 1234 "
            "on 05/10/26 14:22 at fp*Food Panda. "
            "Available limit SGD 1,000.00. Card ending 5678."
        )
        decision = decide(PURCHASE_SUBJECT, body, config_with())
        self.assertEqual(decision.action, "log")
        self.assertEqual(decision.amount, Decimal("5.99"))
        self.assertEqual(decision.last4, "1234")
        self.assertEqual(decision.shop, "fp*Food Panda")
        self.assertEqual(decision.expense_date, date(2026, 10, 5))

    def test_refused_purchase_names_the_missing_piece(self):
        shop = decide(
            PURCHASE_SUBJECT,
            "A transaction of SGD 5.99 was made with your UOB Card ending 1234 on 05/10/26.",
            config_with(),
        )
        amount = decide(
            PURCHASE_SUBJECT,
            "A transaction was made with your UOB Card ending 1234 on 05/10/26 at fp*Food Panda.",
            config_with(),
        )
        card = decide(
            PURCHASE_SUBJECT,
            "A transaction of SGD 5.99 was made with your UOB Card on 05/10/26 at fp*Food Panda.",
            config_with(),
        )
        date_and_shop = decide(
            PURCHASE_SUBJECT,
            "A transaction of SGD 5.99 was made with your UOB Card ending 1234 at fp*Food Panda.",
            config_with(),
        )
        self.assertEqual(shop.action, "log")
        self.assertEqual(shop.amount, Decimal("5.99"))
        self.assertEqual(shop.last4, "1234")
        self.assertEqual(shop.expense_date, date(2026, 10, 5))
        self.assertEqual(shop.shop, "")
        self.assertEqual(amount.text, "I got a transaction, but the amount was missing. I have not logged it.")
        self.assertEqual(card.text, "I got a transaction, but the card was missing. I have not logged it.")
        self.assertEqual(date_and_shop.text, "I got a transaction, but the date was missing. I have not logged it.")
        for decision in (amount, card, date_and_shop):
            self.assertEqual(decision.action, "ask")
            self.assertNotIn("not one clear purchase", decision.text)
            self.assertNotIn("one amount, one shop, one date, and one card", decision.text)

    def test_refund_is_income_misc_and_does_not_touch_the_card(self):
        decision = decide(REFUND_SUBJECT, REFUND_BODY, config_with())
        self.assertEqual(decision.action, "log")
        self.assertEqual(decision.category, INCOME_MISC)
        self.assertEqual(decision.payment_method, "")
        self.assertEqual(decision.payment_owner, "")
        self.assertEqual(decision.payment_channel, "")
        row = build_expense_row(decision, chat_id=-100, now=datetime(2026, 10, 5, 9, 0, tzinfo=SINGAPORE))
        self.assertEqual(row.transaction_type, "Income")
        self.assertEqual(row.payment_method, "")
        self.assertNotEqual(row.category, "Income - A")
        self.assertNotEqual(row.category, "Income - fx")

    def test_refund_income_does_not_reduce_the_card_total(self):
        config = config_with()
        purchase = ExpenseRecord(
            row_number=2,
            entry_id="aaa111",
            timestamp="09:00:00",
            expense_date="2026-10-05",
            month="2026-10",
            logged_by="Me",
            raw_input="email: fp*Food Panda",
            amount=Decimal("18.50"),
            category="Food",
            description="fp*Food Panda",
            input_type="Email",
            status="Confirmed",
            transaction_type="Expense",
            payment_method="UOB Sample Visa",
            payment_owner="Me",
            payment_channel="Online",
        )
        refund = ExpenseRecord(
            row_number=3,
            entry_id="bbb222",
            timestamp="09:05:00",
            expense_date="2026-10-05",
            month="2026-10",
            logged_by="Me",
            raw_input="email: Refund",
            amount=Decimal("18.50"),
            category=INCOME_MISC,
            description="Refund",
            input_type="Email",
            status="Confirmed",
            transaction_type="Income",
            payment_method="",
            payment_owner="",
            payment_channel="",
        )
        items = build_card_summary(config, [purchase, refund], "Me", TODAY)
        self.assertEqual(items[0].total_spend, Decimal("18.50"))

    def test_statement_is_ignored(self):
        with_amount = decide(STATEMENT_SUBJECT, "Your statement total is SGD 900.00.", config_with())
        without_amount = decide(STATEMENT_SUBJECT, "Your statement is ready for viewing.", config_with())
        self.assertEqual(with_amount.action, "ignore")
        self.assertEqual(with_amount.text, "")
        self.assertEqual(without_amount.action, "ignore")
        self.assertEqual(without_amount.text, "")

    def test_paynow_dividend_and_interest_are_income_misc(self):
        paynow = decide(PAYNOW_SUBJECT, PAYNOW_BODY, config_with())
        dividend = decide(
            "Dividend credited",
            "A dividend of SGD 12.00 was credited on 05/10/2026.",
            config_with(),
        )
        interest = decide(
            "Interest credited",
            "Interest of SGD 1.25 was credited on 05/10/2026.",
            config_with(),
        )
        for decision in (paynow, dividend, interest):
            self.assertEqual(decision.action, "log")
            self.assertEqual(decision.category, INCOME_MISC)
            self.assertEqual(decision.payment_method, "")

    def test_missing_income_misc_asks_and_does_not_invent_a_category(self):
        decision = decide(REFUND_SUBJECT, REFUND_BODY, config_with(), categories=("Food", "Income - A", "Income - fx"))
        self.assertEqual(decision.action, "ask")
        self.assertIn("Income - misc", decision.text)
        self.assertEqual(decision.category, "")
        self.assertNotIn("Income - A", decision.category)

    def test_unclear_mail_stays_silent_and_salary_still_asks(self):
        unclear = decide("Security alert", "We noticed a sign-in from a new device.", config_with())
        newsletter = decide("Weekly newsletter", "Hello from the bank.", config_with())
        salary = decide("Salary payment", "Salary of SGD 1000.00 was paid.", config_with())
        dated_only = decide("Security alert", "Something changed on 12/10/26.", config_with())
        for decision in (unclear, newsletter, dated_only):
            self.assertEqual(decision.action, "ignore")
            self.assertEqual(decision.text, "")
        self.assertEqual(salary.action, "ask")
        self.assertIn("salary", salary.text.casefold())
        self.assertNotEqual(salary.category, INCOME_MISC)

    def test_amount_without_date_or_card_still_names_the_missing_piece(self):
        decision = decide(
            "Card transaction",
            "Transaction amount SGD 40.00\nTransaction details SAMPLE SHOP\n",
            config_with(last4="1111"),
        )
        self.assertEqual(decision.action, "ask")
        self.assertIn("the date", decision.text)
        self.assertIn("the card", decision.text)
        self.assertNotIn("the amount", decision.text)

    def test_reversal_removes_the_matching_row_and_does_not_write_income(self):
        body = (
            "This is a reversal.\n"
            "Account number XXXX-XXXX-XXXX-1111\n"
            "Transaction date 12/10/26\n"
            "Transaction amount SGD165.00\n"
            "Transaction details SAMPLE SHOP\n"
        )
        decision = decide("Card transaction", body, config_with(last4="1111"))
        self.assertEqual(decision.action, "reverse")
        self.assertEqual(decision.amount, Decimal("165.00"))
        self.assertEqual(decision.last4, "1111")
        self.assertEqual(decision.shop, "SAMPLE SHOP")
        self.assertNotEqual(decision.category, INCOME_MISC)

        original = ExpenseRecord(
            row_number=2,
            entry_id="ab12cd",
            timestamp="09:00:00",
            expense_date="2026-10-12",
            month="2026-10",
            logged_by="Me",
            raw_input="email: SAMPLE SHOP",
            amount=Decimal("165.00"),
            category="Food",
            description="SAMPLE SHOP",
            input_type="Email",
            status="Confirmed",
            transaction_type="Expense",
            payment_method="UOB Sample Visa",
            payment_owner="Me",
            payment_channel="PayWave",
        )
        other = ExpenseRecord(
            row_number=3,
            entry_id="cd34ef",
            timestamp="09:05:00",
            expense_date="2026-10-12",
            month="2026-10",
            logged_by="Me",
            raw_input="email: SAMPLE SHOP",
            amount=Decimal("10.00"),
            category="Food",
            description="SAMPLE SHOP",
            input_type="Email",
            status="Confirmed",
            transaction_type="Expense",
            payment_method="UOB Sample Visa",
            payment_owner="Me",
            payment_channel="PayWave",
        )
        context = Context(config_with(last4="1111"), ("Food", "Groceries", INCOME_MISC))
        context.records = [original, other]
        context.deleted = []

        def expense_records():
            return list(context.records)

        def delete_expense(entry_id):
            context.deleted.append(entry_id)
            context.records = [record for record in context.records if record.entry_id != entry_id]
            return True

        context.expense_records = expense_records
        context.delete_expense = delete_expense
        mailbox = FakeMailbox()
        state = MemoryState()
        writer = FakeWriter()
        service = EmailService(
            active_settings(),
            mailbox,
            state,
            context,
            writer.write,
            authorize=lambda _headers: True,
            now=lambda: datetime(2026, 10, 12, 9, 0, tzinfo=SINGAPORE),
        )
        state.set(HISTORY_KEY, "100")
        mailbox.added = ["reverse"]
        mailbox.messages["reverse"] = ("Card transaction", body, ("person-a@example.com",))
        sent = []
        service._notify = sent.append
        service._notifier_bound = True
        service.process_notification("999")

        self.assertEqual(writer.rows, [])
        self.assertEqual(context.deleted, ["ab12cd"])
        self.assertEqual([record.entry_id for record in context.records], ["cd34ef"])
        self.assertEqual(len(sent), 1)
        self.assertIn("The original charge was reversed and removed.", sent[0])
        self.assertIn("SAMPLE SHOP", sent[0])
        self.assertIn("ab12cd", sent[0])
        self.assertNotIn("duplicate", sent[0].casefold())
        self.assertNotIn(INCOME_MISC, sent[0])

    def test_reversal_with_no_match_does_not_write_a_row(self):
        body = (
            "A reversal was posted.\n"
            "Account number XXXX-XXXX-XXXX-1111\n"
            "Transaction date 12/10/26\n"
            "Transaction amount SGD165.00\n"
            "Transaction details SAMPLE SHOP\n"
        )
        context = Context(config_with(last4="1111"), ("Food", INCOME_MISC))
        context.records = []
        context.expense_records = lambda: []
        context.delete_expense = lambda entry_id: False
        mailbox = FakeMailbox()
        state = MemoryState()
        writer = FakeWriter()
        service = EmailService(
            active_settings(),
            mailbox,
            state,
            context,
            writer.write,
            authorize=lambda _headers: True,
            now=lambda: datetime(2026, 10, 12, 9, 0, tzinfo=SINGAPORE),
        )
        state.set(HISTORY_KEY, "100")
        mailbox.added = ["reverse"]
        mailbox.messages["reverse"] = ("Card transaction", body, ("person-a@example.com",))
        sent = []
        service._notify = sent.append
        service._notifier_bound = True
        service.process_notification("999")
        self.assertEqual(writer.rows, [])
        self.assertEqual(sent, [REVERSAL_MISSING_TEXT])
        self.assertNotIn("duplicate", sent[0].casefold())

    def test_refund_subject_stays_income_when_the_body_says_reversal(self):
        body = REFUND_BODY + "The posting note says reversal.\n"
        decision = decide(REFUND_SUBJECT, body, config_with())
        self.assertEqual(decision.action, "log")
        self.assertEqual(decision.kind, "refund")
        self.assertEqual(decision.category, INCOME_MISC)
        original = ExpenseRecord(
            row_number=2,
            entry_id="ab12cd",
            timestamp="09:00:00",
            expense_date="2026-10-05",
            month="2026-10",
            logged_by="Me",
            raw_input="email: SAMPLE SHOP",
            amount=Decimal("18.50"),
            category="Food",
            description="SAMPLE SHOP",
            input_type="Email",
            status="Confirmed",
            transaction_type="Expense",
            payment_method="UOB Sample Visa",
            payment_owner="Me",
        )
        context = Context(config_with(), ("Food", INCOME_MISC))
        context.records = [original]
        context.deleted = []
        context.expense_records = lambda: list(context.records)
        context.delete_expense = lambda entry_id: context.deleted.append(entry_id) or False
        mailbox = FakeMailbox()
        state = MemoryState()
        writer = FakeWriter()
        service = EmailService(
            active_settings(),
            mailbox,
            state,
            context,
            writer.write,
            authorize=lambda _headers: True,
            now=lambda: datetime(2026, 10, 5, 9, 0, tzinfo=SINGAPORE),
        )
        state.set(HISTORY_KEY, "100")
        mailbox.added = ["refund"]
        mailbox.messages["refund"] = (REFUND_SUBJECT, body, ("person-a@example.com",))
        service.process_notification("999")
        self.assertEqual(context.deleted, [])
        self.assertEqual([record.entry_id for record in context.records], ["ab12cd"])
        self.assertEqual(writer.rows[0].category, INCOME_MISC)
        self.assertEqual(writer.rows[0].transaction_type, "Income")

    def test_unknown_shop_with_both_channels_is_logged_with_a_blank_channel(self):
        body = (
            "A transaction of SGD 22.00 was made with your UOB Card ending 1234 "
            "on 05/10/2026 at Sample Market.\n"
        )
        decision = decide(PURCHASE_SUBJECT, body, config_with())
        self.assertEqual(decision.action, "log")
        self.assertEqual(decision.payment_channel, "")
        self.assertEqual(decision.category, "Groceries")

    def test_restaurant_uses_paywave_and_one_channel_is_automatic(self):
        cafe = (
            "A transaction of SGD 9.00 was made with your UOB Card ending 1234 "
            "on 05/10/2026 at Sample Cafe.\n"
        )
        both = decide(PURCHASE_SUBJECT, cafe, config_with())
        self.assertEqual(both.action, "log")
        self.assertEqual(both.payment_channel, "PayWave")
        only = decide(PURCHASE_SUBJECT, cafe, config_with(("PayWave",)))
        self.assertEqual(only.payment_channel, "PayWave")

    def test_missing_last4_column_does_not_crash(self):
        config = config_with(include_last4=False)
        self.assertEqual(config.method_for("Me", "UOB Sample Visa").last4, "")
        self.assertEqual(config.method_for("Me", "Cash").name, "Cash")
        decision = decide(PURCHASE_SUBJECT, FOODPANDA_BODY, config)
        self.assertEqual(decision.action, "ask")
        self.assertNotEqual(decision.action, "log")

    def test_extra_last4_column_keeps_existing_cards(self):
        config = config_with()
        self.assertEqual(config.method_for("Me", "UOB Sample Visa").name, "UOB Sample Visa")
        self.assertEqual(config.method_for("Me", "UOB Sample Visa").last4, "1234")
        self.assertEqual(config.method_for("Me", "Cash").last4, "")

    def test_blank_last4_in_the_body_does_not_crash(self):
        body = "A transaction of SGD 18.50 was made with your UOB Card on 05/10/2026 at fp*Food Panda.\n"
        decision = decide(PURCHASE_SUBJECT, body, config_with())
        self.assertEqual(decision.action, "ask")

    def test_any_subject_with_amount_date_and_card_is_logged(self):
        body = (
            "Paid SGD 16.31 with your card ending 1234 "
            "on 08/10/2026 at Zzzqq Plug."
        )
        decision = decide("A note from the bank", body, config_with())
        self.assertEqual(decision.action, "log")
        self.assertEqual(decision.shop, "Zzzqq Plug")
        self.assertEqual(decision.amount, Decimal("16.31"))
        self.assertEqual(decision.last4, "1234")
        self.assertEqual(decision.expense_date, date(2026, 10, 8))
        self.assertEqual(decision.category, "")
        self.assertEqual(decision.payment_channel, "")
        row = build_expense_row(decision, chat_id=-100, now=datetime(2026, 10, 8, 11, 0, tzinfo=SINGAPORE))
        text = email_logged_text(row)
        lines = text.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("Logged $16.31 at Zzzqq Plug - 8 October 2026 via UOB Sample Visa (All) ["))
        self.assertEqual(lines[1], "Category is missing. Reply to this message with the category.")
        self.assertNotIn("Income - A", text)
        self.assertNotIn("Income - fx", text)

    def test_missing_date_is_named_for_any_bank(self):
        decision = decide(
            "Card transaction",
            "Amount: SGD10.00\ncard ending 1111\nTo: SAMPLE SHOP\n",
            config_with(last4="1111"),
        )
        self.assertEqual(decision.action, "ask")
        self.assertEqual(decision.text, "I got a transaction, but the date was missing. I have not logged it.")
        self.assertNotIn("not one clear purchase", decision.text)

    def test_card_shapes_read_the_last_four_and_ignore_a_uen_or_a_long_number(self):
        stars = decide(
            "Card transaction",
            "Amount: SGD 8.00\ndated 09/10/2026\n****1111\nTo: SAMPLE DEPOT\n",
            config_with(last4="1111"),
        )
        bare_ending = decide(
            "Card transaction",
            "Amount: SGD 8.00\ndated 09/10/2026\nending 1111\nTo: SAMPLE DEPOT\n",
            config_with(last4="1111"),
        )
        for decision in (stars, bare_ending):
            self.assertEqual(decision.action, "log")
            self.assertEqual(decision.last4, "1111")
            self.assertEqual(decision.shop, "SAMPLE DEPOT")
            self.assertEqual(decision.expense_date, date(2026, 10, 9))
        uen = decide(
            "Payment sent",
            "Amount: SGD 3.50\nDate & Time: 05 Oct 11:26 (SGT)\nTo: SAMPLE COURIER (UEN ending ABCD)\n",
            config_with(last4="4444"),
        )
        reference = decide(
            "Card transaction",
            "Amount: SGD231.66\ndated 09/10/26\nTransaction Ref: REF000000000000009999\nTo: SAMPLE SHOP\n",
            config_with(last4="1111"),
        )
        for decision in (uen, reference):
            self.assertEqual(decision.action, "ask")
            self.assertIn("the card was missing", decision.text)

    def test_month_without_a_year_uses_another_date_in_the_same_mail(self):
        body = (
            "dated 09/10/27\n"
            "Date & Time: 09 OCT 23:25 (SGT)\n"
            "Amount: SGD10.00\n"
            "card ending 1111\n"
            "To: SAMPLE DEPOT\n"
        )
        decision = decide("Card transaction", body, config_with(last4="1111"))
        self.assertEqual(decision.action, "log")
        self.assertEqual(decision.expense_date, date(2027, 10, 9))
        self.assertEqual(decision.amount, Decimal("10.00"))

    def test_purchase_layouts_log_an_expense(self):
        layouts = (
            (
                "Card transaction",
                "\n".join(
                    (
                        "Transaction Ref: REF000000000000009999",
                        "dated 09/10/26",
                        "Date & Time: 09 OCT 23:25 (SGT)",
                        "Amount: SGD231.66",
                        "card ending 1111",
                        "To: SAMPLE SHOP",
                    )
                ),
                "1111",
                Decimal("231.66"),
                date(2026, 10, 9),
                "SAMPLE SHOP",
            ),
            (
                "Card transaction",
                "\n".join(
                    (
                        "Account number: XXXX-XXXX-XXXX-2222",
                        "Transaction date: 09/10/26",
                        "Transaction time: 13:33:34",
                        "Transaction amount: SGD36.76",
                        "Transaction details : SAMPLE MARKET",
                    )
                ),
                "2222",
                Decimal("36.76"),
                date(2026, 10, 9),
                "SAMPLE MARKET",
            ),
            (
                "Card transaction",
                "\n".join(
                    (
                        "Card Number",
                        "XXXX-XXXX-XXXX-3333",
                        "Transaction Date",
                        "08/OCT/2026",
                        "Transaction Time",
                        "18:50:38",
                        "Transaction Amount",
                        "SGD25.90",
                        "Description",
                        "SAMPLE CAFE",
                    )
                ),
                "3333",
                Decimal("25.90"),
                date(2026, 10, 8),
                "SAMPLE CAFE",
            ),
            (
                "Payment sent",
                "\n".join(
                    (
                        "Date & Time: 05 Oct 11:26 (SGT)",
                        "Amount: SGD3.50",
                        "From: Sample Joint Account A/C ending 4444",
                        "To: SAMPLE COURIER (UEN ending ABCD)",
                    )
                ),
                "4444",
                Decimal("3.50"),
                date(2026, 10, 5),
                "SAMPLE COURIER",
            ),
        )
        for subject, body, last4, amount, when, shop in layouts:
            decision = decide(subject, body, config_with(last4=last4))
            self.assertEqual(decision.action, "log", decision.text)
            self.assertEqual(decision.kind, "purchase")
            self.assertEqual(decision.amount, amount)
            self.assertEqual(decision.last4, last4)
            self.assertEqual(decision.expense_date, when)
            self.assertEqual(decision.shop, shop)
            self.assertFalse(decision.category.casefold().startswith("income"))
            row = build_expense_row(decision, chat_id=-100, now=datetime(2026, 10, 9, 12, 0, tzinfo=SINGAPORE))
            self.assertEqual(row.transaction_type, "Expense")
            self.assertNotEqual(row.category, INCOME_MISC)
            self.assertEqual(row.payment_method, "UOB Sample Visa")
            self.assertEqual(row.description, shop)

    def test_to_email_address_is_skipped_for_the_next_shop_line(self):
        saved = []
        body = "\n".join(
            (
                "To: <person@example.com>",
                "Transaction amount: SGD36.76",
                "Transaction date: 09/10/26",
                "Account number: XXXX-XXXX-XXXX-2222",
                "To: SAMPLE MARKET",
                "Note: Food",
            )
        )
        decision = decide_mail(
            "Card transaction",
            body,
            ("person-a@example.com",),
            forwarders={"person-a@example.com": "Me"},
            methods=config_with(channels=("Online",), last4="2222").payment_methods,
            categories=("Food", "Groceries", INCOME_MISC),
            channels_for=config_with(channels=("Online",), last4="2222").channel_options_for,
            category_for=lambda shop, logged_by, body="": None,
            today=TODAY,
            remember_category=lambda keyword, category: saved.append((keyword, category)),
        )
        self.assertEqual(decision.action, "log", decision.text)
        self.assertEqual(decision.shop, "SAMPLE MARKET")
        self.assertNotIn("person@example.com", decision.shop)
        self.assertEqual(saved, [("sample market", "Food")])
        self.assertFalse(any("@" in keyword for keyword, _category in saved))

    def test_body_category_is_used_and_the_shop_is_not_searched(self):
        calls = []
        saved = []

        def category_for(shop, logged_by, body=""):
            del logged_by, body
            calls.append(shop)
            return None

        decision = decide_mail(
            "Please file this",
            "Income Tax of SGD 16.31 on card ending 1234 on 08/10/2026 at Sample Office.",
            ("person-a@example.com",),
            forwarders={"person-a@example.com": "Me"},
            methods=config_with(("All",)).payment_methods,
            categories=("Food", "Income Tax", INCOME_MISC, "Income - A", "Income - fx"),
            channels_for=config_with(("All",)).channel_options_for,
            category_for=category_for,
            today=TODAY,
            remember_category=lambda keyword, category: saved.append((keyword, category)),
        )
        self.assertEqual(decision.action, "log")
        self.assertEqual(decision.category, "Income Tax")
        self.assertEqual(decision.payment_method, "UOB Sample Visa")
        self.assertEqual(calls, [])
        self.assertEqual(saved, [("sample office", "Income Tax")])
        self.assertNotEqual(decision.category, "Income - A")
        self.assertNotEqual(decision.category, "Income - fx")

    def test_card_ending_to_a_shop_is_an_expense_even_when_lookup_says_income(self):
        body = "\n".join(
            (
                "We refer to your card transaction request dated 13/10/26. We are pleased to confirm that the transaction was completed.",
                "",
                "Date & Time: 13 OCT 14:42 (SGT)",
                "Amount: SGD9.60",
                "From: DBS/POSB card ending 1111",
                "To: GPAY NETWORK (S) PTE L Singapore SGP",
            )
        )
        calls = []
        saved = []

        def category_for(shop, logged_by, body=""):
            del logged_by, body
            calls.append(shop)
            return "Income - misc"

        config = config_with(channels=("Online",), last4="1111")
        decision = decide_mail(
            "Card transaction",
            body,
            ("person-a@example.com",),
            forwarders={"person-a@example.com": "Me"},
            methods=config.payment_methods,
            categories=("Transport", "Food", "Income - misc"),
            channels_for=config.channel_options_for,
            category_for=category_for,
            today=TODAY,
            remember_category=lambda keyword, category: saved.append((keyword, category)),
        )

        self.assertEqual(calls, [])
        self.assertEqual(decision.action, "log", decision.text)
        self.assertEqual(decision.category, "Transport")
        self.assertEqual(decision.shop, "GPAY NETWORK (S) PTE L Singapore SGP")
        self.assertEqual(decision.amount, Decimal("9.60"))
        self.assertEqual(decision.last4, "1111")
        self.assertEqual(decision.expense_date, date(2026, 10, 13))
        self.assertEqual(decision.payment_method, "UOB Sample Visa")
        self.assertEqual(saved, [("gpay network (s) pte l singapore sgp", "Transport")])
        row = build_expense_row(decision, chat_id=-100, now=datetime(2026, 10, 13, 14, 42, tzinfo=SINGAPORE))
        self.assertEqual(row.transaction_type, "Expense")
        self.assertEqual(row.payment_method, "UOB Sample Visa")
        self.assertEqual(row.category, "Transport")

    def test_gpay_without_transport_logs_a_blank_expense(self):
        body = "\n".join(
            (
                "We refer to your card transaction request dated 13/10/26. We are pleased to confirm that the transaction was completed.",
                "",
                "Date & Time: 13 OCT 14:42 (SGT)",
                "Amount: SGD9.60",
                "From: DBS/POSB card ending 1111",
                "To: GPAY NETWORK (S) PTE L Singapore SGP",
            )
        )
        calls = []
        config = config_with(channels=("Online",), last4="1111")
        decision = decide_mail(
            "Card transaction",
            body,
            ("person-a@example.com",),
            forwarders={"person-a@example.com": "Me"},
            methods=config.payment_methods,
            categories=("Food", "Income - misc"),
            channels_for=config.channel_options_for,
            category_for=lambda shop, logged_by, body="": calls.append(shop) or "Income - misc",
            today=TODAY,
        )
        self.assertEqual(calls, [])
        self.assertEqual(decision.action, "log")
        self.assertEqual(decision.category, "")
        self.assertEqual(decision.payment_method, "UOB Sample Visa")
        self.assertEqual(decision.last4, "1111")
        row = build_expense_row(decision, chat_id=-100, now=datetime(2026, 10, 13, 14, 42, tzinfo=SINGAPORE))
        self.assertEqual(row.transaction_type, "Expense")
        self.assertEqual(row.payment_method, "UOB Sample Visa")
        self.assertIn("Category is missing", email_logged_text(row))

    def test_gpay_uses_the_category_whose_keyword_is_transport(self):
        from getrichbot import categories

        original = {
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
                "variable_categories": ["Getting around", "Food"],
                "fixed_categories": ["Rent or mortgage"],
                "category_keywords": {"Getting around": ["transport"]},
                "priority_keywords": [],
                "shopping_keywords": [],
                "shopping_categories": {},
                "category_aliases": {},
                "source": "test",
            }
        )
        try:
            body = "\n".join(
                (
                    "Date & Time: 13 OCT 14:42 (SGT)",
                    "dated 13/10/26",
                    "Amount: SGD9.60",
                    "From: card ending 1111",
                    "To: GPAY NETWORK (S) PTE L Singapore SGP",
                )
            )
            config = config_with(channels=("Online",), last4="1111")
            decision = decide_mail(
                "Card transaction",
                body,
                ("person-a@example.com",),
                forwarders={"person-a@example.com": "Me"},
                methods=config.payment_methods,
                categories=("Getting around", "Food", "Income - misc"),
                channels_for=config.channel_options_for,
                category_for=lambda shop, logged_by, body="": "Income - misc",
                today=TODAY,
            )
            self.assertEqual(decision.category, "Getting around")
            self.assertEqual(decision.payment_method, "UOB Sample Visa")
            row = build_expense_row(decision, chat_id=-100, now=datetime(2026, 10, 13, 14, 42, tzinfo=SINGAPORE))
            self.assertEqual(row.transaction_type, "Expense")
        finally:
            configure_category_config(original)

    def test_other_card_to_shop_mail_drops_an_income_category_and_keeps_the_card(self):
        body = "\n".join(
            (
                "Date & Time: 13 OCT 14:42 (SGT)",
                "dated 13/10/26",
                "Amount: SGD9.60",
                "From: card ending 1111",
                "To: SAMPLE SHOP",
            )
        )
        config = config_with(channels=("Online",), last4="1111")
        decision = decide_mail(
            "Card transaction",
            body,
            ("person-a@example.com",),
            forwarders={"person-a@example.com": "Me"},
            methods=config.payment_methods,
            categories=("Food", "Transport", "Income - misc"),
            channels_for=config.channel_options_for,
            category_for=lambda shop, logged_by, body="": "Income - misc",
            today=TODAY,
        )
        self.assertEqual(decision.action, "log")
        self.assertEqual(decision.category, "")
        self.assertEqual(decision.shop, "SAMPLE SHOP")
        self.assertEqual(decision.payment_method, "UOB Sample Visa")
        self.assertEqual(decision.last4, "1111")
        row = build_expense_row(decision, chat_id=-100, now=datetime(2026, 10, 13, 14, 42, tzinfo=SINGAPORE))
        self.assertEqual(row.transaction_type, "Expense")
        self.assertEqual(row.payment_method, "UOB Sample Visa")
        self.assertIn("Category is missing", email_logged_text(row))

    def test_clock_time_is_optional_between_the_date_and_the_shop(self):
        body = (
            "A transaction of SGD 16.31 was made with your card ending 1234 "
            "on 08/10/2026 14:22 at Sample Cafe."
        )
        decision = decide("Receipt", body, config_with(("PayWave",)))
        self.assertEqual(decision.action, "log")
        self.assertEqual(decision.shop, "Sample Cafe")
        self.assertEqual(decision.expense_date, date(2026, 10, 8))
        self.assertEqual(decision.amount, Decimal("16.31"))

    def test_category_reply_uses_the_replied_message_or_the_previous_one(self):
        older = ExpenseRecord(
            row_number=2,
            entry_id="aaa111",
            timestamp="09:00:00",
            expense_date="2026-10-08",
            month="2026-10",
            logged_by="Me",
            raw_input="email: Sample Cafe",
            amount=Decimal("16.31"),
            category="",
            description="Sample Cafe",
            input_type="Email",
            status="Confirmed",
            payment_method="Sample Card",
            telegram_message_id="41",
        )
        newer = ExpenseRecord(
            row_number=3,
            entry_id="bbb222",
            timestamp="09:05:00",
            expense_date="2026-10-08",
            month="2026-10",
            logged_by="Me",
            raw_input="email: Sample Depot",
            amount=Decimal("9.00"),
            category="",
            description="Sample Depot",
            input_type="Email",
            status="Confirmed",
            payment_method="Sample Card",
            telegram_message_id="42",
        )
        bare = category_for_linked_message("Food", ("Food", "Groceries"), reply_message_id=None, last_bot_message_id="42")
        replied = category_for_linked_message("Groceries", ("Food", "Groceries"), reply_message_id="41", last_bot_message_id="42")
        self.assertEqual(bare, ("Food", "42"))
        self.assertEqual(replied, ("Groceries", "41"))
        self.assertEqual(record_for_message_id([older, newer], "42").entry_id, "bbb222")
        self.assertEqual(record_for_message_id([older, newer], "41").entry_id, "aaa111")
        self.assertIsNone(category_for_linked_message("dinner 20", ("Food",), reply_message_id=None, last_bot_message_id="42"))
        notice = "Logged $16.31 at Sample Cafe - 8 October 2026 via Sample Card (All) [e9e56c]\nCategory is missing. Reply to this message with the category."
        self.assertEqual(logged_notice_entry_id(notice), "e9e56c")
        self.assertIsNone(logged_notice_entry_id("Possible duplicate found:\nExisting [e9e56c]"))
        self.assertEqual(expense_message_column(True, True), "Q")
        self.assertEqual(expense_message_column(True, False), "P")
        self.assertEqual(expense_message_column(False, False), "O")

    def test_mixed_shop_mail_asks_and_stores_the_button_categories(self):
        categories = ("Shopping - Alex", "Home - Alex", "Baby bills - Alex", INCOME_MISC)
        service, mailbox, state, writer = service_for(config_with(("All",)), categories)
        records = (
            ExpenseRecord(
                row_number=2,
                entry_id="aaa111",
                timestamp="09:00:00",
                expense_date="2026-10-01",
                month="2026-10",
                logged_by="Me",
                raw_input="email: Sample Stall",
                amount=Decimal("4.00"),
                category="Shopping - Alex",
                description="Sample Stall",
                input_type="Email",
                status="Confirmed",
            ),
            ExpenseRecord(
                row_number=3,
                entry_id="bbb222",
                timestamp="09:00:00",
                expense_date="2026-10-02",
                month="2026-10",
                logged_by="Me",
                raw_input="email: Sample Stall",
                amount=Decimal("5.00"),
                category="Home - Alex",
                description="Sample Stall",
                input_type="Email",
                status="Confirmed",
            ),
        )
        calls = []
        stopped = []
        service.context.expense_records = lambda: records
        service.context.stop_shop_keyword = lambda shop: stopped.append(shop)
        service.context.category_for = lambda *args, **kwargs: calls.append(args)

        def write(row):
            writer.rows.append(row)
            return WriteOutcome(logged=True, text=email_logged_text(row))

        service.writer = write
        mailbox.messages["m1"] = (
            "Card alert",
            "A transaction of SGD 9.99 was made with your card ending 1234 on 05/10/2026 at Sample Stall.\n",
            ("person-a@example.com",),
        )

        notes = service._consume("m1")

        self.assertEqual(calls, [])
        self.assertEqual(stopped, ["Sample Stall"])
        self.assertEqual(writer.rows[0].category, "")
        self.assertEqual(writer.rows[0].description, "Sample Stall")
        self.assertIn(MIXED_SHOP_ASK, notes[0])
        self.assertNotIn("Category is missing", notes[0])
        stored = json.loads(state.values[shop_choice_state_key(writer.rows[0].entry_id)])
        self.assertEqual(stored, ["Shopping - Alex", "Home - Alex"])


if __name__ == "__main__":
    unittest.main()
