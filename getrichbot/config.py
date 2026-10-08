from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _ids_from_env(name: str) -> set[int]:
    raw = os.getenv(name, "")
    ids: set[int] = set()
    for value in raw.split(","):
        value = value.strip()
        if value:
            ids.add(int(value))
    return ids


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    google_sheet_id: str
    service_account_file: Path | None
    service_account_json: str | None
    me_telegram_ids: set[int]
    wife_telegram_ids: set[int]
    me_label: str
    wife_label: str
    me_card_button_label: str
    wife_card_button_label: str
    raw_expenses_sheet: str
    fixed_expenses_sheet: str
    monthly_summary_sheet: str
    bot_state_sheet: str
    categories_sheet: str
    category_keywords_sheet: str
    payment_methods_sheet: str
    card_limits_sheet: str
    card_usage_sheet: str
    telegram_chat_id: int | None
    openai_api_key: str | None
    openai_model: str
    dashboard_public_url: str | None
    email_logging_enabled: bool
    gmail_oauth_client_id: str | None
    gmail_oauth_client_secret: str | None
    gmail_oauth_refresh_token: str | None
    gmail_pubsub_topic: str | None
    gmail_pubsub_audience: str | None
    gmail_pubsub_service_account: str | None
    me_forwarder_email: str | None
    wife_forwarder_email: str | None
    exa_api_key: str | None

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()
        token = os.environ["TELEGRAM_BOT_TOKEN"]
        sheet_id = os.environ["GOOGLE_SHEET_ID"]
        service_account_file_raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
        service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or None
        service_account_file = Path(service_account_file_raw).expanduser() if service_account_file_raw else None
        if service_account_file is None and service_account_json is None:
            raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_FILE for local use or GOOGLE_SERVICE_ACCOUNT_JSON for Railway.")

        return cls(
            telegram_bot_token=token,
            google_sheet_id=sheet_id,
            service_account_file=service_account_file,
            service_account_json=service_account_json,
            me_telegram_ids=_ids_from_env("ME_TELEGRAM_IDS"),
            wife_telegram_ids=_ids_from_env("WIFE_TELEGRAM_IDS"),
            me_label=os.getenv("ME_LABEL", "Me"),
            wife_label=os.getenv("WIFE_LABEL", "My wife"),
            me_card_button_label=os.getenv("ME_CARD_BUTTON_LABEL", "Partner's Cards"),
            wife_card_button_label=os.getenv("WIFE_CARD_BUTTON_LABEL", "Partner's Cards"),
            raw_expenses_sheet=os.getenv("RAW_EXPENSES_SHEET", "Raw Expenses"),
            fixed_expenses_sheet=os.getenv("FIXED_EXPENSES_SHEET", "Fixed Expenses"),
            monthly_summary_sheet=os.getenv("MONTHLY_SUMMARY_SHEET", "Monthly Summary"),
            bot_state_sheet=os.getenv("BOT_STATE_SHEET", "Bot State"),
            categories_sheet=os.getenv("CATEGORIES_SHEET", "Categories"),
            category_keywords_sheet=os.getenv("CATEGORY_KEYWORDS_SHEET", "Category Keywords"),
            payment_methods_sheet=os.getenv("PAYMENT_METHODS_SHEET", "Payment Methods"),
            card_limits_sheet=os.getenv("CARD_LIMITS_SHEET", "Card Limits"),
            card_usage_sheet=os.getenv("CARD_USAGE_SHEET", "Card Usage"),
            telegram_chat_id=_optional_int("TELEGRAM_CHAT_ID"),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
            dashboard_public_url=_optional_text("DASHBOARD_PUBLIC_URL"),
            email_logging_enabled=_optional_bool("EMAIL_LOGGING_ENABLED"),
            gmail_oauth_client_id=_optional_text("GMAIL_OAUTH_CLIENT_ID"),
            gmail_oauth_client_secret=_optional_text("GMAIL_OAUTH_CLIENT_SECRET"),
            gmail_oauth_refresh_token=_optional_text("GMAIL_OAUTH_REFRESH_TOKEN"),
            gmail_pubsub_topic=_optional_text("GMAIL_PUBSUB_TOPIC"),
            gmail_pubsub_audience=_optional_text("GMAIL_PUBSUB_AUDIENCE"),
            gmail_pubsub_service_account=_optional_text("GMAIL_PUBSUB_SERVICE_ACCOUNT"),
            me_forwarder_email=_optional_text("ME_FORWARDER_EMAIL"),
            wife_forwarder_email=_optional_text("WIFE_FORWARDER_EMAIL"),
            exa_api_key=_optional_text("EXA_API_KEY"),
        )

    def label_for_user(self, telegram_user_id: int) -> str | None:
        if telegram_user_id in self.me_telegram_ids:
            return self.me_label
        if telegram_user_id in self.wife_telegram_ids:
            return self.wife_label
        return None


def _optional_bool(name: str) -> bool:
    raw = os.getenv(name, "").strip().casefold()
    return raw in {"1", "true", "yes", "y", "on"}


def _optional_text(name: str) -> str | None:
    raw = os.getenv(name, "").strip()
    return raw or None


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return int(raw)
