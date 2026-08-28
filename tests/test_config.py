# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import unittest
from pathlib import Path

from vote_viewer.config import load_config


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigTests(unittest.TestCase):
    def load(self, **overrides):
        environ = {"DATABASE_URL": "postgresql://viewer@example/lemmy"}
        environ.update(overrides)
        return load_config(environ, PROJECT_ROOT)

    def test_defaults_are_preserved(self):
        config = self.load()

        self.assertEqual(config.app_prefix, "/votes")
        self.assertEqual(config.page_size, 100)
        self.assertEqual(config.instance_query_timeout_seconds, 12)
        self.assertEqual(config.vote_window_days, 30)
        self.assertEqual(config.timezone_name, "UTC")
        self.assertFalse(config.enable_domain_search)
        self.assertTrue(config.enable_instance_content_counts)
        self.assertTrue(config.enable_community_content_counts)
        self.assertTrue(config.enable_user_vote_graphs)
        self.assertFalse(config.enable_community_vote_graphs)
        self.assertEqual(config.user_vote_graph_cache_seconds, 300)
        self.assertEqual(config.overview_vote_graph_cache_seconds, 1800)
        self.assertEqual(config.auth_provider, "none")
        self.assertEqual(config.auth_search_require, "none")
        self.assertEqual(config.auth_instance_require, "none")
        self.assertEqual(config.auth_allowed_users, frozenset())
        self.assertEqual(config.auth_cookie_name, "jwt")
        self.assertEqual(config.auth_cache_seconds, 60)
        self.assertEqual(config.auth_timeout_seconds, 3.0)
        self.assertIsNone(config.lemmy_base_url)
        self.assertIsNone(config.lemmy_internal_url)

    def test_values_are_normalized_bounded_and_fallback_on_bad_numbers(self):
        config = self.load(
            APP_PREFIX="//viewer//",
            PAGE_SIZE="999",
            INSTANCE_QUERY_TIMEOUT_SECONDS="1",
            VOTE_WINDOW_DAYS="invalid",
            AUTH_PROVIDER="lemmy",
            AUTH_SEARCH_REQUIRE="allowlist",
            AUTH_INSTANCE_REQUIRE="admin",
            AUTH_ALLOWED_USERS=" Dave, BLUEETHER, ",
            AUTH_COOKIE_NAME=" ",
            AUTH_CACHE_SECONDS="-5",
            AUTH_TIMEOUT_SECONDS="99",
            LEMMY_BASE_URL="HTTPS://Lemmy.Example/",
            LEMMY_INTERNAL_URL="http://lemmy:8536/",
            ENABLE_DOMAIN_SEARCH="TRUE",
            ENABLE_INSTANCE_CONTENT_COUNTS="false",
            ENABLE_COMMUNITY_CONTENT_COUNTS="FALSE",
            ENABLE_USER_VOTE_GRAPHS="false",
            ENABLE_COMMUNITY_VOTE_GRAPHS="TRUE",
            USER_VOTE_GRAPH_CACHE_SECONDS="9999",
            OVERVIEW_VOTE_GRAPH_CACHE_SECONDS="999999",
        )

        self.assertEqual(config.app_prefix, "/viewer")
        self.assertEqual(config.page_size, 250)
        self.assertEqual(config.instance_query_timeout_seconds, 5)
        self.assertEqual(config.vote_window_days, 30)
        self.assertTrue(config.enable_domain_search)
        self.assertFalse(config.enable_instance_content_counts)
        self.assertFalse(config.enable_community_content_counts)
        self.assertFalse(config.enable_user_vote_graphs)
        self.assertTrue(config.enable_community_vote_graphs)
        self.assertEqual(config.user_vote_graph_cache_seconds, 3600)
        self.assertEqual(config.overview_vote_graph_cache_seconds, 86400)
        self.assertEqual(
            config.auth_allowed_users, frozenset({"dave", "blueether"})
        )
        self.assertEqual(config.auth_cookie_name, "jwt")
        self.assertEqual(config.auth_cache_seconds, 0)
        self.assertEqual(config.auth_timeout_seconds, 10.0)
        self.assertEqual(config.lemmy_base_url, "https://Lemmy.Example")
        self.assertEqual(config.lemmy_instance, "lemmy.example")
        self.assertEqual(config.lemmy_internal_url, "http://lemmy:8536")
        self.assertEqual(config.lemmy_login_url, "https://Lemmy.Example/login")

    def test_disabled_features_do_not_require_an_authentication_provider(self):
        config = self.load(
            AUTH_SEARCH_REQUIRE="DISABLED",
            AUTH_INSTANCE_REQUIRE="disabled",
        )

        self.assertEqual(config.auth_provider, "none")
        self.assertEqual(config.auth_search_require, "disabled")
        self.assertEqual(config.auth_instance_require, "disabled")

    def test_invalid_settings_raise_the_existing_startup_errors(self):
        invalid_settings = (
            ({"ENABLE_DOMAIN_SEARCH": "yes"}, "must be either true or false"),
            (
                {"ENABLE_INSTANCE_CONTENT_COUNTS": "yes"},
                "must be either true or false",
            ),
            (
                {"ENABLE_COMMUNITY_CONTENT_COUNTS": "yes"},
                "must be either true or false",
            ),
            (
                {"ENABLE_USER_VOTE_GRAPHS": "yes"},
                "must be either true or false",
            ),
            (
                {"ENABLE_COMMUNITY_VOTE_GRAPHS": "yes"},
                "must be either true or false",
            ),
            ({"TIMEZONE": "Not/A_Timezone"}, "Invalid TIMEZONE"),
            ({"AUTH_PROVIDER": "other"}, "AUTH_PROVIDER must be either"),
            ({"AUTH_SEARCH_REQUIRE": "staff"}, "AUTH_SEARCH_REQUIRE must be"),
            (
                {"AUTH_SEARCH_REQUIRE": "login"},
                "AUTH_PROVIDER must be lemmy",
            ),
            (
                {"AUTH_PROVIDER": "lemmy"},
                "LEMMY_INTERNAL_URL or LEMMY_BASE_URL is required",
            ),
            (
                {"LEMMY_BASE_URL": "https://example.com/path"},
                "LEMMY_BASE_URL must be an HTTP",
            ),
            (
                {"LEMMY_BASE_URL": "https://example.com:bad"},
                "LEMMY_BASE_URL must be an HTTP",
            ),
            (
                {"LEMMY_INTERNAL_URL": "https://example.com/?token=value"},
                "LEMMY_INTERNAL_URL must be an HTTP",
            ),
        )
        for overrides, message in invalid_settings:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(RuntimeError, message):
                    self.load(**overrides)

    def test_database_url_remains_required(self):
        with self.assertRaises(KeyError):
            load_config({}, PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
