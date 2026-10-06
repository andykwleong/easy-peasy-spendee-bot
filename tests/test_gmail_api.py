from __future__ import annotations

import logging
import unittest
from io import StringIO
from unittest.mock import patch

from getrichbot.gmail_api import GmailApiMailbox
from getrichbot.gmail_api import user_credentials


class GmailRefreshScopeTests(unittest.TestCase):
    def test_refresh_omits_scopes_and_does_not_log_the_token(self):
        refresh_token = "refresh-token-value"
        client_secret = "client-secret-value"
        access_token = "access-token-value"
        built = {}

        def fake_build(api, version, credentials=None, cache_discovery=False):
            built["credentials"] = credentials
            return object()

        bodies = []

        def fake_token_request(request, token_uri, body, headers=None):
            bodies.append(dict(body))
            return True, {"access_token": access_token, "expires_in": 3600}, False

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("getrichbot.gmail_api")
        logger.addHandler(handler)
        previous_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            with patch("getrichbot.gmail_api.build", fake_build):
                mailbox = GmailApiMailbox("client-id-value", client_secret, refresh_token)
                mailbox._gmail()
            credentials = built["credentials"]
            direct = user_credentials("client-id-value", client_secret, refresh_token)
            self.assertIsNone(credentials.scopes)
            self.assertIsNone(direct.scopes)
            self.assertEqual(credentials.refresh_token, direct.refresh_token)
            with patch("google.oauth2._client._token_endpoint_request_no_throw", fake_token_request):
                credentials.refresh(object())
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        self.assertEqual(len(bodies), 1)
        self.assertNotIn("scope", bodies[0])
        self.assertEqual(bodies[0]["refresh_token"], refresh_token)
        logged = stream.getvalue()
        self.assertNotIn(refresh_token, logged)
        self.assertNotIn(client_secret, logged)
        self.assertNotIn(access_token, logged)
