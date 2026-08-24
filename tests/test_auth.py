# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from vote_viewer.auth import (
    AuthenticationUnavailable,
    AuthManager,
    NoAuthRedirectHandler,
)


class OversizedResponse:
    def __init__(self):
        self.read_limit = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, limit):
        self.read_limit = limit
        return b"x" * limit


def auth_config(**overrides):
    values = {
        "app_version": "test",
        "auth_provider": "lemmy",
        "auth_allowed_users": frozenset(),
        "auth_cookie_name": "jwt",
        "auth_cache_seconds": 60,
        "auth_timeout_seconds": 3.0,
        "lemmy_internal_url": "http://lemmy:8536",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class AuthTests(unittest.TestCase):
    def test_authentication_redirects_remain_disabled(self):
        handler = NoAuthRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(
                None,
                None,
                302,
                "Found",
                {},
                "https://unexpected.example",
            )
        )

    def test_authentication_cache_remains_bounded(self):
        manager = AuthManager(auth_config(), http_opener=Mock())
        for index in range(manager.CACHE_MAX_ENTRIES + 1):
            manager.cache_auth_user(index.to_bytes(4, "big"), None)

        self.assertEqual(len(manager.cache), manager.CACHE_MAX_ENTRIES)
        self.assertNotIn((0).to_bytes(4, "big"), manager.cache)

    def test_oversized_authentication_response_is_rejected(self):
        opener = Mock()
        response = OversizedResponse()
        opener.open.return_value = response
        manager = AuthManager(auth_config(), http_opener=opener)

        with self.assertRaises(AuthenticationUnavailable):
            manager.validate_lemmy_token("test-token")
        self.assertEqual(response.read_limit, 1_048_577)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 3.0)


if __name__ == "__main__":
    unittest.main()
