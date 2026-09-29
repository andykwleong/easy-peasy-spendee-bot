from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from getrichbot.dashboard_auth import (
    AuthFailure,
    TelegramIdentity,
    clear_session_cookie,
    issue_session_token,
    read_cookie,
    read_session_token,
    session_cookie,
    verify_login_widget,
    verify_webapp_init_data,
)
from getrichbot.dashboard_view import build_dashboard_payload

LOGGER = logging.getLogger(__name__)
SINGAPORE_TZ = ZoneInfo("Asia/Singapore")
_MAX_BODY_BYTES = 20_000
_PACKAGE_DIR = Path(__file__).resolve().parent
_STATIC = {
    "/dashboard.css": ("text/css; charset=utf-8", _PACKAGE_DIR / "dashboard.css"),
    "/dashboard.js": ("text/javascript; charset=utf-8", _PACKAGE_DIR / "dashboard.js"),
    "/": ("text/html; charset=utf-8", _PACKAGE_DIR / "dashboard_page.html"),
}


@dataclass
class DashboardContext:
    bot_token: str
    allowed_user_ids: set[int]
    label_for_user: Callable[[int], str | None]
    raw_sheet: str
    card_usage_sheet: str
    payment_methods_sheet: str
    card_limits_sheet: str
    sheets: object
    today: Callable[[], date] | None = None


class DashboardApp:
    """Read-only page. It never calls a Google Sheets write method."""

    def __init__(self, context: DashboardContext):
        self.context = context
        self._username_lock = threading.Lock()
        self._sheet_lock = threading.Lock()
        self._bot_username: str | None = None
        self._static = {
            path: (content_type, file_path.read_bytes())
            for path, (content_type, file_path) in _STATIC.items()
        }

    def set_bot_username(self, username: str) -> None:
        cleaned = username.strip().lstrip("@")
        with self._username_lock:
            self._bot_username = cleaned or None

    def bot_username(self) -> str | None:
        with self._username_lock:
            return self._bot_username

    def handle(self, method: str, target: str, headers: dict[str, str], body: bytes, *, secure: bool) -> tuple[int, list[tuple[str, str]], bytes]:
        normalized = {key.lower(): value for key, value in headers.items()}
        path, query = _split_target(target)
        if method == "GET" and path in self._static:
            content_type, payload = self._static[path]
            return 200, _page_headers(content_type), payload
        if method == "GET" and path == "/api/config":
            return _json(200, {"bot_username": self.bot_username()})
        if method == "GET" and path == "/api/session":
            return self._session_status(normalized)
        if method == "POST" and path == "/api/session/logout":
            return _json(200, {"ok": True}, [(_set_cookie(clear_session_cookie(secure=secure)))])
        if method == "POST" and path == "/api/session/webapp":
            return self._login_webapp(body, secure)
        if method == "POST" and path == "/api/session/widget":
            return self._login_widget(body, secure)
        if method == "GET" and path == "/api/dashboard":
            return self._dashboard(normalized, query)
        if method not in {"GET", "POST"}:
            return _json(405, {"ok": False, "error": "locked"})
        return _json(404, {"ok": False, "error": "locked"})

    def _session_status(self, headers: dict[str, str]) -> tuple[int, list[tuple[str, str]], bytes]:
        identity = self._identity_from_headers(headers)
        if identity is None:
            return _json(401, {"ok": False, "error": "locked"})
        label = self.context.label_for_user(identity.user_id)
        if label is None:
            return _json(401, {"ok": False, "error": "not_allowed"})
        return _json(200, {"ok": True, "label": label})

    def _login_webapp(self, body: bytes, secure: bool) -> tuple[int, list[tuple[str, str]], bytes]:
        payload = _json_object(body)
        if payload is None:
            return _json(401, {"ok": False, "error": "invalid"})
        init_data = payload.get("init_data")
        if not isinstance(init_data, str):
            return _json(401, {"ok": False, "error": "invalid"})
        result = verify_webapp_init_data(
            init_data,
            self.context.bot_token,
            now=_now(),
            allowed_user_ids=self.context.allowed_user_ids,
        )
        return self._login_result(result, secure)

    def _login_widget(self, body: bytes, secure: bool) -> tuple[int, list[tuple[str, str]], bytes]:
        payload = _json_object(body)
        if payload is None:
            return _json(401, {"ok": False, "error": "invalid"})
        result = verify_login_widget(
            payload,
            self.context.bot_token,
            now=_now(),
            allowed_user_ids=self.context.allowed_user_ids,
        )
        return self._login_result(result, secure)

    def _login_result(self, result: TelegramIdentity | AuthFailure, secure: bool) -> tuple[int, list[tuple[str, str]], bytes]:
        if isinstance(result, AuthFailure):
            return _json(401, {"ok": False, "error": result.reason})
        label = self.context.label_for_user(result.user_id)
        if label is None:
            return _json(401, {"ok": False, "error": "not_allowed"})
        token = issue_session_token(result.user_id, self.context.bot_token, now=_now())
        return _json(200, {"ok": True, "label": label}, [(_set_cookie(session_cookie(token, secure=secure)))])

    def _dashboard(self, headers: dict[str, str], query: dict[str, str]) -> tuple[int, list[tuple[str, str]], bytes]:
        identity = self._identity_from_headers(headers)
        if identity is None:
            return _json(401, {"ok": False, "error": "locked"})
        label = self.context.label_for_user(identity.user_id)
        if label is None:
            return _json(401, {"ok": False, "error": "not_allowed"})
        scope = query.get("scope", "mine")
        if scope not in {"mine", "both"}:
            return _json(400, {"ok": False, "error": "scope"})
        try:
            payload = self._read_sheet(label, scope)
        except Exception:
            LOGGER.error("Dashboard could not read the Google Sheet. Nothing was changed.")
            return _json(500, {"ok": False, "error": "sheet"})
        return _json(200, payload)

    def _read_sheet(self, label: str, scope: str) -> dict:
        with self._sheet_lock:
            records = self.context.sheets.get_expense_records(self.context.raw_sheet)
            card_usage = self.context.sheets.get_card_usage_records(self.context.card_usage_sheet)
            payment_config = None
            cards_error = None
            try:
                payment_config = self.context.sheets.get_payment_config(
                    self.context.payment_methods_sheet,
                    self.context.card_limits_sheet,
                )
            except (RuntimeError, ValueError):
                LOGGER.warning("Dashboard could not read payment setup. Other sections are still shown.")
                cards_error = "Card setup could not be read. Nothing was changed."
        today = self.context.today() if self.context.today is not None else datetime.now(SINGAPORE_TZ).date()
        return build_dashboard_payload(
            viewer_label=label,
            scope=scope,
            records=records,
            card_usage=card_usage,
            payment_config=payment_config,
            today=today,
            cards_error=cards_error,
        )

    def _identity_from_headers(self, headers: dict[str, str]) -> TelegramIdentity | None:
        init_data = headers.get("x-telegram-init-data", "").strip()
        if init_data:
            result = verify_webapp_init_data(
                init_data,
                self.context.bot_token,
                now=_now(),
                allowed_user_ids=self.context.allowed_user_ids,
            )
            if isinstance(result, TelegramIdentity):
                return result
        token = read_cookie(headers.get("cookie"))
        if not token:
            return None
        result = read_session_token(
            token,
            self.context.bot_token,
            now=_now(),
            allowed_user_ids=self.context.allowed_user_ids,
        )
        if isinstance(result, TelegramIdentity):
            return result
        return None


class DashboardHTTPServer(ThreadingHTTPServer):
    def __init__(self, host: str, port: int, app: DashboardApp):
        self.dashboard_app = app
        super().__init__((host, port), _DashboardHandler)


class _DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def log_message(self, format: str, *args) -> None:
        return

    def _dispatch(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length < 0 or length > _MAX_BODY_BYTES:
            status, headers, payload = _json(401, {"ok": False, "error": "invalid"})
            self._send(status, headers, payload)
            return
        body = self.rfile.read(length) if length else b""
        secure = _request_is_https(self.headers)
        status, headers, payload = self.server.dashboard_app.handle(
            self.command,
            self.path,
            {key: value for key, value in self.headers.items()},
            body,
            secure=secure,
        )
        self._send(status, headers, payload)

    def _send(self, status: int, headers: list[tuple[str, str]], payload: bytes) -> None:
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def context_from_settings(settings, sheets) -> DashboardContext:
    return DashboardContext(
        bot_token=settings.telegram_bot_token,
        allowed_user_ids=set(settings.me_telegram_ids) | set(settings.wife_telegram_ids),
        label_for_user=settings.label_for_user,
        raw_sheet=settings.raw_expenses_sheet,
        card_usage_sheet=settings.card_usage_sheet,
        payment_methods_sheet=settings.payment_methods_sheet,
        card_limits_sheet=settings.card_limits_sheet,
        sheets=sheets,
    )


def start_dashboard_server(settings, sheets, *, host: str = "0.0.0.0", port: int | None = None) -> DashboardHTTPServer | None:
    if port is None:
        raw_port = os.getenv("PORT", "8080").strip() or "8080"
        try:
            port = int(raw_port)
        except ValueError:
            LOGGER.warning("PORT is not a number. The dashboard page did not start. Telegram chat is unchanged.")
            return None
    app = DashboardApp(context_from_settings(settings, sheets))
    try:
        server = DashboardHTTPServer(host, port, app)
    except OSError:
        LOGGER.exception("The dashboard page could not listen for visitors. Telegram chat is unchanged.")
        return None
    thread = threading.Thread(target=server.serve_forever, name="dashboard-http", daemon=True)
    thread.start()
    LOGGER.info("Dashboard page is listening on port %s.", server.server_address[1])
    return server


def _now() -> int:
    return int(datetime.now(tz=SINGAPORE_TZ).timestamp())


def _request_is_https(headers) -> bool:
    forwarded = headers.get("X-Forwarded-Proto", "")
    return forwarded.split(",")[0].strip().lower() == "https"


def _split_target(target: str) -> tuple[str, dict[str, str]]:
    parts = urlsplit(target)
    query = {key: values[-1] for key, values in parse_qs(parts.query, keep_blank_values=True).items()}
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path, query


def _json_object(body: bytes) -> dict | None:
    if len(body) > _MAX_BODY_BYTES:
        return None
    try:
        payload = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _json(status: int, payload: dict, extra: list[tuple[str, str]] | None = None) -> tuple[int, list[tuple[str, str]], bytes]:
    headers = _page_headers("application/json; charset=utf-8")
    if extra:
        headers.extend(extra)
    return status, headers, json.dumps(payload).encode()


def _page_headers(content_type: str) -> list[tuple[str, str]]:
    return [
        ("Content-Type", content_type),
        ("Cache-Control", "no-store"),
        ("X-Robots-Tag", "noindex"),
        ("Referrer-Policy", "no-referrer"),
        ("X-Content-Type-Options", "nosniff"),
    ]


def _set_cookie(value: str) -> tuple[str, str]:
    return ("Set-Cookie", value)
