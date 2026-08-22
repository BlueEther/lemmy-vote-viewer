# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import hashlib
import json
import math
import os
import re
import threading
import time
from datetime import timezone
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, abort, g, redirect, render_template, request
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)


class AuthenticationUnavailable(Exception):
    pass


APP_VERSION = Path(__file__).with_name("VERSION").read_text(encoding="utf-8").strip()
if not APP_VERSION:
    raise RuntimeError("VERSION file is empty")
DB_DSN = os.environ["DATABASE_URL"]


def boolean_env(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise RuntimeError(f"{name} must be either true or false")


ENABLE_DOMAIN_SEARCH = boolean_env("ENABLE_DOMAIN_SEARCH", False)

ERROR_MESSAGES = {
    400: "The request could not be understood.",
    401: "Log in to the local Lemmy instance to use this viewer.",
    403: "Your Lemmy account does not have permission to view this page.",
    404: "The requested page or item was not found.",
    500: "The viewer encountered an unexpected error.",
    503: "The database query took too long. Please try again later.",
}

_raw_prefix = os.environ.get("APP_PREFIX", "/votes").strip()
APP_PREFIX = "" if _raw_prefix in ("", "/") else "/" + _raw_prefix.strip("/")

try:
    PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "100"))
except ValueError:
    PAGE_SIZE = 100
PAGE_SIZE = max(20, min(PAGE_SIZE, 250))

try:
    INSTANCE_QUERY_TIMEOUT_SECONDS = int(
        os.environ.get("INSTANCE_QUERY_TIMEOUT_SECONDS", "12")
    )
except ValueError:
    INSTANCE_QUERY_TIMEOUT_SECONDS = 12
INSTANCE_QUERY_TIMEOUT_SECONDS = max(5, min(INSTANCE_QUERY_TIMEOUT_SECONDS, 12))

try:
    INSTANCE_VOTE_WINDOW_DAYS = int(
        os.environ.get("INSTANCE_VOTE_WINDOW_DAYS", "30")
    )
except ValueError:
    INSTANCE_VOTE_WINDOW_DAYS = 30
INSTANCE_VOTE_WINDOW_DAYS = max(1, min(INSTANCE_VOTE_WINDOW_DAYS, 365))

TIMEZONE_NAME = os.environ.get("TIMEZONE", "UTC").strip() or "UTC"
try:
    DISPLAY_TIMEZONE = ZoneInfo(TIMEZONE_NAME)
except (ValueError, ZoneInfoNotFoundError) as exc:
    raise RuntimeError(f"Invalid TIMEZONE: {TIMEZONE_NAME}") from exc


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
    return psycopg.connect(
        DB_DSN,
        row_factory=dict_row,
        connect_timeout=5,
        options=(
            "-c default_transaction_read_only=on "
            "-c statement_timeout=5000 "
            "-c idle_in_transaction_session_timeout=10000"
        ),
    )


def safe_http_url(value):
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    return value


def lemmy_instance_config(value):
    url = safe_http_url(value.strip()) if value else None
    if not url:
        return None, None
    try:
        parsed = urlsplit(url)
        if parsed.username or parsed.password:
            return None, None
        base_url = f"{parsed.scheme.lower()}://{parsed.netloc}"
        instance = (parsed.hostname or "").lower().rstrip(".")
        return (base_url, instance) if instance else (None, None)
    except ValueError:
        return None, None


LEMMY_BASE_URL, LEMMY_INSTANCE = lemmy_instance_config(
    os.environ.get("LEMMY_BASE_URL", "")
)


AUTH_REQUIREMENTS = {"none", "login", "allowlist", "admin"}


def auth_requirement_env(name, default="none"):
    requirement = os.environ.get(name, default).strip().lower()
    if requirement not in AUTH_REQUIREMENTS:
        choices = ", ".join(sorted(AUTH_REQUIREMENTS))
        raise RuntimeError(f"{name} must be one of: {choices}")
    return requirement


AUTH_PROVIDER = os.environ.get("AUTH_PROVIDER", "none").strip().lower()
if AUTH_PROVIDER not in ("none", "lemmy"):
    raise RuntimeError("AUTH_PROVIDER must be either none or lemmy")

AUTH_SEARCH_REQUIRE = auth_requirement_env("AUTH_SEARCH_REQUIRE")
AUTH_INSTANCE_REQUIRE = auth_requirement_env("AUTH_INSTANCE_REQUIRE")
if AUTH_PROVIDER == "none" and (
    AUTH_SEARCH_REQUIRE != "none" or AUTH_INSTANCE_REQUIRE != "none"
):
    raise RuntimeError(
        "AUTH_PROVIDER must be lemmy when an authentication requirement is enabled"
    )

AUTH_ALLOWED_USERS = frozenset(
    username.strip().casefold()
    for username in os.environ.get("AUTH_ALLOWED_USERS", "").split(",")
    if username.strip()
)
AUTH_COOKIE_NAME = os.environ.get("AUTH_COOKIE_NAME", "jwt").strip() or "jwt"

try:
    AUTH_CACHE_SECONDS = int(os.environ.get("AUTH_CACHE_SECONDS", "60"))
except ValueError:
    AUTH_CACHE_SECONDS = 60
AUTH_CACHE_SECONDS = max(0, min(AUTH_CACHE_SECONDS, 300))

try:
    AUTH_TIMEOUT_SECONDS = float(os.environ.get("AUTH_TIMEOUT_SECONDS", "3"))
except ValueError:
    AUTH_TIMEOUT_SECONDS = 3.0
AUTH_TIMEOUT_SECONDS = max(1.0, min(AUTH_TIMEOUT_SECONDS, 10.0))

_auth_internal_url = os.environ.get("LEMMY_INTERNAL_URL", "").strip()
LEMMY_INTERNAL_URL, _ = lemmy_instance_config(
    _auth_internal_url or LEMMY_BASE_URL or ""
)
if AUTH_PROVIDER == "lemmy" and not LEMMY_INTERNAL_URL:
    raise RuntimeError(
        "LEMMY_INTERNAL_URL or LEMMY_BASE_URL is required for Lemmy authentication"
    )

LEMMY_LOGIN_URL = f"{LEMMY_BASE_URL}/login" if LEMMY_BASE_URL else None
_AUTH_CACHE = {}
_AUTH_CACHE_LOCK = threading.Lock()
_AUTH_CACHE_MAX_ENTRIES = 1024


class NoAuthRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_AUTH_HTTP_OPENER = build_opener(NoAuthRedirectHandler())


def cached_auth_user(cache_key):
    if AUTH_CACHE_SECONDS == 0:
        return False, None
    now = time.monotonic()
    with _AUTH_CACHE_LOCK:
        cached = _AUTH_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return True, cached[1]
        if cached:
            _AUTH_CACHE.pop(cache_key, None)
    return False, None


def cache_auth_user(cache_key, user):
    if AUTH_CACHE_SECONDS == 0:
        return
    now = time.monotonic()
    with _AUTH_CACHE_LOCK:
        if len(_AUTH_CACHE) >= _AUTH_CACHE_MAX_ENTRIES:
            expired_keys = [
                key for key, (expires_at, _) in _AUTH_CACHE.items()
                if expires_at <= now
            ]
            for key in expired_keys:
                _AUTH_CACHE.pop(key, None)
        if len(_AUTH_CACHE) >= _AUTH_CACHE_MAX_ENTRIES:
            _AUTH_CACHE.pop(next(iter(_AUTH_CACHE)))
        _AUTH_CACHE[cache_key] = (now + AUTH_CACHE_SECONDS, user)


def validate_lemmy_token(token):
    cache_key = hashlib.sha256(token.encode("utf-8")).digest()
    cache_hit, user = cached_auth_user(cache_key)
    if cache_hit:
        return user

    auth_request = Request(
        f"{LEMMY_INTERNAL_URL}/api/v3/site",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": f"lemmy-vote-viewer/{APP_VERSION}",
        },
    )
    try:
        with _AUTH_HTTP_OPENER.open(
            auth_request, timeout=AUTH_TIMEOUT_SECONDS
        ) as response:
            response_body = response.read(1_048_577)
            if len(response_body) > 1_048_576:
                raise AuthenticationUnavailable
            payload = json.loads(response_body)
    except HTTPError as exc:
        if exc.code in (400, 401, 403):
            cache_auth_user(cache_key, None)
            return None
        raise AuthenticationUnavailable from exc
    except (
        URLError,
        TimeoutError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        raise AuthenticationUnavailable from exc

    my_user = payload.get("my_user") if isinstance(payload, dict) else None
    local_user_view = (
        my_user.get("local_user_view") if isinstance(my_user, dict) else None
    )
    local_user = (
        local_user_view.get("local_user")
        if isinstance(local_user_view, dict)
        else None
    )
    person = (
        local_user_view.get("person")
        if isinstance(local_user_view, dict)
        else None
    )
    if not isinstance(local_user, dict) or not isinstance(person, dict):
        cache_auth_user(cache_key, None)
        return None

    username = person.get("name")
    if (
        not isinstance(username, str)
        or not username
        or person.get("banned", False)
        or person.get("deleted", False)
    ):
        cache_auth_user(cache_key, None)
        return None

    user = {
        "username": username,
        "admin": bool(local_user.get("admin", False)),
    }
    cache_auth_user(cache_key, user)
    return user


def authenticated_user():
    if AUTH_PROVIDER != "lemmy":
        return None
    if getattr(g, "auth_unavailable", False):
        raise AuthenticationUnavailable
    if hasattr(g, "auth_user"):
        return g.auth_user
    token = request.cookies.get(AUTH_COOKIE_NAME, "")
    if not token or len(token) > 4096 or "\n" in token or "\r" in token:
        g.auth_user = None
        return None
    try:
        g.auth_user = validate_lemmy_token(token)
    except AuthenticationUnavailable:
        g.auth_unavailable = True
        raise
    return g.auth_user


def access_requirement_met(user, requirement):
    if requirement == "none":
        return True
    if not user:
        return False
    if requirement == "login":
        return True
    if requirement == "admin":
        return user["admin"]
    return user["admin"] or user["username"].casefold() in AUTH_ALLOWED_USERS


def enforce_access(requirement):
    if requirement == "none":
        return None
    user = authenticated_user()
    if not user:
        abort(401)
    if not access_requirement_met(user, requirement):
        abort(403)
    return user


def require_access(requirement):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            enforce_access(requirement)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def actor_domain(actor_id):
    url = safe_http_url(actor_id)
    if not url:
        return None
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".") or None
    except ValueError:
        return None


def make_handle(name, local, actor_id):
    if not name:
        return None
    if local:
        return name
    domain = actor_domain(actor_id)
    return f"{name}@{domain}" if domain else name


def local_profile_path(handle):
    if not handle:
        return None
    return "/u/" + quote(handle, safe="@._~-")


LOCAL_ITEM_PATH = re.compile(r"^/(post|comment)/(\d+)/?$")


def parse_local_item_path(path):
    match = LOCAL_ITEM_PATH.fullmatch(path)
    if not match:
        return None
    item_id = int(match.group(2))
    return (match.group(1), item_id) if item_id > 0 else None


def url_origin(parsed):
    try:
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return None
    default_port = 80 if scheme == "http" else 443 if scheme == "https" else None
    return scheme, host, port or default_port


def parse_item_search(value):
    value = value.strip()
    if not value:
        return None

    if value.startswith("/"):
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            return None
        local_item = parse_local_item_path(parsed.path)
        return {"local_item": local_item} if local_item else None

    url = safe_http_url(value)
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.username or parsed.password or url_origin(parsed) is None:
            return None
    except ValueError:
        return None

    clean_url = urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, "")
    )
    alternate_url = clean_url[:-1] if clean_url.endswith("/") else clean_url

    local_item = None
    if LEMMY_BASE_URL:
        base = urlsplit(LEMMY_BASE_URL)
        if url_origin(parsed) == url_origin(base):
            local_item = parse_local_item_path(parsed.path)

    return {
        "local_item": local_item,
        "ap_urls": (clean_url, alternate_url),
    }


def parse_page():
    try:
        page = int(request.args.get("page", "1"))
    except (TypeError, ValueError):
        return 1
    return max(1, min(page, 1_000_000))


def make_pagination(total, requested_page):
    page_count = max(1, math.ceil(total / PAGE_SIZE)) if total else 1
    page = min(max(1, requested_page), page_count)
    return {
        "page": page,
        "page_count": page_count,
        "total": total,
        "offset": (page - 1) * PAGE_SIZE,
        "has_prev": page > 1,
        "has_next": page < page_count,
        "prev_page": page - 1,
        "next_page": page + 1,
    }


def build_index_url(
    username,
    content_type="all",
    score_filter=None,
    page=1,
    history_view="cast",
    received_sort="date",
):
    params = {"user": username}
    if history_view == "received":
        params["view"] = "received"
        if received_sort != "date":
            params["sort"] = received_sort
    if content_type != "all":
        params["type"] = content_type
    if score_filter is not None:
        params["score"] = str(score_filter)
    if page > 1:
        params["page"] = str(page)
    return f"{APP_PREFIX}/?{urlencode(params)}"


def build_item_url(kind, item_id, page=1):
    path = f"{APP_PREFIX}/item/{kind}/{item_id}"
    return f"{path}?{urlencode({'page': page})}" if page > 1 else path


def build_instance_url(domain, sort="total", page=1):
    path = f"{APP_PREFIX}/instance/{quote(domain, safe='.-')}"
    params = {}
    if sort != "total":
        params["sort"] = sort
    if page > 1:
        params["page"] = str(page)
    return f"{path}?{urlencode(params)}" if params else path


def vote_history_path(handle):
    return build_index_url(handle, "all", None, 1) if handle else None


INSTANCE_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_instance_domain(value):
    value = value.strip().lower().rstrip(".")
    if value.startswith("@"):
        value = value[1:]
    if not value or len(value) > 253 or "/" in value or "@" in value:
        return None
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if len(value) > 253:
        return None
    labels = value.split(".")
    if not labels or any(not INSTANCE_LABEL.fullmatch(label) for label in labels):
        return None
    return value


def resolve_user(cur, username):
    username = username.strip()
    if username.startswith("@"):
        username = username[1:]
    if not username or len(username) > 512:
        return None

    if "@" in username:
        name, domain = username.rsplit("@", 1)
        name = name.strip()
        domain = domain.strip().lower().rstrip(".")
        if not name or not domain or "/" in domain or len(name) > 255:
            return None

        cur.execute(
            """
            SELECT id, name, display_name, local, actor_id, instance_id, deleted
            FROM person
            WHERE lower(name) = lower(%s)
              AND local = false
              AND deleted = false
            """,
            (name,),
        )
        for row in cur.fetchall():
            if actor_domain(row["actor_id"]) == domain:
                row["instance_domain"] = domain
                row["handle"] = make_handle(row["name"], row["local"], row["actor_id"])
                row["profile_path"] = local_profile_path(row["handle"])
                return row
        return None

    cur.execute(
        """
        SELECT id, name, display_name, local, actor_id, instance_id, deleted
        FROM person
        WHERE lower(name) = lower(%s)
          AND local = true
          AND deleted = false
        LIMIT 1
        """,
        (username,),
    )
    row = cur.fetchone()
    if row:
        row["instance_domain"] = actor_domain(row["actor_id"])
        row["handle"] = make_handle(row["name"], row["local"], row["actor_id"])
        row["profile_path"] = local_profile_path(row["handle"])
    return row


USER_SUGGESTIONS_SQL = """
SELECT p.id, p.name, p.display_name, p.local, p.actor_id
FROM person p
LEFT JOIN instance i ON i.id = p.instance_id
WHERE p.deleted = false
  AND p.name ILIKE %s ESCAPE '\\'
  AND (
      %s::text IS NULL
      OR (
          p.local = false
          AND i.domain ILIKE %s ESCAPE '\\'
      )
  )
ORDER BY
    CASE WHEN lower(p.name) = lower(%s) THEN 0 ELSE 1 END,
    p.local DESC,
    lower(p.name),
    p.id
LIMIT %s
"""


def parse_user_suggestion_input(username):
    username = username.strip()
    if username.startswith("@"):
        username = username[1:]

    if "@" in username:
        name_prefix, domain_prefix = username.rsplit("@", 1)
        name_prefix = name_prefix.strip()
        domain_prefix = domain_prefix.strip().lower().rstrip(".")
        if "/" in domain_prefix or len(domain_prefix) > 255:
            return None
    else:
        name_prefix = username
        domain_prefix = None

    if len(name_prefix) < 2 or len(name_prefix) > 255:
        return None
    return name_prefix, domain_prefix


def like_prefix_pattern(value):
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        + "%"
    )


def find_user_suggestions(cur, username, limit=8):
    parsed = parse_user_suggestion_input(username)
    if not parsed:
        return []
    name_prefix, domain_prefix = parsed
    name_pattern = like_prefix_pattern(name_prefix)
    domain_pattern = like_prefix_pattern(domain_prefix) if domain_prefix is not None else None

    cur.execute(
        USER_SUGGESTIONS_SQL,
        (
            name_pattern,
            domain_prefix,
            domain_pattern,
            name_prefix,
            limit,
        ),
    )

    suggestions = []
    for row in cur.fetchall():
        handle = make_handle(row["name"], row["local"], row["actor_id"])
        if not handle:
            continue
        suggestions.append(
            {
                "display_name": row["display_name"] or row["name"],
                "handle": handle,
                "vote_path": vote_history_path(handle),
            }
        )
    return suggestions


USER_VOTES_SQL = """
WITH filters AS (
    SELECT
        %s::text AS content_type,
        %s::smallint AS score_filter
),
eligible_votes AS MATERIALIZED (
    SELECT
        pl.published AS voted_at,
        'post'::text AS type,
        pl.score,
        pl.post_id,
        NULL::integer AS comment_id
    FROM post_like pl
    JOIN post p ON p.id = pl.post_id
    JOIN community c ON c.id = p.community_id
    CROSS JOIN filters f
    WHERE pl.person_id = %s
      AND (f.content_type = 'all' OR f.content_type = 'post')
      AND (f.score_filter IS NULL OR pl.score = f.score_filter)
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false

    UNION ALL

    SELECT
        cl.published AS voted_at,
        'comment'::text AS type,
        cl.score,
        cl.post_id,
        cl.comment_id
    FROM comment_like cl
    JOIN post p ON p.id = cl.post_id
    JOIN community c ON c.id = p.community_id
    CROSS JOIN filters f
    WHERE cl.person_id = %s
      AND (f.content_type = 'all' OR f.content_type = 'comment')
      AND (f.score_filter IS NULL OR cl.score = f.score_filter)
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false
),
paged_votes AS MATERIALIZED (
    SELECT *
    FROM eligible_votes
    ORDER BY voted_at DESC, type, post_id, comment_id
    LIMIT %s OFFSET %s
)
SELECT
    pv.voted_at,
    pv.type,
    pv.score,
    pv.post_id,
    pv.comment_id,
    CASE WHEN p.deleted THEN '[deleted post]'
         WHEN p.removed THEN '[removed post]'
         ELSE p.name END AS post_title,
    CASE WHEN pv.type = 'post' THEN NULL
         WHEN p.deleted OR p.removed THEN '[comment on unavailable post]'
         WHEN cm.deleted THEN '[deleted comment]'
         WHEN cm.removed THEN '[removed comment]'
         ELSE cm.content END AS comment_content,
    c.name AS community_name,
    c.title AS community_title,
    c.local AS community_local,
    c.actor_id AS community_url,
    CASE
        WHEN p.deleted OR p.removed OR author.deleted
          OR (pv.type = 'comment' AND (cm.deleted OR cm.removed))
        THEN NULL
        ELSE author.name
    END AS author_name,
    CASE
        WHEN p.deleted OR p.removed OR author.deleted
          OR (pv.type = 'comment' AND (cm.deleted OR cm.removed))
        THEN NULL
        ELSE author.display_name
    END AS author_display_name,
    author.local AS author_local,
    author.actor_id AS author_url,
    CASE WHEN pv.type = 'post' THEN p.ap_id ELSE cm.ap_id END AS content_url,
    CASE WHEN pv.type = 'post' THEN p.local ELSE cm.local END AS item_local,
    (
        p.deleted OR p.removed
        OR (pv.type = 'comment' AND (cm.deleted OR cm.removed))
    ) AS content_hidden,
    (p.deleted OR p.removed) AS post_hidden
FROM paged_votes pv
JOIN post p ON p.id = pv.post_id
JOIN community c ON c.id = p.community_id
LEFT JOIN comment cm
    ON pv.type = 'comment'
   AND cm.id = pv.comment_id
JOIN person author
    ON author.id = CASE
        WHEN pv.type = 'post' THEN p.creator_id
        ELSE cm.creator_id
    END
ORDER BY pv.voted_at DESC, pv.type, pv.post_id, pv.comment_id
"""


USER_SUMMARY_SQL = """
WITH votes AS (
    SELECT pl.score, 'post'::text AS type
    FROM post_like pl
    JOIN post p ON p.id = pl.post_id
    JOIN community c ON c.id = p.community_id
    WHERE pl.person_id = %s
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false

    UNION ALL

    SELECT cl.score, 'comment'::text AS type
    FROM comment_like cl
    JOIN post p ON p.id = cl.post_id
    JOIN community c ON c.id = p.community_id
    WHERE cl.person_id = %s
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false
)
SELECT
    COUNT(*)::integer AS total,
    COUNT(*) FILTER (WHERE score > 0)::integer AS up,
    COUNT(*) FILTER (WHERE score < 0)::integer AS down,
    COUNT(*) FILTER (WHERE score = 0)::integer AS neutral,
    COUNT(*) FILTER (WHERE type = 'post')::integer AS posts,
    COUNT(*) FILTER (WHERE type = 'comment')::integer AS comments,
    COUNT(*) FILTER (
        WHERE (%s::text = 'all' OR type = %s::text)
          AND (%s::smallint IS NULL OR score = %s::smallint)
    )::integer AS filtered_total
FROM votes
"""


USER_RECEIVED_SUMMARY_SQL = """
WITH received_by_type AS (
    SELECT
        'post'::text AS type,
        COALESCE(SUM(pa.upvotes), 0)::bigint AS up,
        COALESCE(SUM(pa.downvotes), 0)::bigint AS down,
        COUNT(*) FILTER (
            WHERE pa.upvotes + pa.downvotes > 0
        )::bigint AS items
    FROM post_aggregates pa
    JOIN community c ON c.id = pa.community_id
    WHERE pa.creator_id = %s
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false

    UNION ALL

    SELECT
        'comment'::text AS type,
        COALESCE(SUM(ca.upvotes), 0)::bigint AS up,
        COALESCE(SUM(ca.downvotes), 0)::bigint AS down,
        COUNT(*) FILTER (
            WHERE ca.upvotes + ca.downvotes > 0
        )::bigint AS items
    FROM comment cm
    JOIN comment_aggregates ca ON ca.comment_id = cm.id
    JOIN post p ON p.id = cm.post_id
    JOIN community c ON c.id = p.community_id
    WHERE cm.creator_id = %s
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false
)
SELECT
    COALESCE(SUM(up + down), 0)::bigint AS total,
    COALESCE(SUM(up), 0)::bigint AS up,
    COALESCE(SUM(down), 0)::bigint AS down,
    0::bigint AS neutral,
    COALESCE(SUM(up + down) FILTER (WHERE type = 'post'), 0)::bigint AS posts,
    COALESCE(SUM(up + down) FILTER (WHERE type = 'comment'), 0)::bigint AS comments,
    COALESCE(SUM(items), 0)::bigint AS items,
    COALESCE(SUM(items) FILTER (WHERE type = 'post'), 0)::bigint AS post_items,
    COALESCE(SUM(items) FILTER (WHERE type = 'comment'), 0)::bigint AS comment_items
FROM received_by_type
"""


USER_RECEIVED_ITEMS_SQL = """
WITH filters AS (
    SELECT %s::text AS content_type, %s::text AS received_sort
),
eligible_items AS MATERIALIZED (
    SELECT
        pa.published AS published_at,
        'post'::text AS type,
        pa.upvotes,
        pa.downvotes,
        pa.post_id,
        NULL::integer AS comment_id,
        CASE f.received_sort
            WHEN 'top' THEN pa.upvotes - pa.downvotes
            WHEN 'bottom' THEN pa.downvotes - pa.upvotes
            ELSE EXTRACT(EPOCH FROM pa.published)
        END AS sort_value
    FROM post_aggregates pa
    JOIN community c ON c.id = pa.community_id
    CROSS JOIN filters f
    WHERE pa.creator_id = %s
      AND (f.content_type = 'all' OR f.content_type = 'post')
      AND pa.upvotes + pa.downvotes > 0
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false

    UNION ALL

    SELECT
        ca.published AS published_at,
        'comment'::text AS type,
        ca.upvotes,
        ca.downvotes,
        cm.post_id,
        ca.comment_id,
        CASE f.received_sort
            WHEN 'top' THEN ca.upvotes - ca.downvotes
            WHEN 'bottom' THEN ca.downvotes - ca.upvotes
            ELSE EXTRACT(EPOCH FROM ca.published)
        END AS sort_value
    FROM comment cm
    JOIN comment_aggregates ca ON ca.comment_id = cm.id
    JOIN post p ON p.id = cm.post_id
    JOIN community c ON c.id = p.community_id
    CROSS JOIN filters f
    WHERE cm.creator_id = %s
      AND (f.content_type = 'all' OR f.content_type = 'comment')
      AND ca.upvotes + ca.downvotes > 0
      AND c.visibility = 'Public'
      AND c.deleted = false
      AND c.removed = false
),
paged_items AS MATERIALIZED (
    SELECT *
    FROM eligible_items
    ORDER BY sort_value DESC, published_at DESC, type, post_id, comment_id
    LIMIT %s OFFSET %s
)
SELECT
    pi.published_at,
    pi.type,
    (pi.upvotes + pi.downvotes)::bigint AS total,
    pi.upvotes,
    pi.downvotes,
    pi.post_id,
    pi.comment_id,
    CASE WHEN p.deleted THEN '[deleted post]'
         WHEN p.removed THEN '[removed post]'
         ELSE p.name END AS post_title,
    CASE WHEN pi.type = 'post' THEN NULL
         WHEN p.deleted OR p.removed THEN '[comment on unavailable post]'
         WHEN cm.deleted THEN '[deleted comment]'
         WHEN cm.removed THEN '[removed comment]'
         ELSE cm.content END AS comment_content,
    c.name AS community_name,
    c.title AS community_title,
    c.local AS community_local,
    c.actor_id AS community_url,
    CASE WHEN pi.type = 'post' THEN p.ap_id ELSE cm.ap_id END AS content_url,
    CASE WHEN pi.type = 'post' THEN p.local ELSE cm.local END AS item_local,
    (
        p.deleted OR p.removed
        OR (pi.type = 'comment' AND (cm.deleted OR cm.removed))
    ) AS content_hidden,
    (p.deleted OR p.removed) AS post_hidden
FROM paged_items pi
JOIN post p ON p.id = pi.post_id
JOIN community c ON c.id = p.community_id
LEFT JOIN comment cm
    ON pi.type = 'comment'
   AND cm.id = pi.comment_id
ORDER BY pi.sort_value DESC, pi.published_at DESC, pi.type, pi.post_id, pi.comment_id
"""


ITEM_BY_AP_ID_SQL = """
SELECT 'post'::text AS kind, p.id AS item_id
FROM post p
JOIN community c ON c.id = p.community_id
WHERE p.ap_id IN (%s, %s)
  AND c.visibility = 'Public'
  AND c.deleted = false
  AND c.removed = false

UNION ALL

SELECT 'comment'::text AS kind, cm.id AS item_id
FROM comment cm
JOIN post p ON p.id = cm.post_id
JOIN community c ON c.id = p.community_id
WHERE cm.ap_id IN (%s, %s)
  AND c.visibility = 'Public'
  AND c.deleted = false
  AND c.removed = false
LIMIT 1
"""


INSTANCE_LOOKUP_SQL = """
SELECT id, domain
FROM instance
WHERE domain = %s
LIMIT 1
"""


INSTANCE_OVERVIEW_SQL = """
WITH target_instance AS MATERIALIZED (
    SELECT %s::integer AS id
),
source_votes AS (
    SELECT pl.person_id, pl.score, pl.published AS voted_at
    FROM post_like pl
    JOIN person pe ON pe.id = pl.person_id
    WHERE pe.instance_id = (SELECT id FROM target_instance)
      AND pe.deleted = false
      AND pl.published >= CURRENT_TIMESTAMP - INTERVAL '{vote_window_days} days'

    UNION ALL

    SELECT cl.person_id, cl.score, cl.published AS voted_at
    FROM comment_like cl
    JOIN person pe ON pe.id = cl.person_id
    WHERE pe.instance_id = (SELECT id FROM target_instance)
      AND pe.deleted = false
      AND cl.published >= CURRENT_TIMESTAMP - INTERVAL '{vote_window_days} days'
),
vote_totals AS MATERIALIZED (
    SELECT
        person_id,
        COUNT(*)::bigint AS total,
        COUNT(*) FILTER (WHERE score > 0)::bigint AS up,
        COUNT(*) FILTER (WHERE score < 0)::bigint AS down,
        COUNT(*) FILTER (WHERE score = 0)::bigint AS neutral,
        MAX(voted_at) AS latest_vote
    FROM source_votes
    GROUP BY person_id
),
summary AS (
    SELECT
        (
            SELECT COUNT(*)
            FROM person pe
            WHERE pe.instance_id = (SELECT id FROM target_instance)
              AND pe.deleted = false
        ) AS known_users,
        COUNT(*) AS voting_users,
        COALESCE(SUM(total), 0)::bigint AS total,
        COALESCE(SUM(up), 0)::bigint AS up,
        COALESCE(SUM(down), 0)::bigint AS down,
        COALESCE(SUM(neutral), 0)::bigint AS neutral
    FROM vote_totals
),
ranked_users AS (
    SELECT
        pe.id,
        pe.name,
        pe.display_name,
        pe.local,
        pe.actor_id,
        vt.total,
        vt.up,
        vt.down,
        vt.neutral,
        vt.latest_vote,
        ROW_NUMBER() OVER (ORDER BY {order_by}) AS sort_position
    FROM vote_totals vt
    JOIN person pe ON pe.id = vt.person_id
),
paged_users AS (
    SELECT *
    FROM ranked_users
    WHERE sort_position > %s
      AND sort_position <= %s
)
SELECT
    summary.known_users,
    summary.voting_users,
    summary.total AS summary_total,
    summary.up AS summary_up,
    summary.down AS summary_down,
    summary.neutral AS summary_neutral,
    pu.id,
    pu.name,
    pu.display_name,
    pu.local,
    pu.actor_id,
    pu.total,
    pu.up,
    pu.down,
    pu.neutral,
    pu.latest_vote,
    pu.sort_position
FROM summary
LEFT JOIN paged_users pu ON true
ORDER BY pu.sort_position
"""


INSTANCE_SORTS = {
    "total": "vt.total DESC, lower(pe.name), pe.id",
    "down": "vt.down DESC, vt.total DESC, lower(pe.name), pe.id",
    "down_ratio": (
        "CASE WHEN vt.total >= 10 "
        "THEN vt.down::numeric / vt.total ELSE -1 END DESC, "
        "vt.total DESC, lower(pe.name), pe.id"
    ),
    "up": "vt.up DESC, vt.total DESC, lower(pe.name), pe.id",
    "recent": "vt.latest_vote DESC, lower(pe.name), pe.id",
    "username": "lower(pe.name), pe.id",
}


POST_ITEM_SQL = """
SELECT
    p.id AS post_id,
    CASE WHEN p.deleted THEN '[deleted post]'
         WHEN p.removed THEN '[removed post]'
         ELSE p.name END AS post_title,
    p.ap_id AS content_url,
    p.local AS item_local,
    (p.deleted OR p.removed) AS content_hidden,
    (p.deleted OR p.removed) AS post_hidden,
    c.name AS community_name,
    c.title AS community_title,
    c.local AS community_local,
    c.actor_id AS community_url
FROM post p
JOIN community c ON c.id = p.community_id
WHERE p.id = %s
  AND c.visibility = 'Public'
  AND c.deleted = false
  AND c.removed = false
LIMIT 1
"""


COMMENT_ITEM_SQL = """
SELECT
    cm.id AS comment_id,
    CASE WHEN p.deleted OR p.removed THEN '[comment on unavailable post]'
         WHEN cm.deleted THEN '[deleted comment]'
         WHEN cm.removed THEN '[removed comment]'
         ELSE cm.content END AS comment_content,
    cm.ap_id AS content_url,
    cm.local AS item_local,
    (p.deleted OR p.removed OR cm.deleted OR cm.removed) AS content_hidden,
    (p.deleted OR p.removed) AS post_hidden,
    p.id AS post_id,
    CASE WHEN p.deleted THEN '[deleted post]'
         WHEN p.removed THEN '[removed post]'
         ELSE p.name END AS post_title,
    c.name AS community_name,
    c.title AS community_title,
    c.local AS community_local,
    c.actor_id AS community_url
FROM comment cm
JOIN post p ON p.id = cm.post_id
JOIN community c ON c.id = p.community_id
WHERE cm.id = %s
  AND c.visibility = 'Public'
  AND c.deleted = false
  AND c.removed = false
LIMIT 1
"""


POST_VOTERS_SQL = """
SELECT
    pl.published AS voted_at,
    pl.score,
    voter.id AS voter_id,
    voter.name AS voter_name,
    voter.display_name AS voter_display_name,
    voter.local AS voter_local,
    voter.actor_id AS voter_url
FROM post_like pl
JOIN person voter ON voter.id = pl.person_id
WHERE pl.post_id = %s
  AND voter.deleted = false
ORDER BY pl.score DESC, lower(voter.name), voter.id
LIMIT %s OFFSET %s
"""


COMMENT_VOTERS_SQL = """
SELECT
    cl.published AS voted_at,
    cl.score,
    voter.id AS voter_id,
    voter.name AS voter_name,
    voter.display_name AS voter_display_name,
    voter.local AS voter_local,
    voter.actor_id AS voter_url
FROM comment_like cl
JOIN person voter ON voter.id = cl.person_id
WHERE cl.comment_id = %s
  AND voter.deleted = false
ORDER BY cl.score DESC, lower(voter.name), voter.id
LIMIT %s OFFSET %s
"""


POST_VOTER_SUMMARY_SQL = """
SELECT
    COUNT(*)::integer AS total,
    COUNT(*) FILTER (WHERE pl.score > 0)::integer AS up,
    COUNT(*) FILTER (WHERE pl.score < 0)::integer AS down,
    COUNT(*) FILTER (WHERE pl.score = 0)::integer AS neutral
FROM post_like pl
JOIN person voter ON voter.id = pl.person_id
WHERE pl.post_id = %s
  AND voter.deleted = false
"""


COMMENT_VOTER_SUMMARY_SQL = """
SELECT
    COUNT(*)::integer AS total,
    COUNT(*) FILTER (WHERE cl.score > 0)::integer AS up,
    COUNT(*) FILTER (WHERE cl.score < 0)::integer AS down,
    COUNT(*) FILTER (WHERE cl.score = 0)::integer AS neutral
FROM comment_like cl
JOIN person voter ON voter.id = cl.person_id
WHERE cl.comment_id = %s
  AND voter.deleted = false
"""


def enrich_user_vote(row):
    row = dict(row)
    community_domain = actor_domain(row["community_url"])
    row["community_display"] = (
        f"!{row['community_name']}"
        if row["community_local"] or not community_domain
        else f"!{row['community_name']}@{community_domain}"
    )
    row["remote_url"] = None
    if not row["item_local"] and not row["content_hidden"]:
        row["remote_url"] = safe_http_url(row["content_url"])

    if row["author_name"]:
        handle = make_handle(row["author_name"], row["author_local"], row["author_url"])
        row["author_handle"] = handle
        row["author_profile_path"] = local_profile_path(handle)
    else:
        row["author_handle"] = None
        row["author_profile_path"] = None
    return row


def enrich_item(item):
    item = dict(item)
    community_domain = actor_domain(item["community_url"])
    item["community_display"] = (
        f"!{item['community_name']}"
        if item["community_local"] or not community_domain
        else f"!{item['community_name']}@{community_domain}"
    )
    item["remote_url"] = None
    if not item["item_local"] and not item["content_hidden"]:
        item["remote_url"] = safe_http_url(item["content_url"])
    return item


def enrich_voter(row):
    row = dict(row)
    handle = make_handle(row["voter_name"], row["voter_local"], row["voter_url"])
    row["voter_handle"] = handle
    row["voter_display"] = f"@{handle}" if handle else ""
    row["voter_profile_path"] = local_profile_path(handle)
    row["voter_vote_path"] = vote_history_path(handle)
    return row


def enrich_instance_user(row):
    row = dict(row)
    handle = make_handle(row["name"], row["local"], row["actor_id"])
    row["handle"] = handle
    row["vote_path"] = vote_history_path(handle)
    row["down_percent"] = (row["down"] / row["total"] * 100) if row["total"] else 0
    return row


def resolve_item_search(item_query):
    parsed = parse_item_search(item_query)
    if not parsed:
        return None, "invalid"

    if parsed["local_item"]:
        return parsed["local_item"], None

    ap_urls = parsed["ap_urls"]
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(ITEM_BY_AP_ID_SQL, (*ap_urls, *ap_urls))
            row = cur.fetchone()
    if not row:
        return None, "not_found"
    return (row["kind"], row["item_id"]), None


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

    content_type = request.args.get("type", "all")
    if content_type not in ("all", "post", "comment"):
        content_type = "all"

    history_view = request.args.get("view", "cast")
    if history_view not in ("cast", "received"):
        history_view = "cast"

    received_sort = request.args.get("sort", "date")
    if received_sort not in ("date", "top", "bottom"):
        received_sort = "date"

    raw_score = request.args.get("score", "all")
    score_filter = 1 if raw_score == "1" else -1 if raw_score == "-1" else 0 if raw_score == "0" else None
    if history_view == "received":
        score_filter = None
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
    user_suggestions = []

    if username:
        with db() as conn:
            with conn.cursor() as cur:
                user = resolve_user(cur, username)
                if user:
                    canonical_username = user["handle"]
                    cur.execute(
                        USER_SUMMARY_SQL,
                        (user["id"], user["id"], content_type, content_type, score_filter, score_filter),
                    )
                    summary = cur.fetchone()

                    cur.execute(
                        USER_RECEIVED_SUMMARY_SQL,
                        (user["id"], user["id"]),
                    )
                    received_summary = cur.fetchone()

                    if history_view == "cast":
                        pagination = make_pagination(
                            summary["filtered_total"], requested_page
                        )
                        cur.execute(
                            USER_VOTES_SQL,
                            (
                                content_type, score_filter,
                                user["id"], user["id"],
                                PAGE_SIZE, pagination["offset"],
                            ),
                        )
                        rows = [enrich_user_vote(row) for row in cur.fetchall()]
                    else:
                        item_count_key = {
                            "all": "items",
                            "post": "post_items",
                            "comment": "comment_items",
                        }[content_type]
                        received_items_summary = {
                            "total": received_summary["items"],
                            "filtered_total": received_summary[item_count_key],
                        }
                        pagination = make_pagination(
                            received_items_summary["filtered_total"], requested_page
                        )
                        cur.execute(
                            USER_RECEIVED_ITEMS_SQL,
                            (
                                content_type, received_sort,
                                user["id"], user["id"],
                                PAGE_SIZE, pagination["offset"],
                            ),
                        )
                        rows = [enrich_item(row) for row in cur.fetchall()]

                    type_urls = {
                        "all": build_index_url(
                            canonical_username, "all", score_filter, 1, history_view,
                            received_sort,
                        ),
                        "post": build_index_url(
                            canonical_username, "post", score_filter, 1, history_view,
                            received_sort,
                        ),
                        "comment": build_index_url(
                            canonical_username, "comment", score_filter, 1, history_view,
                            received_sort,
                        ),
                    }
                    score_urls = {
                        "all": build_index_url(canonical_username, content_type, None),
                        "1": build_index_url(canonical_username, content_type, 1),
                        "-1": build_index_url(canonical_username, content_type, -1),
                        "0": build_index_url(canonical_username, content_type, 0),
                    }
                    view_urls = {
                        "cast": build_index_url(
                            canonical_username, content_type, score_filter
                        ),
                        "received": build_index_url(
                            canonical_username, content_type, None, 1, "received",
                            received_sort,
                        ),
                    }
                    sort_urls = {
                        sort_name: build_index_url(
                            canonical_username, content_type, None, 1, "received",
                            sort_name,
                        )
                        for sort_name in ("date", "top", "bottom")
                    }
                    if pagination["has_prev"]:
                        pagination["prev_url"] = build_index_url(
                            canonical_username,
                            content_type,
                            score_filter,
                            pagination["prev_page"],
                            history_view,
                            received_sort,
                        )
                    if pagination["has_next"]:
                        pagination["next_url"] = build_index_url(
                            canonical_username,
                            content_type,
                            score_filter,
                            pagination["next_page"],
                            history_view,
                            received_sort,
                        )
                else:
                    user_suggestions = find_user_suggestions(cur, username)

    return render_template(
        "index.html",
        username=username,
        item_query=item_query,
        item_error=item_error,
        instance_query=instance_query,
        instance_error=instance_error,
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
