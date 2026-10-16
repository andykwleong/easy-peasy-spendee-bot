import threading
import time
import unittest

from getrichbot.sheet_changes import SETTLE_CAP_SECONDS
from getrichbot.sheet_changes import SETTLE_SECONDS
from getrichbot.sheet_changes import SheetChanges

WATCHED = frozenset({"Raw Expenses", "Card Usage"})


def watched() -> SheetChanges:
    return SheetChanges(WATCHED)


class SheetChangesTests(unittest.TestCase):
    def test_watched_write_moves_the_mark_and_a_new_boot_differs(self):
        first = watched()
        second = watched()
        before = first.mark()
        boot, count = before.split(".", 1)

        self.assertEqual(count, "0")
        self.assertNotEqual(boot, second.mark().split(".", 1)[0])
        self.assertNotEqual(before, second.mark())

        first.note_write("Raw Expenses")
        moved = first.mark()
        self.assertNotEqual(moved, before)
        self.assertEqual(moved, f"{boot}.1")
        self.assertEqual(moved.split(".", 1)[0], boot)

    def test_unwatched_sheet_names_do_not_move_the_mark(self):
        changes = watched()
        before = changes.mark()

        changes.note_write("Monthly Summary")
        changes.note_write("Bot State")
        changes.note_write("Category Keywords")

        self.assertEqual(changes.mark(), before)
        changes.note_write("Card Usage")
        self.assertNotEqual(changes.mark(), before)

    def test_mismatch_returns_at_once(self):
        changes = watched()
        previous = changes.mark()
        changes.note_write("Raw Expenses")

        started = time.monotonic()
        found = changes.wait_past(previous, timeout=5)

        self.assertEqual(found, changes.mark())
        self.assertNotEqual(found, previous)
        self.assertLess(time.monotonic() - started, 0.25)

    def test_timeout_returns_the_same_mark(self):
        changes = watched()
        current = changes.mark()

        found = changes.wait_past(current, timeout=0.05)

        self.assertEqual(found, current)
        self.assertEqual(changes.mark(), current)

    def test_settle_holds_past_a_burst(self):
        changes = watched()
        since = changes.mark()
        result: dict[str, float | str] = {}

        def wait() -> None:
            result["mark"] = changes.wait_past(since, timeout=5)
            result["at"] = time.monotonic()

        thread = threading.Thread(target=wait)
        thread.start()
        self._wait_until(lambda: changes._waiters == 1)
        changes.note_write("Raw Expenses")
        time.sleep(0.25)
        changes.note_write("Raw Expenses")
        time.sleep(0.25)
        last = time.monotonic()
        changes.note_write("Card Usage")
        thread.join(timeout=3)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result["mark"], changes.mark())
        self.assertTrue(str(result["mark"]).endswith(".3"))
        held = float(result["at"]) - last
        self.assertGreaterEqual(held, SETTLE_SECONDS - 0.25)
        self.assertLess(held, SETTLE_SECONDS + 0.75)

    def test_settle_cap_returns_during_a_continuous_burst(self):
        changes = watched()
        since = changes.mark()
        result: dict[str, float | str] = {}

        def wait() -> None:
            result["mark"] = changes.wait_past(since, timeout=8)
            result["at"] = time.monotonic()

        thread = threading.Thread(target=wait)
        thread.start()
        self._wait_until(lambda: changes._waiters == 1)
        started = time.monotonic()
        while thread.is_alive() and time.monotonic() - started < SETTLE_CAP_SECONDS + 1.5:
            changes.note_write("Raw Expenses")
            time.sleep(0.15)
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        elapsed = float(result["at"]) - started
        self.assertGreaterEqual(elapsed, SETTLE_CAP_SECONDS - 0.4)
        self.assertLess(elapsed, SETTLE_CAP_SECONDS + 1.0)
        self.assertNotEqual(result["mark"], since)

    def test_waiter_cap_returns_busy(self):
        changes = watched()
        since = changes.mark()
        threads = [
            threading.Thread(target=changes.wait_past, args=(since,), kwargs={"timeout": 3})
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        self._wait_until(lambda: changes._waiters == 8)

        self.assertIsNone(changes.wait_past(since, timeout=3))

        changes.note_write("Raw Expenses")
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())

    def _wait_until(self, ready) -> None:
        deadline = time.monotonic() + 2
        while not ready():
            if time.monotonic() > deadline:
                self.fail("timed out waiting for sheet-change waiters")
            time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
