# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

os.environ["DATABASE_URL"] = "postgresql://unused:unused@localhost/unused"
os.environ["APP_PREFIX"] = "/"
os.environ["LEMMY_BASE_URL"] = "https://lemmy.example"
os.environ["LEMMY_INTERNAL_URL"] = "http://lemmy:8536"
os.environ["AUTH_PROVIDER"] = "lemmy"
os.environ["AUTH_SEARCH_REQUIRE"] = "login"
os.environ["AUTH_INSTANCE_REQUIRE"] = "admin"
os.environ["AUTH_ALLOWED_USERS"] = "Dave,BlueEther"
os.environ["AUTH_CACHE_SECONDS"] = "60"
os.environ["ENABLE_DOMAIN_SEARCH"] = "true"

import app as compatibility_entrypoint
from vote_viewer import application as viewer
from vote_viewer import links
from vote_viewer import create_app


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, limit):
        return self.payload[:limit]


class ScriptedDatabase:
    def __init__(self, results):
        self.results = list(results)
        self.queries = []
        self.current_result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self):
        return self

    def execute(self, query, params=None):
        self.queries.append((query, params))
        self.current_result = self.results.pop(0)
        if isinstance(self.current_result, BaseException):
            raise self.current_result

    def fetchone(self):
        return self.current_result

    def fetchall(self):
        return self.current_result


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


class VoteViewerTests(unittest.TestCase):
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

    def test_factory_preserves_app_compatibility_entrypoint(self):
        self.assertIs(create_app(), viewer.app)
        self.assertIs(compatibility_entrypoint.app, viewer.app)
        self.assertIs(
            viewer.app.config["VOTE_VIEWER_CONFIG"], viewer.CONFIG
        )

    def test_pure_link_helpers_use_explicit_configuration(self):
        self.assertEqual(
            links.build_index_url(
                "Dave@lemmy.nz",
                history_view="received",
                received_sort="top",
                app_prefix="/votes",
            ),
            "/votes/?user=Dave%40lemmy.nz&view=received&sort=top",
        )
        self.assertEqual(
            links.parse_item_search(
                "https://lemmy.example/post/123",
                "https://lemmy.example",
            ),
            {"local_item": ("post", 123), "ap_urls": (
                "https://lemmy.example/post/123",
                "https://lemmy.example/post/123",
            )},
        )
        self.assertEqual(
            links.make_pagination(45, 2, 20),
            {
                "page": 2,
                "page_count": 3,
                "total": 45,
                "offset": 20,
                "has_prev": True,
                "has_next": True,
                "prev_page": 1,
                "next_page": 3,
            },
        )

    def request_index(self, path, results, community=None):
        database = ScriptedDatabase(results)
        context = {}
        user = {
            "id": 42,
            "name": "Dave",
            "display_name": "Dave",
            "local": False,
            "actor_id": "https://lemmy.nz/u/Dave",
            "handle": "Dave@lemmy.nz",
            "profile_path": "/u/Dave@lemmy.nz",
        }

        def capture_template(template_name, **values):
            context["template_name"] = template_name
            context.update(values)
            return "rendered"

        with (
            patch.object(viewer, "db", return_value=database),
            patch.object(viewer, "resolve_user", return_value=user),
            patch.object(
                viewer,
                "resolve_community",
                return_value=(community, None),
            ),
            patch.object(viewer, "enrich_user_vote", side_effect=dict),
            patch.object(viewer, "enrich_item", side_effect=dict),
            patch.object(
                viewer,
                "enrich_community_summary",
                side_effect=lambda row, username: dict(row),
            ),
            patch.object(viewer, "render_template", side_effect=capture_template),
        ):
            response = self.request_as(lemmy_user_payload(), path)
        return response, database, context

    @staticmethod
    def user_summaries(filtered_total=1, filtered_items=1):
        cast = {
            "total": 12,
            "up": 9,
            "down": 3,
            "neutral": 0,
            "posts": 5,
            "comments": 7,
            "filtered_total": filtered_total,
        }
        received = {
            "total": 30,
            "up": 25,
            "down": 5,
            "neutral": 0,
            "posts": 20,
            "comments": 10,
            "items": 8,
            "post_items": 5,
            "comment_items": 3,
            "filtered_items": filtered_items,
            "post_filtered_items": filtered_items,
            "comment_filtered_items": filtered_items,
        }
        return cast, received

    def test_anonymous_search_requires_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Log in to Lemmy", response.data)
        self.assertNotIn(b"github.com/BlueEther/lemmy-vote-viewer", response.data)

    def test_anonymous_item_routes_require_login_before_database_access(self):
        self.assertEqual(self.client.get("/item/post/1").status_code, 401)
        self.assertEqual(self.client.get("/item/comment/1").status_code, 401)

    def test_logged_in_user_can_search_but_cannot_see_instance_search(self):
        response = self.request_as(lemmy_user_payload())
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Signed in as Alice", response.data)
        self.assertIn(b"github.com/BlueEther/lemmy-vote-viewer", response.data)
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

    def test_index_cast_view_selects_unfiltered_query_and_preserves_filters(self):
        cast, received = self.user_summaries(filtered_total=250)
        response, database, context = self.request_index(
            "/?user=Dave%40lemmy.nz&type=comment&score=-1&page=2",
            [cast, received, []],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [query for query, _ in database.queries],
            [
                viewer.USER_SUMMARY_SQL,
                viewer.USER_RECEIVED_SUMMARY_SQL,
                viewer.USER_VOTES_SQL,
            ],
        )
        self.assertEqual(
            database.queries[2][1],
            ("comment", -1, 42, 42, viewer.PAGE_SIZE, viewer.PAGE_SIZE),
        )
        self.assertEqual(context["history_view"], "cast")
        self.assertEqual(context["content_type"], "comment")
        self.assertEqual(context["score_filter"], -1)
        self.assertEqual(context["pagination"]["page"], 2)
        self.assertEqual(
            parse_qs(urlsplit(context["pagination"]["next_url"]).query),
            {
                "user": ["Dave@lemmy.nz"],
                "type": ["comment"],
                "score": ["-1"],
                "page": ["3"],
            },
        )

    def test_index_cast_view_selects_community_filtered_query(self):
        cast, received = self.user_summaries()
        community = {
            "id": 77,
            "name": "newzealand",
            "handle": "!newzealand@lemmy.nz",
        }
        response, database, context = self.request_index(
            "/?user=Dave%40lemmy.nz&community=!newzealand%40lemmy.nz",
            [cast, received, []],
            community=community,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(database.queries[2][0], viewer.USER_VOTES_BY_COMMUNITY_SQL)
        self.assertEqual(
            database.queries[2][1],
            ("all", None, 77, 42, 42, viewer.PAGE_SIZE, 0),
        )
        self.assertEqual(context["community_query"], "!newzealand@lemmy.nz")
        self.assertEqual(context["community"], community)

    def test_index_received_view_selects_sort_and_ignores_score(self):
        for sort_name in ("date", "top", "bottom"):
            with self.subTest(sort=sort_name):
                cast, received = self.user_summaries(filtered_items=2)
                path = (
                    "/?user=Dave%40lemmy.nz&view=received&type=post"
                    f"&score=-1&sort={sort_name}"
                )
                response, database, context = self.request_index(
                    path,
                    [cast, received, []],
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    database.queries[2][0], viewer.USER_RECEIVED_ITEMS_SQL
                )
                self.assertEqual(
                    database.queries[2][1],
                    ("post", sort_name, 42, 42, viewer.PAGE_SIZE, 0),
                )
                self.assertIsNone(context["score_filter"])
                self.assertEqual(context["received_sort"], sort_name)

    def test_index_received_view_selects_community_filtered_query(self):
        cast, received = self.user_summaries(filtered_items=250)
        community = {
            "id": 77,
            "name": "newzealand",
            "handle": "!newzealand@lemmy.nz",
        }
        response, database, context = self.request_index(
            "/?user=Dave%40lemmy.nz&view=received&sort=bottom"
            "&community=!newzealand%40lemmy.nz",
            [cast, received, []],
            community=community,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            database.queries[2][0], viewer.USER_RECEIVED_ITEMS_BY_COMMUNITY_SQL
        )
        self.assertEqual(
            database.queries[2][1],
            ("all", "bottom", 77, 42, 42, viewer.PAGE_SIZE, 0),
        )
        self.assertIn("view=received", context["pagination"].get("next_url", ""))

    def test_index_community_view_selects_sort_and_preserves_pagination(self):
        cast, received = self.user_summaries()
        community_row = {"community_count": 250, "community_id": 77}
        response, database, context = self.request_index(
            "/?user=Dave%40lemmy.nz&view=communities&sort=down&page=2",
            [cast, received, [community_row]],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            database.queries[2][0],
            viewer.USER_COMMUNITY_SUMMARY_SQL.format(
                order_by=viewer.COMMUNITY_SUMMARY_SORTS["down"]
            ),
        )
        self.assertEqual(
            database.queries[2][1],
            (42, 42, 42, 42, viewer.PAGE_SIZE, viewer.PAGE_SIZE),
        )
        self.assertEqual(context["content_type"], "all")
        self.assertEqual(
            parse_qs(urlsplit(context["pagination"]["next_url"]).query),
            {
                "user": ["Dave@lemmy.nz"],
                "view": ["communities"],
                "sort": ["down"],
                "page": ["3"],
            },
        )

    def test_index_empty_deep_community_page_redirects_to_first_page(self):
        cast, received = self.user_summaries()
        response, _, _ = self.request_index(
            "/?user=Dave%40lemmy.nz&view=communities&sort=name&page=99",
            [cast, received, []],
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            parse_qs(urlsplit(response.headers["Location"]).query),
            {
                "user": ["Dave@lemmy.nz"],
                "view": ["communities"],
                "sort": ["name"],
            },
        )

    def test_index_cast_page_is_clamped_to_available_results(self):
        cast, received = self.user_summaries(filtered_total=150)
        response, database, context = self.request_index(
            "/?user=Dave%40lemmy.nz&page=9999999",
            [cast, received, []],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(context["pagination"]["page"], 2)
        self.assertEqual(database.queries[2][1][-1], viewer.PAGE_SIZE)

    def test_index_local_item_paths_redirect_without_database_lookup(self):
        for path, expected in (
            ("/?item=/post/123", "/item/post/123"),
            ("/?item=/comment/456", "/item/comment/456"),
        ):
            with self.subTest(path=path):
                with patch.object(viewer, "db") as mocked_db:
                    response = self.request_as(lemmy_user_payload(), path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(urlsplit(response.headers["Location"]).path, expected)
                mocked_db.assert_not_called()

    def test_index_activitypub_item_url_uses_lookup_and_redirects(self):
        for kind, item_id in (("post", 123), ("comment", 456)):
            with self.subTest(kind=kind):
                item_url = f"https://remote.example/{kind}/{item_id}/"
                alternate_url = item_url.rstrip("/")
                database = ScriptedDatabase(
                    [{"kind": kind, "item_id": item_id}]
                )
                with patch.object(viewer, "db", return_value=database):
                    response = self.request_as(
                        lemmy_user_payload(),
                        f"/?item={item_url}",
                    )

                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    urlsplit(response.headers["Location"]).path,
                    f"/item/{kind}/{item_id}",
                )
                self.assertEqual(database.queries[0][0], viewer.ITEM_BY_AP_ID_SQL)
                self.assertEqual(
                    database.queries[0][1],
                    (item_url, alternate_url, item_url, alternate_url),
                )

    def test_index_query_timeout_returns_503(self):
        database = ScriptedDatabase([viewer.psycopg.errors.QueryCanceled()])
        with (
            patch.object(viewer, "db", return_value=database),
            patch.object(
                viewer,
                "resolve_user",
                side_effect=lambda cur, username: cur.execute("slow query"),
            ),
        ):
            response = self.request_as(
                lemmy_user_payload(),
                "/?user=Dave%40lemmy.nz",
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn(b"database query took too long", response.data.lower())

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
                "post_local": False,
                "post_hidden": False,
                "post_url": "https://lemmy.nz/post/123",
            }
        )
        self.assertEqual(row["item_vote_path"], "/item/comment/456")
        self.assertEqual(row["item_local_path"], "/comment/456")
        self.assertEqual(row["remote_url"], "https://lemmy.nz/comment/456")
        self.assertEqual(row["community_overview_path"], "/community/support")
        self.assertEqual(row["community_local_path"], "/c/support")
        self.assertIsNone(row["community_remote_url"])
        self.assertEqual(row["post_remote_url"], "https://lemmy.nz/post/123")

    def test_item_community_text_links_only_for_instance_authorized_users(self):
        item = {
            "post_id": 123,
            "post_title": "Example post",
            "content_hidden": False,
            "remote_url": "https://lemmy.ml/post/456",
            "community_display": "!asklemmy@lemmy.ml",
            "community_overview_path": "/community/asklemmy@lemmy.ml",
            "community_local_path": "/c/asklemmy@lemmy.ml",
            "community_remote_url": "https://lemmy.ml/c/asklemmy",
        }
        context = {
            "kind": "post",
            "item": item,
            "rows": [],
            "summary": {"up": 0, "down": 0, "neutral": 0, "total": 0},
            "pagination": {"page_count": 1},
        }

        with viewer.app.test_request_context("/item/post/123"):
            with patch.object(
                viewer,
                "authenticated_user",
                return_value={"username": "Alice", "admin": False},
            ):
                regular_html = viewer.render_template("item.html", **context)
            with patch.object(
                viewer,
                "authenticated_user",
                return_value={"username": "Admin", "admin": True},
            ):
                admin_html = viewer.render_template("item.html", **context)

        overview_link = 'href="/community/asklemmy@lemmy.ml"'
        local_link = 'href="/c/asklemmy@lemmy.ml"'
        self.assertNotIn(overview_link, regular_html)
        self.assertIn(local_link, regular_html)
        self.assertIn(overview_link, admin_html)

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
