from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from decimal import Decimal
from decimal import InvalidOperation
from zoneinfo import ZoneInfo

from getrichbot.cards import PaymentMethod
from getrichbot.models import ExpenseRow
from getrichbot.parser import categorize_description

SINGAPORE_TZ = ZoneInfo("Asia/Singapore")
INCOME_MISC = "Income - misc"

PURCHASE_SUBJECT = "uob - transaction alert"
REFUND_SUBJECT = "your transaction has been refunded"
STATEMENT_SUBJECT = "your estatement/eadvice is ready for viewing"
PAYNOW_SUBJECT = "uob-paynow transfer received"

_AMOUNT_RE = re.compile(
    r"(?:SGD|S\$|\$)\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
_LAST4_RE = re.compile(
    r"(?:card\s+ending|ending|last\s*4(?:\s*digits)?)\D{0,16}(\d{4})(?!\d)",
    re.IGNORECASE,
)
_MASKED_LAST4_RE = re.compile(r"(?:[*xX]{2,}|•{2,})\s*(\d{4})(?!\d)")
_DATE_RE = re.compile(
    r"\bon\s+(\d{1,2}/\d{1,2}/\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})",
    re.IGNORECASE,
)
_SHOP_AFTER_DATE_RE = re.compile(
    r"\bon\s+\d{1,2}/\d{1,2}/\d{4}(?:\s+at\s+\d{1,2}:\d{2}(?:\s*[AP]M)?)?\s+at\s+(.+?)(?:\.|\n|$)",
    re.IGNORECASE,
)
_SHOP_AFTER_MONTH_RE = re.compile(
    r"\bon\s+\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}(?:\s+at\s+\d{1,2}:\d{2}(?:\s*[AP]M)?)?\s+at\s+(.+?)(?:\.|\n|$)",
    re.IGNORECASE,
)
_LABELED_SHOP_RE = re.compile(r"(?:merchant|shop)\s*:\s*(.+?)(?:\n|$)", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_ONLINE_RE = re.compile(
    r"food\s*panda|foodpanda|deliveroo|\bgrab\b|grabfood|booking\.com|\bagoda\b|\bexpedia\b|\bklook\b|"
    r"\bairbnb\b|hotels\.com|\btraveloka\b|trip\.com|travel\s+booking",
    re.IGNORECASE,
)
_RESTAURANT_RE = re.compile(
    r"\brestaurant\b|\bcafe\b|\bcafé\b|\bcoffee\b|\bbistro\b|\bkitchen\b|\bdiner\b|\bbakery\b|"
    r"\beatery\b|\bhawker\b|\bkopitiam\b",
    re.IGNORECASE,
)
_DIVIDEND_PHRASE_RE = re.compile(r"\bdividends?\b", re.IGNORECASE)
_INTEREST_PHRASE_RE = re.compile(
    r"\binterest\s+of\b|\binterest\s+credited\b|\bcredit\s+interest\b|\binterest\s+credit\b",
    re.IGNORECASE,
)
_SALARY_RE = re.compile(r"\bsalary\b", re.IGNORECASE)
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass(frozen=True)
class EmailDecision:
    action: str
    kind: str
    text: str = ""
    amount: Decimal | None = None
    shop: str = ""
    last4: str = ""
    expense_date: date | None = None
    category: str = ""
    logged_by: str = ""
    payment_method: str = ""
    payment_owner: str = ""
    payment_channel: str = ""
    channels: tuple[str, ...] = ()
    description: str = ""


def category_from_keywords(shop: str, logged_by: str, me_label: str, wife_label: str) -> str | None:
    category, confidence = categorize_description(shop, logged_by, me_label, wife_label)
    if category is None or confidence < 0.7:
        return None
    if category.lower().startswith("income"):
        return None
    return category


def decide_mail(
    subject: str,
    body: str,
    addresses: tuple[str, ...],
    *,
    forwarders: dict[str, str],
    methods: tuple[PaymentMethod, ...],
    categories: tuple[str, ...],
    channels_for,
    category_for,
    today: date,
) -> EmailDecision:
    kind = classify_kind(subject, body)
    if kind == "statement":
        return EmailDecision(action="ignore", kind=kind)
    if kind in {"refund", "paynow", "dividend", "interest"}:
        return _income_decision(kind, body, addresses, forwarders, categories, today)
    if kind == "salary":
        return EmailDecision(
            action="ask",
            kind=kind,
            text="This looks like salary. I have not logged it. Type income in the chat if you want it recorded.",
        )
    if kind == "purchase":
        return _purchase_decision(
            body,
            addresses,
            forwarders=forwarders,
            methods=methods,
            channels_for=channels_for,
            category_for=category_for,
        )
    return EmailDecision(
        action="ask",
        kind="unclear",
        text="I got mail that is not one clear purchase. I have not logged it.",
    )


def classify_kind(subject: str, body: str) -> str:
    normalized = _normalize_subject(subject)
    if normalized == STATEMENT_SUBJECT:
        return "statement"
    if normalized == PURCHASE_SUBJECT:
        return "purchase"
    if normalized == REFUND_SUBJECT:
        return "refund"
    if normalized == PAYNOW_SUBJECT:
        return "paynow"
    if _DIVIDEND_PHRASE_RE.search(normalized):
        return "dividend"
    if re.search(r"\binterest\b", normalized):
        return "interest"
    if _DIVIDEND_PHRASE_RE.search(body) or _INTEREST_PHRASE_RE.search(body):
        return "dividend" if _DIVIDEND_PHRASE_RE.search(body) else "interest"
    if _SALARY_RE.search(normalized) or _SALARY_RE.search(body):
        return "salary"
    return "other"


def match_cards_by_last4(last4: str, methods: tuple[PaymentMethod, ...]) -> tuple[PaymentMethod, ...]:
    if len(last4) != 4 or not last4.isdigit():
        return ()
    return tuple(method for method in methods if method.last4 == last4)


def shop_channel_hint(shop: str) -> str | None:
    if _ONLINE_RE.search(shop):
        return "Online"
    if _RESTAURANT_RE.search(shop):
        return "PayWave"
    return None


def choose_channel(shop: str, channels: tuple[str, ...]) -> str | None:
    if len(channels) == 0:
        return "All"
    if len(channels) == 1:
        return channels[0]
    hint = shop_channel_hint(shop)
    if hint is None:
        return None
    for channel in channels:
        if channel.casefold() == hint.casefold():
            return channel
    return None


def build_expense_row(decision: EmailDecision, *, chat_id: int | str, now: datetime) -> ExpenseRow:
    expense_date = decision.expense_date or now.date()
    timestamp = datetime.combine(
        expense_date,
        time(hour=now.hour, minute=now.minute, second=now.second),
        SINGAPORE_TZ,
    )
    income = decision.category == INCOME_MISC
    description = decision.description or decision.shop or decision.kind
    shop_label = decision.shop or description
    return ExpenseRow(
        entry_id=_entry_id(),
        timestamp=timestamp,
        logged_by=decision.logged_by,
        raw_input=f"email: {shop_label}"[:180],
        amount=decision.amount or Decimal("0"),
        category=decision.category,
        description=description,
        input_type="Email",
        status="Confirmed",
        telegram_chat_id=chat_id,
        telegram_message_id="email",
        transaction_type="Income" if income else "Expense",
        payment_method="" if income else decision.payment_method,
        payment_owner="" if income else decision.payment_owner,
        payment_channel="" if income else decision.payment_channel,
    )


def email_logged_text(row: ExpenseRow) -> str:
    when = row.timestamp.strftime("%-d %B %Y")
    if row.transaction_type.lower() == "income":
        detail = f" - {row.description}" if row.description else ""
        return f"Logged income ${row.amount:.2f} to {row.category} - {when}{detail} [{row.entry_id}]"
    channel = row.payment_channel or "All"
    return (
        f"Logged ${row.amount:.2f} at {row.description} to {row.category} - "
        f"{when} via {row.payment_method} ({channel}) [{row.entry_id}]"
    )


def addresses_in_text(value: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in _EMAIL_RE.findall(value or ""):
        lowered = match.casefold()
        if lowered not in found:
            found.append(lowered)
    return tuple(found)


def _purchase_decision(
    body: str,
    addresses: tuple[str, ...],
    *,
    forwarders: dict[str, str],
    methods: tuple[PaymentMethod, ...],
    channels_for,
    category_for,
) -> EmailDecision:
    amount = _one_amount(body)
    last4s = _last4s(body)
    shop = _shop(body)
    expense_date = _mail_date(body)
    if amount is None or shop is None or expense_date is None or len(last4s) != 1:
        return EmailDecision(
            action="ask",
            kind="purchase",
            text="I got a UOB transaction alert, but it was not one amount, one shop, one date, and one card. I have not logged it.",
        )
    last4 = last4s[0]
    matches = match_cards_by_last4(last4, methods)
    if len(matches) != 1:
        return EmailDecision(
            action="ask",
            kind="purchase",
            shop=shop,
            amount=amount,
            text="I got a UOB purchase but could not match one card from the Last 4 column. I have not logged it.",
        )
    card = matches[0]
    logged_by = _logged_by(addresses, forwarders)
    if logged_by is None:
        return EmailDecision(
            action="ask",
            kind="purchase",
            shop=shop,
            amount=amount,
            text="I got a UOB purchase but could not tell who forwarded it. I have not logged it.",
        )
    category = category_for(shop, logged_by)
    if not category:
        return EmailDecision(
            action="ask",
            kind="purchase",
            shop=shop,
            amount=amount,
            text=f"I could not match a category for {shop}. I have not logged it.",
        )
    try:
        channels = tuple(channels_for(card.owner, card.name))
    except Exception:
        channels = ()
    channel = choose_channel(shop, channels)
    if channel is None:
        choices = " or ".join(f"email channel {channel}" for channel in channels)
        return EmailDecision(
            action="ask",
            kind="channel",
            text=(
                f"I have not logged ${amount:.2f} at {shop}. "
                f"The card has {' and '.join(channels)}, and the shop does not say which. "
                f"Reply {choices}."
            ),
            amount=amount,
            shop=shop,
            last4=last4,
            expense_date=expense_date,
            category=category,
            logged_by=logged_by,
            payment_method=card.name,
            payment_owner=card.owner,
            channels=channels,
            description=shop,
        )
    return EmailDecision(
        action="log",
        kind="purchase",
        amount=amount,
        shop=shop,
        last4=last4,
        expense_date=expense_date,
        category=category,
        logged_by=logged_by,
        payment_method=card.name,
        payment_owner=card.owner,
        payment_channel=channel,
        description=shop,
    )


def _income_decision(
    kind: str,
    body: str,
    addresses: tuple[str, ...],
    forwarders: dict[str, str],
    categories: tuple[str, ...],
    today: date,
) -> EmailDecision:
    if INCOME_MISC not in categories:
        return EmailDecision(
            action="ask",
            kind=kind,
            text=(
                "I have not logged this. The sheet has no category named exactly "
                "Income - misc, and I will not invent one."
            ),
        )
    amount = _one_amount(body)
    if amount is None:
        return EmailDecision(
            action="ask",
            kind=kind,
            text="I could not see one amount, so I have not logged this as income.",
        )
    labels = {
        "refund": "Refund",
        "paynow": "PayNow",
        "dividend": "Dividend",
        "interest": "Interest",
    }
    description = labels.get(kind, kind)
    logged_by = _logged_by(addresses, forwarders) or ""
    return EmailDecision(
        action="log",
        kind=kind,
        amount=amount,
        expense_date=_mail_date(body) or today,
        category=INCOME_MISC,
        logged_by=logged_by,
        description=description,
    )


def _logged_by(addresses: tuple[str, ...], forwarders: dict[str, str]) -> str | None:
    labels: list[str] = []
    for address in addresses:
        label = forwarders.get(address.casefold())
        if label and label not in labels:
            labels.append(label)
    if len(labels) == 1:
        return labels[0]
    return None


def _one_amount(body: str) -> Decimal | None:
    found: list[Decimal] = []
    for raw in _AMOUNT_RE.findall(body or ""):
        try:
            found.append(Decimal(raw.replace(",", "")))
        except InvalidOperation:
            continue
    unique = []
    for amount in found:
        if amount not in unique:
            unique.append(amount)
    if len(unique) != 1 or unique[0] <= 0:
        return None
    return unique[0]


def _last4s(body: str) -> tuple[str, ...]:
    found: list[str] = []
    for regex in (_LAST4_RE, _MASKED_LAST4_RE):
        for match in regex.findall(body or ""):
            if match not in found:
                found.append(match)
    return tuple(found)


def _shop(body: str) -> str | None:
    for regex in (_SHOP_AFTER_DATE_RE, _SHOP_AFTER_MONTH_RE, _LABELED_SHOP_RE):
        match = regex.search(body or "")
        if match is None:
            continue
        shop = " ".join(match.group(1).strip(" -:").split())
        shop = shop.rstrip(".")
        if shop:
            return shop
    return None


def _mail_date(body: str) -> date | None:
    match = _DATE_RE.search(body or "")
    if match is None:
        return None
    raw = match.group(1)
    if "/" in raw:
        day_raw, month_raw, year_raw = raw.split("/")
        try:
            return date(int(year_raw), int(month_raw), int(day_raw))
        except ValueError:
            return None
    parts = raw.split()
    if len(parts) != 3:
        return None
    month = _MONTHS.get(parts[1].casefold())
    if month is None:
        return None
    try:
        return date(int(parts[2]), month, int(parts[0]))
    except ValueError:
        return None


def _normalize_subject(subject: str) -> str:
    text = " ".join((subject or "").split())
    changed = True
    while changed:
        changed = False
        for prefix in ("fwd:", "fw:", "re:"):
            if text.casefold().startswith(prefix):
                text = text[len(prefix) :].strip()
                changed = True
    return text.casefold()


def _entry_id() -> str:
    return uuid.uuid4().hex[:6]
