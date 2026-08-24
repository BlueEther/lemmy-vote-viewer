# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

from flask import Blueprint, abort, redirect, request

from .. import application as legacy


blueprint = Blueprint("search", __name__)


@blueprint.route("/")
@legacy.require_access(legacy.AUTH_SEARCH_REQUIRE)
def index():
    username = request.args.get("user", "").strip()
    if len(username) > 512:
        abort(400)

    item_query = request.args.get("item", "").strip()
    if len(item_query) > 2048:
        abort(400)
    item_error = None
    if item_query:
        item_result, item_error = legacy.resolve_item_search(item_query)
        if item_result:
            return redirect(legacy.build_item_url(*item_result))

    instance_query = ""
    instance_error = None
    community_overview_query = ""
    community_overview_error = None
    if legacy.ENABLE_DOMAIN_SEARCH:
        instance_query = request.args.get("instance", "").strip()
        if len(instance_query) > 255:
            abort(400)
        if instance_query:
            legacy.enforce_access(legacy.AUTH_INSTANCE_REQUIRE)
            instance_domain = legacy.normalize_instance_domain(instance_query)
            if instance_domain:
                return redirect(legacy.build_instance_url(instance_domain))
            instance_error = "invalid"

        community_overview_query = request.args.get(
            "community_overview",
            "",
        ).strip()
        if len(community_overview_query) > 512:
            abort(400)
        if community_overview_query:
            legacy.enforce_access(legacy.AUTH_INSTANCE_REQUIRE)
            parsed_community = legacy.parse_community_handle(
                community_overview_query
            )
            if parsed_community:
                community_name, community_domain = parsed_community
                community_handle = f"!{community_name}"
                if community_domain:
                    community_handle += f"@{community_domain}"
                return redirect(
                    legacy.build_community_overview_url(community_handle)
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
    if community_sort not in legacy.COMMUNITY_SUMMARY_SORTS:
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
    requested_page = legacy.parse_page()

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
        with legacy.db() as conn:
            with conn.cursor() as cur:
                user = legacy.resolve_user(cur, username)
                if user:
                    user["remote_url"] = legacy.remote_profile_url(
                        user["local"], user["actor_id"]
                    )
                    canonical_username = user["handle"]
                    community_id = None
                    if community_query:
                        community, community_error = legacy.resolve_community(
                            cur, community_query
                        )
                        if community:
                            community_id = community["id"]
                            community_query = community["handle"]
                        else:
                            community_id = -1

                    cur.execute(
                        legacy.USER_SUMMARY_SQL,
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
                        legacy.USER_RECEIVED_SUMMARY_SQL,
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
                        pagination = legacy.make_pagination(
                            summary["filtered_total"], requested_page
                        )
                        if community_id is None:
                            votes_sql = legacy.USER_VOTES_SQL
                            votes_params = (
                                content_type, score_filter,
                                user["id"], user["id"],
                                legacy.PAGE_SIZE, pagination["offset"],
                            )
                        else:
                            votes_sql = legacy.USER_VOTES_BY_COMMUNITY_SQL
                            votes_params = (
                                content_type, score_filter, community_id,
                                user["id"], user["id"],
                                legacy.PAGE_SIZE, pagination["offset"],
                            )
                        cur.execute(votes_sql, votes_params)
                        rows = [legacy.enrich_user_vote(row) for row in cur.fetchall()]
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
                        pagination = legacy.make_pagination(
                            received_items_summary["filtered_total"], requested_page
                        )
                        if community_id is None:
                            received_items_sql = legacy.USER_RECEIVED_ITEMS_SQL
                            received_items_params = (
                                content_type, received_sort,
                                user["id"], user["id"],
                                legacy.PAGE_SIZE, pagination["offset"],
                            )
                        else:
                            received_items_sql = legacy.USER_RECEIVED_ITEMS_BY_COMMUNITY_SQL
                            received_items_params = (
                                content_type, received_sort, community_id,
                                user["id"], user["id"],
                                legacy.PAGE_SIZE, pagination["offset"],
                            )
                        cur.execute(received_items_sql, received_items_params)
                        rows = [legacy.enrich_item(row) for row in cur.fetchall()]
                    else:
                        requested_offset = (requested_page - 1) * legacy.PAGE_SIZE
                        community_summary_sql = legacy.USER_COMMUNITY_SUMMARY_SQL.format(
                            order_by=legacy.COMMUNITY_SUMMARY_SORTS[community_sort]
                        )
                        cur.execute(
                            community_summary_sql,
                            (
                                user["id"],
                                user["id"],
                                user["id"],
                                user["id"],
                                legacy.PAGE_SIZE,
                                requested_offset,
                            ),
                        )
                        result_rows = cur.fetchall()
                        if not result_rows and requested_page > 1:
                            return redirect(
                                legacy.build_index_url(
                                    canonical_username,
                                    history_view="communities",
                                    community_sort=community_sort,
                                )
                            )
                        community_total = (
                            result_rows[0]["community_count"] if result_rows else 0
                        )
                        pagination = legacy.make_pagination(
                            community_total, requested_page
                        )
                        rows = [
                            legacy.enrich_community_summary(row, canonical_username)
                            for row in result_rows
                        ]

                    type_urls = {
                        "all": legacy.build_index_url(
                            canonical_username, "all", score_filter, 1, history_view,
                            received_sort, community_query,
                        ),
                        "post": legacy.build_index_url(
                            canonical_username, "post", score_filter, 1, history_view,
                            received_sort, community_query,
                        ),
                        "comment": legacy.build_index_url(
                            canonical_username, "comment", score_filter, 1, history_view,
                            received_sort, community_query,
                        ),
                    }
                    score_urls = {
                        "all": legacy.build_index_url(
                            canonical_username, content_type, None,
                            community=community_query,
                        ),
                        "1": legacy.build_index_url(
                            canonical_username, content_type, 1,
                            community=community_query,
                        ),
                        "-1": legacy.build_index_url(
                            canonical_username, content_type, -1,
                            community=community_query,
                        ),
                        "0": legacy.build_index_url(
                            canonical_username, content_type, 0,
                            community=community_query,
                        ),
                    }
                    view_urls = {
                        "cast": legacy.build_index_url(
                            canonical_username, content_type, score_filter,
                            community=community_query,
                        ),
                        "received": legacy.build_index_url(
                            canonical_username, content_type, None, 1, "received",
                            received_sort, community_query,
                        ),
                        "communities": legacy.build_index_url(
                            canonical_username,
                            history_view="communities",
                            community_sort=community_sort,
                        ),
                    }
                    sort_urls = {
                        sort_name: legacy.build_index_url(
                            canonical_username, content_type, None, 1, "received",
                            sort_name, community_query,
                        )
                        for sort_name in ("date", "top", "bottom")
                    }
                    community_sort_urls = {
                        sort_name: legacy.build_index_url(
                            canonical_username,
                            history_view="communities",
                            community_sort=sort_name,
                        )
                        for sort_name in legacy.COMMUNITY_SUMMARY_SORTS
                    }
                    if pagination["has_prev"]:
                        if history_view == "communities":
                            pagination["prev_url"] = legacy.build_index_url(
                                canonical_username,
                                page=pagination["prev_page"],
                                history_view="communities",
                                community_sort=community_sort,
                            )
                        else:
                            pagination["prev_url"] = legacy.build_index_url(
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
                            pagination["next_url"] = legacy.build_index_url(
                                canonical_username,
                                page=pagination["next_page"],
                                history_view="communities",
                                community_sort=community_sort,
                            )
                        else:
                            pagination["next_url"] = legacy.build_index_url(
                                canonical_username,
                                content_type,
                                score_filter,
                                pagination["next_page"],
                                history_view,
                                received_sort,
                                community_query,
                            )
                    community_clear_url = legacy.build_index_url(
                        canonical_username,
                        content_type,
                        score_filter,
                        1,
                        history_view,
                        received_sort,
                    )
                else:
                    user_suggestions = legacy.find_user_suggestions(
                        cur, username, community_query
                    )

    return legacy.render_template(
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
