import unittest
from unittest.mock import MagicMock

from getrichbot.sheets import SheetsClient


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
        self.assertEqual(repeat_cell["range"]["startColumnIndex"], 1)
        self.assertEqual(repeat_cell["cell"]["userEnteredFormat"]["numberFormat"], {
            "type": "CURRENCY",
            "pattern": "$#,##0.00",
        })
