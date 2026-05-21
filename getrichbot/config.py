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
    service_account_file: Path
    me_telegram_ids: set[int]
    wife_telegram_ids: set[int]
    me_label: str
    wife_label: str
    raw_expenses_sheet: str
    fixed_expenses_sheet: str
    openai_api_key: str | None
    openai_model: str

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()
        token = os.environ["TELEGRAM_BOT_TOKEN"]
        sheet_id = os.environ["GOOGLE_SHEET_ID"]
        service_account_file = Path(os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]).expanduser()

        return cls(
            telegram_bot_token=token,
            google_sheet_id=sheet_id,
            service_account_file=service_account_file,
            me_telegram_ids=_ids_from_env("ME_TELEGRAM_IDS"),
            wife_telegram_ids=_ids_from_env("WIFE_TELEGRAM_IDS"),
            me_label=os.getenv("ME_LABEL", "Me"),
            wife_label=os.getenv("WIFE_LABEL", "My wife"),
            raw_expenses_sheet=os.getenv("RAW_EXPENSES_SHEET", "Raw Expenses"),
            fixed_expenses_sheet=os.getenv("FIXED_EXPENSES_SHEET", "Fixed Expenses"),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
        )

    def label_for_user(self, telegram_user_id: int) -> str | None:
        if telegram_user_id in self.me_telegram_ids:
            return self.me_label
        if telegram_user_id in self.wife_telegram_ids:
            return self.wife_label
        return None
