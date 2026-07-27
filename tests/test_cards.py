from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from getrichbot.cards import build_card_summary, current_card_period, format_card_summary, parse_payment_config
from getrichbot.models import CardUsageRecord, ExpenseRecord


def record(
    entry_id: str,
    amount: str,
    category: str,
    payment_method: str,
    expense_date: str = "2026-07-10",
    logged_by: str = "Me",
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
        description="test",
        input_type="Text",
        status="Confirmed",
        payment_method=payment_method,
    )


def card_usage(
    entry_id: str,
    amount: str,
    payment_method: str,
    usage_date: str = "2026-07-10",
    logged_by: str = "Me",
) -> CardUsageRecord:
    return CardUsageRecord(
        row_number=2,
        entry_id=entry_id,
        timestamp="12:00:00",
        usage_date=usage_date,
        month=usage_date[:7],
        logged_by=logged_by,
        raw_input="doctor claim",
        amount=Decimal(amount),
        payment_method=payment_method,
        description="doctor claim",
        usage_type="Claimable",
        status="Confirmed",
    )


class TestCardTracking(unittest.TestCase):
    def setUp(self):
        self.config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["UOB Lady's", "Me", "Credit Card", "Billing", "17", "TRUE"],
                ["Citi PremierMiles", "Me", "Credit Card", "Calendar", "1", "TRUE"],
                ["UOB Lady's", "My wife", "Credit Card", "Billing", "17", "TRUE"],
                ["Cash", "Me", "Cash", "Calendar", "1", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Limit Amount", "Active"],
                ["UOB Lady's", "Me", "Food", "750", "TRUE"],
                ["UOB Lady's", "Me", "Groceries", "750", "TRUE"],
                ["UOB Lady's", "My wife", "Food", "750", "TRUE"],
            ],
        )

    def test_billing_cycle_uses_configured_reset_day(self):
        card = self.config.method_for("Me", "UOB Lady's")
        start, end = current_card_period(card, date(2026, 7, 10))
        self.assertEqual(start, date(2026, 6, 17))
        self.assertEqual(end, date(2026, 7, 16))

    def test_summary_keeps_capped_and_uncapped_cards_separate(self):
        items = build_card_summary(
            self.config,
            [
                record("food01", "400", "Food", "UOB Lady's", "2026-07-05"),
                record("groc01", "200", "Groceries", "UOB Lady's", "2026-07-06"),
                record("miles1", "94", "Food", "Citi PremierMiles", "2026-07-08"),
                record("wife01", "700", "Food", "UOB Lady's", "2026-07-08", "My wife"),
            ],
            "Me",
            date(2026, 7, 10),
        )
        message = format_card_summary(items)

        self.assertIn("Capped:\n\nUOB Lady's", message)
        self.assertIn("UOB Lady's", message)
        self.assertIn("Food - $400.00/$750.00 (🟢 53%)", message)
        self.assertIn("Groceries - $200.00/$750.00 (🟢 27%)", message)
        self.assertIn("Uncapped:", message)
        self.assertIn("Citi PremierMiles - $94.00", message)
        self.assertNotIn("to 16 Jul", message)
        self.assertNotIn("$700.00", message)
        self.assertIn("Groceries - $200.00/$750.00 (🟢 27%)\n\nUncapped:\n\nCiti PremierMiles", message)

    def test_overall_limit_counts_every_category(self):
        config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["Citi Rewards", "Me", "Credit Card", "Calendar", "1", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Limit Amount", "Active"],
                ["Citi Rewards", "Me", "All", "100", "TRUE"],
            ],
        )
        item = build_card_summary(
            config,
            [
                record("one111", "60", "Food", "Citi Rewards"),
                record("two222", "35", "Transport/Car", "Citi Rewards"),
            ],
            "Me",
            date(2026, 7, 10),
        )[0]
        self.assertEqual(item.limits[0].spent, Decimal("95"))
        self.assertIn("Citi Rewards - $95.00/$100.00 (🔴 95%)", format_card_summary([item]))

    def test_blank_active_limit_amount_keeps_card_uncapped(self):
        config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["Citi PremierMiles", "Me", "Credit Card", "Calendar", "1", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Limit Amount", "Active"],
                ["Citi PremierMiles", "Me", "All", "", "TRUE"],
            ],
        )

        item = build_card_summary(
            config,
            [record("miles1", "94", "Food", "Citi PremierMiles")],
            "Me",
            date(2026, 7, 10),
        )[0]

        self.assertEqual(item.limits, ())
        self.assertIn("Uncapped:", format_card_summary([item]))
        self.assertIn("$94.00", format_card_summary([item]))

    def test_card_usage_counts_towards_overall_limit_and_total_spend(self):
        config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["Citi Rewards", "Me", "Credit Card", "Calendar", "1", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Limit Amount", "Active"],
                ["Citi Rewards", "Me", "All", "1000", "TRUE"],
            ],
        )

        item = build_card_summary(
            config,
            [record("food01", "60", "Food", "Citi Rewards")],
            "Me",
            date(2026, 7, 10),
            [card_usage("claim1", "120", "Citi Rewards")],
        )[0]

        self.assertEqual(item.total_spend, Decimal("180"))
        self.assertEqual(item.limits[0].spent, Decimal("180"))
        self.assertIn("Citi Rewards - $180.00/$1,000.00", format_card_summary([item]))

    def test_card_usage_does_not_count_towards_category_specific_limit(self):
        item = build_card_summary(
            self.config,
            [record("food01", "60", "Food", "UOB Lady's")],
            "Me",
            date(2026, 7, 10),
            [card_usage("claim1", "120", "UOB Lady's")],
        )[0]

        self.assertEqual(item.total_spend, Decimal("180"))
        self.assertEqual(item.limits[0].spent, Decimal("60"))
        self.assertEqual(item.limits[1].spent, Decimal("0"))
