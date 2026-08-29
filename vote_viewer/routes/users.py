# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import hashlib
import json

from flask import (
    Blueprint,
    abort,
    current_app,
    make_response,
    redirect,
    render_template,
    request,
)
import psycopg

from ..links import (
    build_index_url,
    build_instance_url,
    local_profile_path,
    make_handle,
    remote_profile_url,
)
from ..queries import (
    USERS_OVERVIEW_CONTENT_SQL,
    USERS_OVERVIEW_SORTS,
    USERS_OVERVIEW_SQL,
    USERS_OVERVIEW_VIEWS,
)
from ..web import (
    build_users_data_url,
    build_users_url,
    config,
    db,
    enforce_access,
    make_pagination,
    parse_page,
    users_overview_cache,
)


blueprint = Blueprint("users", __name__)


def users_response(payload, status=200, cache_status=None):
    response = make_response(payload, status)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    if cache_status:
        response.headers["X-Users-Overview-Cache"] = cache_status
    return response


def selected_sort():
    sort = request.args.get("sort", "total")
    return sort if sort in USERS_OVERVIEW_SORTS else "total"


def selected_view():
    view = request.args.get("view", "all")
    return view if view in USERS_OVERVIEW_VIEWS else "all"


def selected_window(settings):
    raw_window = request.args.get("window")
    try:
        window = int(raw_window) if raw_window is not None else settings.vote_window_days
    except (TypeError, ValueError):
        return settings.vote_window_days
    allowed = {1, 2, 7, settings.vote_window_days}
    return window if window in allowed else settings.vote_window_days


def window_options(settings):
    options = [(1, "1 Day"), (2, "2 Days"), (7, "1 Week")]
    full = (settings.vote_window_days, f"Full {settings.vote_window_days} Days")
    return [option for option in options if option[0] != full[0]] + [full]


def vote_metric(row, view, metric):
    if view == "all":
        return row[f"cast_{metric}"] + row[f"received_{metric}"]
    return row[f"{view}_{metric}"]


def latest_vote_epoch(row, view):
    if view == "cast":
        return row["latest_vote_epoch"]
    if view == "received":
        return row["latest_received_vote_epoch"]
    epochs = (
        row["latest_vote_epoch"],
        row["latest_received_vote_epoch"],
    )
    return max((value for value in epochs if value is not None), default=None)


def sort_snapshot(rows, view, sort):
    def identity(row):
        return (row["name"].lower(), row["id"])

    if sort == "username":
        return sorted(rows, key=identity)
    if sort == "recent":
        return sorted(
            rows,
            key=lambda row: (
                -latest_vote_epoch(row, view)
                if latest_vote_epoch(row, view) is not None
                else float("inf"),
                *identity(row),
            ),
        )

    def sort_key(row):
        total = vote_metric(row, view, "total")
        if sort == "down_ratio":
            primary = (
                vote_metric(row, view, "down") / total
                if total >= 10
                else -1
            )
        else:
            primary = vote_metric(row, view, sort)
        return (-primary, -total, *identity(row))

    return sorted(rows, key=sort_key)


def enrich_user(row, settings):
    row = dict(row)
    search_handle = make_handle(row["name"], row["local"], row["actor_id"])
    if not search_handle:
        search_handle = row["name"]
    row["handle"] = f"{row['name']}@{row['instance_domain']}"
    row["vote_path"] = build_index_url(
        search_handle,
        app_prefix=settings.app_prefix,
    )
    row["profile_path"] = local_profile_path(search_handle)
    row["remote_url"] = remote_profile_url(row["local"], row["actor_id"])
    row["instance_path"] = (
        build_instance_url(
            row["instance_domain"],
            app_prefix=settings.app_prefix,
        )
        if settings.enable_domain_search
        else None
    )
    row["cast_down_percent"] = (
        row["cast_down"] * 100 / row["cast_total"]
        if row["cast_total"]
        else 0.0
    )
    return row


def require_users_overview(settings):
    if not settings.enable_users_overview:
        abort(404)
    enforce_access(settings.auth_instance_require)


@blueprint.route("/users/")
def users_overview():
    settings = config()
    require_users_overview(settings)
    sort = selected_sort()
    view = selected_view()
    window = selected_window(settings)
    page = parse_page()
    if "cache_refresh" in request.args:
        users_overview_cache().clear()
        return redirect(build_users_url(sort, page, view, window=window))
    window_urls = {
        days: build_users_url(sort, page, view, window=days)
        for days, _label in window_options(settings)
    }
    return render_template(
        "users.html",
        sort=sort,
        view=view,
        page=page,
        vote_window_days=window,
        users_data_url=build_users_data_url(sort, page, view, window),
        users_refresh_url=build_users_url(
            sort,
            page,
            view,
            cache_refresh=True,
            window=window,
        ),
        window=window,
        window_options=window_options(settings),
        window_urls=window_urls,
    )


@blueprint.route("/users/data")
def users_overview_data():
    settings = config()
    require_users_overview(settings)
    sort = selected_sort()
    view = selected_view()
    window = selected_window(settings)
    requested_page = parse_page()
    cache_key = hashlib.sha256(
        json.dumps(
            (
                "users-overview-snapshot-v1",
                window,
            ),
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    cache = users_overview_cache()
    cache_state, cached_payload = cache.claim(cache_key)
    if cache_state == "busy":
        response = users_response("", status=202, cache_status="busy")
        response.headers["Retry-After"] = "1"
        return response

    try:
        if cache_state == "hit":
            snapshot_rows = json.loads(cached_payload)
        else:
            overview_sql = USERS_OVERVIEW_SQL.format(
                vote_window_days=window,
            )
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{settings.instance_query_timeout_seconds}s",),
                    )
                    cur.execute(overview_sql)
                    result_rows = [dict(row) for row in cur.fetchall()]
                    cur.execute(
                        USERS_OVERVIEW_CONTENT_SQL.format(
                            vote_window_days=window,
                        )
                    )
                    content_by_id = {
                        row["id"]: dict(row) for row in cur.fetchall()
                    }
            snapshot_rows = []
            for row in result_rows:
                row.update(
                    content_by_id.get(
                        row["id"],
                        {"post_count": 0, "comment_count": 0},
                    )
                )
                snapshot_rows.append(row)
            cache.store(
                cache_key,
                json.dumps(snapshot_rows, separators=(",", ":")),
            )

        total_users = len(snapshot_rows)
        pagination = make_pagination(total_users, requested_page)
        if pagination["page"] != requested_page:
            return redirect(
                build_users_data_url(sort, pagination["page"], view, window)
            )

        sorted_rows = sort_snapshot(snapshot_rows, view, sort)
        offset = (pagination["page"] - 1) * settings.page_size
        page_rows = sorted_rows[offset : offset + settings.page_size]
        rows = [enrich_user(row, settings) for row in page_rows]
        sort_urls = {
            key: build_users_url(key, view=view, window=window)
            for key in USERS_OVERVIEW_SORTS
        }
        view_urls = {
            key: build_users_url(sort, view=key, window=window)
            for key in USERS_OVERVIEW_VIEWS
        }
        if pagination["has_prev"]:
            pagination["prev_url"] = build_users_url(
                sort, pagination["prev_page"], view, window=window
            )
        if pagination["has_next"]:
            pagination["next_url"] = build_users_url(
                sort, pagination["next_page"], view, window=window
            )
        payload = render_template(
            "_users_overview.html",
            rows=rows,
            sort=sort,
            view=view,
            sort_urls=sort_urls,
            view_urls=view_urls,
            pagination=pagination,
            vote_window_days=window,
            window=window,
            window_options=window_options(settings),
        )
        return users_response(
            payload,
            cache_status="miss" if cache_state == "claimed" else "hit",
        )
    except psycopg.errors.QueryCanceled:
        cache.release(cache_key)
        current_app.logger.warning("Global users overview query timed out")
        return users_response(
            render_template(
                "_users_overview_error.html",
                message=(
                    "The users overview took too long to calculate. "
                    "Try again later."
                ),
            ),
            status=503,
            cache_status="error",
        )
    except Exception:
        cache.release(cache_key)
        raise
