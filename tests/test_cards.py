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
    payment_owner: str = "",
    payment_channel: str = "",
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
        payment_owner=payment_owner,
        payment_channel=payment_channel,
    )


def card_usage(
    entry_id: str,
    amount: str,
    payment_method: str,
    usage_date: str = "2026-07-10",
    logged_by: str = "Me",
    payment_owner: str = "",
    payment_channel: str = "",
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
        payment_owner=payment_owner,
        payment_channel=payment_channel,
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

    def test_previous_billing_cycle_uses_configured_reset_day(self):
        config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["Citi Rewards", "My wife", "Credit Card", "Billing", "14", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Limit Amount", "Active"],
                ["Citi Rewards", "My wife", "Shopping - My wife", "1000", "TRUE"],
            ],
        )
        card = config.method_for("My wife", "Citi Rewards")
        start, end = current_card_period(card, date(2026, 8, 15), cycle_offset=-1)

        self.assertEqual(start, date(2026, 7, 14))
        self.assertEqual(end, date(2026, 8, 13))

    def test_summary_can_count_previous_billing_cycle(self):
        config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["Citi Rewards", "My wife", "Credit Card", "Billing", "14", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Limit Amount", "Active"],
                ["Citi Rewards", "My wife", "Shopping - My wife", "1000", "TRUE"],
            ],
        )

        current_item = build_card_summary(
            config,
            [
                record(
                    "wife01",
                    "399.25",
                    "Shopping - My wife",
                    "Citi Rewards",
                    "2026-08-11",
                    "My wife",
                    payment_owner="My wife",
                )
            ],
            "My wife",
            date(2026, 8, 15),
        )[0]
        previous_item = build_card_summary(
            config,
            [
                record(
                    "wife01",
                    "399.25",
                    "Shopping - My wife",
                    "Citi Rewards",
                    "2026-08-11",
                    "My wife",
                    payment_owner="My wife",
                )
            ],
            "My wife",
            date(2026, 8, 15),
            cycle_offset=-1,
        )[0]

        self.assertEqual(current_item.total_spend, Decimal("0"))
        self.assertEqual(previous_item.total_spend, Decimal("399.25"))
        self.assertEqual(previous_item.limits[0].spent, Decimal("399.25"))

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

    def test_channel_specific_limits_count_only_matching_channel(self):
        config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["UOB PP (Blue)", "Me", "Credit Card", "Calendar", "1", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
                ["UOB PP (Blue)", "Me", "All", "PayWave", "600", "TRUE"],
                ["UOB PP (Blue)", "Me", "All", "Online", "600", "TRUE"],
            ],
        )

        item = build_card_summary(
            config,
            [
                record("online", "120", "Food", "UOB PP (Blue)", payment_channel="Online"),
                record("paywve", "80", "Groceries", "UOB PP (Blue)", payment_channel="PayWave"),
                record("blank1", "50", "Food", "UOB PP (Blue)"),
            ],
            "Me",
            date(2026, 7, 10),
        )[0]

        self.assertEqual(config.channel_options_for("Me", "UOB PP (Blue)"), ("PayWave", "Online"))
        self.assertEqual(item.total_spend, Decimal("250"))
        self.assertEqual(item.limits[0].spent, Decimal("80"))
        self.assertEqual(item.limits[1].spent, Decimal("120"))
        message = format_card_summary([item])
        self.assertIn("PayWave - $80.00/$600.00", message)
        self.assertIn("Online - $120.00/$600.00", message)

    def test_all_channel_limit_counts_blank_and_specific_channels(self):
        config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["DBS' Womens", "Me", "Credit Card", "Calendar", "1", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
                ["DBS' Womens", "Me", "All", "All", "1000", "TRUE"],
            ],
        )

        item = build_card_summary(
            config,
            [
                record("online", "120", "Food", "DBS' Womens", payment_channel="Online"),
                record("blank1", "50", "Food", "DBS' Womens"),
            ],
            "Me",
            date(2026, 7, 10),
        )[0]

        self.assertEqual(item.limits[0].spent, Decimal("170"))

    def test_spouse_logged_expense_counts_for_the_selected_card_owner(self):
        items = build_card_summary(
            self.config,
            [
                record(
                    "wife01",
                    "80",
                    "Shopping - My wife",
                    "UOB Lady's",
                    logged_by="My wife",
                    payment_owner="Me",
                )
            ],
            "Me",
            date(2026, 7, 10),
        )

        uob_ladys = next(item for item in items if item.payment_method.name == "UOB Lady's")
        self.assertEqual(uob_ladys.total_spend, Decimal("80"))

    def test_spouse_logged_card_usage_counts_for_the_selected_card_owner(self):
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
            [],
            "Me",
            date(2026, 7, 10),
            [
                card_usage(
                    "claim1",
                    "120",
                    "Citi Rewards",
                    logged_by="My wife",
                    payment_owner="Me",
                )
            ],
        )[0]

        self.assertEqual(item.total_spend, Decimal("120"))
        self.assertEqual(item.limits[0].spent, Decimal("120"))
