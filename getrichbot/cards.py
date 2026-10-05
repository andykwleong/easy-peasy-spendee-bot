from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from getrichbot.models import CardUsageRecord, ExpenseRecord


@dataclass(frozen=True)
class PaymentMethod:
    name: str
    owner: str
    payment_type: str
    cycle_type: str
    cycle_start_day: int
    last4: str = ""

    @property
    def is_credit_card(self) -> bool:
        return self.payment_type.strip().lower() in {"credit card", "credit", "card"}


@dataclass(frozen=True)
class CardLimit:
    payment_method: str
    owner: str
    category: str
    amount: Decimal
    payment_channel: str = "All"


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

    def channel_options_for(self, owner: str, payment_method: str) -> tuple[str, ...]:
        channels: list[str] = []
        for limit in self.limits_for(owner, payment_method):
            if limit.payment_channel.casefold() == "all":
                continue
            if not any(channel.casefold() == limit.payment_channel.casefold() for channel in channels):
                channels.append(limit.payment_channel)
        return tuple(channels)

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


def current_card_period(method: PaymentMethod, today: date, cycle_offset: int = 0) -> tuple[date, date]:
    if method.cycle_type.casefold() == "calendar":
        start = today.replace(day=1)
        end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return _shift_card_period(method, start, end, cycle_offset)

    start_this_month = _date_in_month(today.year, today.month, method.cycle_start_day)
    if today >= start_this_month:
        start = start_this_month
    else:
        previous_month = today.replace(day=1) - date.resolution
        start = _date_in_month(previous_month.year, previous_month.month, method.cycle_start_day)
    next_month = (start.replace(day=1) + date.resolution * 32).replace(day=1)
    next_start = _date_in_month(next_month.year, next_month.month, method.cycle_start_day)
    end = next_start - date.resolution
    return _shift_card_period(method, start, end, cycle_offset)


def build_card_summary(
    config: PaymentConfig,
    records: list[ExpenseRecord],
    owner: str,
    today: date,
    card_usage_records: list[CardUsageRecord] | None = None,
    cycle_offset: int = 0,
) -> list[CardSummaryItem]:
    card_usage_records = card_usage_records or []
    items: list[CardSummaryItem] = []
    for method in config.methods_for_owner(owner):
        if not method.is_credit_card:
            continue
        period_start, period_end = current_card_period(method, today, cycle_offset=cycle_offset)
        card_records = [
            record
            for record in records
            if _is_matching_card_expense(record, owner, method.name, period_start, period_end)
        ]
        usage_records = [
            record
            for record in card_usage_records
            if _is_matching_card_usage(record, owner, method.name, period_start, period_end)
        ]
        limits = tuple(
            CardLimitUsage(
                limit=limit,
                spent=sum(
                    (
                        record.amount
                        for record in card_records
                        if _matches_limit_category(record, limit.category)
                        and _matches_limit_channel(record.payment_channel, limit.payment_channel)
                    ),
                    Decimal("0"),
                )
                + sum(
                    (
                        record.amount
                        for record in usage_records
                        if limit.category.casefold() == "all"
                        and _matches_limit_channel(record.payment_channel, limit.payment_channel)
                    ),
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
                total_spend=sum((record.amount for record in card_records), Decimal("0"))
                + sum((record.amount for record in usage_records), Decimal("0")),
                limits=limits,
            )
        )
    return items


def format_card_summary(items: list[CardSummaryItem], title: str = "Card summary") -> str:
    if not items:
        return "No active credit cards are configured for you."

    capped = [item for item in items if item.limits]
    uncapped = [item for item in items if not item.limits]
    lines = [title]
    if capped:
        lines.extend(["", "Capped:", ""])
        for item in capped:
            if len(item.limits) == 1:
                lines.append(f"{item.payment_method.name} - {_format_limit_usage(item.limits[0], include_category=False)}")
            else:
                lines.append(item.payment_method.name)
                lines.extend(_format_limit_usage(usage, include_category=True) for usage in item.limits)
            lines.append("")
    if uncapped:
        if lines[-1] != "":
            lines.append("")
        lines.extend(["Uncapped:", ""])
        for item in uncapped:
            lines.append(f"{item.payment_method.name} - ${item.total_spend:,.2f}")
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
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
    last4_index = headers.get("last 4", -1)
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
        last4 = _last4_digits(_value(row, last4_index)) if last4_index >= 0 else ""
        methods.append(PaymentMethod(name, owner, payment_type, cycle_type, start_day, last4))
    return methods


def _parse_card_limits(rows: list[list[str]]) -> list[CardLimit]:
    headers, data_rows = _headers_and_rows(rows)
    indexes = _required_indexes(
        headers,
        {
            "payment_method": ("payment method",),
            "owner": ("owner",),
            "category": ("category", "applies to categories"),
            "payment_channel": ("payment channel", "channel"),
            "amount": ("limit amount",),
            "active": ("active",),
        },
        "Card Limits",
        required=("payment_method", "owner", "category", "amount", "active"),
    )
    limits: list[CardLimit] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in data_rows:
        if not _is_active(_value(row, indexes["active"])):
            continue
        payment_method = _value(row, indexes["payment_method"])
        owner = _value(row, indexes["owner"])
        category = _value(row, indexes["category"])
        payment_channel = _value(row, indexes.get("payment_channel", -1)) or "All"
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
        key = (owner.casefold(), payment_method.casefold(), category.casefold(), payment_channel.casefold())
        if key in seen:
            raise ValueError(f"Card Limits has a duplicate active row for {payment_method} / {owner} / {category} / {payment_channel}.")
        seen.add(key)
        limits.append(CardLimit(payment_method, owner, category, amount, payment_channel))
    return limits


def _headers_and_rows(rows: list[list[str]]) -> tuple[dict[str, int], list[list[str]]]:
    if not rows:
        return {}, []
    return ({_normalize_header(value): index for index, value in enumerate(rows[0])}, rows[1:])


def _required_indexes(
    headers: dict[str, int],
    wanted: dict[str, tuple[str, ...]],
    sheet_name: str,
    required: tuple[str, ...] | None = None,
) -> dict[str, int]:
    indexes: dict[str, int] = {}
    missing: list[str] = []
    required_keys = set(required or wanted.keys())
    for key, aliases in wanted.items():
        index = next((headers[alias] for alias in aliases if alias in headers), None)
        if index is None:
            if key in required_keys:
                missing.append(aliases[0].title())
        else:
            indexes[key] = index
    if missing:
        raise ValueError(f"{sheet_name} is missing these header(s): {', '.join(missing)}.")
    return indexes


def _normalize_header(value: str) -> str:
    return " ".join(str(value).strip().casefold().split())


def _last4_digits(value: str) -> str:
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) < 4:
        return ""
    return digits[-4:]


def _value(row: list[str], index: int) -> str:
    if index < 0:
        return ""
    return str(row[index]).strip() if index < len(row) else ""


def _is_active(value: str) -> bool:
    return value.strip().casefold() in {"true", "yes", "y", "1"}


def _date_in_month(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift_card_period(method: PaymentMethod, start: date, end: date, cycle_offset: int) -> tuple[date, date]:
    if cycle_offset == 0:
        return start, end
    if cycle_offset > 0:
        shifted_start = start
        shifted_end = end
        for _ in range(cycle_offset):
            next_reference = shifted_end + date.resolution
            shifted_start, shifted_end = current_card_period(method, next_reference)
        return shifted_start, shifted_end

    shifted_start = start
    shifted_end = end
    for _ in range(abs(cycle_offset)):
        previous_reference = shifted_start - date.resolution
        shifted_start, shifted_end = current_card_period(method, previous_reference)
    return shifted_start, shifted_end


def _is_matching_card_expense(
    record: ExpenseRecord,
    owner: str,
    payment_method: str,
    period_start: date,
    period_end: date,
) -> bool:
    if record.status.casefold() != "confirmed" or record.transaction_type.casefold() != "expense":
        return False
    payment_owner = record.payment_owner or record.logged_by
    if payment_owner != owner or record.payment_method.casefold() != payment_method.casefold():
        return False
    try:
        record_date = date.fromisoformat(record.expense_date)
    except ValueError:
        return False
    return period_start <= record_date <= period_end


def _is_matching_card_usage(
    record: CardUsageRecord,
    owner: str,
    payment_method: str,
    period_start: date,
    period_end: date,
) -> bool:
    if record.status.casefold() != "confirmed":
        return False
    payment_owner = record.payment_owner or record.logged_by
    if payment_owner != owner or record.payment_method.casefold() != payment_method.casefold():
        return False
    try:
        record_date = date.fromisoformat(record.usage_date)
    except ValueError:
        return False
    return period_start <= record_date <= period_end


def _matches_limit_category(record: ExpenseRecord, category: str) -> bool:
    return category.casefold() == "all" or record.category.casefold() == category.casefold()


def _matches_limit_channel(record_channel: str, limit_channel: str) -> bool:
    return limit_channel.casefold() == "all" or record_channel.casefold() == limit_channel.casefold()


def _format_limit_usage(usage: CardLimitUsage, include_category: bool) -> str:
    usage_text = f"${usage.spent:,.2f}/${usage.limit.amount:,.2f} ({_limit_marker(usage.percent)} {usage.percent:.0f}%)"
    if not include_category:
        return usage_text
    label_parts: list[str] = []
    if usage.limit.category.casefold() != "all":
        label_parts.append(usage.limit.category)
    if usage.limit.payment_channel.casefold() != "all":
        label_parts.append(usage.limit.payment_channel)
    label = " / ".join(label_parts) if label_parts else "All spending"
    return f"{label} - {usage_text}"


def _limit_marker(percent: Decimal) -> str:
    if percent < Decimal("60"):
        return "🟢"
    if percent < Decimal("80"):
        return "🟡"
    if percent < Decimal("95"):
        return "🟠"
    return "🔴"
