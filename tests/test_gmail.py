from __future__ import annotations

import base64
import json
import time
import unittest
from datetime import date
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from googleapiclient.errors import HttpError

from getrichbot import categories as category_lists
from getrichbot.dashboard_http import DashboardApp
from getrichbot.dashboard_http import DashboardContext
from getrichbot.email_mail import INCOME_MISC
from getrichbot.email_mail import email_logged_text
from getrichbot.gmail_api import GmailApiMailbox
from getrichbot.gmail_watch import BACKLOG_KEY
from getrichbot.gmail_watch import HISTORY_KEY
from getrichbot.gmail_watch import BotEmailContext
from getrichbot.gmail_watch import EmailService
from getrichbot.gmail_watch import EmailSettings
from getrichbot.gmail_watch import WriteOutcome
from getrichbot.gmail_watch import decode_pubsub_notification
from getrichbot.shop_category import categorize_email_shop
from test_email_mail import FOODPANDA_BODY
from test_email_mail import PAYNOW_BODY
from test_email_mail import PAYNOW_SUBJECT
from test_email_mail import PURCHASE_SUBJECT
from test_email_mail import REFUND_BODY
from test_email_mail import REFUND_SUBJECT
from test_email_mail import SINGAPORE
from test_email_mail import STATEMENT_SUBJECT
from test_email_mail import Context
from test_email_mail import FakeMailbox
from test_email_mail import FakeWriter
from test_email_mail import MemoryState
from test_email_mail import active_settings
from test_email_mail import config_with
from test_email_mail import service_for
from getrichbot.cards import parse_payment_config


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
        service._notifier_bound = True
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
            def category_for(self, shop, logged_by, body=""):
                del body
                return categorize_email_shop(
                    shop,
                    logged_by,
                    "Me",
                    "My wife",
                    self._categories,
                    self.lookup,
                    self.save,
                    self.ask,
                )

        context = LookupContext(config_with(channels=("All",)), ("Food", "Groceries", INCOME_MISC))

        def lookup(query):
            queries.append(query)
            return "a neighbourhood supermarket"

        def save(keyword, category):
            saved.append((keyword, category))

        def ask(shop, description, choices):
            self.assertEqual(shop, "Sample Depot")
            self.assertNotIn("18.50", description)
            self.assertNotIn("1234", description)
            self.assertIn("Groceries", choices)
            if "supermarket" in description:
                return "Groceries"
            return ""

        context.lookup = lookup
        context.save = save
        context.ask = ask
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

    def test_push_before_the_reader_or_history_id_is_a_retry(self):
        encoded = base64.urlsafe_b64encode(
            json.dumps({"emailAddress": "mailbox@example.com", "historyId": "55"}).encode()
        ).decode()
        body = json.dumps(
            {
                "message": {"data": encoded, "messageId": "1"},
                "subscription": "projects/your-project-id/subscriptions/gmail-push",
            }
        ).encode()
        app = DashboardApp(
            DashboardContext(
                bot_token="test-token",
                allowed_user_ids={111},
                label_for_user=lambda user_id: "Alex",
                raw_sheet="Raw Expenses",
                card_usage_sheet="Card Usage",
                payment_methods_sheet="Payment Methods",
                card_limits_sheet="Card Limits",
                sheets=object(),
            )
        )
        status, _headers, payload = app.handle("POST", "/gmail/push", {}, body, secure=False)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(payload)["ok"], False)
        self.assertFalse(hasattr(app, "_held_gmail_pushes"))

        empty_status, _empty_headers, empty_payload = app.handle("POST", "/gmail/push", {}, b"", secure=False)
        self.assertEqual(empty_status, 503)
        self.assertEqual(json.loads(empty_payload)["ok"], False)

        previous_source = category_lists.CONFIG_SOURCE
        category_lists.CONFIG_SOURCE = "fallback"
        try:
            sheet_context = BotEmailContext(type("Bot", (), {})())
            self.assertEqual(sheet_context.categories(), ())
            self.assertIsNone(sheet_context.category_for("Sample Shop", "Alex", "card ending 1111"))
        finally:
            category_lists.CONFIG_SOURCE = previous_source

        config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active", "Last 4"],
                ["Sample Visa", "Alex", "Credit Card", "Calendar", "1", "TRUE", "1111"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
                ["Sample Visa", "Alex", "All", "All", "", "TRUE"],
            ],
        )

        class NotReadyContext:
            def __init__(self, payment_config):
                self.payment_config = payment_config

            def methods(self):
                return self.payment_config.payment_methods

            def categories(self):
                return ()

            def has_income_misc(self):
                return False

            def channels_for(self, owner, payment_method):
                return self.payment_config.channel_options_for(owner, payment_method)

            def category_for(self, shop, logged_by, body=""):
                return None

        mailbox = FakeMailbox()
        state = MemoryState()
        writer = FakeWriter()

        def write(row):
            writer.rows.append(row)
            return WriteOutcome(logged=True, text=email_logged_text(row))

        service = EmailService(
            active_settings(),
            mailbox,
            state,
            NotReadyContext(config),
            write,
            authorize=lambda _headers: True,
            now=lambda: datetime(2026, 10, 5, 9, 0, tzinfo=SINGAPORE),
        )
        mail = (
            "A transaction of SGD 18.50 was made with your card ending 1111 "
            "on 05/10/2026 at Sample Shop.\n"
        )
        mailbox.added = ["buy"]
        mailbox.messages["buy"] = ("card transaction alert", mail, ("person-a@example.com",))

        app.gmail_push = service.handle_http
        waiting, waiting_payload = service.handle_http("POST", {"authorization": "Bearer test-token"}, body)
        self.assertEqual(waiting, 503)
        self.assertFalse(waiting_payload["ok"])
        self.assertEqual(writer.rows, [])
        self.assertEqual(mailbox.fetch_calls, 0)

        state.set(HISTORY_KEY, "50")
        status, _headers, payload = app.handle(
            "POST",
            "/gmail/push",
            {"authorization": "Bearer test-token"},
            body,
            secure=False,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["ok"], True)
        self.assertEqual(state.get(BACKLOG_KEY), None)
        self.assertEqual(len(writer.rows), 1)
        row = writer.rows[0]
        self.assertEqual(row.amount, Decimal("18.50"))
        self.assertEqual(row.description, "Sample Shop")
        self.assertEqual(row.payment_method, "Sample Visa")
        self.assertEqual(row.payment_owner, "Alex")
        self.assertEqual(row.category, "")
        self.assertEqual(row.timestamp.date(), date(2026, 10, 5))
        self.assertEqual(len(service._queued_notices), 1)
        self.assertIn("Sample Shop", service._queued_notices[0])
        self.assertIn("Sample Visa", service._queued_notices[0])
        self.assertIn("Category is missing", service._queued_notices[0])
        self.assertEqual(service.handle_command("log email backlog"), ["There is no mail pile waiting."])

    def test_open_gmail_tap_does_not_move_the_history_bookmark(self):
        service, mailbox, state, _writer = service_for(config_with())
        state.set(HISTORY_KEY, "50")
        self.assertTrue(service.open_gmail_tap())
        self.assertEqual(mailbox.watch_calls, 1)
        self.assertEqual(mailbox.history_calls, 0)
        self.assertEqual(state.get(HISTORY_KEY), "50")

    def test_open_gmail_tap_keeps_trying_until_a_history_id_is_saved(self):
        service, mailbox, state, _writer = service_for(config_with())
        mailbox.next_history = ""
        self.assertFalse(service.open_gmail_tap())
        self.assertEqual(mailbox.watch_calls, 1)
        self.assertIsNone(state.get(HISTORY_KEY))
        mailbox.next_history = "80"
        self.assertTrue(service.open_gmail_tap())
        self.assertEqual(state.get(HISTORY_KEY), "80")

    def test_tap_without_telegram_logs_a_purchase_ping(self):
        config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active", "Last 4"],
                ["Sample Visa", "Alex", "Credit Card", "Calendar", "1", "TRUE", "1111"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
                ["Sample Visa", "Alex", "All", "All", "", "TRUE"],
            ],
        )

        class QuietCategory:
            def __init__(self, payment_config):
                self.payment_config = payment_config

            def methods(self):
                return self.payment_config.payment_methods

            def categories(self):
                return ()

            def has_income_misc(self):
                return False

            def channels_for(self, owner, payment_method):
                return self.payment_config.channel_options_for(owner, payment_method)

            def category_for(self, shop, logged_by, body=""):
                return None

        mailbox = FakeMailbox()
        mailbox.next_history = "80"
        state = MemoryState()
        writer = FakeWriter()

        def write(row):
            writer.rows.append(row)
            return WriteOutcome(logged=True, text=email_logged_text(row))

        service = EmailService(
            active_settings(),
            mailbox,
            state,
            QuietCategory(config),
            write,
            authorize=lambda _headers: True,
            now=lambda: datetime(2026, 10, 5, 9, 0, tzinfo=SINGAPORE),
        )
        self.assertFalse(service._notifier_bound)
        self.assertIsNone(state.get(HISTORY_KEY))
        mail = (
            "A transaction of SGD 18.50 was made with your card ending 1111 "
            "on 05/10/2026 at Sample Shop.\n"
        )
        mailbox.added = ["buy"]
        mailbox.messages["buy"] = ("card transaction alert", mail, ("person-a@example.com",))
        encoded = base64.urlsafe_b64encode(
            json.dumps({"emailAddress": "mailbox@example.com", "historyId": "81"}).encode()
        ).decode()
        body = json.dumps(
            {
                "message": {"data": encoded, "messageId": "1"},
                "subscription": "projects/your-project-id/subscriptions/gmail-push",
            }
        ).encode()

        early_status, early_payload = service.handle_http("POST", {"authorization": "Bearer test-token"}, body)
        self.assertEqual(early_status, 503)
        self.assertFalse(early_payload["ok"])
        self.assertEqual(writer.rows, [])

        service.start_tap()
        deadline = time.monotonic() + 2
        while state.get(HISTORY_KEY) != "80" and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(state.get(HISTORY_KEY), "80")
        self.assertFalse(service._notifier_bound)
        status, payload = service.handle_http("POST", {"authorization": "Bearer test-token"}, body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(writer.rows), 1)
        row = writer.rows[0]
        self.assertEqual(row.amount, Decimal("18.50"))
        self.assertEqual(row.description, "Sample Shop")
        self.assertEqual(row.payment_method, "Sample Visa")
        self.assertEqual(row.timestamp.date(), date(2026, 10, 5))
        self.assertEqual(len(service._queued_notices), 1)
        self.assertIn("Sample Shop", service._queued_notices[0])
        self.assertEqual(state.get(BACKLOG_KEY), None)

    def test_missing_message_is_skipped_and_the_later_purchase_logs(self):
        service, mailbox, state, writer = service_for(config_with())
        state.set(HISTORY_KEY, "100")
        mailbox.next_history = "250"
        mailbox.added = ["gone", "buy"]
        mailbox.messages["buy"] = (PURCHASE_SUBJECT, FOODPANDA_BODY, ("person-a@example.com",))
        original_fetch = mailbox.fetch

        def fetch(message_id):
            if message_id == "gone":
                mailbox.fetch_calls += 1
                raise _http_error(404, "Not Found")
            return original_fetch(message_id)

        mailbox.fetch = fetch
        sent = []
        service._notify = sent.append
        service._notifier_bound = True

        status, payload = service.handle_http("POST", {"authorization": "Bearer test-token"}, _push_body("250"))

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual([row.description for row in writer.rows], ["fp*Food Panda"])
        self.assertEqual(state.get("gmail_message_id:gone"), "skipped")
        self.assertEqual(state.get(HISTORY_KEY), "250")
        self.assertEqual(sent, ["logged Food"])

    def test_sheet_limit_waits_a_minute_before_reading_again(self):
        service, mailbox, _state, _writer = service_for(config_with())
        sheet = _LimitingSheet()
        sheet.values[HISTORY_KEY] = "100"
        service.state = sheet
        mailbox.added = []
        body = _push_body("200")
        clock = {"now": 1_000.0}

        with patch("getrichbot.gmail_watch.time.monotonic", lambda: clock["now"]):
            status, payload = service.handle_http("POST", {"authorization": "Bearer test-token"}, body)
            self.assertEqual(status, 503)
            self.assertFalse(payload["ok"])
            self.assertEqual(sheet.calls, 1)
            self.assertEqual(mailbox.history_calls, 0)

            clock["now"] = 1_059.0
            status, payload = service.handle_http("POST", {"authorization": "Bearer test-token"}, body)
            self.assertEqual(status, 503)
            self.assertFalse(payload["ok"])
            self.assertEqual(sheet.calls, 1)
            self.assertEqual(mailbox.history_calls, 0)

            clock["now"] = 1_060.0
            sheet.limited = False
            status, payload = service.handle_http("POST", {"authorization": "Bearer test-token"}, body)
            self.assertGreater(sheet.calls, 1)
            self.assertEqual(mailbox.history_calls, 1)
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])


def _http_error(status: int, reason: str) -> HttpError:
    response = type("Response", (), {"status": status, "reason": reason})()
    return HttpError(response, b"")


def _push_body(history_id: str) -> bytes:
    encoded = base64.urlsafe_b64encode(
        json.dumps({"emailAddress": "mailbox@example.com", "historyId": history_id}).encode()
    ).decode()
    return json.dumps({"message": {"data": encoded, "messageId": "1"}, "subscription": "sub"}).encode()


class _LimitingSheet:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.calls = 0
        self.limited = True

    def get(self, key: str):
        self.calls += 1
        if self.limited:
            raise _http_error(429, "Read requests per minute per user")
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.calls += 1
        if self.limited:
            raise _http_error(429, "Read requests per minute per user")
        self.values[key] = value


class _HistoryCall:
    def __init__(self, respond):
        self.respond = respond
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.respond(kwargs)
        return type("Request", (), {"execute": lambda _self: payload})()


class _Users:
    def __init__(self, history):
        self._history = history

    def history(self):
        return self._history


class _GmailService:
    def __init__(self, history):
        self._users = _Users(history)

    def users(self):
        return self._users


def _inbox_history(kwargs):
    """Gmail drops the useful record when the list is filtered, and otherwise omits messagesAdded."""
    if kwargs.get("labelId") or kwargs.get("historyTypes"):
        return {"historyId": "200"}
    return {
        "historyId": "200",
        "history": [
            {"id": "150", "messages": [{"id": "bare-mail", "threadId": "bare-mail"}]},
            {
                "id": "160",
                "labelsAdded": [
                    {"message": {"id": "inbox-mail", "threadId": "thread-inbox"}, "labelIds": ["INBOX"]}
                ],
            },
            {
                "id": "170",
                "labelsAdded": [
                    {"message": {"id": "sent-mail", "threadId": "thread-sent"}, "labelIds": ["SENT"]}
                ],
            },
            {
                "id": "180",
                "messagesDeleted": [{"message": {"id": "gone-mail", "threadId": "thread-gone"}}],
            },
            {"id": "190", "messagesAdded": [{"message": {"id": "added-mail", "threadId": "thread-added"}}]},
        ],
    }


class GmailHistoryTests(unittest.TestCase):
    def test_inbox_mail_is_found_when_gmail_omits_messages_added(self):
        history = _HistoryCall(_inbox_history)
        mailbox = GmailApiMailbox("client", "secret", "refresh")
        mailbox._service = _GmailService(history)

        message_ids, history_id = mailbox.added_since("100")

        self.assertEqual(message_ids, ["bare-mail", "inbox-mail", "added-mail"])
        self.assertEqual(history_id, "200")
        self.assertEqual(len(history.calls), 1)
        self.assertNotIn("labelId", history.calls[0])
        self.assertNotIn("historyTypes", history.calls[0])

    def test_inbox_mail_is_found_when_inbox_is_only_on_the_message(self):
        def inbox_on_the_message(_kwargs):
            return {
                "historyId": "200",
                "history": [
                    {
                        "id": "160",
                        "messages": [{"id": "new-mail", "threadId": "thread-new"}],
                        "labelsAdded": [
                            {
                                "message": {
                                    "id": "new-mail",
                                    "threadId": "thread-new",
                                    "labelIds": ["UNREAD", "CATEGORY_UPDATES", "INBOX"],
                                },
                                "labelIds": ["UNREAD", "CATEGORY_UPDATES"],
                            }
                        ],
                    },
                    {
                        "id": "170",
                        "messages": [{"id": "sent-mail", "threadId": "thread-sent"}],
                        "labelsAdded": [
                            {
                                "message": {
                                    "id": "sent-mail",
                                    "threadId": "thread-sent",
                                    "labelIds": ["SENT"],
                                },
                                "labelIds": ["UNREAD"],
                            }
                        ],
                    },
                ],
            }

        history = _HistoryCall(inbox_on_the_message)
        mailbox = GmailApiMailbox("client", "secret", "refresh")
        mailbox._service = _GmailService(history)

        message_ids, history_id = mailbox.added_since("100")

        self.assertEqual(message_ids, ["new-mail"])
        self.assertEqual(history_id, "200")

    def test_extra_history_page_does_not_move_past_mail_that_was_not_listed(self):
        pages = []
        for index in range(6):
            page = {
                "historyId": "999",
                "history": [
                    {
                        "id": str(150 + index),
                        "messagesAdded": [
                            {
                                "message": {
                                    "id": f"mail-{index}",
                                    "threadId": f"mail-{index}",
                                    "labelIds": ["INBOX"],
                                }
                            }
                        ],
                    }
                ],
            }
            if index < 5:
                page["nextPageToken"] = f"page-{index + 1}"
            pages.append(page)

        def respond(kwargs):
            token = kwargs.get("pageToken")
            if not token:
                return pages[0]
            return pages[int(str(token).split("-")[1])]

        history = _HistoryCall(respond)
        mailbox = GmailApiMailbox("client", "secret", "refresh")
        mailbox._service = _GmailService(history)

        message_ids, history_id = mailbox.added_since("100")

        self.assertEqual(message_ids, [f"mail-{index}" for index in range(5)])
        self.assertEqual(history_id, "154")
        self.assertNotEqual(history_id, "999")

    def test_push_logs_the_mail_gmail_left_out_of_messages_added(self):
        def bare_only(_kwargs):
            return {
                "historyId": "200",
                "history": [
                    {"id": "150", "messages": [{"id": "bare-mail", "threadId": "bare-mail"}]},
                ],
            }

        history = _HistoryCall(bare_only)
        listing = GmailApiMailbox("client", "secret", "refresh")
        listing._service = _GmailService(history)
        mail = (
            "A transaction of SGD 18.50 was made with your card ending 1111 "
            "on 05/10/2026 at Sample Shop.\n"
        )

        class Mailbox:
            def added_since(self, start_history_id):
                return listing.added_since(start_history_id)

            def fetch(self, message_id):
                self.fetched = message_id
                return ("card transaction alert", mail, ("person-a@example.com",))

        config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active", "Last 4"],
                ["Sample Visa", "Alex", "Credit Card", "Calendar", "1", "TRUE", "1111"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
            ],
        )
        state = MemoryState()
        state.set(HISTORY_KEY, "100")
        writer = FakeWriter()
        mailbox = Mailbox()

        def write(row):
            writer.rows.append(row)
            return WriteOutcome(logged=True, text=email_logged_text(row))

        service = EmailService(
            active_settings(),
            mailbox,
            state,
            Context(config, ()),
            write,
            authorize=lambda _headers: True,
            now=lambda: datetime(2026, 10, 5, 9, 0, tzinfo=SINGAPORE),
        )
        encoded = base64.urlsafe_b64encode(
            json.dumps({"emailAddress": "mailbox@example.com", "historyId": "200"}).encode()
        ).decode()
        body = json.dumps({"message": {"data": encoded, "messageId": "1"}, "subscription": "sub"}).encode()

        status, payload = service.handle_http("POST", {"authorization": "Bearer test-token"}, body)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(mailbox.fetched, "bare-mail")
        self.assertEqual(len(writer.rows), 1)
        self.assertEqual(writer.rows[0].description, "Sample Shop")
        self.assertEqual(writer.rows[0].amount, Decimal("18.50"))
        self.assertEqual(len(service._queued_notices), 1)
        self.assertIn("Sample Shop", service._queued_notices[0])


if __name__ == "__main__":
    unittest.main()
