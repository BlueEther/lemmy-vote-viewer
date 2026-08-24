# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import timezone
from pathlib import Path

from flask import Flask, abort, g, redirect, render_template, request
import psycopg

from .auth import AuthenticationUnavailable, AuthManager
from .config import load_config
from .database import connect_database
from .links import (
    build_community_overview_url as _build_community_overview_url,
    build_index_url as _build_index_url,
    build_instance_url as _build_instance_url,
    build_item_url as _build_item_url,
    local_community_path,
    make_pagination as _make_pagination,
    normalize_instance_domain,
    parse_community_handle,
    parse_item_search as _parse_item_search,
    parse_page as _parse_page,
    remote_profile_url,
    safe_http_url,
    vote_history_path as _vote_history_path,
)
from .queries import (
    USER_VOTES_SQL,
    USER_VOTES_BY_COMMUNITY_SQL,
    USER_SUMMARY_SQL,
    USER_RECEIVED_SUMMARY_SQL,
    USER_RECEIVED_ITEMS_SQL,
    USER_RECEIVED_ITEMS_BY_COMMUNITY_SQL,
    COMMUNITY_SUMMARY_SORTS,
    USER_COMMUNITY_SUMMARY_SQL,
    COMMUNITY_OVERVIEW_SQL,
    INSTANCE_LOOKUP_SQL,
    INSTANCE_OVERVIEW_SQL,
    INSTANCE_SORTS,
    COMMUNITY_OVERVIEW_SORTS,
    POST_ITEM_SQL,
    COMMENT_ITEM_SQL,
    POST_VOTERS_SQL,
    COMMENT_VOTERS_SQL,
    POST_VOTER_SUMMARY_SQL,
    COMMENT_VOTER_SUMMARY_SQL,
)
from .services import (
    enrich_community_summary as _enrich_community_summary,
    enrich_community_user as _enrich_community_user,
    enrich_instance_user as _enrich_instance_user,
    enrich_item as _enrich_item,
    enrich_user_vote as _enrich_user_vote,
    enrich_voter as _enrich_voter,
    find_user_suggestions as _find_user_suggestions,
    resolve_community,
    resolve_item_search as _resolve_item_search,
    resolve_user,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = load_config(project_root=PROJECT_ROOT)

APP_VERSION = CONFIG.app_version
DB_DSN = CONFIG.database_url
ENABLE_DOMAIN_SEARCH = CONFIG.enable_domain_search
APP_PREFIX = CONFIG.app_prefix
PAGE_SIZE = CONFIG.page_size
INSTANCE_QUERY_TIMEOUT_SECONDS = CONFIG.instance_query_timeout_seconds
INSTANCE_VOTE_WINDOW_DAYS = CONFIG.instance_vote_window_days
TIMEZONE_NAME = CONFIG.timezone_name
DISPLAY_TIMEZONE = CONFIG.display_timezone
LEMMY_BASE_URL = CONFIG.lemmy_base_url
LEMMY_INSTANCE = CONFIG.lemmy_instance
AUTH_PROVIDER = CONFIG.auth_provider
AUTH_SEARCH_REQUIRE = CONFIG.auth_search_require
AUTH_INSTANCE_REQUIRE = CONFIG.auth_instance_require
AUTH_ALLOWED_USERS = CONFIG.auth_allowed_users
AUTH_COOKIE_NAME = CONFIG.auth_cookie_name
AUTH_CACHE_SECONDS = CONFIG.auth_cache_seconds
AUTH_TIMEOUT_SECONDS = CONFIG.auth_timeout_seconds
LEMMY_INTERNAL_URL = CONFIG.lemmy_internal_url
LEMMY_LOGIN_URL = CONFIG.lemmy_login_url

app = Flask(
    __name__,
    template_folder=str(PROJECT_ROOT / "templates"),
    static_folder=str(PROJECT_ROOT / "static"),
)
app.config["VOTE_VIEWER_CONFIG"] = CONFIG
AUTH_MANAGER = AuthManager(CONFIG)

# Temporary compatibility aliases while routes and tests move into their
# owning modules.
_AUTH_CACHE = AUTH_MANAGER.cache
_AUTH_HTTP_OPENER = AUTH_MANAGER.http_opener

ERROR_MESSAGES = {
    400: "The request could not be understood.",
    401: "Log in to the local Lemmy instance to use this viewer.",
    403: "Your Lemmy account does not have permission to view this page.",
    404: "The requested page or item was not found.",
    500: "The viewer encountered an unexpected error.",
    503: "The database query took too long. Please try again later.",
}



@app.after_request
def security_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "script-src 'none'; "
        "connect-src 'none'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )
    return response


@app.context_processor
def inject_app_config():
    try:
        auth_user = authenticated_user()
    except AuthenticationUnavailable:
        auth_user = None
    return {
        "app_prefix": APP_PREFIX,
        "app_version": APP_VERSION,
        "lemmy_base_url": LEMMY_BASE_URL,
        "lemmy_instance": LEMMY_INSTANCE,
        "lemmy_login_url": LEMMY_LOGIN_URL,
        "auth_user": auth_user,
        "viewer_access_enabled": access_requirement_met(
            auth_user, AUTH_SEARCH_REQUIRE
        ),
        "domain_search_enabled": (
            ENABLE_DOMAIN_SEARCH
            and access_requirement_met(auth_user, AUTH_INSTANCE_REQUIRE)
        ),
    }


@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(500)
def handle_error(error):
    status_code = getattr(error, "code", 500)
    return render_error(status_code)


@app.errorhandler(psycopg.errors.QueryCanceled)
def handle_query_timeout(error):
    app.logger.warning(
        "Database query timed out for %s %s",
        request.method,
        request.path,
    )
    return render_error(503)


@app.errorhandler(AuthenticationUnavailable)
def handle_authentication_unavailable(error):
    g.auth_unavailable = True
    app.logger.warning(
        "Lemmy authentication service unavailable for %s %s",
        request.method,
        request.path,
    )
    return render_error(
        503,
        "The Lemmy authentication service is unavailable. Please try again later.",
    )


def render_error(status_code, message=None):
    return (
        render_template(
            "error.html",
            status_code=status_code,
            message=message or ERROR_MESSAGES.get(status_code, ERROR_MESSAGES[500]),
        ),
        status_code,
    )


@app.template_filter("display_datetime")
def display_datetime(value):
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M %Z")


def db():
    return connect_database(DB_DSN)




def cached_auth_user(cache_key):
    return AUTH_MANAGER.cached_auth_user(cache_key)


def cache_auth_user(cache_key, user):
    return AUTH_MANAGER.cache_auth_user(cache_key, user)


def validate_lemmy_token(token):
    return AUTH_MANAGER.validate_lemmy_token(token)


def authenticated_user():
    return AUTH_MANAGER.authenticated_user()


def access_requirement_met(user, requirement):
    return AUTH_MANAGER.access_requirement_met(user, requirement)


def enforce_access(requirement):
    return AUTH_MANAGER.enforce_access(requirement)


def require_access(requirement):
    return AUTH_MANAGER.require_access(requirement)


def parse_item_search(value):
    return _parse_item_search(value, LEMMY_BASE_URL)


def parse_page():
    return _parse_page(request.args.get("page", "1"))


def make_pagination(total, requested_page):
    return _make_pagination(total, requested_page, PAGE_SIZE)


def build_index_url(
    username,
    content_type="all",
    score_filter=None,
    page=1,
    history_view="cast",
    received_sort="date",
    community=None,
    community_sort="total",
):
    return _build_index_url(
        username,
        content_type,
        score_filter,
        page,
        history_view,
        received_sort,
        community,
        community_sort,
        APP_PREFIX,
    )


def build_item_url(kind, item_id, page=1):
    return _build_item_url(kind, item_id, page, APP_PREFIX)


def build_instance_url(domain, sort="total", page=1):
    return _build_instance_url(domain, sort, page, APP_PREFIX)


def build_community_overview_url(handle, sort="total", page=1):
    return _build_community_overview_url(handle, sort, page, APP_PREFIX)


def vote_history_path(handle):
    return _vote_history_path(handle, APP_PREFIX)


def find_user_suggestions(cur, username, community=None, limit=8):
    return _find_user_suggestions(
        cur,
        username,
        community,
        limit,
        APP_PREFIX,
    )


def enrich_user_vote(row):
    return _enrich_user_vote(row, APP_PREFIX)


def enrich_item(item):
    return _enrich_item(item, APP_PREFIX)


def enrich_community_summary(row, user_handle):
    return _enrich_community_summary(row, user_handle, APP_PREFIX)


def enrich_voter(row):
    return _enrich_voter(row, APP_PREFIX)


def enrich_instance_user(row):
    return _enrich_instance_user(row, APP_PREFIX)


def enrich_community_user(row, community_handle):
    return _enrich_community_user(row, community_handle, APP_PREFIX)


def resolve_item_search(item_query):
    return _resolve_item_search(item_query, LEMMY_BASE_URL, db)








@app.route("/")
@require_access(AUTH_SEARCH_REQUIRE)
def index():
    username = request.args.get("user", "").strip()
    if len(username) > 512:
        abort(400)

    item_query = request.args.get("item", "").strip()
    if len(item_query) > 2048:
        abort(400)
    item_error = None
    if item_query:
        item_result, item_error = resolve_item_search(item_query)
        if item_result:
            return redirect(build_item_url(*item_result))

    instance_query = ""
    instance_error = None
    community_overview_query = ""
    community_overview_error = None
    if ENABLE_DOMAIN_SEARCH:
        instance_query = request.args.get("instance", "").strip()
        if len(instance_query) > 255:
            abort(400)
        if instance_query:
            enforce_access(AUTH_INSTANCE_REQUIRE)
            instance_domain = normalize_instance_domain(instance_query)
            if instance_domain:
                return redirect(build_instance_url(instance_domain))
            instance_error = "invalid"

        community_overview_query = request.args.get(
            "community_overview",
            "",
        ).strip()
        if len(community_overview_query) > 512:
            abort(400)
        if community_overview_query:
            enforce_access(AUTH_INSTANCE_REQUIRE)
            parsed_community = parse_community_handle(
                community_overview_query
            )
            if parsed_community:
                community_name, community_domain = parsed_community
                community_handle = f"!{community_name}"
                if community_domain:
                    community_handle += f"@{community_domain}"
                return redirect(
                    build_community_overview_url(community_handle)
                )
            community_overview_error = "invalid"

    content_type = request.args.get("type", "all")
    if content_type not in ("all", "post", "comment"):
        content_type = "all"

    history_view = request.args.get("view", "cast")
    if history_view not in ("cast", "received", "communities"):
        history_view = "cast"

    if history_view == "communities":
        content_type = "all"

    raw_sort = request.args.get("sort", "")
    received_sort = raw_sort or "date"
    if received_sort not in ("date", "top", "bottom"):
        received_sort = "date"

    community_sort = raw_sort or "total"
    if community_sort not in COMMUNITY_SUMMARY_SORTS:
        community_sort = "total"

    raw_score = request.args.get("score", "all")
    score_filter = 1 if raw_score == "1" else -1 if raw_score == "-1" else 0 if raw_score == "0" else None
    if history_view != "cast":
        score_filter = None

    community_query = request.args.get("community", "").strip()
    if len(community_query) > 512:
        abort(400)
    if history_view == "communities":
        community_query = ""
    requested_page = parse_page()

    rows = []
    summary = None
    received_summary = None
    received_items_summary = None
    user = None
    pagination = None
    type_urls = {}
    score_urls = {}
    view_urls = {}
    sort_urls = {}
    community_sort_urls = {}
    community_clear_url = None
    community_error = None
    community = None
    user_suggestions = []

    if username:
        with db() as conn:
            with conn.cursor() as cur:
                user = resolve_user(cur, username)
                if user:
                    user["remote_url"] = remote_profile_url(
                        user["local"], user["actor_id"]
                    )
                    canonical_username = user["handle"]
                    community_id = None
                    if community_query:
                        community, community_error = resolve_community(
                            cur, community_query
                        )
                        if community:
                            community_id = community["id"]
                            community_query = community["handle"]
                        else:
                            community_id = -1

                    cur.execute(
                        USER_SUMMARY_SQL,
                        (
                            user["id"],
                            user["id"],
                            content_type,
                            content_type,
                            score_filter,
                            score_filter,
                            community_id,
                            community_id,
                        ),
                    )
                    summary = cur.fetchone()

                    cur.execute(
                        USER_RECEIVED_SUMMARY_SQL,
                        (
                            community_id,
                            community_id,
                            user["id"],
                            community_id,
                            community_id,
                            user["id"],
                        ),
                    )
                    received_summary = cur.fetchone()

                    if history_view == "cast":
                        pagination = make_pagination(
                            summary["filtered_total"], requested_page
                        )
                        if community_id is None:
                            votes_sql = USER_VOTES_SQL
                            votes_params = (
                                content_type, score_filter,
                                user["id"], user["id"],
                                PAGE_SIZE, pagination["offset"],
                            )
                        else:
                            votes_sql = USER_VOTES_BY_COMMUNITY_SQL
                            votes_params = (
                                content_type, score_filter, community_id,
                                user["id"], user["id"],
                                PAGE_SIZE, pagination["offset"],
                            )
                        cur.execute(votes_sql, votes_params)
                        rows = [enrich_user_vote(row) for row in cur.fetchall()]
                    elif history_view == "received":
                        item_count_key = {
                            "all": "filtered_items",
                            "post": "post_filtered_items",
                            "comment": "comment_filtered_items",
                        }[content_type]
                        received_items_summary = {
                            "total": received_summary["items"],
                            "filtered_total": received_summary[item_count_key],
                        }
                        pagination = make_pagination(
                            received_items_summary["filtered_total"], requested_page
                        )
                        if community_id is None:
                            received_items_sql = USER_RECEIVED_ITEMS_SQL
                            received_items_params = (
                                content_type, received_sort,
                                user["id"], user["id"],
                                PAGE_SIZE, pagination["offset"],
                            )
                        else:
                            received_items_sql = USER_RECEIVED_ITEMS_BY_COMMUNITY_SQL
                            received_items_params = (
                                content_type, received_sort, community_id,
                                user["id"], user["id"],
                                PAGE_SIZE, pagination["offset"],
                            )
                        cur.execute(received_items_sql, received_items_params)
                        rows = [enrich_item(row) for row in cur.fetchall()]
                    else:
                        requested_offset = (requested_page - 1) * PAGE_SIZE
                        community_summary_sql = USER_COMMUNITY_SUMMARY_SQL.format(
                            order_by=COMMUNITY_SUMMARY_SORTS[community_sort]
                        )
                        cur.execute(
                            community_summary_sql,
                            (
                                user["id"],
                                user["id"],
                                user["id"],
                                user["id"],
                                PAGE_SIZE,
                                requested_offset,
                            ),
                        )
                        result_rows = cur.fetchall()
                        if not result_rows and requested_page > 1:
                            return redirect(
                                build_index_url(
                                    canonical_username,
                                    history_view="communities",
                                    community_sort=community_sort,
                                )
                            )
                        community_total = (
                            result_rows[0]["community_count"] if result_rows else 0
                        )
                        pagination = make_pagination(
                            community_total, requested_page
                        )
                        rows = [
                            enrich_community_summary(row, canonical_username)
                            for row in result_rows
                        ]

                    type_urls = {
                        "all": build_index_url(
                            canonical_username, "all", score_filter, 1, history_view,
                            received_sort, community_query,
                        ),
                        "post": build_index_url(
                            canonical_username, "post", score_filter, 1, history_view,
                            received_sort, community_query,
                        ),
                        "comment": build_index_url(
                            canonical_username, "comment", score_filter, 1, history_view,
                            received_sort, community_query,
                        ),
                    }
                    score_urls = {
                        "all": build_index_url(
                            canonical_username, content_type, None,
                            community=community_query,
                        ),
                        "1": build_index_url(
                            canonical_username, content_type, 1,
                            community=community_query,
                        ),
                        "-1": build_index_url(
                            canonical_username, content_type, -1,
                            community=community_query,
                        ),
                        "0": build_index_url(
                            canonical_username, content_type, 0,
                            community=community_query,
                        ),
                    }
                    view_urls = {
                        "cast": build_index_url(
                            canonical_username, content_type, score_filter,
                            community=community_query,
                        ),
                        "received": build_index_url(
                            canonical_username, content_type, None, 1, "received",
                            received_sort, community_query,
                        ),
                        "communities": build_index_url(
                            canonical_username,
                            history_view="communities",
                            community_sort=community_sort,
                        ),
                    }
                    sort_urls = {
                        sort_name: build_index_url(
                            canonical_username, content_type, None, 1, "received",
                            sort_name, community_query,
                        )
                        for sort_name in ("date", "top", "bottom")
                    }
                    community_sort_urls = {
                        sort_name: build_index_url(
                            canonical_username,
                            history_view="communities",
                            community_sort=sort_name,
                        )
                        for sort_name in COMMUNITY_SUMMARY_SORTS
                    }
                    if pagination["has_prev"]:
                        if history_view == "communities":
                            pagination["prev_url"] = build_index_url(
                                canonical_username,
                                page=pagination["prev_page"],
                                history_view="communities",
                                community_sort=community_sort,
                            )
                        else:
                            pagination["prev_url"] = build_index_url(
                                canonical_username,
                                content_type,
                                score_filter,
                                pagination["prev_page"],
                                history_view,
                                received_sort,
                                community_query,
                            )
                    if pagination["has_next"]:
                        if history_view == "communities":
                            pagination["next_url"] = build_index_url(
                                canonical_username,
                                page=pagination["next_page"],
                                history_view="communities",
                                community_sort=community_sort,
                            )
                        else:
                            pagination["next_url"] = build_index_url(
                                canonical_username,
                                content_type,
                                score_filter,
                                pagination["next_page"],
                                history_view,
                                received_sort,
                                community_query,
                            )
                    community_clear_url = build_index_url(
                        canonical_username,
                        content_type,
                        score_filter,
                        1,
                        history_view,
                        received_sort,
                    )
                else:
                    user_suggestions = find_user_suggestions(
                        cur, username, community_query
                    )

    return render_template(
        "index.html",
        username=username,
        item_query=item_query,
        item_error=item_error,
        instance_query=instance_query,
        instance_error=instance_error,
        community_overview_query=community_overview_query,
        community_overview_error=community_overview_error,
        user=user,
        user_suggestions=user_suggestions,
        rows=rows,
        summary=summary,
        received_summary=received_summary,
        received_items_summary=received_items_summary,
        pagination=pagination,
        history_view=history_view,
        received_sort=received_sort,
        content_type=content_type,
        score_filter=score_filter,
        type_urls=type_urls,
        score_urls=score_urls,
        view_urls=view_urls,
        sort_urls=sort_urls,
        community_sort=community_sort,
        community_sort_urls=community_sort_urls,
        community_query=community_query,
        community=community,
        community_error=community_error,
        community_clear_url=community_clear_url,
    )


@app.route("/instance/<domain>")
def instance_overview(domain):
    if not ENABLE_DOMAIN_SEARCH:
        abort(404)
    enforce_access(AUTH_INSTANCE_REQUIRE)

    normalized_domain = normalize_instance_domain(domain)
    if not normalized_domain:
        abort(404)

    sort = request.args.get("sort", "total")
    if sort not in INSTANCE_SORTS:
        sort = "total"
    requested_page = parse_page()

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(INSTANCE_LOOKUP_SQL, (normalized_domain,))
            instance = cur.fetchone()
            if not instance:
                abort(404)

            canonical_domain = normalize_instance_domain(instance["domain"])
            if not canonical_domain:
                abort(404)

            requested_offset = (requested_page - 1) * PAGE_SIZE
            overview_sql = INSTANCE_OVERVIEW_SQL.format(
                order_by=INSTANCE_SORTS[sort],
                vote_window_days=INSTANCE_VOTE_WINDOW_DAYS,
            )
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{INSTANCE_QUERY_TIMEOUT_SECONDS}s",),
            )
            cur.execute(
                overview_sql,
                (
                    instance["id"],
                    requested_offset,
                    requested_offset + PAGE_SIZE,
                ),
            )
            result_rows = cur.fetchall()
            overview = result_rows[0]
            summary = {
                "known_users": overview["known_users"],
                "voting_users": overview["voting_users"],
                "total": overview["summary_total"],
                "up": overview["summary_up"],
                "down": overview["summary_down"],
                "neutral": overview["summary_neutral"],
            }
            pagination = make_pagination(summary["voting_users"], requested_page)
            if pagination["page"] != requested_page:
                return redirect(
                    build_instance_url(canonical_domain, sort, pagination["page"])
                )
            rows = [
                enrich_instance_user(row)
                for row in result_rows
                if row["id"] is not None
            ]

    sort_urls = {
        key: build_instance_url(canonical_domain, key)
        for key in INSTANCE_SORTS
    }
    if pagination["has_prev"]:
        pagination["prev_url"] = build_instance_url(
            canonical_domain, sort, pagination["prev_page"]
        )
    if pagination["has_next"]:
        pagination["next_url"] = build_instance_url(
            canonical_domain, sort, pagination["next_page"]
        )

    return render_template(
        "instance.html",
        instance=instance,
        domain=canonical_domain,
        summary=summary,
        rows=rows,
        sort=sort,
        sort_urls=sort_urls,
        pagination=pagination,
        vote_window_days=INSTANCE_VOTE_WINDOW_DAYS,
    )


@app.route("/community/<community_handle>")
def community_overview(community_handle):
    if not ENABLE_DOMAIN_SEARCH:
        abort(404)
    enforce_access(AUTH_INSTANCE_REQUIRE)

    sort = request.args.get("sort", "total")
    if sort not in COMMUNITY_OVERVIEW_SORTS:
        sort = "total"
    requested_page = parse_page()

    with db() as conn:
        with conn.cursor() as cur:
            community, community_error = resolve_community(
                cur,
                f"!{community_handle}",
            )
            if community_error or not community:
                abort(404)

            canonical_handle = community["handle"]
            community["local_path"] = local_community_path(canonical_handle)
            community["remote_url"] = (
                None
                if community["local"]
                else safe_http_url(community["actor_id"])
            )
            requested_offset = (requested_page - 1) * PAGE_SIZE
            overview_sql = COMMUNITY_OVERVIEW_SQL.format(
                order_by=COMMUNITY_OVERVIEW_SORTS[sort],
                vote_window_days=INSTANCE_VOTE_WINDOW_DAYS,
            )
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{INSTANCE_QUERY_TIMEOUT_SECONDS}s",),
            )
            cur.execute(
                overview_sql,
                (
                    community["id"],
                    community["id"],
                    requested_offset,
                    requested_offset + PAGE_SIZE,
                ),
            )
            result_rows = cur.fetchall()
            overview = result_rows[0]
            summary = {
                "voting_users": overview["voting_users"],
                "total": overview["summary_total"],
                "up": overview["summary_up"],
                "down": overview["summary_down"],
                "neutral": overview["summary_neutral"],
            }
            pagination = make_pagination(
                summary["voting_users"],
                requested_page,
            )
            if pagination["page"] != requested_page:
                return redirect(
                    build_community_overview_url(
                        canonical_handle,
                        sort,
                        pagination["page"],
                    )
                )
            rows = [
                enrich_community_user(row, canonical_handle)
                for row in result_rows
                if row["id"] is not None
            ]

    sort_urls = {
        key: build_community_overview_url(canonical_handle, key)
        for key in COMMUNITY_OVERVIEW_SORTS
    }
    if pagination["has_prev"]:
        pagination["prev_url"] = build_community_overview_url(
            canonical_handle,
            sort,
            pagination["prev_page"],
        )
    if pagination["has_next"]:
        pagination["next_url"] = build_community_overview_url(
            canonical_handle,
            sort,
            pagination["next_page"],
        )

    return render_template(
        "community.html",
        community=community,
        summary=summary,
        rows=rows,
        sort=sort,
        sort_urls=sort_urls,
        pagination=pagination,
        vote_window_days=INSTANCE_VOTE_WINDOW_DAYS,
    )


def item_votes(kind, item_id):
    requested_page = parse_page()
    if kind == "post":
        item_sql, summary_sql, voters_sql = POST_ITEM_SQL, POST_VOTER_SUMMARY_SQL, POST_VOTERS_SQL
    elif kind == "comment":
        item_sql, summary_sql, voters_sql = COMMENT_ITEM_SQL, COMMENT_VOTER_SUMMARY_SQL, COMMENT_VOTERS_SQL
    else:
        abort(404)

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(item_sql, (item_id,))
            item = cur.fetchone()
            if not item:
                abort(404)
            item = enrich_item(item)

            cur.execute(summary_sql, (item_id,))
            summary = cur.fetchone()
            pagination = make_pagination(summary["total"], requested_page)

            cur.execute(voters_sql, (item_id, PAGE_SIZE, pagination["offset"]))
            rows = [enrich_voter(row) for row in cur.fetchall()]

    if pagination["has_prev"]:
        pagination["prev_url"] = build_item_url(kind, item_id, pagination["prev_page"])
    if pagination["has_next"]:
        pagination["next_url"] = build_item_url(kind, item_id, pagination["next_page"])

    return render_template(
        "item.html",
        kind=kind,
        item_id=item_id,
        item=item,
        rows=rows,
        summary=summary,
        pagination=pagination,
    )


@app.route("/item/post/<int:item_id>")
@require_access(AUTH_SEARCH_REQUIRE)
def post_votes(item_id):
    return item_votes("post", item_id)


@app.route("/item/comment/<int:item_id>")
@require_access(AUTH_SEARCH_REQUIRE)
def comment_votes(item_id):
    return item_votes("comment", item_id)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
