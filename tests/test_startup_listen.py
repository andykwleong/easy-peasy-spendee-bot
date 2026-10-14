import socket
import threading
import time
import unittest
from http.client import HTTPConnection

from getrichbot import dashboard_http
from getrichbot.categories import category_config_status
from getrichbot.sheets import SheetsClient
from getrichbot.sheets import authorized_sheets_http
from getrichbot.startup import load_category_config_from_sheets
from getrichbot.startup import load_category_config_until_ready
from getrichbot.startup import start_page_and_mail


class _Settings:
    telegram_bot_token = "test-token"
    me_telegram_ids = {111}
    wife_telegram_ids = {222}
    raw_expenses_sheet = "Raw Expenses"
    card_usage_sheet = "Card Usage"
    payment_methods_sheet = "Payment Methods"
    card_limits_sheet = "Card Limits"
    monthly_summary_sheet = "Monthly Summary"
    categories_sheet = "Categories"
    category_keywords_sheet = "Category Keywords"
    google_sheet_id = "sheet-id"
    service_account_file = None
    service_account_json = None

    def label_for_user(self, user_id: int) -> str:
        return "Alex"


class _Email:
    def __init__(self):
        self.started = False
        self.pushes = []

    def handle_http(self, method, headers, body):
        self.pushes.append(body)
        return 200, {"ok": True}

    def start_tap(self):
        self.started = True


class _PageDuringRead:
    def __init__(self):
        self.server = None
        self.status = None

    def get_category_config(self, categories_sheet, keywords_sheet, timeout_seconds=20):
        host, port = self.server.server_address
        connection = HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/")
        response = connection.getresponse()
        self.status = response.status
        response.read()
        connection.close()
        raise RuntimeError("No active categories found in Google Sheets.")


class TestStartupListen(unittest.TestCase):
    def test_page_is_listening_with_the_gmail_reader_before_categories_finish(self):
        sheets = _PageDuringRead()
        email = _Email()
        original = dashboard_http.start_dashboard_server

        def start(settings, sheets_arg, *, host="0.0.0.0", port=None, gmail_push=None):
            server = original(settings, sheets_arg, host="127.0.0.1", port=0, gmail_push=gmail_push)
            sheets.server = server
            return server

        dashboard_http.start_dashboard_server = start
        server = None
        try:
            server = start_page_and_mail(_Settings(), _Settings(), email, category_sheets=sheets)
            deadline = time.monotonic() + 2
            while sheets.status is None and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            dashboard_http.start_dashboard_server = original
            if server is not None:
                server.shutdown()
                server.server_close()

        self.assertEqual(sheets.status, 200)
        self.assertTrue(email.started)
        self.assertIsNotNone(server.dashboard_app.gmail_push)

    def test_category_read_turns_a_socket_timeout_into_timeout_error(self):
        class _Service:
            def __init__(self):
                self.calls = 0

            def spreadsheets(self):
                return self

            def values(self):
                return self

            def get(self, **kwargs):
                self.calls += 1
                return self

            def execute(self):
                raise socket.timeout("timed out")

        service = _Service()
        client = SheetsClient("sheet-id")
        client.service = service
        with self.assertRaises(TimeoutError):
            client.get_category_config("Categories", "Category Keywords", timeout_seconds=0.2)
        self.assertEqual(service.calls, 1)

    def test_category_connection_is_built_with_the_deadline(self):
        http = authorized_sheets_http(object(), 5)
        self.assertEqual(http.http.timeout, 5)

    def test_other_sheet_reads_have_no_deadline(self):
        class _RecordingService:
            def __init__(self):
                self.seen = []
                self.http = type("Inner", (), {"timeout": None})()

            def spreadsheets(self):
                return self

            def values(self):
                return self

            def get(self, **kwargs):
                return self

            def execute(self):
                self.seen.append(self.http.timeout)
                return {"values": [["visa"]]}

        service = _RecordingService()
        client = SheetsClient("sheet-id")
        client.service = service
        source, rows = client._get_values_first_available(["Payment Methods"], "A:Z")
        self.assertEqual(source, "Payment Methods")
        self.assertEqual(rows, [["visa"]])
        self.assertEqual(service.seen, [None])

    def test_one_unanswered_category_read_does_not_return_placeholders(self):
        class _Sheets:
            def get_category_config(self, categories_sheet, keywords_sheet, timeout_seconds=20):
                raise TimeoutError("Google Sheet read exceeded 20 seconds.")

        with self.assertRaises(TimeoutError):
            load_category_config_from_sheets(_Settings(), _Sheets())

    def test_unanswered_category_sheet_retries_and_the_page_stays_up(self):
        class _Sheets:
            def __init__(self):
                self.calls = 0

            def get_category_config(self, categories_sheet, keywords_sheet, timeout_seconds=20):
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("Google Sheet read exceeded 20 seconds.")
                return {"variable_categories": ["Food"], "source": "google_sheets"}

        class _Hold:
            def __init__(self, release):
                self.release = release

            def get_category_config(self, categories_sheet, keywords_sheet, timeout_seconds=20):
                self.release.wait(5)
                raise RuntimeError("No active categories found in Google Sheets.")

        sheets = _Sheets()
        release = threading.Event()
        before = category_config_status()
        original = dashboard_http.start_dashboard_server

        def start(settings, sheets_arg, *, host="0.0.0.0", port=None, gmail_push=None):
            return original(settings, sheets_arg, host="127.0.0.1", port=0, gmail_push=gmail_push)

        dashboard_http.start_dashboard_server = start
        server = None
        try:
            server = start_page_and_mail(_Settings(), _Settings(), _Email(), category_sheets=_Hold(release))

            def pause(seconds):
                self.assertEqual(seconds, 15)
                host, port = server.server_address
                connection = HTTPConnection(host, port, timeout=2)
                connection.request("GET", "/")
                response = connection.getresponse()
                sheets.status_during_retry = response.status
                response.read()
                connection.close()

            config = load_category_config_until_ready(_Settings(), sheets, pause=pause, retry_seconds=15)
            host, port = server.server_address
            connection = HTTPConnection(host, port, timeout=2)
            connection.request("GET", "/")
            response = connection.getresponse()
            still_up = response.status
            response.read()
            connection.close()
        finally:
            release.set()
            dashboard_http.start_dashboard_server = original
            if server is not None:
                server.shutdown()
                server.server_close()

        self.assertEqual(sheets.calls, 2)
        self.assertEqual(sheets.status_during_retry, 200)
        self.assertEqual(still_up, 200)
        self.assertEqual(config["variable_categories"], ["Food"])
        self.assertEqual(category_config_status(), before)

    def test_empty_category_sheet_is_not_retried(self):
        class _Sheets:
            def __init__(self):
                self.calls = 0

            def get_category_config(self, categories_sheet, keywords_sheet, timeout_seconds=20):
                self.calls += 1
                return {"variable_categories": []}

        sheets = _Sheets()
        pauses = []
        with self.assertRaises(RuntimeError) as caught:
            load_category_config_until_ready(
                _Settings(),
                sheets,
                pause=lambda seconds: pauses.append(seconds),
            )
        self.assertIn("No active categories", str(caught.exception))
        self.assertEqual(sheets.calls, 1)
        self.assertEqual(pauses, [])

    def test_empty_category_sheet_still_fails_loudly(self):
        class _Sheets:
            def get_category_config(self, categories_sheet, keywords_sheet, timeout_seconds=20):
                return {"variable_categories": []}

        with self.assertRaises(RuntimeError) as caught:
            load_category_config_from_sheets(_Settings(), _Sheets())
        self.assertIn("No active categories", str(caught.exception))

    def test_gmail_reader_is_attached_before_the_category_sheet_is_read(self):
        class _Sheets:
            def __init__(self):
                self.category_calls = 0

            def get_category_config(self, categories_sheet, keywords_sheet, timeout_seconds=20):
                self.category_calls += 1
                raise AssertionError("The mailbox connection read the category sheet.")

        class _SlowCategories:
            def __init__(self):
                self.started = threading.Event()

            def get_category_config(self, categories_sheet, keywords_sheet, timeout_seconds=20):
                self.started.set()
                raise TimeoutError("Google Sheet read exceeded 20 seconds.")

        sheets = _Sheets()
        categories = _SlowCategories()
        email = _Email()
        original = dashboard_http.start_dashboard_server

        def start(settings, sheets_arg, *, host="0.0.0.0", port=None, gmail_push=None):
            return original(settings, sheets_arg, host="127.0.0.1", port=0, gmail_push=gmail_push)

        dashboard_http.start_dashboard_server = start
        server = None
        try:
            server = start_page_and_mail(_Settings(), sheets, email, category_sheets=categories)
            self.assertTrue(email.started)
            self.assertIsNotNone(server.dashboard_app.gmail_push)
            self.assertEqual(sheets.category_calls, 0)
            host, port = server.server_address
            connection = HTTPConnection(host, port, timeout=2)
            connection.request("GET", "/")
            response = connection.getresponse()
            page_status = response.status
            response.read()
            connection.close()
            connection = HTTPConnection(host, port, timeout=2)
            connection.request("POST", "/gmail/push", body=b"{}")
            response = connection.getresponse()
            push_status = response.status
            response.read()
            connection.close()
            self.assertTrue(categories.started.wait(2))
        finally:
            dashboard_http.start_dashboard_server = original
            if server is not None:
                server.shutdown()
                server.server_close()

        self.assertEqual(page_status, 200)
        self.assertEqual(push_status, 200)
        self.assertEqual(email.pushes, [b"{}"])
        self.assertEqual(sheets.category_calls, 0)


if __name__ == "__main__":
    unittest.main()
