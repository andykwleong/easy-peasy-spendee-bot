from __future__ import annotations

import logging
import re


_TELEGRAM_API_TOKEN_PATTERN = re.compile(
    r"(https://api\.telegram\.org/bot)[^/\s\"']+",
    re.IGNORECASE,
)
_BARE_TELEGRAM_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])\d{6,}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"
)


def redact_sensitive_text(value: str) -> str:
    redacted = _TELEGRAM_API_TOKEN_PATTERN.sub(r"\1<redacted>", value)
    return _BARE_TELEGRAM_TOKEN_PATTERN.sub("<telegram-token-redacted>", redacted)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_text(super().format(record))


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, force=True)

    # HTTPX includes complete request URLs at INFO level. Telegram embeds the
    # bot token in those URLs, so routine request logs must stay suppressed.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    for handler in logging.getLogger().handlers:
        current = handler.formatter
        handler.setFormatter(
            RedactingFormatter(
                fmt=current._fmt if current is not None else None,
                datefmt=current.datefmt if current is not None else None,
            )
        )
