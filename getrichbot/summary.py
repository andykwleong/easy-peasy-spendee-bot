from __future__ import annotations

import re
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
    total_income: Decimal
    total_expenses: Decimal
    net: Decimal


@dataclass(frozen=True)
class ExpenseHistory:
    period: SummaryPeriod
    records: list[ExpenseRecord]
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


def parse_expense_history_period(text: str, today: date) -> SummaryPeriod | None:
    lowered = " ".join(text.lower().strip().split())
    if not looks_like_expense_history_request(lowered):
        return None

    range_match = re.search(
        r"\b(?:between|from)\s+(\d{1,2})(?:st|nd|rd|th)?\s*(?:-|to|and|till|until|through)\s*"
        r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)(?:\s+(\d{4}))?\b",
        lowered,
    )
    if range_match is not None:
        month = _month_number(range_match.group(3))
        year = int(range_match.group(4) or today.year)
        if month is None:
            return None
        try:
            start = date(year, month, int(range_match.group(1)))
            end = date(year, month, int(range_match.group(2)))
        except ValueError:
            return None
        if end < start:
            return None
        return SummaryPeriod(start=start, end=end, label=f"{start.strftime('%-d %B')} to {end.strftime('%-d %B %Y')}")

    range_match = re.search(r"\b(?:between|from)\s+(.+?)\s+(?:and|to|till|until|through)\s+(.+?)\s*$", lowered)
    if range_match is not None:
        start = _parse_history_date(range_match.group(1), today)
        end = _parse_history_date(range_match.group(2), today)
        if start is not None and end is not None and end >= start:
            return SummaryPeriod(start=start, end=end, label=f"{start.strftime('%-d %B')} to {end.strftime('%-d %B %Y')}")

    on_match = re.search(r"\b(?:on|for)\s+(.+?)\s*$", lowered)
    if on_match is None:
        return None
    target = _parse_history_date(on_match.group(1), today)
    if target is None:
        return None
    return SummaryPeriod(start=target, end=target, label=target.strftime("%-d %B %Y"))


def looks_like_expense_history_request(text: str) -> bool:
    lowered = " ".join(text.lower().strip().split())
    has_history_word = (
        any(word in lowered for word in ("expense", "expenses", "spending", "spent", "keyed", "logged", "entered"))
        or "key in" in lowered
    )
    has_date_phrase = any(
        phrase in lowered
        for phrase in (" on ", " for ", " between ", " from ", " to ", " till ", " until ", " through ")
    )
    return has_history_word and has_date_phrase


def expense_history_clarification() -> str:
    return (
        "I think you are asking for past expenses, but I could not understand the date range.\n\n"
        "Try:\n"
        "expenses from 11 July to 14 July\n"
        "expenses between 11-14 July\n"
        "expenses on 11 July"
    )


def build_personal_expense_history(records: list[ExpenseRecord], period: SummaryPeriod, logged_by: str) -> ExpenseHistory:
    matching: list[ExpenseRecord] = []
    for record in records:
        if record.status.casefold() != "confirmed" or record.transaction_type.casefold() != "expense":
            continue
        if record.logged_by != logged_by:
            continue
        try:
            record_date = date.fromisoformat(record.expense_date)
        except ValueError:
            continue
        if period.start <= record_date <= period.end:
            matching.append(record)
    matching.sort(key=lambda record: (record.expense_date, record.timestamp, record.entry_id))
    return ExpenseHistory(period=period, records=matching, total=sum((record.amount for record in matching), Decimal("0")))


def format_personal_expense_history(history: ExpenseHistory) -> str:
    if not history.records:
        return f"Your expenses - {history.period.label}:\n\nNo confirmed expenses logged."
    lines = [f"Your expenses - {history.period.label}:"]
    for record in history.records:
        payment = f" via {record.payment_method}" if record.payment_method else ""
        lines.append(
            f"${record.amount:,.2f} to {record.category} - {record.description}{payment} [{record.entry_id}]"
        )
    lines.append(f"Total: ${history.total:,.2f}")
    return "\n\n".join(lines)


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
    total_income = sum((item.total for item in categories if _is_income_category(item.category)), Decimal("0"))
    total_expenses = sum((item.total for item in categories if not _is_income_category(item.category)), Decimal("0"))
    return SpendingSummary(
        period=period,
        categories=categories,
        total_income=total_income,
        total_expenses=total_expenses,
        net=total_income - total_expenses,
    )


def build_monthly_summary_table(
    records: list[ExpenseRecord],
    include_month: str | None = None,
    fixed_overrides: dict[str, dict[str, Decimal]] | None = None,
) -> list[list[str]]:
    fixed_overrides = fixed_overrides or {}
    record_months = [_month_from_record_date(record) for record in records if record.status.lower() == "confirmed"]
    months = sorted({month for month in record_months if month is not None})
    months = sorted({*months, *fixed_overrides.keys()})
    if include_month and include_month not in months:
        months.append(include_month)
        months.sort()

    header = ["Category", *months]
    rows = [header]
    income_categories = [category for category in ALL_CATEGORIES if _is_income_category(category)]
    expense_categories = [category for category in ALL_CATEGORIES if not _is_income_category(category)]
    categories = [*income_categories, *expense_categories]
    category_months: dict[str, dict[str, Decimal]] = {category: {} for category in categories}

    for record in records:
        record_month = _month_from_record_date(record)
        if record.status.lower() != "confirmed" or record.category not in category_months or record_month not in months:
            continue
        if record.transaction_type.lower() == "fixed" or record.input_type.lower() == "fixed":
            category_months[record.category][record_month] = record.amount
            continue
        category_total = category_months[record.category].get(record_month, Decimal("0")) + record.amount
        category_months[record.category][record_month] = category_total

    for month, month_values in fixed_overrides.items():
        if month not in months:
            continue
        for category, amount in month_values.items():
            if category not in category_months:
                category_months[category] = {}
                categories.append(category)
            category_months[category][month] = amount

    for category in income_categories:
        rows.append([category, *[_format_optional_amount(category_months[category].get(month)) for month in months]])
    income_totals = _month_totals(category_months, income_categories, months)
    rows.append(["Total Income", *[f"{income_totals[month]:.2f}" for month in months]])
    rows.append(["", *([""] * len(months))])

    for category in expense_categories:
        rows.append([category, *[_format_optional_amount(category_months[category].get(month)) for month in months]])
    expense_totals = _month_totals(category_months, expense_categories, months)
    rows.append(["Total Expenses", *[f"{expense_totals[month]:.2f}" for month in months]])
    net_by_month = {
        month: income_totals[month] - expense_totals[month]
        for month in months
    }
    rows.append(["Net P&L", *[f"{net_by_month[month]:.2f}" for month in months]])
    running_total = Decimal("0")
    cumulative_values: list[str] = []
    for month in months:
        running_total += net_by_month[month]
        cumulative_values.append(f"{running_total:.2f}")
    rows.append(["Cumulative P&L", *cumulative_values])
    return rows


def format_spending_summary(summary: SpendingSummary) -> str:
    start_text = summary.period.start.strftime("%-d %B")
    end_text = summary.period.end.strftime("%-d %B %Y")
    if not summary.categories:
        return f"{summary.period.label} summary ({start_text} to {end_text}):\n\nNo confirmed expenses yet."

    income_items = [item for item in summary.categories if _is_income_category(item.category)]
    expense_items = [item for item in summary.categories if not _is_income_category(item.category)]
    lines = [f"{summary.period.label} summary ({start_text} to {end_text}):"]
    if income_items:
        lines.append("Income:")
        lines.extend(f"{item.category}: ${item.total:.2f}" for item in income_items)
        lines.append(f"Total Income: ${summary.total_income:.2f}")
    if expense_items:
        lines.append("Expenses:")
        lines.extend(f"{item.category}: ${item.total:.2f}" for item in expense_items)
        lines.append(f"Total Expenses: ${summary.total_expenses:.2f}")
    lines.append(f"Net P&L: ${summary.net:.2f}")
    return "\n\n".join(lines)


def _month_totals(
    category_months: dict[str, dict[str, Decimal]],
    categories: list[str],
    months: list[str],
) -> dict[str, Decimal]:
    return {
        month: sum((category_months[category].get(month, Decimal("0")) for category in categories), Decimal("0"))
        for month in months
    }


def _is_income_category(category: str) -> bool:
    return category.lower().startswith("income -")


def _format_optional_amount(value: Decimal | None) -> str:
    if value is None or value == 0:
        return ""
    return f"{value:.2f}"


def _parse_history_date(text: str, today: date) -> date | None:
    normalized = text.strip().casefold()
    if normalized == "today":
        return today
    if normalized == "yesterday":
        return today - timedelta(days=1)
    iso_match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", normalized)
    if iso_match is not None:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)(?:\s+(\d{4}))?", normalized)
    if match is None:
        return None
    month = _month_number(match.group(2))
    if month is None:
        return None
    try:
        return date(int(match.group(3) or today.year), month, int(match.group(1)))
    except ValueError:
        return None


def _month_number(value: str) -> int | None:
    months = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
        "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    return months.get(value.casefold())


def _month_from_record_date(record: ExpenseRecord) -> str | None:
    try:
        return date.fromisoformat(record.expense_date).strftime("%Y-%m")
    except ValueError:
        return None
