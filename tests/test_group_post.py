from __future__ import annotations

import asyncio
import unittest
from datetime import date
from decimal import Decimal

from getrichbot.bot import FinanceBot
from getrichbot.dashboard_view import build_dashboard_payload, person_filter_labels
from getrichbot.group_post import (
    MESSAGE_FAIL,
    MESSAGE_SENT,
    deliver_group_post,
    logged_row_ids,
    marks_from_state_rows,
    messages_sent_count,
    one_attempt,
)
from getrichbot.models import ExpenseRecord


class _Sent:
    def __init__(self, message_id):
        self.message_id = message_id


class GroupPostTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_message_id_is_message_sent_and_is_not_retried(self):
        calls = {"n": 0}

        async def send():
            calls["n"] += 1
            return _Sent(42)

        mark, message_id = await deliver_group_post(send)

        self.assertEqual(mark, MESSAGE_SENT)
        self.assertEqual(message_id, "42")
        self.assertEqual(calls["n"], 1)

    async def test_one_retry_then_success(self):
        calls = {"n": 0}

        async def send():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("telegram down")
            return _Sent(7)

        mark, message_id = await deliver_group_post(send)

        self.assertEqual(mark, MESSAGE_SENT)
        self.assertEqual(message_id, "7")
        self.assertEqual(calls["n"], 2)

    async def test_one_retry_then_failure_is_message_fail(self):
        calls = {"n": 0}

        async def send():
            calls["n"] += 1
            raise RuntimeError("telegram down")

        mark, message_id = await deliver_group_post(send)

        self.assertEqual(mark, MESSAGE_FAIL)
        self.assertIsNone(message_id)
        self.assertEqual(calls["n"], 2)

    async def test_a_silent_result_is_not_retried_and_is_not_a_mark(self):
        calls = {"n": 0}

        async def send():
            calls["n"] += 1
            return None

        kind, message_id = await one_attempt(send)
        mark, delivered = await deliver_group_post(send)

        self.assertEqual(kind, "silent")
        self.assertIsNone(message_id)
        self.assertIsNone(mark)
        self.assertIsNone(delivered)
        self.assertEqual(calls["n"], 2)

    def test_historical_and_unknown_rows_are_not_given_a_status(self):
        marks = marks_from_state_rows(
            [
                ["group_post:aa1001", "Message sent"],
                ["group_post:bb2002", ""],
                ["group_post:cc3003", "guessed"],
                ["last_bot_message_id", "Message sent"],
                ["group_post:not-an-id", MESSAGE_FAIL],
            ]
        )

        self.assertEqual(marks, {"aa1001": MESSAGE_SENT})
        self.assertEqual(messages_sent_count(marks), 1)
        self.assertEqual(logged_row_ids("Possible duplicate [aa1001]\nLogged $1.00 to Food [bb2002]"), ["bb2002"])

    async def test_group_reply_stores_the_mark_and_a_failed_retry_stores_message_fail(self):
        sheets = _Sheets()
        bot = FinanceBot(_Settings(), sheets)

        async def accept():
            return _Sent(9)

        await bot._post_group_notice(accept, "Logged $1.00 to Food - 1 May 2026 [abc123]", -100)
        self.assertEqual(sheets.state["group_post:abc123"], MESSAGE_SENT)

        calls = {"n": 0}

        async def reject():
            calls["n"] += 1
            raise RuntimeError("telegram down")

        await bot._post_group_notice(reject, "Logged $2.00 to Food - 2 May 2026 [def456]", -100)
        await _finish(bot)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(sheets.state["group_post:def456"], MESSAGE_FAIL)
        self.assertNotIn("Logged", " ".join(sheets.state.values()))

    async def test_a_chat_outside_the_group_does_not_invent_a_mark(self):
        sheets = _Sheets()
        bot = FinanceBot(_Settings(), sheets)
        calls = {"n": 0}

        async def accept():
            calls["n"] += 1
            return _Sent(3)

        await bot._post_group_notice(accept, "Logged $1.00 to Food [abc123]", 55)

        self.assertEqual(calls["n"], 1)
        self.assertEqual(sheets.state, {})


class PersonFilterTests(unittest.TestCase):
    def test_andy_and_fx_are_fixed_for_every_viewer(self):
        labels = ("Alex", "Sam")
        self.assertEqual(
            person_filter_labels(labels),
            [{"scope": "Alex", "label": "Andy"}, {"scope": "Sam", "label": "FX"}],
        )
        self.assertEqual(person_filter_labels(("Sam", "Alex"))[0]["label"], "Andy")
        self.assertEqual(person_filter_labels(("Sam", "Alex"))[0]["scope"], "Sam")
        named = person_filter_labels(("FX", "Andy"))
        self.assertEqual(named, [{"scope": "Andy", "label": "Andy"}, {"scope": "FX", "label": "FX"}])

    def test_eval_counts_sent_marks_and_a_blank_row_has_no_dot(self):
        records = [
            _expense("aa1001", "Alex"),
            _expense("bb2002", "Sam"),
            _expense("cc3003", "Alex"),
        ]
        marks = {"aa1001": MESSAGE_SENT, "cc3003": MESSAGE_FAIL}
        opened_by_alex = build_dashboard_payload(
            viewer_label="Alex",
            scope="both",
            records=records,
            card_usage=[],
            payment_config=None,
            today=date(2026, 9, 26),
            household_labels=("Alex", "Sam"),
            message_marks=marks,
        )
        opened_by_sam = build_dashboard_payload(
            viewer_label="Sam",
            scope="both",
            records=records,
            card_usage=[],
            payment_config=None,
            today=date(2026, 9, 26),
            household_labels=("Alex", "Sam"),
            message_marks=marks,
        )
        by_id = {row["id"]: row["message_status"] for row in opened_by_alex["recent"]}

        self.assertEqual(opened_by_alex["person_filters"], opened_by_sam["person_filters"])
        self.assertEqual(by_id["aa1001"], "Message sent")
        self.assertEqual(by_id["cc3003"], "message fail")
        self.assertEqual(by_id["bb2002"], "")
        self.assertEqual(opened_by_alex["agent_eval"]["messages_sent"], 1)
        self.assertEqual(opened_by_alex["agent_eval"]["description"], "Telegram accepted the group post.")
        self.assertFalse(opened_by_alex["agent_eval"]["scoring"])
        self.assertNotIn("aa1001", opened_by_alex["agent_eval"])

        andy_cards = build_dashboard_payload(
            viewer_label="Sam",
            scope="both",
            records=records,
            card_usage=[],
            payment_config=None,
            today=date(2026, 9, 26),
            cards_scope="Alex",
            household_labels=("Alex", "Sam"),
        )
        self.assertEqual(andy_cards["cards"]["owner"], "Alex")


class _Settings:
    telegram_chat_id = -100
    bot_state_sheet = "Bot State"


class _Sheets:
    def __init__(self):
        self.state = {}

    def set_state_value(self, _sheet_name, key, value):
        self.state[key] = value


def _expense(entry_id: str, logged_by: str) -> ExpenseRecord:
    return ExpenseRecord(
        row_number=2,
        entry_id=entry_id,
        timestamp="12:00:00",
        expense_date="2026-09-20",
        month="2026-09",
        logged_by=logged_by,
        raw_input="sample",
        amount=Decimal("1.00"),
        category="Food",
        description="Sample cafe",
        input_type="text",
        status="Confirmed",
    )


async def _finish(bot: FinanceBot) -> None:
    tasks = list(bot._group_post_tasks)
    if tasks:
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    unittest.main()
