from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from getrichbot.models import ExpenseRow

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    def __init__(self, sheet_id: str, service_account_file: Path):
        credentials = Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
        self.sheet_id = sheet_id
        self.service = build("sheets", "v4", credentials=credentials)

    def append_expense(self, sheet_name: str, row: ExpenseRow) -> None:
        self.service.spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A:L",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row.to_sheet_row()]},
        ).execute()

    def get_fixed_expenses(self, sheet_name: str) -> list[dict[str, str | Decimal]]:
        result = self.service.spreadsheets().values().get(
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
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A2:L",
        ).execute()
        rows = result.get("values", [])

        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            if _cell(row, 3) == logged_by:
                sheet_row_number = index + 2
                self._delete_sheet_row(sheet_name, sheet_row_number)
                return True
        return False

    def _delete_sheet_row(self, sheet_name: str, one_based_row_number: int) -> None:
        metadata = self.service.spreadsheets().get(spreadsheetId=self.sheet_id).execute()
        sheet_id = None
        for sheet in metadata.get("sheets", []):
            properties = sheet.get("properties", {})
            if properties.get("title") == sheet_name:
                sheet_id = properties["sheetId"]
                break
        if sheet_id is None:
            raise ValueError(f"Sheet tab not found: {sheet_name}")

        self.service.spreadsheets().batchUpdate(
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
