from __future__ import annotations

import base64
import json
import unittest
from datetime import date
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from getrichbot.cards import build_card_summary
from getrichbot.cards import parse_payment_config
from getrichbot.email_mail import INCOME_MISC
from getrichbot.email_mail import build_expense_row
from getrichbot.email_mail import category_from_keywords
from getrichbot.email_mail import decide_mail
from getrichbot.shop_category import categorize_email_shop
from getrichbot.gmail_watch import BACKLOG_KEY
from getrichbot.gmail_watch import HISTORY_KEY
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

    def category_for(self, shop, logged_by):
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
        category_for=lambda shop, logged_by: category_from_keywords(shop, logged_by, "Me", "My wife"),
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
        decision = decide(STATEMENT_SUBJECT, "Your statement total is SGD 900.00.", config_with())
        self.assertEqual(decision.action, "ignore")
        self.assertEqual(decision.text, "")

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

    def test_unclear_mail_and_salary_ask_and_do_not_write(self):
        unclear = decide("Weekly newsletter", "Hello from the bank.", config_with())
        salary = decide("Salary payment", "Salary of SGD 1000.00 was paid.", config_with())
        self.assertEqual(unclear.action, "ask")
        self.assertEqual(salary.action, "ask")
        self.assertNotEqual(salary.category, INCOME_MISC)

    def test_unknown_shop_with_both_channels_asks_and_does_not_write(self):
        body = (
            "A transaction of SGD 22.00 was made with your UOB Card ending 1234 "
            "on 05/10/2026 at Sample Market.\n"
        )
        decision = decide(PURCHASE_SUBJECT, body, config_with())
        self.assertEqual(decision.action, "ask")
        self.assertEqual(decision.kind, "channel")
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


class EmailWatchTests(unittest.TestCase):
    def test_email_disabled_does_not_start_the_watch(self):
        mailbox = FakeMailbox()
        service = EmailService(
            EmailSettings(
                enabled=False,
                client_id="",
                client_secret="",
                refresh_token="",
                topic="",
                audience="",
                push_service_account="",
                forwarders={},
                chat_id=-100,
                me_label="Me",
                wife_label="My wife",
            ),
            mailbox,
            MemoryState(),
            Context(config_with(), ("Food", INCOME_MISC)),
            FakeWriter().write,
        )
        self.assertEqual(service.start(), [])
        self.assertEqual(mailbox.watch_calls, 0)
        self.assertEqual(mailbox.fetch_calls, 0)
        status, payload = service.handle_http("POST", {}, b"{}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(mailbox.watch_calls, 0)

    def test_enabled_without_token_does_not_start_the_watch(self):
        settings = type(
            "Settings",
            (),
            {
                "email_logging_enabled": True,
                "me_label": "Me",
                "wife_label": "My wife",
                "telegram_chat_id": None,
            },
        )()
        config = EmailSettings.from_bot_settings(settings)
        self.assertFalse(config.active)

    def test_daily_renewal_only_renews_the_tap(self):
        service, mailbox, state, _writer = service_for(config_with())
        state.set(HISTORY_KEY, "100")
        service.renew_watch()
        self.assertEqual(mailbox.watch_calls, 1)
        self.assertEqual(mailbox.history_calls, 0)
        self.assertEqual(mailbox.recent_calls, 0)
        self.assertEqual(mailbox.fetch_calls, 0)

    def test_paused_renewal_does_not_watch(self):
        service, mailbox, state, _writer = service_for(config_with())
        state.set(HISTORY_KEY, "100")
        service.pause()
        mailbox.watch_calls = 0
        service.renew_watch()
        self.assertEqual(mailbox.stop_calls, 1)
        self.assertEqual(mailbox.watch_calls, 0)

    def test_first_start_asks_about_old_mail_and_does_not_log_it(self):
        service, mailbox, state, writer = service_for(config_with())
        mailbox.recent = ["oldmail"]
        mailbox.messages["oldmail"] = (PURCHASE_SUBJECT, FOODPANDA_BODY, ("person-a@example.com",))
        messages = service.start()
        self.assertEqual(mailbox.watch_calls, 1)
        self.assertEqual(mailbox.fetch_calls, 0)
        self.assertEqual(writer.rows, [])
        self.assertIn("oldmail", state.get(BACKLOG_KEY))
        self.assertTrue(any("not logged" in message.lower() or "mail" in message.lower() for message in messages))

    def test_purchase_refund_statement_and_paynow_follow_the_write_rules(self):
        service, mailbox, state, writer = service_for(config_with())
        state.set(HISTORY_KEY, "100")
        mailbox.added = ["buy", "refund", "statement", "paynow"]
        mailbox.messages = {
            "buy": (PURCHASE_SUBJECT, FOODPANDA_BODY, ("person-a@example.com",)),
            "refund": (REFUND_SUBJECT, REFUND_BODY, ("person-a@example.com",)),
            "statement": (STATEMENT_SUBJECT, "Statement total SGD 900.00", ("person-a@example.com",)),
            "paynow": (PAYNOW_SUBJECT, PAYNOW_BODY, ("person-a@example.com",)),
        }
        sent = []
        service._notify = sent.append
        service.process_notification("999")
        self.assertEqual(writer.updates, [])
        categories = [row.category for row in writer.rows]
        self.assertEqual(categories, ["Food", INCOME_MISC, INCOME_MISC])
        purchase = writer.rows[0]
        self.assertEqual(purchase.description, "fp*Food Panda")
        self.assertEqual(purchase.payment_channel, "Online")
        self.assertEqual(purchase.payment_method, "UOB Sample Visa")
        self.assertEqual(writer.rows[1].transaction_type, "Income")
        self.assertEqual(writer.rows[1].payment_method, "")
        self.assertEqual(writer.rows[2].description, "PayNow")
        self.assertEqual(state.get("gmail_message_id:statement"), "ignored")
        self.assertFalse(any("statement" in text.lower() for text in sent))

    def test_unknown_shop_is_logged_without_asking_for_a_category(self):
        queries = []
        saved = []

        class LookupContext(Context):
            def category_for(self, shop, logged_by):
                return categorize_email_shop(
                    shop,
                    logged_by,
                    "Me",
                    "My wife",
                    self._categories,
                    self.lookup,
                    self.save,
                )

        context = LookupContext(config_with(channels=("All",)), ("Food", "Groceries", INCOME_MISC))

        def lookup(query):
            queries.append(query)
            return "a neighbourhood supermarket"

        def save(keyword, category):
            saved.append((keyword, category))

        context.lookup = lookup
        context.save = save
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
        body = (
            "A transaction of SGD 18.50 was made with your UOB Card ending 1234 "
            "on 05/10/2026 at fp*Sample Depot.\n"
        )
        mailbox.added = ["buy", "refund"]
        mailbox.messages = {
            "buy": (PURCHASE_SUBJECT, body, ("person-a@example.com",)),
            "refund": (REFUND_SUBJECT, REFUND_BODY, ("person-a@example.com",)),
        }
        service.process_notification("999")

        self.assertEqual(queries, ["Sample Depot"])
        self.assertEqual(saved, [("sample depot", "Groceries")])
        self.assertEqual([row.category for row in writer.rows], ["Groceries", INCOME_MISC])
        self.assertEqual(writer.rows[0].description, "fp*Sample Depot")
        self.assertEqual(writer.rows[0].payment_method, "UOB Sample Visa")
        self.assertEqual(writer.rows[1].payment_method, "")
        self.assertNotIn("18.50", queries[0])
        self.assertNotIn("1234", queries[0])

    def test_missing_last4_on_a_live_message_does_not_crash(self):
        service, mailbox, state, writer = service_for(config_with(include_last4=False))
        state.set(HISTORY_KEY, "100")
        mailbox.added = ["buy"]
        mailbox.messages["buy"] = (PURCHASE_SUBJECT, FOODPANDA_BODY, ("person-a@example.com",))
        service.process_notification("999")
        self.assertEqual(writer.rows, [])

    def test_push_carries_a_history_id_and_not_the_mail(self):
        encoded = base64.urlsafe_b64encode(
            json.dumps({"emailAddress": "mailbox@example.com", "historyId": "55"}).encode()
        ).decode()
        body = json.dumps(
            {
                "message": {"data": encoded, "messageId": "1"},
                "subscription": "projects/your-project-id/subscriptions/gmail-push",
            }
        ).encode()
        note = decode_pubsub_notification(body)
        self.assertIsNotNone(note)
        self.assertEqual(note.history_id, "55")
        self.assertNotIn(b"fp*Food Panda", body)

        service, mailbox, state, writer = service_for(config_with())
        state.set(HISTORY_KEY, "50")
        mailbox.added = ["buy"]
        mailbox.messages["buy"] = (PURCHASE_SUBJECT, FOODPANDA_BODY, ("person-a@example.com",))
        status, payload = service.handle_http("POST", {"authorization": "Bearer test-token"}, body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(mailbox.fetch_calls, 1)
        self.assertEqual(writer.rows[0].category, "Food")
        self.assertEqual(mailbox.start_history, "50")


if __name__ == "__main__":
    unittest.main()
