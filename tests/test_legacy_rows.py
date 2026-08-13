import unittest

from getrichbot.sheets import SheetsClient
from getrichbot.summary import build_monthly_summary_table


class _Request:
    def __init__(self, values):
        self.values = values

    def execute(self):
        return {"values": self.values}


class _Values:
    def __init__(self, values):
        self.values = values

    def get(self, **kwargs):
        return _Request(self.values)


class _Spreadsheets:
    def __init__(self, values):
        self.values_client = _Values(values)

    def values(self):
        return self.values_client


class _Service:
    def __init__(self, values):
        self.spreadsheets_client = _Spreadsheets(values)

    def spreadsheets(self):
        return self.spreadsheets_client


class TestLegacyRows(unittest.TestCase):
    def test_pre_payment_method_rows_remain_confirmed_and_in_monthly_summary(self):
        legacy_row = [
            "may001", "12:00:00", "2026-05-20", "2026-05", "Me", "dinner 20",
            "20.00", "Food", "dinner", "Expense", "Text", "Confirmed", "-100", "1",
        ]
        client = SheetsClient("test-sheet")
        client.service = _Service([legacy_row])

        records = client.get_expense_records("Raw Expenses")
        table = build_monthly_summary_table(records)

        self.assertEqual(records[0].input_type, "Text")
        self.assertEqual(records[0].status, "Confirmed")
        self.assertIn(["Food", "20.00"], table)
        self.assertIn(["Total Expenses", "20.00"], table)

    def test_payment_method_rows_without_payment_owner_use_logged_by(self):
        row = [
            "jul001", "12:00:00", "2026-07-20", "2026-07", "My wife", "shopping 80",
            "80.00", "Shopping - My wife", "shopping", "Wife Card", "Expense", "Text",
            "Confirmed", "-100", "2",
        ]
        client = SheetsClient("test-sheet")
        client.service = _Service([row])

        record = client.get_expense_records("Raw Expenses")[0]

        self.assertEqual(record.payment_method, "Wife Card")
        self.assertEqual(record.payment_owner, "My wife")

    def test_payment_owner_rows_keep_logged_by_and_card_owner_separate(self):
        row = [
            "jul002", "12:00:00", "2026-07-20", "2026-07", "My wife", "shopping 80",
            "80.00", "Shopping - My wife", "shopping", "Me", "Citi Rewards", "Expense",
            "Text", "Confirmed", "-100", "3",
        ]
        client = SheetsClient("test-sheet")
        client.service = _Service([row])

        record = client.get_expense_records("Raw Expenses")[0]

        self.assertEqual(record.logged_by, "My wife")
        self.assertEqual(record.payment_owner, "Me")
        self.assertEqual(record.payment_method, "Citi Rewards")

    def test_card_usage_payment_owner_rows_keep_logger_and_card_owner_separate(self):
        row = [
            "jul003", "12:00:00", "2026-07-20", "2026-07", "My wife", "doctor claim 120",
            "120.00", "Me", "Citi Rewards", "doctor claim", "Claimable", "Confirmed", "-100", "4",
        ]
        client = SheetsClient("test-sheet")
        client.service = _Service([row])

        record = client.get_card_usage_records("Card Usage")[0]

        self.assertEqual(record.logged_by, "My wife")
        self.assertEqual(record.payment_owner, "Me")
        self.assertEqual(record.payment_method, "Citi Rewards")
