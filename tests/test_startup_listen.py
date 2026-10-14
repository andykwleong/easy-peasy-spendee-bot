import threading
import time
import unittest
from http.client import HTTPConnection

from getrichbot import dashboard_http
from getrichbot.bot import listen_for_page_and_gmail, load_category_config_from_sheets, open_dashboard_then_load_categories
from getrichbot.categories import category_config_status
from getrichbot.sheets import SheetsClient


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

    def label_for_user(self, user_id: int) -> str:
        return "Alex"


class _PageDuringRead:
    def __init__(self):
        self.server = None
        self.status = None

    def get_category_config(self, categories_sheet, keywords_sheet):
        host, port = self.server.server_address
        connection = HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/")
        response = connection.getresponse()
        self.status = response.status
        response.read()
        connection.close()
        return {"variable_categories": ["Food"]}


class _HangService:
    def __init__(self, release: threading.Event):
        self.release = release
        self.calls = 0

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, **kwargs):
        self.calls += 1
        return self

    def execute(self):
        self.release.wait(30)
        return {"values": []}


class _RecordingService:
    def __init__(self, rows):
        self.rows = rows
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
        return {"values": self.rows}


class TestStartupListen(unittest.TestCase):
    def test_page_answers_before_the_category_sheet_read_finishes(self):
        sheets = _PageDuringRead()
        original = dashboard_http.start_dashboard_server

        def start(settings, sheets_arg, *, host="0.0.0.0", port=None):
            server = original(settings, sheets_arg, host="127.0.0.1", port=0)
            sheets.server = server
            return server

        dashboard_http.start_dashboard_server = start
        server = None
        try:
            server, config = open_dashboard_then_load_categories(_Settings(), sheets)
        finally:
            dashboard_http.start_dashboard_server = original
            live = server if server is not None else sheets.server
            if live is not None:
                live.shutdown()
                live.server_close()

        self.assertEqual(sheets.status, 200)
        self.assertEqual(config["variable_categories"], ["Food"])
        self.assertIsNone(server.dashboard_app.gmail_push)

    def test_slow_category_sheet_read_stops(self):
        release = threading.Event()
        service = _HangService(release)
        client = SheetsClient("sheet-id")
        client.service = service
        started = time.monotonic()
        try:
            with self.assertRaises(TimeoutError):
                client.get_category_config("Categories", "Category Keywords", timeout_seconds=0.2)
        finally:
            release.set()
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(service.calls, 1)

    def test_category_read_sets_a_socket_deadline_and_restores_it(self):
        class _Service:
            def __init__(self):
                self.seen = []
                self.inner = type("Inner", (), {"timeout": None})()
                self.http = type("Http", (), {"http": self.inner})()

            def spreadsheets(self):
                return self

            def values(self):
                return self

            def get(self, **kwargs):
                return self

            def execute(self):
                self.seen.append(self.inner.timeout)
                return {"values": [["Food", "Variable", "TRUE", ""]]}

        service = _Service()
        client = SheetsClient("sheet-id")
        client.service = service
        config = client.get_category_config("Categories", "Category Keywords", timeout_seconds=5)
        self.assertEqual(config["variable_categories"], ["Food"])
        self.assertEqual(service.seen, [5, 5])
        self.assertIsNone(service.inner.timeout)

    def test_other_sheet_reads_have_no_deadline(self):
        service = _RecordingService([["visa"]])
        client = SheetsClient("sheet-id")
        client.service = service
        source, rows = client._get_values_first_available(["Payment Methods"], "A:Z")
        self.assertEqual(source, "Payment Methods")
        self.assertEqual(rows, [["visa"]])
        self.assertEqual(service.seen, [None])

    def test_one_unanswered_category_read_does_not_return_placeholders(self):
        class _Sheets:
            def get_category_config(self, categories_sheet, keywords_sheet):
                raise TimeoutError("Google Sheet read exceeded 20 seconds.")

        with self.assertRaises(TimeoutError):
            load_category_config_from_sheets(_Settings(), _Sheets())

    def test_unanswered_category_sheet_retries_and_the_page_stays_up(self):
        class _Sheets:
            def __init__(self):
                self.calls = 0
                self.server = None
                self.status_during_retry = None

            def get_category_config(self, categories_sheet, keywords_sheet):
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("Google Sheet read exceeded 20 seconds.")
                return {"variable_categories": ["Food"], "source": "google_sheets"}

        sheets = _Sheets()
        before = category_config_status()
        original = dashboard_http.start_dashboard_server

        def start(settings, sheets_arg, *, host="0.0.0.0", port=None):
            server = original(settings, sheets_arg, host="127.0.0.1", port=0)
            sheets.server = server
            return server

        def pause(seconds):
            self.assertEqual(seconds, 15)
            host, port = sheets.server.server_address
            connection = HTTPConnection(host, port, timeout=2)
            connection.request("GET", "/")
            response = connection.getresponse()
            sheets.status_during_retry = response.status
            response.read()
            connection.close()

        dashboard_http.start_dashboard_server = start
        server = None
        try:
            server, config = open_dashboard_then_load_categories(
                _Settings(),
                sheets,
                pause=pause,
                retry_seconds=15,
            )
            host, port = server.server_address
            connection = HTTPConnection(host, port, timeout=2)
            connection.request("GET", "/")
            response = connection.getresponse()
            still_up = response.status
            response.read()
            connection.close()
        finally:
            dashboard_http.start_dashboard_server = original
            live = server if server is not None else sheets.server
            if live is not None:
                live.shutdown()
                live.server_close()

        self.assertEqual(sheets.calls, 2)
        self.assertEqual(sheets.status_during_retry, 200)
        self.assertEqual(still_up, 200)
        self.assertEqual(config["variable_categories"], ["Food"])
        self.assertEqual(category_config_status(), before)

    def test_empty_category_sheet_is_not_retried(self):
        class _Sheets:
            def __init__(self):
                self.calls = 0
                self.server = None

            def get_category_config(self, categories_sheet, keywords_sheet):
                self.calls += 1
                return {"variable_categories": []}

        sheets = _Sheets()
        pauses = []
        original = dashboard_http.start_dashboard_server

        def start(settings, sheets_arg, *, host="0.0.0.0", port=None):
            server = original(settings, sheets_arg, host="127.0.0.1", port=0)
            sheets.server = server
            return server

        dashboard_http.start_dashboard_server = start
        try:
            with self.assertRaises(RuntimeError) as caught:
                open_dashboard_then_load_categories(
                    _Settings(),
                    sheets,
                    pause=lambda seconds: pauses.append(seconds),
                )
        finally:
            dashboard_http.start_dashboard_server = original
            if sheets.server is not None:
                sheets.server.shutdown()
                sheets.server.server_close()

        self.assertIn("No active categories", str(caught.exception))
        self.assertEqual(sheets.calls, 1)
        self.assertEqual(pauses, [])

    def test_empty_category_sheet_still_fails_loudly(self):
        class _Sheets:
            def get_category_config(self, categories_sheet, keywords_sheet):
                return {"variable_categories": []}

        with self.assertRaises(RuntimeError) as caught:
            load_category_config_from_sheets(_Settings(), _Sheets())
        self.assertIn("No active categories", str(caught.exception))

    def test_gmail_tap_starts_before_the_category_sheet_is_read(self):
        class _Sheets:
            def __init__(self):
                self.category_calls = 0

            def get_category_config(self, categories_sheet, keywords_sheet):
                self.category_calls += 1
                raise AssertionError("The category sheet was read before the Gmail tap.")

        class _Email:
            def __init__(self):
                self.started = False
                self.pushes = []

            def handle_http(self, method, headers, body):
                self.pushes.append(body)
                return 200, {"ok": True}

            def start_tap(self):
                self.started = True

        sheets = _Sheets()
        email = _Email()
        original = dashboard_http.start_dashboard_server

        def start(settings, sheets_arg, *, host="0.0.0.0", port=None):
            return original(settings, sheets_arg, host="127.0.0.1", port=0)

        dashboard_http.start_dashboard_server = start
        server = None
        try:
            server = listen_for_page_and_gmail(_Settings(), sheets, email)
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
