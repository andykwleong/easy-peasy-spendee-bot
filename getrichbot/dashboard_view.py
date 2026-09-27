from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from getrichbot.cards import CardLimit, PaymentConfig, build_card_summary
from getrichbot.categories import ALL_CATEGORIES
from getrichbot.models import CardUsageRecord, ExpenseRecord
from getrichbot.summary import SummaryPeriod, _is_income_category, build_spending_summary

RECENT_LIMIT = 40
RAW_LIMIT = 200


def limit_band(percent: Decimal) -> str:
    if percent < Decimal("60"):
        return "green"
    if percent < Decimal("80"):
        return "yellow"
    if percent < Decimal("95"):
        return "orange"
    return "red"


def build_dashboard_payload(
    *,
    viewer_label: str,
    scope: str,
    records: list[ExpenseRecord],
    card_usage: list[CardUsageRecord],
    payment_config: PaymentConfig | None,
    today: date,
    cards_error: str | None = None,
) -> dict:
    if scope not in {"mine", "both"}:
        raise ValueError("scope must be mine or both")

    months = [
        _month_snapshot(records, _previous_month_period(today)),
        _month_snapshot(records, _current_month_period(today)),
    ]
    raw_expenses = _raw_expenses(records)
    usage_rows = _raw_card_usage(card_usage)
    recent = _recent_items(records, card_usage, viewer_label, scope)
    return {
        "viewer_label": viewer_label,
        "scope": scope,
        "recent": recent[:RECENT_LIMIT],
        "recent_limit": RECENT_LIMIT,
        "recent_truncated": len(recent) > RECENT_LIMIT,
        "cards": _cards_snapshot(viewer_label, records, card_usage, payment_config, today, cards_error),
        "months": months,
        "raw_expenses": raw_expenses[:RAW_LIMIT],
        "raw_expenses_limit": RAW_LIMIT,
        "raw_expenses_truncated": len(raw_expenses) > RAW_LIMIT,
        "card_usage": usage_rows[:RAW_LIMIT],
        "card_usage_limit": RAW_LIMIT,
        "card_usage_truncated": len(usage_rows) > RAW_LIMIT,
        "agent_eval": {"status": "later", "scoring": False},
    }


def _current_month_period(today: date) -> SummaryPeriod:
    start = today.replace(day=1)
    return SummaryPeriod(start=start, end=today, label=start.strftime("%B %Y"))


def _previous_month_period(today: date) -> SummaryPeriod:
    end = today.replace(day=1) - timedelta(days=1)
    start = end.replace(day=1)
    return SummaryPeriod(start=start, end=end, label=start.strftime("%B %Y"))


def _month_snapshot(records: list[ExpenseRecord], period: SummaryPeriod) -> dict:
    summary = build_spending_summary(records, period)
    totals = {item.category: item.total for item in summary.categories if item.category in ALL_CATEGORIES}
    income = [
        {"category": category, "amount": _money(totals[category])}
        for category in ALL_CATEGORIES
        if _is_income_category(category) and totals.get(category, Decimal("0")) != 0
    ]
    expenses = [
        {"category": category, "amount": _money(totals[category])}
        for category in ALL_CATEGORIES
        if not _is_income_category(category) and totals.get(category, Decimal("0")) != 0
    ]
    total_income = sum((totals[item["category"]] for item in income), Decimal("0"))
    total_expenses = sum((totals[item["category"]] for item in expenses), Decimal("0"))
    return {
        "label": period.label,
        "start": period.start.isoformat(),
        "end": period.end.isoformat(),
        "income": income,
        "expenses": expenses,
        "total_income": _money(total_income),
        "total_expenses": _money(total_expenses),
        "net": _money(total_income - total_expenses),
    }


def _cards_snapshot(
    viewer_label: str,
    records: list[ExpenseRecord],
    card_usage: list[CardUsageRecord],
    payment_config: PaymentConfig | None,
    today: date,
    cards_error: str | None,
) -> dict:
    if payment_config is None:
        return {"owner": viewer_label, "capped": [], "uncapped": [], "error": cards_error}
    items = build_card_summary(payment_config, records, viewer_label, today, card_usage)
    capped = []
    uncapped = []
    for item in items:
        card = {
            "name": item.payment_method.name,
            "period_start": item.period_start.isoformat(),
            "period_end": item.period_end.isoformat(),
            "spent": _money(item.total_spend),
            "limits": [_limit_usage(usage.limit, usage.spent, usage.percent) for usage in item.limits],
        }
        if item.limits:
            capped.append(card)
        else:
            uncapped.append(card)
    return {"owner": viewer_label, "capped": capped, "uncapped": uncapped, "error": None}


def _limit_usage(limit: CardLimit, spent: Decimal, percent: Decimal) -> dict:
    return {
        "label": _limit_label(limit),
        "spent": _money(spent),
        "limit": _money(limit.amount),
        "percent": f"{percent:.4f}",
        "percent_label": f"{percent:.0f}",
        "band": limit_band(percent),
    }


def _limit_label(limit: CardLimit) -> str:
    parts: list[str] = []
    if limit.category.casefold() != "all":
        parts.append(limit.category)
    if limit.payment_channel.casefold() != "all":
        parts.append(limit.payment_channel)
    return " / ".join(parts) if parts else "All spending"


def _recent_items(
    records: list[ExpenseRecord],
    card_usage: list[CardUsageRecord],
    viewer_label: str,
    scope: str,
) -> list[dict]:
    items = [_recent_expense(record) for record in records if record.status.casefold() == "confirmed"]
    items.extend(_recent_card_usage(record) for record in card_usage if record.status.casefold() == "confirmed")
    if scope == "mine":
        items = [item for item in items if item["logged_by"] == viewer_label]
    items.sort(key=lambda item: (item["date"], item["time"], item["id"]), reverse=True)
    return items


def _recent_expense(record: ExpenseRecord) -> dict:
    kind = _expense_kind(record)
    payment_owner = ""
    if kind != "income" and record.payment_method:
        payment_owner = record.payment_owner or record.logged_by
    return {
        "id": record.entry_id,
        "date": record.expense_date,
        "time": record.timestamp,
        "description": record.description or record.raw_input,
        "amount": _money(record.amount),
        "category": "" if kind == "card_only" else record.category,
        "logged_by": record.logged_by,
        "payment_owner": payment_owner,
        "payment_method": "" if kind == "income" else record.payment_method,
        "payment_channel": "" if kind == "income" else record.payment_channel,
        "kind": kind,
    }


def _recent_card_usage(record: CardUsageRecord) -> dict:
    return {
        "id": record.entry_id,
        "date": record.usage_date,
        "time": record.timestamp,
        "description": record.description or record.raw_input,
        "amount": _money(record.amount),
        "category": "",
        "logged_by": record.logged_by,
        "payment_owner": record.payment_owner or record.logged_by,
        "payment_method": record.payment_method,
        "payment_channel": record.payment_channel,
        "kind": "card_only",
    }


def _expense_kind(record: ExpenseRecord) -> str:
    kind = record.transaction_type.casefold()
    if kind == "income":
        return "income"
    if kind == "fixed":
        return "fixed"
    return "expense"


def _raw_expenses(records: list[ExpenseRecord]) -> list[dict]:
    rows = [
        {
            "id": record.entry_id,
            "date": record.expense_date,
            "time": record.timestamp,
            "logged_by": record.logged_by,
            "amount": _money(record.amount),
            "category": record.category,
            "description": record.description,
            "payment_owner": record.payment_owner,
            "payment_method": record.payment_method,
            "payment_channel": record.payment_channel,
            "type": record.transaction_type or "Expense",
            "status": record.status,
        }
        for record in records
    ]
    rows.sort(key=lambda row: (row["date"], row["time"], row["id"]), reverse=True)
    return rows


def _raw_card_usage(records: list[CardUsageRecord]) -> list[dict]:
    rows = [
        {
            "id": record.entry_id,
            "date": record.usage_date,
            "time": record.timestamp,
            "logged_by": record.logged_by,
            "amount": _money(record.amount),
            "payment_owner": record.payment_owner,
            "payment_method": record.payment_method,
            "payment_channel": record.payment_channel,
            "description": record.description,
            "type": "Card only",
            "status": record.status,
        }
        for record in records
    ]
    rows.sort(key=lambda row: (row["date"], row["time"], row["id"]), reverse=True)
    return rows


def _money(value: Decimal) -> str:
    return f"{value:.2f}"
