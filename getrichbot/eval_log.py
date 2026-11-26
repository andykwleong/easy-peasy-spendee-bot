"""Hidden Eval Log rows and the numbers the Eval tab shows.

One row is written when a Gmail message is handled. The mail body and the card
number are not columns. A later category change updates the final category on
that row. Opening a not-logged row reads that one message from Gmail then.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from getrichbot.email_mail import REASON_DUPLICATE
from getrichbot.email_mail import REASON_LOGGED
from getrichbot.email_mail import REASON_NO_AMOUNT
from getrichbot.email_mail import REASON_NO_CARD
from getrichbot.email_mail import REASON_NO_CARD_MATCH
from getrichbot.email_mail import REASON_NO_DATE
from getrichbot.email_mail import REASON_NO_INCOME_MISC
from getrichbot.email_mail import REASON_NOT_FOUND
from getrichbot.email_mail import REASON_NOT_WRITTEN
from getrichbot.email_mail import REASON_REVERSAL
from getrichbot.email_mail import REASON_REVERSAL_MISSING
from getrichbot.email_mail import REASON_SALARY
from getrichbot.email_mail import REASON_SHEET_UNREADABLE
from getrichbot.email_mail import REASON_STATEMENT
from getrichbot.email_mail import _amounts
from getrichbot.email_mail import _last4s
from getrichbot.email_mail import _mail_date
from getrichbot.group_post import MESSAGE_FAIL
from getrichbot.group_post import MESSAGE_SENT
from getrichbot.group_post import message_status_for

LOGGER = logging.getLogger(__name__)
EVAL_LOG_SHEET = "Eval Log"
EVAL_HEADERS = (
    "Date",
    "Gmail Message Id",
    "Sender",
    "Subject",
    "Result",
    "Reason",
    "Entry Id",
    "First Category",
    "Category Source",
    "Final Category",
)
CATEGORY_SOURCES = ("keyword", "grab", "exa_ai", "none")
SUBJECT_LIMIT = 150
SENDER_LIMIT = 200
MAIL_TEXT_LIMIT = 12_000
_MESSAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_LONG_NUMBER = re.compile(r"(?<!\d)(?:\d[ \-]?){12,}\d(?!\d)")

DIAGNOSIS_PREFIX = "eval_diagnosis:"
UNKNOWN_APPROVAL = "unknown"
APPROVAL_NOTE = "Approved. The mail was not logged."
_DIAGNOSIS_TEXT = {
    REASON_LOGGED: "This row says it was logged, but it is still marked not logged. Nothing new was written.",
    REASON_NO_AMOUNT: "This mail had no amount, so it was not logged.",
    REASON_NO_DATE: "This mail had no date, so it was not logged.",
    REASON_NO_CARD: "This mail had no card, so it was not logged. I will not guess a card.",
    REASON_NO_CARD_MATCH: "The card in this mail did not match one card on the sheet. I will not guess a card.",
    REASON_SALARY: "This looks like salary. It was left for you to type. I will not log it from here.",
    REASON_STATEMENT: "This is a statement. It was left unlogged on purpose.",
    REASON_REVERSAL: "The original charge was removed. This row is still marked not logged.",
    REASON_REVERSAL_MISSING: "This was a reversal, and the original charge was not on the sheet. No new row was written.",
    REASON_NOT_FOUND: "This mail is no longer in the mailbox, so it was not logged.",
    REASON_NO_INCOME_MISC: "Income - misc is not on the category list, so this was not logged. I will not invent a category.",
    REASON_DUPLICATE: "This looked like a duplicate, so it was not logged yet.",
    REASON_SHEET_UNREADABLE: "The card list could not be read, so this mail was not logged.",
    REASON_NOT_WRITTEN: "The sheet did not save the row, so this mail was not logged.",
}
_APPROVAL_VALUES = frozenset(_DIAGNOSIS_TEXT) | {UNKNOWN_APPROVAL}

PLAIN_REASONS = {
    REASON_LOGGED: "Logged",
    REASON_NO_AMOUNT: "No amount",
    REASON_NO_DATE: "No date",
    REASON_NO_CARD: "No card",
    REASON_NO_CARD_MATCH: "Card did not match",
    REASON_SALARY: "Salary, left for you to type",
    REASON_STATEMENT: "Statement",
    REASON_REVERSAL: "Original charge was removed",
    REASON_REVERSAL_MISSING: "Reversal, original charge not found",
    REASON_NOT_FOUND: "Mail is no longer in the mailbox",
    REASON_NO_INCOME_MISC: "Income - misc is not on the category list",
    REASON_DUPLICATE: "Possible duplicate, not logged yet",
    REASON_SHEET_UNREADABLE: "Could not read the card list",
    REASON_NOT_WRITTEN: "The sheet did not save the row",
}


@dataclass(frozen=True)
class EvalRow:
    row_number: int
    logged_on: str
    message_id: str
    sender: str
    subject: str
    result: str
    reason: str
    entry_id: str
    first_category: str
    category_source: str
    final_category: str

    @property
    def purchase(self) -> bool:
        return self.result == "pass" and self.category_source in CATEGORY_SOURCES


@dataclass(frozen=True)
class MailDiagnosis:
    text: str
    needs_mail: bool


def valid_message_id(message_id: str) -> bool:
    return _MESSAGE_ID.fullmatch(message_id or "") is not None


def mask_card_digits(text: str) -> str:
    """Hide a full card number. The last 4 digits stay. Shorter numbers stay."""

    def replace(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 13 or len(digits) > 19:
            return match.group(0)
        return "**** **** **** " + digits[-4:]

    return _LONG_NUMBER.sub(replace, text or "")


def clip_text(value: str, limit: int) -> str:
    text = mask_card_digits(" ".join((value or "").split()))
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def plain_reason(reason: str) -> str:
    return PLAIN_REASONS.get(reason, "Not logged")


def diagnosis_for(reason: str) -> MailDiagnosis:
    text = _DIAGNOSIS_TEXT.get(reason)
    if text is None:
        return MailDiagnosis(
            "The stored reason does not say why this mail was not logged. "
            "Open it to read the message. I will not guess a category or a card.",
            True,
        )
    return MailDiagnosis(text, False)


def approval_token(reason: str) -> str:
    if reason in _DIAGNOSIS_TEXT:
        return reason
    return UNKNOWN_APPROVAL


def approval_matches(reason: str, stored: str) -> bool:
    return stored == approval_token(reason)


def diagnosis_state_key(message_id: str) -> str:
    return DIAGNOSIS_PREFIX + message_id


def approved_from_state_rows(rows: list[list[str]] | None) -> dict[str, str]:
    found: dict[str, str] = {}
    for row in rows or []:
        if not row:
            continue
        key = str(row[0]).strip() if len(row) > 0 else ""
        value = str(row[1]).strip() if len(row) > 1 else ""
        if not key.startswith(DIAGNOSIS_PREFIX):
            continue
        message_id = key[len(DIAGNOSIS_PREFIX) :]
        if not valid_message_id(message_id) or value not in _APPROVAL_VALUES:
            continue
        found[message_id] = value
    return found


def open_mail_diagnosis(body: str, today: date) -> str:
    text = body or ""
    if not _amounts(text):
        return "This open mail has no amount, so there is nothing to log."
    if _mail_date(text, today) is None:
        return "This open mail has no date, so it stays unlogged."
    if not _last4s(text):
        return "This open mail has no card. I will not guess one."
    return (
        "This open mail has an amount, a date, and a card. "
        "I will not log it from here, and I will not guess a category."
    )


def failed_eval_row(raw_rows: list[list[str]] | None, message_id: str) -> EvalRow | None:
    if not valid_message_id(message_id):
        return None
    for row in parse_eval_rows(raw_rows):
        if row.result != "pass" and row.message_id == message_id:
            return row
    return None


def whole_percent(part: int, whole: int) -> int | None:
    """Nearest whole percent. Empty when there is nothing to divide."""
    if whole <= 0:
        return None
    return (part * 100 + whole // 2) // whole


def is_category_miss(first: str, final: str, source: str = "") -> bool:
    """A miss is a category changed after the row already had one.

    A blank first category is the first choice, including a mixed shop and a
    source of none. Source does not decide the miss.
    """
    del source
    first_text = " ".join((first or "").split())
    final_text = " ".join((final or "").split())
    if not first_text:
        return False
    return first_text != final_text


def category_after_choice(first: str, final: str, chosen: str) -> tuple[str, str]:
    """First choice fills both columns. A later choice updates only the final one."""
    chosen_text = " ".join((chosen or "").split())
    first_text = " ".join((first or "").split())
    final_text = " ".join((final or "").split())
    if not chosen_text:
        return first_text, final_text
    if not first_text:
        return chosen_text, chosen_text
    return first_text, chosen_text


def eval_sheet_row(
    *,
    logged_on: str,
    message_id: str,
    sender: str,
    subject: str,
    result: str,
    reason: str,
    entry_id: str = "",
    first_category: str = "",
    category_source: str = "",
    final_category: str = "",
    purchase: bool = False,
) -> list[str]:
    """One Eval Log row. There is no body column and no card-number column."""
    passed = result == "pass"
    source = ""
    first = ""
    final = ""
    saved_entry = entry_id if passed else ""
    if passed and purchase:
        source = category_source if category_source in CATEGORY_SOURCES else "none"
        first = " ".join((first_category or "").split())
        final = " ".join((final_category or first).split())
    return [
        logged_on,
        message_id,
        clip_text(sender, SENDER_LIMIT),
        clip_text(subject, SUBJECT_LIMIT),
        "pass" if passed else "fail",
        reason,
        saved_entry,
        first,
        source,
        final,
    ]


def handled_reversal_row_numbers(raw_rows: list[list[str]] | None) -> list[int]:
    """Rows whose charge was removed, saved before that counted as handled.

    Reason ``reversal`` is stored only when the original charge was removed.
    Reason ``reversal_missing`` means that charge was not found, so it stays
    a fail. The subject is not used.
    """
    return [
        row.row_number
        for row in parse_eval_rows(raw_rows)
        if row.reason == REASON_REVERSAL and row.result != "pass"
    ]


def parse_eval_rows(raw_rows: list[list[str]] | None) -> list[EvalRow]:
    parsed: list[EvalRow] = []
    for index, raw in enumerate(raw_rows or [], start=2):
        row = _parse_eval_row(index, raw)
        if row is not None:
            parsed.append(row)
    return parsed


def eval_view(
    raw_rows: list[list[str]] | None,
    records,
    card_usage,
    marks: dict[str, str],
    today,
    approvals: dict[str, str] | None = None,
) -> dict:
    shops: dict[str, str] = {}
    for record in records or []:
        entry_id = str(getattr(record, "entry_id", "") or "")
        if entry_id:
            shops[entry_id.casefold()] = str(getattr(record, "description", "") or "")
    emails = []
    purchases = []
    accepted = approvals or {}
    for row in parse_eval_rows(raw_rows):
        item = {
            "date": row.logged_on,
            "sender": row.sender,
            "subject": row.subject,
            "result": row.result,
            "reason": plain_reason(row.reason),
            "message_id": row.message_id,
        }
        if row.result != "pass":
            diagnosis = diagnosis_for(row.reason)
            item["diagnosis"] = diagnosis.text
            item["needs_mail"] = diagnosis.needs_mail
            item["approved"] = approval_matches(row.reason, accepted.get(row.message_id, ""))
        emails.append(item)
        if not row.purchase:
            continue
        purchases.append(
            {
                "date": row.logged_on,
                "shop": shops.get(row.entry_id.casefold(), ""),
                "first_category": row.first_category,
                "final_category": row.final_category,
                "changed": is_category_miss(row.first_category, row.final_category, row.category_source),
            }
        )
    return {
        "today": today.isoformat(),
        "emails": emails,
        "purchases": purchases,
        "group_messages": group_message_rows(records, card_usage, marks),
    }


def group_message_rows(records, card_usage, marks: dict[str, str]) -> list[dict]:
    """Logged notes only. A row with no mark is left out."""
    rows: list[dict] = []
    for record in records or []:
        if str(getattr(record, "status", "")).casefold() != "confirmed":
            continue
        status = message_status_for(str(getattr(record, "entry_id", "") or ""), marks)
        if status not in {MESSAGE_SENT, MESSAGE_FAIL}:
            continue
        rows.append(
            {
                "date": str(getattr(record, "expense_date", "") or ""),
                "shop": str(getattr(record, "description", "") or ""),
                "amount": f"{getattr(record, 'amount'):.2f}",
                "logged_by": str(getattr(record, "logged_by", "") or ""),
                "sent": status == MESSAGE_SENT,
            }
        )
    for record in card_usage or []:
        if str(getattr(record, "status", "")).casefold() != "confirmed":
            continue
        status = message_status_for(str(getattr(record, "entry_id", "") or ""), marks)
        if status not in {MESSAGE_SENT, MESSAGE_FAIL}:
            continue
        rows.append(
            {
                "date": str(getattr(record, "usage_date", "") or ""),
                "shop": str(getattr(record, "description", "") or ""),
                "amount": f"{getattr(record, 'amount'):.2f}",
                "logged_by": str(getattr(record, "logged_by", "") or ""),
                "sent": status == MESSAGE_SENT,
            }
        )
    return rows


def remember_category_choice(sheets, entry_id: str, category: str) -> None:
    """Update the Eval Log category. A failure leaves the expense row as saved."""
    updater = getattr(sheets, "update_eval_category", None)
    if updater is None:
        return
    try:
        updater(entry_id, category)
    except Exception:
        LOGGER.exception("Could not update the Eval Log category. The expense row is already saved.")


def failed_message_ids(raw_rows: list[list[str]] | None) -> set[str]:
    return {row.message_id for row in parse_eval_rows(raw_rows) if row.result != "pass" and row.message_id}


def limit_mail_text(text: str) -> str:
    masked = mask_card_digits(text or "")
    if len(masked) <= MAIL_TEXT_LIMIT:
        return masked
    return masked[:MAIL_TEXT_LIMIT].rstrip() + "\n\nThe rest of this mail is not shown."


def _parse_eval_row(row_number: int, raw: list[str]) -> EvalRow | None:
    cells = [_cell(raw, index) for index in range(len(EVAL_HEADERS))]
    if cells[0] == EVAL_HEADERS[0] and cells[1] == EVAL_HEADERS[1]:
        return None
    if not cells[0] and not cells[1]:
        return None
    result = "pass" if cells[4] == "pass" else "fail"
    source = cells[8] if cells[8] in CATEGORY_SOURCES else ""
    return EvalRow(
        row_number=row_number,
        logged_on=cells[0],
        message_id=cells[1],
        sender=cells[2],
        subject=cells[3],
        result=result,
        reason=cells[5],
        entry_id=cells[6],
        first_category=cells[7],
        category_source=source,
        final_category=cells[9],
    )


def _cell(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return str(row[index] or "").strip()
