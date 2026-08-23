# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

os.environ["DATABASE_URL"] = "postgresql://unused:unused@localhost/unused"
os.environ["LEMMY_BASE_URL"] = "https://lemmy.example"
os.environ["LEMMY_INTERNAL_URL"] = "http://lemmy:8536"
os.environ["AUTH_PROVIDER"] = "lemmy"
os.environ["AUTH_SEARCH_REQUIRE"] = "login"
os.environ["AUTH_INSTANCE_REQUIRE"] = "admin"
os.environ["AUTH_ALLOWED_USERS"] = "Dave,BlueEther"
os.environ["AUTH_CACHE_SECONDS"] = "60"
os.environ["ENABLE_DOMAIN_SEARCH"] = "true"

import app as viewer


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, limit):
        return self.payload[:limit]


def lemmy_user_payload(username="Alice", admin=False, banned=False, deleted=False):
    return {
        "my_user": {
            "local_user_view": {
                "local_user": {"admin": admin},
                "person": {
                    "name": username,
                    "banned": banned,
                    "deleted": deleted,
                },
            }
        }
    }


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        viewer._AUTH_CACHE.clear()
        self.client = viewer.app.test_client()

    def request_as(self, payload, path="/"):
        self.client.set_cookie("jwt", "test-token")
        with patch.object(
            viewer._AUTH_HTTP_OPENER,
            "open",
            return_value=FakeResponse(payload),
        ):
            return self.client.get(path)

    def test_anonymous_search_requires_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Log in to Lemmy", response.data)

    def test_anonymous_item_routes_require_login_before_database_access(self):
        self.assertEqual(self.client.get("/item/post/1").status_code, 401)
        self.assertEqual(self.client.get("/item/comment/1").status_code, 401)

    def test_logged_in_user_can_search_but_cannot_see_instance_search(self):
        response = self.request_as(lemmy_user_payload())
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Signed in as Alice", response.data)
        self.assertNotIn(b"Instance vote overview", response.data)
        self.assertNotIn(b"Community vote overview", response.data)

    def test_logged_in_non_admin_cannot_submit_instance_search(self):
        response = self.request_as(lemmy_user_payload(), "/?instance=lemmy.world")
        self.assertEqual(response.status_code, 403)

    def test_logged_in_non_admin_cannot_submit_community_search(self):
        response = self.request_as(
            lemmy_user_payload(),
            "/?community_overview=!technology@lemmy.world",
        )
        self.assertEqual(response.status_code, 403)

    def test_logged_in_non_admin_cannot_open_instance_route(self):
        response = self.request_as(lemmy_user_payload(), "/instance/lemmy.world")
        self.assertEqual(response.status_code, 403)

    def test_logged_in_non_admin_cannot_open_community_overview(self):
        response = self.request_as(
            lemmy_user_payload(),
            "/community/technology@lemmy.world",
        )
        self.assertEqual(response.status_code, 403)

    def test_disabled_instance_search_returns_404_before_authentication(self):
        with patch.object(viewer, "ENABLE_DOMAIN_SEARCH", False):
            response = self.client.get("/instance/lemmy.world")
        self.assertEqual(response.status_code, 404)

    def test_disabled_instance_search_hides_community_overview(self):
        with patch.object(viewer, "ENABLE_DOMAIN_SEARCH", False):
            response = self.client.get("/community/technology@lemmy.world")
        self.assertEqual(response.status_code, 404)

    def test_admin_sees_instance_search(self):
        response = self.request_as(lemmy_user_payload(admin=True))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Signed in as Alice (admin)", response.data)
        self.assertIn(b"Instance vote overview", response.data)
        self.assertIn(b"Community vote overview", response.data)

    def test_admin_community_search_normalizes_and_redirects(self):
        response = self.request_as(
            lemmy_user_payload(admin=True),
            "/?community_overview=!technology@LEMMY.WORLD.",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            urlsplit(response.headers["Location"]).path,
            "/community/technology@lemmy.world",
        )

    def test_allowlist_is_case_insensitive_and_also_allows_admins(self):
        self.assertTrue(
            viewer.access_requirement_met(
                {"username": "dAvE", "admin": False}, "allowlist"
            )
        )
        self.assertTrue(
            viewer.access_requirement_met(
                {"username": "SomeoneElse", "admin": True}, "allowlist"
            )
        )
        self.assertFalse(
            viewer.access_requirement_met(
                {"username": "SomeoneElse", "admin": False}, "allowlist"
            )
        )

    def test_banned_user_is_not_authenticated(self):
        response = self.request_as(lemmy_user_payload(banned=True))
        self.assertEqual(response.status_code, 401)

    def test_authentication_failure_returns_503(self):
        self.client.set_cookie("jwt", "test-token")
        with patch.object(
            viewer._AUTH_HTTP_OPENER,
            "open",
            side_effect=URLError("unavailable"),
        ):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"authentication service is unavailable", response.data)

    def test_authentication_result_is_cached_without_storing_token(self):
        self.client.set_cookie("jwt", "test-token")
        with patch.object(
            viewer._AUTH_HTTP_OPENER,
            "open",
            return_value=FakeResponse(lemmy_user_payload()),
        ) as mocked_urlopen:
            self.assertEqual(self.client.get("/").status_code, 200)
            self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(mocked_urlopen.call_count, 1)
        auth_request = mocked_urlopen.call_args.args[0]
        self.assertEqual(auth_request.full_url, "http://lemmy:8536/api/v3/site")
        self.assertEqual(
            auth_request.get_header("Authorization"), "Bearer test-token"
        )
        self.assertTrue(all(isinstance(key, bytes) for key in viewer._AUTH_CACHE))

    def test_community_handle_parser_accepts_local_and_remote_handles(self):
        self.assertEqual(
            viewer.parse_community_handle("!newzealand"),
            ("newzealand", None),
        )
        self.assertEqual(
            viewer.parse_community_handle(" !technology@LEMMY.WORLD. "),
            ("technology", "lemmy.world"),
        )

    def test_community_handle_parser_rejects_invalid_values(self):
        for value in (
            "community",
            "!",
            "!community@",
            "!community/path",
            "!community name",
        ):
            with self.subTest(value=value):
                self.assertIsNone(viewer.parse_community_handle(value))

    def test_index_url_preserves_community_filter(self):
        url = viewer.build_index_url(
            "Dave@lemmy.nz",
            "comment",
            -1,
            3,
            "cast",
            community="!newzealand@lemmy.nz",
        )
        self.assertEqual(
            parse_qs(urlsplit(url).query),
            {
                "user": ["Dave@lemmy.nz"],
                "type": ["comment"],
                "score": ["-1"],
                "community": ["!newzealand@lemmy.nz"],
                "page": ["3"],
            },
        )

    def test_unfiltered_history_queries_keep_community_out_of_filter_cte(self):
        self.assertNotIn("f.community_id", viewer.USER_VOTES_SQL)
        self.assertNotIn("f.community_id", viewer.USER_RECEIVED_ITEMS_SQL)
        self.assertIn("f.community_id", viewer.USER_VOTES_BY_COMMUNITY_SQL)
        self.assertIn(
            "f.community_id",
            viewer.USER_RECEIVED_ITEMS_BY_COMMUNITY_SQL,
        )

    def test_community_summary_url_preserves_sort_and_page(self):
        url = viewer.build_index_url(
            "Dave@lemmy.nz",
            page=2,
            history_view="communities",
            community_sort="down",
        )
        self.assertEqual(
            parse_qs(urlsplit(url).query),
            {
                "user": ["Dave@lemmy.nz"],
                "view": ["communities"],
                "sort": ["down"],
                "page": ["2"],
            },
        )

    def test_community_overview_url_preserves_sort_and_page(self):
        url = viewer.build_community_overview_url(
            "!technology@lemmy.world",
            "down_ratio",
            2,
        )
        self.assertEqual(urlsplit(url).path, "/community/technology@lemmy.world")
        self.assertEqual(
            parse_qs(urlsplit(url).query),
            {"sort": ["down_ratio"], "page": ["2"]},
        )

    def test_community_user_links_to_profile_and_filtered_history(self):
        row = viewer.enrich_community_user(
            {
                "name": "Dave",
                "local": False,
                "actor_id": "https://lemmy.nz/u/Dave",
                "down": 2,
                "total": 10,
            },
            "!newzealand@lemmy.nz",
        )
        self.assertEqual(row["profile_path"], "/u/Dave@lemmy.nz")
        self.assertEqual(row["remote_url"], "https://lemmy.nz/u/Dave")
        self.assertEqual(
            parse_qs(urlsplit(row["vote_path"]).query),
            {
                "user": ["Dave@lemmy.nz"],
                "community": ["!newzealand@lemmy.nz"],
            },
        )

    def test_user_link_enrichment_separates_viewer_local_and_remote_urls(self):
        row = viewer.enrich_instance_user(
            {
                "name": "Dave",
                "local": False,
                "actor_id": "https://lemmy.nz/u/Dave",
                "down": 2,
                "total": 10,
            }
        )
        self.assertEqual(
            parse_qs(urlsplit(row["vote_path"]).query),
            {"user": ["Dave@lemmy.nz"]},
        )
        self.assertEqual(row["profile_path"], "/u/Dave@lemmy.nz")
        self.assertEqual(row["remote_url"], "https://lemmy.nz/u/Dave")

        local_row = viewer.enrich_instance_user(
            {
                "name": "Alice",
                "local": True,
                "actor_id": "https://lemmy.example/u/Alice",
                "down": 0,
                "total": 1,
            }
        )
        self.assertEqual(local_row["profile_path"], "/u/Alice")
        self.assertIsNone(local_row["remote_url"])

    def test_received_item_text_and_local_links_have_separate_targets(self):
        row = viewer.enrich_item(
            {
                "type": "comment",
                "comment_id": 456,
                "post_id": 123,
                "community_name": "support",
                "community_local": True,
                "community_url": "https://lemmy.example/c/support",
                "item_local": False,
                "content_hidden": False,
                "content_url": "https://lemmy.nz/comment/456",
            }
        )
        self.assertEqual(row["item_vote_path"], "/item/comment/456")
        self.assertEqual(row["item_local_path"], "/comment/456")
        self.assertEqual(row["remote_url"], "https://lemmy.nz/comment/456")

    def test_community_summary_links_to_cast_and_received_filters(self):
        row = viewer.enrich_community_summary(
            {
                "community_name": "newzealand",
                "community_local": False,
                "community_url": "https://lemmy.nz/c/newzealand",
            },
            "Dave@lemmy.nz",
        )
        self.assertEqual(row["community_display"], "!newzealand@lemmy.nz")
        self.assertEqual(
            row["community_local_path"],
            "/c/newzealand@lemmy.nz",
        )
        self.assertEqual(
            row["community_remote_url"],
            "https://lemmy.nz/c/newzealand",
        )
        self.assertEqual(
            row["overview_path"],
            "/community/newzealand@lemmy.nz",
        )
        self.assertEqual(
            parse_qs(urlsplit(row["cast_path"]).query)["community"],
            ["!newzealand@lemmy.nz"],
        )
        self.assertEqual(
            parse_qs(urlsplit(row["received_path"]).query),
            {
                "user": ["Dave@lemmy.nz"],
                "view": ["received"],
                "community": ["!newzealand@lemmy.nz"],
            },
        )

        local_row = viewer.enrich_community_summary(
            {
                "community_name": "support",
                "community_local": True,
                "community_url": "https://example.com/c/support",
            },
            "Dave@lemmy.nz",
        )
        self.assertEqual(local_row["community_display"], "!support")
        self.assertEqual(local_row["community_local_path"], "/c/support")
        self.assertIsNone(local_row["community_remote_url"])


if __name__ == "__main__":
    unittest.main()
