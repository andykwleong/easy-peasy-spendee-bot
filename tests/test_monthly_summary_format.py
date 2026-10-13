import threading
import time
import unittest
from unittest.mock import MagicMock

from getrichbot.sheets import SHEETS_TIMEOUT_SECONDS, SheetsClient, sheets_http


class TestMonthlySummaryFormatting(unittest.TestCase):
    def test_monthly_summary_formats_value_columns_as_currency(self):
        service = MagicMock()
        service.spreadsheets().get().execute.return_value = {
            "sheets": [{"properties": {"title": "Monthly Summary", "sheetId": 42}}]
        }
        client = SheetsClient("test-sheet")
        client.service = service

        client.update_monthly_summary("Monthly Summary", [["Category", "2026-07"], ["Food", "12.30"]])

        requests = service.spreadsheets().batchUpdate.call_args.kwargs["body"]["requests"]
        repeat_cell = requests[0]["repeatCell"]
        self.assertEqual(repeat_cell["range"]["sheetId"], 42)
        self.assertEqual(repeat_cell["range"]["startRowIndex"], 1)
        self.assertEqual(repeat_cell["range"]["endRowIndex"], 2)
        self.assertEqual(repeat_cell["range"]["startColumnIndex"], 1)
        self.assertEqual(repeat_cell["range"]["endColumnIndex"], 2)
        self.assertEqual(repeat_cell["cell"]["userEnteredFormat"]["numberFormat"], {
            "type": "CURRENCY",
            "pattern": "$#,##0.00",
        })

    def test_sheet_calls_do_not_overlap_and_time_out(self):
        self.assertEqual(sheets_http().timeout, SHEETS_TIMEOUT_SECONDS)
        client = SheetsClient("test-sheet")
        gate = _GateService()
        client.service = gate

        def read_sheet():
            client.get_expense_records("Raw Expenses")

        first = threading.Thread(target=read_sheet)
        second = threading.Thread(target=read_sheet)
        first.start()
        self.assertTrue(gate.started.wait(1))
        second.start()
        time.sleep(0.05)
        gate.release.set()
        first.join(2)
        second.join(2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(gate.max_active, 1)
        self.assertEqual(gate.calls, 2)


class _GateService:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self._count_lock = threading.Lock()

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, **kwargs):
        return self

    def execute(self):
        with self._count_lock:
            self.calls += 1
            call_no = self.calls
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        if call_no == 1:
            self.started.set()
            self.release.wait(2)
        with self._count_lock:
            self.active -= 1
        return {"values": []}
