from __future__ import annotations

import json
from typing import Any
from decimal import Decimal, InvalidOperation
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
            range=f"{sheet_name}!A:N",
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
            parsed_amount = _parse_sheet_amount(amount)
            if parsed_amount is None:
                raise ValueError(f"Could not read fixed expense amount for {category}: {amount}")
            expenses.append(
                {
                    "category": category,
                    "amount": parsed_amount,
                    "notes": notes,
                }
            )
        return expenses

    def get_category_config(self, categories_sheet: str, keywords_sheet: str) -> dict[str, Any]:
        category_source, category_rows = self._get_values_first_available(
            [categories_sheet, "Categories", "categories"],
            "A2:D",
        )
        keyword_source, keyword_rows = self._get_values_first_available(
            [keywords_sheet, "Category Keywords", "Category Keyword", "Categories Keyword", "categories keyword"],
            "A2:D",
        )

        variable_categories: list[str] = []
        fixed_categories: list[str] = []
        shopping_categories: dict[str, str] = {}

        for row in category_rows:
            category = _cell(row, 0)
            category_type = _cell(row, 1).lower()
            active = _cell(row, 2).lower()
            shopping_owner = _cell(row, 3).lower()
            if not category or active not in {"true", "yes", "y", "1"}:
                continue
            if category_type == "fixed":
                fixed_categories.append(category)
            else:
                variable_categories.append(category)
            if shopping_owner:
                shopping_categories[shopping_owner] = category

        category_keywords: dict[str, list[str]] = {}
        priority_keywords: list[dict[str, Any]] = []
        category_aliases: dict[str, str] = {}
        shopping_keywords: list[str] = []

        for row in keyword_rows:
            keyword = _cell(row, 0).lower()
            category = _cell(row, 1)
            priority = _cell(row, 2).lower()
            active = _cell(row, 3).lower()
            if not keyword or not category or active not in {"true", "yes", "y", "1"}:
                continue
            if category.lower() == "shopping - sender":
                shopping_keywords.append(keyword)
                continue
            category_keywords.setdefault(category, []).append(keyword)
            category_aliases[keyword] = category
            if priority == "priority":
                priority_keywords.append({"category": category, "keywords": [keyword]})

        return {
            "source": "google_sheets" if category_rows else "empty",
            "categories_sheet_loaded": category_source,
            "keywords_sheet_loaded": keyword_source,
            "variable_categories": variable_categories,
            "fixed_categories": fixed_categories,
            "category_keywords": category_keywords,
            "priority_keywords": priority_keywords,
            "shopping_keywords": shopping_keywords,
            "shopping_categories": shopping_categories,
            "category_aliases": category_aliases,
        }

    def delete_last_matching_row(self, sheet_name: str, logged_by: str) -> bool:
        record = self.get_last_matching_record(sheet_name, logged_by)
        if record is None:
            return False
        self._delete_sheet_row(sheet_name, record.row_number)
        return True

    def get_last_matching_record(self, sheet_name: str, logged_by: str) -> ExpenseRecord | None:
        records = self.get_expense_records(sheet_name)
        for record in reversed(records):
            if record.logged_by == logged_by:
                return record
        return None

    def get_record_by_id(self, sheet_name: str, entry_id: str, logged_by: str | None = None) -> ExpenseRecord | None:
        records = self.get_expense_records(sheet_name)
        for record in reversed(records):
            if record.entry_id.lower() != entry_id.lower():
                continue
            if logged_by is not None and record.logged_by != logged_by:
                return None
            return record
        return None

    def delete_entry_by_id(self, sheet_name: str, entry_id: str, logged_by: str | None = None) -> bool:
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A2:N",
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

    def delete_fixed_expenses_for_month(self, sheet_name: str, month: str) -> int:
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A2:N",
        ).execute()
        rows = result.get("values", [])
        deleted_count = 0

        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            transaction_type, input_type, status = _record_type_fields(row)
            if _cell(row, 3) != month:
                continue
            if status.lower() != "confirmed":
                continue
            if transaction_type.lower() != "fixed" and input_type.lower() != "fixed":
                continue
            self._delete_sheet_row(sheet_name, index + 2)
            deleted_count += 1
        return deleted_count

    def get_expense_records(self, sheet_name: str) -> list[ExpenseRecord]:
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A2:N",
        ).execute()
        rows = result.get("values", [])
        records: list[ExpenseRecord] = []

        for index, row in enumerate(rows, start=2):
            entry_id = _cell(row, 0)
            if not entry_id:
                continue
            amount = _parse_sheet_amount(_cell(row, 6))
            if amount is None:
                continue
            transaction_type, input_type, status = _record_type_fields(row)
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
                    input_type=input_type,
                    status=status,
                    transaction_type=transaction_type,
                )
            )
        return records

    def update_monthly_summary(self, sheet_name: str, rows: list[list[str]]) -> None:
        self._ensure_sheet(sheet_name)
        self._service().spreadsheets().values().clear(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A:ZZ",
            body={},
        ).execute()
        self._service().spreadsheets().values().update(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ).execute()

    def get_state_value(self, sheet_name: str, key: str) -> str | None:
        self._ensure_sheet(sheet_name)
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A:B",
        ).execute()
        for row in result.get("values", []):
            if _cell(row, 0) == key:
                return _cell(row, 1)
        return None

    def set_state_value(self, sheet_name: str, key: str, value: str) -> None:
        self._ensure_sheet(sheet_name)
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A:B",
        ).execute()
        rows = result.get("values", [])
        for index, row in enumerate(rows, start=1):
            if _cell(row, 0) == key:
                self._service().spreadsheets().values().update(
                    spreadsheetId=self.sheet_id,
                    range=f"{sheet_name}!B{index}",
                    valueInputOption="USER_ENTERED",
                    body={"values": [[value]]},
                ).execute()
                return
        self._service().spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A:B",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [[key, value]]},
        ).execute()

    def update_expense_record(
        self,
        sheet_name: str,
        row_number: int,
        amount: Decimal | None = None,
        category: str | None = None,
        description: str | None = None,
        expense_date: str | None = None,
        transaction_type: str | None = None,
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
        if transaction_type is not None:
            updates.append({"range": f"{sheet_name}!J{row_number}", "values": [[transaction_type]]})
        if not updates:
            return

        self._service().spreadsheets().values().batchUpdate(
            spreadsheetId=self.sheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()

    def _delete_sheet_row(self, sheet_name: str, one_based_row_number: int) -> None:
        sheet_id = self._sheet_id(sheet_name)
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

    def _ensure_sheet(self, sheet_name: str) -> None:
        if self._sheet_id(sheet_name) is not None:
            return
        self._service().spreadsheets().batchUpdate(
            spreadsheetId=self.sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
        ).execute()

    def _sheet_id(self, sheet_name: str) -> int | None:
        metadata = self._service().spreadsheets().get(spreadsheetId=self.sheet_id).execute()
        for sheet in metadata.get("sheets", []):
            properties = sheet.get("properties", {})
            if properties.get("title") == sheet_name:
                return properties["sheetId"]
        return None

    def _get_values_first_available(self, sheet_names: list[str], cell_range: str) -> tuple[str | None, list[list[str]]]:
        seen = []
        for sheet_name in sheet_names:
            if not sheet_name or sheet_name in seen:
                continue
            seen.append(sheet_name)
            try:
                result = self._service().spreadsheets().values().get(
                    spreadsheetId=self.sheet_id,
                    range=f"{sheet_name}!{cell_range}",
                ).execute()
            except Exception:
                continue
            return sheet_name, result.get("values", [])
        return None, []


def _cell(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return str(row[index]).strip()


def _record_type_fields(row: list[str]) -> tuple[str, str, str]:
    new_status = _cell(row, 11)
    if new_status:
        transaction_type = _cell(row, 9)
        input_type = _cell(row, 10)
        status = new_status
    else:
        transaction_type = ""
        input_type = _cell(row, 9)
        status = _cell(row, 10)
    if not transaction_type:
        transaction_type = _infer_transaction_type(_cell(row, 7), input_type)
    return transaction_type, input_type, status


def _infer_transaction_type(category: str, input_type: str) -> str:
    if input_type.lower() == "fixed":
        return "Fixed"
    if category.lower().startswith("income -"):
        return "Income"
    return "Expense"


def _parse_sheet_amount(raw: str) -> Decimal | None:
    cleaned = raw.strip().replace(",", "").replace("S$", "").replace("$", "")
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None
