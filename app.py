# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import math
import os
from datetime import timezone
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, abort, render_template, request
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
APP_VERSION = "0.2"
DB_DSN = os.environ["DATABASE_URL"]

ERROR_MESSAGES = {
    400: "The request could not be understood.",
    404: "The requested page or item was not found.",
    500: "The viewer encountered an unexpected error.",
}

_raw_prefix = os.environ.get("APP_PREFIX", "/votes").strip()
APP_PREFIX = "" if _raw_prefix in ("", "/") else "/" + _raw_prefix.strip("/")

try:
    PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "100"))
except ValueError:
    PAGE_SIZE = 100
PAGE_SIZE = max(20, min(PAGE_SIZE, 250))

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
    return {
        "app_prefix": APP_PREFIX,
        "app_version": APP_VERSION,
        "lemmy_base_url": LEMMY_BASE_URL,
        "lemmy_instance": LEMMY_INSTANCE,
    }


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(500)
def handle_error(error):
    status_code = getattr(error, "code", 500)
    return (
        render_template(
            "error.html",
            status_code=status_code,
            message=ERROR_MESSAGES.get(status_code, ERROR_MESSAGES[500]),
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


def build_index_url(username, content_type="all", score_filter=None, page=1):
    params = {"user": username}
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


def vote_history_path(handle):
    return build_index_url(handle, "all", None, 1) if handle else None


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


USER_VOTES_SQL = """
WITH votes AS (
    SELECT
        pl.published AS voted_at,
        'post'::text AS type,
        pl.score,
        p.id AS post_id,
        NULL::integer AS comment_id,
        CASE WHEN p.deleted THEN '[deleted post]'
             WHEN p.removed THEN '[removed post]'
             ELSE p.name END AS post_title,
        NULL::text AS comment_content,
        c.name AS community_name,
        c.title AS community_title,
        c.local AS community_local,
        c.actor_id AS community_url,
        CASE WHEN p.deleted OR p.removed OR author.deleted THEN NULL ELSE author.name END AS author_name,
        CASE WHEN p.deleted OR p.removed OR author.deleted THEN NULL ELSE author.display_name END AS author_display_name,
        author.local AS author_local,
        author.actor_id AS author_url,
        p.ap_id AS content_url,
        p.local AS item_local,
        (p.deleted OR p.removed) AS content_hidden,
        (p.deleted OR p.removed) AS post_hidden
    FROM post_like pl
    JOIN post p ON p.id = pl.post_id
    JOIN community c ON c.id = p.community_id
    JOIN person author ON author.id = p.creator_id
    WHERE pl.person_id = %s
      AND c.visibility::text = 'Public'
      AND c.deleted = false
      AND c.removed = false

    UNION ALL

    SELECT
        cl.published AS voted_at,
        'comment'::text AS type,
        cl.score,
        p.id AS post_id,
        cm.id AS comment_id,
        CASE WHEN p.deleted THEN '[deleted post]'
             WHEN p.removed THEN '[removed post]'
             ELSE p.name END AS post_title,
        CASE WHEN p.deleted OR p.removed THEN '[comment on unavailable post]'
             WHEN cm.deleted THEN '[deleted comment]'
             WHEN cm.removed THEN '[removed comment]'
             ELSE cm.content END AS comment_content,
        c.name AS community_name,
        c.title AS community_title,
        c.local AS community_local,
        c.actor_id AS community_url,
        CASE WHEN p.deleted OR p.removed OR cm.deleted OR cm.removed OR author.deleted THEN NULL ELSE author.name END AS author_name,
        CASE WHEN p.deleted OR p.removed OR cm.deleted OR cm.removed OR author.deleted THEN NULL ELSE author.display_name END AS author_display_name,
        author.local AS author_local,
        author.actor_id AS author_url,
        cm.ap_id AS content_url,
        cm.local AS item_local,
        (p.deleted OR p.removed OR cm.deleted OR cm.removed) AS content_hidden,
        (p.deleted OR p.removed) AS post_hidden
    FROM comment_like cl
    JOIN comment cm ON cm.id = cl.comment_id
    JOIN post p ON p.id = cl.post_id
    JOIN community c ON c.id = p.community_id
    JOIN person author ON author.id = cm.creator_id
    WHERE cl.person_id = %s
      AND c.visibility::text = 'Public'
      AND c.deleted = false
      AND c.removed = false
)
SELECT *
FROM votes
WHERE (%s::text = 'all' OR type = %s::text)
  AND (%s::smallint IS NULL OR score = %s::smallint)
ORDER BY voted_at DESC
LIMIT %s OFFSET %s
"""


USER_SUMMARY_SQL = """
WITH votes AS (
    SELECT pl.score, 'post'::text AS type
    FROM post_like pl
    JOIN post p ON p.id = pl.post_id
    JOIN community c ON c.id = p.community_id
    WHERE pl.person_id = %s
      AND c.visibility::text = 'Public'
      AND c.deleted = false
      AND c.removed = false

    UNION ALL

    SELECT cl.score, 'comment'::text AS type
    FROM comment_like cl
    JOIN post p ON p.id = cl.post_id
    JOIN community c ON c.id = p.community_id
    WHERE cl.person_id = %s
      AND c.visibility::text = 'Public'
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
  AND c.visibility::text = 'Public'
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
  AND c.visibility::text = 'Public'
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
ORDER BY pl.score DESC, lower(voter.name)
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
ORDER BY cl.score DESC, lower(voter.name)
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


@app.route("/")
def index():
    username = request.args.get("user", "").strip()
    if len(username) > 512:
        abort(400)

    content_type = request.args.get("type", "all")
    if content_type not in ("all", "post", "comment"):
        content_type = "all"

    raw_score = request.args.get("score", "all")
    score_filter = 1 if raw_score == "1" else -1 if raw_score == "-1" else 0 if raw_score == "0" else None
    requested_page = parse_page()

    rows = []
    summary = None
    user = None
    pagination = None
    type_urls = {}
    score_urls = {}

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
                    pagination = make_pagination(summary["filtered_total"], requested_page)

                    cur.execute(
                        USER_VOTES_SQL,
                        (
                            user["id"], user["id"],
                            content_type, content_type,
                            score_filter, score_filter,
                            PAGE_SIZE, pagination["offset"],
                        ),
                    )
                    rows = [enrich_user_vote(row) for row in cur.fetchall()]

                    type_urls = {
                        "all": build_index_url(canonical_username, "all", score_filter),
                        "post": build_index_url(canonical_username, "post", score_filter),
                        "comment": build_index_url(canonical_username, "comment", score_filter),
                    }
                    score_urls = {
                        "all": build_index_url(canonical_username, content_type, None),
                        "1": build_index_url(canonical_username, content_type, 1),
                        "-1": build_index_url(canonical_username, content_type, -1),
                        "0": build_index_url(canonical_username, content_type, 0),
                    }
                    if pagination["has_prev"]:
                        pagination["prev_url"] = build_index_url(
                            canonical_username, content_type, score_filter, pagination["prev_page"]
                        )
                    if pagination["has_next"]:
                        pagination["next_url"] = build_index_url(
                            canonical_username, content_type, score_filter, pagination["next_page"]
                        )

    return render_template(
        "index.html",
        username=username,
        user=user,
        rows=rows,
        summary=summary,
        pagination=pagination,
        content_type=content_type,
        score_filter=score_filter,
        type_urls=type_urls,
        score_urls=score_urls,
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
def post_votes(item_id):
    return item_votes("post", item_id)


@app.route("/item/comment/<int:item_id>")
def comment_votes(item_id):
    return item_votes("comment", item_id)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
