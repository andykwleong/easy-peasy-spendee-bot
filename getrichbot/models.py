from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class ExpenseDraft:
    raw_input: str
    amount: Decimal
    category: str | None
    description: str
    confidence: float
    expense_date: date | None = None
    needs_date_confirmation: bool = False


@dataclass(frozen=True)
class ExpenseRow:
    timestamp: datetime
    logged_by: str
    raw_input: str
    amount: Decimal
    category: str
    description: str
    input_type: str
    status: str
    telegram_chat_id: int | str
    telegram_message_id: int | str

    def to_sheet_row(self) -> list[str]:
        expense_date = self.timestamp.strftime("%Y-%m-%d")
        month = self.timestamp.strftime("%Y-%m")
        return [
            self.timestamp.isoformat(timespec="seconds"),
            expense_date,
            month,
            self.logged_by,
            self.raw_input,
            f"{self.amount:.2f}",
            self.category,
            self.description,
            self.input_type,
            self.status,
            str(self.telegram_chat_id),
            str(self.telegram_message_id),
        ]
