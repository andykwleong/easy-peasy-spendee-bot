from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from urllib.parse import urlencode

from getrichbot.dashboard_auth import (
    AUTH_MAX_AGE_SECONDS,
    CLOCK_SKEW_SECONDS,
    SESSION_TTL_SECONDS,
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

TOKEN = "test-bot-token"
ALLOWED = {111, 222}
NOW = 1_700_000_000


def sign_webapp(fields: dict[str, str], token: str = TOKEN) -> str:
    data_check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def sign_widget(fields: dict[str, str], token: str = TOKEN) -> dict:
    data_check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hashlib.sha256(token.encode()).digest()
    digest = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    payload: dict[str, object] = dict(fields)
    payload["hash"] = digest
    payload["id"] = int(fields["id"])
    payload["auth_date"] = int(fields["auth_date"])
    return payload


def webapp_fields(user_id: int = 111, auth_date: int = NOW) -> dict[str, str]:
    user = json.dumps({"id": user_id, "first_name": "Alex"}, separators=(",", ":"))
    return {"auth_date": str(auth_date), "query_id": "sample-query", "user": user}


class DashboardAuthTests(unittest.TestCase):
    def test_webapp_login_accepts_a_signed_allowed_user(self):
        result = verify_webapp_init_data(sign_webapp(webapp_fields()), TOKEN, now=NOW, allowed_user_ids=ALLOWED)

        self.assertEqual(result, TelegramIdentity(111))

    def test_webapp_login_rejects_a_tampered_user_id(self):
        signed = sign_webapp(webapp_fields())
        tampered = signed.replace("111", "222", 1)

        result = verify_webapp_init_data(tampered, TOKEN, now=NOW, allowed_user_ids=ALLOWED)

        self.assertEqual(result, AuthFailure("invalid"))

    def test_webapp_login_rejects_a_user_who_is_not_allowed(self):
        result = verify_webapp_init_data(
            sign_webapp(webapp_fields(user_id=333)),
            TOKEN,
            now=NOW,
            allowed_user_ids=ALLOWED,
        )

        self.assertEqual(result, AuthFailure("not_allowed"))

    def test_webapp_login_rejects_a_stale_or_future_confirmation(self):
        stale = verify_webapp_init_data(
            sign_webapp(webapp_fields(auth_date=NOW - AUTH_MAX_AGE_SECONDS - 1)),
            TOKEN,
            now=NOW,
            allowed_user_ids=ALLOWED,
        )
        future = verify_webapp_init_data(
            sign_webapp(webapp_fields(auth_date=NOW + CLOCK_SKEW_SECONDS + 5)),
            TOKEN,
            now=NOW,
            allowed_user_ids=ALLOWED,
        )

        self.assertEqual(stale, AuthFailure("expired"))
        self.assertEqual(future, AuthFailure("expired"))

    def test_webapp_login_rejects_the_wrong_bot_token(self):
        result = verify_webapp_init_data(sign_webapp(webapp_fields()), "other-token", now=NOW, allowed_user_ids=ALLOWED)

        self.assertEqual(result, AuthFailure("invalid"))

    def test_widget_login_accepts_numeric_fields_from_the_browser(self):
        payload = sign_widget(
            {
                "auth_date": str(NOW),
                "first_name": "Alex Example",
                "id": "111",
                "username": "sample_user",
            }
        )

        result = verify_login_widget(payload, TOKEN, now=NOW, allowed_user_ids=ALLOWED)

        self.assertEqual(result, TelegramIdentity(111))

    def test_widget_login_rejects_a_missing_hash_and_a_changed_id(self):
        payload = sign_widget({"auth_date": str(NOW), "first_name": "Alex", "id": "111"})
        missing = dict(payload)
        missing.pop("hash")
        changed = dict(payload)
        changed["id"] = 222

        self.assertEqual(verify_login_widget(missing, TOKEN, now=NOW, allowed_user_ids=ALLOWED), AuthFailure("invalid"))
        self.assertEqual(verify_login_widget(changed, TOKEN, now=NOW, allowed_user_ids=ALLOWED), AuthFailure("invalid"))

    def test_widget_login_rejects_someone_outside_the_two_accounts(self):
        payload = sign_widget({"auth_date": str(NOW), "first_name": "Pat", "id": "333"})

        result = verify_login_widget(payload, TOKEN, now=NOW, allowed_user_ids=ALLOWED)

        self.assertEqual(result, AuthFailure("not_allowed"))

    def test_session_remembers_an_allowed_user_for_thirty_days(self):
        self.assertEqual(SESSION_TTL_SECONDS, 30 * 24 * 60 * 60)
        token = issue_session_token(111, TOKEN, now=NOW)

        self.assertEqual(
            read_session_token(token, TOKEN, now=NOW + SESSION_TTL_SECONDS - 1, allowed_user_ids=ALLOWED),
            TelegramIdentity(111),
        )
        self.assertEqual(
            read_session_token(token, TOKEN, now=NOW + SESSION_TTL_SECONDS, allowed_user_ids=ALLOWED),
            AuthFailure("expired"),
        )

    def test_session_rejects_tampering_and_a_removed_user(self):
        token = issue_session_token(111, TOKEN, now=NOW)
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")

        self.assertEqual(read_session_token(tampered, TOKEN, now=NOW, allowed_user_ids=ALLOWED), AuthFailure("invalid"))
        self.assertEqual(read_session_token(token, "other-token", now=NOW, allowed_user_ids=ALLOWED), AuthFailure("invalid"))
        self.assertEqual(read_session_token(token, TOKEN, now=NOW, allowed_user_ids={222}), AuthFailure("not_allowed"))

    def test_logout_cookie_forgets_the_browser_immediately(self):
        kept = session_cookie("sample-token", secure=True)
        cleared = clear_session_cookie(secure=True)

        self.assertIn("Max-Age=2592000", kept)
        self.assertIn("HttpOnly", kept)
        self.assertIn("Secure", kept)
        self.assertNotIn("Secure", session_cookie("sample-token", secure=False))
        self.assertIn("Max-Age=0", cleared)
        self.assertEqual(read_cookie(f"other=1; {kept.split(';', 1)[0]}"), "sample-token")
        self.assertIsNone(read_cookie(cleared))


if __name__ == "__main__":
    unittest.main()
