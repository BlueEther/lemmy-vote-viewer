# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import timezone
from pathlib import Path

from flask import Flask, g, render_template, request
import psycopg

from .auth import AuthenticationUnavailable, AuthManager
from .config import load_config
from .graph_cache import GraphCache
from .routes import register_blueprints


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = load_config(project_root=PROJECT_ROOT)

app = Flask(
    __name__,
    template_folder=str(PROJECT_ROOT / "templates"),
    static_folder=str(PROJECT_ROOT / "static"),
)
app.config["VOTE_VIEWER_CONFIG"] = CONFIG
app.extensions["vote_viewer_auth"] = AuthManager(CONFIG)
app.extensions["vote_viewer_graph_cache"] = GraphCache(
    "/tmp/lemmy-vote-viewer-graphs.sqlite3",
    CONFIG.user_vote_graph_cache_seconds,
    CONFIG.instance_query_timeout_seconds + 3,
)
app.extensions["vote_viewer_overview_graph_cache"] = GraphCache(
    "/tmp/lemmy-vote-viewer-overview-graphs.sqlite3",
    CONFIG.overview_vote_graph_cache_seconds,
    CONFIG.instance_query_timeout_seconds + 3,
)
app.extensions["vote_viewer_users_overview_cache"] = GraphCache(
    "/tmp/lemmy-vote-viewer-users.sqlite3",
    CONFIG.users_overview_cache_seconds,
    CONFIG.instance_query_timeout_seconds + 3,
    max_entries=64,
)

FEDERATION_HAIKU = (
    "Beyond this node's reach",
    "Federation brings it here",
    "Then it can be seen",
)

ERROR_MESSAGES = {
    400: "The request could not be understood.",
    401: "Log in to the local Lemmy instance to use this viewer.",
    403: "Your Lemmy account does not have permission to view this page.",
    404: FEDERATION_HAIKU,
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
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )
    return response


@app.context_processor
def inject_app_config():
    settings = app.config["VOTE_VIEWER_CONFIG"]
    auth_manager = app.extensions["vote_viewer_auth"]
    feature_requirement = {
        "search": settings.auth_search_require,
        "items": settings.auth_search_require,
        "overviews": settings.auth_instance_require,
        "users": settings.auth_instance_require,
    }.get(request.blueprint)
    if feature_requirement == "disabled":
        auth_user = None
    else:
        try:
            auth_user = auth_manager.authenticated_user()
        except AuthenticationUnavailable:
            auth_user = None
    return {
        "app_prefix": settings.app_prefix,
        "app_version": settings.app_version,
        "lemmy_base_url": settings.lemmy_base_url,
        "lemmy_instance": settings.lemmy_instance,
        "lemmy_login_url": settings.lemmy_login_url,
        "auth_user": auth_user,
        "viewer_access_enabled": auth_manager.access_requirement_met(
            auth_user, settings.auth_search_require
        ),
        "domain_search_enabled": (
            settings.enable_domain_search
            and auth_manager.access_requirement_met(
                auth_user, settings.auth_instance_require
            )
        ),
        "users_overview_enabled": (
            settings.enable_users_overview
            and auth_manager.access_requirement_met(
                auth_user, settings.auth_instance_require
            )
        ),
        "federation_haiku": FEDERATION_HAIKU,
    }


@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(500)
def handle_error(error):
    return render_error(getattr(error, "code", 500))


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
    error_message = message or ERROR_MESSAGES.get(
        status_code,
        ERROR_MESSAGES[500],
    )
    message_paragraphs = (
        error_message
        if isinstance(error_message, tuple)
        else (error_message,)
    )
    return (
        render_template(
            "error.html",
            status_code=status_code,
            message_paragraphs=message_paragraphs,
        ),
        status_code,
    )


@app.template_filter("display_datetime")
def display_datetime(value):
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CONFIG.display_timezone).strftime("%Y-%m-%d %H:%M %Z")


register_blueprints(app)


def create_app():
    """Return the configured Flask application."""
    return app
