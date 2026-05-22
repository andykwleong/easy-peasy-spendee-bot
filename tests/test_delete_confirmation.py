import unittest
from decimal import Decimal

from getrichbot.bot import FinanceBot
from getrichbot.models import ExpenseRecord


class TestDeleteConfirmation(unittest.TestCase):
    def test_delete_candidate_line_matches_logged_format(self):
        record = ExpenseRecord(
            row_number=2,
            entry_id="d5cf6d",
            timestamp="12:00:00",
            expense_date="2026-05-16",
            month="2026-05",
            logged_by="Me",
            raw_input="dinner",
            amount=Decimal("25.18"),
            category="Food",
            description="Dinner",
            input_type="Text",
            status="Confirmed",
        )

        bot = FinanceBot.__new__(FinanceBot)

        self.assertEqual(
            bot._delete_candidate_line(record),
            "$25.18 logged as Food - 16 May 2026 [d5cf6d]",
        )


if __name__ == "__main__":
    unittest.main()
