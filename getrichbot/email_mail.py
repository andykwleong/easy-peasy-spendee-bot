from __future__ import annotations

import logging
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
from getrichbot.models import PAYNOW_METHOD
from getrichbot.models import ExpenseRow
from getrichbot.parser import resolve_transaction_date
from getrichbot.shop_category import category_for_purchase
from getrichbot.shop_category import category_from_keywords
from getrichbot.shop_category import purchase_category
from getrichbot.shop_category import is_email_shop

LOGGER = logging.getLogger(__name__)
LAST_BOT_MESSAGE_KEY = "last_bot_message_id"
_ENTRY_RE = re.compile(r"\[([A-Za-z0-9]{6})\]")

SINGAPORE_TZ = ZoneInfo("Asia/Singapore")
INCOME_MISC = "Income - misc"

PURCHASE_SUBJECT = "uob - transaction alert"
REFUND_SUBJECT = "your transaction has been refunded"
STATEMENT_SUBJECT = "your estatement/eadvice is ready for viewing"
PAYNOW_SUBJECT = "uob-paynow transfer received"

_AMOUNT_RE = re.compile(
    r"(?:SGD|S\$|\$)\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
_LAST4_RE = re.compile(
    r"(?:card\s+ending|last\s*4(?:\s*digits)?|(?<![A-Za-z])ending)\s*[:\-]?\s*(\d{4})\b",
    re.IGNORECASE,
)
_ACCOUNT_ENDING_RE = re.compile(
    r"(?<![A-Za-z])(?:a/c|account)\s+ending\s*[:\-]?\s*(\d{4})\b",
    re.IGNORECASE,
)
_MASKED_GROUP_LAST4_RE = re.compile(r"(?:[*xX]{2,4}(?:[-\s]+[*xX]{2,4}){2,}[-\s]+)(\d{4})\b")
_MASKED_LAST4_RE = re.compile(r"(?:[*xX]{2,}|•{2,})\s*(\d{4})(?!\d)")
_SLASH_DATE = r"\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})"
_MONTH_DATE = r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}"
_CLOCK = r"\d{1,2}:\d{2}(?:\s*[AP]M)?"
_OPTIONAL_CLOCK = rf"(?:\s+(?:at\s+)?{_CLOCK})?"
_SHOP_AFTER_DATE_RE = re.compile(
    rf"\bon\s+({_SLASH_DATE}){_OPTIONAL_CLOCK}\s+at\s+(.+?)(?=\.|\n|$)",
    re.IGNORECASE,
)
_SHOP_AFTER_MONTH_RE = re.compile(
    rf"\bon\s+({_MONTH_DATE}){_OPTIONAL_CLOCK}\s+at\s+(.+?)(?=\.|\n|$)",
    re.IGNORECASE,
)
_SHOP_LABEL_RE = re.compile(
    r"^(?:transaction\s+details|description|merchant|shop|to)\b\s*:?\s*(.*)$",
    re.IGNORECASE,
)
_UEN_RE = re.compile(r"\s*\(\s*UEN\s+ending\s+[^)]*\)", re.IGNORECASE)
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
_REVERSAL_RE = re.compile(r"\breversal\b", re.IGNORECASE)
REVERSAL_MISSING_TEXT = (
    "I could not find the original charge to reverse. I have not logged a new row."
)
MIXED_SHOP_ASK = "This shop has been more than one category. Which one is this?"
_CATEGORY_MISSING_TEXT = "Category is missing. Reply to this message with the category."


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
    shop_choices: tuple[str, ...] = ()


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
    remember_category=None,
    expense_records=None,
    stop_keyword=None,
) -> EmailDecision:
    kind = classify_kind(subject, body)
    if kind == "statement":
        return EmailDecision(action="ignore", kind=kind)
    if kind in {"refund", "paynow", "dividend", "interest"}:
        return _income_decision(kind, body, addresses, forwarders, categories, today)
    if _REVERSAL_RE.search(body or ""):
        return _reversal_decision(body, today)
    purchase = _purchase_decision(
        body,
        addresses,
        forwarders=forwarders,
        methods=methods,
        categories=categories,
        channels_for=channels_for,
        category_for=category_for,
        today=today,
        remember_category=remember_category,
        expense_records=expense_records,
        stop_keyword=stop_keyword,
    )
    if purchase.action == "log":
        return purchase
    if kind == "salary":
        return EmailDecision(
            action="ask",
            kind=kind,
            text="This looks like salary. I have not logged it. Type income in the chat if you want it recorded.",
        )
    return purchase


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
    income = decision.category.casefold().startswith("income -")
    if decision.kind == "purchase":
        description = decision.shop or decision.description or ""
    else:
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
    place = f" at {row.description}" if row.description else ""
    if not (row.category or "").strip():
        return (
            f"Logged ${row.amount:.2f}{place} - {when} via {row.payment_method} ({channel}) [{row.entry_id}]\n"
            f"{_CATEGORY_MISSING_TEXT}"
        )
    return (
        f"Logged ${row.amount:.2f}{place} to {row.category} - "
        f"{when} via {row.payment_method} ({channel}) [{row.entry_id}]"
    )


def mixed_shop_notice(text: str) -> str:
    """Ask which past category this charge is, instead of saying the category is missing."""
    if _CATEGORY_MISSING_TEXT in text:
        return text.replace(_CATEGORY_MISSING_TEXT, MIXED_SHOP_ASK)
    if MIXED_SHOP_ASK in text:
        return text
    return f"{text}\n{MIXED_SHOP_ASK}"


def logged_notice_entry_id(text: str) -> str | None:
    first = (text or "").splitlines()[0]
    if not first.startswith("Logged "):
        return None
    return entry_id_in_text(first)


def entry_id_in_text(text: str) -> str | None:
    match = _ENTRY_RE.search(text or "")
    if match is None:
        return None
    return match.group(1)


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
    categories: tuple[str, ...],
    channels_for,
    category_for,
    today: date,
    remember_category=None,
    expense_records=None,
    stop_keyword=None,
) -> EmailDecision:
    clause = _first_purchase_clause(body)
    if clause is None:
        shop = _labeled_shop(body) or ""
        expense_date = _mail_date(body, today)
        sentence = None
    else:
        shop = clause.shop
        expense_date = clause.expense_date
        sentence = clause.sentence
    amount = _chosen_amount(body, sentence)
    if _is_bank_account_payment(body):
        return _bank_account_decision(
            body,
            addresses,
            forwarders=forwarders,
            categories=categories,
            category_for=category_for,
            shop=shop,
            amount=amount,
            expense_date=expense_date,
            remember_category=remember_category,
            expense_records=expense_records,
            stop_keyword=stop_keyword,
        )
    last4 = _chosen_last4(body, sentence)
    missing = _missing_purchase_pieces(amount, expense_date, last4)
    if missing:
        if amount is None and not last4:
            return EmailDecision(action="ignore", kind="purchase")
        return EmailDecision(
            action="ask",
            kind="purchase",
            text=_missing_purchase_text(missing),
        )
    matches = match_cards_by_last4(last4, methods)
    if len(matches) != 1:
        return EmailDecision(
            action="ask",
            kind="purchase",
            shop=shop,
            amount=amount,
            text="I got a purchase but could not match one card from the Last 4 column. I have not logged it.",
        )
    card = matches[0]
    logged_by = _logged_by(addresses, forwarders) or ""
    chosen_category = purchase_category(
        body,
        shop,
        logged_by,
        categories,
        category_for,
        remember_category,
        expense_records,
        stop_keyword,
    )
    category = chosen_category.category
    try:
        channels = tuple(channels_for(card.owner, card.name))
    except Exception:
        channels = ()
    chosen = choose_channel(shop, channels)
    channel = "" if chosen is None else chosen
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
        shop_choices=chosen_category.choices,
    )


def _is_bank_account_payment(body: str) -> bool:
    return bool(_ACCOUNT_ENDING_RE.search(body or "")) and not _last4s(body)


def _bank_account_decision(
    body: str,
    addresses: tuple[str, ...],
    *,
    forwarders: dict[str, str],
    categories: tuple[str, ...],
    category_for,
    shop: str,
    amount: Decimal | None,
    expense_date: date | None,
    remember_category=None,
    expense_records=None,
    stop_keyword=None,
) -> EmailDecision:
    missing = []
    if amount is None:
        missing.append("amount")
    if expense_date is None:
        missing.append("date")
    if missing:
        if amount is None:
            return EmailDecision(action="ignore", kind="purchase")
        return EmailDecision(
            action="ask",
            kind="purchase",
            text=_missing_purchase_text(missing),
        )
    logged_by = _logged_by(addresses, forwarders) or ""
    chosen_category = purchase_category(
        body,
        shop,
        logged_by,
        categories,
        category_for,
        remember_category,
        expense_records,
        stop_keyword,
    )
    return EmailDecision(
        action="log",
        kind="purchase",
        amount=amount,
        shop=shop,
        expense_date=expense_date,
        category=chosen_category.category,
        logged_by=logged_by,
        payment_method=PAYNOW_METHOD,
        description=shop,
        shop_choices=chosen_category.choices,
    )


def _reversal_decision(body: str, today: date) -> EmailDecision:
    clause = _first_purchase_clause(body)
    if clause is None:
        shop = _labeled_shop(body) or ""
        sentence = None
    else:
        shop = clause.shop
        sentence = clause.sentence
    amount = _chosen_amount(body, sentence)
    last4 = _chosen_last4(body, sentence) or ""
    return EmailDecision(
        action="reverse",
        kind="reversal",
        amount=amount,
        shop=shop,
        last4=last4,
        expense_date=_mail_date(body, today),
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
        expense_date=_mail_date(body, today) or today,
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


@dataclass(frozen=True)
class _PurchaseClause:
    shop: str
    expense_date: date | None
    sentence: str


def _one_amount(body: str) -> Decimal | None:
    found = _amounts(body)
    if len(found) == 1:
        return found[0]
    return None


def _amounts(body: str) -> list[Decimal]:
    found: list[Decimal] = []
    for raw in _AMOUNT_RE.findall(body or ""):
        try:
            amount = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            continue
        if amount > 0 and amount not in found:
            found.append(amount)
    return found


def _account_ending_spans(body: str) -> tuple[tuple[int, int], ...]:
    return tuple((match.start(), match.end()) for match in _ACCOUNT_ENDING_RE.finditer(body or ""))


def _overlaps_account_ending(start: int, end: int, spans: tuple[tuple[int, int], ...]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _last4s(body: str) -> tuple[str, ...]:
    spans = _account_ending_spans(body)
    found: list[str] = []
    for regex in (_MASKED_GROUP_LAST4_RE, _MASKED_LAST4_RE, _LAST4_RE):
        for match in regex.finditer(body or ""):
            if _overlaps_account_ending(match.start(), match.end(), spans):
                continue
            value = match.group(1)
            if value not in found:
                found.append(value)
    return tuple(found)


def _chosen_amount(body: str, sentence: str | None) -> Decimal | None:
    return _chosen_value(_amounts(body), _amounts(sentence or ""))


def _chosen_last4(body: str, sentence: str | None) -> str | None:
    return _chosen_value(list(_last4s(body)), list(_last4s(sentence or "")))


def _chosen_value(whole, inside):
    if len(whole) == 1:
        return whole[0]
    if len(whole) > 1 and len(inside) == 1:
        return inside[0]
    return None


def _missing_purchase_pieces(amount, expense_date, last4) -> list[str]:
    missing = []
    if amount is None:
        missing.append("amount")
    if expense_date is None:
        missing.append("date")
    if not last4:
        missing.append("card")
    return missing


def _missing_purchase_text(missing: list[str]) -> str:
    pieces = [f"the {name}" for name in missing]
    if len(pieces) == 1:
        detail = f"{pieces[0]} was"
    elif len(pieces) == 2:
        detail = f"{pieces[0]} and {pieces[1]} were"
    else:
        detail = f"{', '.join(pieces[:-1])}, and {pieces[-1]} were"
    return f"I got a transaction, but {detail} missing. I have not logged it."


def _first_purchase_clause(body: str) -> _PurchaseClause | None:
    text = body or ""
    found = None
    for regex in (_SHOP_AFTER_DATE_RE, _SHOP_AFTER_MONTH_RE):
        match = regex.search(text)
        if match is not None and (found is None or match.start() < found.start()):
            found = match
    if found is None:
        return None
    shop = _clean_shop(found.group(2))
    if not shop:
        return None
    return _PurchaseClause(
        shop=shop,
        expense_date=_parse_date_text(found.group(1)),
        sentence=_sentence_around(text, found.start(), found.end()),
    )


def _labeled_shop(body: str) -> str | None:
    lines = (body or "").splitlines()
    for index, line in enumerate(lines):
        match = _SHOP_LABEL_RE.match(line.strip())
        if match is None:
            continue
        raw = match.group(1).strip()
        if not raw:
            for later in lines[index + 1 :]:
                if later.strip():
                    raw = later.strip()
                    break
        shop = _clean_shop(raw)
        if not shop or is_email_shop(shop):
            continue
        return shop
    return None


def _clean_shop(raw: str) -> str | None:
    shop = _UEN_RE.sub("", raw or "")
    shop = " ".join(shop.strip(" -:").split())
    shop = shop.rstrip(".")
    return shop or None


def _sentence_around(body: str, start: int, end: int) -> str:
    left = start
    while left > 0:
        if body[left - 1] == "\n":
            break
        if body[left - 1] == "." and not _decimal_dot(body, left - 1):
            break
        left -= 1
    right = end
    while right < len(body):
        if body[right] == "\n":
            break
        if body[right] == "." and not _decimal_dot(body, right):
            break
        right += 1
    return body[left:right]


def _decimal_dot(body: str, index: int) -> bool:
    return index > 0 and index + 1 < len(body) and body[index - 1].isdigit() and body[index + 1].isdigit()


def _mail_date(body: str, today: date) -> date | None:
    when, _span = resolve_transaction_date(body, today, include_numeric=True)
    return when


def _parse_date_text(raw: str) -> date | None:
    when, _span = resolve_transaction_date(raw, date.today(), include_numeric=True)
    return when


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
