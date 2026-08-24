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








from .routes import register_blueprints

register_blueprints(app)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
