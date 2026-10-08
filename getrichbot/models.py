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
    transaction_type: str = "Expense"
    payment_method: str = ""
    payment_owner: str = ""
    payment_channel: str = ""

    def to_sheet_row(self, include_payment_owner: bool = True, include_payment_channel: bool = False) -> list[str]:
        expense_date = self.timestamp.strftime("%Y-%m-%d")
        month = self.timestamp.strftime("%Y-%m")
        values = [
            self.entry_id,
            self.timestamp.strftime("%H:%M:%S"),
            expense_date,
            month,
            self.logged_by,
            self.raw_input,
            f"{self.amount:.2f}",
            self.category,
            self.description,
        ]
        if include_payment_owner:
            values.append((self.payment_owner or self.logged_by) if self.payment_method else "")
        values.extend(
            [
                self.payment_method,
            ]
        )
        if include_payment_channel:
            values.append(self.payment_channel if self.payment_method else "")
        values.extend(
            [
                self.transaction_type,
                self.input_type,
                self.status,
                str(self.telegram_chat_id),
                str(self.telegram_message_id),
            ]
        )
        return values


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
    transaction_type: str = "Expense"
    payment_method: str = ""
    payment_owner: str = ""
    payment_channel: str = ""
    telegram_message_id: str = ""

    def compact(self) -> str:
        return (
            f"{self.entry_id} | {self.expense_date} | {self.logged_by} | "
            f"${self.amount:.2f} | {self.category} | {self.description} | raw: {self.raw_input}"
        )


@dataclass(frozen=True)
class CardUsageRow:
    entry_id: str
    timestamp: datetime
    logged_by: str
    raw_input: str
    amount: Decimal
    payment_method: str
    description: str
    usage_type: str
    status: str
    telegram_chat_id: int | str
    telegram_message_id: int | str
    payment_owner: str = ""
    payment_channel: str = ""

    def to_sheet_row(self, include_payment_owner: bool = True, include_payment_channel: bool = False) -> list[str]:
        usage_date = self.timestamp.strftime("%Y-%m-%d")
        month = self.timestamp.strftime("%Y-%m")
        values = [
            self.entry_id,
            self.timestamp.strftime("%H:%M:%S"),
            usage_date,
            month,
            self.logged_by,
            self.raw_input,
            f"{self.amount:.2f}",
        ]
        if include_payment_owner:
            values.append(self.payment_owner or self.logged_by)
        values.extend(
            [
                self.payment_method,
            ]
        )
        if include_payment_channel:
            values.append(self.payment_channel if self.payment_method else "")
        values.extend(
            [
                self.description,
                self.usage_type,
                self.status,
                str(self.telegram_chat_id),
                str(self.telegram_message_id),
            ]
        )
        return values


@dataclass(frozen=True)
class CardUsageRecord:
    row_number: int
    entry_id: str
    timestamp: str
    usage_date: str
    month: str
    logged_by: str
    raw_input: str
    amount: Decimal
    payment_method: str
    description: str
    usage_type: str
    status: str
    payment_owner: str = ""
    payment_channel: str = ""
