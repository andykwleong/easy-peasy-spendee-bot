from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from getrichbot.models import ExpenseRecord
from getrichbot.summary import (
    build_personal_expense_history,
    expense_history_clarification,
    format_personal_expense_history,
    looks_like_expense_history_request,
    parse_expense_history_period,
)


def record(entry_id: str, logged_by: str, expense_date: str, category: str = "Food", transaction_type: str = "Expense") -> ExpenseRecord:
    return ExpenseRecord(
        row_number=2,
        entry_id=entry_id,
        timestamp="12:00:00",
        expense_date=expense_date,
        month=expense_date[:7],
        logged_by=logged_by,
        raw_input="test",
        amount=Decimal("20"),
        category=category,
        description="lunch",
        input_type="Text",
        status="Confirmed",
        transaction_type=transaction_type,
        payment_method="Citi Rewards",
    )


class TestPersonalHistory(unittest.TestCase):
    def test_parses_single_date_and_day_range(self):
        today = date(2026, 7, 13)
        single = parse_expense_history_period("expenses on 12 July", today)
        ranged = parse_expense_history_period("expenses between 10-12 July", today)
        till_range = parse_expense_history_period("expenses from 11 till 14th july", today)

        self.assertEqual((single.start, single.end), (date(2026, 7, 12), date(2026, 7, 12)))
        self.assertEqual((ranged.start, ranged.end), (date(2026, 7, 10), date(2026, 7, 12)))
        self.assertEqual((till_range.start, till_range.end), (date(2026, 7, 11), date(2026, 7, 14)))

    def test_unclear_history_request_can_be_clarified(self):
        text = "expenses from 11"

        self.assertTrue(looks_like_expense_history_request(text))
        self.assertIsNone(parse_expense_history_period(text, date(2026, 7, 13)))
        self.assertIn("expenses from 11 July to 14 July", expense_history_clarification())

    def test_personal_history_only_returns_sender_expenses(self):
        period = parse_expense_history_period("expenses from 10 July to 12 July", date(2026, 7, 13))
        history = build_personal_expense_history(
            [
                record("me1010", "Me", "2026-07-10"),
                record("me1011", "Me", "2026-07-11"),
                record("wife12", "My wife", "2026-07-12"),
                record("income1", "Me", "2026-07-12", "Income - A", "Income"),
                record("fixed01", "Me", "2026-07-12", "Mortgage", "Fixed"),
            ],
            period,
            "Me",
        )
        message = format_personal_expense_history(history)

        self.assertEqual([item.entry_id for item in history.records], ["me1010", "me1011"])
        self.assertIn("via Citi Rewards", message)
        self.assertIn("Total: $40.00", message)
        self.assertNotIn("wife12", message)
