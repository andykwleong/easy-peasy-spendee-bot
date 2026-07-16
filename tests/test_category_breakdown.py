from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from getrichbot import categories
from getrichbot.categories import configure_category_config
from getrichbot.models import ExpenseRecord
from getrichbot.summary import (
    build_category_breakdown,
    category_breakdown_clarification,
    format_category_breakdown,
    looks_like_category_breakdown_request,
    parse_category_breakdown_request,
)


def record(
    entry_id: str,
    logged_by: str,
    expense_date: str,
    amount: str,
    category: str,
    description: str,
    transaction_type: str = "Expense",
) -> ExpenseRecord:
    return ExpenseRecord(
        row_number=2,
        entry_id=entry_id,
        timestamp="12:00:00",
        expense_date=expense_date,
        month=expense_date[:7],
        logged_by=logged_by,
        raw_input="test",
        amount=Decimal(amount),
        category=category,
        description=description,
        input_type="Text",
        status="Confirmed",
        transaction_type=transaction_type,
        payment_method="Citi Rewards",
    )


class TestCategoryBreakdown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_config = {
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
                "variable_categories": [
                    "Groceries",
                    "Food",
                    "Shopping - Me",
                    "Shopping - My wife",
                    "Bills (Baby)",
                ],
                "fixed_categories": ["Mortgage"],
                "category_keywords": {},
                "priority_keywords": [],
                "shopping_keywords": [],
                "shopping_categories": {"me": "Shopping - Me", "wife": "Shopping - My wife"},
                "category_aliases": {"baby": "Bills (Baby)"},
                "source": "test",
            }
        )

    @classmethod
    def tearDownClass(cls):
        configure_category_config(cls.original_config)

    def test_normal_category_breakdown_lists_both_people(self):
        request = parse_category_breakdown_request(
            "food for june",
            date(2026, 7, 16),
            "Me",
            "Me",
            "My wife",
        )

        self.assertEqual(request.categories, ("Food",))
        breakdown = build_category_breakdown(
            [
                record("andy01", "Me", "2026-06-03", "12.50", "Food", "Lunch"),
                record("wife01", "My wife", "2026-06-04", "18.20", "Food", "Dinner"),
                record("shop01", "Me", "2026-06-05", "80", "Shopping - Me", "Shoes"),
            ],
            request,
        )
        message = format_category_breakdown(breakdown)

        self.assertIn("Food breakdown - June 2026", message)
        self.assertIn("3 Jun - $12.50 - Me - Lunch [andy01]", message)
        self.assertIn("4 Jun - $18.20 - My wife - Dinner [wife01]", message)
        self.assertIn("Total: $30.70", message)
        self.assertNotIn("shop01", message)

    def test_category_breakdown_accepts_month_range_without_from(self):
        request = parse_category_breakdown_request(
            "food may - july",
            date(2026, 7, 16),
            "Me",
            "Me",
            "My wife",
        )

        self.assertEqual(request.categories, ("Food",))
        self.assertEqual(request.period.start, date(2026, 5, 1))
        self.assertEqual(request.period.end, date(2026, 7, 31))

    def test_shopping_defaults_to_requesting_person(self):
        request = parse_category_breakdown_request(
            "shopping for june",
            date(2026, 7, 16),
            "My wife",
            "Me",
            "My wife",
        )

        self.assertEqual(request.categories, ("Shopping - My wife",))
        breakdown = build_category_breakdown(
            [
                record("me001", "Me", "2026-06-03", "80", "Shopping - Me", "Shoes"),
                record("wife01", "My wife", "2026-06-04", "90", "Shopping - My wife", "Dress"),
            ],
            request,
        )
        message = format_category_breakdown(breakdown)

        self.assertIn("Shopping - My wife breakdown - June 2026", message)
        self.assertIn("Dress [wife01]", message)
        self.assertNotIn("Shoes [me001]", message)

    def test_all_shopping_range_groups_by_month(self):
        request = parse_category_breakdown_request(
            "all shopping from may to july",
            date(2026, 7, 16),
            "Me",
            "Me",
            "My wife",
        )

        self.assertEqual(request.categories, ("Shopping - Me", "Shopping - My wife"))
        breakdown = build_category_breakdown(
            [
                record("may001", "Me", "2026-05-03", "20", "Shopping - Me", "Shopee"),
                record("jun001", "My wife", "2026-06-04", "30", "Shopping - My wife", "Guardian"),
                record("jul001", "Me", "2026-07-05", "40", "Shopping - Me", "Uniqlo"),
            ],
            request,
        )
        message = format_category_breakdown(breakdown)

        self.assertIn("Shopping breakdown - May to July 2026", message)
        self.assertIn("May 2026", message)
        self.assertIn("Total: $20.00", message)
        self.assertIn("June 2026", message)
        self.assertIn("Total: $30.00", message)
        self.assertIn("July 2026", message)
        self.assertIn("Total: $40.00", message)
        self.assertIn("Grand total: $90.00", message)

    def test_unclear_category_breakdown_can_be_clarified(self):
        text = "category spending random for june"

        self.assertTrue(looks_like_category_breakdown_request(text))
        self.assertIsNone(parse_category_breakdown_request(text, date(2026, 7, 16), "Me", "Me", "My wife"))
        self.assertIn("food for june", category_breakdown_clarification())
