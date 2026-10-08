from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import Callable

from getrichbot.email_mail import EmailDecision
from getrichbot.email_mail import INCOME_MISC
from getrichbot.email_mail import SINGAPORE_TZ
from getrichbot.email_mail import build_expense_row
from getrichbot.categories import ALL_CATEGORIES
from getrichbot.shop_category import apply_shop_keyword
from getrichbot.shop_category import categorize_email_shop
from getrichbot.shop_lookup import lookup_shop_text
from getrichbot.ai import pick_existing_category
from getrichbot.email_mail import decide_mail
from getrichbot.gmail_api import GmailApiMailbox
from getrichbot.gmail_api import HistoryExpired
from getrichbot.gmail_api import verify_pubsub_token
from getrichbot.models import ExpenseRow

LOGGER = logging.getLogger(__name__)

HISTORY_KEY = "gmail_history_id"
PAUSED_KEY = "gmail_paused"
BACKLOG_KEY = "gmail_backlog_ids"
PENDING_CHANNEL_KEY = "gmail_pending_channel"
_DONE = {"logged", "ignored", "asked", "skipped"}
_DAY_SECONDS = 24 * 60 * 60
_BACKLOG_TEXT = (
    "There is mail I have not logged. "
    "Reply log email backlog to log it, or skip email backlog to leave it."
)


@dataclass(frozen=True)
class EmailSettings:
    enabled: bool
    client_id: str
    client_secret: str
    refresh_token: str
    topic: str
    audience: str
    push_service_account: str
    forwarders: dict[str, str]
    chat_id: int | str
    me_label: str
    wife_label: str

    @property
    def active(self) -> bool:
        return bool(self.enabled and self.client_id and self.client_secret and self.refresh_token and self.topic)

    @classmethod
    def from_bot_settings(cls, settings) -> "EmailSettings":
        audience = (getattr(settings, "gmail_pubsub_audience", None) or "").strip()
        public_url = (getattr(settings, "dashboard_public_url", None) or "").strip().rstrip("/")
        if not audience and public_url:
            audience = f"{public_url}/gmail/push"
        forwarders: dict[str, str] = {}
        me_email = (getattr(settings, "me_forwarder_email", None) or "").strip().casefold()
        wife_email = (getattr(settings, "wife_forwarder_email", None) or "").strip().casefold()
        if me_email:
            forwarders[me_email] = settings.me_label
        if wife_email:
            forwarders[wife_email] = settings.wife_label
        return cls(
            enabled=bool(getattr(settings, "email_logging_enabled", False)),
            client_id=(getattr(settings, "gmail_oauth_client_id", None) or "").strip(),
            client_secret=(getattr(settings, "gmail_oauth_client_secret", None) or "").strip(),
            refresh_token=(getattr(settings, "gmail_oauth_refresh_token", None) or "").strip(),
            topic=(getattr(settings, "gmail_pubsub_topic", None) or "").strip(),
            audience=audience,
            push_service_account=(getattr(settings, "gmail_pubsub_service_account", None) or "").strip(),
            forwarders=forwarders,
            chat_id=getattr(settings, "telegram_chat_id", None) or "email",
            me_label=settings.me_label,
            wife_label=settings.wife_label,
        )


@dataclass(frozen=True)
class WriteOutcome:
    logged: bool
    text: str


@dataclass(frozen=True)
class PushNote:
    history_id: str


class EmailService:
    def __init__(
        self,
        config: EmailSettings,
        mailbox,
        state,
        context,
        writer: Callable[[ExpenseRow], WriteOutcome],
        *,
        authorize: Callable[[dict[str, str]], bool] | None = None,
        renew_every_seconds: int = _DAY_SECONDS,
        now: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self.mailbox = mailbox
        self.state = state
        self.context = context
        self.writer = writer
        self._authorize = authorize
        self.renew_every_seconds = renew_every_seconds
        self._now = now or (lambda: datetime.now(SINGAPORE_TZ))
        self._notify = lambda _text: None
        self._lock = threading.RLock()

    def bind_notifier(self, loop, send) -> None:
        def notify(text: str) -> None:
            if not text:
                return
            asyncio.run_coroutine_threadsafe(send(text), loop)

        self._notify = notify

    def start(self) -> list[str]:
        with self._lock:
            return self._start()

    def _start(self) -> list[str]:
        if not self.config.active:
            LOGGER.info("Email logging is off. Gmail watch was not started.")
            return []
        if not self.config.audience:
            LOGGER.warning("The Gmail tap address is not set. Taps will be ignored until it is. The bot still starts.")
        if self._paused():
            LOGGER.info("Email logging is paused. Gmail watch was not started.")
            return self._pending_channel_reminders()
        if not self.state.get(HISTORY_KEY):
            try:
                history_id = self.mailbox.watch(self.config.topic)
            except Exception:
                LOGGER.exception("Gmail watch did not start. Telegram still runs.")
                return ["Email logging is turned on, but I could not start the Gmail tap. The chat still works."]
            if history_id:
                self.state.set(HISTORY_KEY, history_id)
            try:
                recent = [message_id for message_id in self.mailbox.recent_ids() if _valid_id(message_id)]
            except Exception:
                LOGGER.exception("Could not see whether mail was already waiting.")
                recent = []
            messages = self._pending_channel_reminders()
            fresh = [message_id for message_id in recent if not self._done(message_id)]
            if fresh:
                self._hold(fresh)
                messages.append(_BACKLOG_TEXT)
            return messages
        try:
            message_ids, history_id = self.mailbox.added_since(self.state.get(HISTORY_KEY))
        except HistoryExpired:
            return self._recover_expired_history()
        except Exception:
            LOGGER.exception("Gmail catch-up failed. Telegram still runs.")
            self._renew_quietly()
            return self._pending_channel_reminders()
        self.state.set(HISTORY_KEY, history_id)
        messages = self._pending_channel_reminders()
        fresh = [message_id for message_id in message_ids if not self._done(message_id)]
        if fresh:
            self._hold(fresh)
            messages.append(_BACKLOG_TEXT)
        self._renew_quietly()
        return messages

    def renew_watch(self) -> None:
        with self._lock:
            if not self.config.active or self._paused():
                return
            self.mailbox.watch(self.config.topic)

    async def run_daily_renewal(self) -> None:
        while True:
            await asyncio.sleep(self.renew_every_seconds)
            if not self.config.active:
                return
            try:
                self.renew_watch()
            except Exception:
                LOGGER.exception("Daily Gmail watch renewal failed")

    def pause(self) -> list[str]:
        with self._lock:
            return self._pause()

    def _pause(self) -> list[str]:
        if not self.config.active:
            return ["Email logging is off."]
        try:
            self.mailbox.stop()
        except Exception:
            LOGGER.exception("Gmail stop failed")
            self.state.set(PAUSED_KEY, "yes")
            return [
                "Email logging is paused here, so I will not log new mail. "
                "I could not reach Gmail to stop the tap. Google may keep tapping for a few minutes. "
                "Nothing was deleted."
            ]
        self.state.set(PAUSED_KEY, "yes")
        return [
            "Email logging is paused. I asked Gmail to stop tapping. "
            "Mail in the mailbox stays, and rows already in the sheet stay."
        ]

    def resume(self) -> list[str]:
        with self._lock:
            return self._resume()

    def _resume(self) -> list[str]:
        if not self.config.active:
            return ["Email logging is off."]
        self.state.set(PAUSED_KEY, "no")
        try:
            message_ids, history_id = self.mailbox.added_since(self.state.get(HISTORY_KEY) or "0")
        except HistoryExpired:
            return ["Email logging is on again.", *self._recover_expired_history()]
        except Exception:
            LOGGER.exception("Gmail resume could not look for piled-up mail")
            self._renew_quietly()
            return ["Email logging is on again, but I could not check mail that arrived while it was paused."]
        fresh = [message_id for message_id in message_ids if not self._done(message_id)]
        if fresh:
            self._hold(fresh)
        if history_id:
            self.state.set(HISTORY_KEY, history_id)
        self._renew_quietly()
        messages = ["Email logging is on again."]
        if fresh:
            messages.append(_BACKLOG_TEXT)
        return messages

    def log_backlog(self) -> list[str]:
        with self._lock:
            return self._log_backlog()

    def _log_backlog(self) -> list[str]:
        message_ids = self._backlog_ids()
        if not message_ids:
            return ["There is no mail pile waiting."]
        notes: list[str] = []
        for message_id in message_ids:
            notes.extend(self._consume(message_id))
        self.state.set(BACKLOG_KEY, "")
        notes.append("I finished that mail pile.")
        return notes

    def skip_backlog(self) -> list[str]:
        with self._lock:
            return self._skip_backlog()

    def _skip_backlog(self) -> list[str]:
        message_ids = self._backlog_ids()
        if not message_ids:
            return ["There is no mail pile waiting."]
        for message_id in message_ids:
            self.state.set(_message_key(message_id), "skipped")
        self.state.set(BACKLOG_KEY, "")
        return ["I left that mail pile alone. Nothing new was logged."]

    def choose_channel(self, raw_channel: str) -> list[str]:
        with self._lock:
            return self._choose_channel(raw_channel)

    def _choose_channel(self, raw_channel: str) -> list[str]:
        pending = self._pending_channels()
        if not pending:
            return ["There is no email waiting for a channel."]
        wanted = " ".join(raw_channel.strip().split())
        current = pending[0]
        match = next((channel for channel in current.get("channels", []) if channel.casefold() == wanted.casefold()), None)
        if match is None:
            choices = " or ".join(f"email channel {channel}" for channel in current.get("channels", []))
            return [f"Reply {choices}."]
        amount = _decimal(current.get("amount"))
        if not isinstance(amount, Decimal):
            return ["I could not read the amount for that email, so I have not logged it."]
        decision = EmailDecision(
            action="log",
            kind="purchase",
            amount=amount,
            shop=str(current.get("shop") or ""),
            expense_date=_date(current.get("date")),
            category=str(current.get("category") or ""),
            logged_by=str(current.get("logged_by") or ""),
            payment_method=str(current.get("payment_method") or ""),
            payment_owner=str(current.get("payment_owner") or ""),
            payment_channel=match,
            description=str(current.get("shop") or ""),
        )
        text = self._write(decision, str(current.get("message_id") or ""))
        self._save_pending(pending[1:])
        return [text] if text else []

    def handle_command(self, text: str) -> list[str]:
        lowered = " ".join(text.casefold().split())
        if lowered == "pause email":
            return self.pause()
        if lowered == "resume email":
            return self.resume()
        if lowered == "log email backlog":
            return self.log_backlog()
        if lowered == "skip email backlog":
            return self.skip_backlog()
        if lowered.startswith("email channel "):
            return self.choose_channel(" ".join(text.split()[2:]))
        return []

    def handle_http(self, method: str, headers: dict[str, str], body: bytes) -> tuple[int, dict]:
        if method != "POST":
            return 405, {"ok": False}
        if not self.config.active:
            return 200, {"ok": True}
        if not self._authorized(headers):
            return 401, {"ok": False}
        note = decode_pubsub_notification(body)
        if note is None:
            return 400, {"ok": False}
        try:
            self.process_notification(note.history_id)
        except Exception:
            LOGGER.exception("Gmail notification could not be handled")
            return 500, {"ok": False}
        return 200, {"ok": True}

    def process_notification(self, pushed_history_id: str) -> None:
        with self._lock:
            self._process_notification(pushed_history_id)

    def _process_notification(self, _pushed_history_id: str) -> None:
        # The tap carries a history id, not the mail. Listing starts from the id stored earlier.
        if not self.config.active:
            return
        if self._paused():
            return
        stored = self.state.get(HISTORY_KEY)
        if not stored:
            return
        message_ids, history_id = self.mailbox.added_since(stored)
        fresh = [message_id for message_id in message_ids if not self._done(message_id)]
        if self._backlog_ids():
            extra = [message_id for message_id in fresh if message_id not in self._backlog_ids()]
            if extra:
                self._hold(extra)
                self._notify("More mail arrived while a pile is waiting. I have not logged it.")
            if history_id:
                self.state.set(HISTORY_KEY, history_id)
            return
        for message_id in fresh:
            for text in self._consume(message_id):
                self._notify(text)
        if history_id:
            self.state.set(HISTORY_KEY, history_id)

    def _consume(self, message_id: str) -> list[str]:
        if self._done(message_id):
            return []
        subject, body, addresses = self.mailbox.fetch(message_id)
        decision = self._decide(subject, body, tuple(addresses))
        if decision.action == "ignore":
            self.state.set(_message_key(message_id), "ignored")
            return []
        if decision.action == "ask":
            if decision.kind == "channel":
                self._remember_channel(message_id, decision)
                self.state.set(_message_key(message_id), "channel")
            else:
                self.state.set(_message_key(message_id), "asked")
            return [decision.text] if decision.text else []
        if decision.action == "log":
            text = self._write(decision, message_id)
            return [text] if text else []
        return []

    def _write(self, decision: EmailDecision, message_id: str) -> str:
        if decision.category == INCOME_MISC and not self.context.has_income_misc():
            self.state.set(_message_key(message_id), "asked")
            return (
                "I have not logged this. The sheet has no category named exactly "
                "Income - misc, and I will not invent one."
            )
        row = build_expense_row(decision, chat_id=self.config.chat_id, now=self._now())
        outcome = self.writer(row)
        self.state.set(_message_key(message_id), "logged" if outcome.logged else "asked")
        return outcome.text

    def _decide(self, subject: str, body: str, addresses: tuple[str, ...]) -> EmailDecision:
        try:
            methods = tuple(self.context.methods())
            categories = tuple(self.context.categories())
        except Exception:
            LOGGER.exception("Could not read card or category setup for an email")
            return EmailDecision(
                action="ask",
                kind="unclear",
                text="I got mail but could not read the card list. I have not logged it.",
            )
        return decide_mail(
            subject,
            body,
            addresses,
            forwarders=self.config.forwarders,
            methods=methods,
            categories=categories,
            channels_for=self.context.channels_for,
            category_for=self.context.category_for,
            today=self._now().date(),
            remember_category=getattr(self.context, "remember_category", None),
        )

    def _authorized(self, headers: dict[str, str]) -> bool:
        if self._authorize is not None:
            return bool(self._authorize(headers))
        if not self.config.audience:
            return False
        header = headers.get("authorization", "")
        if not header.casefold().startswith("bearer "):
            return False
        token = header.split(" ", 1)[1].strip()
        if not token:
            return False
        return verify_pubsub_token(token, self.config.audience, self.config.push_service_account or None)

    def _recover_expired_history(self) -> list[str]:
        try:
            recent = [message_id for message_id in self.mailbox.recent_ids() if _valid_id(message_id)]
            history_id = self.mailbox.watch(self.config.topic)
        except Exception:
            LOGGER.exception("Gmail history expired and the watch could not be renewed")
            return ["I lost my place in the mailbox and could not renew the tap. I have not logged old mail."]
        if history_id:
            self.state.set(HISTORY_KEY, history_id)
        fresh = [message_id for message_id in recent if not self._done(message_id)]
        if fresh:
            self._hold(fresh)
            return ["I lost my place in the mailbox. I have not logged the mail that is already there.", _BACKLOG_TEXT]
        return ["I lost my place in the mailbox. There was no mail pile to log."]

    def _renew_quietly(self) -> None:
        try:
            self.renew_watch()
        except Exception:
            LOGGER.exception("Gmail watch renewal failed")

    def _hold(self, message_ids: list[str]) -> None:
        merged = list(self._backlog_ids())
        for message_id in message_ids:
            if message_id in merged or self._done(message_id):
                continue
            merged.append(message_id)
            if self.state.get(_message_key(message_id)) not in _DONE:
                self.state.set(_message_key(message_id), "backlog")
        self.state.set(BACKLOG_KEY, ",".join(merged))

    def _backlog_ids(self) -> list[str]:
        raw = self.state.get(BACKLOG_KEY) or ""
        return [part for part in raw.split(",") if _valid_id(part)]

    def _paused(self) -> bool:
        return (self.state.get(PAUSED_KEY) or "").casefold() == "yes"

    def _done(self, message_id: str) -> bool:
        return (self.state.get(_message_key(message_id)) or "") in _DONE

    def _remember_channel(self, message_id: str, decision: EmailDecision) -> None:
        pending = [
            item
            for item in self._pending_channels()
            if item.get("message_id") != message_id
        ]
        pending.append(
            {
                "message_id": message_id,
                "amount": f"{decision.amount:.2f}" if decision.amount is not None else "",
                "shop": decision.shop,
                "date": decision.expense_date.isoformat() if decision.expense_date else "",
                "category": decision.category,
                "logged_by": decision.logged_by,
                "payment_method": decision.payment_method,
                "payment_owner": decision.payment_owner,
                "channels": list(decision.channels),
            }
        )
        self._save_pending(pending)

    def _pending_channels(self) -> list[dict]:
        raw = self.state.get(PENDING_CHANNEL_KEY) or ""
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _save_pending(self, pending: list[dict]) -> None:
        if pending:
            self.state.set(PENDING_CHANNEL_KEY, json.dumps(pending))
            return
        self.state.set(PENDING_CHANNEL_KEY, "")

    def _pending_channel_reminders(self) -> list[str]:
        pending = self._pending_channels()
        if not pending:
            return []
        current = pending[0]
        shop = current.get("shop") or "that shop"
        choices = " or ".join(f"email channel {channel}" for channel in current.get("channels", []))
        return [f"A purchase at {shop} is still waiting for a channel. Reply {choices}."]


class SheetBotState:
    def __init__(self, sheets, sheet_name: str):
        self.sheets = sheets
        self.sheet_name = sheet_name

    def get(self, key: str) -> str | None:
        return self.sheets.get_state_value(self.sheet_name, key)

    def set(self, key: str, value: str) -> None:
        self.sheets.set_state_value(self.sheet_name, key, value)


class BotEmailContext:
    def __init__(self, bot):
        self.bot = bot

    def methods(self):
        return self.bot._load_payment_config().payment_methods

    def categories(self) -> tuple[str, ...]:
        return tuple(ALL_CATEGORIES)

    def has_income_misc(self) -> bool:
        return INCOME_MISC in self.categories()

    def channels_for(self, owner: str, payment_method: str) -> tuple[str, ...]:
        return self.bot._load_payment_config().channel_options_for(owner, payment_method)

    def remember_category(self, keyword: str, category: str) -> None:
        settings = self.bot.settings
        apply_shop_keyword(self.bot.sheets, settings.category_keywords_sheet, keyword, category)

    def category_for(self, shop: str, logged_by: str, body: str = "") -> str | None:
        del body
        settings = self.bot.settings

        def save(keyword: str, category: str) -> None:
            apply_shop_keyword(self.bot.sheets, settings.category_keywords_sheet, keyword, category)

        def lookup(query: str) -> str | None:
            return lookup_shop_text(query, settings.exa_api_key)

        def ask(query: str, description: str, categories: tuple[str, ...]) -> str | None:
            return pick_existing_category(
                query,
                description,
                categories,
                settings.openai_api_key,
                settings.openai_model,
            )

        return categorize_email_shop(
            shop,
            logged_by,
            settings.me_label,
            settings.wife_label,
            self.categories(),
            lookup,
            save,
            ask,
        )


def attach_email(bot, settings, sheets) -> EmailService:
    config = EmailSettings.from_bot_settings(settings)
    if config.active:
        mailbox = GmailApiMailbox(config.client_id, config.client_secret, config.refresh_token)
    else:
        mailbox = InactiveMailbox()
    service = EmailService(
        config=config,
        mailbox=mailbox,
        state=SheetBotState(sheets, settings.bot_state_sheet),
        context=BotEmailContext(bot),
        writer=bot.log_email_row,
    )
    bot.email_service = service
    return service


class InactiveMailbox:
    def watch(self, topic: str) -> str:
        raise RuntimeError("Email logging is off.")

    def stop(self) -> None:
        raise RuntimeError("Email logging is off.")

    def added_since(self, start_history_id: str) -> tuple[list[str], str]:
        raise RuntimeError("Email logging is off.")

    def recent_ids(self, limit: int = 20) -> list[str]:
        raise RuntimeError("Email logging is off.")

    def fetch(self, message_id: str) -> tuple[str, str, tuple[str, ...]]:
        raise RuntimeError("Email logging is off.")


def decode_pubsub_notification(body: bytes) -> PushNote | None:
    try:
        payload = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    data = message.get("data")
    if not isinstance(data, str) or not data:
        return None
    padded = data + "=" * (-len(data) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return None
    if not isinstance(decoded, dict):
        return None
    history_id = str(decoded.get("historyId") or "")
    if not history_id:
        return None
    return PushNote(history_id=history_id)


def is_email_command(text: str) -> bool:
    lowered = " ".join(text.casefold().split())
    return lowered in {
        "pause email",
        "resume email",
        "log email backlog",
        "skip email backlog",
    } or lowered.startswith("email channel ")


def _message_key(message_id: str) -> str:
    return f"gmail_message_id:{message_id}"


def _valid_id(message_id: str) -> bool:
    return bool(message_id) and all(char.isalnum() or char in "-_" for char in message_id)


def _decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None
