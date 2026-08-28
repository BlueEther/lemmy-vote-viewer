# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

import hashlib
import json
from urllib.parse import urlencode

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
    normalize_instance_domain,
    parse_community_handle,
    remote_profile_url,
)
from ..queries import (
    COMMUNITY_SUMMARY_SORTS,
    USER_COMMUNITY_SUMMARY_SQL,
    USER_RECEIVED_ITEMS_BY_COMMUNITY_SQL,
    USER_RECEIVED_ITEMS_SQL,
    USER_RECEIVED_VOTE_GRAPH_SQL,
    USER_RECEIVED_SUMMARY_SQL,
    USER_SUMMARY_SQL,
    USER_VOTE_GRAPH_SQL,
    USER_VOTES_BY_COMMUNITY_SQL,
    USER_VOTES_OLDEST_BY_COMMUNITY_SQL,
    USER_VOTES_OLDEST_SQL,
    USER_VOTES_SQL,
)
from ..services import (
    build_vote_graph,
    enrich_community_summary,
    enrich_item,
    enrich_user_vote,
    find_user_suggestions,
    resolve_community,
    resolve_user,
)
from ..web import (
    build_community_overview_url,
    build_index_url,
    build_instance_url,
    build_item_url,
    config,
    db,
    enforce_access,
    graph_cache,
    make_pagination,
    parse_page,
    require_access,
    resolve_item_search,
)


blueprint = Blueprint("search", __name__)


def build_user_graph_url(
    user_id,
    history_view,
    content_type,
    score_filter,
    community_id,
    app_prefix,
):
    params = {
        "user_id": user_id,
        "view": history_view,
        "type": content_type,
    }
    if history_view == "cast" and score_filter is not None:
        params["score"] = str(score_filter)
    if community_id is not None:
        params["community_id"] = community_id
    return f"{app_prefix}/graph/user?{urlencode(params)}"


def graph_response(payload, status=200, cache_status=None):
    response = make_response(payload, status)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    if cache_status:
        response.headers["X-Vote-Graph-Cache"] = cache_status
    return response


@blueprint.route("/graph/user")
@require_access("auth_search_require")
def user_vote_graph():
    settings = config()
    if not settings.enable_user_vote_graphs:
        abort(404)

    try:
        user_id = int(request.args.get("user_id", ""))
    except ValueError:
        abort(400)
    if user_id <= 0:
        abort(400)

    history_view = request.args.get("view", "cast")
    if history_view not in ("cast", "received"):
        abort(400)

    content_type = request.args.get("type", "all")
    if content_type not in ("all", "post", "comment"):
        abort(400)

    raw_score = request.args.get("score", "all")
    score_filter = (
        1
        if raw_score == "1"
        else -1
        if raw_score == "-1"
        else 0
        if raw_score == "0"
        else None
    )
    if history_view != "cast":
        score_filter = None

    raw_community_id = request.args.get("community_id", "").strip()
    try:
        community_id = int(raw_community_id) if raw_community_id else None
    except ValueError:
        abort(400)
    if community_id is not None and community_id <= 0:
        abort(400)

    cache_key = None
    cache = graph_cache()
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cache_key = hashlib.sha256(
                    json.dumps(
                        (
                            user_id,
                            history_view,
                            content_type,
                            score_filter,
                            community_id,
                            settings.timezone_name,
                            settings.vote_window_days,
                        ),
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                cache_state, cached_payload = cache.claim(cache_key)
                if cache_state == "hit":
                    return graph_response(
                        cached_payload,
                        cache_status="hit",
                    )
                if cache_state == "busy":
                    response = graph_response(
                        "",
                        status=202,
                        cache_status="busy",
                    )
                    response.headers["Retry-After"] = "1"
                    return response

                if history_view == "cast":
                    graph_sql = USER_VOTE_GRAPH_SQL
                    graph_params = (
                        user_id,
                        content_type,
                        score_filter,
                        community_id,
                        settings.timezone_name,
                        settings.vote_window_days,
                    )
                    graph_title = "Votes cast by day"
                else:
                    cur.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{settings.instance_query_timeout_seconds}s",),
                    )
                    graph_sql = USER_RECEIVED_VOTE_GRAPH_SQL
                    graph_params = (
                        user_id,
                        content_type,
                        community_id,
                        settings.timezone_name,
                        settings.vote_window_days,
                    )
                    graph_title = "Votes received by day"

                cur.execute(graph_sql, graph_params)
                vote_graph = build_vote_graph(cur.fetchall())
                payload = render_template(
                    "_vote_graph.html",
                    vote_graph=vote_graph,
                    vote_graph_title=graph_title,
                    vote_graph_window_days=settings.vote_window_days,
                )
                cache.store(cache_key, payload)
                return graph_response(payload, cache_status="miss")
    except psycopg.errors.QueryCanceled:
        if cache_key:
            cache.release(cache_key)
        current_app.logger.warning(
            "User vote graph query timed out for %s",
            user_id,
        )
        return graph_response(
            render_template(
                "_vote_graph_error.html",
                message="The graph took too long to calculate. Try again later.",
            ),
            status=503,
            cache_status="error",
        )
    except Exception:
        if cache_key:
            cache.release(cache_key)
        raise


@blueprint.route("/")
@require_access("auth_search_require")
def index():
    settings = config()
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
    if settings.enable_domain_search:
        instance_query = request.args.get("instance", "").strip()
        if len(instance_query) > 255:
            abort(400)
        if instance_query:
            enforce_access(settings.auth_instance_require)
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
            enforce_access(settings.auth_instance_require)
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
    cast_sort = raw_sort or "date"
    if cast_sort not in ("date", "oldest"):
        cast_sort = "date"

    received_sort = raw_sort or "date"
    if received_sort not in ("date", "oldest", "top", "bottom"):
        received_sort = "date"

    history_sort = received_sort if history_view == "received" else cast_sort

    community_sort = raw_sort or "total"
    if community_sort not in COMMUNITY_SUMMARY_SORTS:
        community_sort = "total"

    raw_score = request.args.get("score", "all")
    score_filter = (
        1
        if raw_score == "1"
        else -1
        if raw_score == "-1"
        else 0
        if raw_score == "0"
        else None
    )
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
    vote_graph_url = None
    vote_graph_title = None

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

                    if settings.enable_user_vote_graphs and history_view in (
                        "cast",
                        "received",
                    ):
                        vote_graph_title = (
                            "Votes cast by day"
                            if history_view == "cast"
                            else "Votes received by day"
                        )
                        vote_graph_url = build_user_graph_url(
                            user["id"],
                            history_view,
                            content_type,
                            score_filter,
                            community_id,
                            settings.app_prefix,
                        )

                    if history_view == "cast":
                        pagination = make_pagination(
                            summary["filtered_total"], requested_page
                        )
                        if community_id is None:
                            votes_sql = (
                                USER_VOTES_OLDEST_SQL
                                if cast_sort == "oldest"
                                else USER_VOTES_SQL
                            )
                            votes_params = (
                                content_type, score_filter,
                                user["id"], user["id"],
                                settings.page_size, pagination["offset"],
                            )
                        else:
                            votes_sql = (
                                USER_VOTES_OLDEST_BY_COMMUNITY_SQL
                                if cast_sort == "oldest"
                                else USER_VOTES_BY_COMMUNITY_SQL
                            )
                            votes_params = (
                                content_type, score_filter, community_id,
                                user["id"], user["id"],
                                settings.page_size, pagination["offset"],
                            )
                        cur.execute(votes_sql, votes_params)
                        rows = [
                            enrich_user_vote(row, settings.app_prefix)
                            for row in cur.fetchall()
                        ]
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
                                settings.page_size, pagination["offset"],
                            )
                        else:
                            received_items_sql = USER_RECEIVED_ITEMS_BY_COMMUNITY_SQL
                            received_items_params = (
                                content_type, received_sort, community_id,
                                user["id"], user["id"],
                                settings.page_size, pagination["offset"],
                            )
                        cur.execute(received_items_sql, received_items_params)
                        rows = [
                            enrich_item(row, settings.app_prefix)
                            for row in cur.fetchall()
                        ]
                    else:
                        requested_offset = (requested_page - 1) * settings.page_size
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
                                user["id"],
                                user["id"],
                                settings.page_size,
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
                            enrich_community_summary(
                                row,
                                canonical_username,
                                settings.app_prefix,
                            )
                            for row in result_rows
                        ]

                    type_urls = {
                        "all": build_index_url(
                            canonical_username, "all", score_filter, 1, history_view,
                            history_sort, community_query,
                        ),
                        "post": build_index_url(
                            canonical_username, "post", score_filter, 1, history_view,
                            history_sort, community_query,
                        ),
                        "comment": build_index_url(
                            canonical_username,
                            "comment",
                            score_filter,
                            1,
                            history_view,
                            history_sort, community_query,
                        ),
                    }
                    score_urls = {
                        "all": build_index_url(
                            canonical_username, content_type, None, 1, "cast",
                            cast_sort,
                            community=community_query,
                        ),
                        "1": build_index_url(
                            canonical_username, content_type, 1, 1, "cast",
                            cast_sort,
                            community=community_query,
                        ),
                        "-1": build_index_url(
                            canonical_username, content_type, -1, 1, "cast",
                            cast_sort,
                            community=community_query,
                        ),
                        "0": build_index_url(
                            canonical_username, content_type, 0, 1, "cast",
                            cast_sort,
                            community=community_query,
                        ),
                    }
                    view_urls = {
                        "cast": build_index_url(
                            canonical_username, content_type, score_filter,
                            history_sort=cast_sort,
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
                    sort_names = (
                        ("date", "oldest", "top", "bottom")
                        if history_view == "received"
                        else ("date", "oldest")
                    )
                    sort_urls = {
                        sort_name: build_index_url(
                            canonical_username, content_type, score_filter, 1,
                            history_view,
                            sort_name, community_query,
                        )
                        for sort_name in sort_names
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
                                history_sort,
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
                                history_sort,
                                community_query,
                            )
                    community_clear_url = build_index_url(
                        canonical_username,
                        content_type,
                        score_filter,
                        1,
                        history_view,
                        history_sort,
                    )
                else:
                    user_suggestions = find_user_suggestions(
                        cur,
                        username,
                        community_query,
                        app_prefix=settings.app_prefix,
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
        history_sort=history_sort,
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
        vote_graph_url=vote_graph_url,
        vote_graph_title=vote_graph_title,
        vote_graph_window_days=settings.vote_window_days,
    )
