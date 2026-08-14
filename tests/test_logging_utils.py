from __future__ import annotations

import logging
import unittest

from getrichbot.logging_utils import RedactingFormatter, redact_sensitive_text


class LoggingSecurityTests(unittest.TestCase):
    def test_redacts_token_inside_telegram_api_url(self):
        token = "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcd123456"
        message = f'POST https://api.telegram.org/bot{token}/getUpdates "HTTP/1.1 200 OK"'

        redacted = redact_sensitive_text(message)

        self.assertNotIn(token, redacted)
        self.assertIn("https://api.telegram.org/bot<redacted>/getUpdates", redacted)

    def test_redacts_bare_telegram_token(self):
        token = "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcd123456"

        redacted = redact_sensitive_text(f"Telegram failed with token {token}")

        self.assertNotIn(token, redacted)
        self.assertIn("<telegram-token-redacted>", redacted)

    def test_formatter_redacts_exception_text(self):
        token = "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcd123456"
        formatter = RedactingFormatter("%(levelname)s:%(name)s:%(message)s")

        try:
            raise RuntimeError(f"request failed for https://api.telegram.org/bot{token}/getMe")
        except RuntimeError:
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="Telegram request failed",
                args=(),
                exc_info=__import__("sys").exc_info(),
            )

        output = formatter.format(record)

        self.assertNotIn(token, output)
        self.assertIn("https://api.telegram.org/bot<redacted>/getMe", output)


if __name__ == "__main__":
    unittest.main()
