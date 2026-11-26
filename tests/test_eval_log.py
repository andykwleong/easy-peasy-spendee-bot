from __future__ import annotations

import json
import unittest
from datetime import date
from decimal import Decimal

from getrichbot.email_mail import DECIDE_MAIL_REASONS
from getrichbot.email_mail import INCOME_MISC
from getrichbot.email_mail import REASON_LOGGED
from getrichbot.email_mail import REASON_NO_AMOUNT
from getrichbot.email_mail import REASON_NO_CARD
from getrichbot.email_mail import REASON_NO_CARD_MATCH
from getrichbot.email_mail import REASON_NO_DATE
from getrichbot.email_mail import REASON_NO_INCOME_MISC
from getrichbot.email_mail import REASON_NOT_FOUND
from getrichbot.email_mail import REASON_REVERSAL
from getrichbot.email_mail import REASON_REVERSAL_MISSING
from getrichbot.email_mail import REASON_SALARY
from getrichbot.email_mail import REASON_STATEMENT
from getrichbot.email_mail import decide_mail
from getrichbot.eval_log import PLAIN_REASONS
from getrichbot.eval_log import approved_from_state_rows
from getrichbot.eval_log import category_after_choice
from getrichbot.eval_log import diagnosis_for
from getrichbot.eval_log import diagnosis_state_key
from getrichbot.eval_log import eval_sheet_row
from getrichbot.eval_log import eval_view
from getrichbot.eval_log import open_mail_diagnosis
from getrichbot.eval_log import group_message_rows
from getrichbot.eval_log import handled_reversal_row_numbers
from getrichbot.eval_log import is_category_miss
from getrichbot.eval_log import limit_mail_text
from getrichbot.eval_log import mask_card_digits
from getrichbot.eval_log import parse_eval_rows
from getrichbot.eval_log import remember_category_choice
from getrichbot.eval_log import whole_percent
from getrichbot.gmail_watch import HISTORY_KEY
from getrichbot.group_post import MESSAGE_FAIL
from getrichbot.group_post import MESSAGE_SENT
from getrichbot.models import ExpenseRecord
from getrichbot.sheet_changes import SheetChanges
from getrichbot.sheets import SheetsClient
from test_dashboard_http import FakeSheets
from test_dashboard_http import app_for
from test_dashboard_http import body_json
from test_dashboard_http import sign_webapp
from test_email_mail import FOODPANDA_BODY
from test_email_mail import PURCHASE_SUBJECT
from test_email_mail import REFUND_BODY
from test_email_mail import REFUND_SUBJECT
from test_email_mail import STATEMENT_SUBJECT
from test_email_mail import Context
from test_email_mail import config_with
from test_email_mail import decide
from test_email_mail import service_for


class DecideMailReasonTests(unittest.TestCase):
    def test_each_decide_mail_reason_code(self):
        config = config_with(last4="1111")
        dated_shop = "\n".join(
            (
                "Date & Time: 10 Oct 2026 22:42 (SGT)",
                "Amount: SGD18.50",
                "To: SAMPLE SHOP",
            )
        )
        found = {
            decide("Security alert", "We noticed a sign-in from a new device.", config).reason: REASON_NO_AMOUNT,
            decide("Card transaction", "From: card ending 1111\nTo: SAMPLE SHOP\n", config).reason: REASON_NO_AMOUNT,
            decide(
                "Card transaction",
                "Amount: SGD18.50\nFrom: card ending 1111\nTo: SAMPLE SHOP\n",
                config,
            ).reason: REASON_NO_DATE,
            decide("Card transaction", dated_shop, config).reason: REASON_NO_CARD,
            decide(
                "Card transaction",
                dated_shop + "\nFrom: card ending 9999\n",
                config,
            ).reason: REASON_NO_CARD_MATCH,
            decide("Salary payment", "Salary of SGD 1000.00 was paid.", config).reason: REASON_SALARY,
            decide(STATEMENT_SUBJECT, "Statement total SGD 900.00", config).reason: REASON_STATEMENT,
            decide(
                "Card transaction",
                "This is a reversal.\nAmount: SGD18.50\nFrom: card ending 1111\nTo: SAMPLE SHOP\n",
                config,
            ).reason: REASON_REVERSAL,
            decide(REFUND_SUBJECT, REFUND_BODY, config, categories=("Food",)).reason: REASON_NO_INCOME_MISC,
            decide(PURCHASE_SUBJECT, FOODPANDA_BODY, config_with()).reason: REASON_LOGGED,
        }
        self.assertEqual(set(found), set(DECIDE_MAIL_REASONS))
        logged = decide(PURCHASE_SUBJECT, FOODPANDA_BODY, config_with())
        self.assertEqual(logged.action, "log")
        self.assertEqual(logged.reason, REASON_LOGGED)

    def test_category_source_is_keyword_grab_exa_or_none(self):
        config = config_with(last4="1111")
        dated = "\n".join(
            (
                "Date & Time: 10 Oct 2026 22:42 (SGT)",
                "Amount: SGD18.50",
                "From: card ending 1111",
            )
        )
        keyword = decide_mail(
            "Card transaction",
            dated + "\nTo: dinner place\n",
            ("person-a@example.com",),
            forwarders={"person-a@example.com": "Me"},
            methods=config.payment_methods,
            categories=("Food", "Groceries", INCOME_MISC),
            channels_for=config.channel_options_for,
            category_for=lambda *_args: "Food",
            today=date(2026, 10, 5),
        )
        grab = decide_mail(
            "Card transaction",
            dated + "\nTo: GPAY NETWORK\n",
            ("person-a@example.com",),
            forwarders={"person-a@example.com": "Me"},
            methods=config.payment_methods,
            categories=("Transport", INCOME_MISC),
            channels_for=config.channel_options_for,
            category_for=lambda *_args: "Food",
            today=date(2026, 10, 5),
        )
        exa = decide_mail(
            "Card transaction",
            dated + "\nTo: SAMPLE DEPOT\n",
            ("person-a@example.com",),
            forwarders={"person-a@example.com": "Me"},
            methods=config.payment_methods,
            categories=("Food", "Groceries", INCOME_MISC),
            channels_for=config.channel_options_for,
            category_for=lambda *_args: ("Groceries", "exa_ai"),
            today=date(2026, 10, 5),
        )
        blank = decide_mail(
            "Card transaction",
            dated + "\nTo: SAMPLE DEPOT\n",
            ("person-a@example.com",),
            forwarders={"person-a@example.com": "Me"},
            methods=config.payment_methods,
            categories=("Food", "Groceries", INCOME_MISC),
            channels_for=config.channel_options_for,
            category_for=lambda *_args: "",
            today=date(2026, 10, 5),
        )
        self.assertEqual(keyword.category_source, "keyword")
        self.assertEqual(keyword.category, "Food")
        self.assertEqual(grab.category_source, "grab")
        self.assertEqual(grab.category, "Transport")
        self.assertEqual(exa.category_source, "exa_ai")
        self.assertEqual(exa.category, "Groceries")
        self.assertEqual(blank.category_source, "none")
        self.assertEqual(blank.category, "")
        self.assertFalse(is_category_miss("", "", "none"))


class AccuracyTests(unittest.TestCase):
    def test_first_choice_is_not_a_miss_and_a_later_change_is(self):
        first, final = category_after_choice("", "", "Food")
        self.assertEqual((first, final), ("Food", "Food"))
        self.assertFalse(is_category_miss(first, final, "none"))
        self.assertFalse(is_category_miss("", "Food", "keyword"))

        changed_first, changed_final = category_after_choice(first, final, "Transport")
        self.assertEqual((changed_first, changed_final), ("Food", "Transport"))
        self.assertTrue(is_category_miss(changed_first, changed_final, "none"))
        self.assertTrue(is_category_miss("Food", "Transport", "exa_ai"))
        self.assertFalse(is_category_miss("Food", "Food", "grab"))

        kept, same = category_after_choice("Food", "Transport", "Food")
        self.assertEqual((kept, same), ("Food", "Food"))
        self.assertFalse(is_category_miss(kept, same, "keyword"))

    def test_percent_uses_every_email_purchase_and_skips_an_empty_period(self):
        rows = [
            ["2026-11-03", "m1", "Bank", "Shop", "pass", "logged", "aaa111", "Food", "none", "Food"],
            ["2026-11-03", "m2", "Bank", "Other", "pass", "logged", "bbb222", "Food", "keyword", "Transport"],
            ["2026-11-03", "m3", "Bank", "Income", "pass", "logged", "ccc333", "", "", ""],
            ["2026-11-03", "m4", "Bank", "Quiet", "fail", "statement", "", "", "", ""],
        ]
        parsed = parse_eval_rows(rows)
        purchases = [row for row in parsed if row.purchase]
        kept = [row for row in purchases if not is_category_miss(row.first_category, row.final_category, row.category_source)]
        self.assertEqual(len(purchases), 2)
        self.assertEqual(len(kept), 1)
        self.assertEqual(whole_percent(len(kept), len(purchases)), 50)
        self.assertIsNone(whole_percent(0, 0))
        self.assertEqual(whole_percent(1, 8), 13)


class EvalSheetRowTests(unittest.TestCase):
    def test_row_keeps_the_subject_and_drops_the_body_and_card_number(self):
        card = "4111111111111111"
        row = eval_sheet_row(
            logged_on="2026-11-03",
            message_id="mail-1",
            sender="Bank alerts",
            subject="Card " + card + " at Sample Shop",
            result="pass",
            reason=REASON_LOGGED,
            entry_id="abc123",
            first_category="Food",
            category_source="keyword",
            final_category="Food",
            purchase=True,
        )
        self.assertNotIn(card, " ".join(row))
        self.assertNotIn("the rest of the email body", " ".join(row))
        self.assertIn("**** **** **** 1111", row[3])
        self.assertLessEqual(len(row[3]), 150)
        self.assertEqual(row[4], "pass")
        self.assertEqual(row[8], "keyword")
        self.assertEqual(mask_card_digits("ending 1234 and SGD 18.50"), "ending 1234 and SGD 18.50")
        shown = limit_mail_text("Paid with " + card + ".")
        self.assertNotIn(card, shown)
        self.assertIn("1111", shown)


class EvalWriteTests(unittest.TestCase):
    def test_each_handled_message_gets_one_row_and_a_failed_eval_write_still_logs(self):
        service, mailbox, state, writer = service_for(config_with())
        recorded = []
        service.eval_log = recorded.append
        state.set(HISTORY_KEY, "100")
        mailbox.added = ["buy", "statement", "gone"]
        mailbox.messages = {
            "buy": (PURCHASE_SUBJECT, FOODPANDA_BODY, ("person-a@example.com",)),
            "statement": (STATEMENT_SUBJECT, "Statement total SGD 900.00", ("person-a@example.com",)),
        }

        def fetch(message_id):
            if message_id == "gone":
                raise type("Missing", (Exception,), {"status_code": 404})()
            return mailbox.messages[message_id]

        mailbox.fetch = fetch
        service.process_notification("999")
        self.assertEqual([row.category for row in writer.rows], ["Food"])
        self.assertEqual([row[5] for row in recorded], [REASON_LOGGED, REASON_STATEMENT, REASON_NOT_FOUND])
        self.assertNotIn("SGD 18.50", json.dumps(recorded))
        self.assertNotIn("was made with your UOB Card", json.dumps(recorded))

        blocked = service_for(config_with())
        blocked_service, blocked_mailbox, blocked_state, blocked_writer = blocked
        blocked_state.set(HISTORY_KEY, "100")
        blocked_mailbox.added = ["buy"]
        blocked_mailbox.messages["buy"] = (PURCHASE_SUBJECT, FOODPANDA_BODY, ("person-a@example.com",))

        def boom(_row):
            raise RuntimeError("eval sheet down")

        blocked_service.eval_log = boom
        blocked_service.process_notification("999")
        self.assertEqual(len(blocked_writer.rows), 1)

    def test_a_reversal_that_removes_the_charge_is_not_counted_as_not_logged(self):
        config = config_with(last4="1111")
        context = Context(config, ("Food", INCOME_MISC))
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
        context.records = [original]
        context.deleted = []

        def expense_records():
            return list(context.records)

        def delete_expense(entry_id):
            context.deleted.append(entry_id)
            context.records = [record for record in context.records if record.entry_id != entry_id]
            return True

        context.expense_records = expense_records
        context.delete_expense = delete_expense
        service, mailbox, state, writer = service_for(config)
        service.context = context
        recorded = []
        service.eval_log = recorded.append
        sent = []
        service._notify = sent.append
        service._notifier_bound = True
        state.set(HISTORY_KEY, "100")
        body = "\n".join(
            (
                "This is a reversal.",
                "Account number XXXX-XXXX-XXXX-1111",
                "Transaction date 12/10/26",
                "Transaction amount SGD165.00",
                "Transaction details SAMPLE SHOP",
            )
        )
        mailbox.added = ["reverse"]
        mailbox.messages["reverse"] = ("Card 4111111111111111", body, ("person-a@example.com",))
        service.process_notification("999")

        self.assertEqual(writer.rows, [])
        self.assertEqual(context.deleted, ["ab12cd"])
        self.assertEqual(len(sent), 1)
        self.assertIn("The original charge was reversed and removed.", sent[0])
        self.assertEqual(len(recorded), 1)
        row = recorded[0]
        self.assertEqual(row[4], "pass")
        self.assertEqual(row[5], REASON_REVERSAL)
        self.assertEqual(row[6], "")
        joined = " ".join(row)
        self.assertNotIn("4111111111111111", joined)
        self.assertNotIn("XXXX-XXXX-XXXX-1111", joined)
        self.assertNotIn("This is a reversal", joined)
        self.assertNotIn("SGD165.00", joined)
        view = eval_view([row], [], [], {}, date(2026, 10, 5))
        self.assertEqual(view["emails"][0]["result"], "pass")
        self.assertEqual([item for item in view["emails"] if item["result"] != "pass"], [])
        self.assertEqual(view["purchases"], [])

        def boom(_row):
            raise RuntimeError("eval sheet down")

        context.records = [original]
        service.eval_log = boom
        mailbox.added = ["reverse-again"]
        mailbox.messages["reverse-again"] = ("Card transaction", body, ("person-a@example.com",))
        service.process_notification("1000")
        self.assertEqual(context.deleted, ["ab12cd", "ab12cd"])
        self.assertEqual(writer.rows, [])

    def test_a_reversal_with_no_original_charge_is_reversal_missing(self):
        context = Context(config_with(last4="1111"), ("Food", INCOME_MISC))
        context.expense_records = lambda: []
        context.delete_expense = lambda _entry_id: False
        service, mailbox, state, writer = service_for(config_with(last4="1111"))
        service.context = context
        recorded = []
        service.eval_log = recorded.append
        state.set(HISTORY_KEY, "100")
        mailbox.added = ["reverse"]
        mailbox.messages["reverse"] = (
            "Card transaction",
            "\n".join(
                (
                    "This is a reversal.",
                    "Account number XXXX-XXXX-XXXX-1111",
                    "Transaction date 12/10/26",
                    "Transaction amount SGD165.00",
                    "Transaction details SAMPLE SHOP",
                )
            ),
            ("person-a@example.com",),
        )
        service.process_notification("999")
        self.assertEqual(writer.rows, [])
        self.assertEqual(recorded[0][4], "fail")
        self.assertEqual(recorded[0][5], REASON_REVERSAL_MISSING)
        self.assertEqual(recorded[0][6], "")

    def test_a_failed_category_update_does_not_raise(self):
        class Sheets:
            def update_eval_category(self, _entry_id, _category):
                raise RuntimeError("eval sheet down")

        remember_category_choice(Sheets(), "abc123", "Food")


class OldReversalRowTests(unittest.TestCase):
    def test_a_saved_removal_is_marked_handled_and_a_missing_charge_stays_not_logged(self):
        card = "4111111111111111"
        removed = [
            "2026-10-12",
            "mail-old",
            "Sample Bank",
            "Card " + card + " reversal notice",
            "fail",
            REASON_REVERSAL,
            "",
            "",
            "",
            "",
        ]
        missing = [
            "2026-10-12",
            "mail-miss",
            "Sample Bank",
            "A reversal notice",
            "fail",
            REASON_REVERSAL_MISSING,
            "",
            "",
            "",
            "",
        ]
        other = [
            "2026-10-12",
            "mail-quiet",
            "Sample Bank",
            "This is a reversal.",
            "fail",
            REASON_NO_AMOUNT,
            "",
            "",
            "",
            "",
        ]
        already = [
            "2026-10-13",
            "mail-new",
            "Sample Bank",
            "Later reversal",
            "pass",
            REASON_REVERSAL,
            "",
            "",
            "",
            "",
        ]
        rows = [removed, [], missing, other, already]
        self.assertEqual(handled_reversal_row_numbers(rows), [2])

        service = _EvalSheet(rows)
        changes = SheetChanges(watched_sheets=frozenset({"Eval Log"}))
        client = SheetsClient("sheet-id", changes=changes)
        client.service = service
        before = changes.mark()

        updated = client.mark_handled_reversals()

        self.assertEqual(len(service.writes), 1)
        written = service.writes[0]["body"]["data"]
        self.assertEqual(written, [{"range": "Eval Log!E2", "values": [["pass"]]}])
        dumped = json.dumps(service.writes)
        self.assertNotIn(card, dumped)
        self.assertNotIn("This is a reversal", dumped)
        self.assertNotIn("SGD", dumped)
        self.assertNotEqual(changes.mark(), before)
        self.assertEqual(updated[0][4], "pass")
        self.assertEqual(updated[0][5], REASON_REVERSAL)
        self.assertEqual(updated[0][3], removed[3])
        self.assertEqual(updated[2][4], "fail")
        self.assertEqual(updated[2][5], REASON_REVERSAL_MISSING)
        self.assertEqual(updated[3][4], "fail")
        self.assertEqual(updated[3][5], REASON_NO_AMOUNT)
        self.assertEqual(updated[4][4], "pass")
        view = eval_view(updated, [], [], {}, date(2026, 10, 14))
        not_logged = [item for item in view["emails"] if item["result"] != "pass"]
        self.assertEqual([item["subject"] for item in not_logged], ["A reversal notice", "This is a reversal."])
        self.assertEqual(view["purchases"], [])

        again = client.mark_handled_reversals()
        self.assertEqual(len(service.writes), 1)
        self.assertEqual(again[0][4], "pass")
        self.assertEqual([item for item in eval_view(again, [], [], {}, date(2026, 10, 14))["emails"] if item["result"] != "pass"], not_logged)

    def test_a_failed_result_update_leaves_the_old_reversal_not_logged(self):
        rows = [[
            "2026-10-12",
            "mail-old",
            "Sample Bank",
            "A reversal notice",
            "fail",
            REASON_REVERSAL,
            "",
            "",
            "",
            "",
        ]]
        service = _EvalSheet(rows, fail_write=True)
        changes = SheetChanges(watched_sheets=frozenset({"Eval Log"}))
        client = SheetsClient("sheet-id", changes=changes)
        client.service = service
        before = changes.mark()

        updated = client.mark_handled_reversals()

        self.assertEqual(updated[0][4], "fail")
        self.assertEqual(updated[0][5], REASON_REVERSAL)
        self.assertEqual(changes.mark(), before)
        self.assertEqual(service.rows[0][4], "fail")
        view = eval_view(updated, [], [], {}, date(2026, 10, 14))
        self.assertEqual([item["result"] for item in view["emails"]], ["fail"])

    def test_the_eval_page_uses_the_corrected_reversal_row(self):
        sheets = FakeSheets()
        calls = {"plain": 0, "correct": 0}

        def plain():
            calls["plain"] += 1
            return [[
                "2026-10-12",
                "mail-old",
                "Sample Bank",
                "A reversal notice",
                "fail",
                REASON_REVERSAL,
                "",
                "",
                "",
                "",
            ]]

        def correct():
            calls["correct"] += 1
            return [[
                "2026-10-12",
                "mail-old",
                "Sample Bank",
                "A reversal notice",
                "pass",
                REASON_REVERSAL,
                "",
                "",
                "",
                "",
            ]]

        sheets.get_eval_log_rows = plain
        sheets.mark_handled_reversals = correct
        app, _sheets = app_for(sheets)
        headers = {"X-Telegram-Init-Data": sign_webapp(111)}
        status, _, payload = app.handle("GET", "/api/dashboard?scope=both", headers, b"", secure=True)
        body = body_json(payload)
        self.assertEqual(status, 200)
        emails = body["agent_eval"]["emails"]
        self.assertEqual(emails[0]["result"], "pass")
        self.assertEqual(emails[0]["reason"], "Original charge was removed")
        self.assertEqual([item for item in emails if item["result"] != "pass"], [])
        self.assertEqual(calls["correct"], 1)
        self.assertEqual(calls["plain"], 0)


class _EvalResult:
    def __init__(self, payload=None, error: Exception | None = None):
        self._payload = {} if payload is None else payload
        self._error = error

    def execute(self):
        if self._error is not None:
            raise self._error
        return self._payload


class _EvalSheet:
    def __init__(self, rows: list[list[str]], fail_write: bool = False):
        self.rows = [list(row) for row in rows]
        self.fail_write = fail_write
        self.writes = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, **kwargs):
        if "range" in kwargs:
            return _EvalResult({"values": [list(row) for row in self.rows]})
        return _EvalResult({"sheets": [{"properties": {"title": "Eval Log", "sheetId": 9}}]})

    def batchUpdate(self, **kwargs):
        self.writes.append(kwargs)
        if self.fail_write:
            return _EvalResult(error=RuntimeError("sheet down"))
        for item in kwargs["body"]["data"]:
            cell = item["range"].split("!")[-1]
            row_number = int(cell[1:])
            value = item["values"][0][0]
            while len(self.rows[row_number - 2]) <= 4:
                self.rows[row_number - 2].append("")
            self.rows[row_number - 2][4] = value
        return _EvalResult({})


class EvalDashboardTests(unittest.TestCase):
    def test_group_notes_skip_a_row_with_no_mark(self):
        records = [
            _expense("aa1001", "2026-11-02"),
            _expense("bb2002", "2026-10-02"),
            _expense("cc3003", "2026-11-03"),
        ]
        rows = group_message_rows(
            records,
            [],
            {"aa1001": MESSAGE_SENT, "cc3003": MESSAGE_FAIL},
        )
        self.assertEqual([row["sent"] for row in rows], [True, False])
        self.assertEqual(rows[1]["shop"], "Sample cafe")
        self.assertEqual(rows[1]["date"], "2026-11-03")
        self.assertNotIn("bb2002", json.dumps(rows))

    def test_opening_one_failed_mail_masks_the_card_and_a_miss_stays_on_that_row(self):
        sheets = FakeSheets()
        sheets.get_eval_log_rows = lambda: [
            ["2026-11-03", "mail-1", "Bank alerts", "A charge", "fail", "no_card", "", "", "", ""],
            ["2026-11-03", "mail-2", "Bank alerts", "Logged", "pass", "logged", "abc123", "Food", "keyword", "Food"],
        ]
        app, _sheets = app_for(sheets)
        app.read_mail = lambda _message_id: "Paid with 4111111111111111 at Sample Shop."
        headers = {"X-Telegram-Init-Data": sign_webapp(111)}

        status, _, payload = app.handle("GET", "/api/dashboard/eval-mail?id=mail-1", headers, b"", secure=True)
        body = body_json(payload)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertNotIn("4111111111111111", body["body"])
        self.assertIn("1111", body["body"])

        blocked, _, blocked_body = app.handle("GET", "/api/dashboard/eval-mail?id=mail-2", headers, b"", secure=True)
        self.assertEqual(blocked, 200)
        self.assertFalse(body_json(blocked_body)["ok"])

        def blow(_message_id):
            raise RuntimeError("gmail down")

        app.read_mail = blow
        failed, _, failed_body = app.handle("GET", "/api/dashboard/eval-mail?id=mail-1", headers, b"", secure=True)
        self.assertEqual(failed, 200)
        self.assertEqual(body_json(failed_body)["message"], "I could not read that mail.")
        dashboard_status, _, dashboard = app.handle("GET", "/api/dashboard?scope=both", headers, b"", secure=True)
        self.assertEqual(dashboard_status, 200)
        self.assertEqual(body_json(dashboard)["recent"][0]["description"], "Sample cafe")
        self.assertFalse(body_json(dashboard)["agent_eval"]["scoring"])

    def test_eval_page_uses_plain_words(self):
        app, _sheets = app_for()
        status, _, payload = app.handle("GET", "/dashboard.js", {}, b"", secure=False)
        script = payload.decode()
        self.assertEqual(status, 200)
        self.assertIn('["eval", "Eval"]', script)
        self.assertIn("All time", script)
        self.assertIn("Email successfully logged", script)
        self.assertIn("Categories successfully matched", script)
        self.assertIn("Telegram message successfully sent", script)
        self.assertIn("not logged", script)
        self.assertIn("unchanged after logging", script)
        self.assertIn("not sent", script)
        self.assertIn('el("button", "eval-card"', script)
        self.assertIn("The purchase is on the sheet. Telegram did not accept the group note.", script)
        self.assertIn('el("button", "ghost eval-approve", "Approve")', script)
        self.assertIn("Nothing is written until you approve. The mail stays unlogged.", script)
        self.assertIn("Approved. The mail was not logged.", script)
        self.assertNotIn("Show this mail", script)
        self.assertNotIn("no mark yet", script)
        self.assertIn("function evalHealthBand(percent)", script)
        self.assertIn('if (percent >= 95) return "green"', script)
        self.assertIn('if (percent >= 80) return "orange"', script)
        self.assertIn('if (percent >= 60) return "yellow"', script)
        self.assertEqual(script.count("block.append(evalCount(percent))"), 3)
        self.assertIn('["green", "Green", "Under 60%"]', script)
        self.assertIn('["red", "Red", "95% or more"]', script)
        self.assertIn('"fill-" + limit.band', script)

        css_status, _, css_payload = app.handle("GET", "/dashboard.css", {}, b"", secure=False)
        style = css_payload.decode()
        self.assertEqual(css_status, 200)
        self.assertIn(".eval-count.tone-green { color: #1b6b45; }", style)
        self.assertIn(".eval-count.tone-yellow { color: #c4a035; }", style)
        self.assertIn(".eval-count.tone-orange { color: #e07a2f; }", style)
        self.assertIn(".eval-count.tone-red { color: #c44747; }", style)
        self.assertIn(".fill-green { background: #1b6b45; }", style)
        self.assertIn(".fill-red { background: #c44747; }", style)


class DiagnosisTests(unittest.TestCase):
    def test_every_stored_reason_has_a_diagnosis_and_an_unknown_reason_needs_the_mail(self):
        for reason in PLAIN_REASONS:
            diagnosis = diagnosis_for(reason)
            self.assertFalse(diagnosis.needs_mail)
            self.assertNotIn("4111111111111111", diagnosis.text)
            self.assertNotRegex(diagnosis.text, r"\d{4}")
            self.assertTrue(diagnosis.text.endswith("."))
        unknown = diagnosis_for("not-a-reason")
        self.assertTrue(unknown.needs_mail)
        self.assertIn("I will not guess a category or a card.", unknown.text)

    def test_an_unlogged_row_carries_its_diagnosis_and_a_logged_row_does_not(self):
        rows = [
            ["2026-09-12", "mail-quiet", "quiet@example.com", "Quiet note", "fail", "no_amount", "", "", "", ""],
            ["2026-09-20", "mail-cafe", "cafe@example.com", "Sample cafe", "pass", "logged", "aa1001", "Food", "keyword", "Food"],
        ]
        view = eval_view(rows, [], [], {}, date(2026, 9, 26))
        quiet = view["emails"][0]
        logged = view["emails"][1]
        self.assertEqual(quiet["diagnosis"], "This mail had no amount, so it was not logged.")
        self.assertFalse(quiet["needs_mail"])
        self.assertFalse(quiet["approved"])
        self.assertNotIn("diagnosis", logged)
        approved = eval_view(rows, [], [], {}, date(2026, 9, 26), {"mail-quiet": "no_amount"})
        self.assertTrue(approved["emails"][0]["approved"])
        changed = eval_view(rows, [], [], {}, date(2026, 9, 26), {"mail-quiet": "no_date"})
        self.assertFalse(changed["emails"][0]["approved"])

    def test_open_mail_diagnosis_copies_neither_the_amount_nor_the_card(self):
        body = "Paid SGD 20.00 on 09/10/2026 with card ending 4242 at Sample Shop. Card 4111111111111111."
        text = open_mail_diagnosis(body, date(2026, 11, 26))
        self.assertNotIn("20.00", text)
        self.assertNotIn("4242", text)
        self.assertNotIn("4111111111111111", text)
        self.assertNotIn("Sample Shop", text)
        self.assertIn("I will not log it from here", text)
        self.assertIn("no amount", open_mail_diagnosis("Hello from the bank.", date(2026, 11, 26)))

    def test_a_card_number_in_bot_state_is_not_treated_as_approval(self):
        rows = [
            ["eval_diagnosis:mail-quiet", "4111111111111111"],
            ["eval_diagnosis:mail-ok", "no_amount"],
            ["group_post:aa1001", "Message sent"],
        ]
        self.assertEqual(approved_from_state_rows(rows), {"mail-ok": "no_amount"})

    def test_approving_a_diagnosis_writes_only_the_reason_code(self):
        sheets = _DiagnosisSheets()
        reads = []
        app, _sheets = app_for(sheets)
        app.read_mail = lambda message_id: reads.append(message_id) or "Paid SGD 20.00 with 4111111111111111."
        headers = {"X-Telegram-Init-Data": sign_webapp(111)}

        status, _, payload = app.handle("GET", "/api/dashboard?scope=both", headers, b"", secure=True)
        quiet = body_json(payload)["agent_eval"]["emails"][0]
        self.assertEqual(status, 200)
        self.assertEqual(quiet["subject"], "Quiet note")
        self.assertEqual(quiet["diagnosis"], "This mail had no amount, so it was not logged.")
        self.assertFalse(quiet["approved"])

        opened, _, opened_body = app.handle(
            "GET", "/api/dashboard/eval-mail?id=sample-quiet-mail", headers, b"", secure=True
        )
        opened_json = body_json(opened_body)
        self.assertEqual(opened, 200)
        self.assertTrue(opened_json["ok"])
        self.assertNotIn("diagnosis", opened_json)
        self.assertEqual(reads, ["sample-quiet-mail"])

        approved, _, approved_body = app.handle(
            "POST",
            "/api/dashboard/eval-diagnosis",
            headers,
            json.dumps({"id": "sample-quiet-mail", "diagnosis": "4111111111111111"}).encode(),
            secure=True,
        )
        self.assertEqual(approved, 200)
        self.assertEqual(body_json(approved_body)["message"], "Approved. The mail was not logged.")
        self.assertEqual(sheets.state_writes, [("Bot State", diagnosis_state_key("sample-quiet-mail"), "no_amount")])
        self.assertEqual(reads, ["sample-quiet-mail"])
        self.assertEqual([record.description for record in sheets.records], ["Sample cafe"])

        again, _, _again_body = app.handle(
            "POST",
            "/api/dashboard/eval-diagnosis",
            headers,
            json.dumps({"id": "sample-quiet-mail"}).encode(),
            secure=True,
        )
        self.assertEqual(again, 200)
        self.assertEqual(len(sheets.state_writes), 1)

        reloaded, _, reloaded_body = app.handle("GET", "/api/dashboard?scope=both", headers, b"", secure=True)
        self.assertEqual(reloaded, 200)
        self.assertTrue(body_json(reloaded_body)["agent_eval"]["emails"][0]["approved"])

        logged, _, logged_body = app.handle(
            "POST",
            "/api/dashboard/eval-diagnosis",
            headers,
            json.dumps({"id": "sample-cafe-mail"}).encode(),
            secure=True,
        )
        self.assertEqual(logged, 200)
        self.assertFalse(body_json(logged_body)["ok"])
        self.assertEqual(len(sheets.state_writes), 1)

        locked, _, _locked_body = app.handle(
            "POST",
            "/api/dashboard/eval-diagnosis",
            {},
            json.dumps({"id": "sample-quiet-mail"}).encode(),
            secure=True,
        )
        self.assertEqual(locked, 401)

    def test_an_unknown_reason_uses_the_open_mail_and_approval_does_not_log_it(self):
        sheets = _DiagnosisSheets()
        sheets.eval_rows[0][5] = "mystery"
        reads = []
        app, _sheets = app_for(sheets)

        def read_mail(message_id):
            reads.append(message_id)
            return "Paid SGD 20.00 on 09/10/2026 with card ending 4242 at Sample Shop."

        app.read_mail = read_mail
        headers = {"X-Telegram-Init-Data": sign_webapp(111)}
        opened, _, opened_body = app.handle(
            "GET", "/api/dashboard/eval-mail?id=sample-quiet-mail", headers, b"", secure=True
        )
        note = body_json(opened_body)
        self.assertEqual(opened, 200)
        self.assertIn("I will not guess a category", note["diagnosis"])
        self.assertNotIn("4242", note["diagnosis"])
        self.assertNotIn("20.00", note["diagnosis"])
        self.assertNotIn("4242", json.dumps(sheets.state_rows))

        approved, _, approved_body = app.handle(
            "POST",
            "/api/dashboard/eval-diagnosis",
            headers,
            json.dumps({"id": "sample-quiet-mail"}).encode(),
            secure=True,
        )
        self.assertEqual(approved, 200)
        self.assertEqual(body_json(approved_body)["message"], "Approved. The mail was not logged.")
        self.assertEqual(sheets.state_writes, [("Bot State", "eval_diagnosis:sample-quiet-mail", "unknown")])
        self.assertEqual(reads, ["sample-quiet-mail"])
        self.assertEqual(len(sheets.records), 1)

    def test_a_failed_approval_read_does_not_write(self):
        sheets = _DiagnosisSheets()

        def boom(sheet_name, key):
            raise RuntimeError("sheet down")

        sheets.get_state_value = boom
        app, _sheets = app_for(sheets)
        headers = {"X-Telegram-Init-Data": sign_webapp(111)}
        status, _, payload = app.handle(
            "POST",
            "/api/dashboard/eval-diagnosis",
            headers,
            json.dumps({"id": "sample-quiet-mail"}).encode(),
            secure=True,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body_json(payload)["message"], "I could not save that approval. Nothing was written.")
        self.assertEqual(sheets.state_writes, [])
        self.assertEqual(len(sheets.records), 1)

    def test_a_failed_approval_write_leaves_the_expense_sheet_alone(self):
        sheets = _DiagnosisSheets(fail_write=True)
        app, _sheets = app_for(sheets)
        headers = {"X-Telegram-Init-Data": sign_webapp(111)}
        status, _, payload = app.handle(
            "POST",
            "/api/dashboard/eval-diagnosis",
            headers,
            json.dumps({"id": "sample-quiet-mail"}).encode(),
            secure=True,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body_json(payload)["message"], "I could not save that approval. Nothing was written.")
        self.assertEqual(sheets.state_rows, [])
        self.assertEqual(len(sheets.records), 1)


class _DiagnosisSheets(FakeSheets):
    def __init__(self, fail_write: bool = False):
        super().__init__()
        self.fail_write = fail_write
        self.state_rows = []
        self.state_writes = []
        self.eval_rows = [
            ["2026-09-12", "sample-quiet-mail", "quiet@example.com", "Quiet note", "fail", "no_amount", "", "", "", ""],
            ["2026-09-20", "sample-cafe-mail", "cafe@example.com", "Sample cafe", "pass", "logged", "aa1001", "Food", "keyword", "Food"],
        ]

    def get_eval_log_rows(self):
        return [list(row) for row in self.eval_rows]

    def get_state_rows(self, sheet_name):
        return [list(row) for row in self.state_rows]

    def get_state_value(self, sheet_name, key):
        for row in self.state_rows:
            if row[0] == key:
                return row[1]
        return None

    def set_state_value(self, sheet_name, key, value):
        if self.fail_write:
            raise RuntimeError("sheet down")
        self.state_writes.append((sheet_name, key, value))
        for row in self.state_rows:
            if row[0] == key:
                row[1] = value
                return
        self.state_rows.append([key, value])


def _expense(entry_id: str, expense_date: str) -> ExpenseRecord:
    return ExpenseRecord(
        row_number=2,
        entry_id=entry_id,
        timestamp="12:00:00",
        expense_date=expense_date,
        month=expense_date[:7],
        logged_by="Alex",
        raw_input="sample",
        amount=Decimal("18.50"),
        category="Food",
        description="Sample cafe",
        input_type="text",
        status="Confirmed",
        transaction_type="Expense",
    )


if __name__ == "__main__":
    unittest.main()
