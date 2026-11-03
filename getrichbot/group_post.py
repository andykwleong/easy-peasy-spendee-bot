"""Whether Telegram accepted the group post for a logged row.

The sheet write happens first. This module only decides the mark after Telegram
answers. A mark is stored in Bot State under ``group_post:`` plus the entry id.
The value is exactly ``Message sent`` or ``message fail``. The mail text and the
card number are not stored.
"""

from __future__ import annotations

import logging
import re

LOGGER = logging.getLogger(__name__)

MESSAGE_SENT = "Message sent"
MESSAGE_FAIL = "message fail"
GROUP_POST_PREFIX = "group_post:"
_ENTRY_ID = re.compile(r"^[A-Za-z0-9]{6}$")
_ENTRY_IN_TEXT = re.compile(r"\[([A-Za-z0-9]{6})\]")
_MARKS = {MESSAGE_SENT, MESSAGE_FAIL}


def group_post_key(entry_id: str) -> str:
    return f"{GROUP_POST_PREFIX}{entry_id}"


def logged_row_ids(text: str) -> list[str]:
    """Entry ids from lines that announce a logged row, in order, without duplicates."""
    found: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("Logged ") and not stripped.startswith("Tracked "):
            continue
        match = _ENTRY_IN_TEXT.search(stripped)
        if match is None:
            continue
        entry_id = match.group(1)
        if entry_id not in found:
            found.append(entry_id)
    return found


def message_id_from(sent: object) -> str | None:
    if isinstance(sent, bool) or sent is None:
        return None
    if isinstance(sent, int):
        return str(sent) if sent else None
    if isinstance(sent, str):
        cleaned = sent.strip()
        return cleaned or None
    message_id = getattr(sent, "message_id", None)
    if message_id in (None, ""):
        return None
    return str(message_id)


async def one_attempt(send) -> tuple[str, str | None]:
    """One Telegram try.

    ``sent`` means Telegram returned a message id.
    ``silent`` means the call finished without a message id and without an error.
    ``failed`` means the call raised.
    """
    try:
        sent = await send()
    except Exception:
        LOGGER.warning("Telegram did not accept the group post.", exc_info=True)
        return "failed", None
    message_id = message_id_from(sent)
    if message_id:
        return "sent", message_id
    return "silent", None


async def deliver_group_post(send) -> tuple[str | None, str | None]:
    """Try once. If that try raises, try once more.

    A first try that finishes without a message id and without an error is left
    unmarked and is not retried. Two failures record ``message fail``.
    """
    kind, message_id = await one_attempt(send)
    if kind == "sent":
        return MESSAGE_SENT, message_id
    if kind == "silent":
        return None, None
    kind, message_id = await one_attempt(send)
    if kind == "sent":
        return MESSAGE_SENT, message_id
    return MESSAGE_FAIL, None


def marks_from_state_rows(rows: list[list[str]] | None) -> dict[str, str]:
    """Known marks only. Anything else, including a blank, is left out."""
    marks: dict[str, str] = {}
    for row in rows or []:
        if not row:
            continue
        key = str(row[0]).strip() if len(row) > 0 else ""
        value = str(row[1]).strip() if len(row) > 1 else ""
        if not key.startswith(GROUP_POST_PREFIX):
            continue
        entry_id = key[len(GROUP_POST_PREFIX) :]
        if not _ENTRY_ID.fullmatch(entry_id) or value not in _MARKS:
            continue
        marks[entry_id] = value
    return marks


def messages_sent_count(marks: dict[str, str]) -> int:
    return sum(1 for value in marks.values() if value == MESSAGE_SENT)


def message_status_for(entry_id: str, marks: dict[str, str]) -> str:
    status = marks.get(entry_id, "")
    if status not in _MARKS:
        return ""
    return status
