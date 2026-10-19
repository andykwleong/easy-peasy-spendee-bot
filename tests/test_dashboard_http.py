from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from http.client import HTTPConnection
from urllib.parse import urlencode

from telegram.error import TelegramError

from getrichbot.cards import CardLimit, CardLimitUsage, parse_payment_config
from getrichbot.sheet_changes import SheetChanges
from getrichbot.sheets import SheetsClient
from getrichbot.dashboard_auth import issue_session_token
from getrichbot.dashboard_bot import _DASHBOARD_REPLY_TEXT, configure_dashboard_menu, dashboard_prompt, reply_with_dashboard
from getrichbot.dashboard_http import DashboardApp, DashboardContext, DashboardHTTPServer
from getrichbot.models import ExpenseRecord

TOKEN = "test-bot-token"
NOW = int(time.time())


def sign_webapp(user_id: int, token: str = TOKEN, auth_date: int = NOW) -> str:
    user = json.dumps({"id": user_id, "first_name": "Alex"}, separators=(",", ":"))
    fields = {"auth_date": str(auth_date), "user": user}
    data_check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def sign_widget(user_id: int, auth_date: int = NOW, first_name: str = "Alex") -> dict:
    fields = {"auth_date": str(auth_date), "first_name": first_name, "id": str(user_id)}
    data_check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hashlib.sha256(TOKEN.encode()).digest()
    digest = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return {"auth_date": auth_date, "first_name": first_name, "id": user_id, "hash": digest}


class FakeSheets:
    def __init__(self):
        self.calls: list[str] = []
        self.records = [
            ExpenseRecord(
                row_number=2,
                entry_id="aa1001",
                timestamp="12:00:00",
                expense_date="2026-09-20",
                month="2026-09",
                logged_by="Alex",
                raw_input="sample",
                amount=Decimal("18.50"),
                category="Food",
                description="Sample cafe",
                input_type="text",
                status="Confirmed",
                transaction_type="Expense",
                payment_method="Sample Visa",
                payment_owner="Alex",
            )
        ]
        self.usage = []
        self.config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["Sample Visa", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Limit Amount", "Active"],
                ["Sample Visa", "Alex", "All", "100", "TRUE"],
            ],
        )

    def get_expense_records(self, sheet_name, timeout_seconds=None):
        self.calls.append("get_expense_records")
        return list(self.records)

    def get_card_usage_records(self, sheet_name):
        self.calls.append("get_card_usage_records")
        return list(self.usage)

    def get_payment_config(self, payment_methods_sheet, card_limits_sheet):
        self.calls.append("get_payment_config")
        if getattr(self, "payment_error", None):
            raise ValueError(self.payment_error)
        return self.config

    def append_expense(self, *args, **kwargs):
        raise AssertionError("dashboard must not write")

    def update_expense_record(self, *args, **kwargs):
        raise AssertionError("dashboard must not write")

    def delete_entry_by_id(self, *args, **kwargs):
        raise AssertionError("dashboard must not write")


class EditableSheets(FakeSheets):
    def __init__(self):
        super().__init__()
        self.writes = []
        self.deletes = []
        self.keywords = []
        self.config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["Sample Visa", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
                ["Sample Mastercard", "Sam", "Credit Card", "Calendar", "1", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
                ["Sample Visa", "Alex", "All", "All", "100", "TRUE"],
                ["Sample Mastercard", "Sam", "All", "PayWave", "200", "TRUE"],
            ],
        )

    def update_expense_record(self, sheet_name, row_number, **kwargs):
        self.writes.append(("expense", row_number, kwargs))
        updated = []
        for record in self.records:
            if record.row_number == row_number:
                changes = {key: value for key, value in kwargs.items() if value is not None and key in record.__dataclass_fields__}
                record = replace(record, **changes)
            updated.append(record)
        self.records = updated

    def upsert_category_keyword(self, sheet_name, keyword, category):
        self.keywords.append((sheet_name, keyword, category))

    def update_card_usage_record(self, sheet_name, row_number, **kwargs):
        self.writes.append(("card_usage", row_number, kwargs))

    def update_monthly_summary(self, sheet_name, rows, timeout_seconds=None):
        self.summary_rows = rows

    def delete_entry_by_id(self, sheet_name, entry_id, logged_by=None):
        self.deletes.append((sheet_name, entry_id))
        if sheet_name == "Card Usage":
            before = len(self.usage)
            self.usage = [row for row in self.usage if row.entry_id.lower() != entry_id.lower()]
            return len(self.usage) != before
        before = len(self.records)
        self.records = [row for row in self.records if row.entry_id.lower() != entry_id.lower()]
        return len(self.records) != before


def labels(user_id: int) -> str | None:
    if user_id == 111:
        return "Alex"
    if user_id == 222:
        return "Sam"
    return None


class _SheetValues:
    def __init__(self, sheets: dict[str, list]):
        self._sheets = sheets

    def get(self, **kwargs):
        title, _cells = kwargs["range"].split("!", 1)
        return _SheetRequest(self._sheets[title])


class _SheetRequest:
    def __init__(self, values):
        self._values = values

    def execute(self):
        return {"values": self._values}


class _SheetSpreadsheets:
    def __init__(self, sheets: dict[str, list]):
        self._values = _SheetValues(sheets)

    def values(self):
        return self._values


class _SheetService:
    def __init__(self, sheets: dict[str, list]):
        self._spreadsheets = _SheetSpreadsheets(sheets)

    def spreadsheets(self):
        return self._spreadsheets


def app_for(sheets: FakeSheets | None = None) -> tuple[DashboardApp, FakeSheets]:
    sheets = sheets or FakeSheets()
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
    return DashboardApp(context), sheets


def body_json(payload: bytes) -> dict:
    return json.loads(payload.decode())


class DashboardHttpTests(unittest.TestCase):
    def test_locked_visit_has_no_rows(self):
        app, sheets = app_for()

        status, _, payload = app.handle("GET", "/api/dashboard", {}, b"", secure=False)
        page_status, _, page = app.handle("GET", "/", {}, b"", secure=False)

        self.assertEqual(status, 401)
        self.assertNotIn(b"Sample cafe", payload)
        self.assertEqual(sheets.calls, [])
        self.assertEqual(page_status, 200)
        self.assertIn(b"Household money", page)
        self.assertNotIn(b"Sample cafe", page)

    def test_user_id_alone_does_not_open_the_page(self):
        app, _sheets = app_for()

        status, _, payload = app.handle(
            "GET",
            "/api/dashboard?user_id=111&label=Alex",
            {"X-User-Id": "111"},
            b"",
            secure=False,
        )

        self.assertEqual(status, 401)
        self.assertNotIn(b"18.50", payload)

    def test_signed_webapp_login_reads_the_sheet_and_does_not_write(self):
        app, sheets = app_for()
        init_data = sign_webapp(111)

        status, headers, payload = app.handle(
            "POST",
            "/api/session/webapp",
            {"Content-Type": "application/json"},
            json.dumps({"init_data": init_data}).encode(),
            secure=True,
        )
        dashboard_status, _, dashboard = app.handle(
            "GET",
            "/api/dashboard?scope=both&label=Sam",
            {"X-Telegram-Init-Data": init_data},
            b"",
            secure=True,
        )

        self.assertEqual(status, 200)
        self.assertIn("Secure", dict(headers)["Set-Cookie"])
        self.assertEqual(body_json(payload)["label"], "Alex")
        self.assertEqual(dashboard_status, 200)
        self.assertEqual(body_json(dashboard)["viewer_label"], "Alex")
        self.assertEqual(body_json(dashboard)["recent"][0]["description"], "Sample cafe")
        self.assertEqual(sheets.calls, ["get_expense_records", "get_card_usage_records", "get_payment_config"])
        self.assertFalse(body_json(dashboard)["agent_eval"]["scoring"])

    def test_recent_request_can_show_only_rows_with_no_category(self):
        app, sheets = app_for()
        sheets.records.append(
            ExpenseRecord(
                row_number=3,
                entry_id="bb2002",
                timestamp="13:00:00",
                expense_date="2026-09-21",
                month="2026-09",
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
            )
        )
        init_data = sign_webapp(111)
        headers = {"X-Telegram-Init-Data": init_data}

        full_status, _, full = app.handle("GET", "/api/dashboard?scope=both", headers, b"", secure=True)
        filtered_status, _, filtered = app.handle(
            "GET",
            "/api/dashboard?scope=both&missing=1",
            headers,
            b"",
            secure=True,
        )

        self.assertEqual(full_status, 200)
        self.assertEqual(filtered_status, 200)
        full_ids = [row["id"] for row in body_json(full)["recent"]]
        self.assertEqual(full_ids, ["bb2002", "aa1001"])
        self.assertEqual([row["id"] for row in body_json(filtered)["recent"]], ["bb2002"])
        self.assertEqual(body_json(filtered)["recent"][0]["category"], "")
        self.assertIn("aa1001", [row["id"] for row in body_json(filtered)["raw_expenses"]])

    def test_either_signed_in_person_can_read_the_other_persons_recent_rows(self):
        sheets = EditableSheets()
        sheets.records.extend(
            [
                ExpenseRecord(
                    row_number=3,
                    entry_id="bb2002",
                    timestamp="15:00:00",
                    expense_date="2026-09-21",
                    month="2026-09",
                    logged_by="Sam",
                    raw_input="sample",
                    amount=Decimal("12.40"),
                    category="Groceries",
                    description="Sample market",
                    input_type="text",
                    status="Confirmed",
                    transaction_type="Expense",
                    payment_method="Sample Mastercard",
                    payment_owner="Sam",
                ),
                ExpenseRecord(
                    row_number=4,
                    entry_id="cc3003",
                    timestamp="11:00:00",
                    expense_date="2026-09-19",
                    month="2026-09",
                    logged_by="Alex",
                    raw_input="sample",
                    amount=Decimal("7.00"),
                    category="Food",
                    description="Sample gift",
                    input_type="text",
                    status="Confirmed",
                    transaction_type="Expense",
                    payment_method="Sample Mastercard",
                    payment_owner="Sam",
                ),
                ExpenseRecord(
                    row_number=5,
                    entry_id="dd4004",
                    timestamp="16:00:00",
                    expense_date="2026-09-22",
                    month="2026-09",
                    logged_by="Sam",
                    raw_input="sample",
                    amount=Decimal("4.00"),
                    category="",
                    description="Sample stall",
                    input_type="text",
                    status="Confirmed",
                    transaction_type="Expense",
                    payment_method="Sample Visa",
                    payment_owner="Alex",
                ),
            ]
        )
        app, _sheets = app_for(sheets)
        alex = {"X-Telegram-Init-Data": sign_webapp(111)}
        sam = {"X-Telegram-Init-Data": sign_webapp(222)}

        alex_status, _, alex_payload = app.handle("GET", "/api/dashboard", alex, b"", secure=True)
        sam_status, _, sam_payload = app.handle("GET", "/api/dashboard", sam, b"", secure=True)
        alex_body = body_json(alex_payload)
        sam_body = body_json(sam_payload)

        self.assertEqual(alex_status, 200)
        self.assertEqual(sam_status, 200)
        self.assertEqual(alex_body["scope"], "both")
        self.assertEqual(sam_body["scope"], "both")
        self.assertEqual(alex_body["household_labels"], ["Alex", "Sam"])
        self.assertEqual(sam_body["household_labels"], ["Alex", "Sam"])
        self.assertEqual(alex_body["viewer_label"], "Alex")
        self.assertEqual(sam_body["viewer_label"], "Sam")
        self.assertEqual(
            [row["id"] for row in alex_body["recent"]],
            ["dd4004", "bb2002", "aa1001", "cc3003"],
        )
        self.assertEqual([row["id"] for row in sam_body["recent"]], [row["id"] for row in alex_body["recent"]])

        sam_for_alex_status, _, sam_for_alex = app.handle("GET", "/api/dashboard?scope=Sam", alex, b"", secure=True)
        alex_for_sam_status, _, alex_for_sam = app.handle("GET", "/api/dashboard?scope=Alex", sam, b"", secure=True)
        missing_status, _, missing = app.handle(
            "GET",
            "/api/dashboard?scope=Sam&missing=1",
            alex,
            b"",
            secure=True,
        )

        self.assertEqual(sam_for_alex_status, 200)
        self.assertEqual(alex_for_sam_status, 200)
        self.assertEqual(missing_status, 200)
        self.assertEqual([row["id"] for row in body_json(sam_for_alex)["recent"]], ["dd4004", "bb2002"])
        self.assertTrue(all(row["logged_by"] == "Sam" for row in body_json(sam_for_alex)["recent"]))
        self.assertEqual([row["id"] for row in body_json(alex_for_sam)["recent"]], ["aa1001", "cc3003"])
        gift = next(row for row in body_json(alex_for_sam)["recent"] if row["id"] == "cc3003")
        self.assertEqual(gift["logged_by"], "Alex")
        self.assertEqual(gift["payment_owner"], "Sam")
        self.assertEqual([row["id"] for row in body_json(missing)["recent"]], ["dd4004"])
        self.assertEqual(body_json(sam_for_alex)["cards"]["showing"], "mine")
        self.assertEqual(body_json(sam_for_alex)["cards"]["owner"], "Alex")

        calls_before = len(sheets.calls)
        mine_status, _, mine_body = app.handle("GET", "/api/dashboard?scope=mine", sam, b"", secure=True)
        self.assertEqual(mine_status, 400)
        self.assertEqual(body_json(mine_body)["error"], "scope")
        self.assertNotIn(b"Sample cafe", mine_body)
        self.assertEqual(len(sheets.calls), calls_before)

        edit_status, _, edit_body = app.handle(
            "POST",
            "/api/dashboard/edit",
            {"Content-Type": "application/json", **alex},
            json.dumps({
                "id": "aa1001",
                "source": "expense",
                "field": "amount",
                "value": "9.00",
                "scope": "mine",
            }).encode(),
            secure=True,
        )
        delete_status, _, delete_body = app.handle(
            "POST",
            "/api/dashboard/delete",
            {"Content-Type": "application/json", **alex},
            json.dumps({
                "id": "aa1001",
                "source": "expense",
                "confirm": True,
                "scope": "someone-else",
            }).encode(),
            secure=True,
        )
        self.assertEqual(edit_status, 400)
        self.assertEqual(body_json(edit_body)["error"], "scope")
        self.assertEqual(delete_status, 400)
        self.assertEqual(body_json(delete_body)["error"], "scope")
        self.assertEqual(sheets.writes, [])
        self.assertEqual(sheets.deletes, [])

    def test_recent_page_does_not_use_a_viewer_relative_scope(self):
        app, _sheets = app_for()

        status, _, payload = app.handle("GET", "/dashboard.js", {}, b"", secure=False)

        self.assertEqual(status, 200)
        script = payload.decode()
        self.assertIn("household_labels", script)
        self.assertNotIn('viewer_label + " only"', script)
        self.assertNotIn('scope: "mine"', script)
        self.assertNotIn('state.scope = "mine"', script)
        self.assertIn("No category", script)
        self.assertIn('["mine", cards.viewer_label]', script)

    def test_widget_login_sets_a_thirty_day_cookie_and_logout_clears_it(self):
        app, _sheets = app_for()

        status, headers, _payload = app.handle(
            "POST",
            "/api/session/widget",
            {"Content-Type": "application/json"},
            json.dumps(sign_widget(111)).encode(),
            secure=False,
        )
        cookie = dict(headers)["Set-Cookie"]
        token = cookie.split(";", 1)[0].split("=", 1)[1]
        dashboard_status, _, dashboard = app.handle(
            "GET",
            "/api/dashboard",
            {"Cookie": f"grb_session={token}"},
            b"",
            secure=False,
        )
        logout_status, logout_headers, _logout = app.handle("POST", "/api/session/logout", {}, b"", secure=False)
        forgotten_status, _, forgotten = app.handle("GET", "/api/dashboard", {}, b"", secure=False)

        self.assertEqual(status, 200)
        self.assertIn("Max-Age=2592000", cookie)
        self.assertNotIn("Secure", cookie)
        self.assertEqual(dashboard_status, 200)
        self.assertIn("Sample cafe", dashboard.decode())
        self.assertEqual(logout_status, 200)
        self.assertIn("Max-Age=0", dict(logout_headers)["Set-Cookie"])
        self.assertEqual(forgotten_status, 401)
        self.assertNotIn(b"Sample cafe", forgotten)

    def test_other_telegram_account_stays_locked(self):
        app, sheets = app_for()

        status, _, payload = app.handle(
            "POST",
            "/api/session/widget",
            {"Content-Type": "application/json"},
            json.dumps(sign_widget(333)).encode(),
            secure=False,
        )

        self.assertEqual(status, 401)
        self.assertEqual(body_json(payload)["error"], "not_allowed")
        self.assertNotIn(b"Sample cafe", payload)
        self.assertEqual(sheets.calls, [])

    def test_expired_cookie_and_bad_scope_do_not_return_rows(self):
        app, sheets = app_for()
        expired = issue_session_token(111, TOKEN, now=10, ttl_seconds=10)

        expired_status, _, expired_body = app.handle(
            "GET",
            "/api/dashboard",
            {"Cookie": f"grb_session={expired}"},
            b"",
            secure=False,
        )
        scope_status, _, scope_body = app.handle(
            "GET",
            "/api/dashboard?scope=someone-else",
            {"X-Telegram-Init-Data": sign_webapp(111)},
            b"",
            secure=False,
        )

        self.assertEqual(expired_status, 401)
        self.assertNotIn(b"Sample cafe", expired_body)
        self.assertEqual(scope_status, 400)
        self.assertNotIn(b"Sample cafe", scope_body)
        self.assertEqual(sheets.calls, [])

    def test_nan_limit_does_not_blank_the_page_and_blank_limit_stays_uncapped(self):
        expense = [[
            "aa1001", "12:00:00", "2026-09-20", "2026-09", "Alex", "sample", "18.50", "Food", "Sample cafe",
            "Alex", "Sample Visa", "", "Expense", "text", "Confirmed", "1", "2",
        ]]
        methods = [
            ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
            ["Sample Visa", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
            ["Sample Cashback", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
        ]
        limits = [
            ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
            ["Sample Visa", "Alex", "All", "All", "", "TRUE"],
            ["Sample Cashback", "Alex", "All", "All", "NaN", "TRUE"],
        ]
        client = SheetsClient("sheet-id")
        client.service = _SheetService({
            "Raw Expenses": expense,
            "Card Usage": [],
            "Payment Methods": methods,
            "Card Limits": limits,
        })
        app, _sheets = app_for(client)
        token = issue_session_token(111, TOKEN, now=NOW)
        status, _, payload = app.handle(
            "GET",
            "/api/dashboard?scope=both&cards=mine",
            {"cookie": f"grb_session={token}"},
            b"",
            secure=False,
        )
        body = body_json(payload)
        self.assertEqual(status, 200)
        self.assertNotEqual(body.get("error"), "sheet")
        self.assertEqual(body["recent"][0]["id"], "aa1001")
        uncapped = {card["name"] for card in body["cards"]["uncapped"]}
        self.assertIn("Sample Visa", uncapped)
        self.assertIn("Sample Cashback", uncapped)
        self.assertEqual(body["cards"]["capped"], [])
        self.assertIsNone(body["cards"]["error"])
        safe = CardLimitUsage(CardLimit("Sample Visa", "Alex", "All", Decimal("0")), Decimal("10"))
        self.assertEqual(safe.percent, Decimal("0"))

    def test_sheet_failure_does_not_echo_private_details_or_write(self):
        sheets = FakeSheets()

        def explode(_sheet_name):
            raise RuntimeError("private-sheet-id")

        sheets.get_expense_records = explode
        app, _sheets = app_for(sheets)

        status, _, payload = app.handle(
            "GET",
            "/api/dashboard",
            {"X-Telegram-Init-Data": sign_webapp(111)},
            b"",
            secure=False,
        )

        self.assertEqual(status, 500)
        self.assertEqual(body_json(payload)["error"], "sheet")
        self.assertNotIn(b"private-sheet-id", payload)

    def test_card_setup_problem_keeps_the_other_sections(self):
        sheets = FakeSheets()
        sheets.payment_error = "private card setup detail"
        app, _sheets = app_for(sheets)

        status, _, payload = app.handle(
            "GET",
            "/api/dashboard",
            {"X-Telegram-Init-Data": sign_webapp(111)},
            b"",
            secure=False,
        )
        data = body_json(payload)

        self.assertEqual(status, 200)
        self.assertEqual(data["recent"][0]["description"], "Sample cafe")
        self.assertEqual(data["cards"]["capped"], [])
        self.assertNotIn("private card setup detail", payload.decode())

    def test_there_is_no_write_route(self):
        app, _sheets = app_for()

        for path in ("/api/dashboard", "/api/fix", "/api/expenses"):
            status, _, payload = app.handle("POST", path, {}, b"{}", secure=False)
            self.assertIn(status, {404, 405})
            self.assertNotIn(b"Sample cafe", payload)

    def test_live_server_sets_the_login_cookie(self):
        app, _sheets = app_for()
        server = DashboardHTTPServer("127.0.0.1", 0, app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            connection = HTTPConnection("127.0.0.1", port)
            connection.request(
                "POST",
                "/api/session/widget",
                json.dumps(sign_widget(222)).encode(),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read()
            cookie = response.getheader("Set-Cookie")
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(raw)["label"], "Sam")
        self.assertIsNotNone(cookie)
        self.assertIn("grb_session=", cookie or "")
        self.assertIn("HttpOnly", cookie or "")

    def test_tidy_dashboard_page_hides_explanations_and_keeps_colour_rules(self):
        app, _sheets = app_for()

        script_status, _, script_payload = app.handle("GET", "/dashboard.js", {}, b"", secure=False)
        css_status, _, css_payload = app.handle("GET", "/dashboard.css", {}, b"", secure=False)

        self.assertEqual(script_status, 200)
        self.assertEqual(css_status, 200)
        script = script_payload.decode()
        css = css_payload.decode()
        self.assertNotIn('["later", "Agent eval"]', script)
        self.assertNotIn("section.append(legend())", script)
        self.assertNotIn("bandText(limit.band)", script)
        self.assertNotIn("The same rows as the record book", script)
        self.assertNotIn("Household income, expenses, and what is left", script)
        self.assertNotIn("Colours match the bot", script)
        self.assertNotIn("Card Usage counts toward the card", script)
        self.assertIn('["raw", "Raw entries"]', script)
        self.assertIn("raw-frame", script)
        self.assertIn("limit.percent_label", script)
        self.assertIn("No cap", script)
        self.assertIn(".raw-frame", css)
        self.assertIn("overflow: scroll", css)

    def test_saved_row_stays_saved_when_the_month_rewrite_cannot_reread_the_sheet(self):
        sheets = EditableSheets()
        sheets.records[0] = replace(
            sheets.records[0],
            description="Sample stall",
            amount=Decimal("9.99"),
            category="Food",
            payment_method="Sample Store Card",
            payment_owner="Alex",
            logged_by="Alex",
        )
        reads = {"count": 0}
        original = sheets.get_expense_records

        def fail_after_the_row_is_loaded(sheet_name, timeout_seconds=None):
            reads["count"] += 1
            if reads["count"] > 1:
                raise TimeoutError("Google Sheet read exceeded 20 seconds.")
            return original(sheet_name, timeout_seconds)

        sheets.get_expense_records = fail_after_the_row_is_loaded
        app, _sheets = app_for(sheets)
        _status, headers, _payload = app.handle(
            "POST",
            "/api/session/widget",
            {"Content-Type": "application/json"},
            json.dumps(sign_widget(111)).encode(),
            secure=True,
        )
        cookie = dict(headers)["Set-Cookie"].split(";", 1)[0]

        status, _, payload = app.handle(
            "POST",
            "/api/dashboard/edit",
            {"Content-Type": "application/json", "Cookie": cookie},
            json.dumps({
                "id": "aa1001",
                "source": "expense",
                "field": "category",
                "value": "Groceries",
            }).encode(),
            secure=True,
        )

        saved = body_json(payload)
        self.assertEqual(status, 200, saved)
        self.assertTrue(saved["ok"])
        self.assertNotIn("could not update the Google Sheet", saved["message"])
        shown = next(item for item in saved["recent"] if item["id"] == "aa1001")
        self.assertEqual(shown["category"], "Groceries")
        self.assertEqual(shown["amount"], "9.99")
        self.assertEqual(shown["payment_method"], "Sample Store Card")
        self.assertEqual(shown["payment_owner"], "Alex")
        self.assertFalse(any(card["name"] == "Sample Store Card" for card in saved["edit_choices"]["cards"]))
        stored = sheets.records[0]
        self.assertEqual(stored.category, "Groceries")
        self.assertEqual(stored.payment_method, "Sample Store Card")
        self.assertEqual(stored.payment_owner, "Alex")
        self.assertIsNone(sheets.writes[0][2]["payment_method"])
        self.assertIsNone(sheets.writes[0][2]["payment_owner"])

    def test_changes_wait_does_not_read_the_sheet_when_the_mark_matches(self):
        sheets = FakeSheets()
        sheets.changes = _FastChanges(frozenset({"Raw Expenses", "Card Usage"}))
        app, _sheets = app_for(sheets)
        shown = sheets.changes.mark()

        status, _, payload = app.handle(
            "GET",
            "/api/dashboard/changes?since=" + shown,
            {"X-Telegram-Init-Data": sign_webapp(111)},
            b"",
            secure=False,
        )

        body = body_json(payload)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["mark"], shown)
        self.assertEqual(sheets.calls, [])

    def test_changes_route_checks_identity_and_a_full_wait_queue(self):
        app, sheets = app_for()
        locked, _, locked_body = app.handle("GET", "/api/dashboard/changes?since=boot.0", {}, b"", secure=False)
        self.assertEqual(locked, 401)
        self.assertEqual(body_json(locked_body)["error"], "locked")
        self.assertEqual(sheets.calls, [])

        denied, _sheets = app_for()
        denied.context.label_for_user = lambda user_id: None
        status, _, payload = denied.handle(
            "GET",
            "/api/dashboard/changes?since=boot.0",
            {"X-Telegram-Init-Data": sign_webapp(111)},
            b"",
            secure=False,
        )
        self.assertEqual(status, 401)
        self.assertEqual(body_json(payload)["error"], "not_allowed")

        missing, missing_sheets = app_for()
        with self.assertLogs("getrichbot.dashboard_http", level="ERROR") as logs:
            first, _, first_body = missing.handle(
                "GET",
                "/api/dashboard/changes?since=boot.0",
                {"X-Telegram-Init-Data": sign_webapp(111)},
                b"",
                secure=False,
            )
            second, _, second_body = missing.handle(
                "GET",
                "/api/dashboard/changes?since=boot.0",
                {"X-Telegram-Init-Data": sign_webapp(111)},
                b"",
                secure=False,
            )
        self.assertEqual(first, 503)
        self.assertEqual(second, 503)
        self.assertEqual(body_json(first_body)["error"], "changes")
        self.assertEqual(body_json(second_body)["error"], "changes")
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(missing_sheets.calls, [])

        busy_sheets = FakeSheets()
        busy_sheets.changes = _BusyChanges()
        busy, _busy_sheets = app_for(busy_sheets)
        status, _, payload = busy.handle(
            "GET",
            "/api/dashboard/changes?since=boot.0",
            {"X-Telegram-Init-Data": sign_webapp(111)},
            b"",
            secure=False,
        )
        self.assertEqual(status, 429)
        self.assertEqual(body_json(payload)["error"], "busy")
        self.assertEqual(busy_sheets.calls, [])

    def test_change_mark_is_taken_before_the_sheet_read(self):
        sheets = FakeSheets()
        sheets.changes = SheetChanges(frozenset({"Raw Expenses", "Card Usage"}))
        original = sheets.get_expense_records

        def read_and_move(sheet_name, timeout_seconds=None):
            sheets.sampled = sheets.changes.mark()
            sheets.changes.note_write("Raw Expenses")
            return original(sheet_name, timeout_seconds)

        sheets.get_expense_records = read_and_move
        app, _sheets = app_for(sheets)

        status, _, payload = app.handle(
            "GET",
            "/api/dashboard?scope=both&cards=mine",
            {"X-Telegram-Init-Data": sign_webapp(111)},
            b"",
            secure=False,
        )

        body = body_json(payload)
        self.assertEqual(status, 200)
        self.assertEqual(body["change_mark"], sheets.sampled)
        self.assertNotEqual(body["change_mark"], sheets.changes.mark())

    def test_edit_and_delete_replies_carry_the_change_mark(self):
        sheets = EditableSheets()
        sheets.changes = SheetChanges(frozenset({"Raw Expenses", "Card Usage"}))
        app, _sheets = app_for(sheets)
        headers = {
            "Content-Type": "application/json",
            "X-Telegram-Init-Data": sign_webapp(111),
        }

        edit_status, _, edit_body = app.handle(
            "POST",
            "/api/dashboard/edit",
            headers,
            json.dumps({"id": "aa1001", "source": "expense", "field": "amount", "value": "9.00"}).encode(),
            secure=False,
        )
        edited = body_json(edit_body)
        self.assertEqual(edit_status, 200, edited)
        self.assertEqual(edited["change_mark"], sheets.changes.mark())

        delete_status, _, delete_body = app.handle(
            "POST",
            "/api/dashboard/delete",
            headers,
            json.dumps({"id": "aa1001", "source": "expense", "confirm": True}).encode(),
            secure=False,
        )
        deleted = body_json(delete_body)
        self.assertEqual(delete_status, 200, deleted)
        self.assertEqual(deleted["change_mark"], sheets.changes.mark())
        self.assertTrue(all(item["id"] != "aa1001" for item in deleted["recent"]))

    def test_edit_fallback_keeps_the_mark_from_just_after_the_write(self):
        sheets = EditableSheets()
        changes = SheetChanges(frozenset({"Raw Expenses", "Card Usage"}))
        sheets.changes = changes
        before = changes.mark()
        reads = {"count": 0}
        original = sheets.get_expense_records

        def fail_after_the_row_is_loaded(sheet_name, timeout_seconds=None):
            reads["count"] += 1
            if reads["count"] > 1:
                changes.note_write("Raw Expenses")
                raise TimeoutError("Google Sheet read exceeded 20 seconds.")
            return original(sheet_name, timeout_seconds)

        sheets.get_expense_records = fail_after_the_row_is_loaded
        app, _sheets = app_for(sheets)

        status, _, payload = app.handle(
            "POST",
            "/api/dashboard/edit",
            {"Content-Type": "application/json", "X-Telegram-Init-Data": sign_webapp(111)},
            json.dumps({"id": "aa1001", "source": "expense", "field": "amount", "value": "9.00"}).encode(),
            secure=False,
        )

        saved = body_json(payload)
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["change_mark"], before)
        self.assertNotEqual(changes.mark(), before)
        shown = next(item for item in saved["recent"] if item["id"] == "aa1001")
        self.assertEqual(shown["amount"], "9.00")

    def test_dashboard_script_reloads_quietly_when_the_mark_moves(self):
        app, _sheets = app_for()

        status, _, payload = app.handle("GET", "/dashboard.js", {}, b"", secure=False)

        self.assertEqual(status, 200)
        script = payload.decode()
        self.assertIn("Reading the sheet...", script)
        self.assertIn("/api/dashboard/changes?since=", script)
        self.assertIn("loadDashboard({ quiet: true })", script)
        self.assertIn("state.editingId", script)
        self.assertIn("state.pendingDelete", script)
        self.assertIn("state.deleting", script)
        self.assertIn('active.tagName !== "INPUT"', script)
        self.assertIn('active.tagName !== "SELECT"', script)
        self.assertIn("Could not refresh. The list on screen is unchanged.", script)


class _FastChanges(SheetChanges):
    def wait_past(self, since: str, *, timeout: float = 25.0) -> str | None:
        return super().wait_past(since, timeout=0.05)


class _BusyChanges:
    def wait_past(self, since: str, *, timeout: float = 25.0) -> str | None:
        return None


class DashboardPromptTests(unittest.IsolatedAsyncioTestCase):
    def test_button_stays_off_until_an_https_address_is_set(self):
        for value in (None, "", "   ", "http://your-app.example.com"):
            text, url = dashboard_prompt(value)
            self.assertIsNone(url)
            self.assertIn("does nothing useful", text)

        text, url = dashboard_prompt("https://your-app.example.com")
        self.assertEqual(url, "https://your-app.example.com")
        self.assertEqual(text, "open the dashboard here:")
        self.assertEqual(text, _DASHBOARD_REPLY_TEXT)
        self.assertNotIn("\u200b", text)
        self.assertNotEqual(text, "Dashboard")
        self.assertNotIn("30 days", text)
        self.assertNotIn("Log out", text)

    async def test_private_chat_includes_the_link_and_a_mini_app_button(self):
        sent = []

        class Message:
            async def reply_text(self, text, reply_markup=None):
                sent.append((text, reply_markup))

        class Update:
            def __init__(self, user_id, chat_type="private"):
                self.message = Message()
                self.effective_user = type("User", (), {"id": user_id})()
                self.effective_chat = type("Chat", (), {"type": chat_type})()

        class Settings:
            dashboard_public_url = "https://your-app.example.com"

            def label_for_user(self, user_id):
                return "Alex" if user_id == 111 else None

        await reply_with_dashboard(Update(111), Settings())
        await reply_with_dashboard(Update(333), Settings())

        text, markup = sent[0]
        self.assertEqual(text, "open the dashboard here:")
        self.assertEqual(len(markup.inline_keyboard), 1)
        self.assertEqual(len(markup.inline_keyboard[0]), 1)
        button = markup.inline_keyboard[0][0]
        self.assertEqual(button.text, "Open dashboard")
        self.assertEqual(button.web_app.url, "https://your-app.example.com")
        self.assertIsNone(button.url)
        self.assertNotIn("reply_keyboard", markup.to_dict())
        self.assertIn("do not recognize", sent[1][0])
        self.assertIsNone(sent[1][1])

    async def test_group_reply_is_one_browser_link_button(self):
        sent = []

        class Message:
            async def reply_text(self, text, reply_markup=None):
                sent.append((text, reply_markup))

        class Update:
            def __init__(self, chat_type):
                self.message = Message()
                self.effective_user = type("User", (), {"id": 111})()
                self.effective_chat = type("Chat", (), {"type": chat_type})()

        class Settings:
            dashboard_public_url = "https://your-app.example.com"

            def label_for_user(self, user_id):
                return "Alex"

        for chat_type in ("group", "supergroup"):
            await reply_with_dashboard(Update(chat_type), Settings())

        self.assertEqual(len(sent), 2)
        for text, markup in sent:
            self.assertEqual(text, "open the dashboard here:")
            self.assertEqual(len(markup.inline_keyboard), 1)
            self.assertEqual(len(markup.inline_keyboard[0]), 1)
            button = markup.inline_keyboard[0][0]
            self.assertEqual(button.text, "Open dashboard")
            self.assertEqual(button.url, "https://your-app.example.com")
            self.assertIsNone(button.web_app)

    async def test_rejected_private_button_still_sends_the_link(self):
        sent = []

        class Message:
            async def reply_text(self, text, reply_markup=None):
                if reply_markup is not None:
                    raise TelegramError("web_app buttons are only allowed in private chats")
                sent.append(text)

        class Update:
            message = Message()
            effective_user = type("User", (), {"id": 111})()
            effective_chat = type("Chat", (), {"type": "private"})()

        class Settings:
            dashboard_public_url = "https://your-app.example.com"

            def label_for_user(self, user_id):
                return "Alex"

        await reply_with_dashboard(Update(), Settings())

        self.assertEqual(sent, ["open the dashboard here:"])

    async def test_menu_button_is_not_set_without_https(self):
        calls = []

        class Bot:
            async def set_chat_menu_button(self, menu_button=None):
                calls.append(menu_button)

        class Application:
            bot = Bot()

        class Dashboard:
            def set_bot_username(self, username):
                self.username = username

        class Server:
            dashboard_app = Dashboard()

        settings = type("Settings", (), {"dashboard_public_url": ""})()
        server = Server()
        await configure_dashboard_menu(Application(), settings, server, "sample_bot")

        self.assertEqual(calls, [])
        self.assertEqual(server.dashboard_app.username, "sample_bot")

        settings.dashboard_public_url = "https://your-app.example.com"
        await configure_dashboard_menu(Application(), settings, server, "sample_bot")
        self.assertEqual(calls[0].text, "Dashboard")
        self.assertEqual(calls[0].web_app.url, "https://your-app.example.com")

    def test_either_person_can_fix_a_row_and_a_locked_visit_cannot(self):
        sheets = EditableSheets()
        app, _sheets = app_for(sheets)
        _status, headers, _payload = app.handle(
            "POST",
            "/api/session/widget",
            {"Content-Type": "application/json"},
            json.dumps(sign_widget(111)).encode(),
            secure=True,
        )
        cookie = dict(headers)["Set-Cookie"].split(";", 1)[0]

        locked, _, locked_body = app.handle(
            "POST",
            "/api/dashboard/edit",
            {"Content-Type": "application/json"},
            json.dumps({"id": "aa1001", "source": "expense", "field": "amount", "value": "9.00"}).encode(),
            secure=True,
        )
        self.assertEqual(locked, 401)
        self.assertEqual(sheets.writes, [])
        self.assertNotIn(b"Sample cafe", locked_body)

        saved_status, _, saved_body = app.handle(
            "POST",
            "/api/dashboard/edit",
            {"Content-Type": "application/json", "Cookie": cookie},
            json.dumps({
                "id": "aa1001",
                "source": "expense",
                "field": "card",
                "value": "Sample Mastercard",
                "owner": "Sam",
                "scope": "both",
                "cards": "other",
            }).encode(),
            secure=True,
        )

        self.assertEqual(saved_status, 200)
        saved = body_json(saved_body)
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["cards"]["owner"], "Sam")
        self.assertEqual(sheets.writes[0][2]["payment_owner"], "Sam")
        self.assertEqual(sheets.writes[0][2]["payment_method"], "Sample Mastercard")
        self.assertNotIn("logged_by", sheets.writes[0][2])
        self.assertEqual(sheets.records[0].logged_by, "Alex")
        self.assertEqual(sheets.records[0].payment_owner, "Sam")

    def test_income_card_edit_does_not_write(self):
        sheets = EditableSheets()
        sheets.records.append(
            ExpenseRecord(
                row_number=3,
                entry_id="aa1005",
                timestamp="13:00:00",
                expense_date="2026-09-20",
                month="2026-09",
                logged_by="Alex",
                raw_input="sample pay",
                amount=Decimal("100.00"),
                category="Income - Sample Pay",
                description="Sample pay",
                input_type="text",
                status="Confirmed",
                transaction_type="Income",
            )
        )
        app, _sheets = app_for(sheets)
        _status, headers, _payload = app.handle(
            "POST",
            "/api/session/widget",
            {"Content-Type": "application/json"},
            json.dumps(sign_widget(111)).encode(),
            secure=True,
        )
        cookie = dict(headers)["Set-Cookie"].split(";", 1)[0]

        status, _, payload = app.handle(
            "POST",
            "/api/dashboard/edit",
            {"Content-Type": "application/json", "Cookie": cookie},
            json.dumps({"id": "aa1005", "source": "expense", "field": "card", "value": "Sample Visa", "owner": "Alex"}).encode(),
            secure=True,
        )

        self.assertEqual(status, 400)
        self.assertEqual(body_json(payload)["message"], "Income has no card.")
        self.assertEqual(sheets.writes, [])

    def test_fix_tagging_switches_transaction_type_and_lists_expenses_first(self):
        from getrichbot import categories
        from getrichbot.categories import configure_category_config

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
                "variable_categories": ["Food", "Transport", "Income - Misc"],
                "fixed_categories": ["Rent or mortgage"],
                "category_keywords": {"Transport": ["transport"]},
                "priority_keywords": [],
                "shopping_keywords": [],
                "shopping_categories": {},
                "category_aliases": {},
                "source": "test",
            }
        )
        try:
            sheets = EditableSheets()
            sheets.records.append(
                ExpenseRecord(
                    row_number=8,
                    entry_id="cc3111",
                    timestamp="14:42:00",
                    expense_date="2026-10-13",
                    month="2026-10",
                    logged_by="Alex",
                    raw_input="email: GPAY NETWORK (S) PTE L Singapore SGP",
                    amount=Decimal("9.60"),
                    category="Income - Misc",
                    description="GPAY NETWORK (S) PTE L Singapore SGP",
                    input_type="Email",
                    status="Confirmed",
                    transaction_type="Income",
                    payment_method="Sample Visa",
                    payment_owner="Alex",
                    payment_channel="All",
                )
            )

            rewrite_calls = []

            def rewrite_month(sheet_name, rows, timeout_seconds=None):
                rewrite_calls.append(timeout_seconds)
                raise TimeoutError("Monthly summary rewrite exceeded 20 seconds.")

            sheets.update_monthly_summary = rewrite_month
            app, _sheets = app_for(sheets)
            _status, headers, _payload = app.handle(
                "POST",
                "/api/session/widget",
                {"Content-Type": "application/json"},
                json.dumps(sign_widget(111)).encode(),
                secure=True,
            )
            cookie = dict(headers)["Set-Cookie"].split(";", 1)[0]
            signed = {"Content-Type": "application/json", "Cookie": cookie}

            expense_status, _, expense_body = app.handle(
                "POST",
                "/api/dashboard/edit",
                signed,
                json.dumps({
                    "id": "cc3111",
                    "source": "expense",
                    "field": "category",
                    "value": "Transport",
                }).encode(),
                secure=True,
            )
            self.assertEqual(expense_status, 200)
            expense_saved = body_json(expense_body)
            self.assertEqual(expense_saved["edit_choices"]["category_picker"], ["Food", "Transport", "Income - Misc"])
            self.assertLess(
                expense_saved["edit_choices"]["category_picker"].index("Food"),
                expense_saved["edit_choices"]["category_picker"].index("Income - Misc"),
            )
            income_write = sheets.writes[0][2]
            self.assertEqual(income_write["category"], "Transport")
            self.assertEqual(income_write["transaction_type"], "Expense")
            self.assertIsNone(income_write["payment_method"])
            self.assertIsNone(income_write["payment_owner"])
            self.assertIsNone(income_write["payment_channel"])
            changed = next(row for row in sheets.records if row.entry_id == "cc3111")
            self.assertEqual(changed.transaction_type, "Expense")
            self.assertEqual(changed.payment_method, "Sample Visa")
            shown = next(item for item in expense_saved["recent"] if item["id"] == "cc3111")
            self.assertEqual(shown["category"], "Transport")
            self.assertEqual(shown["kind"], "expense")
            self.assertEqual(shown["payment_method"], "Sample Visa")
            self.assertEqual(rewrite_calls[0], 20)
            self.assertFalse(hasattr(sheets, "summary_rows"))
            self.assertEqual(changed.payment_owner, "Alex")
            self.assertEqual(
                sheets.keywords,
                [("Category Keywords", "gpay network (s) pte l singapore sgp", "Transport")],
            )

            income_status, _, income_body = app.handle(
                "POST",
                "/api/dashboard/edit",
                signed,
                json.dumps({
                    "id": "aa1001",
                    "source": "expense",
                    "field": "category",
                    "value": "Income - Misc",
                }).encode(),
                secure=True,
            )
            self.assertEqual(income_status, 200)
            self.assertEqual(body_json(income_body)["edit_choices"]["category_picker"][0], "Food")
            cleared = sheets.writes[1][2]
            self.assertEqual(cleared["category"], "Income - Misc")
            self.assertEqual(cleared["transaction_type"], "Income")
            self.assertEqual(cleared["payment_method"], "")
            self.assertEqual(cleared["payment_owner"], "")
            self.assertEqual(cleared["payment_channel"], "")
            original_row = next(row for row in sheets.records if row.entry_id == "aa1001")
            self.assertEqual(original_row.transaction_type, "Income")
            self.assertEqual(original_row.payment_method, "")
            self.assertEqual(original_row.payment_owner, "")
            self.assertEqual(original_row.payment_channel, "")
        finally:
            configure_category_config(original)

    def test_delete_does_not_run_until_confirm(self):
        sheets = EditableSheets()
        sheets.records.append(
            ExpenseRecord(
                row_number=4,
                entry_id="bb2002",
                timestamp="15:00:00",
                expense_date="2026-09-21",
                month="2026-09",
                logged_by="Sam",
                raw_input="sample books",
                amount=Decimal("12.00"),
                category="Shopping - Person B",
                description="Sample books",
                input_type="text",
                status="Confirmed",
                transaction_type="Expense",
                payment_method="Sample Mastercard",
                payment_owner="Sam",
            )
        )
        app, _sheets = app_for(sheets)
        _status, headers, _payload = app.handle(
            "POST",
            "/api/session/widget",
            {"Content-Type": "application/json"},
            json.dumps(sign_widget(111)).encode(),
            secure=True,
        )
        cookie = dict(headers)["Set-Cookie"].split(";", 1)[0]
        signed = {"Content-Type": "application/json", "Cookie": cookie}

        locked, _, _locked_body = app.handle(
            "POST",
            "/api/dashboard/delete",
            {"Content-Type": "application/json"},
            json.dumps({"id": "aa1001", "source": "expense", "confirm": True}).encode(),
            secure=True,
        )
        self.assertEqual(locked, 401)

        for extra in ({}, {"confirm": False}, {"confirm": "yes"}):
            status, _, payload = app.handle(
                "POST",
                "/api/dashboard/delete",
                signed,
                json.dumps({"id": "aa1001", "source": "expense", "scope": "both", **extra}).encode(),
                secure=True,
            )
            self.assertEqual(status, 400)
            self.assertEqual(body_json(payload)["error"], "confirm")
            self.assertIn(b"Nothing was deleted", payload)

        self.assertEqual(sheets.deletes, [])
        self.assertEqual([record.entry_id for record in sheets.records], ["aa1001", "bb2002"])
        self.assertFalse(hasattr(sheets, "summary_rows"))

        deleted_status, _, deleted_body = app.handle(
            "POST",
            "/api/dashboard/delete",
            signed,
            json.dumps({
                "id": "aa1001",
                "source": "expense",
                "confirm": True,
                "scope": "both",
            }).encode(),
            secure=True,
        )

        self.assertEqual(deleted_status, 200)
        deleted = body_json(deleted_body)
        self.assertTrue(deleted["ok"])
        self.assertEqual(sheets.deletes, [("Raw Expenses", "aa1001")])
        self.assertEqual([record.entry_id for record in sheets.records], ["bb2002"])
        self.assertTrue(all(item["id"] != "aa1001" for item in deleted["recent"]))
        self.assertTrue(any(item["id"] == "bb2002" and item["logged_by"] == "Sam" for item in deleted["recent"]))
        self.assertTrue(hasattr(sheets, "summary_rows"))
        self.assertNotIn(b"Andy", deleted_body)

    async def test_menu_button_failure_does_not_stop_startup(self):
        class Bot:
            async def set_chat_menu_button(self, menu_button=None):
                raise TelegramError("menu button failed")

        class Application:
            bot = Bot()

        class Dashboard:
            def set_bot_username(self, username):
                self.username = username

        class Server:
            dashboard_app = Dashboard()

        settings = type("Settings", (), {"dashboard_public_url": "https://your-app.example.com"})()
        await configure_dashboard_menu(Application(), settings, Server(), "sample_bot")


if __name__ == "__main__":
    unittest.main()
