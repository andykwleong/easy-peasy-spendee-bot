from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from getrichbot.models import ExpenseRecord


@dataclass(frozen=True)
class PaymentMethod:
    name: str
    owner: str
    payment_type: str
    cycle_type: str
    cycle_start_day: int

    @property
    def is_credit_card(self) -> bool:
        return self.payment_type.strip().lower() in {"credit card", "credit", "card"}


@dataclass(frozen=True)
class CardLimit:
    payment_method: str
    owner: str
    category: str
    amount: Decimal


@dataclass(frozen=True)
class PaymentConfig:
    payment_methods: tuple[PaymentMethod, ...]
    card_limits: tuple[CardLimit, ...]

    def methods_for_owner(self, owner: str) -> tuple[PaymentMethod, ...]:
        return tuple(method for method in self.payment_methods if method.owner == owner)

    def limits_for(self, owner: str, payment_method: str) -> tuple[CardLimit, ...]:
        return tuple(
            limit
            for limit in self.card_limits
            if limit.owner == owner and limit.payment_method.casefold() == payment_method.casefold()
        )

    def method_for(self, owner: str, payment_method: str) -> PaymentMethod | None:
        for method in self.methods_for_owner(owner):
            if method.name.casefold() == payment_method.casefold():
                return method
        return None


@dataclass(frozen=True)
class CardLimitUsage:
    limit: CardLimit
    spent: Decimal

    @property
    def percent(self) -> Decimal:
        return (self.spent / self.limit.amount) * Decimal("100")


@dataclass(frozen=True)
class CardSummaryItem:
    payment_method: PaymentMethod
    period_start: date
    period_end: date
    total_spend: Decimal
    limits: tuple[CardLimitUsage, ...]


def parse_payment_config(method_rows: list[list[str]], limit_rows: list[list[str]]) -> PaymentConfig:
    methods = _parse_payment_methods(method_rows)
    limits = _parse_card_limits(limit_rows)
    if not methods:
        raise ValueError("No active payment methods found in the Payment Methods tab.")

    method_keys = {(method.owner.casefold(), method.name.casefold()) for method in methods}
    for limit in limits:
        key = (limit.owner.casefold(), limit.payment_method.casefold())
        if key not in method_keys:
            raise ValueError(
                f"Card Limits refers to '{limit.payment_method}' for '{limit.owner}', "
                "but that exact card and owner pair is not active in Payment Methods."
            )
        if limit.amount <= 0:
            raise ValueError(f"Card limit must be greater than zero for {limit.payment_method} / {limit.category}.")
    return PaymentConfig(tuple(methods), tuple(limits))


def current_card_period(method: PaymentMethod, today: date) -> tuple[date, date]:
    if method.cycle_type.casefold() == "calendar":
        return today.replace(day=1), today.replace(day=calendar.monthrange(today.year, today.month)[1])

    start_this_month = _date_in_month(today.year, today.month, method.cycle_start_day)
    if today >= start_this_month:
        start = start_this_month
    else:
        previous_month = today.replace(day=1) - date.resolution
        start = _date_in_month(previous_month.year, previous_month.month, method.cycle_start_day)
    next_month = (start.replace(day=1) + date.resolution * 32).replace(day=1)
    next_start = _date_in_month(next_month.year, next_month.month, method.cycle_start_day)
    return start, next_start - date.resolution


def build_card_summary(
    config: PaymentConfig,
    records: list[ExpenseRecord],
    owner: str,
    today: date,
) -> list[CardSummaryItem]:
    items: list[CardSummaryItem] = []
    for method in config.methods_for_owner(owner):
        if not method.is_credit_card:
            continue
        period_start, period_end = current_card_period(method, today)
        card_records = [
            record
            for record in records
            if _is_matching_card_expense(record, owner, method.name, period_start, period_end)
        ]
        limits = tuple(
            CardLimitUsage(
                limit=limit,
                spent=sum(
                    (record.amount for record in card_records if _matches_limit_category(record, limit.category)),
                    Decimal("0"),
                ),
            )
            for limit in config.limits_for(owner, method.name)
        )
        items.append(
            CardSummaryItem(
                payment_method=method,
                period_start=period_start,
                period_end=period_end,
                total_spend=sum((record.amount for record in card_records), Decimal("0")),
                limits=limits,
            )
        )
    return items


def format_card_summary(items: list[CardSummaryItem]) -> str:
    if not items:
        return "No active credit cards are configured for you."

    capped = [item for item in items if item.limits]
    uncapped = [item for item in items if not item.limits]
    lines = ["Card summary"]
    if capped:
        lines.extend(["", "Capped"])
        for item in capped:
            if len(item.limits) == 1:
                lines.append(f"{item.payment_method.name} - {_format_limit_usage(item.limits[0], include_category=False)}")
                continue
            lines.append(item.payment_method.name)
            lines.extend(_format_limit_usage(usage, include_category=True) for usage in item.limits)
    if uncapped:
        lines.extend(["", "Uncapped"])
        for item in uncapped:
            lines.append(f"{item.payment_method.name} - ${item.total_spend:,.2f}")
    return "\n".join(lines)


def _parse_payment_methods(rows: list[list[str]]) -> list[PaymentMethod]:
    headers, data_rows = _headers_and_rows(rows)
    indexes = _required_indexes(
        headers,
        {
            "payment_method": ("payment method",),
            "owner": ("owner",),
            "payment_type": ("type", "payment type"),
            "cycle_type": ("cycle type",),
            "cycle_start_day": ("cycle start day",),
            "active": ("active",),
        },
        "Payment Methods",
    )
    methods: list[PaymentMethod] = []
    seen: set[tuple[str, str]] = set()
    for row in data_rows:
        if not _is_active(_value(row, indexes["active"])):
            continue
        name = _value(row, indexes["payment_method"])
        owner = _value(row, indexes["owner"])
        payment_type = _value(row, indexes["payment_type"])
        cycle_type = _value(row, indexes["cycle_type"])
        start_day_raw = _value(row, indexes["cycle_start_day"])
        if not name or not owner or not payment_type or not cycle_type or not start_day_raw:
            raise ValueError("Each active Payment Methods row needs Payment Method, Owner, Type, Cycle Type, and Cycle Start Day.")
        normalized_cycle = cycle_type.casefold()
        if normalized_cycle not in {"calendar", "billing"}:
            raise ValueError(f"Cycle Type for {name} must be Calendar or Billing.")
        try:
            start_day = int(start_day_raw)
        except ValueError as exc:
            raise ValueError(f"Cycle Start Day for {name} must be a number from 1 to 31.") from exc
        if not 1 <= start_day <= 31:
            raise ValueError(f"Cycle Start Day for {name} must be a number from 1 to 31.")
        key = (owner.casefold(), name.casefold())
        if key in seen:
            raise ValueError(f"Payment Methods has a duplicate active row for {name} / {owner}.")
        seen.add(key)
        methods.append(PaymentMethod(name, owner, payment_type, cycle_type, start_day))
    return methods


def _parse_card_limits(rows: list[list[str]]) -> list[CardLimit]:
    headers, data_rows = _headers_and_rows(rows)
    indexes = _required_indexes(
        headers,
        {
            "payment_method": ("payment method",),
            "owner": ("owner",),
            "category": ("category", "applies to categories"),
            "amount": ("limit amount",),
            "active": ("active",),
        },
        "Card Limits",
    )
    limits: list[CardLimit] = []
    seen: set[tuple[str, str, str]] = set()
    for row in data_rows:
        if not _is_active(_value(row, indexes["active"])):
            continue
        payment_method = _value(row, indexes["payment_method"])
        owner = _value(row, indexes["owner"])
        category = _value(row, indexes["category"])
        amount_raw = _value(row, indexes["amount"])
        if not payment_method or not owner or not category:
            raise ValueError("Each active Card Limits row needs Payment Method, Owner, and Category.")
        # An active row without an amount documents an uncapped card. It does
        # not create a limit, but the card still appears via Payment Methods.
        if not amount_raw:
            continue
        try:
            amount = Decimal(amount_raw.replace(",", "").replace("S$", "").replace("$", ""))
        except Exception as exc:
            raise ValueError(f"Could not read limit amount for {payment_method} / {category}.") from exc
        key = (owner.casefold(), payment_method.casefold(), category.casefold())
        if key in seen:
            raise ValueError(f"Card Limits has a duplicate active row for {payment_method} / {owner} / {category}.")
        seen.add(key)
        limits.append(CardLimit(payment_method, owner, category, amount))
    return limits


def _headers_and_rows(rows: list[list[str]]) -> tuple[dict[str, int], list[list[str]]]:
    if not rows:
        return {}, []
    return ({_normalize_header(value): index for index, value in enumerate(rows[0])}, rows[1:])


def _required_indexes(headers: dict[str, int], wanted: dict[str, tuple[str, ...]], sheet_name: str) -> dict[str, int]:
    indexes: dict[str, int] = {}
    missing: list[str] = []
    for key, aliases in wanted.items():
        index = next((headers[alias] for alias in aliases if alias in headers), None)
        if index is None:
            missing.append(aliases[0].title())
        else:
            indexes[key] = index
    if missing:
        raise ValueError(f"{sheet_name} is missing these header(s): {', '.join(missing)}.")
    return indexes


def _normalize_header(value: str) -> str:
    return " ".join(str(value).strip().casefold().split())


def _value(row: list[str], index: int) -> str:
    return str(row[index]).strip() if index < len(row) else ""


def _is_active(value: str) -> bool:
    return value.strip().casefold() in {"true", "yes", "y", "1"}


def _date_in_month(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _is_matching_card_expense(
    record: ExpenseRecord,
    owner: str,
    payment_method: str,
    period_start: date,
    period_end: date,
) -> bool:
    if record.status.casefold() != "confirmed" or record.transaction_type.casefold() != "expense":
        return False
    if record.logged_by != owner or record.payment_method.casefold() != payment_method.casefold():
        return False
    try:
        record_date = date.fromisoformat(record.expense_date)
    except ValueError:
        return False
    return period_start <= record_date <= period_end


def _matches_limit_category(record: ExpenseRecord, category: str) -> bool:
    return category.casefold() == "all" or record.category.casefold() == category.casefold()


def _format_limit_usage(usage: CardLimitUsage, include_category: bool) -> str:
    usage_text = f"${usage.spent:,.2f}/${usage.limit.amount:,.2f} ({_limit_marker(usage.percent)} {usage.percent:.0f}%)"
    if not include_category:
        return usage_text
    label = "All spending" if usage.limit.category.casefold() == "all" else usage.limit.category
    return f"{label} - {usage_text}"


def _limit_marker(percent: Decimal) -> str:
    if percent < Decimal("60"):
        return "🟢"
    if percent < Decimal("80"):
        return "🟡"
    if percent < Decimal("95"):
        return "🟠"
    return "🔴"
