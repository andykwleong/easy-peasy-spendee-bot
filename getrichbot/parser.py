from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from getrichbot.categories import CATEGORY_KEYWORDS, SHOPPING_KEYWORDS
from getrichbot.models import ExpenseDraft

AMOUNT_RE = re.compile(r"(?:(?:S\$|\$)\s*)?(\d+(?:,\d{3})*(?:\.\d{1,2})?)", re.IGNORECASE)
ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{1,2}-\d{1,2})\b")
SLASH_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")
DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+(\d{2,4}))?\b",
    re.IGNORECASE,
)
MONTH_DAY_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{2,4}))?\b",
    re.IGNORECASE,
)
MONTHS = {
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


def parse_expense(
    text: str,
    logged_by: str,
    me_label: str,
    wife_label: str,
    today: date | None = None,
) -> ExpenseDraft | None:
    cleaned = " ".join(text.strip().split())
    amount = _extract_amount(cleaned)
    if amount is None:
        return None

    reference_date = today or date.today()
    expense_date, without_date, needs_date_confirmation = _extract_expense_date(cleaned, reference_date)
    description = _description_without_amount(without_date)
    category, confidence = _categorize(description, logged_by, me_label, wife_label)
    return ExpenseDraft(
        raw_input=cleaned,
        amount=amount,
        category=category,
        description=description or cleaned,
        confidence=confidence,
        expense_date=expense_date,
        needs_date_confirmation=needs_date_confirmation,
    )


def _extract_amount(text: str) -> Decimal | None:
    matches = list(AMOUNT_RE.finditer(text))
    if not matches:
        return None

    # Household expense messages normally have one amount. If there are multiple,
    # take the last one because "dinner for 2 60" is common.
    raw = matches[-1].group(1).replace(",", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _description_without_amount(text: str) -> str:
    matches = list(AMOUNT_RE.finditer(text))
    if not matches:
        return text
    match = matches[-1]
    before = text[: match.start()].strip(" -:")
    after = text[match.end() :].strip(" -:")
    return " ".join(part for part in [before, after] if part).strip()


def extract_standalone_date(text: str, today: date | None = None) -> tuple[date | None, bool]:
    reference_date = today or date.today()
    parsed, remaining, needs_confirmation = _extract_expense_date(text.strip(), reference_date)
    if parsed is not None and not remaining.strip():
        return parsed, needs_confirmation
    return None, False


def extract_date_phrase(text: str, today: date | None = None) -> tuple[date | None, bool]:
    reference_date = today or date.today()
    parsed, _, needs_confirmation = _extract_expense_date(text.strip(), reference_date)
    return parsed, needs_confirmation


def _extract_expense_date(text: str, today: date) -> tuple[date | None, str, bool]:
    lowered = text.lower()
    if "yesterday" in lowered:
        return today - timedelta(days=1), _remove_word(text, "yesterday"), False
    if "today" in lowered:
        return today, _remove_word(text, "today"), False

    match = ISO_DATE_RE.search(text)
    if match is not None:
        try:
            parsed = date.fromisoformat(match.group(1))
        except ValueError:
            return None, text, True
        return parsed, (text[: match.start()] + text[match.end() :]).strip(), False

    for regex, builder in (
        (DAY_MONTH_RE, lambda m: _date_from_parts(m.group(1), m.group(2), m.group(3), today)),
        (MONTH_DAY_RE, lambda m: _date_from_parts(m.group(2), m.group(1), m.group(3), today)),
    ):
        match = regex.search(text)
        if match is not None:
            parsed = builder(match)
            if parsed is None:
                continue
            return parsed, (text[: match.start()] + text[match.end() :]).strip(), False

    match = SLASH_DATE_RE.search(text)
    if match is not None:
        parsed, needs_confirmation = _slash_date(match, today)
        remaining = (text[: match.start()] + text[match.end() :]).strip()
        return parsed, remaining, needs_confirmation

    return None, text, False


def _date_from_parts(day_raw: str, month_raw: str, year_raw: str | None, today: date) -> date | None:
    day = int(day_raw)
    month = MONTHS[month_raw.lower()]
    year = _normalize_year(year_raw, today.year)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _slash_date(match: re.Match[str], today: date) -> tuple[date | None, bool]:
    first = int(match.group(1))
    second = int(match.group(2))
    year = _normalize_year(match.group(3), today.year)

    if first > 12 and second <= 12:
        try:
            return date(year, second, first), False
        except ValueError:
            return None, True

    if second > 12 and first <= 12:
        try:
            return date(year, first, second), False
        except ValueError:
            return None, True

    # Ambiguous dates like 05/06 could be 5 Jun or 6 May.
    return None, True


def _normalize_year(year_raw: str | None, default_year: int) -> int:
    if not year_raw:
        return default_year
    year = int(year_raw)
    if year < 100:
        return 2000 + year
    return year


def _remove_word(text: str, word: str) -> str:
    return re.sub(rf"\b{re.escape(word)}\b", "", text, flags=re.IGNORECASE).strip()


def _categorize(description: str, logged_by: str, me_label: str, wife_label: str) -> tuple[str | None, float]:
    lowered = description.lower()

    if any(keyword in lowered for keyword in SHOPPING_KEYWORDS):
        if logged_by == wife_label:
            return "Shopping - My wife", 0.9
        if logged_by == me_label:
            return "Shopping - Me", 0.9

    best_category: str | None = None
    best_score = 0
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in lowered)
        if score > best_score:
            best_category = category
            best_score = score

    if best_category is None:
        return None, 0.0
    return best_category, min(0.95, 0.55 + (best_score * 0.2))
