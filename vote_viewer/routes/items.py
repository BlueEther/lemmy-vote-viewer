# Copyright (C) 2026 BlueEther@no.lastname.nz
# SPDX-License-Identifier: AGPL-3.0-or-later

from flask import Blueprint, abort

from .. import application as legacy


blueprint = Blueprint("items", __name__)


def item_votes(kind, item_id):
    requested_page = legacy.parse_page()
    if kind == "post":
        item_sql, summary_sql, voters_sql = legacy.POST_ITEM_SQL, legacy.POST_VOTER_SUMMARY_SQL, legacy.POST_VOTERS_SQL
    elif kind == "comment":
        item_sql, summary_sql, voters_sql = legacy.COMMENT_ITEM_SQL, legacy.COMMENT_VOTER_SUMMARY_SQL, legacy.COMMENT_VOTERS_SQL
    else:
        abort(404)

    with legacy.db() as conn:
        with conn.cursor() as cur:
            cur.execute(item_sql, (item_id,))
            item = cur.fetchone()
            if not item:
                abort(404)
            item = legacy.enrich_item(item)

            cur.execute(summary_sql, (item_id,))
            summary = cur.fetchone()
            pagination = legacy.make_pagination(summary["total"], requested_page)

            cur.execute(voters_sql, (item_id, legacy.PAGE_SIZE, pagination["offset"]))
            rows = [legacy.enrich_voter(row) for row in cur.fetchall()]

    if pagination["has_prev"]:
        pagination["prev_url"] = legacy.build_item_url(kind, item_id, pagination["prev_page"])
    if pagination["has_next"]:
        pagination["next_url"] = legacy.build_item_url(kind, item_id, pagination["next_page"])

    return legacy.render_template(
        "item.html",
        kind=kind,
        item_id=item_id,
        item=item,
        rows=rows,
        summary=summary,
        pagination=pagination,
    )



@blueprint.route("/item/post/<int:item_id>")
@legacy.require_access(legacy.AUTH_SEARCH_REQUIRE)
def post_votes(item_id):
    return item_votes("post", item_id)



@blueprint.route("/item/comment/<int:item_id>")
@legacy.require_access(legacy.AUTH_SEARCH_REQUIRE)
def comment_votes(item_id):
    return item_votes("comment", item_id)
