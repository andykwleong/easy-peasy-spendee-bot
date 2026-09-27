from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from getrichbot import categories
from getrichbot.cards import parse_payment_config
from getrichbot.categories import configure_category_config
from getrichbot.dashboard_view import build_dashboard_payload, limit_band
from getrichbot.models import CardUsageRecord, ExpenseRecord


def expense(
    entry_id: str,
    amount: str,
    category: str,
    *,
    logged_by: str = "Alex",
    expense_date: str = "2026-09-20",
    payment_method: str = "",
    payment_owner: str = "",
    payment_channel: str = "",
    transaction_type: str = "Expense",
    status: str = "Confirmed",
    description: str = "",
    timestamp: str = "12:00:00",
) -> ExpenseRecord:
    return ExpenseRecord(
        row_number=2,
        entry_id=entry_id,
        timestamp=timestamp,
        expense_date=expense_date,
        month=expense_date[:7],
        logged_by=logged_by,
        raw_input="sample",
        amount=Decimal(amount),
        category=category,
        description=description or category,
        input_type="fixed" if transaction_type == "Fixed" else "text",
        status=status,
        transaction_type=transaction_type,
        payment_method=payment_method,
        payment_owner=payment_owner,
        payment_channel=payment_channel,
    )


def card_usage(
    entry_id: str,
    amount: str,
    payment_method: str,
    *,
    logged_by: str = "Alex",
    usage_date: str = "2026-09-18",
    payment_owner: str = "Alex",
    description: str = "Sample clinic",
) -> CardUsageRecord:
    return CardUsageRecord(
        row_number=2,
        entry_id=entry_id,
        timestamp="09:00:00",
        usage_date=usage_date,
        month=usage_date[:7],
        logged_by=logged_by,
        raw_input="card only",
        amount=Decimal(amount),
        payment_method=payment_method,
        description=description,
        usage_type="Claimable",
        status="Confirmed",
        payment_owner=payment_owner,
        payment_channel="Online",
    )


def payment_config():
    return parse_payment_config(
        [
            ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
            ["Sample Visa", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
            ["Sample Shop Card", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
            ["Sample Travel Card", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
            ["Sample Cashback", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
            ["Sample Cash", "Alex", "Cash", "Calendar", "1", "TRUE"],
            ["Sample Mastercard", "Sam", "Credit Card", "Calendar", "1", "TRUE"],
        ],
        [
            ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
            ["Sample Visa", "Alex", "All", "All", "100", "TRUE"],
            ["Sample Shop Card", "Alex", "Food", "All", "100", "TRUE"],
            ["Sample Shop Card", "Alex", "Groceries", "PayWave", "50", "TRUE"],
            ["Sample Travel Card", "Alex", "All", "All", "100", "TRUE"],
            ["Sample Cashback", "Alex", "All", "All", "", "TRUE"],
            ["Sample Mastercard", "Sam", "All", "All", "200", "TRUE"],
        ],
    )


class DashboardViewTests(unittest.TestCase):
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
                "variable_categories": ["Food", "Groceries", "Transport", "Income - Sample Pay"],
                "fixed_categories": ["Sample Bills"],
                "category_keywords": {},
                "priority_keywords": [],
                "shopping_keywords": [],
                "shopping_categories": {},
                "category_aliases": {},
                "source": "test",
            }
        )

    @classmethod
    def tearDownClass(cls):
        configure_category_config(cls.original_config)

    def test_color_bands_match_the_bot(self):
        self.assertEqual(limit_band(Decimal("59.99")), "green")
        self.assertEqual(limit_band(Decimal("60")), "yellow")
        self.assertEqual(limit_band(Decimal("79.99")), "yellow")
        self.assertEqual(limit_band(Decimal("80")), "orange")
        self.assertEqual(limit_band(Decimal("94.99")), "orange")
        self.assertEqual(limit_band(Decimal("95")), "red")

    def test_dashboard_shapes_sheet_rows_without_a_second_copy(self):
        records = [
            expense("aa1001", "60.00", "Food", payment_method="Sample Visa", payment_owner="", description="Sample cafe"),
            expense("aa1002", "80.00", "Food", payment_method="Sample Shop Card", payment_owner="Alex", description="Sample dinner"),
            expense("aa1003", "40.00", "Groceries", payment_method="Sample Shop Card", payment_owner="Alex", payment_channel="PayWave"),
            expense("aa1004", "95.00", "Transport", payment_method="Sample Travel Card", payment_owner="Alex"),
            expense("aa1005", "100.00", "Income - Sample Pay", logged_by="Alex", transaction_type="Income", description="Sample pay"),
            expense("aa1006", "40.00", "Income - Sample Pay", logged_by="Sam", transaction_type="Income", description="Sample other pay"),
            expense("aa1007", "7.00", "Food", logged_by="Alex", payment_method="Sample Mastercard", payment_owner="Sam", description="Sample market"),
            expense("aa1008", "5.00", "Transport", logged_by="Sam", payment_method="Sample Mastercard", payment_owner="Sam"),
            expense("aa1009", "20.00", "Sample Bills", transaction_type="Fixed", description="Sample rent"),
            expense("aa1010", "999.00", "Not A Real Category", description="Should stay out of the month"),
            expense("aa1011", "3.00", "Food", expense_date="2026-08-11", description="Sample august"),
            expense("aa1012", "500.00", "Food", status="Pending", description="Not confirmed"),
        ]
        usage = [
            card_usage("cc1001", "12.00", "Sample Cashback"),
            card_usage("cc1002", "999.00", "Sample Visa", description="Card only sample"),
        ]

        mine = build_dashboard_payload(
            viewer_label="Alex",
            scope="mine",
            records=records,
            card_usage=usage,
            payment_config=payment_config(),
            today=date(2026, 9, 26),
        )
        both = build_dashboard_payload(
            viewer_label="Alex",
            scope="both",
            records=records,
            card_usage=usage,
            payment_config=payment_config(),
            today=date(2026, 9, 26),
        )

        self.assertEqual(
            [item["id"] for item in mine["recent"]],
            ["aa1010", "aa1009", "aa1007", "aa1005", "aa1004", "aa1003", "aa1002", "aa1001", "cc1002", "cc1001", "aa1011"],
        )
        self.assertIn("aa1008", [item["id"] for item in both["recent"]])
        self.assertNotIn("aa1008", [item["id"] for item in mine["recent"]])
        self.assertNotIn("aa1012", [item["id"] for item in both["recent"]])
        partner_card = next(item for item in mine["recent"] if item["id"] == "aa1007")
        self.assertEqual(partner_card["logged_by"], "Alex")
        self.assertEqual(partner_card["payment_owner"], "Sam")
        blank_owner = next(item for item in mine["recent"] if item["id"] == "aa1001")
        self.assertEqual(blank_owner["payment_owner"], "Alex")
        income = next(item for item in mine["recent"] if item["id"] == "aa1005")
        self.assertEqual(income["kind"], "income")
        self.assertEqual(income["payment_method"], "")

        september = mine["months"][1]
        self.assertEqual(september["label"], "September 2026")
        self.assertEqual(september["total_income"], "140.00")
        self.assertEqual(september["income"], [{"category": "Income - Sample Pay", "amount": "140.00"}])
        self.assertNotIn("logged_by", september["income"][0])
        self.assertEqual(september["total_expenses"], "307.00")
        self.assertEqual(september["net"], "-167.00")
        self.assertNotIn("Not A Real Category", [item["category"] for item in september["expenses"]])
        self.assertEqual(mine["months"][0]["total_expenses"], "3.00")
        self.assertFalse(mine["agent_eval"]["scoring"])

        capped = {card["name"]: card for card in mine["cards"]["capped"]}
        uncapped = {card["name"]: card for card in mine["cards"]["uncapped"]}
        self.assertEqual(capped["Sample Visa"]["limits"][0]["band"], "red")
        self.assertEqual(capped["Sample Visa"]["limits"][0]["spent"], "1059.00")
        shop_limits = {item["label"]: item for item in capped["Sample Shop Card"]["limits"]}
        self.assertEqual(shop_limits["Food"]["band"], "orange")
        self.assertEqual(shop_limits["Groceries / PayWave"]["band"], "orange")
        self.assertEqual(capped["Sample Travel Card"]["limits"][0]["band"], "red")
        self.assertEqual(uncapped["Sample Cashback"]["spent"], "12.00")
        self.assertEqual(uncapped["Sample Cashback"]["limits"], [])
        self.assertNotIn("Sample Mastercard", capped)
        self.assertNotIn("Sample Cash", {**capped, **uncapped})
        self.assertEqual(next(row for row in mine["raw_expenses"] if row["id"] == "aa1001")["payment_owner"], "")
        self.assertIn("aa1012", [row["id"] for row in mine["raw_expenses"]])
        self.assertIn("cc1002", [row["id"] for row in mine["card_usage"]])

    def test_recent_and_raw_lists_say_when_they_are_shortened(self):
        records = [
            expense(f"aa{index:04d}", "1.00", "Food", timestamp=f"{index % 24:02d}:00:00", expense_date="2026-09-01")
            for index in range(41)
        ]

        payload = build_dashboard_payload(
            viewer_label="Alex",
            scope="both",
            records=records,
            card_usage=[],
            payment_config=payment_config(),
            today=date(2026, 9, 26),
        )

        self.assertEqual(len(payload["recent"]), 40)
        self.assertTrue(payload["recent_truncated"])


if __name__ == "__main__":
    unittest.main()
