"""Immediate corrections for a saved row.

Category, amount, card, and channel update the Google Sheet as soon as the
change is finished. Delete is not handled here. Nothing in this module guesses
a payment channel, and it does not offer a field the row cannot use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from getrichbot.cards import PaymentConfig, PaymentMethod
from getrichbot.categories import CATEGORY_ALIASES, FIXED_CATEGORIES, VARIABLE_CATEGORIES
from getrichbot.models import CardUsageRecord, ExpenseRecord

_EXPLICIT_EDIT = re.compile(
    r"(?:change|changed|update|set|make)"
    r"(?:\s+(?:the|its|this|that))?"
    r"\s+(?P<field>payment channel|payment method|amount|channel|card|category)"
    r"\s+(?:to|as)\s+(?P<value>.+)",
    re.IGNORECASE,
)
_LOOSE_EDIT = re.compile(
    r"(?:change|changed|update|set|make)"
    r"(?:\s+(?:it|this|that|category|the category|expense category|spend category))?"
    r"\s+(?:to|as)\s+(?P<value>.+)",
    re.IGNORECASE,
)
_FINISHED_AMOUNT = re.compile(r"\d+(?:\.\d{1,2})?")
_MONEY_TEXT = re.compile(r"\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?")


@dataclass(frozen=True)
class ParsedEdit:
    field: str
    value: str


@dataclass(frozen=True)
class PlannedEdit:
    message: str
    error: str = ""
    amount: Decimal | None = None
    category: str | None = None
    transaction_type: str | None = None
    payment_method: str | None = None
    payment_owner: str | None = None
    payment_channel: str | None = None
    touch_payment: bool = False

    @property
    def ok(self) -> bool:
        return self.error == ""


def parse_immediate_edit(text: str) -> ParsedEdit | None:
    """Read a finished category, amount, card, or channel correction.

    Date changes, deletes, and pending-list edits such as ``change 2 to Food``
    are left alone.
    """
    cleaned = " ".join(text.strip().split()).strip(" .")
    if not cleaned or re.search(r"\bdate\b", cleaned, re.IGNORECASE):
        return None

    explicit = _EXPLICIT_EDIT.fullmatch(cleaned)
    if explicit is not None:
        field_name = explicit.group("field").casefold()
        value = explicit.group("value").strip(" .")
        if not value:
            return None
        if field_name == "amount":
            return ParsedEdit("amount", value)
        if field_name in {"card", "payment method"}:
            return ParsedEdit("card", value)
        if field_name in {"channel", "payment channel"}:
            return ParsedEdit("channel", value)
        return ParsedEdit("category", value)

    loose = _LOOSE_EDIT.fullmatch(cleaned)
    if loose is None:
        return None
    value = loose.group("value").strip(" .")
    if not value:
        return None
    if _MONEY_TEXT.fullmatch(value):
        return ParsedEdit("amount", value)
    if re.search(r"\b(?:amount|price|cost)\b", cleaned, re.IGNORECASE):
        return None
    return ParsedEdit("category", value)


def parse_finished_amount(raw: str) -> Decimal | None:
    cleaned = raw.strip().replace("S$", "").replace("$", "").replace(",", "").strip()
    if not _FINISHED_AMOUNT.fullmatch(cleaned):
        return None
    value = Decimal(cleaned)
    if value <= 0:
        return None
    return value.quantize(Decimal("0.01"))


def expense_kind(transaction_type: str, input_type: str = "") -> str:
    if transaction_type.casefold() == "income" or input_type.casefold() == "income":
        return "income"
    if transaction_type.casefold() == "fixed" or input_type.casefold() == "fixed":
        return "fixed"
    return "expense"


def allowed_fields(kind: str) -> frozenset[str]:
    if kind == "card_only":
        return frozenset({"amount", "card", "channel"})
    if kind in {"income", "fixed"}:
        return frozenset({"category", "amount"})
    return frozenset({"category", "amount", "card", "channel"})


def categories_for(kind: str) -> tuple[str, ...]:
    if kind == "income":
        return tuple(category for category in VARIABLE_CATEGORIES if category.casefold().startswith("income -"))
    if kind == "fixed":
        return tuple(FIXED_CATEGORIES)
    if kind == "expense":
        return tuple(category for category in VARIABLE_CATEGORIES if not category.casefold().startswith("income -"))
    return ()


def category_for_linked_message(
    text: str,
    categories: tuple[str, ...],
    *,
    reply_message_id: str | int | None,
    last_bot_message_id: str | int | None,
) -> tuple[str, str] | None:
    """A bare category attaches to the replied message, or the previous bot message."""
    category = match_category(text, categories)
    if category is None:
        return None
    if reply_message_id not in (None, ""):
        return category, str(reply_message_id)
    if last_bot_message_id not in (None, ""):
        return category, str(last_bot_message_id)
    return None


def record_for_message_id(records: list[ExpenseRecord], message_id: str | int | None) -> ExpenseRecord | None:
    if message_id in (None, ""):
        return None
    wanted = str(message_id)
    found = [
        record
        for record in records
        if str(getattr(record, "telegram_message_id", "") or "") == wanted
    ]
    if not found:
        return None
    return found[-1]


def match_category(raw: str, choices: tuple[str, ...]) -> str | None:
    lowered = " ".join(raw.strip().split()).casefold()
    if not lowered:
        return None
    alias = CATEGORY_ALIASES.get(lowered)
    if alias is not None and alias in choices:
        return alias
    exact = [category for category in choices if category.casefold() == lowered]
    if len(exact) == 1:
        return exact[0]
    prefix = [category for category in choices if category.casefold().startswith(lowered)]
    if len(prefix) == 1:
        return prefix[0]
    return None


def edit_choices(payment_config: PaymentConfig | None) -> dict:
    cards: list[dict] = []
    if payment_config is not None:
        for method in payment_config.payment_methods:
            cards.append(
                {
                    "name": method.name,
                    "owner": method.owner,
                    "channels": list(payment_config.channel_options_for(method.owner, method.name)),
                }
            )
    return {
        "expense_categories": list(categories_for("expense")),
        "income_categories": list(categories_for("income")),
        "fixed_categories": list(categories_for("fixed")),
        "cards": cards,
    }


def latest_logged_row(
    expenses: list[ExpenseRecord],
    card_usage: list[CardUsageRecord],
    logged_by: str,
) -> tuple[str, ExpenseRecord | CardUsageRecord] | None:
    expense = next((record for record in reversed(expenses) if record.logged_by == logged_by), None)
    usage = next((record for record in reversed(card_usage) if record.logged_by == logged_by), None)
    if expense is None and usage is None:
        return None
    if usage is None and expense is not None:
        return "expense", expense
    if expense is None and usage is not None:
        return "card_usage", usage
    assert expense is not None and usage is not None
    if (usage.usage_date, usage.timestamp) > (expense.expense_date, expense.timestamp):
        return "card_usage", usage
    return "expense", expense


def find_payment_method(
    config: PaymentConfig,
    raw_name: str,
    owner: str | None = None,
) -> tuple[str, PaymentMethod | None]:
    name = " ".join(raw_name.strip().split())
    if not name:
        return "missing", None
    if owner:
        matches = [
            method
            for method in config.payment_methods
            if method.owner.casefold() == owner.strip().casefold() and method.name.casefold() == name.casefold()
        ]
        if len(matches) == 1:
            return "found", matches[0]
        return "missing", None

    matches = [method for method in config.payment_methods if method.name.casefold() == name.casefold()]
    if len(matches) == 1:
        return "found", matches[0]
    if len(matches) > 1:
        return "ambiguous", None

    prefixed: list[PaymentMethod] = []
    for method in config.payment_methods:
        for prefix in (f"{method.owner} ", f"{method.owner}'s ", f"{method.owner}’s "):
            remainder = name[len(prefix) :] if name.casefold().startswith(prefix.casefold()) else ""
            if remainder and remainder.casefold() == method.name.casefold():
                prefixed.append(method)
                break
    if len(prefixed) == 1:
        return "found", prefixed[0]
    if len(prefixed) > 1:
        return "ambiguous", None
    return "missing", None


def channel_after_card_change(config: PaymentConfig, method: PaymentMethod, current_channel: str) -> str:
    """Use the only real channel. Never invent Online or PayWave."""
    options = config.channel_options_for(method.owner, method.name)
    if len(options) == 1:
        return options[0]
    if len(options) == 0:
        return ""
    for option in options:
        if option.casefold() == current_channel.strip().casefold():
            return option
    return ""


def plan_edit(
    *,
    kind: str,
    field: str,
    value: str,
    owner: str | None = None,
    current_method: str = "",
    current_owner: str = "",
    current_channel: str = "",
    payment_config: PaymentConfig | None = None,
) -> PlannedEdit:
    if field not in {"category", "amount", "card", "channel"}:
        return PlannedEdit("That change is not available.", error="invalid")
    if field not in allowed_fields(kind):
        return PlannedEdit(_rejected_field_message(kind, field), error="not_applicable")

    if field == "amount":
        amount = parse_finished_amount(value)
        if amount is None:
            return PlannedEdit("I could not read that amount. Use a number such as 23.20.", error="invalid")
        return PlannedEdit("Saved.", amount=amount)

    if field == "category":
        choices = categories_for(kind)
        category = match_category(value, choices)
        if category is None:
            return PlannedEdit("I do not recognize this category. Send: categories", error="invalid")
        transaction_type = "Fixed" if kind == "fixed" else ("Income" if kind == "income" else "Expense")
        return PlannedEdit("Saved.", category=category, transaction_type=transaction_type)

    if payment_config is None:
        return PlannedEdit("I could not read the card list just now. Nothing was changed.", error="sheet")

    if field == "card":
        status, method = find_payment_method(payment_config, value, owner)
        if status == "ambiguous":
            return PlannedEdit(
                "That card name matches more than one person, so I did not guess.",
                error="ambiguous",
            )
        if status != "found" or method is None:
            return PlannedEdit(
                "I do not know that card. Check Payment Methods, or send /refreshpayments.",
                error="invalid",
            )
        channel = channel_after_card_change(payment_config, method, current_channel)
        return PlannedEdit(
            "Saved.",
            payment_method=method.name,
            payment_owner=method.owner,
            payment_channel=channel,
            touch_payment=True,
        )

    method_status, method = find_payment_method(payment_config, current_method, current_owner or None)
    if method_status != "found" or method is None:
        named = find_payment_method(payment_config, current_method, None)
        if current_owner and named[0] == "found":
            method = named[1]
        else:
            method = None
    if method is None:
        return PlannedEdit("This row has no card, so there is no channel to change.", error="not_applicable")
    options = payment_config.channel_options_for(method.owner, method.name)
    if not options:
        return PlannedEdit("This card has no channel to choose.", error="not_applicable")
    requested = value.strip()
    match = next((option for option in options if option.casefold() == requested.casefold()), None)
    if match is None:
        return PlannedEdit("I do not know that channel for this card.", error="invalid")
    return PlannedEdit(
        "Saved.",
        payment_method=method.name,
        payment_owner=method.owner,
        payment_channel=match,
        touch_payment=True,
    )


def format_saved_row(
    *,
    amount: Decimal,
    category: str,
    expense_date: str,
    entry_id: str,
    kind: str,
    payment_method: str = "",
    payment_owner: str = "",
    payment_channel: str = "",
    showed_payment: bool = False,
) -> str:
    date_text = _human_date(expense_date)
    if kind == "card_only":
        detail = payment_method or "card"
        extras = [part for part in (payment_owner, payment_channel) if part and part.casefold() != "all"]
        if extras:
            detail = f"{detail} ({', '.join(extras)})"
        return f"Updated ${amount:.2f} on {detail} - {date_text} [{entry_id}]"
    if showed_payment and payment_method:
        extras = [part for part in (payment_owner, payment_channel) if part and part.casefold() != "all"]
        via = f"{payment_method} ({', '.join(extras)})" if extras else payment_method
        return f"Updated ${amount:.2f} to {category} via {via} - {date_text} [{entry_id}]"
    return f"Updated ${amount:.2f} to {category} - {date_text} [{entry_id}]"


def _rejected_field_message(kind: str, field: str) -> str:
    if kind == "income" and field in {"card", "channel"}:
        return "Income has no card."
    if kind == "fixed" and field in {"card", "channel"}:
        return "Fixed expenses have no card."
    if kind == "card_only" and field == "category":
        return "Card-only spend has no expense category."
    return "That change does not apply to this row."


def _human_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%-d %B %Y")
    except ValueError:
        return value
