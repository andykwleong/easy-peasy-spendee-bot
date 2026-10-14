from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from getrichbot.email_mail import addresses_in_text

LOGGER = logging.getLogger(__name__)
_HEADER_NAMES = {
    "from",
    "reply-to",
    "resent-from",
    "x-forwarded-for",
    "x-forwarded-to",
    "to",
    "cc",
    "delivered-to",
    "x-original-to",
    "return-path",
}


class HistoryExpired(Exception):
    pass


@dataclass(frozen=True)
class FetchedMail:
    subject: str
    body: str
    addresses: tuple[str, ...]


class GmailApiMailbox:
    """Reads one mailbox. The spreadsheet key is not used."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._service = None

    def watch(self, topic: str) -> str:
        response = (
            self._gmail()
            .users()
            .watch(
                userId="me",
                body={
                    "topicName": topic,
                    "labelIds": ["INBOX"],
                    "labelFilterBehavior": "INCLUDE",
                },
            )
            .execute()
        )
        return str(response.get("historyId") or "")

    def stop(self) -> None:
        self._gmail().users().stop(userId="me").execute()

    def added_since(self, start_history_id: str) -> tuple[list[str], str]:
        message_ids: list[str] = []
        page_token = None
        history_id = start_history_id
        for _page in range(5):
            try:
                response = (
                    self._gmail()
                    .users()
                    .history()
                    .list(
                        userId="me",
                        startHistoryId=start_history_id,
                        historyTypes=["messageAdded"],
                        labelId="INBOX",
                        pageToken=page_token,
                    )
                    .execute()
                )
            except Exception as exc:
                if _is_not_found(exc):
                    raise HistoryExpired(start_history_id) from exc
                raise
            history_id = str(response.get("historyId") or history_id)
            for item in response.get("history", []):
                for added in item.get("messagesAdded", []):
                    message = added.get("message") or {}
                    message_id = str(message.get("id") or "")
                    if message_id and message_id not in message_ids:
                        message_ids.append(message_id)
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return message_ids, history_id

    def recent_ids(self, limit: int = 20) -> list[str]:
        response = (
            self._gmail()
            .users()
            .messages()
            .list(userId="me", labelIds=["INBOX"], maxResults=limit)
            .execute()
        )
        return [str(item.get("id")) for item in response.get("messages", []) if item.get("id")]

    def fetch(self, message_id: str) -> tuple[str, str, tuple[str, ...]]:
        message = self._gmail().users().messages().get(userId="me", id=message_id, format="full").execute()
        fetched = parse_gmail_message(message)
        return fetched.subject, fetched.body, fetched.addresses

    def _gmail(self):
        if self._service is None:
            credentials = user_credentials(self.client_id, self.client_secret, self.refresh_token)
            self._service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        return self._service


def user_credentials(client_id: str, client_secret: str, refresh_token: str) -> Credentials:
    """User credentials that refresh without naming a scope list.

    The OAuth playground often grants extra scopes beside Gmail read-only.
    Sending only the read-only scope on refresh makes Google reject the token.
    Leaving scopes off lets Google keep the scopes it originally granted.
    """
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
    )


def parse_gmail_message(message: dict) -> FetchedMail:
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []
    subject = _header(headers, "Subject")
    address_blob = "\n".join(_header(headers, name) for name in _HEADER_NAMES)
    addresses = addresses_in_text(address_blob + "\n" + _plain_text(payload))
    return FetchedMail(subject=subject, body=_plain_text(payload), addresses=addresses)


def verify_pubsub_token(token: str, audience: str, expected_email: str | None) -> bool:
    try:
        claim = id_token.verify_oauth2_token(token, google_requests.Request(), audience)
    except Exception:
        LOGGER.info("Rejected a Gmail tap because the Google signature did not match.")
        return False
    if not claim.get("email_verified"):
        return False
    if expected_email and str(claim.get("email", "")).casefold() != expected_email.casefold():
        return False
    return True


def _plain_text(payload: dict) -> str:
    mime = str(payload.get("mimeType") or "")
    data = (payload.get("body") or {}).get("data")
    if mime == "text/plain" and data:
        return _decode_body(str(data))
    parts = [_plain_text(part) for part in payload.get("parts") or []]
    text = "\n".join(part for part in parts if part)
    if text:
        return text
    if data:
        raw = _decode_body(str(data))
        if mime == "text/html":
            return re.sub(r"<[^>]+>", " ", raw)
        return raw
    return ""


def _header(headers: list[dict], name: str) -> str:
    for item in headers:
        if str(item.get("name", "")).casefold() == name.casefold():
            return str(item.get("value") or "")
    return ""


def _decode_body(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _is_not_found(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "resp", None), "status", None)
    return str(status) == "404"
