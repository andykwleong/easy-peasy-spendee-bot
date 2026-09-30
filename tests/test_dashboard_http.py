from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from decimal import Decimal
from http.client import HTTPConnection
from urllib.parse import urlencode

from telegram.error import TelegramError

from getrichbot.cards import parse_payment_config
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


def sign_widget(user_id: int, auth_date: int = NOW) -> dict:
    fields = {"auth_date": str(auth_date), "first_name": "Alex", "id": str(user_id)}
    data_check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hashlib.sha256(TOKEN.encode()).digest()
    digest = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return {"auth_date": auth_date, "first_name": "Alex", "id": user_id, "hash": digest}


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

    def get_expense_records(self, sheet_name):
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


def labels(user_id: int) -> str | None:
    if user_id == 111:
        return "Alex"
    if user_id == 222:
        return "Sam"
    return None


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

    def test_dashboard_assets_match_the_phone_layout(self):
        root = Path(__file__).resolve().parents[1] / "getrichbot"
        js = (root / "dashboard.js").read_text(encoding="utf-8")
        css = (root / "dashboard.css").read_text(encoding="utf-8")

        self.assertNotIn("Fix tagging", js)
        self.assertNotIn("Agent eval", js)
        self.assertNotIn("Comes later", js)
        self.assertNotIn("setInterval", js)
        self.assertNotIn("On a computer, use Log in with Telegram", js)
        self.assertIn("Left this month", js)
        self.assertIn("No limit", js)
        self.assertIn('["raw", "Entries"]', js)
        self.assertIn("This browser remembers you for 30 days, and Log out forgets it.", js)
        self.assertIn("This page cannot edit the sheet, and changes stay in the Telegram chat.", js)
        self.assertIn("This page only looks, and the sheet is still the record.", js)
        self.assertIn("--paper: #f3efe6", css)
        self.assertIn("radial-gradient", css)
        self.assertIn(".raw-cards", css)
        self.assertIn("nav-at-bottom", css)

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
            "/api/dashboard?scope=mine&label=Sam",
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
