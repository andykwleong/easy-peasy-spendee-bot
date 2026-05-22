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
    entry_id: str
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
            self.entry_id,
            self.timestamp.strftime("%H:%M:%S"),
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


@dataclass(frozen=True)
class ExpenseRecord:
    row_number: int
    entry_id: str
    timestamp: str
    expense_date: str
    month: str
    logged_by: str
    raw_input: str
    amount: Decimal
    category: str
    description: str
    input_type: str
    status: str

    def compact(self) -> str:
        return (
            f"{self.entry_id} | {self.expense_date} | {self.logged_by} | "
            f"${self.amount:.2f} | {self.category} | {self.description} | raw: {self.raw_input}"
        )
