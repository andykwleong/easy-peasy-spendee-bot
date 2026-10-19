from __future__ import annotations

import json
import logging
import socket
import threading
from typing import Any
from decimal import Decimal, InvalidOperation
from pathlib import Path

from getrichbot.models import CardUsageRecord, CardUsageRow, ExpenseRecord, ExpenseRow, is_paynow_method
from getrichbot.cards import PaymentConfig, parse_payment_config
from getrichbot.sheet_changes import SheetChanges

LOGGER = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
# A category read at startup must finish or fail. An open socket with no
# deadline can sit forever, and the process then never answers the page.
CATEGORY_SHEET_READ_TIMEOUT_SECONDS = 20


def authorized_sheets_http(credentials: Any, timeout_seconds: float):
    """A Sheets connection that stops when the deadline passes."""
    if timeout_seconds <= 0:
        raise ValueError("Sheet read timeout must be greater than zero.")
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp

    return AuthorizedHttp(credentials, http=httplib2.Http(timeout=timeout_seconds))


def build_authorized_sheets(credentials: Any, timeout_seconds: float | None):
    from googleapiclient.discovery import build

    if timeout_seconds is None:
        return build("sheets", "v4", credentials=credentials)
    return build("sheets", "v4", http=authorized_sheets_http(credentials, timeout_seconds))


def _run_sheet_request(request: Any, timeout_seconds: float | None) -> Any:
    try:
        return request.execute()
    except (TimeoutError, socket.timeout) as exc:
        if timeout_seconds is None:
            raise
        raise TimeoutError(f"Google Sheet read exceeded {timeout_seconds:g} seconds.") from exc


class SheetsClient:
    def __init__(
        self,
        sheet_id: str,
        service_account_file: Path | None = None,
        service_account_json: str | None = None,
        changes: SheetChanges | None = None,
    ):
        self.sheet_id = sheet_id
        self.service_account_file = service_account_file
        self.service_account_json = service_account_json
        self.changes = changes
        self.service: Any | None = None
        # httplib2 is not safe on a shared connection. Each thread builds one
        # Sheets client the first time it needs the sheet, and keeps that client
        # until the thread ends. There is no timer and no command to drop it.
        # Restarting the bot clears them. Web requests end with their thread.
        self._thread_clients = threading.local()
        # Tab ids and Bot State rows stay after a successful read. A Bot State
        # write drops that tab's rows. Creating a tab reloads the tab ids.
        # There is no timer. Restarting the bot clears both. The lock covers
        # only those dict updates. It is not held during a sheet call.
        self._cache_lock = threading.Lock()
        self._tab_ids: dict[str, int] = {}
        self._state_rows: dict[str, list[list[str]]] = {}

    def _note_sheet_write(self, sheet_name: str) -> None:
        changes = self.changes
        if changes is None:
            return
        try:
            changes.note_write(sheet_name)
        except Exception:
            # The cells are already saved. A mark failure must not look like the save failed.
            LOGGER.exception("Dashboard change mark was not moved. The sheet write already finished.")

    def _credentials(self):
        from google.oauth2.service_account import Credentials

        if self.service_account_json:
            info = json.loads(self.service_account_json)
            return Credentials.from_service_account_info(info, scopes=SCOPES)
        if self.service_account_file:
            return Credentials.from_service_account_file(self.service_account_file, scopes=SCOPES)
        raise RuntimeError("Google service account credentials are not configured.")

    def _service(self, timeout_seconds: float | None = None):
        if timeout_seconds is not None and (self.service_account_json or self.service_account_file):
            return build_authorized_sheets(self._credentials(), timeout_seconds)
        # Tests inject one stand-in client. A real client is one per thread.
        if self.service is not None:
            return self.service
        client = getattr(self._thread_clients, "service", None)
        if client is None:
            client = build_authorized_sheets(self._credentials(), None)
            self._thread_clients.service = client
        return client

    def append_expense(self, sheet_name: str, row: ExpenseRow) -> None:
        include_payment_owner = self.has_payment_owner_column(sheet_name)
        include_payment_channel = self.has_payment_channel_column(sheet_name)
        self._service().spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A:Q",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row.to_sheet_row(include_payment_owner=include_payment_owner, include_payment_channel=include_payment_channel)]},
        ).execute()
        self._note_sheet_write(sheet_name)

    def append_card_usage(self, sheet_name: str, row: CardUsageRow) -> None:
        self._ensure_sheet(sheet_name)
        include_payment_owner = self.has_payment_owner_column(sheet_name)
        include_payment_channel = self.has_payment_channel_column(sheet_name)
        self._service().spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A:O",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row.to_sheet_row(include_payment_owner=include_payment_owner, include_payment_channel=include_payment_channel)]},
        ).execute()
        self._note_sheet_write(sheet_name)

    def get_payment_config(self, payment_methods_sheet: str, card_limits_sheet: str) -> PaymentConfig:
        methods_source, method_rows = self._get_values_first_available(
            [payment_methods_sheet, "Payment Methods", "payment methods"],
            "A:Z",
        )
        limits_source, limit_rows = self._get_values_first_available(
            [card_limits_sheet, "Card Limits", "card limits"],
            "A:Z",
        )
        if methods_source is None:
            raise ValueError(f"Sheet tab not found: {payment_methods_sheet}")
        if limits_source is None:
            raise ValueError(f"Sheet tab not found: {card_limits_sheet}")
        return parse_payment_config(method_rows, limit_rows)

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

    def get_category_config(
        self,
        categories_sheet: str,
        keywords_sheet: str,
        *,
        timeout_seconds: float = CATEGORY_SHEET_READ_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        if timeout_seconds <= 0:
            raise ValueError("Category sheet read timeout must be greater than zero.")
        category_source, category_rows = self._get_values_first_available(
            [categories_sheet, "Categories", "categories"],
            "A2:D",
            timeout_seconds=timeout_seconds,
        )
        keyword_source, keyword_rows = self._get_values_first_available(
            [keywords_sheet, "Category Keywords", "Category Keyword", "Categories Keyword", "categories keyword"],
            "A2:D",
            timeout_seconds=timeout_seconds,
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

    def upsert_category_keyword(self, sheet_name: str, keyword: str, category: str) -> None:
        cleaned = " ".join(keyword.strip().split()).casefold()
        chosen = category.strip()
        if not cleaned or not chosen:
            raise ValueError("A shop keyword needs a shop and an existing category.")
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A2:D",
        ).execute()
        rows = result.get("values", [])
        actions = keyword_upsert_actions(rows, cleaned, chosen)
        updates = []
        append_rows = []
        for action, row_number, values in actions:
            if action == "update":
                updates.append({"range": f"{sheet_name}!B{row_number}:C{row_number}", "values": [values]})
            else:
                append_rows.append(values)
        self._batch_update_cells(sheet_name, updates)
        if append_rows:
            self._service().spreadsheets().values().append(
                spreadsheetId=self.sheet_id,
                range=f"{sheet_name}!A:D",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": append_rows},
            ).execute()

    def deactivate_category_keyword(self, sheet_name: str, keyword: str) -> None:
        cleaned = " ".join(keyword.strip().split()).casefold()
        if not cleaned:
            return
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A2:D",
        ).execute()
        rows = result.get("values", [])
        updates = [
            {"range": f"{sheet_name}!D{row_number}", "values": [["FALSE"]]}
            for row_number in keyword_deactivate_actions(rows, cleaned)
        ]
        self._batch_update_cells(sheet_name, updates)

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
            range=f"{sheet_name}!A2:Q",
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
            range=f"{sheet_name}!A2:Q",
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

    def get_expense_records(self, sheet_name: str, *, timeout_seconds: float | None = None) -> list[ExpenseRecord]:
        result = _run_sheet_request(
            self._service(timeout_seconds).spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=f"{sheet_name}!A2:Q",
            ),
            timeout_seconds,
        )
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
                    payment_method=_record_payment_method(row),
                    payment_owner=_record_payment_owner(row),
                    payment_channel=_record_payment_channel(row),
                    telegram_message_id=_record_message_id(row),
                )
            )
        return records

    def get_card_usage_records(self, sheet_name: str) -> list[CardUsageRecord]:
        source, rows = self._get_values_first_available([sheet_name], "A2:O")
        if source is None:
            return []
        records: list[CardUsageRecord] = []
        for index, row in enumerate(rows, start=2):
            entry_id = _cell(row, 0)
            if not entry_id:
                continue
            amount = _parse_sheet_amount(_cell(row, 6))
            if amount is None:
                continue
            payment_method, payment_owner, payment_channel, description, usage_type, status = _card_usage_fields(row)
            records.append(
                CardUsageRecord(
                    row_number=index,
                    entry_id=entry_id,
                    timestamp=_cell(row, 1),
                    usage_date=_cell(row, 2),
                    month=_cell(row, 3),
                    logged_by=_cell(row, 4),
                    raw_input=_cell(row, 5),
                    amount=amount,
                    payment_method=payment_method,
                    description=description,
                    usage_type=usage_type,
                    status=status,
                    payment_owner=payment_owner,
                    payment_channel=payment_channel,
                )
            )
        return records

    def update_monthly_summary(
        self,
        sheet_name: str,
        rows: list[list[str]],
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        sheet_id = self._ensure_sheet(sheet_name)
        service = self._service(timeout_seconds)
        _run_sheet_request(
            service.spreadsheets().values().clear(
                spreadsheetId=self.sheet_id,
                range=f"{sheet_name}!A:ZZ",
                body={},
            ),
            timeout_seconds,
        )
        _run_sheet_request(
            service.spreadsheets().values().update(
                spreadsheetId=self.sheet_id,
                range=f"{sheet_name}!A1",
                valueInputOption="USER_ENTERED",
                body={"values": rows},
            ),
            timeout_seconds,
        )
        _run_sheet_request(
            service.spreadsheets().batchUpdate(
                spreadsheetId=self.sheet_id,
                body={
                    "requests": [
                        {
                            "repeatCell": {
                                "range": {
                                    "sheetId": sheet_id,
                                    "startRowIndex": 1,
                                    "startColumnIndex": 1,
                                },
                                "cell": {
                                    "userEnteredFormat": {
                                        "numberFormat": {
                                            "type": "CURRENCY",
                                            "pattern": "$#,##0.00",
                                        }
                                    }
                                },
                                "fields": "userEnteredFormat.numberFormat",
                            }
                        }
                    ]
                },
            ),
            timeout_seconds,
        )

    def get_state_value(self, sheet_name: str, key: str) -> str | None:
        for row in self._state_snapshot(sheet_name):
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
                with self._cache_lock:
                    self._state_rows.pop(sheet_name, None)
                return
        self._service().spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A:B",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [[key, value]]},
        ).execute()
        with self._cache_lock:
            self._state_rows.pop(sheet_name, None)

    def _state_snapshot(self, sheet_name: str) -> list[list[str]]:
        with self._cache_lock:
            cached = self._state_rows.get(sheet_name)
        if cached is not None:
            return cached
        self._ensure_sheet(sheet_name)
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A:B",
        ).execute()
        rows = [list(row) for row in result.get("values", [])]
        with self._cache_lock:
            self._state_rows[sheet_name] = rows
        return rows

    def update_expense_record(
        self,
        sheet_name: str,
        row_number: int,
        amount: Decimal | None = None,
        category: str | None = None,
        description: str | None = None,
        expense_date: str | None = None,
        transaction_type: str | None = None,
        payment_method: str | None = None,
        payment_owner: str | None = None,
        payment_channel: str | None = None,
        telegram_message_id: str | None = None,
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
        if payment_method is not None or payment_owner is not None or payment_channel is not None:
            owner_column, method_column, channel_column = self._expense_payment_columns(sheet_name)
            if payment_owner is not None and owner_column is not None:
                updates.append({"range": f"{sheet_name}!{owner_column}{row_number}", "values": [[payment_owner]]})
            if payment_method is not None:
                updates.append({"range": f"{sheet_name}!{method_column}{row_number}", "values": [[payment_method]]})
            if payment_channel is not None and channel_column is not None:
                updates.append({"range": f"{sheet_name}!{channel_column}{row_number}", "values": [[payment_channel]]})
        if transaction_type is not None:
            if self.has_payment_channel_column(sheet_name):
                transaction_column = "M"
            else:
                transaction_column = "L" if self.has_payment_owner_column(sheet_name) else "K"
            updates.append({"range": f"{sheet_name}!{transaction_column}{row_number}", "values": [[transaction_type]]})
        if telegram_message_id is not None:
            message_column = expense_message_column(
                self.has_payment_owner_column(sheet_name),
                self.has_payment_channel_column(sheet_name),
            )
            updates.append(
                {"range": f"{sheet_name}!{message_column}{row_number}", "values": [[telegram_message_id]]}
            )
        self._batch_update_cells(sheet_name, updates)

    def update_card_usage_record(
        self,
        sheet_name: str,
        row_number: int,
        amount: Decimal | None = None,
        payment_method: str | None = None,
        payment_owner: str | None = None,
        payment_channel: str | None = None,
    ) -> None:
        updates = []
        if amount is not None:
            updates.append({"range": f"{sheet_name}!G{row_number}", "values": [[f"{amount:.2f}"]]})
        if payment_method is not None or payment_owner is not None or payment_channel is not None:
            owner_column, method_column, channel_column = self._card_usage_payment_columns(sheet_name)
            if payment_owner is not None and owner_column is not None:
                updates.append({"range": f"{sheet_name}!{owner_column}{row_number}", "values": [[payment_owner]]})
            if payment_method is not None:
                updates.append({"range": f"{sheet_name}!{method_column}{row_number}", "values": [[payment_method]]})
            if payment_channel is not None and channel_column is not None:
                updates.append({"range": f"{sheet_name}!{channel_column}{row_number}", "values": [[payment_channel]]})
        self._batch_update_cells(sheet_name, updates)

    def _batch_update_cells(self, sheet_name: str, updates: list[dict]) -> None:
        if not updates:
            return
        self._service().spreadsheets().values().batchUpdate(
            spreadsheetId=self.sheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()
        self._note_sheet_write(sheet_name)

    def _expense_payment_columns(self, sheet_name: str) -> tuple[str | None, str, str | None]:
        has_owner = self.has_payment_owner_column(sheet_name)
        has_channel = self.has_payment_channel_column(sheet_name)
        if has_owner and has_channel:
            return "J", "K", "L"
        if has_owner:
            return "J", "K", None
        return None, "J", None

    def _card_usage_payment_columns(self, sheet_name: str) -> tuple[str | None, str, str | None]:
        has_owner = self.has_payment_owner_column(sheet_name)
        has_channel = self.has_payment_channel_column(sheet_name)
        if has_owner and has_channel:
            return "H", "I", "J"
        if has_owner:
            return "H", "I", None
        return None, "H", None

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
        self._note_sheet_write(sheet_name)

    def has_payment_owner_column(self, sheet_name: str) -> bool:
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A1:Q1",
        ).execute()
        rows = result.get("values", [])
        if not rows:
            return False
        return any(_normalize_header(value) == "payment owner" for value in rows[0])

    def has_payment_channel_column(self, sheet_name: str) -> bool:
        result = self._service().spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A1:Q1",
        ).execute()
        rows = result.get("values", [])
        if not rows:
            return False
        return any(_normalize_header(value) == "payment channel" for value in rows[0])

    def _ensure_sheet(self, sheet_name: str) -> int:
        sheet_id = self._sheet_id(sheet_name)
        if sheet_id is not None:
            return sheet_id
        self._service().spreadsheets().batchUpdate(
            spreadsheetId=self.sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
        ).execute()
        with self._cache_lock:
            self._tab_ids.clear()
        sheet_id = self._sheet_id(sheet_name)
        if sheet_id is None:
            raise RuntimeError(f"Could not create sheet tab: {sheet_name}")
        return sheet_id

    def _sheet_id(self, sheet_name: str) -> int | None:
        with self._cache_lock:
            if sheet_name in self._tab_ids:
                return self._tab_ids[sheet_name]
        self._load_sheet_ids()
        with self._cache_lock:
            return self._tab_ids.get(sheet_name)

    def _load_sheet_ids(self) -> None:
        metadata = self._service().spreadsheets().get(spreadsheetId=self.sheet_id).execute()
        found: dict[str, int] = {}
        for sheet in metadata.get("sheets", []):
            properties = sheet.get("properties", {})
            title = properties.get("title")
            sheet_id = properties.get("sheetId")
            if title is None or sheet_id is None:
                continue
            found[str(title)] = sheet_id
        with self._cache_lock:
            self._tab_ids.update(found)

    def _get_values_first_available(
        self,
        sheet_names: list[str],
        cell_range: str,
        timeout_seconds: float | None = None,
    ) -> tuple[str | None, list[list[str]]]:
        seen = []
        for sheet_name in sheet_names:
            if not sheet_name or sheet_name in seen:
                continue
            seen.append(sheet_name)
            request = self._service(timeout_seconds).spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=f"{sheet_name}!{cell_range}",
            )
            try:
                result = _run_sheet_request(request, timeout_seconds)
            except TimeoutError:
                raise
            except Exception:
                continue
            return sheet_name, result.get("values", [])
        return None, []


def expense_message_column(has_owner: bool, has_channel: bool) -> str:
    if has_channel:
        return "Q"
    if has_owner:
        return "P"
    return "O"


def keyword_deactivate_actions(rows: list[list[str]], keyword: str) -> list[int]:
    """Sheet rows whose Active cell should be turned off for this shop."""
    cleaned = " ".join(keyword.strip().split()).casefold()
    if not cleaned:
        return []
    row_numbers = []
    for index, row in enumerate(rows):
        if _cell(row, 0).casefold() != cleaned:
            continue
        if _cell(row, 3).casefold() in {"false", "no", "n", "0"}:
            continue
        row_numbers.append(index + 2)
    return row_numbers


def keyword_upsert_actions(
    rows: list[list[str]],
    keyword: str,
    category: str,
) -> list[tuple[str, int, list[str]]]:
    cleaned = " ".join(keyword.strip().split()).casefold()
    matches = [
        index + 2
        for index, row in enumerate(rows)
        if _cell(row, 0).casefold() == cleaned
    ]
    if not matches:
        return [("append", 0, [cleaned, category, "Priority", "TRUE"])]
    return [("update", row_number, [category, "Priority"]) for row_number in matches]


def _cell(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return str(row[index]).strip()


def _record_type_fields(row: list[str]) -> tuple[str, str, str]:
    payment_channel_status = _cell(row, 14)
    if payment_channel_status.casefold() in {"confirmed", "pending", "cancelled", "canceled"}:
        transaction_type = _cell(row, 12)
        input_type = _cell(row, 13)
        status = payment_channel_status
        if not transaction_type:
            transaction_type = _infer_transaction_type(_cell(row, 7), input_type)
        return transaction_type, input_type, status

    payment_owner_status = _cell(row, 13)
    if payment_owner_status.casefold() in {"confirmed", "pending", "cancelled", "canceled"}:
        transaction_type = _cell(row, 11)
        input_type = _cell(row, 12)
        status = payment_owner_status
    else:
        new_status = _cell(row, 12)
    if payment_owner_status.casefold() not in {"confirmed", "pending", "cancelled", "canceled"} and new_status.casefold() in {"confirmed", "pending", "cancelled", "canceled"}:
        transaction_type = _cell(row, 10)
        input_type = _cell(row, 11)
        status = new_status
    elif payment_owner_status.casefold() not in {"confirmed", "pending", "cancelled", "canceled"}:
        transaction_type = ""
        # Rows created before Payment Method used the 14-column layout:
        # transaction type I, input type J, status K.
        input_type = _cell(row, 10)
        status = _cell(row, 11)
    if not transaction_type:
        transaction_type = _infer_transaction_type(_cell(row, 7), input_type)
    return transaction_type, input_type, status


def _record_payment_method(row: list[str]) -> str:
    statuses = {"confirmed", "pending", "cancelled", "canceled"}
    if _cell(row, 14).casefold() in statuses:
        return _cell(row, 10)
    if _cell(row, 13).casefold() in statuses:
        return _cell(row, 10)
    if _cell(row, 12).casefold() in statuses:
        return _cell(row, 9)
    return ""


def _record_payment_owner(row: list[str]) -> str:
    statuses = {"confirmed", "pending", "cancelled", "canceled"}
    method = _record_payment_method(row)
    if _cell(row, 14).casefold() in statuses:
        return _owner_cell_or_logger(row, 9, method)
    if _cell(row, 13).casefold() in statuses:
        return _owner_cell_or_logger(row, 9, method)
    if _cell(row, 12).casefold() in statuses and _cell(row, 9):
        if is_paynow_method(method):
            return ""
        return _cell(row, 4)
    return ""


def _owner_cell_or_logger(row: list[str], owner_index: int, method: str) -> str:
    owner = _cell(row, owner_index)
    if owner or is_paynow_method(method):
        return owner
    return _cell(row, 4)


def _record_message_id(row: list[str]) -> str:
    statuses = {"confirmed", "pending", "cancelled", "canceled"}
    for index in (14, 13, 12, 11):
        if _cell(row, index).casefold() in statuses:
            return _cell(row, index + 2)
    return ""


def _record_payment_channel(row: list[str]) -> str:
    statuses = {"confirmed", "pending", "cancelled", "canceled"}
    if _cell(row, 14).casefold() in statuses:
        return _cell(row, 11)
    return ""


def _card_usage_fields(row: list[str]) -> tuple[str, str, str, str, str, str]:
    statuses = {"confirmed", "pending", "cancelled", "canceled"}
    if _cell(row, 12).casefold() in statuses:
        return _cell(row, 8), _cell(row, 7) or _cell(row, 4), _cell(row, 9), _cell(row, 10), _cell(row, 11), _cell(row, 12)
    if _cell(row, 11).casefold() in statuses:
        return _cell(row, 8), _cell(row, 7) or _cell(row, 4), "", _cell(row, 9), _cell(row, 10), _cell(row, 11)
    return _cell(row, 7), _cell(row, 4), "", _cell(row, 8), _cell(row, 9), _cell(row, 10)


def _normalize_header(value: str) -> str:
    return " ".join(str(value).strip().casefold().split())


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
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    return amount
