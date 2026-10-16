"""In-memory mark that moves when a dashboard row is written. One bot process, so memory is enough."""

from __future__ import annotations

import secrets
import threading
import time

WAIT_SECONDS = 25.0
SETTLE_SECONDS = 1.0
SETTLE_CAP_SECONDS = 4.0
MAX_WAITERS = 8


class SheetChanges:
    """Opaque mark "<boot>.<count>". A new process gets a new boot part, so every open page refreshes once after a restart."""

    def __init__(self, watched_sheets: frozenset[str] | set[str]) -> None:
        self._watched = frozenset(watched_sheets)
        self._boot = secrets.token_hex(4)
        self._count = 0
        self._waiters = 0
        self._last_note = 0.0
        self._changed = threading.Condition()

    def mark(self) -> str:
        with self._changed:
            return self._mark_locked()

    def note_write(self, sheet_name: str) -> None:
        """Called after a write has returned. Unwatched sheets do not move the mark."""
        if sheet_name not in self._watched:
            return
        with self._changed:
            self._count += 1
            self._last_note = time.monotonic()
            self._changed.notify_all()

    def wait_past(self, since: str, *, timeout: float = WAIT_SECONDS) -> str | None:
        """Current mark once it differs from since, held SETTLE_SECONDS past the last write (capped).

        Returns the unchanged mark at timeout. Returns None when MAX_WAITERS are already waiting.
        A mark that already differs is returned at once, so a restart or a finished burst does not sit through settle.
        """
        deadline = time.monotonic() + timeout
        with self._changed:
            if self._mark_locked() != since:
                return self._mark_locked()
            if self._waiters >= MAX_WAITERS:
                return None
            self._waiters += 1
            try:
                changed_at: float | None = None
                while True:
                    now = time.monotonic()
                    current = self._mark_locked()
                    if current != since:
                        if changed_at is None:
                            changed_at = now
                        # A burst that never pauses must still let the page read.
                        settle_until = self._last_note + SETTLE_SECONDS
                        cap_until = changed_at + SETTLE_CAP_SECONDS
                        if now >= settle_until or now >= cap_until or now >= deadline:
                            return current
                        self._changed.wait(min(settle_until, cap_until, deadline) - now)
                        continue
                    if now >= deadline:
                        return current
                    self._changed.wait(deadline - now)
            finally:
                self._waiters -= 1

    def _mark_locked(self) -> str:
        return f"{self._boot}.{self._count}"
