from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from getrichbot.categories import ALL_CATEGORIES
from getrichbot.models import ExpenseRecord


@dataclass(frozen=True)
class SummaryPeriod:
    start: date
    end: date
    label: str


@dataclass(frozen=True)
class CategorySummary:
    category: str
    total: Decimal


@dataclass(frozen=True)
class SpendingSummary:
    period: SummaryPeriod
    categories: list[CategorySummary]
    total: Decimal


def parse_summary_period(text: str, today: date) -> SummaryPeriod | None:
    lowered = " ".join(text.lower().strip().split())
    if lowered.startswith("/summary"):
        lowered = "summary" + lowered[len("/summary") :]

    words = set(lowered.replace("/", " ").split())
    if "summary" not in words:
        return None

    if "last month" in lowered:
        first_this_month = today.replace(day=1)
        end = first_this_month - timedelta(days=1)
        start = end.replace(day=1)
        return SummaryPeriod(start=start, end=end, label=start.strftime("%B %Y"))

    if "this month" in lowered or lowered == "summary" or "summary" in words:
        start = today.replace(day=1)
        return SummaryPeriod(start=start, end=today, label=today.strftime("%B %Y"))

    return None


def build_spending_summary(records: list[ExpenseRecord], period: SummaryPeriod) -> SpendingSummary:
    totals: dict[str, Decimal] = {}
    for record in records:
        if record.status.lower() != "confirmed":
            continue
        try:
            expense_date = date.fromisoformat(record.expense_date)
        except ValueError:
            continue
        if expense_date < period.start or expense_date > period.end:
            continue
        totals[record.category] = totals.get(record.category, Decimal("0")) + record.amount

    categories = [
        CategorySummary(category=category, total=totals[category])
        for category in ALL_CATEGORIES
        if category in totals
    ]
    extra_categories = sorted(category for category in totals if category not in ALL_CATEGORIES)
    categories.extend(CategorySummary(category=category, total=totals[category]) for category in extra_categories)
    total = sum((item.total for item in categories), Decimal("0"))
    return SpendingSummary(period=period, categories=categories, total=total)


def build_monthly_summary_table(records: list[ExpenseRecord], include_month: str | None = None) -> list[list[str]]:
    months = sorted({record.month for record in records if record.status.lower() == "confirmed" and record.month})
    if include_month and include_month not in months:
        months.append(include_month)
        months.sort()

    header = ["Category", *months]
    rows = [header]
    month_totals = {month: Decimal("0") for month in months}
    category_months: dict[str, dict[str, Decimal]] = {category: {} for category in ALL_CATEGORIES}

    for record in records:
        if record.status.lower() != "confirmed" or record.category not in category_months or record.month not in months:
            continue
        category_total = category_months[record.category].get(record.month, Decimal("0")) + record.amount
        category_months[record.category][record.month] = category_total
        month_totals[record.month] += record.amount

    for category in ALL_CATEGORIES:
        rows.append([category, *[_format_optional_amount(category_months[category].get(month)) for month in months]])
    rows.append(["Total", *[f"{month_totals[month]:.2f}" for month in months]])
    return rows


def format_spending_summary(summary: SpendingSummary) -> str:
    start_text = summary.period.start.strftime("%-d %B")
    end_text = summary.period.end.strftime("%-d %B %Y")
    if not summary.categories:
        return f"{summary.period.label} summary ({start_text} to {end_text}):\n\nNo confirmed expenses yet."

    lines = [f"{summary.period.label} summary ({start_text} to {end_text}):"]
    lines.extend(f"{item.category}: ${item.total:.2f}" for item in summary.categories)
    lines.append(f"Total: ${summary.total:.2f}")
    return "\n\n".join(lines)


def _format_optional_amount(value: Decimal | None) -> str:
    if value is None or value == 0:
        return ""
    return f"{value:.2f}"
