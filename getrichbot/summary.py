from __future__ import annotations

import re
import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from getrichbot.categories import ALL_CATEGORIES, CATEGORY_ALIASES, SHOPPING_CATEGORIES
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


@dataclass(frozen=True)
class CategoryBreakdownRequest:
    categories: tuple[str, ...]
    display_category: str
    period: SummaryPeriod


@dataclass(frozen=True)
class MonthlyCategoryBreakdown:
    month_start: date
    records: list[ExpenseRecord]
    total: Decimal


@dataclass(frozen=True)
class CategoryBreakdown:
    request: CategoryBreakdownRequest
    months: list[MonthlyCategoryBreakdown]
    grand_total: Decimal


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


def parse_category_breakdown_request(
    text: str,
    today: date,
    logged_by: str,
    me_label: str,
    wife_label: str,
) -> CategoryBreakdownRequest | None:
    lowered = " ".join(text.lower().strip().split())
    if not looks_like_category_breakdown_request(lowered):
        return None

    category_text, period_text = _split_category_breakdown_request(lowered)
    if category_text is None or period_text is None:
        return None

    categories, display_category = _category_breakdown_categories(category_text, logged_by, me_label, wife_label)
    if not categories:
        return None
    period = _parse_month_period(period_text, today)
    if period is None:
        return None
    return CategoryBreakdownRequest(categories=tuple(categories), display_category=display_category, period=period)


def looks_like_category_breakdown_request(text: str) -> bool:
    lowered = " ".join(text.lower().strip().split())
    if lowered.startswith(("change ", "update ", "edit ", "delete ", "remove ", "confirm ", "cancel ")):
        return False
    if any(phrase in lowered for phrase in ("category spending", "category breakdown", "breakdown")):
        return True
    if any(month in lowered.split() for month in _MONTH_NAMES):
        month_range_pattern = rf"\b({_MONTH_PATTERN})\b\s*(?:-|to|till|until|through)\s*\b({_MONTH_PATTERN})\b"
        if re.search(month_range_pattern, lowered) and not looks_like_expense_history_request(lowered):
            return True
        return bool(
            re.search(r"\b(for|in|from|between|to|till|until|through)\b", lowered)
            and not looks_like_expense_history_request(lowered)
        )
    return False


def category_breakdown_clarification() -> str:
    return (
        "I think you are asking for category spending, but I could not understand the category or month.\n\n"
        "Try:\n"
        "food for june\n"
        "groceries from may to july\n"
        "all shopping for july"
    )


def build_category_breakdown(records: list[ExpenseRecord], request: CategoryBreakdownRequest) -> CategoryBreakdown:
    months = _month_starts(request.period.start, request.period.end)
    monthly: list[MonthlyCategoryBreakdown] = []
    category_set = {category.casefold() for category in request.categories}
    for month_start in months:
        month_end = _month_end(month_start.year, month_start.month)
        matching: list[ExpenseRecord] = []
        for record in records:
            if record.status.casefold() != "confirmed" or record.transaction_type.casefold() == "income":
                continue
            if record.category.casefold() not in category_set:
                continue
            try:
                record_date = date.fromisoformat(record.expense_date)
            except ValueError:
                continue
            if month_start <= record_date <= month_end and request.period.start <= record_date <= request.period.end:
                matching.append(record)
        matching.sort(key=lambda record: (record.expense_date, record.timestamp, record.entry_id))
        monthly.append(
            MonthlyCategoryBreakdown(
                month_start=month_start,
                records=matching,
                total=sum((record.amount for record in matching), Decimal("0")),
            )
        )
    return CategoryBreakdown(
        request=request,
        months=monthly,
        grand_total=sum((month.total for month in monthly), Decimal("0")),
    )


def format_category_breakdown(breakdown: CategoryBreakdown) -> str:
    lines = [f"{breakdown.request.display_category} breakdown - {breakdown.request.period.label}"]
    multi_month = len(breakdown.months) > 1
    for month in breakdown.months:
        if multi_month:
            lines.append(month.month_start.strftime("%B %Y"))
        if not month.records:
            lines.append("No confirmed expenses.")
        for record in month.records:
            try:
                record_date = date.fromisoformat(record.expense_date)
                date_text = record_date.strftime("%-d %b")
            except ValueError:
                date_text = record.expense_date
            lines.append(
                f"{date_text} - ${record.amount:,.2f} - {record.logged_by} - {record.description} [{record.entry_id}]"
            )
        lines.append(f"Total: ${month.total:,.2f}")
        lines.append("")
    if multi_month:
        lines.append(f"Grand total: ${breakdown.grand_total:,.2f}")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n\n".join(lines)


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


def _split_category_breakdown_request(text: str) -> tuple[str | None, str | None]:
    cleaned = re.sub(r"^(?:category\s+)?(?:spending|breakdown)\s+", "", text.strip())
    marker_match = re.search(r"\b(for|in|from|between)\b", cleaned)
    if marker_match is not None:
        category_text = cleaned[:marker_match.start()].strip()
        period_text = cleaned[marker_match.start():].strip()
        return category_text, period_text

    month_names = "|".join(_MONTH_NAMES)
    month_match = re.search(rf"\b({month_names})\b", cleaned)
    if month_match is None:
        return None, None
    category_text = cleaned[:month_match.start()].strip()
    period_text = cleaned[month_match.start():].strip()
    return category_text, period_text


def _category_breakdown_categories(
    category_text: str,
    logged_by: str,
    me_label: str,
    wife_label: str,
) -> tuple[list[str], str]:
    raw = category_text.strip()
    all_shopping = raw.casefold().startswith("all shopping")
    normalized = raw.casefold()
    if all_shopping:
        shopping_categories = [category for category in SHOPPING_CATEGORIES.values() if category in ALL_CATEGORIES]
        return shopping_categories, "Shopping"

    if normalized == "shopping":
        if logged_by == wife_label:
            category = SHOPPING_CATEGORIES.get("wife")
        else:
            category = SHOPPING_CATEGORIES.get("me")
        if category in ALL_CATEGORIES:
            return [category], category

    category = _normalize_category_for_breakdown(raw)
    if category in ALL_CATEGORIES:
        return [category], category
    return [], raw


def _normalize_category_for_breakdown(raw: str) -> str:
    lowered = raw.strip().casefold()
    if lowered in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[lowered]
    simplified = _simplify_category_text(lowered)
    for category in ALL_CATEGORIES:
        if _simplify_category_text(category) == simplified:
            return category
    for category in ALL_CATEGORIES:
        if _simplify_category_text(category).startswith(simplified):
            return category
    return raw


def _parse_month_period(text: str, today: date) -> SummaryPeriod | None:
    cleaned = re.sub(r"^(?:for|in|from|between)\s+", "", text.strip().casefold())
    range_match = re.fullmatch(
        rf"({_MONTH_PATTERN})(?:\s+(\d{{4}}))?\s*(?:-|to|and|till|until|through)\s*"
        rf"({_MONTH_PATTERN})(?:\s+(\d{{4}}))?",
        cleaned,
    )
    if range_match is not None:
        start_month = _month_number(range_match.group(1))
        end_month = _month_number(range_match.group(3))
        start_year = int(range_match.group(2) or today.year)
        end_year = int(range_match.group(4) or range_match.group(2) or today.year)
        if start_month is None or end_month is None:
            return None
        start = date(start_year, start_month, 1)
        end = _month_end(end_year, end_month)
        if end < start:
            return None
        return SummaryPeriod(start=start, end=end, label=f"{start.strftime('%B')} to {end.strftime('%B %Y')}")

    single_match = re.fullmatch(rf"({_MONTH_PATTERN})(?:\s+(\d{{4}}))?", cleaned)
    if single_match is None:
        return None
    month = _month_number(single_match.group(1))
    if month is None:
        return None
    year = int(single_match.group(2) or today.year)
    start = date(year, month, 1)
    return SummaryPeriod(start=start, end=_month_end(year, month), label=start.strftime("%B %Y"))


def _month_starts(start: date, end: date) -> list[date]:
    months: list[date] = []
    current = start.replace(day=1)
    last = end.replace(day=1)
    while current <= last:
        months.append(current)
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _simplify_category_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


_MONTH_NAMES = {
    "jan", "january", "feb", "february", "mar", "march", "apr", "april",
    "may", "jun", "june", "jul", "july", "aug", "august", "sep", "sept",
    "september", "oct", "october", "nov", "november", "dec", "december",
}
_MONTH_PATTERN = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))


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
