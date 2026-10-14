"""Open the page and the Gmail tap, then load categories in the background."""

from __future__ import annotations

import logging
import threading
from time import sleep

from getrichbot.categories import FIXED_CATEGORIES
from getrichbot.categories import VARIABLE_CATEGORIES
from getrichbot.categories import configure_category_config
from getrichbot.sheets import CATEGORY_SHEET_READ_TIMEOUT_SECONDS
from getrichbot.sheets import SheetsClient

LOGGER = logging.getLogger(__name__)
CATEGORY_SHEET_RETRY_SECONDS = 15


def load_category_config_from_sheets(settings, sheets: SheetsClient) -> dict:
    sheet_category_config = sheets.get_category_config(settings.categories_sheet, settings.category_keywords_sheet)
    if not sheet_category_config.get("variable_categories"):
        raise RuntimeError(
            "No active categories found in Google Sheets. "
            f"Check sheet tabs '{settings.categories_sheet}' and '{settings.category_keywords_sheet}'."
        )
    return sheet_category_config


def load_category_config_until_ready(
    settings,
    sheets: SheetsClient,
    *,
    pause=sleep,
    retry_seconds: float = CATEGORY_SHEET_RETRY_SECONDS,
) -> dict:
    """Read the category sheet. A slow sheet retries. Placeholder categories are not loaded."""
    if retry_seconds <= 0:
        raise ValueError("Category sheet retry wait must be greater than zero.")
    while True:
        try:
            return load_category_config_from_sheets(settings, sheets)
        except TimeoutError:
            LOGGER.error(
                "Category sheet did not answer within %s seconds. The page stays up and the read will be tried again. Placeholder categories were not loaded.",
                CATEGORY_SHEET_READ_TIMEOUT_SECONDS,
            )
            pause(retry_seconds)


def start_page_and_mail(
    settings,
    sheets: SheetsClient,
    email_service,
    *,
    category_sheets: SheetsClient | None = None,
    pause=sleep,
    retry_seconds: float = CATEGORY_SHEET_RETRY_SECONDS,
):
    """Listen with the Gmail reader already attached, then read categories on another connection."""
    from getrichbot.dashboard_http import start_dashboard_server

    print("Dashboard page is starting.", flush=True)
    dashboard_server = start_dashboard_server(settings, sheets, gmail_push=email_service.handle_http)
    email_service.start_tap()
    reader = category_sheets
    if reader is None:
        reader = SheetsClient(
            settings.google_sheet_id,
            service_account_file=settings.service_account_file,
            service_account_json=settings.service_account_json,
        )

    def load_sheet_categories() -> None:
        try:
            sheet_category_config = load_category_config_until_ready(
                settings,
                reader,
                pause=pause,
                retry_seconds=retry_seconds,
            )
        except Exception:
            LOGGER.exception(
                "Category sheet did not load. Placeholder categories were not used. The page stays up."
            )
            return
        configure_category_config(sheet_category_config)
        LOGGER.info(
            "Loaded %d variable and %d fixed categories from Google Sheets tab %s. Keywords tab: %s.",
            len(VARIABLE_CATEGORIES),
            len(FIXED_CATEGORIES),
            sheet_category_config.get("categories_sheet_loaded"),
            sheet_category_config.get("keywords_sheet_loaded"),
        )

    threading.Thread(target=load_sheet_categories, name="category-sheet", daemon=True).start()
    return dashboard_server
