import threading
import time
import unittest
from http.client import HTTPConnection

from getrichbot import dashboard_http
from getrichbot.bot import load_category_config_from_sheets, open_dashboard_then_load_categories
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

    def test_timed_out_categories_do_not_fall_back(self):
        class _Sheets:
            def get_category_config(self, categories_sheet, keywords_sheet):
                raise TimeoutError("Google Sheet read exceeded 20 seconds.")

        with self.assertRaises(RuntimeError) as caught:
            load_category_config_from_sheets(_Settings(), _Sheets())
        self.assertIn("placeholder categories were not used", str(caught.exception))

    def test_empty_category_sheet_still_fails_loudly(self):
        class _Sheets:
            def get_category_config(self, categories_sheet, keywords_sheet):
                return {"variable_categories": []}

        with self.assertRaises(RuntimeError) as caught:
            load_category_config_from_sheets(_Settings(), _Sheets())
        self.assertIn("No active categories", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
