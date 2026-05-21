from __future__ import annotations

import json
from typing import Any
from decimal import Decimal
from pathlib import Path

from getrichbot.models import ExpenseRecord, ExpenseRow

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    def __init__(self, sheet_id: str, service_account_file: Path | None = None, service_account_json: str | None = None):
        self.sheet_id = sheet_id
        self.service_account_file = service_account_file
        self.service_account_json = service_account_json
        self.service: Any | None = None

    def _service(self):
        if self.service is None:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build

            if self.service_account_json:
                info = json.loads(self.service_account_json)
                credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
            elif self.service_account_file:
                credentials = Credentials.from_service_account_file(self.service_account_file, scopes=SCOPES)
            else:
                raise RuntimeError("Google service account credentials are not configured.")
            self.service = build("sheets", "v4", credentials=credentials)
        return self.service

    def append_expense(self, sheet_name: str, row: ExpenseRow) -> None:
        self._service().spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A:M",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row.to_sheet_row()]},
        ).execute()

    def get_fixed_expenses(self, sheet_name: str) -> list[dict[str, str | Decimal]]:
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A2:D",
        ).execute()
        rows = result.get("values", [])
        expenses: list[dict[str, str | Decimal]] = []

        for row in rows:
            category = _cell(row, 0)
            amount = _cell(row, 1)
            active = _cell(row, 2).lower()
            notes = _cell(row, 3)
            if not category or active not in {"true", "yes", "y", "1"}:
                continue
            expenses.append(
                {
                    "category": category,
                    "amount": Decimal(amount.replace(",", "")),
                    "notes": notes,
                }
            )
        return expenses

    def delete_last_matching_row(self, sheet_name: str, logged_by: str) -> bool:
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A2:M",
        ).execute()
        rows = result.get("values", [])

        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            if _cell(row, 4) == logged_by:
                sheet_row_number = index + 2
                self._delete_sheet_row(sheet_name, sheet_row_number)
                return True
        return False

    def delete_entry_by_id(self, sheet_name: str, entry_id: str, logged_by: str | None = None) -> bool:
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A2:M",
        ).execute()
        rows = result.get("values", [])

        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            if _cell(row, 0).lower() != entry_id.lower():
                continue
            if logged_by is not None and _cell(row, 4) != logged_by:
                return False
            self._delete_sheet_row(sheet_name, index + 2)
            return True
        return False

    def get_expense_records(self, sheet_name: str) -> list[ExpenseRecord]:
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A2:M",
        ).execute()
        rows = result.get("values", [])
        records: list[ExpenseRecord] = []

        for index, row in enumerate(rows, start=2):
            entry_id = _cell(row, 0)
            if not entry_id:
                continue
            try:
                amount = Decimal(_cell(row, 6).replace(",", ""))
            except Exception:
                continue
            records.append(
                ExpenseRecord(
                    row_number=index,
                    entry_id=entry_id,
                    timestamp=_cell(row, 1),
                    expense_date=_cell(row, 2),
                    month=_cell(row, 3),
                    logged_by=_cell(row, 4),
                    raw_input=_cell(row, 5),
                    amount=amount,
                    category=_cell(row, 7),
                    description=_cell(row, 8),
                    status=_cell(row, 10),
                )
            )
        return records

    def update_expense_record(
        self,
        sheet_name: str,
        row_number: int,
        amount: Decimal | None = None,
        category: str | None = None,
        description: str | None = None,
        expense_date: str | None = None,
    ) -> None:
        updates = []
        if expense_date is not None:
            updates.extend(
                [
                    {"range": f"{sheet_name}!C{row_number}", "values": [[expense_date]]},
                    {"range": f"{sheet_name}!D{row_number}", "values": [[expense_date[:7]]]},
                ]
            )
        if amount is not None:
            updates.append({"range": f"{sheet_name}!G{row_number}", "values": [[f"{amount:.2f}"]]})
        if category is not None:
            updates.append({"range": f"{sheet_name}!H{row_number}", "values": [[category]]})
        if description is not None:
            updates.append({"range": f"{sheet_name}!I{row_number}", "values": [[description]]})
        if not updates:
            return

        self._service().spreadsheets().values().batchUpdate(
            spreadsheetId=self.sheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()

    def _delete_sheet_row(self, sheet_name: str, one_based_row_number: int) -> None:
        metadata = self._service().spreadsheets().get(spreadsheetId=self.sheet_id).execute()
        sheet_id = None
        for sheet in metadata.get("sheets", []):
            properties = sheet.get("properties", {})
            if properties.get("title") == sheet_name:
                sheet_id = properties["sheetId"]
                break
        if sheet_id is None:
            raise ValueError(f"Sheet tab not found: {sheet_name}")

        self._service().spreadsheets().batchUpdate(
            spreadsheetId=self.sheet_id,
            body={
                "requests": [
                    {
                        "deleteDimension": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "ROWS",
                                "startIndex": one_based_row_number - 1,
                                "endIndex": one_based_row_number,
                            }
                        }
                    }
                ]
            },
        ).execute()


def _cell(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return str(row[index]).strip()
