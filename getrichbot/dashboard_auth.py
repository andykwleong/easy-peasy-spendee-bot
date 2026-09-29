from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from urllib.parse import parse_qsl

# A Telegram confirmation is only used to open a session. It is not the
# 30-day browser memory. That memory is the signed cookie issued afterwards.
AUTH_MAX_AGE_SECONDS = 24 * 60 * 60
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
CLOCK_SKEW_SECONDS = 30
COOKIE_NAME = "grb_session"
_MAX_INIT_DATA_LENGTH = 8000
_MAX_SESSION_LENGTH = 500


@dataclass(frozen=True)
class TelegramIdentity:
    user_id: int


@dataclass(frozen=True)
class AuthFailure:
    reason: str


def verify_webapp_init_data(
    init_data: str,
    bot_token: str,
    *,
    now: int,
    allowed_user_ids: set[int],
    max_age_seconds: int = AUTH_MAX_AGE_SECONDS,
) -> TelegramIdentity | AuthFailure:
    if not init_data or len(init_data) > _MAX_INIT_DATA_LENGTH:
        return AuthFailure("invalid")
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return AuthFailure("invalid")
    received_hash = parsed.pop("hash", "")
    if not received_hash:
        return AuthFailure("invalid")
    data_check = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not _signatures_match(expected, received_hash):
        return AuthFailure("invalid")

    auth_date = _positive_int(parsed.get("auth_date"))
    if auth_date is None:
        return AuthFailure("invalid")
    if _auth_date_is_stale(auth_date, now, max_age_seconds):
        return AuthFailure("expired")

    user_id = _user_id_from_webapp_user(parsed.get("user", ""))
    if user_id is None:
        return AuthFailure("invalid")
    if user_id not in allowed_user_ids:
        return AuthFailure("not_allowed")
    return TelegramIdentity(user_id)


def verify_login_widget(
    payload: object,
    bot_token: str,
    *,
    now: int,
    allowed_user_ids: set[int],
    max_age_seconds: int = AUTH_MAX_AGE_SECONDS,
) -> TelegramIdentity | AuthFailure:
    if not isinstance(payload, dict):
        return AuthFailure("invalid")
    received_hash = payload.get("hash")
    if not isinstance(received_hash, str) or not received_hash:
        return AuthFailure("invalid")

    pairs: list[tuple[str, str]] = []
    for key, value in payload.items():
        if key == "hash":
            continue
        if not isinstance(key, str) or not key:
            return AuthFailure("invalid")
        text = _widget_text(value)
        if text is None:
            return AuthFailure("invalid")
        pairs.append((key, text))

    data_check = "\n".join(f"{key}={value}" for key, value in sorted(pairs))
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not _signatures_match(expected, received_hash):
        return AuthFailure("invalid")

    fields = dict(pairs)
    user_id = _positive_int(fields.get("id"))
    auth_date = _positive_int(fields.get("auth_date"))
    if user_id is None or auth_date is None:
        return AuthFailure("invalid")
    if _auth_date_is_stale(auth_date, now, max_age_seconds):
        return AuthFailure("expired")
    if user_id not in allowed_user_ids:
        return AuthFailure("not_allowed")
    return TelegramIdentity(user_id)


def issue_session_token(
    user_id: int,
    bot_token: str,
    *,
    now: int,
    ttl_seconds: int = SESSION_TTL_SECONDS,
) -> str:
    if user_id <= 0:
        raise ValueError("user_id must be positive")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    payload = {"exp": now + ttl_seconds, "uid": user_id}
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    signature = hmac.new(bot_token.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def read_session_token(
    token: str,
    bot_token: str,
    *,
    now: int,
    allowed_user_ids: set[int],
) -> TelegramIdentity | AuthFailure:
    if not token or len(token) > _MAX_SESSION_LENGTH or token.count(".") != 1:
        return AuthFailure("invalid")
    body, signature = token.split(".", 1)
    expected = hmac.new(bot_token.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not _signatures_match(expected, signature):
        return AuthFailure("invalid")
    padding = "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + padding))
    except (json.JSONDecodeError, ValueError):
        return AuthFailure("invalid")
    if not isinstance(payload, dict):
        return AuthFailure("invalid")
    user_id = payload.get("uid")
    exp = payload.get("exp")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        return AuthFailure("invalid")
    if isinstance(exp, bool) or not isinstance(exp, int):
        return AuthFailure("invalid")
    if exp <= now:
        return AuthFailure("expired")
    if user_id not in allowed_user_ids:
        return AuthFailure("not_allowed")
    return TelegramIdentity(user_id)


def session_cookie(token: str, *, secure: bool, max_age: int = SESSION_TTL_SECONDS) -> str:
    parts = [
        f"{COOKIE_NAME}={token}",
        "HttpOnly",
        "Path=/",
        "SameSite=Lax",
        f"Max-Age={max_age}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_session_cookie(*, secure: bool) -> str:
    parts = [
        f"{COOKIE_NAME}=",
        "HttpOnly",
        "Path=/",
        "SameSite=Lax",
        "Max-Age=0",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def read_cookie(header: str | None, name: str = COOKIE_NAME) -> str | None:
    if not header:
        return None
    for part in header.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() == name:
            return value.strip() or None
    return None


def _widget_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        return None
    if isinstance(value, int):
        return str(value)
    return None


def _user_id_from_webapp_user(raw: str) -> int | None:
    if not raw:
        return None
    try:
        user = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(user, dict):
        return None
    user_id = user.get("id")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        return None
    return user_id


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        number = int(value)
        return number if number > 0 else None
    return None


def _auth_date_is_stale(auth_date: int, now: int, max_age_seconds: int) -> bool:
    if auth_date > now + CLOCK_SKEW_SECONDS:
        return True
    return now - auth_date > max_age_seconds


def _signatures_match(expected: str, received: str) -> bool:
    if len(expected) != len(received):
        return False
    return hmac.compare_digest(expected, received)
