import unittest
from datetime import date
from decimal import Decimal

from getrichbot.categories import SHOPPING_CATEGORIES
from getrichbot.models import ExpenseRecord
from getrichbot.summary import build_monthly_summary_table, build_spending_summary, format_spending_summary, parse_summary_period


def record(expense_date: str, amount: str, category: str, status: str = "Confirmed", input_type: str = "Text", row_number: int = 2) -> ExpenseRecord:
    return ExpenseRecord(
        row_number=row_number,
        entry_id="abc123",
        timestamp="12:00:00",
        expense_date=expense_date,
        month=expense_date[:7],
        logged_by="Me",
        raw_input="test",
        amount=Decimal(amount),
        category=category,
        description="test",
        input_type=input_type,
        status=status,
    )


def record_with_month(expense_date: str, month: str, amount: str, category: str) -> ExpenseRecord:
    item = record(expense_date, amount, category)
    return ExpenseRecord(
        row_number=item.row_number,
        entry_id=item.entry_id,
        timestamp=item.timestamp,
        expense_date=item.expense_date,
        month=month,
        logged_by=item.logged_by,
        raw_input=item.raw_input,
        amount=item.amount,
        category=item.category,
        description=item.description,
        input_type=item.input_type,
        status=item.status,
    )


class TestSummary(unittest.TestCase):
    def test_summary_defaults_to_current_month_checkpoint(self):
        period = parse_summary_period("summary", date(2026, 5, 22))

        self.assertIsNotNone(period)
        self.assertEqual(period.start, date(2026, 5, 1))
        self.assertEqual(period.end, date(2026, 5, 22))
        self.assertEqual(period.label, "May 2026")

    def test_summary_last_month_uses_full_previous_month(self):
        period = parse_summary_period("summary last month", date(2026, 5, 22))

        self.assertIsNotNone(period)
        self.assertEqual(period.start, date(2026, 4, 1))
        self.assertEqual(period.end, date(2026, 4, 30))
        self.assertEqual(period.label, "April 2026")

    def test_build_summary_groups_confirmed_rows_by_category(self):
        period = parse_summary_period("summary", date(2026, 5, 22))
        summary = build_spending_summary(
            [
                record("2026-05-01", "10.50", "Food"),
                record("2026-05-22", "5.25", "Food"),
                record("2026-05-10", "20", "Groceries"),
                record("2026-04-30", "99", "Food"),
                record("2026-05-12", "88", "Travel", status="Pending"),
            ],
            period,
        )

        self.assertEqual(summary.total, Decimal("35.75"))
        self.assertEqual([(item.category, item.total) for item in summary.categories], [
            ("Groceries", Decimal("20")),
            ("Food", Decimal("15.75")),
        ])

    def test_format_summary_omits_entry_count(self):
        period = parse_summary_period("summary", date(2026, 5, 22))
        summary = build_spending_summary([record("2026-05-03", "15", "Food")], period)

        message = format_spending_summary(summary)

        self.assertIn("May 2026 summary (1 May to 22 May 2026):", message)
        self.assertIn("Food: $15.00", message)
        self.assertIn("Total: $15.00", message)
        self.assertNotIn("Entries", message)

    def test_monthly_summary_table_uses_categories_as_rows_and_months_as_columns(self):
        table = build_monthly_summary_table(
            [
                record("2026-05-03", "15", "Food"),
                record("2026-05-04", "20", "Groceries"),
                record("2026-06-01", "30", "Food"),
            ],
            include_month="2026-07",
        )

        self.assertEqual(table[0], ["Category", "2026-05", "2026-06", "2026-07"])
        self.assertIn(["Groceries", "20.00", "", ""], table)
        self.assertIn(["Food", "15.00", "30.00", ""], table)
        self.assertEqual(table[-1], ["Total", "35.00", "30.00", "0.00"])

    def test_monthly_summary_uses_date_when_month_column_is_wrong(self):
        shopping_category = SHOPPING_CATEGORIES["me"]
        table = build_monthly_summary_table([
            record_with_month("2026-05-20", "2023-05", "23.20", shopping_category)
        ])

        self.assertEqual(table[0], ["Category", "2026-05"])
        self.assertIn([shopping_category, "23.20"], table)

    def test_monthly_summary_includes_confirmed_categories_not_in_config(self):
        table = build_monthly_summary_table([
            record("2026-05-31", "123.45", "Custom Fixed Expense")
        ])

        self.assertIn(["Custom Fixed Expense", "123.45"], table)
        self.assertEqual(table[-1], ["Total", "123.45"])

    def test_monthly_summary_uses_latest_fixed_value_without_summing_duplicates(self):
        table = build_monthly_summary_table([
            record("2026-05-31", "920", "Mortgage", input_type="Fixed", row_number=2),
            record("2026-05-31", "930", "Mortgage", input_type="Fixed", row_number=3),
        ])

        self.assertIn(["Mortgage", "930.00"], table)
        self.assertEqual(table[-1], ["Total", "930.00"])

    def test_monthly_summary_fixed_override_replaces_raw_fixed_value(self):
        table = build_monthly_summary_table(
            [record("2026-05-31", "920", "Mortgage", input_type="Fixed")],
            fixed_overrides={"2026-05": {"Mortgage": Decimal("950")}},
        )

        self.assertIn(["Mortgage", "950.00"], table)
        self.assertEqual(table[-1], ["Total", "950.00"])


if __name__ == "__main__":
    unittest.main()
