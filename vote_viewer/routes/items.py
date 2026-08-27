# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

from flask import Blueprint, abort, render_template, request

from ..queries import (
    COMMENT_ITEM_SQL,
    COMMENT_VOTERS_SQL_BY_SORT,
    COMMENT_VOTER_SUMMARY_SQL,
    ITEM_VOTER_ACTIVITY_SQL,
    ITEM_VOTER_SORTS,
    POST_ITEM_SQL,
    POST_VOTERS_SQL_BY_SORT,
    POST_VOTER_SUMMARY_SQL,
)
from ..services import enrich_item, enrich_voter
from ..web import (
    build_item_url,
    config,
    db,
    make_pagination,
    parse_page,
    require_access,
)


blueprint = Blueprint("items", __name__)


def item_votes(kind, item_id):
    settings = config()
    requested_page = parse_page()
    voter_sort = request.args.get("sort", "vote")
    if voter_sort not in ITEM_VOTER_SORTS:
        voter_sort = "vote"
    if kind == "post":
        item_sql, summary_sql, voters_sql_by_sort = (
            POST_ITEM_SQL,
            POST_VOTER_SUMMARY_SQL,
            POST_VOTERS_SQL_BY_SORT,
        )
    elif kind == "comment":
        item_sql, summary_sql, voters_sql_by_sort = (
            COMMENT_ITEM_SQL,
            COMMENT_VOTER_SUMMARY_SQL,
            COMMENT_VOTERS_SQL_BY_SORT,
        )
    else:
        abort(404)
    voters_sql = voters_sql_by_sort[voter_sort]

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(item_sql, (item_id,))
            item = cur.fetchone()
            if not item:
                abort(404)
            item = enrich_item(item, settings.app_prefix)

            cur.execute(summary_sql, (item_id,))
            summary = cur.fetchone()
            pagination = make_pagination(summary["total"], requested_page)

            cur.execute(
                voters_sql,
                (item_id, settings.page_size, pagination["offset"]),
            )
            rows = cur.fetchall()

            if rows and settings.enable_community_content_counts:
                voter_ids = [row["voter_id"] for row in rows]
                cur.execute("SELECT set_config('jit', 'off', true)")
                cur.execute(
                    "SELECT set_config("
                    "'max_parallel_workers_per_gather', '0', true)"
                )
                cur.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{settings.instance_query_timeout_seconds}s",),
                )
                activity_params = (
                    voter_ids,
                    voter_ids,
                    item["community_id"],
                    settings.instance_vote_window_days,
                    voter_ids,
                    item["community_id"],
                    settings.instance_vote_window_days,
                    voter_ids,
                    item["community_id"],
                    settings.instance_vote_window_days,
                    voter_ids,
                    item["community_id"],
                    settings.instance_vote_window_days,
                )
                cur.execute(
                    ITEM_VOTER_ACTIVITY_SQL,
                    activity_params,
                )
                activity_by_voter = {
                    row["voter_id"]: row for row in cur.fetchall()
                }
                for row in rows:
                    row.update(activity_by_voter.get(row["voter_id"], {}))

            rows = [
                enrich_voter(row, settings.app_prefix) for row in rows
            ]

    if pagination["has_prev"]:
        pagination["prev_url"] = build_item_url(
            kind, item_id, pagination["prev_page"], voter_sort
        )
    if pagination["has_next"]:
        pagination["next_url"] = build_item_url(
            kind, item_id, pagination["next_page"], voter_sort
        )

    sort_urls = {
        sort_name: build_item_url(kind, item_id, sort=sort_name)
        for sort_name in ITEM_VOTER_SORTS
    }

    return render_template(
        "item.html",
        kind=kind,
        item_id=item_id,
        item=item,
        rows=rows,
        summary=summary,
        pagination=pagination,
        voter_sort=voter_sort,
        community_content_counts_enabled=(
            settings.enable_community_content_counts
        ),
        vote_window_days=settings.instance_vote_window_days,
        sort_urls=sort_urls,
    )



@blueprint.route("/item/post/<int:item_id>")
@require_access("auth_search_require")
def post_votes(item_id):
    return item_votes("post", item_id)



@blueprint.route("/item/comment/<int:item_id>")
@require_access("auth_search_require")
def comment_votes(item_id):
    return item_votes("comment", item_id)
